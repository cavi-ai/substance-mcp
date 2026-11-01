import base64
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from clock import Clock
from owned_client import OwnedPainterClient, wait_ready

from cavi_substance_mcp.painter import operations as lease
from cavi_substance_mcp.painter.client import PainterClient, PainterTimeout, PainterUnavailable
from evals.painter_simulator import PainterSimulator

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0p0AAAAASUVORK5CYII="
)


def serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


class TransportTests(unittest.TestCase):
    def test_timeout_and_refused_are_distinct(self):
        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                time.sleep(1)

        server, worker = serve(Slow)
        try:
            with tempfile.TemporaryDirectory() as folder:
                client = PainterClient(port=server.server_port, workspace=folder)
                with self.assertRaisesRegex(PainterTimeout, "outcome unknown"):
                    client.call("status", timeout=0.2)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaisesRegex(PainterUnavailable, "not reachable") as caught:
            PainterClient(port=port).call("status")
        self.assertNotIsInstance(caught.exception, TimeoutError)

    def test_legit_error_key_in_result_is_not_a_failure(self):
        replies = [{"ok": True, "result": {"error": "field named error"}},
                   {"ok": False, "error": "ValueError: real failure"},
                   {"error": {"description": "SyntaxError: native"}}]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(replies.pop(0)).encode())

        server, worker = serve(Handler)
        try:
            client = PainterClient(port=server.server_port)
            self.assertEqual(client.call("status"), {"error": "field named error"})
            with self.assertRaisesRegex(RuntimeError, "real failure"):
                client.call("status")
            with self.assertRaisesRegex(RuntimeError, "scripting error: SyntaxError"):
                client.call("status")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_payload_is_sent_once_then_cached(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            client = PainterClient(port=simulator.port, workspace=folder)
            client.call("status")
            self.assertEqual(len(simulator.requests), 2)
            small, full = simulator.requests
            self.assertGreater(full, 10 * small)
            client.call("status")
            client.call("session_control", {"action": "inspect"})
            self.assertEqual(len(simulator.requests), 4)
            self.assertTrue(all(size < 2000 for size in simulator.requests[2:]))

    def test_simulator_runs_only_the_client_expressions(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            marker = Path(folder) / "ran.txt"
            expression = f"__import__('pathlib').Path({str(marker)!r}).write_text('ran')"
            body = json.dumps({"python": base64.b64encode(expression.encode()).decode()})
            request = urllib.request.Request(
                f"http://127.0.0.1:{simulator.port}/run.json", body.encode(),
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                reply = json.loads(response.read())
            self.assertFalse(marker.exists())
            self.assertIn("not an expression the client sends", reply["error"]["description"])
            client = PainterClient(port=simulator.port, workspace=folder)
            self.assertTrue(client.call("status")["connected"])

    def test_read_only_status_does_not_create_workspace(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            missing = Path(folder) / "not-yet"
            status = PainterClient(port=simulator.port, workspace=missing).call("status")
            self.assertFalse(status["workspace_exists"])
            self.assertFalse(missing.exists())
            self.assertIn("edition_state", status)


class SharedLeaseTests(unittest.TestCase):
    def test_every_payload_shares_one_lease(self):
        module = ModuleType("substance_painter")
        current = lease.shared_lease(module)
        self.assertIsInstance(current, lease.Lease)
        self.assertIs(getattr(module, lease.LEASE_ATTR), current)
        self.assertIs(lease.shared_lease(module), current)

    def test_concurrent_first_use_creates_one_lease(self):
        module = ModuleType("substance_painter")
        start = threading.Barrier(8)
        found = []

        def first_use():
            start.wait()
            found.append(lease.shared_lease(module))

        workers = [threading.Thread(target=first_use) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(len(found), 8)
        self.assertEqual(len({id(item) for item in found}), 1)

    def test_a_lease_object_from_another_version_is_left_alone_and_not_adopted(self):
        module = ModuleType("substance_painter")
        older = SimpleNamespace(lock=threading.RLock(), token="old-token", owner="agent-old",
                                project="project-A", expires=time.monotonic() + 100,
                                touched=time.monotonic())
        for name in (f"_cavi_mcp_lease_v{lease.LEASE_VERSION - 1}", "_cavi_mcp_lease"):
            setattr(module, name, older)
        current = lease.shared_lease(module)
        self.assertIsNot(current, older)
        self.assertIsNone(current.token)
        self.assertFalse(current.active())
        self.assertIs(module._cavi_mcp_lease, older)
        self.assertIs(getattr(module, f"_cavi_mcp_lease_v{lease.LEASE_VERSION - 1}"), older)

    def test_an_existing_object_under_the_current_name_is_reused(self):
        module = ModuleType("substance_painter")
        existing = SimpleNamespace()
        setattr(module, lease.LEASE_ATTR, existing)
        self.assertIs(lease.shared_lease(module), existing)


class BreakTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000.0)
        self.session = lease.Lease(clock=self.clock)
        self.token = self.session.control({"action": "claim", "owner": "agent-A",
                                           "expected_project": "P", "ttl_seconds": 3600}, "P")[
            "lease_id"]

    def brk(self, project="P"):
        return self.session.control({"action": "break", "expected_project": project}, "P")

    def test_recent_lease_cannot_be_broken(self):
        self.clock.now = 1000.0 + lease.BREAK_IDLE_SECONDS - 1
        with self.assertRaisesRegex(RuntimeError, "idle"):
            self.brk()
        self.assertTrue(self.session.active())

    def test_edit_and_renew_reset_idle(self):
        self.clock.now = 1250.0
        with self.session.edit(self.token, lambda: "P"):
            pass
        self.clock.now = 1400.0
        with self.assertRaisesRegex(RuntimeError, "idle"):
            self.brk()
        self.session.control({"action": "renew", "lease_id": self.token,
                              "ttl_seconds": 3600}, "P")
        self.clock.now = 1650.0
        with self.assertRaisesRegex(RuntimeError, "idle"):
            self.brk()

    def test_idle_lease_break_requires_project_and_returns_owner(self):
        self.clock.now = 1000.0 + lease.BREAK_IDLE_SECONDS
        with self.assertRaisesRegex(RuntimeError, "project changed"):
            self.brk("other")
        result = self.brk()
        self.assertEqual(result["previous_owner"], "agent-A")
        self.assertFalse(result["active"])
        with self.assertRaisesRegex(RuntimeError, "lease"), self.session.edit(
                self.token, lambda: "P"):
            self.fail("Broken lease still edits")
        with self.assertRaisesRegex(RuntimeError, "No active lease"):
            self.brk()


class ProjectTests(unittest.TestCase):
    def test_wait_ready_polls_until_edition_state(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("loading")
            result = wait_ready(simulator.port, folder, 10, 0.01)
            self.assertTrue(result["ready"])
            self.assertTrue(result["edition_state"])
            self.assertFalse(result["busy"])

    def test_wait_ready_times_out_with_final_status(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("closed")
            started = time.monotonic()
            result = wait_ready(simulator.port, folder, 1, 0.1)
            self.assertFalse(result["ready"])
            self.assertFalse(result["open"])
            self.assertLess(time.monotonic() - started, 3)

    def test_missing_expected_file_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(
                    RuntimeError, "ValueError: Missing required.*expected_file"):
                client.call("close_project", {})
            self.assertTrue(client.call("status")["open"])

    def test_create_maps_settings(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            (Path(folder) / "mesh.obj").write_text("v 0 0 0\n")
            simulator.setup("closed")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(RuntimeError, "power of two"):
                client.call("create_project", {"path": "mesh.obj",
                                               "default_texture_resolution": 1000})
            self.assertFalse(simulator.opened)
            client.call("create_project", {"path": "mesh.obj", "normal_map_format": "DirectX",
                                           "default_texture_resolution": 4096})
            self.assertEqual(simulator.created_settings.normal_map_format, "DirectX")
            self.assertEqual(simulator.created_settings.default_texture_resolution, 4096)

    def test_save_in_place_requires_workspace_file(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("dirty-unsaved")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(RuntimeError, "never saved"):
                client.call("save")
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("workspace-dirty")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            result = client.call("save")
            self.assertTrue(result["saved"])
            self.assertFalse(result["needs_saving"])
            self.assertEqual((Path(folder) / "input.spp").read_bytes(), b"simulated-saved-spp")

    def test_texture_sets_report_resolution(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            sets = PainterClient(port=simulator.port, workspace=folder).call("texture_sets")
            self.assertEqual(sets["texture_sets"][0]["resolution"], {"width": 2048, "height": 2048})


class TextureCleanupTests(unittest.TestCase):
    def fill(self, folder, simulator):
        (Path(folder) / "color.png").write_bytes(PNG)
        client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
        return client.call("create_texture_fill", {
            "texture_set": "Body", "name": "Failed", "base_color_texture": "color.png"})

    def test_failure_after_import_removes_temp_and_reports_resource(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("bitmap-failure")
            with self.assertRaisesRegex(RuntimeError, "temporary files removed.*no Painter API"):
                self.fill(folder, simulator)
            self.assertEqual(list(Path(folder).glob(".cavi-bitmap-*")), [])
            self.assertEqual(simulator.layers, [])

    def test_import_failure_removes_temp(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("import-failure")
            with self.assertRaisesRegex(RuntimeError, "Texture import failed"):
                self.fill(folder, simulator)
            self.assertEqual(list(Path(folder).glob(".cavi-bitmap-*")), [])
            self.assertEqual(simulator.resources, [])

    def test_unremovable_temp_is_reported(self):
        real = shutil.rmtree

        def stuck(path, *args, **kwargs):
            # With ignore_errors, an undeletable directory is silently left behind.
            if not Path(path).name.startswith(".cavi-bitmap-"):
                real(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("bitmap-failure")
            with (
                patch("shutil.rmtree", stuck),
                self.assertRaisesRegex(RuntimeError, r"temporary files kept.*\.cavi-bitmap-"),
            ):
                self.fill(folder, simulator)
            self.assertEqual(len(list(Path(folder).glob(".cavi-bitmap-*"))), 1)


if __name__ == "__main__":
    unittest.main()
