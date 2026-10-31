"""Run deterministic evaluations over the real stdio MCP surface."""

import asyncio
import base64
import hashlib
import json
import threading
from contextlib import ExitStack
from socketserver import BaseRequestHandler, TCPServer

import common

APP = "substance"
SERVER = ["-m", "cavi_substance_mcp.painter.server"]


class PainterHooks(common.Hooks):
    """Painter's cases: simulator setup, the edit lease, and replies that carry an image."""

    def __init__(self, mode, adapter=None):
        self.mode, self.adapter, self.lease_id = mode, adapter, None

    def setup(self, case):
        if case.get("setup"):
            if self.adapter is None:
                raise AssertionError("Case requires the explicitly simulated adapter")
            self.adapter.setup(case["setup"])

    async def prepare(self, session, tools, case, arguments):
        if self.mode == "protocol":
            return arguments
        needs_lease = (case["tool"] != "session_control"
                       and not tools[case["tool"]].annotations.read_only_hint
                       and not case.get("without_lease"))
        # keep_lease: continue the previous case's lease so ownership spans cases.
        keep = needs_lease and case.get("keep_lease") and self.lease_id
        if (needs_lease or case.get("fresh_lease")) and not keep:
            if self.lease_id:
                released = await session.call_tool("session_control", {
                    "action": "release", "lease_id": self.lease_id})
                assert not released.is_error, str(released.content)
                self.lease_id = None
            state = await session.call_tool("session_control", {"action": "inspect"})
            assert not state.is_error, str(state.content)
            if needs_lease:
                claim = await session.call_tool("session_control", {
                    "action": "claim", "owner": "eval-fixture",
                    "expected_project": state.structured_content["project_id"]})
                assert not claim.is_error, str(claim.content)
                self.lease_id = claim.structured_content["lease_id"]
        if arguments.get("expected_project") == "$project":
            state = await session.call_tool("session_control", {"action": "inspect"})
            arguments["expected_project"] = state.structured_content["project_id"]
        if arguments.get("lease_id") == "$lease" or needs_lease:
            arguments["lease_id"] = self.lease_id
        return arguments

    def observe(self, case, arguments, reply):
        if not reply.is_error and case["tool"] == "session_control":
            if arguments.get("action") == "claim":
                self.lease_id = reply.structured_content["lease_id"]
            elif arguments.get("action") == "release":
                self.lease_id = None

    def check_success(self, case, reply):
        if case.get("image"):
            blocks = [item for item in reply.content if item.type == "image"]
            assert len(blocks) == 1 and blocks[0].mime_type == "image/png"
            assert base64.b64decode(blocks[0].data).startswith(b"\x89PNG\r\n\x1a\n")


def main():
    mode, evaluator, output = common.start()
    workspace = common.make_workspace(output, ["existing"])
    (output / "outside/input.spp").write_bytes(b"outside-project")
    (output / "outside/input.obj").write_bytes(b"outside-mesh")
    (output / "outside/high.obj").write_bytes(b"outside-mesh")
    (output / "outside/material.sbsar").write_bytes(b"outside-sbsar")
    (output / "outside/template.spt").write_bytes(b"outside-template")
    (output / "outside/external.png").write_bytes(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00'
        b'\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
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
    (workspace / "color.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ"
        "/pLvAAAAAElFTkSuQmCC"
    ))
    material = (workspace / "material.sbsar").read_bytes()
    (workspace / "material-manifest.json").write_text(json.dumps({
        "schema": 1, "app": "designer", "operation": "cook", "inputs": [],
        "outputs": [{"path": "material.sbsar", "size": len(material),
                     "sha256": hashlib.sha256(material).hexdigest()}],
    }))
    env = common.environment(output, workspace)
    with ExitStack() as stack:
        adapter = None
        if mode == "simulated":
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
        results = asyncio.run(common.exercise(
            "cases.json", mode, SERVER, env, workspace, PainterHooks(mode, adapter)))
        common.finish(
            mode=mode, evaluator=evaluator, output=output, results=results, env=env, app=APP,
            server=SERVER, manifest_prefix="",
            native_status="not run by the simulated or disconnected evaluator",
        )


if __name__ == "__main__":
    main()
