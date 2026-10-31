"""Command-line batch runner for Sampler jobs."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import batch, job


def _fail(code: int, message: str) -> int:
    print(json.dumps({"status": "failed", "code": code, "error": message}), file=sys.stderr)
    return code


def _print(value, as_json: bool, lines) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print("\n".join(lines(value)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cavi-substance-sampler-mcp")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run a batch job file")
    run.add_argument("job")
    run.add_argument("--workspace")
    run.add_argument("--timeout", type=float, default=1800)
    run.add_argument("--wait-for-app", type=float, nargs="?", const=300, default=0)
    run.add_argument("--keep-on-failure", action="store_true")
    run.add_argument("--lock-timeout", type=float, default=300)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true")

    validate = commands.add_parser("validate", help="Validate a batch job without launching Sampler")
    validate.add_argument("job")
    validate.add_argument("--workspace")
    validate.add_argument("--check-sampler", action="store_true")
    validate.add_argument("--check-project", action="store_true")
    validate.add_argument("--timeout", type=float, default=300)
    validate.add_argument("--json", action="store_true")

    commands.add_parser("schema", help="Print the batch job JSON Schema")

    doctor = commands.add_parser("doctor", help="Check Sampler batch prerequisites")
    doctor.add_argument("--workspace")
    doctor.add_argument("--launch", action="store_true")
    doctor.add_argument("--timeout", type=float, default=300)
    doctor.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(job.job_schema(), indent=2))
            return 0
        if args.command == "doctor":
            result = batch.doctor(args.workspace)
            status = 0
            if args.launch:
                try:
                    result["probe"] = batch.probe_sampler(args.workspace, args.timeout)
                except Exception as exc:  # noqa: BLE001 -- report probe failure
                    result["probe"] = {
                        "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                    }
                    status = getattr(exc, "code", 5)
            _print(result, args.json, lambda value: [
                f"available: {value['available']}",
                f"executable: {value['executable']}",
                f"version: {value['version']}",
                f"running: {value['running']}",
                f"workspace: {value['workspace']}",
                f"workspace_writable: {value['workspace_writable']}",
                f"lock_held: {value['lock_held']}",
                "targets: " + ", ".join(value["targets"]),
                "probe: " + (
                    "ok" if value.get("probe", {}).get("status") != "failed"
                    else "failed - " + value["probe"]["error"]
                ) if "probe" in value else "probe: not run",
            ])
            return status
        if args.command == "validate" or (args.command == "run" and args.dry_run):
            if args.command == "validate" and args.check_project:
                result = batch.validate_with_project(
                    args.job, args.workspace, args.timeout
                )
            elif args.command == "validate" and args.check_sampler:
                result = batch.validate_with_sampler(
                    args.job, args.workspace, args.timeout
                )
            else:
                result = batch.validate_job_file(args.job, args.workspace)
            result["dry_run"] = True
            _print(result, args.json, lambda value: [
                f"job: {value['job']}",
                f"directory: {value['directory']}",
                f"project: {value['project']}",
                f"steps: {', '.join(value['steps'])}",
                "targets: " + ", ".join(item["target"] for item in value["targets"]),
                "sampler: " + ("ok" if "sampler" in value else "not checked"),
                "project check: " + value.get("project_check", "not checked"),
                "warnings: " + str(len(value.get("preflight_warnings", []))
                                   + len(value.get("project_warnings", []))),
            ])
            return 0
        manifest = asyncio.run(batch.run_job_file_async(
            args.job, args.workspace, args.timeout, args.wait_for_app,
            args.keep_on_failure, args.lock_timeout,
        ))
        _print(manifest, args.json, lambda value: [
            f"job: {value['job']}",
            f"project: {value['project']}",
            f"outputs: {len(value['outputs'])}",
            f"seconds: {value['seconds']}",
            f"manifest: {(Path(value['job']).parent / 'manifest.json').as_posix()}",
        ])
        return 0
    except job.JobValidationError as exc:
        return _fail(2, str(exc))
    except (FileNotFoundError, NotADirectoryError) as exc:
        return _fail(2, str(exc))
    except batch.AppUnavailable as exc:
        return _fail(3, str(exc))
    except (batch.AppRunning, batch.LockTimeout) as exc:
        return _fail(4, str(exc))
    except batch.JobTimeout as exc:
        return _fail(6, str(exc))
    except batch.BatchError as exc:
        return _fail(exc.code, str(exc))
    except TimeoutError as exc:
        return _fail(6, str(exc))
    except Exception as exc:  # noqa: BLE001 -- CLI boundary
        return _fail(5, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
