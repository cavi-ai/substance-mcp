import contextlib
import io
import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PainterLaunchTests(unittest.TestCase):
    def app(self, folder, identifier="com.adobe.Adobe-Substance-3D-Painter"):
        app = Path(folder) / "Adobe Substance 3D Painter.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": identifier,
            "CFBundleExecutable": "Adobe Substance 3D Painter",
        }))
        return app

    def test_launch_includes_remote_scripting_flag(self):
        from cavi_substance_mcp.runtime import start_painter

        with tempfile.TemporaryDirectory() as folder:
            app = self.app(folder)
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                patch.dict("os.environ", {"CAVI_PAINTER_APP": str(app)}),
                patch("cavi_substance_mcp.runtime.sys.platform", "darwin"),
                patch("cavi_substance_mcp.runtime.PainterClient.call", side_effect=ConnectionError()),
                patch("cavi_substance_mcp.runtime.subprocess.run", side_effect=run),
            ):
                result = start_painter()
        self.assertTrue(result["launch_requested"])
        self.assertFalse(result["connected"])
        self.assertEqual(calls[-1], [
            "/usr/bin/open", "-a", str(app.resolve()), "--args", "--enable-remote-scripting",
        ])

    def test_connected_painter_is_reused_without_launch(self):
        from cavi_substance_mcp.runtime import start_painter

        with (
            patch("cavi_substance_mcp.runtime.PainterClient.call", return_value={"connected": True}),
            patch("cavi_substance_mcp.runtime.subprocess.run", side_effect=AssertionError("launched")),
        ):
            result = start_painter()
        self.assertTrue(result["connected"])
        self.assertFalse(result["launch_requested"])

    def test_open_disconnected_painter_is_not_relaunched(self):
        from cavi_substance_mcp.runtime import start_painter

        with tempfile.TemporaryDirectory() as folder:
            app = self.app(folder)
            executable = app / "Contents/MacOS/Adobe Substance 3D Painter"

            def run(command, **kwargs):
                if command != ["/bin/ps", "-axo", "pid=,comm="]:
                    raise AssertionError("An already open application was relaunched")
                return subprocess.CompletedProcess(command, 0, stdout=f"42 {executable}\n")

            with (
                patch.dict("os.environ", {"CAVI_PAINTER_APP": str(app)}),
                patch("cavi_substance_mcp.runtime.sys.platform", "darwin"),
                patch("cavi_substance_mcp.runtime.PainterClient.call", side_effect=ConnectionError()),
                patch("cavi_substance_mcp.runtime.subprocess.run", side_effect=run),
                self.assertRaisesRegex(RuntimeError, "already running"),
            ):
                start_painter()

    def test_corel_bundle_is_refused_before_launch(self):
        from cavi_substance_mcp.runtime import start_painter

        with tempfile.TemporaryDirectory() as folder:
            app = self.app(folder, identifier="com.corel.Painter")
            with (
                patch.dict("os.environ", {"CAVI_PAINTER_APP": str(app)}),
                patch("cavi_substance_mcp.runtime.sys.platform", "darwin"),
                patch("cavi_substance_mcp.runtime.PainterClient.call", side_effect=ConnectionError()),
                patch("cavi_substance_mcp.runtime.subprocess.run", side_effect=AssertionError("launched")),
                self.assertRaisesRegex(ValueError, "Adobe Substance"),
            ):
                start_painter()

    def test_cli_offline_status_returns_json_and_failure_exit(self):
        from cavi_substance_mcp.cli import main

        stdout = io.StringIO()
        with (
            patch("cavi_substance_mcp.runtime.PainterClient.call", side_effect=ConnectionError("offline")),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["runtime", "status"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue()), {"connected": False, "reason": "offline"})
