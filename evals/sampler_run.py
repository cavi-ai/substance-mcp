"""Run deterministic Sampler evaluations over the real stdio MCP surface."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
APP = "substance-sampler"
SERVER = ["-m", "cavi_substance_mcp.sampler.server"]
MODES = ["protocol", "simulated"]


def pointer(value, path):
    for key in path.strip("/").split("/"):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def fingerprint(*roots):
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


async def exercise(mode, env, workspace):
    corpus = json.loads((ROOT / "evals/sampler_cases.json").read_text())
    if len({case["id"] for case in corpus}) != len(corpus):
        raise ValueError("Corpus case IDs must be unique")
    selected = [case for case in corpus if mode in case["modes"]]
    results = []
    params = StdioServerParameters(command=sys.executable, args=SERVER, env=env)
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=90)) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        expected = {case["tool"] for case in corpus}
        if set(tools) != expected:
            raise AssertionError(f"Tool coverage drift: listed={set(tools)}, corpus={expected}")
        for tool in tools.values():
            covered = [case for case in corpus if case["tool"] == tool.name]
            if not any(case["kind"] == "success" for case in covered):
                raise AssertionError(f"Missing success coverage: {tool.name}")
            if tool.inputSchema.get("required") and not any(
                case["kind"] == "invalid" for case in covered
            ):
                raise AssertionError(f"Missing invalid-input coverage: {tool.name}")
            if not (tool.annotations and tool.annotations.readOnlyHint) and not any(
                case["kind"] == "safety" for case in covered
            ):
                raise AssertionError(f"Missing safety coverage: {tool.name}")
        for case in selected:
            try:
                protected = fingerprint(workspace, workspace.parent / "outside")
                reply = await session.call_tool(case["tool"], case["arguments"])
                error = "\n".join(getattr(item, "text", "") for item in reply.content)
                if case.get("error"):
                    assert reply.isError, "Expected an explicit MCP tool error"
                    assert case["error"].lower() in error.lower(), error
                    for path, digest in protected.items():
                        assert path.is_file(), f"Failed call deleted {path.name}"
                        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
                            f"Failed call changed {path.name}"
                        )
                else:
                    assert not reply.isError, error
                    data = reply.structuredContent
                    for path, value in case.get("equals", {}).items():
                        assert pointer(data, path) == value, f"Mismatch at {path}: {data}"
                    for path in case.get("required", []):
                        pointer(data, path)
                    for path in case.get("files", []):
                        target = workspace / path
                        assert target.is_file() and target.stat().st_size > 0, path
                for path in case.get("absent", []):
                    assert not (workspace / path).exists(), path
                results.append(
                    {"id": case["id"], "tool": case["tool"], "kind": case["kind"],
                     "passed": True}
                )
            except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
                results.append({"id": case["id"], "tool": case["tool"], "kind": case["kind"],
                                "passed": False, "reason": str(exc)})
    return results


def fixtures(output: Path) -> Path:
    sys.path.insert(0, str(ROOT / "evals"))
    from sampler_fake_cli import install

    workspace, outside = output / "workspace", output / "outside"
    for folder in (workspace / "maps", workspace / "assets", workspace / "jobs",
                   workspace / "existing", outside):
        folder.mkdir(parents=True)
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (outside / "outside.ssa").write_text("{}")
    (workspace / "maps/basecolor.png").write_bytes(b"fake-png")
    (workspace / "maps/normal.png").write_bytes(b"fake-png")
    (workspace / "assets/custom.sbsar").write_bytes(b"fake-sbsar")
    (workspace / "existing/keep.txt").write_text("keep")

    def build(directory, target, asset="Rock"):
        return {
            "schema": 1,
            "directory": directory,
            "project": {"name": "RockProject",
                        "asset": {"name": asset, "model": "openpbr",
                                  "preset": "base_material"}},
            "steps": [
                {"action": "import_images", "asset": asset,
                 "images": ["maps/basecolor.png"], "position": 0,
                 "import_option": "use_as_bitmap"},
                {"action": "insert_filter", "asset": asset, "filter": "Tiling",
                 "position": 1},
                {"action": "set_parameters", "asset": asset, "layer": "Tiling",
                 "parameters": {"Tiling": 3}},
                {"action": "apply_preset", "asset": asset, "layer": "Tiling",
                 "preset": "Wet"},
                {"action": "export", "asset": asset, "target": target,
                 "resolution": 64},
            ],
        }

    jobs = {
        "unreal": build("jobs/unreal", "unreal"),
        "unity": build("jobs/unity", "unity"),
        "blender": build("jobs/blender", "blender"),
        "generic": build("jobs/generic", "generic"),
        "existing": build("existing", "generic"),
        "fail": build("jobs/fail", "generic", asset="fail"),
        "b2m": {
            "schema": 1,
            "directory": "jobs/b2m",
            "project": {"name": "B2MProject"},
            "steps": [
                {"action": "create_asset", "name": "Photo", "empty": True},
                {"action": "import_images", "asset": "Photo",
                 "images": ["maps/basecolor.png"], "position": 0,
                 "import_option": "image_to_material_B2M"},
                {"action": "export", "asset": "Photo", "target": "generic",
                 "outputs": ["sbsar"]},
            ],
        },
    }
    for name, data in jobs.items():
        (workspace / "jobs" / f"{name}.json").write_text(json.dumps(data))
    invalid = build("jobs/invalid", "generic")
    invalid["directory"] = "../outside/escape"
    (workspace / "jobs/invalid.json").write_text(json.dumps(invalid))
    bad_schema = build("jobs/bad-schema", "generic")
    bad_schema["steps"] = [step for step in bad_schema["steps"]
                           if step["action"] != "export"]
    (workspace / "jobs/bad-schema.json").write_text(json.dumps(bad_schema))
    install(output / "app")
    return workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES)
    args = parser.parse_args()
    evaluator = ROOT / "node_modules/.bin/mcpeval"
    if not evaluator.exists():
        parser.error("Install the pinned evaluator with npm ci first")
    output_root = ROOT / ".tmp/evals"
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"sampler-{args.mode}-", dir=output_root))
    workspace = fixtures(output)
    env = dict(os.environ)
    env.pop("CAVI_SAMPLER_APP", None)
    env.pop("CAVI_SAMPLER_BIN", None)
    env.update({"MCPEVAL_HOME": str(output / "store"),
                "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
    if args.mode == "simulated":
        env["CAVI_SAMPLER_APP"] = str(output / "app/Fake Sampler.app")
    else:
        (output / "no-sampler").mkdir()
        env["CAVI_SAMPLER_BIN"] = str(output / "no-sampler")
    results = asyncio.run(exercise(args.mode, env, workspace))
    report = {
        "mode": args.mode,
        "application": APP,
        "cases": results,
        "native_evidence": False,
        "native_status": "fake Sampler or no executable; run tests/test_sampler_native.py for native",
        "model_evaluation": "not supported",
    }
    (output / "cases.json").write_text(json.dumps(report, indent=2) + "\n")
    manifest = "protocol" if args.mode == "protocol" else "core"
    command = [str(evaluator), "probe", "--server", f"{APP}-{args.mode}", "--manifest",
               str(ROOT / f"evals/sampler.{manifest}.manifest.json"), "--format", "json"]
    if args.mode == "protocol":
        command.append("--gate-only")
    command.extend(["--", sys.executable, *SERVER])
    with (output / "mcpeval.json").open("w") as stream:
        evaluated = subprocess.run(command, env=env, cwd=ROOT, stdout=stream, check=False)
    failed = [case for case in results if not case["passed"]]
    print(json.dumps({"mode": args.mode, "passed": len(results) - len(failed), "failed": failed,
                      "mcpeval_exit": evaluated.returncode, "artifacts": str(output)},
                     indent=2))
    if failed or evaluated.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
