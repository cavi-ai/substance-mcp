"""Painter HTTP transport: payload loading, malformed replies, deadlines and readiness."""

import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
import unittest

from cavi_substance_mcp.painter.client import PainterClient, PainterTimeout, PainterUnavailable


def raw_server(respond):
    """Serve each connection with respond(connection); the reply bytes are fully scripted."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                continue
            try:
                connection.settimeout(5)
                buffer = b""
                while b"\r\n\r\n" not in buffer:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                head, _, body = buffer.partition(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n")[1:]:
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value)
                while len(body) < length:
                    body += connection.recv(65536)
                respond(connection)
            except OSError:
                pass
            finally:
                connection.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()

    def close():
        stop.set()
        worker.join()
        listener.close()

    return listener.getsockname()[1], close


class PayloadSourceTests(unittest.TestCase):
    def test_payload_source_is_read_as_utf8(self):
        script = textwrap.dedent("""
            import json
            from pathlib import Path

            seen = []
            real = Path.read_text

            def spy(self, *args, **kwargs):
                seen.append([self.name, kwargs.get("encoding", args[0] if args else None)])
                return real(self, *args, **kwargs)

            Path.read_text = spy
            import cavi_substance_mcp.painter.client  # noqa: F401
            print(json.dumps(seen))
        """)
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True)
        reads = dict(json.loads(done.stdout.splitlines()[-1]))
        self.assertEqual(reads["operations.py"], "utf-8")


class MalformedReplyTests(unittest.TestCase):
    def reply(self, data):
        port, close = raw_server(lambda connection: connection.sendall(data))
        self.addCleanup(close)
        return PainterClient(port=port)

    def test_bad_status_line_is_unavailable_not_a_raw_http_error(self):
        client = self.reply(b"THIS IS NOT HTTP\r\n\r\n")
        with self.assertRaisesRegex(PainterUnavailable, "outcome unknown"):
            client.call("status")

    def test_truncated_chunked_body_is_unavailable_not_a_raw_http_error(self):
        client = self.reply(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20\r\nonly-part-of-the-chunk"
        )
        with self.assertRaisesRegex(PainterUnavailable, "outcome unknown"):
            client.call("status")


class DeadlineTests(unittest.TestCase):
    def trickle(self, head, seconds=4.0, pause=0.05):
        def respond(connection):
            connection.sendall(head)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                connection.sendall(b" ")
                time.sleep(pause)

        port, close = raw_server(respond)
        self.addCleanup(close)
        return PainterClient(port=port)

    def test_body_trickle_cannot_outlive_the_call_deadline(self):
        # Each recv() succeeds within the per-socket timeout, so only a wall clock stops this.
        client = self.trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        started = time.monotonic()
        with self.assertRaisesRegex(PainterTimeout, "outcome unknown"):
            client.call("status", timeout=0.5)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_header_trickle_cannot_outlive_the_call_deadline(self):
        client = self.trickle(b"HTTP/1.1 200 OK\r\nX-Slow: ")
        started = time.monotonic()
        with self.assertRaisesRegex(PainterTimeout, "outcome unknown"):
            client.call("status", timeout=0.5)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_fast_reply_still_succeeds(self):
        body = json.dumps({"ok": True, "result": {"open": True}}).encode()
        port, close = raw_server(lambda connection: connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        ))
        self.addCleanup(close)
        self.assertEqual(PainterClient(port=port).call("status", timeout=0.5), {"open": True})


READY = {"connected": True, "open": True, "busy": False, "edition_state": True,
         "baking": False, "mesh_reload": None}


def scripted(*replies):
    """Serve one ok-wrapped result or int HTTP status per request, repeating the last."""
    queue = list(replies)

    def respond(connection):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, int):
            connection.sendall(
                f"HTTP/1.1 {reply} Error\r\nContent-Length: 0\r\n\r\n".encode()
            )
            return
        body = json.dumps({"ok": True, "result": reply}).encode()
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    return respond


class WaitReadyTransportTests(unittest.TestCase):
    def client(self, *replies):
        port, close = raw_server(scripted(*replies))
        self.addCleanup(close)
        return PainterClient(port=port)

    def test_http_5xx_while_painter_starts_is_not_ready_yet(self):
        result = self.client(503, 500, READY).wait_ready(5, 0.01)
        self.assertTrue(result["ready"])

    def test_persistent_5xx_times_out_as_not_ready(self):
        result = self.client(503).wait_ready(0.5, 0.05)
        self.assertFalse(result["ready"])
        self.assertFalse(result["connected"])
        self.assertIn("503", result["reason"])

    def test_client_errors_are_still_failures(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            self.client(404).wait_ready(1, 0.05)


class ReadinessTests(unittest.TestCase):
    def client(self, *replies):
        port, close = raw_server(scripted(*replies))
        self.addCleanup(close)
        return PainterClient(port=port)

    def test_baking_without_busy_is_not_ready(self):
        result = self.client({**READY, "baking": True}).wait_ready(0.4, 0.05)
        self.assertFalse(result["ready"])
        self.assertEqual(result["waiting_on"], ["baking"])

    def test_pending_mesh_reload_is_not_ready(self):
        pending = {**READY, "mesh_reload": {"pending": True, "status": None, "path": "m.obj"}}
        result = self.client(pending).wait_ready(0.4, 0.05)
        self.assertFalse(result["ready"])
        self.assertEqual(result["waiting_on"], ["mesh_reload"])

    def test_ready_after_baking_and_reload_finish_reports_reload_status(self):
        baking = {**READY, "baking": True}
        pending = {**READY, "mesh_reload": {"pending": True, "status": None, "path": "m.obj"}}
        done = {**READY, "mesh_reload": {"pending": False, "status": "SUCCESS", "path": "m.obj"}}
        result = self.client(baking, pending, done).wait_ready(5, 0.01)
        self.assertTrue(result["ready"])
        self.assertEqual(result["waiting_on"], [])
        self.assertEqual(result["mesh_reload"]["status"], "SUCCESS")

    def test_failed_mesh_reload_fails_clearly_instead_of_waiting(self):
        failed = {**READY, "mesh_reload": {"pending": False, "status": "ERROR", "path": "m.obj"}}
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "Mesh reload failed.*m.obj"):
            self.client(failed).wait_ready(30, 0.05)
        self.assertLess(time.monotonic() - started, 3)

    def test_unreachable_painter_reports_connection_as_the_wait(self):
        port, close = raw_server(lambda connection: None)
        close()
        result = PainterClient(port=port).wait_ready(0.3, 0.05)
        self.assertFalse(result["ready"])
        self.assertEqual(result["waiting_on"], ["connection"])


if __name__ == "__main__":
    unittest.main()
