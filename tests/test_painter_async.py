"""The Painter MCP server stays responsive while Painter works, and Painter calls never overlap."""

import asyncio
import inspect
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cavi_substance_mcp.painter import client as painter_client
from cavi_substance_mcp.painter import server
from cavi_substance_mcp.painter.client import PainterClient, PainterTimeout
from evals.painter_simulator import PainterSimulator


class FakePainter:
    """Painter's scripting server: one main thread, so requests run strictly one at a time."""

    def wait_until_running(self, seconds=10):
        """Block until a request is inside Painter, so the sending client holds its gate."""
        deadline = time.monotonic() + seconds
        while self.running == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

    def __init__(self, delay=0.0, result=None):
        self.delay, self.result = delay, result or {"connected": True, "open": False}
        self.running = self.peak = self.total = 0
        self.guard, self.main_thread = threading.Lock(), threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                with owner.guard:
                    owner.running += 1
                    owner.total += 1
                    owner.peak = max(owner.peak, owner.running)
                try:
                    with owner.main_thread:
                        time.sleep(owner.delay)
                        body = json.dumps({"ok": True, "result": owner.result}).encode()
                finally:
                    with owner.guard:
                        owner.running -= 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


class SerializationTests(unittest.TestCase):
    def test_concurrent_calls_never_overlap_at_painter(self):
        with FakePainter(delay=0.1) as painter:
            clients = [PainterClient(port=painter.port) for _ in range(4)]
            workers = [threading.Thread(target=client.call, args=("layer_stack", {})) for client
                       in clients]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
            self.assertEqual(painter.total, 4)
            self.assertEqual(painter.peak, 1)

    def test_status_does_not_queue_behind_a_long_call(self):
        with FakePainter(delay=0.8) as painter, patch.object(
                painter_client, "STATUS_QUEUE_SECONDS", 0.1):
            worker = threading.Thread(
                target=PainterClient(port=painter.port).call, args=("export_presets",))
            worker.start()
            painter.wait_until_running()
            started = time.monotonic()
            status = PainterClient(port=painter.port).call("status")
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(status["busy"])
            self.assertEqual(status["in_flight"], "export_presets")
            worker.join(10)
            self.assertEqual(painter.total, 1)

    def test_queued_call_that_cannot_start_in_time_was_never_sent(self):
        with FakePainter(delay=0.8) as painter:
            worker = threading.Thread(
                target=PainterClient(port=painter.port).call, args=("export_presets",))
            worker.start()
            painter.wait_until_running()
            with self.assertRaisesRegex(painter_client.PainterBusy, "not sent") as caught:
                PainterClient(port=painter.port).call("texture_sets", timeout=0.3)
            self.assertIsInstance(caught.exception, PainterTimeout)
            worker.join(10)
            self.assertEqual(painter.total, 1)

    def test_lock_is_released_after_a_failed_call(self):
        with FakePainter() as painter:
            client = PainterClient(port=painter.port)
            with patch.object(PainterClient, "_post", side_effect=RuntimeError("boom")), \
                    self.assertRaises(RuntimeError):
                client.call("texture_sets")
            self.assertEqual(client.call("texture_sets", timeout=1), painter.result)


class TimeoutTests(unittest.TestCase):
    def timeout_of(self, operation):
        seen = []
        client = PainterClient(workspace=tempfile.gettempdir())
        with patch.object(PainterClient, "_post",
                          lambda self, expression, timeout: seen.append(timeout) or
                          {"ok": True, "result": {}}):
            client.call(operation)
        return seen[0]

    def test_layer_edit_is_not_given_the_export_timeout(self):
        self.assertEqual(self.timeout_of("layer_edit"), 120.0)

    def test_save_and_export_keep_the_long_timeout(self):
        for operation in ("save", "save_copy", "export_textures"):
            self.assertEqual(self.timeout_of(operation), 600.0, operation)

    def test_other_calls_use_the_default_timeout(self):
        self.assertEqual(self.timeout_of("status"), 60.0)
        self.assertEqual(self.timeout_of("texture_sets"), 60.0)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_painter_tool_is_a_coroutine(self):
        tools = await server.mcp.list_tools()
        self.assertTrue(tools)
        # The module-level names are the undecorated coroutines (core.mcp.Server.tool).
        self.assertEqual([tool.name for tool in tools
                          if not inspect.iscoroutinefunction(getattr(server, tool.name))], [])

    async def test_slow_call_does_not_block_the_event_loop_or_other_handlers(self):
        def slow(self, operation, params=None, *, lease_id=None, timeout=None, cancelled=None):
            if operation == "save":
                time.sleep(0.6)
            return {"operation": operation}

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        with patch.object(PainterClient, "call", slow):
            beat = asyncio.create_task(heartbeat())
            saving = asyncio.create_task(server.project(action="save", lease_id="x"))
            await asyncio.sleep(0.1)
            started = time.monotonic()
            status = await server.painter_status()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(status, {"operation": "status"})
            self.assertFalse(saving.done())
            self.assertEqual(await saving, {"operation": "save"})
            beat.cancel()
        self.assertGreater(ticks, 15)

    async def test_cancelling_a_request_returns_without_waiting_for_painter(self):
        finished = threading.Event()

        def slow(self, operation, params=None, *, lease_id=None, timeout=None, cancelled=None):
            time.sleep(0.8)
            finished.set()
            return {}

        with patch.object(PainterClient, "call", slow):
            task = asyncio.create_task(server.project(action="save", lease_id="x"))
            await asyncio.sleep(0.1)
            started = time.monotonic()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertLess(time.monotonic() - started, 0.5)
            # Let the abandoned worker finish before the patch ends.
            self.assertTrue(await asyncio.to_thread(finished.wait, 5))


class CancelledQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_request_cancelled_while_queued_never_reaches_painter(self):
        with FakePainter(delay=0.8) as painter, patch.dict(
                os.environ, {"CAVI_PAINTER_PORT": str(painter.port)}):
            running = threading.Thread(
                target=PainterClient(port=painter.port).call, args=("export_presets",))
            running.start()
            await asyncio.sleep(0.2)
            queued = asyncio.create_task(server.inspect(action="texture_sets"))
            await asyncio.sleep(0.3)
            self.assertFalse(queued.done())
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            running.join(10)
            await asyncio.sleep(0.3)  # time for a wrongly surviving worker to send it
            self.assertEqual(painter.total, 1)


class StdioResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def session(self, port, work):
        env = {**os.environ, "CAVI_PAINTER_PORT": str(port)}
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "cavi_substance_mcp.painter.server"], env=env)
        async with stdio_client(params) as (reader, writer), ClientSession(
                reader, writer) as session:
            await session.initialize()
            return await work(session)

    async def test_ping_and_listing_answer_while_a_painter_call_is_in_flight(self):
        async def work(session):
            call = asyncio.create_task(session.call_tool("painter_status", {}))
            await asyncio.sleep(0.5)
            started = time.monotonic()
            await session.send_ping()
            await session.list_tools()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0, "the server froze while Painter worked")
            self.assertFalse(call.done(), "the Painter call should still be running")
            reply = await call
            self.assertFalse(reply.is_error)

        with FakePainter(delay=1.6) as painter:
            await self.session(painter.port, work)

    async def test_wait_ready_polls_without_freezing_the_server(self):
        async def work(session):
            call = asyncio.create_task(session.call_tool(
                "project", {"action": "wait_ready", "timeout_seconds": 2}))
            await asyncio.sleep(0.7)
            started = time.monotonic()
            await session.send_ping()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0, "the server froze while polling")
            self.assertFalse(call.done(), "wait_ready should still be polling")
            reply = await call
            self.assertFalse(reply.structured_content["ready"])

        with FakePainter(result={"connected": True, "open": False}) as painter:
            await self.session(painter.port, work)


class SimulatorDelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_painter_with_delay_keeps_the_loop_alive(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as sim:
            sim.delay = 1.0
            with patch.dict(os.environ, {"CAVI_PAINTER_PORT": str(sim.port),
                                         "CAVI_SUBSTANCE_WORKSPACE": folder}):
                slow = asyncio.create_task(server.inspect(action="texture_sets"))
                await asyncio.sleep(0.1)
                started = time.monotonic()
                await asyncio.sleep(0.05)
                self.assertLess(time.monotonic() - started, 0.3)
                self.assertEqual((await slow)["texture_sets"][0]["name"], "Body")


if __name__ == "__main__":
    unittest.main()
