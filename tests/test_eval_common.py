import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
