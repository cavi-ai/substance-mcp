"""Export and save_copy write decisions and leftovers."""

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from test_texturing import Fixture

from cavi_substance_mcp.painter import operations as ops
from cavi_substance_mcp.painter.client import PainterClient

PRESET = "resource://eval-preset"


class DryRunTests(Fixture):
    def anonymous(self):
        return PainterClient(port=self.sim.port, workspace=self.root)

    def test_dry_run_needs_no_lease_but_writing_does(self):
        plan = self.anonymous().call(
            "export_textures", {"directory": "out", "preset": PRESET, "dry_run": True})
        self.assertTrue(plan["dry_run"])
        with self.assertRaisesRegex(RuntimeError, "lease"):
            self.anonymous().call(
                "export_textures", {"directory": "out", "preset": PRESET, "dry_run": False})
        self.assertFalse((self.root / "out").exists())

    def test_dry_run_must_be_a_real_boolean_for_the_lease_gate_and_the_write_alike(self):
        for value in (None, 1, 0, "false", "true", []):
            params = {"directory": "out", "preset": PRESET, "dry_run": value}
            with self.subTest(value=value, leased=False), self.assertRaisesRegex(
                    RuntimeError, "dry_run must be a boolean"):
                self.anonymous().call("export_textures", params)
            with self.subTest(value=value, leased=True), self.assertRaisesRegex(
                    RuntimeError, "dry_run must be a boolean"):
                self.client.call("export_textures", params)
            self.assertFalse((self.root / "out").exists(), repr(value))


class FailedOutputTests(Fixture):
    def export(self, directory="out"):
        return self.client.call(
            "export_textures", {"directory": directory, "preset": PRESET, "dry_run": False})

    def stuck_removal(self):
        real = shutil.rmtree

        def stuck(path, *args, **kwargs):
            if not Path(path).name.startswith("out"):
                real(path, *args, **kwargs)

        return patch("shutil.rmtree", stuck)

    def test_failed_export_leaves_no_directory_and_the_name_can_be_reused(self):
        for state in ("failed-export", "empty-export"):
            with self.subTest(state=state):
                self.sim.state = state
                with self.assertRaisesRegex(RuntimeError, "removed"):
                    self.export()
                self.assertFalse((self.root / "out").exists())
        self.sim.state = "open"
        self.assertEqual(self.export()["status"], "Success")
        self.assertTrue((self.root / "out/Body.png").is_file())

    def test_failed_save_copy_leaves_no_directory_and_the_name_can_be_reused(self):
        for state, message in (("save-copy-failure", "simulated save failure"),
                               ("save-copy-empty", "did not produce")):
            with self.subTest(state=state):
                self.sim.state = state
                with self.assertRaisesRegex(RuntimeError, message + ".*removed"):
                    self.client.call("save_copy", {"directory": "out"})
                self.assertFalse((self.root / "out").exists())
        self.sim.state = "open"
        result = self.client.call("save_copy", {"directory": "out"})
        self.assertTrue(Path(result["manifest"]).is_file())

    def test_leftovers_are_reported_when_removal_fails(self):
        self.sim.state = "failed-export"
        with self.stuck_removal(), self.assertRaisesRegex(
                RuntimeError, r"could not remove.*out"):
            self.export()
        self.assertTrue((self.root / "out").exists())
        self.sim.state = "save-copy-failure"
        with self.stuck_removal(), self.assertRaisesRegex(
                RuntimeError, r"could not remove.*out"):
            self.client.call("save_copy", {"directory": "out2"})

    def test_successful_outputs_and_dry_runs_are_untouched(self):
        self.assertFalse(self.client.call(
            "export_textures", {"directory": "out", "preset": PRESET})["files"] == [])
        self.assertFalse((self.root / "out").exists())
        self.export()
        self.assertTrue((self.root / "out/manifest.json").is_file())

    def test_directory_with_a_manifest_is_kept_even_if_the_call_then_fails(self):
        folder = self.root / "done"
        with self.assertRaisesRegex(ZeroDivisionError, "late"), ops.new_output_directory(folder):
            (folder / ops.receipts.MANIFEST).write_text("{}")
            raise ZeroDivisionError("late")
        self.assertTrue(folder.is_dir())

    def test_a_directory_this_call_did_not_create_is_never_removed(self):
        folder = self.root / "theirs"
        folder.mkdir()
        (folder / "precious.txt").write_text("keep")
        with self.assertRaises(FileExistsError), ops.new_output_directory(folder):
            self.fail("entered a directory that already existed")
        self.assertEqual((folder / "precious.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
