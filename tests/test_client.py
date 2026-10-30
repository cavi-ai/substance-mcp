import base64
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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
        module = Path(__file__).resolve().parents[1] / "src/cavi_substance_mcp/client.py"
        self.assertTrue(module.exists(), "Painter transport is not implemented")
        from cavi_substance_mcp.client import PainterClient

        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            PainterClient().call("execute_python", {"code": "print(1)"})

    def test_remote_host_is_rejected(self):
        from cavi_substance_mcp.client import PainterClient

        with self.assertRaisesRegex(ValueError, "loopback"):
            PainterClient(host="example.com")

    def test_official_http_contract_and_data_encoding(self):
        from cavi_substance_mcp.client import PainterClient

        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"open": False}).encode())

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
        from cavi_substance_mcp.client import expression_for

        malicious = "'); __import__('os').system('false'); #"
        expression = expression_for("open_project", {"path": malicious}, Path("/tmp/work"))
        captured = []
        import builtins

        original = builtins.exec

        def run_source(source, namespace):
            namespace["execute"] = lambda op, params, workspace: captured.append(params) or {}

        builtins.exec = run_source
        try:
            eval(expression, {"__builtins__": {"__import__": __import__}})
        finally:
            builtins.exec = original
        self.assertEqual(captured, [{"path": malicious}])

    def test_export_folder_must_be_new_and_inside_workspace(self):
        from cavi_substance_mcp.painter_operations import export_destination

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "workspace"):
                export_destination(root, "../escape")
            (root / "used").mkdir()
            with self.assertRaises(FileExistsError):
                export_destination(root, "used")
            self.assertEqual(export_destination(root, "new"), root.resolve() / "new")


if __name__ == "__main__":
    unittest.main()
