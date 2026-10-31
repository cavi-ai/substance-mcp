"""Run deterministic Designer evaluations over the real stdio MCP surface."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
APP = "substance-designer"
SERVER = ["-m", "cavi_substance_mcp.designer.server"]
MODES = ["protocol", "simulated"]


def pointer(value, path):
    for key in path.strip("/").split("/"):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def fingerprint(*roots):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


async def exercise(mode, env, workspace):
    corpus = json.loads((ROOT / "evals/designer_cases.json").read_text())
    if len({case["id"] for case in corpus}) != len(corpus):
        raise ValueError("Corpus case IDs must be unique")
    selected = [case for case in corpus if mode in case["modes"]]
    results = []
    params = StdioServerParameters(command=sys.executable, args=SERVER, env=env)
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=90) as session,
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
            if tool.input_schema.get("required") and not any(
                case["kind"] == "invalid" for case in covered
            ):
                raise AssertionError(f"Missing invalid-input coverage: {tool.name}")
            if not (tool.annotations and tool.annotations.read_only_hint) and not any(
                case["kind"] == "safety" for case in covered
            ):
                raise AssertionError(f"Missing safety coverage: {tool.name}")
        for case in selected:
            try:
                protected = fingerprint(workspace, workspace.parent / "outside")
                reply = await session.call_tool(case["tool"], case["arguments"])
                error = "\n".join(getattr(item, "text", "") for item in reply.content)
                if case.get("error"):
                    assert reply.is_error, "Expected an explicit MCP tool error"
                    assert case["error"].lower() in error.lower(), error
                    for path, digest in protected.items():
                        assert path.is_file(), f"Failed call deleted {path.name}"
                        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
                            f"Failed call changed {path.name}")
                else:
                    assert not reply.is_error, error
                    data = reply.structured_content
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
                    {"id": case["id"], "tool": case["tool"], "kind": case["kind"], "passed": True})
            except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
                results.append({"id": case["id"], "tool": case["tool"], "kind": case["kind"],
                                "passed": False, "reason": str(exc)})
    return results


def fixtures(output: Path) -> Path:
    sys.path.insert(0, str(ROOT / "evals"))
    from designer_fake_cli import sbsar
    from PIL import Image

    workspace, outside = output / "workspace", output / "outside"
    for folder in (workspace / "maps", workspace / "meshes", workspace / "existing", outside):
        folder.mkdir(parents=True)
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (outside / "outside.sbsar").write_text(sbsar(["pkg://single"]))
    (outside / "low.fbx").write_text("outside-mesh")
    (workspace / "single.sbsar").write_text(sbsar(["pkg://single"]))
    (workspace / "multi.sbsar").write_text(
        sbsar(["pkg://metallic_roughness", "pkg://metallic_roughness_sheen"]))
    for name, mode in {"broken": "render_fail", "noreport": "render_noreport"}.items():
        data = json.loads(sbsar(["pkg://single"]))
        (workspace / f"{name}.sbsar").write_text(json.dumps({**data, "mode": mode}))
    (workspace / "source.sbs").write_text("fake-sbs")
    (workspace / "silent.sbs").write_text("fake-cook-silent")
    for name, value in {"basecolor": (204, 102, 51), "roughness": 64, "metallic": 255,
                        "ao": 200}.items():
        Image.new("RGB" if isinstance(value, tuple) else "L", (32, 32), value).save(
            workspace / f"maps/{name}.png")
    Image.new("L", (16, 16), 1).save(workspace / "maps/small.png")
    for name, text in {"low": "low", "high": "high", "silent": "fake-bake-silent",
                       "hang": "fake-bake-hang"}.items():
        (workspace / f"meshes/{name}.fbx").write_text(text)
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
    output = Path(tempfile.mkdtemp(prefix=f"designer-{args.mode}-", dir=output_root))
    workspace = fixtures(output)
    env = dict(os.environ)
    for key in ("CAVI_DESIGNER_APP", "CAVI_DESIGNER_BIN_DIR"):
        env.pop(key, None)
    env.update({"MCPEVAL_HOME": str(output / "store"), "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
    if args.mode == "simulated":
        from designer_fake_cli import ENV, install

        env[ENV] = str(install(output / "app"))
    else:
        (output / "no-designer").mkdir()
        env["CAVI_DESIGNER_BIN_DIR"] = str(output / "no-designer")
    results = asyncio.run(exercise(args.mode, env, workspace))
    report = {
        "mode": args.mode,
        "application": APP,
        "cases": results,
        "native_evidence": False,
        "native_status": "fake CLIs or no CLIs; run tests/test_designer_native.py for native",
        "model_evaluation": "not supported",
    }
    (output / "cases.json").write_text(json.dumps(report, indent=2) + "\n")
    manifest = "protocol" if args.mode == "protocol" else "core"
    command = [str(evaluator), "probe", "--server", f"{APP}-{args.mode}", "--manifest",
               str(ROOT / f"evals/designer.{manifest}.manifest.json"), "--format", "json"]
    if args.mode == "protocol":
        command.append("--gate-only")
    command.extend(["--", sys.executable, *SERVER])
    with (output / "mcpeval.json").open("w") as stream:
        evaluated = subprocess.run(command, env=env, cwd=ROOT, stdout=stream, check=False)
    failed = [case for case in results if not case["passed"]]
    print(json.dumps({"mode": args.mode, "passed": len(results) - len(failed), "failed": failed,
                      "mcpeval_exit": evaluated.returncode, "artifacts": str(output)}, indent=2))
    if failed or evaluated.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
