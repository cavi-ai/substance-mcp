"""Painter's documented loopback HTTP remote-scripting transport."""

import base64
import http.client
import json
import os
from pathlib import Path

OPERATIONS = frozenset(
    {
        "status",
        "open_project",
        "create_project",
        "close_project",
        "texture_sets",
        "export_presets",
        "save_copy",
        "export_textures",
    }
)


def expression_for(operation: str, params: dict, workspace: Path) -> str:
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown operation: {operation}")
    source = Path(__file__).with_name("painter_operations.py").read_text()
    # repr() makes every caller-controlled value data in a single Python expression.
    return (
        "(lambda ns: (__import__('builtins').exec(" + repr(source) + ", ns), "
        "__import__('json').dumps(ns['execute'](" + repr(operation) + ", "
        "__import__('json').loads("
        + repr(json.dumps(params, allow_nan=False))
        + "), "
        + repr(str(workspace))
        + ")))[1])({})"
    )


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
        ).expanduser()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = self.workspace.resolve()

    def call(self, operation: str, params: dict | None = None):
        expression = expression_for(operation, params or {}, self.workspace)
        body = json.dumps({"python": base64.b64encode(expression.encode()).decode()})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            connection.request("POST", "/run.json", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ConnectionError("Painter response exceeds size limit")
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
            if not isinstance(result, dict):
                raise TypeError("Painter returned an unexpected response")
            if "error" in result:
                raise RuntimeError(f"Painter operation failed: {result['error']}")
            return result
        except OSError as exc:
            raise ConnectionError(
                "Painter remote scripting is not reachable or the request timed out. Start licensed Painter with --enable-remote-scripting. Do not retry an uncertain mutation without inspection."
            ) from exc
        finally:
            connection.close()
