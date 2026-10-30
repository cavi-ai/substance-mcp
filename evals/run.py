"""Run deterministic evaluations over the real stdio MCP surface."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from socketserver import BaseRequestHandler, TCPServer

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
APP = "substance"
MODES = ["protocol", "simulated"]


def pointer(value, path):
    for key in path.strip("/").split("/"):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


async def exercise(mode, env, workspace, adapter=None):
    corpus = json.loads((ROOT / "evals/cases.json").read_text())
    if len({case["id"] for case in corpus}) != len(corpus):
        raise ValueError("Corpus case IDs must be unique")
    selected = [case for case in corpus if mode in case["modes"]]
    results = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", f"cavi_{APP}_mcp.server"], env=env
    )
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
        lease_id = None
        for case in selected:
            try:
                if case.get("setup"):
                    if adapter is None:
                        raise AssertionError("Case requires the explicitly simulated adapter")
                    adapter.setup(case["setup"])
                protected = {
                    p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in list(workspace.rglob("*"))
                    + list((workspace.parent / "outside").rglob("*"))
                    if p.is_file()
                }

                arguments = dict(case["arguments"])
                if mode != "protocol":
                    needs_lease = (case["tool"] not in {"session_control", "generate_mesh"}
                                   and not tools[case["tool"]].annotations.readOnlyHint
                                   and not case.get("without_lease"))
                    if needs_lease or case.get("fresh_lease"):
                        if lease_id:
                            released = await session.call_tool("session_control", {
                                "action": "release", "lease_id": lease_id})
                            assert not released.isError, str(released.content)
                            lease_id = None
                        state = await session.call_tool("session_control", {"action": "inspect"})
                        assert not state.isError, str(state.content)
                        if needs_lease:
                            claim = await session.call_tool("session_control", {
                                "action": "claim", "owner": "eval-fixture",
                                "expected_project": state.structuredContent["project_id"]})
                            assert not claim.isError, str(claim.content)
                            lease_id = claim.structuredContent["lease_id"]
                    if arguments.get("expected_project") == "$project":
                        state = await session.call_tool("session_control", {"action": "inspect"})
                        arguments["expected_project"] = state.structuredContent["project_id"]
                    if arguments.get("lease_id") == "$lease" or needs_lease:
                        arguments["lease_id"] = lease_id
                reply = await session.call_tool(case["tool"], arguments)
                if not reply.isError and case["tool"] == "session_control":
                    if arguments.get("action") == "claim":
                        lease_id = reply.structuredContent["lease_id"]
                    elif arguments.get("action") == "release":
                        lease_id = None
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
                    if case.get("image"):
                        blocks = [item for item in reply.content if item.type == "image"]
                        assert len(blocks) == 1 and blocks[0].mimeType == "image/png"
                        assert base64.b64decode(blocks[0].data).startswith(b"\x89PNG\r\n\x1a\n")
                    for path in case.get("files", []):
                        target = workspace / path
                        assert target.is_file() and target.stat().st_size > 0, path
                for path in case.get("absent", []):
                    assert not (workspace / path).exists(), path
                results.append(
                    {"id": case["id"], "tool": case["tool"], "kind": case["kind"], "passed": True}
                )
            except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
                results.append(
                    {
                        "id": case["id"],
                        "tool": case["tool"],
                        "kind": case["kind"],
                        "passed": False,
                        "reason": str(exc),
                    }
                )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES)
    args = parser.parse_args()
    evaluator = ROOT / "node_modules/.bin/mcpeval"
    if not evaluator.exists():
        parser.error("Install the pinned evaluator with npm ci first")
    output_root = ROOT / ".tmp/evals"
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"{args.mode}-", dir=output_root))
    workspace = output / "workspace"
    workspace.mkdir()
    (output / "outside").mkdir()
    (output / "outside/input.spp").write_bytes(b"outside-project")
    (output / "outside/input.obj").write_bytes(b"outside-mesh")
    (output / "outside/high.obj").write_bytes(b"outside-mesh")
    (output / "outside/material.sbsar").write_bytes(b"outside-sbsar")
    (output / "outside/template.spt").write_bytes(b"outside-template")
    (output / "outside/external.png").write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82')
    (workspace / "escape").symlink_to(output / "outside", target_is_directory=True)
    for path, content in {
        "existing.blend": b"preserve-project",
        "existing.glb": b"preserve-mesh",
        "existing.png": b"preserve-preview",
        "input.spp": b"simulated-project",
        "input.obj": b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        "high.obj": b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        "cage.obj": b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        "material.sbsar": b"simulated-sbsar",
        "template.spt": b"simulated-template",
    }.items():
        (workspace / path).write_bytes(content)
    (workspace / "color.png").write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'))
    (workspace / "existing").mkdir()
    env = dict(os.environ)
    env.update(
        {
            "MCPEVAL_HOME": str(output / "store"),
            "CAVI_SUBSTANCE_WORKSPACE": str(workspace),
        }
    )
    with ExitStack() as stack:
        adapter = None
        if args.mode == "simulated":
            from painter_simulator import PainterSimulator

            adapter = stack.enter_context(PainterSimulator(workspace))
            env["CAVI_PAINTER_PORT"] = str(adapter.port)
        else:
            # Accept then close: deterministic unavailability without accessing Painter.
            endpoint = stack.enter_context(TCPServer(("127.0.0.1", 0), BaseRequestHandler))
            worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
            worker.start()
            stack.callback(worker.join)
            stack.callback(endpoint.shutdown)
            env["CAVI_PAINTER_PORT"] = str(endpoint.server_address[1])
        results = asyncio.run(exercise(args.mode, env, workspace, adapter))
        report = {
            "mode": args.mode,
            "application": APP,
            "cases": results,
            "native_evidence": False,
            "native_status": "not run by the simulated or disconnected evaluator",
            "model_evaluation": "not supported",
        }
        (output / "cases.json").write_text(json.dumps(report, indent=2) + "\n")
        manifest = "protocol" if args.mode == "protocol" else "core"
        command = [
            str(evaluator),
            "probe",
            "--server",
            f"{APP}-{args.mode}",
            "--manifest",
            str(ROOT / f"evals/{manifest}.manifest.json"),
            "--format",
            "json",
        ]
        if args.mode == "protocol":
            command.append("--gate-only")
        command.extend(["--", sys.executable, "-m", f"cavi_{APP}_mcp.server"])
        with (output / "mcpeval.json").open("w") as stream:
            evaluated = subprocess.run(command, env=env, cwd=ROOT, stdout=stream, check=False)
        failed = [case for case in results if not case["passed"]]
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "passed": len(results) - len(failed),
                    "failed": failed,
                    "mcpeval_exit": evaluated.returncode,
                    "artifacts": str(output),
                },
                indent=2,
            )
        )
        if failed or evaluated.returncode:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
