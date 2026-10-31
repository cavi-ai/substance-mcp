import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from home import isolate_home
from owned_client import OwnedPainterClient


class RemoteTests(unittest.TestCase):
    def test_close_refuses_unsaved_work_by_default(self):
        from evals.painter_simulator import PainterSimulator

        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("dirty")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(RuntimeError, "unsaved"):
                client.call("close_project", {"expected_file": "simulated-original.spp"})
            self.assertTrue(client.call("status")["open"])

    def test_close_requires_the_inspected_project_and_explicit_discard(self):
        from evals.painter_simulator import PainterSimulator

        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("dirty-unsaved")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(RuntimeError, "changed"):
                client.call("close_project", {"expected_file": "another.spp", "discard_unsaved": True})
            self.assertTrue(client.call("status")["open"])
            result = client.call("close_project", {"expected_file": None, "discard_unsaved": True})
            self.assertTrue(result["closed"])
            self.assertFalse(client.call("status")["open"])

    def test_successful_export_reports_maps_omitted_by_painter(self):
        from evals.painter_simulator import PainterSimulator

        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with PainterSimulator(workspace) as simulator:
                simulator.setup("omitted-export")
                result = OwnedPainterClient(port=simulator.port, workspace=workspace).call(
                    "export_textures",
                    {"directory": "textures", "preset": "resource://eval-preset", "dry_run": False},
                )
            self.assertEqual(result["files"], [str(workspace / "textures/Body.png")])
            self.assertEqual(result["omitted_files"], [str(workspace / "textures/Body_AO.png")])
            self.assertFalse((workspace / "textures/Body_AO.png").exists())

    def test_export_plan_satisfies_painter_configuration_contract(self):
        from evals.painter_simulator import PainterSimulator

        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            with PainterSimulator(workspace) as simulator:
                simulator.setup("open")
                result = OwnedPainterClient(port=simulator.port, workspace=workspace).call(
                    "export_textures", {"directory": "textures", "preset": "resource://eval-preset"}
                )
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["files"], [str(workspace.resolve() / "textures/Body.png")])
            self.assertFalse((workspace / "textures").exists())

    def test_unknown_operations_are_rejected_without_network(self):
        module = Path(__file__).resolve().parents[1] / "src/cavi_substance_mcp/painter/client.py"
        self.assertTrue(module.exists(), "Painter transport is not implemented")
        from cavi_substance_mcp.painter.client import PainterClient

        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            PainterClient().call("execute_python", {"code": "print(1)"})

    def test_remote_host_is_rejected(self):
        from cavi_substance_mcp.painter.client import PainterClient

        with self.assertRaisesRegex(ValueError, "loopback"):
            PainterClient(host="example.com")

    def test_official_http_contract_and_data_encoding(self):
        from cavi_substance_mcp.painter.client import PainterClient

        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "result": {"open": False}}).encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = PainterClient(port=server.server_port).call("status")
            self.assertFalse(result["open"])
            self.assertEqual(len(captured), 1)
            route, body = captured[0]
            self.assertEqual(route, "/run.json")
            self.assertEqual(set(body), {"python"})
            expression = base64.b64decode(body["python"]).decode()
            compile(expression, "remote_expression", "eval")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_data_cannot_inject_python(self):
        import sys
        from types import ModuleType

        from cavi_substance_mcp.painter.client import (
            SOURCE_KEY,
            bootstrap_expression,
            full_expression,
        )

        malicious = "'); __import__('os').system('false'); #"
        captured = []
        module = ModuleType("substance_painter")
        module._cavi_mcp_payloads = {
            SOURCE_KEY: lambda op, params, workspace: captured.append(params) or {}
        }
        previous = sys.modules.get("substance_painter")
        sys.modules["substance_painter"] = module
        try:
            eval(bootstrap_expression("open_project", {"path": malicious}, Path("/tmp/work")))
        finally:
            if previous is None:
                sys.modules.pop("substance_painter")
            else:
                sys.modules["substance_painter"] = previous
        self.assertEqual(captured, [{"path": malicious}])
        compile(full_expression("open_project", {"path": malicious}, Path("/tmp/w")), "x", "eval")

    def test_export_folder_must_be_new_and_inside_workspace(self):
        from cavi_substance_mcp.painter.operations import export_destination

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "workspace"):
                export_destination(root, "../escape")
            (root / "used").mkdir()
            with self.assertRaises(FileExistsError):
                export_destination(root, "used")
            self.assertEqual(export_destination(root, "new"), root.resolve() / "new")


class WorkspaceRootTests(unittest.TestCase):
    def setUp(self):
        self.home = isolate_home(self)

    def client(self, **kwargs):
        from cavi_substance_mcp.painter.client import PainterClient

        # Nothing listens on port 1, so a call that passes the workspace check ends in
        # PainterUnavailable instead of reaching an application.
        return PainterClient(port=1, **kwargs)

    def test_default_workspace_is_created_only_for_a_write(self):
        from cavi_substance_mcp.painter.client import PainterUnavailable

        default = self.home / "Documents/SubstanceMCP"
        client = self.client()
        self.assertEqual(client.workspace, default)
        with self.assertRaises(PainterUnavailable):
            client.call("texture_sets")
        self.assertFalse(default.exists(), "a read-only call created the workspace")
        with self.assertRaises(PainterUnavailable):
            client.call("save")
        self.assertTrue(default.is_dir())

    def test_missing_explicit_workspace_is_refused_and_not_created(self):
        missing = self.home / "not-there"
        with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(missing)}), \
                self.assertRaisesRegex(ValueError, "CAVI_SUBSTANCE_WORKSPACE.*does not exist"):
            self.client().call("save")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.client(workspace=missing).call("save")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
