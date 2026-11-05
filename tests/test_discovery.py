import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from cavi_mcp_core import apps
from env import patched_environment
from launcher import write_launcher

from cavi_substance_mcp.core import discovery

POSIX = os.name != "nt"
posix_only = unittest.skipUnless(POSIX, "needs POSIX file modes")
OVERRIDES = ("CAVI_DESIGNER_BIN_DIR", "CAVI_DESIGNER_APP", "CAVI_SAMPLER_BIN",
             "CAVI_SAMPLER_APP", "CAVI_PAINTER_APP", "PROGRAMFILES")


def platform(name):
    return patch.object(apps.sys, "platform", name)


class Scratch(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = patched_environment(drop=OVERRIDES)
        patcher.start()
        self.addCleanup(patcher.stop)


class DefaultTableTests(unittest.TestCase):
    def test_every_application_has_a_default_on_every_platform(self):
        expected = {
            ("designer", "darwin"):
                "/Applications/Adobe Substance 3D Designer/Adobe Substance 3D Designer.app/"
                "Contents/MacOS",
            ("designer", "win32"): r"C:\Program Files\Adobe\Adobe Substance 3D Designer",
            ("designer", "linux"): "/opt/Adobe/Adobe_Substance_3D_Designer",
            ("sampler", "darwin"):
                "/Applications/Adobe Substance 3D Sampler/Adobe Substance 3D Sampler.app",
            ("sampler", "win32"):
                r"C:\Program Files\Adobe\Adobe Substance 3D Sampler"
                r"\Adobe Substance 3D Sampler.exe",
            ("sampler", "linux"):
                "/opt/Adobe/Adobe_Substance_3D_Sampler/Adobe Substance 3D Sampler",
            ("painter", "darwin"):
                "/Applications/Adobe Substance 3D Painter/Adobe Substance 3D Painter.app",
            ("painter", "win32"):
                r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
            ("painter", "linux"):
                "/opt/Adobe/Adobe_Substance_3D_Painter/Adobe Substance 3D Painter",
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROGRAMFILES", None)
            for (key, name), text in expected.items():
                with self.subTest(app=key, platform=name), platform(name):
                    self.assertEqual(str(discovery.default_location(key)), text)

    def test_windows_uses_the_program_files_variable(self):
        with platform("win32"), patch.dict(os.environ, {"PROGRAMFILES": r"D:\Apps"}):
            self.assertEqual(str(discovery.default_location("designer")),
                             r"D:\Apps\Adobe\Adobe Substance 3D Designer")

    def test_an_unknown_platform_uses_the_linux_layout(self):
        with platform("freebsd14"):
            self.assertEqual(apps.platform_name(), "freebsd14")
            self.assertTrue(str(discovery.default_location("sampler")).startswith("/opt/Adobe"))


class DirectoryTests(Scratch):
    def find(self):
        return discovery.find_directory(
            "designer", "sbsrender", "CAVI_DESIGNER_BIN_DIR", "CAVI_DESIGNER_APP")

    def make_tools(self, folder):
        write_launcher(folder, "sbsrender", "print('x')\n")
        return folder

    def test_environment_override_comes_first_and_is_final(self):
        wrong = self.root / "wrong"
        os.environ["CAVI_DESIGNER_BIN_DIR"] = str(wrong)
        default = self.make_tools(self.root / "default")
        on_path = self.make_tools(self.root / "bin")
        with (patch.object(discovery, "default_location", return_value=default),
              patch.dict(os.environ, {"PATH": str(on_path)})):
            found = self.find()
        self.assertEqual(found, (wrong, "CAVI_DESIGNER_BIN_DIR"))

    def test_app_override_uses_the_macos_directory_of_a_bundle(self):
        app = self.root / "Designer.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        os.environ["CAVI_DESIGNER_APP"] = str(app)
        self.assertEqual(self.find(), (app / "Contents/MacOS", "CAVI_DESIGNER_APP"))

    def test_app_override_may_be_a_plain_directory(self):
        os.environ["CAVI_DESIGNER_APP"] = str(self.root)
        self.assertEqual(self.find(), (self.root, "CAVI_DESIGNER_APP"))

    def test_bin_dir_beats_app(self):
        os.environ["CAVI_DESIGNER_BIN_DIR"] = "/x"
        os.environ["CAVI_DESIGNER_APP"] = "/y"
        self.assertEqual(self.find().path, Path("/x"))

    def test_default_location_comes_before_path(self):
        default = self.make_tools(self.root / "default")
        on_path = self.make_tools(self.root / "bin")
        with (patch.object(discovery, "default_location", return_value=default),
              patch.dict(os.environ, {"PATH": str(on_path)})):
            self.assertEqual(self.find(), (default, "default"))

    def test_path_is_the_last_resort(self):
        on_path = self.make_tools(self.root / "bin")
        with (patch.object(discovery, "default_location", return_value=self.root / "none"),
              patch.dict(os.environ, {"PATH": str(on_path)})):
            found = self.find()
        self.assertEqual(found.source, "PATH")
        self.assertEqual(found.path, on_path.resolve())

    def test_nothing_found_names_the_default(self):
        with (patch.object(discovery, "default_location", return_value=self.root / "none"),
              patch.dict(os.environ, {"PATH": str(self.root / "empty")})):
            self.assertEqual(self.find(), (self.root / "none", "default"))

    def test_a_tool_is_found_through_the_platforms_executable_suffixes(self):
        folder = self.make_tools(self.root / "tools")
        self.assertEqual(apps.tool(folder, "sbsrender"),
                         Path(folder / ("sbsrender.cmd" if os.name == "nt" else "sbsrender")))
        self.assertIsNone(apps.tool(folder, "missing"))
        self.assertIsNone(apps.tool(self.root / "no-such-dir", "sbsrender"))

    @posix_only
    def test_a_tool_must_be_executable(self):
        folder = self.root / "tools"
        folder.mkdir()
        (folder / "sbsrender").write_text("#!/bin/sh\n")
        self.assertIsNone(apps.tool(folder, "sbsrender"))


class ExecutableTests(Scratch):
    def find(self):
        return discovery.find_executable(
            "sampler", "Adobe Substance 3D Sampler", "CAVI_SAMPLER_BIN", "CAVI_SAMPLER_APP")

    def program(self, folder, name="Adobe Substance 3D Sampler"):
        return write_launcher(self.root / folder, name, "print('x')\n")

    def test_bin_override_is_an_exact_executable(self):
        program = self.program("a")
        os.environ["CAVI_SAMPLER_BIN"] = str(program)
        self.assertEqual(self.find(), (program, "CAVI_SAMPLER_BIN"))

    def test_bin_override_may_name_the_install_directory(self):
        with patch.object(discovery, "default_location",
                          return_value=self.root / "a" / "Adobe Substance 3D Sampler"):
            program = self.program("a")
            if os.name == "nt":
                self.skipTest("the default Windows name ends in .exe")
            os.environ["CAVI_SAMPLER_BIN"] = str(program.parent)
            self.assertEqual(self.find(), (program, "CAVI_SAMPLER_BIN"))

    def test_a_missing_bin_override_is_reported_not_replaced(self):
        default = self.program("default")
        os.environ["CAVI_SAMPLER_BIN"] = str(self.root / "gone")
        with patch.object(discovery, "default_location", return_value=default):
            self.assertEqual(self.find(), (None, "CAVI_SAMPLER_BIN"))

    def test_app_override_reads_the_executable_name_from_the_bundle(self):
        app = self.root / "Fake Sampler.app"
        macos = app / "Contents/MacOS"
        macos.mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleExecutable": "Custom Name"}))
        (macos / "Custom Name").write_text("x")
        os.environ["CAVI_SAMPLER_APP"] = str(app)
        self.assertEqual(self.find(), (macos / "Custom Name", "CAVI_SAMPLER_APP"))

    def test_app_override_falls_back_to_the_bundle_name_without_a_plist(self):
        app = self.root / "Fake Sampler.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/MacOS/Fake Sampler").write_text("x")
        os.environ["CAVI_SAMPLER_APP"] = str(app)
        self.assertEqual(self.find().path, app / "Contents/MacOS/Fake Sampler")

    def test_app_override_may_be_the_executable_itself(self):
        program = self.program("a")
        os.environ["CAVI_SAMPLER_APP"] = str(program)
        self.assertEqual(self.find(), (program, "CAVI_SAMPLER_APP"))

    def test_default_is_used_when_it_exists(self):
        program = self.program("default")
        with patch.object(discovery, "default_location", return_value=program):
            self.assertEqual(self.find(), (program, "default"))

    def test_a_default_bundle_resolves_to_its_executable(self):
        app = self.root / "Default.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/MacOS/Default").write_text("x")
        with patch.object(discovery, "default_location", return_value=app):
            self.assertEqual(self.find().path, app / "Contents/MacOS/Default")

    def test_path_lookup_is_after_the_default(self):
        program = self.program("bin")
        with (patch.object(discovery, "default_location", return_value=self.root / "none"),
              patch.dict(os.environ, {"PATH": str(program.parent)})):
            found = self.find()
        self.assertEqual(found.source, "PATH")
        # Windows spells the suffix as PATHEXT does (.CMD); its paths compare case-blind.
        self.assertEqual(Path(found.path), program)

    def test_nothing_found(self):
        with (patch.object(discovery, "default_location", return_value=self.root / "none"),
              patch.dict(os.environ, {"PATH": str(self.root / "empty")})):
            self.assertEqual(self.find(), (None, "default"))

    def test_painter_has_no_bin_override(self):
        app = self.root / "Painter.app"
        app.mkdir()
        os.environ["CAVI_PAINTER_APP"] = str(app)
        found = discovery.find_executable("painter", "Adobe Substance 3D Painter", None,
                                          "CAVI_PAINTER_APP")
        self.assertEqual(found.source, "CAVI_PAINTER_APP")


if __name__ == "__main__":
    unittest.main()
