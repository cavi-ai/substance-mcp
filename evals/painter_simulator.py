"""Explicitly simulated Painter API behind its real loopback scripting protocol.

Executes the production operation source. This fixture establishes operation and
transport behavior against the stated API assumptions, never native compatibility.
"""

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace


class PainterSimulator:
    def __init__(self, workspace):
        self.workspace = workspace
        self.state = "open"
        self.opened = True

    def setup(self, state):
        self.state = state
        self.opened = state != "closed"

    def __enter__(self):
        module = ModuleType("substance_painter")
        module.__version__ = "simulated"
        self.previous = sys.modules.get("substance_painter")
        sys.modules["substance_painter"] = module

        def open_project(*args, **kwargs):
            self.opened = True

        def save_copy(path, mode):
            Path(path).write_bytes(b"simulated-spp")

        module.project = SimpleNamespace(
            is_open=lambda: self.opened,
            is_busy=lambda: self.state == "busy",
            file_path=lambda: "simulated-original.spp",
            needs_saving=lambda: False,
            open=open_project,
            create=open_project,
            save_as_copy=save_copy,
            ProjectSaveMode=SimpleNamespace(Full="Full"),
        )
        module.textureset = SimpleNamespace(
            all_texture_sets=lambda: [SimpleNamespace(name=lambda: "Body")]
        )
        success, failure = SimpleNamespace(name="Success"), SimpleNamespace(name="Error")

        def plan(config):
            if type(config.get("exportShaderParams")) is not bool:
                raise ValueError("@/exportShaderParams, Expected a boolean value")
            if not any(
                item.get("parameters", {}).get("paddingAlgorithm")
                for item in config.get("exportParameters", [])
                if not item.get("filter")
            ):
                raise ValueError("padding algorithm could not be resolved")
            folder = Path(config["exportPath"])
            if self.state == "empty-plan":
                return {}
            if self.state == "escape-plan":
                return {"Body": [str(folder.parent / "escaped.png")]}
            if self.state == "omitted-export":
                return {"Body": [str(folder / "Body.png"), str(folder / "Body_AO.png")]}
            return {"Body": [str(folder / "Body.png")]}

        def export(config):
            outputs = plan(config)
            if self.state == "omitted-export":
                outputs = {"Body": outputs["Body"][:1]}
            for paths in outputs.values():
                for path in paths:
                    Path(path).write_bytes(
                        b"" if self.state == "empty-export" else b"simulated-png"
                    )
            return SimpleNamespace(
                status=failure if self.state == "failed-export" else success,
                message="simulated export",
                textures=outputs,
            )

        module.export = SimpleNamespace(
            list_predefined_export_presets=lambda: [
                SimpleNamespace(name="Eval", url="resource://eval-preset")
            ],
            list_project_textures=plan,
            export_project_textures=export,
            ExportStatus=SimpleNamespace(Success=success),
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                assert self.path == "/run.json"
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                expression = base64.b64decode(request["python"], validate=True).decode()
                try:
                    result = eval(expression)
                except Exception as exc:  # noqa: BLE001 -- mirror the remote scripting error boundary
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.previous is None:
            sys.modules.pop("substance_painter", None)
        else:
            sys.modules["substance_painter"] = self.previous
