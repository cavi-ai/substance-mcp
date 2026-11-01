import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from env import patched_environment
from launcher import write_launcher

from cavi_substance_mcp.core import discovery, proc

POSIX = os.name != "nt"
posix_only = unittest.skipUnless(POSIX, "needs pgrep and POSIX process names")
OVERRIDES = ("CAVI_DESIGNER_BIN_DIR", "CAVI_DESIGNER_APP", "CAVI_SAMPLER_BIN",
             "CAVI_SAMPLER_APP", "CAVI_PAINTER_APP", "PROGRAMFILES")


def platform(name):
    return patch.object(discovery.sys, "platform", name)


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
            self.assertEqual(discovery.platform_name(), "freebsd14")
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
        self.assertEqual(discovery.tool(folder, "sbsrender"),
                         Path(folder / ("sbsrender.cmd" if os.name == "nt" else "sbsrender")))
        self.assertIsNone(discovery.tool(folder, "missing"))
        self.assertIsNone(discovery.tool(self.root / "no-such-dir", "sbsrender"))

    @posix_only
    def test_a_tool_must_be_executable(self):
        folder = self.root / "tools"
        folder.mkdir()
        (folder / "sbsrender").write_text("#!/bin/sh\n")
        self.assertIsNone(discovery.tool(folder, "sbsrender"))


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


class VersionTests(Scratch):
    def test_bundle_version_comes_from_the_plist_without_running_anything(self):
        app = self.root / "Thing.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleShortVersionString": "6.0.4", "CFBundleVersion": "9"}))
        executable = app / "Contents/MacOS/Thing"
        executable.write_text("x")
        with platform("darwin"), patch.object(
                proc, "run_sync", side_effect=AssertionError("ran the application")):
            self.assertEqual(discovery.version(executable), "6.0.4")

    def test_bundle_version_falls_back_to_the_build_number(self):
        app = self.root / "Thing.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleVersion": "9"}))
        executable = app / "Contents/MacOS/Thing"
        executable.write_text("x")
        with platform("darwin"):
            self.assertEqual(discovery.version(executable), "9")

    def test_an_unreadable_plist_is_unknown_and_the_application_is_not_run(self):
        app = self.root / "Thing.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        executable = app / "Contents/MacOS/Thing"
        executable.write_text("x")
        with platform("darwin"), patch.object(
                proc, "run_sync", side_effect=AssertionError("ran the application")):
            self.assertIsNone(discovery.version(executable))

    def test_other_platforms_ask_the_program(self):
        app = self.root / "Thing.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleShortVersionString": "from-plist"}))
        program = write_launcher(
            app / "Contents/MacOS", "Thing", "print('version 1.2.3 (build 9)')\n")
        with platform("linux"):
            self.assertEqual(discovery.version(program), "1.2.3")

    def test_a_program_outside_any_bundle_is_asked_even_on_macos(self):
        program = write_launcher(self.root, "tool", "print('7.8.9-fake')\n")
        with platform("darwin"):
            self.assertEqual(discovery.version(program), "7.8.9-fake")

    def test_the_output_formats_the_tools_use(self):
        for text, expected in (
            ("version 16.0.7 (build 123 commit 0x1)", "16.0.7"),
            ("Substance Designer Version 16.0.7", "16.0.7"),
            ("0.0.0-fake", "0.0.0-fake"),
            ("Adobe Substance 3D Sampler 6.0.4\n", "6.0.4"),
            ("no digits here", None),
            ("", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(discovery.parse_version(text), expected)

    def test_the_probe_runs_once_per_path_and_mtime(self):
        program = write_launcher(self.root, "tool", "print('1.0.0')\n")
        calls = []
        real = proc.run_sync

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with patch.object(proc, "run_sync", counting):
            self.assertEqual(discovery.version(program), "1.0.0")
            self.assertEqual(discovery.version(program), "1.0.0")
            self.assertEqual(len(calls), 1)
            later = program.stat().st_mtime + 10
            os.utime(program, (later, later))
            discovery.version(program)
            self.assertEqual(len(calls), 2)

    def test_a_failed_probe_is_unknown_and_not_repeated(self):
        program = write_launcher(self.root, "tool", "import sys\nsys.exit(3)\n")
        with patch.object(proc, "run_sync", side_effect=TimeoutError("slow")) as probe:
            self.assertIsNone(discovery.version(program))
            self.assertIsNone(discovery.version(program))
        self.assertEqual(probe.call_count, 1)

    def test_a_program_that_never_answers_is_given_up_on_at_the_timeout(self):
        program = write_launcher(self.root, "hang", "import time\ntime.sleep(60)\n")
        self.assertLessEqual(discovery.VERSION_TIMEOUT, 5)
        with patch.object(discovery, "VERSION_TIMEOUT", 0.3):
            started = time.monotonic()
            self.assertIsNone(discovery.version(program))
            self.assertLess(time.monotonic() - started, 5)

    def test_the_caller_can_shorten_the_timeout(self):
        program = write_launcher(self.root, "hang", "import time\ntime.sleep(60)\n")
        started = time.monotonic()
        self.assertIsNone(discovery.version(program, timeout=0.3))
        self.assertLess(time.monotonic() - started, 4)

    def test_a_timeout_cannot_be_raised_above_the_default(self):
        program = write_launcher(self.root, "hang", "import time\ntime.sleep(60)\n")
        with patch.object(discovery, "VERSION_TIMEOUT", 0.3):
            started = time.monotonic()
            self.assertIsNone(discovery.version(program, timeout=600))
            self.assertLess(time.monotonic() - started, 5)

    def test_a_probe_cut_short_by_the_caller_is_not_remembered(self):
        program = write_launcher(self.root, "late", "import time\ntime.sleep(1)\nprint('2.0.0')\n")
        self.assertIsNone(discovery.version(program, timeout=0.2))
        self.assertEqual(discovery.version(program), "2.0.0")

    def test_a_probe_that_reaches_the_default_timeout_is_remembered(self):
        program = write_launcher(self.root, "hang", "import time\ntime.sleep(60)\n")
        with patch.object(proc, "run_sync", side_effect=TimeoutError("slow")) as probe:
            self.assertIsNone(discovery.version(program, timeout=discovery.VERSION_TIMEOUT))
            self.assertIsNone(discovery.version(program))
        self.assertEqual(probe.call_count, 1)

    def test_a_missing_file_has_no_version(self):
        self.assertIsNone(discovery.version(self.root / "nothing"))
        self.assertIsNone(discovery.version(None))


class PatternTests(unittest.TestCase):
    def test_the_pattern_matches_a_binary_only_as_the_first_argument(self):
        import re

        path = Path("/Applications/Adobe Substance 3D Sampler/Adobe Substance 3D Sampler.app/"
                    "Contents/MacOS/Adobe Substance 3D Sampler")
        # pgrep uses POSIX extended expressions; Python's engine reads this one the same way
        # once the POSIX class is spelled out.
        pattern = discovery.pattern_for(path).replace("[[:space:]]", r"\s")
        self.assertTrue(re.search(pattern, f"{path} --skip-version-check"))
        self.assertTrue(re.search(pattern, str(path)))
        self.assertFalse(re.search(pattern, f"/usr/bin/python {path}"))
        self.assertFalse(re.search(pattern, f"AdobeIPCBroker -launchedbyvulcan {path}"))
        self.assertFalse(re.search(pattern, f"licensing --appPath={path}"))
        self.assertFalse(re.search(pattern, f"{path}-helper"))

    def test_the_pattern_matches_a_script_as_a_whole_argument(self):
        import re

        with tempfile.TemporaryDirectory() as folder:
            path = write_launcher(Path(folder), "Adobe Substance 3D Sampler", "print(1)\n",
                                  windows=False)
            pattern = discovery.pattern_for(path).replace("[[:space:]]", r"\s")
            self.assertTrue(re.search(pattern, f"/usr/bin/python {path} --idle"))
            self.assertTrue(re.search(pattern, f"{path} --idle"))
            self.assertFalse(re.search(pattern, f"licensing --appPath={path}"))

    def test_regex_metacharacters_in_a_path_are_literal(self):
        import re

        path = Path("/opt/a+b (x)/[app]/tool.v1")
        pattern = discovery.pattern_for(path).replace("[[:space:]]", r"\s")
        self.assertTrue(re.search(pattern, f"{path} run"))
        self.assertFalse(re.search(pattern, "/opt/aab (x)/[app]/toolxv1 run"))

    def test_the_resolved_form_of_a_symlinked_path_matches_too(self):
        import re

        with tempfile.TemporaryDirectory() as folder:
            real = Path(folder).resolve() / "real"
            real.write_text("x")
            link = Path(folder) / "link"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("no symbolic links")
            pattern = discovery.pattern_for(link).replace("[[:space:]]", r"\s")
            self.assertTrue(re.search(pattern, f"{real} arg"))
            self.assertTrue(re.search(pattern, f"{link} arg"))


@posix_only
class RunningPosixTests(Scratch):
    def hold(self, program, *args):
        child = subprocess.Popen([str(program), *args])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        return child

    def idle(self, name="Adobe Substance 3D Sampler"):
        return write_launcher(self.root / "app", name, "import time\ntime.sleep(60)\n")

    def wait_for(self, program, expected, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if discovery.running(program) is expected:
                return True
            time.sleep(0.1)
        return discovery.running(program) is expected

    def test_a_running_program_is_seen_and_a_stopped_one_is_not(self):
        program = self.idle()
        self.assertFalse(discovery.running(program))
        child = self.hold(program, "--idle")
        self.assertTrue(self.wait_for(program, True))
        child.kill()
        child.wait()
        self.assertTrue(self.wait_for(program, False))

    def test_a_path_with_spaces_and_brackets_is_matched(self):
        program = write_launcher(self.root / "A (B) [C]", "Adobe Substance 3D Sampler",
                                 "import time\ntime.sleep(60)\n")
        self.hold(program)
        self.assertTrue(self.wait_for(program, True))

    def test_the_licensing_client_does_not_count(self):
        program = self.idle()
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                   f"--appPath={program}"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        time.sleep(0.5)
        self.assertFalse(discovery.running(program))

    def test_a_process_naming_a_binary_as_an_option_value_does_not_count(self):
        # A link, not a copy: macOS kills copies of its own signed binaries.
        program = self.root / "app" / "Adobe Substance 3D Sampler"
        program.parent.mkdir()
        program.symlink_to(shutil.which("sleep"))
        broker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                   "-launchedbyvulcan", str(program)])
        self.addCleanup(broker.wait)
        self.addCleanup(broker.kill)
        time.sleep(0.5)
        self.assertFalse(discovery.running(program))
        self.hold(program, "60")
        self.assertTrue(self.wait_for(program, True))

    def test_a_symlinked_executable_is_seen_when_the_process_uses_the_real_path(self):
        program = self.idle()
        link = self.root / "linked"
        link.symlink_to(program)
        self.hold(program)
        self.assertTrue(self.wait_for(link, True))

    def test_a_symlinked_executable_is_seen_when_the_process_uses_the_link(self):
        program = self.idle()
        link = self.root / "linked"
        link.symlink_to(program)
        self.hold(link)
        self.assertTrue(self.wait_for(link, True))

    def test_no_executable_is_not_running(self):
        self.assertFalse(discovery.running(None))

    def test_unknown_without_pgrep(self):
        with patch.object(discovery.shutil, "which", return_value=None):
            self.assertIsNone(discovery.running(Path("/x/y")))

    def test_unknown_when_pgrep_fails(self):
        for outcome in (subprocess.CompletedProcess([], 2), subprocess.CompletedProcess([], 3)):
            with patch.object(discovery.subprocess, "run", return_value=outcome):
                self.assertIsNone(discovery.running(Path("/x/y")))
        with patch.object(discovery.subprocess, "run", side_effect=OSError("denied")):
            self.assertIsNone(discovery.running(Path("/x/y")))
        with patch.object(discovery.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("pgrep", 10)):
            self.assertIsNone(discovery.running(Path("/x/y")))

    def test_pgrep_is_called_with_full_command_line_matching(self):
        with patch.object(discovery.shutil, "which", return_value="/usr/bin/pgrep"), \
                patch.object(discovery.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertFalse(discovery.running(Path("/x/y")))
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/pgrep", "-f"])


class RunningWindowsTests(unittest.TestCase):
    """Windows cannot run here: the tasklist call and its parsing are checked by simulation."""

    @staticmethod
    def listing(text, code=0):
        return subprocess.CompletedProcess([], code, stdout=text, stderr="")

    def check(self, output,
              executable=r"C:\Program Files\Adobe\Sampler\Adobe Substance 3D Sampler.exe"):
        with platform("win32"), patch.object(discovery.subprocess, "run",
                                             return_value=self.listing(output)) as run:
            result = discovery.running(Path(executable))
        return result, run

    def test_a_listed_image_is_running(self):
        result, run = self.check('"Adobe Substance 3D Sampler.exe","1234","Console","1","1 K"\n',
                                 "/fake/Adobe Substance 3D Sampler.exe")
        self.assertTrue(result)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "tasklist")
        self.assertIn("IMAGENAME eq Adobe Substance 3D Sampler.exe", command)
        self.assertEqual(command[-3:], ["/FO", "CSV", "/NH"])

    def test_the_image_name_comparison_ignores_case(self):
        result, _ = self.check('"ADOBE SUBSTANCE 3D SAMPLER.EXE","1","Console","1","1 K"\n',
                               "/fake/Adobe Substance 3D Sampler.exe")
        self.assertTrue(result)

    def test_no_match_is_not_running(self):
        result, _ = self.check("INFO: No tasks are running which match the specified criteria.\n",
                               "/fake/Adobe Substance 3D Sampler.exe")
        self.assertFalse(result)

    def test_another_image_does_not_count(self):
        result, _ = self.check('"notepad.exe","1","Console","1","1 K"\n',
                               "/fake/Adobe Substance 3D Sampler.exe")
        self.assertFalse(result)

    def test_unknown_when_tasklist_fails(self):
        for failure in (OSError("missing"), subprocess.CalledProcessError(1, "tasklist"),
                        subprocess.TimeoutExpired("tasklist", 10)):
            with platform("win32"), patch.object(discovery.subprocess, "run",
                                                 side_effect=failure):
                self.assertIsNone(discovery.running(Path("/fake/x.exe")))

    def test_windows_never_calls_pgrep(self):
        with platform("win32"), patch.object(discovery.shutil, "which",
                                             side_effect=AssertionError("pgrep looked up")), \
                patch.object(discovery.subprocess, "run", return_value=self.listing("")):
            self.assertFalse(discovery.running(Path("/fake/x.exe")))


if __name__ == "__main__":
    unittest.main()
