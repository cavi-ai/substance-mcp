"""Export and save_copy write decisions and leftovers."""

import unittest

from test_texturing import Fixture

from cavi_substance_mcp.client import PainterClient

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


if __name__ == "__main__":
    unittest.main()
