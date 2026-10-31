"""Painter's documented loopback HTTP remote-scripting transport."""

import base64
import contextlib
import hashlib
import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path

OPERATIONS = frozenset(
    {
        "status",
        "open_project",
        "create_project",
        "close_project",
        "save",
        "save_copy",
        "texture_sets",
        "layer_stack",
        "create_fill_layer",
        "create_texture_fill",
        "export_presets",
        "export_textures",
        "session_control",
        "layer_edit",
        "bake",
        "bake_cancel",
        "bake_status",
        "bake_settings",
        "shelf",
        "reload_mesh",
    }
)
READ_ONLY_OPERATIONS = frozenset(
    {"status", "session_control", "texture_sets", "layer_stack", "export_presets", "shelf",
     "bake_settings", "bake_status"}
)
DEFAULT_TIMEOUT = 60.0
SLOW_TIMEOUT = 600.0
SLOW_OPERATIONS = frozenset({"save", "save_copy", "export_textures"})
OPERATION_TIMEOUTS = {"layer_edit": 120.0, **dict.fromkeys(SLOW_OPERATIONS, SLOW_TIMEOUT)}
# How long status waits for a request already in flight before reporting it instead.
STATUS_QUEUE_SECONDS = 0.5
RESPONSE_LIMIT = 8 * 1024 * 1024
SOURCE = Path(__file__).with_name("painter_operations.py").read_text(encoding="utf-8")
SOURCE_KEY = hashlib.sha256(SOURCE.encode()).hexdigest()
CACHE_ATTR = "_cavi_mcp_payloads"
CACHE_LIMIT = 8
TIMEOUT_MESSAGE = "Painter did not answer in time; outcome unknown, check painter_status"


class PainterTimeout(TimeoutError):
    pass


class PainterUnavailable(ConnectionError):
    pass


class PainterBusy(PainterTimeout):
    """An earlier request still occupies Painter; nothing was sent, so retrying is safe."""


class PainterHTTPError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"Painter HTTP {status}; request was not retried")
        self.status = status


class PainterCancelled(RuntimeError):
    """The request was cancelled while queued and never reached Painter."""


class _Gate:
    """Painter runs one scripting request at a time; this server never overlaps them either."""

    def __init__(self):
        self.lock = threading.Lock()
        self.operation = None
        self.since = 0.0


_GATES: dict[int, _Gate] = {}
_GATES_GUARD = threading.Lock()


def _gate(port: int) -> _Gate:
    with _GATES_GUARD:
        return _GATES.setdefault(port, _Gate())


def _arguments(operation: str, params: dict, workspace: Path) -> str:
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown operation: {operation}")
    # repr() keeps every caller-controlled value as data.
    return (
        repr(operation) + ", __import__('json').loads("
        + repr(json.dumps(params, allow_nan=False)) + "), " + repr(str(workspace))
    )


def bootstrap_expression(operation: str, params: dict, workspace: Path) -> str:
    arguments, key = _arguments(operation, params, workspace), repr(SOURCE_KEY)
    return (
        "(lambda c: __import__('json').dumps(c[" + key + "](" + arguments + ") if " + key
        + " in c else {'cavi_cache_miss': " + key + "}))"
        "(vars(__import__('substance_painter')).get(" + repr(CACHE_ATTR) + ", {}))"
    )


def full_expression(operation: str, params: dict, workspace: Path) -> str:
    arguments, key = _arguments(operation, params, workspace), repr(SOURCE_KEY)
    return (
        "(lambda c, ns: (__import__('builtins').exec(__import__('builtins').compile("
        + repr(SOURCE) + ", 'cavi_painter_operations', 'exec'), ns), "
        "c.clear() if len(c) >= " + str(CACHE_LIMIT) + " else None, "
        "c.__setitem__(" + key + ", ns['execute']), "
        "__import__('json').dumps(ns['execute'](" + arguments + ")))[3])"
        "(vars(__import__('substance_painter')).setdefault(" + repr(CACHE_ATTR) + ", {}), {})"
    )


def unwrap(result):
    if isinstance(result, dict) and result.get("ok") is True and "result" in result:
        return result["result"]
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(f"Painter operation failed: {result.get('error')}")
    if isinstance(result, dict) and "error" in result:
        native = result["error"]
        detail = native.get("description", native) if isinstance(native, dict) else native
        raise RuntimeError(f"Painter scripting error: {detail}")
    raise TypeError("Painter returned an unexpected response")


def not_ready_reasons(status: dict) -> list[str]:
    """What an editable project is still waiting on; raises when a mesh reload failed."""
    if not status.get("connected"):
        return ["connection"]
    if status.get("in_flight"):
        return ["in_flight"]
    reload = status.get("mesh_reload") or {}
    if not reload.get("pending") and str(reload.get("status")).upper() == "ERROR":
        raise RuntimeError(
            f"Mesh reload failed (status ERROR) for {reload.get('path')}; inspect Painter "
            "before continuing"
        )
    waiting = []
    if not status.get("open"):
        waiting.append("project_open")
    if status.get("busy"):
        waiting.append("busy")
    if status.get("open") and not status.get("edition_state"):
        waiting.append("edition_state")
    if status.get("baking"):
        waiting.append("baking")
    if reload.get("pending"):
        waiting.append("mesh_reload")
    return waiting


class PainterClient:
    def __init__(self, host="127.0.0.1", port=None, workspace=None):
        if host != "127.0.0.1":
            raise ValueError("Painter connection must use IPv4 loopback")
        self.port = int(port if port is not None else os.environ.get("CAVI_PAINTER_PORT", "60041"))
        if not 1 <= self.port <= 65535:
            raise ValueError("Invalid Painter port")
        self.workspace = Path(
            workspace
            or os.environ.get("CAVI_SUBSTANCE_WORKSPACE", Path.home() / "Documents/CaviGameArt")
        ).expanduser().resolve()

    def ensure_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = self.workspace.resolve()

    def call(
        self,
        operation: str,
        params: dict | None = None,
        *,
        lease_id: str | None = None,
        timeout: float | None = None,
        cancelled: threading.Event | None = None,
    ):
        params = dict(params or {})
        if lease_id is not None:
            params["lease_id"] = lease_id
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}")
        if operation not in READ_ONLY_OPERATIONS:
            self.ensure_workspace()
        if timeout is None:
            timeout = OPERATION_TIMEOUTS.get(operation, DEFAULT_TIMEOUT)
        gate = _gate(self.port)
        if operation == "status":
            if not gate.lock.acquire(timeout=STATUS_QUEUE_SECONDS):
                return self._in_flight(gate)
        elif not gate.lock.acquire(timeout=timeout):
            raise PainterBusy(
                f"Painter is still busy with {gate.operation}; this request was not sent"
            )
        try:
            if cancelled is not None and cancelled.is_set():
                # The caller gave up while queued; nothing was sent to Painter.
                raise PainterCancelled(f"{operation} was cancelled before it was sent")
            gate.operation, gate.since = operation, time.monotonic()
            result = self._post(bootstrap_expression(operation, params, self.workspace), timeout)
            if result == {"cavi_cache_miss": SOURCE_KEY}:
                # Nothing ran on a miss, so sending the full payload is not a retry.
                result = self._post(
                    full_expression(operation, params, self.workspace), timeout
                )
        finally:
            gate.operation = None
            gate.lock.release()
        return unwrap(result)

    @staticmethod
    def _in_flight(gate: _Gate) -> dict:
        operation = gate.operation or "another request"
        return {
            "connected": True, "busy": True, "in_flight": operation,
            "in_flight_seconds": round(time.monotonic() - gate.since, 1),
            "reason": f"Painter is executing {operation}; poll again when it finishes",
        }

    def probe(self, deadline: float) -> dict:
        """One status read bounded by deadline; transient failures read as not connected."""
        remaining = deadline - time.monotonic()
        try:
            return self.call("status", timeout=max(0.5, min(10.0, remaining)))
        except (PainterTimeout, PainterUnavailable) as exc:
            return {"connected": False, "reason": str(exc)}
        except PainterHTTPError as exc:
            # Painter's scripting server answers 5xx while it is starting or busy.
            if exc.status < 500:
                raise
            return {"connected": False, "reason": str(exc)}

    def readiness(self, deadline: float, start: float) -> dict:
        status = self.probe(deadline)
        waiting = not_ready_reasons(status)
        return {**status, "ready": not waiting, "waiting_on": waiting,
                "waited_seconds": round(time.monotonic() - start, 3)}

    def wait_ready(self, timeout_seconds: float = 60, interval: float = 0.5):
        start = time.monotonic()
        deadline = start + timeout_seconds
        while True:
            result = self.readiness(deadline, start)
            if result["ready"] or time.monotonic() + interval >= deadline:
                return result
            time.sleep(interval)

    def _post(self, expression: str, timeout: float):
        body = json.dumps({"python": base64.b64encode(expression.encode()).decode()})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        expired = threading.Event()

        def expire():
            # Socket timeouts restart on every recv(), so a trickling reply could outlive
            # them; this watchdog bounds the whole exchange by wall-clock time.
            expired.set()
            sock = connection.sock
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)

        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            try:
                connection.connect()
            except OSError as exc:
                if expired.is_set():
                    raise PainterTimeout(TIMEOUT_MESSAGE) from exc
                raise PainterUnavailable(
                    f"Painter not reachable on 127.0.0.1:{self.port}. Start licensed Painter "
                    "with --enable-remote-scripting."
                ) from exc
            try:
                connection.request(
                    "POST", "/run.json", body, {"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                data = response.read(RESPONSE_LIMIT + 1)
            except TimeoutError as exc:
                raise PainterTimeout(TIMEOUT_MESSAGE) from exc
            except (OSError, http.client.HTTPException) as exc:
                if expired.is_set():
                    raise PainterTimeout(TIMEOUT_MESSAGE) from exc
                raise PainterUnavailable(
                    "Painter not reachable or dropped the connection; outcome unknown, "
                    "check painter_status before retrying"
                ) from exc
        finally:
            watchdog.cancel()
            connection.close()
        if len(data) > RESPONSE_LIMIT:
            raise RuntimeError("Painter response exceeds size limit")
        if response.status != 200:
            raise PainterHTTPError(response.status)
        try:
            result = json.loads(data.decode().strip())
            if isinstance(result, str):
                result = json.loads(result)
        except (ValueError, UnicodeDecodeError) as exc:
            if expired.is_set():
                # The watchdog cut the reply short; a complete reply would have parsed.
                raise PainterTimeout(TIMEOUT_MESSAGE) from exc
            raise RuntimeError(
                "Painter returned an invalid response; inspect the application before retrying"
            ) from exc
        return result
