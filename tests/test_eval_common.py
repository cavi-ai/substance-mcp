import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

import common


def can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(Path(folder), target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "a").mkdir()
        (self.root / "a/file.txt").write_text("one")
        (self.root / "top.txt").write_text("two")

    def test_lists_every_file_below_the_roots(self):
        self.assertEqual(set(common.fingerprint(self.root)),
                         {self.root / "a/file.txt", self.root / "top.txt"})

    def test_an_untouched_tree_passes(self):
        common.assert_untouched(common.fingerprint(self.root))

    def test_a_changed_file_is_reported(self):
        before = common.fingerprint(self.root)
        (self.root / "top.txt").write_text("changed")
        with self.assertRaisesRegex(AssertionError, "changed top.txt"):
            common.assert_untouched(before)

    def test_a_deleted_file_is_reported(self):
        before = common.fingerprint(self.root)
        (self.root / "a/file.txt").unlink()
        with self.assertRaisesRegex(AssertionError, "deleted file.txt"):
            common.assert_untouched(before)

    @unittest.skipUnless(can_symlink(), "symbolic links are not available")
    def test_a_link_is_recorded_as_itself_and_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        workspace = self.root / "ws"
        workspace.mkdir()
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
        (workspace / "alias.txt").symlink_to(outside / "secret.txt")
        before = common.fingerprint(workspace)
        self.assertEqual(set(before), {workspace / "escape", workspace / "alias.txt"})
        self.assertTrue(all(state.startswith("link:") for state in before.values()))
        # Changing what a link points at is noticed even though the old target is untouched.
        (workspace / "alias.txt").unlink()
        (workspace / "alias.txt").symlink_to(self.root / "top.txt")
        with self.assertRaisesRegex(AssertionError, "changed alias.txt"):
            common.assert_untouched(before)
        (workspace / "escape").unlink()
        with self.assertRaisesRegex(AssertionError, "deleted|changed"):
            common.assert_untouched(before)


class PointerTests(unittest.TestCase):
    def test_follows_keys_and_list_indexes(self):
        data = {"a": [{"b": 5}]}
        self.assertEqual(common.pointer(data, "/a/0/b"), 5)
        with self.assertRaises(KeyError):
            common.pointer(data, "/missing")


class EvaluatorTests(unittest.TestCase):
    def test_source_executable_override_does_not_require_npm(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evaluator = root / "source-mcpeval"
            evaluator.write_text("#!/bin/sh\nexit 0\n")
            evaluator.chmod(0o700)
            with (patch.object(common, "ROOT", root),
                  patch.dict(os.environ, {"MCPEVAL_EXECUTABLE": str(evaluator)}),
                  patch.object(sys, "argv", ["runner", "protocol"])):
                _, selected, _ = common.start()
            self.assertEqual(selected, evaluator.resolve())
            self.assertFalse((root / "node_modules").exists())

    def test_failed_readiness_gate_fails_the_runner_and_records_binary_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            evaluator = output / "mcpeval"
            evaluator.write_bytes(b"test evaluator")
            calls = [subprocess.CompletedProcess([], 0, stdout="mcpeval test\n"),
                     subprocess.CompletedProcess([], 1)]
            with (patch.object(common.subprocess, "run", side_effect=calls) as run,
                  contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit)):
                common.finish(mode="simulated", evaluator=evaluator, output=output,
                              results=[], env={}, app="substance", server=[],
                              manifest_prefix="", native_status="not run")
            command = run.call_args_list[-1].args[0]
            self.assertIn("--min-readiness", command)
            self.assertIn("catalog=100", command)
            self.assertIn("--exclude-latency", command)
            recorded = json.loads((output / "evaluator.json").read_text())
            self.assertEqual(recorded["version"], "mcpeval test")
            self.assertEqual(len(recorded["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
