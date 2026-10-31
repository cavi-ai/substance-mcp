"""The fake Designer and Sampler programs must install and run the same way everywhere.

Windows runs no shebang scripts and has no macOS bundles, so the fakes write a launcher
per platform (evals/launcher.py) and tests point discovery at it with an environment
override. These tests run the real launcher on this platform and check the Windows
layout's files on every platform.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

import designer_fake_cli
import sampler_fake_cli
from launcher import write_launcher

from cavi_substance_mcp.core import discovery, proc
from cavi_substance_mcp.designer import runner as designer_runner
from cavi_substance_mcp.sampler import runner as sampler_runner

ECHO = "import json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7)\n"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_launcher_runs_with_arguments_and_returns_the_exit_code(self):
        program = write_launcher(self.root, "echo tool", ECHO)
        arguments = ["plain", "two words", "--flag=1,2", "pkg://name", "$outputsize@8,8",
                     "{outputNodeName}"]
        result = asyncio.run(proc.run([program, *arguments], 30))
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(result.stdout), arguments)

    def test_discovery_finds_the_launcher_by_its_plain_name(self):
        program = write_launcher(self.root, "sbsrender", ECHO)
        self.assertEqual(discovery.tool(self.root, "sbsrender"), program)

    def test_posix_layout_is_one_executable_script(self):
        program = write_launcher(self.root, "tool", ECHO, windows=False)
        self.assertEqual(program, self.root / "tool")
        self.assertEqual(program.read_text().splitlines()[0], f"#!{sys.executable}")
        if os.name != "nt":
            self.assertTrue(os.access(program, os.X_OK))

    def test_windows_layout_is_a_cmd_wrapper_around_a_sidecar_script(self):
        program = write_launcher(self.root, "Adobe Substance 3D Sampler", ECHO, windows=True)
        self.assertEqual(program, self.root / "Adobe Substance 3D Sampler.cmd")
        lines = program.read_bytes().decode().split("\r\n")
        sidecar = self.root / "Adobe Substance 3D Sampler-launcher.py"
        self.assertEqual(sidecar.read_text(), ECHO)
        self.assertEqual(lines[0], "@echo off")
        self.assertEqual(lines[1], f'"{sys.executable}" "{sidecar}" %*')
        self.assertEqual(lines[2], "exit /b %ERRORLEVEL%")

    def test_the_sidecar_name_cannot_be_mistaken_for_the_program(self):
        # "sbsrender.py" would be on PATHEXT where the Python launcher is installed.
        write_launcher(self.root, "sbsrender", ECHO, windows=True)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["sbsrender-launcher.py", "sbsrender.cmd"])


class FakeDesignerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.bin_dir = designer_fake_cli.install(Path(folder.name))

    def test_every_tool_is_discoverable_and_reports_its_version(self):
        with patch.dict(os.environ, {designer_fake_cli.ENV: str(self.bin_dir)}):
            found = designer_runner.installation()
            for name in designer_runner.TOOLS:
                with self.subTest(tool=name):
                    path = found.require(name)
                    self.assertEqual(discovery.version(path), "0.0.0-fake")
        self.assertEqual(found.source, designer_fake_cli.ENV)

    def test_resources_are_beside_the_tools(self):
        with patch.dict(os.environ, {designer_fake_cli.ENV: str(self.bin_dir)}):
            found = designer_runner.installation()
        self.assertEqual(found.resources, (self.bin_dir.parent / "resources").resolve())

    def test_the_install_does_not_depend_on_a_macos_bundle(self):
        self.assertFalse(any(part.endswith(".app") for part in self.bin_dir.parts))
        self.assertFalse((self.bin_dir.parent / "Contents").exists())


class FakeSamplerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.program = sampler_fake_cli.install(Path(folder.name))

    def test_the_sampler_is_discoverable_and_reports_its_version(self):
        with patch.dict(os.environ, {sampler_fake_cli.ENV: str(self.program)}):
            found = sampler_runner.installation()
            self.assertEqual(found.executable, self.program)
            self.assertEqual(found.source, sampler_fake_cli.ENV)
            self.assertEqual(found.version, "0.0.0-fake")
            self.assertEqual(found.require(), self.program)

    def test_the_install_does_not_depend_on_a_macos_bundle(self):
        self.assertFalse(any(part.endswith(".app") for part in self.program.parts))


if __name__ == "__main__":
    unittest.main()
