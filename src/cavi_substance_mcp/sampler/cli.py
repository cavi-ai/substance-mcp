"""Command-line batch runner for Sampler jobs."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .. import packing
from ..core import workspace
from . import batch, job
from . import pack as packer


def _fail(code: int, message: str) -> int:
    print(json.dumps({"status": "failed", "code": code, "error": message}), file=sys.stderr)
    return code


def _print(value, as_json: bool, lines) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print("\n".join(lines(value)))


def _exit(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, (job.JobValidationError, FileNotFoundError, NotADirectoryError)):
        return 2, str(exc)
    if isinstance(exc, batch.BatchError):
        return exc.code, str(exc)
    if isinstance(exc, TimeoutError):
        return 6, str(exc)
    return 5, f"{type(exc).__name__}: {exc}"


def _plan_lines(value) -> list[str]:
    return [
        f"job: {value['job']}",
        f"directory: {value['directory']}",
        f"project: {value['project']}",
        f"steps: {', '.join(value['steps'])}",
        "targets: " + ", ".join(item["target"] for item in value["targets"]),
        "sampler: " + ("ok" if "sampler" in value else "not checked"),
        "project check: " + value.get("project_check", "not checked"),
        "build check: " + value.get("build_check", {}).get("status", "not checked"),
        "exports skipped: " + str(value.get("build_check", {}).get("exports_skipped", 0)),
        "warnings: " + str(len(value.get("preflight_warnings", []))
                           + len(value.get("project_warnings", []))
                           + len(value.get("build_check", {}).get("warnings", []))),
    ]


def _manifest_lines(value) -> list[str]:
    return [
        f"job: {value['job']}",
        f"project: {value['project']}",
        f"outputs: {len(value['outputs'])}",
        f"seconds: {value['seconds']}",
        f"manifest: {(Path(value['job']).parent / 'manifest.json').as_posix()}",
    ]


def _progress(label: str):
    def report(event):
        status = event.get("status")
        step = f"{event.get('step', 0) + 1}/{event.get('total', '?')}"
        if status == "start":
            print(f"[{label}] step {step} {event.get('action')} start", file=sys.stderr)
        elif status == "ok":
            print(
                f"[{label}] step {step} {event.get('action')} ok "
                f"({event.get('seconds')}s)",
                file=sys.stderr,
            )
        else:
            print(
                f"[{label}] step {step} {event.get('action')} error: "
                f"{event.get('error')}",
                file=sys.stderr,
            )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cavi-substance-sampler-mcp")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run one or more batch job files")
    run.add_argument("jobs", nargs="+")
    run.add_argument("--workspace")
    run.add_argument("--timeout", type=float, default=1800)
    run.add_argument("--wait-for-app", type=float, nargs="?", const=300, default=0)
    run.add_argument("--keep-on-failure", action="store_true")
    run.add_argument("--lock-timeout", type=float, default=300)
    run.add_argument("--continue-on-error", action="store_true")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true")

    validate = commands.add_parser(
        "validate", help="Validate a batch job without launching Sampler")
    validate.add_argument("job")
    validate.add_argument("--workspace")
    validate.add_argument("--check-sampler", action="store_true")
    validate.add_argument("--check-project", action="store_true")
    validate.add_argument("--check-build", action="store_true")
    validate.add_argument("--timeout", type=float, default=300)
    validate.add_argument("--build-timeout", type=float, default=1800)
    validate.add_argument("--json", action="store_true")

    commands.add_parser("schema", help="Print the batch job JSON Schema")

    pack = commands.add_parser("pack", help="Pack workspace maps using a JSON spec")
    pack.add_argument("file")
    pack.add_argument("--workspace")
    pack.add_argument("--json", action="store_true")

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
        if args.command == "pack":
            root = workspace.existing_root(args.workspace)
            path = Path(args.file).expanduser()
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
            if (not path.is_relative_to(root) or not path.is_file()
                    or path.suffix.lower() != ".json"):
                raise job.JobValidationError(
                    "pack file must be a JSON file inside the workspace"
                )
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                spec = packing.PackSpec.model_validate(data["pack"])
                directory = data["directory"]
                sources = data["sources"]
            except (KeyError, TypeError, ValueError) as exc:
                raise job.JobValidationError(f"invalid pack file: {exc}") from exc
            result = packer.pack_sync(directory, sources, spec, str(root))
            _print(result, args.json, lambda value: [
                f"directory: {value['directory']}",
                f"maps: {len(value['maps'])}",
            ])
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
        if args.command == "validate":
            if args.check_build:
                result = batch.check_build(
                    args.job, args.workspace, args.build_timeout
                )
            elif args.check_project:
                result = batch.validate_with_project(args.job, args.workspace, args.timeout)
            elif args.check_sampler:
                result = batch.validate_with_sampler(args.job, args.workspace, args.timeout)
            else:
                result = batch.validate_job_file(args.job, args.workspace)
            result["dry_run"] = True
            _print(result, args.json, _plan_lines)
            return 0
        if args.dry_run:
            plans = []
            for name in args.jobs:
                try:
                    plan = batch.validate_job_file(name, args.workspace)
                except Exception as exc:  # noqa: BLE001 -- CLI boundary
                    code, message = _exit(exc)
                    return _fail(code, message)
                plan["dry_run"] = True
                plans.append(plan)
            if len(plans) == 1:
                _print(plans[0], args.json, _plan_lines)
            else:
                _print({"status": "success", "jobs": plans}, args.json,
                       lambda value: [line for plan in value["jobs"]
                                      for line in _plan_lines(plan)])
            return 0

        manifests, failures = [], []
        for name in args.jobs:
            try:
                manifest = asyncio.run(batch.run_job_file_async(
                    name, args.workspace, args.timeout, args.wait_for_app,
                    args.keep_on_failure, args.lock_timeout,
                    on_progress=_progress(name) if args.verbose else None,
                ))
            except Exception as exc:  # noqa: BLE001 -- CLI boundary
                code, message = _exit(exc)
                failures.append({"job": name, "code": code, "error": message})
                if not args.continue_on_error:
                    return _fail(code, message)
                continue
            manifests.append(manifest)
        if len(args.jobs) == 1 and not failures:
            _print(manifests[0], args.json, _manifest_lines)
            return 0
        summary = {
            "status": "failed" if failures else "success",
            "jobs": manifests,
            "failures": failures,
        }
        _print(summary, args.json, lambda value: [
            *[line for item in value["jobs"] for line in _manifest_lines(item)],
            *[f"failed: {item['job']} ({item['code']}) {item['error']}"
              for item in value["failures"]],
        ])
        return failures[0]["code"] if failures else 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary
        code, message = _exit(exc)
        return _fail(code, message)


if __name__ == "__main__":
    raise SystemExit(main())
