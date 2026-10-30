"""Painter's documented loopback HTTP remote-scripting transport."""

import base64
import hashlib
import http.client
import json
import os
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
SLOW_OPERATIONS = frozenset({"save", "save_copy", "export_textures", "layer_edit"})
RESPONSE_LIMIT = 8 * 1024 * 1024
SOURCE = Path(__file__).with_name("painter_operations.py").read_text()
SOURCE_KEY = hashlib.sha256(SOURCE.encode()).hexdigest()
CACHE_ATTR = "_cavi_mcp_payloads"
CACHE_LIMIT = 8
TIMEOUT_MESSAGE = "Painter did not answer in time; outcome unknown, check painter_status"


class PainterTimeout(TimeoutError):
    pass


class PainterUnavailable(ConnectionError):
    pass


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
    ):
        params = dict(params or {})
        if lease_id is not None:
            params["lease_id"] = lease_id
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}")
        if operation not in READ_ONLY_OPERATIONS:
            self.ensure_workspace()
        expression = bootstrap_expression(operation, params, self.workspace)
        if timeout is None:
            timeout = SLOW_TIMEOUT if operation in SLOW_OPERATIONS else DEFAULT_TIMEOUT
        result = self._post(expression, timeout)
        if result == {"cavi_cache_miss": SOURCE_KEY}:
            # Nothing ran on a miss, so sending the full payload is not a retry.
            result = self._post(full_expression(operation, params, self.workspace), timeout)
        return unwrap(result)

    def wait_ready(self, timeout_seconds: float = 60, interval: float = 0.5):
        start = time.monotonic()
        deadline = start + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            try:
                status = self.call("status", timeout=max(0.5, min(10.0, remaining)))
            except (PainterTimeout, PainterUnavailable) as exc:
                status = {"connected": False, "reason": str(exc)}
            ready = bool(
                status.get("open") and not status.get("busy") and status.get("edition_state")
            )
            if ready or time.monotonic() + interval >= deadline:
                return {**status, "ready": ready,
                        "waited_seconds": round(time.monotonic() - start, 3)}
            time.sleep(interval)

    def _post(self, expression: str, timeout: float):
        body = json.dumps({"python": base64.b64encode(expression.encode()).decode()})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            try:
                connection.connect()
            except OSError as exc:
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
            except OSError as exc:
                raise PainterUnavailable(
                    "Painter not reachable or dropped the connection; outcome unknown, "
                    "check painter_status before retrying"
                ) from exc
        finally:
            connection.close()
        if len(data) > RESPONSE_LIMIT:
            raise RuntimeError("Painter response exceeds size limit")
        if response.status != 200:
            raise RuntimeError(f"Painter HTTP {response.status}; request was not retried")
        try:
            result = json.loads(data.decode().strip())
            if isinstance(result, str):
                result = json.loads(result)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                "Painter returned an invalid response; inspect the application before retrying"
            ) from exc
        return result
