import asyncio
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from home import isolate_home

from cavi_substance_mcp.core import workspace
from cavi_substance_mcp.painter import client


def can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(Path(folder), target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


needs_symlinks = unittest.skipUnless(can_symlink(), "symbolic links are not available")


class RootTests(unittest.TestCase):
    def setUp(self):
        self.home = isolate_home(self)
        self.default = self.home / "Documents" / "SubstanceMCP"

    def test_default_is_documents_substancemcp(self):
        self.assertEqual(workspace.default_root().resolve(), self.default.resolve())
        self.assertEqual(workspace.root(), self.default)

    def test_environment_selects_the_root(self):
        chosen = self.home / "elsewhere"
        chosen.mkdir()
        with patch.dict(os.environ, {workspace.ENV: str(chosen)}):
            self.assertEqual(workspace.root(), chosen)

    def test_empty_environment_value_counts_as_unset(self):
        with patch.dict(os.environ, {workspace.ENV: ""}):
            self.assertEqual(workspace.root(), self.default)

    def test_argument_beats_environment(self):
        argument, variable = self.home / "argument", self.home / "variable"
        argument.mkdir()
        variable.mkdir()
        with patch.dict(os.environ, {workspace.ENV: str(variable)}):
            self.assertEqual(workspace.root(argument), argument)

    def test_a_relative_environment_root_is_refused(self):
        with patch.dict(os.environ, {workspace.ENV: "relative"}), \
                self.assertRaisesRegex(ValueError, f"{workspace.ENV} must be an absolute path"):
            workspace.root()

    def test_reading_never_creates_anything(self):
        workspace.root()
        with patch.dict(os.environ, {workspace.ENV: str(self.home / "missing")}):
            workspace.root()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_the_default_is_created_on_demand(self):
        self.assertEqual(workspace.root(create=True), self.default)
        self.assertTrue(self.default.is_dir())
        self.assertEqual(workspace.root(create=True), self.default)

    def test_a_missing_environment_workspace_is_refused(self):
        missing = self.home / "missing"
        with patch.dict(os.environ, {workspace.ENV: str(missing)}), \
                self.assertRaises(ValueError) as caught:
            workspace.root(create=True)
        message = str(caught.exception)
        self.assertIn(workspace.ENV, message)
        self.assertIn(str(missing), message)
        self.assertIn("does not exist", message)
        self.assertIn("SubstanceMCP", message)
        self.assertFalse(missing.exists())

    def test_a_missing_argument_workspace_is_refused(self):
        missing = self.home / "missing"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            workspace.root(missing, create=True)
        self.assertFalse(missing.exists())

    def test_a_file_is_not_a_workspace(self):
        occupied = self.home / "file"
        occupied.write_text("x")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            workspace.root(occupied, create=True)
        self.default.parent.mkdir()
        self.default.write_text("x")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            workspace.root(create=True)

    def test_existing_requires_a_directory(self):
        self.assertEqual(workspace.existing(self.home), self.home)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            workspace.existing(self.home / "missing")
        (self.home / "file").write_text("x")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            workspace.existing(self.home / "file")

    def test_existing_root_helper_never_creates(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            workspace.existing_root()
        self.assertFalse(self.default.exists())
        self.default.mkdir(parents=True)
        self.assertEqual(workspace.existing_root(), self.default)


class PathRuleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name).resolve()
        self.root = self.base / "ws"
        self.outside = self.base / "outside"
        for path in (self.root / "maps", self.outside):
            path.mkdir(parents=True)
        (self.root / "maps/a.png").write_bytes(b"png")
        (self.root / "maps/B.PNG").write_bytes(b"png")
        (self.root / "maps/a.txt").write_bytes(b"txt")
        (self.outside / "secret.png").write_bytes(b"png")

    def test_input_inside_the_root(self):
        self.assertEqual(workspace.input_file(self.root, "maps/a.png", {".png"}),
                         self.root / "maps/a.png")
        self.assertEqual(workspace.input_file(self.root, "maps/../maps/a.png", {".png"}),
                         self.root / "maps/a.png")

    def test_suffix_match_ignores_case(self):
        self.assertEqual(workspace.input_file(self.root, "maps/B.PNG", {".png"}).name, "B.PNG")

    def test_input_refusals_share_one_message(self):
        for name in ("maps/a.txt", "maps", "maps/missing.png", "", "../outside/secret.png",
                     str(self.outside / "secret.png"), "maps/a\x00.png"):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "supported file inside the workspace"):
                workspace.input_file(self.root, name, {".png"})
        for name in (None, 3, ["maps/a.png"]):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "supported file inside the workspace"):
                workspace.input_file(self.root, name, {".png"})

    @needs_symlinks
    def test_input_symlinks_may_not_leave_the_root(self):
        (self.root / "escape.png").symlink_to(self.outside / "secret.png")
        (self.root / "via").symlink_to(self.outside, target_is_directory=True)
        (self.root / "inside.png").symlink_to(self.root / "maps/a.png")
        for name in ("escape.png", "via/secret.png"):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "supported file inside the workspace"):
                workspace.input_file(self.root, name, {".png"})
        self.assertEqual(workspace.input_file(self.root, "inside.png", {".png"}),
                         self.root / "maps/a.png")

    def test_input_needs_an_existing_root(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            workspace.input_file(self.base / "gone", "a.png", {".png"})

    def test_new_output_directory(self):
        made = workspace.new_output_dir(self.root, "out")
        self.assertEqual(made, self.root / "out")
        self.assertTrue(made.is_dir())
        self.assertEqual(workspace.new_output_dir(self.root, "maps/sub"),
                         self.root / "maps/sub")

    def test_existing_output_is_never_reused(self):
        (self.root / "used").mkdir()
        (self.root / "file").write_text("x")
        for name in ("used", "maps", "file"):
            with self.subTest(name=name), self.assertRaises(FileExistsError):
                workspace.new_output_dir(self.root, name)

    def test_output_must_be_a_new_directory_inside_the_root(self):
        for name in ("", ".", "..", "../escape", str(self.outside / "new"), "a\x00b"):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "inside the workspace"):
                workspace.new_output_dir(self.root, name)
        self.assertEqual(list(self.outside.iterdir()), [self.outside / "secret.png"])

    def test_output_parent_must_exist_unless_requested(self):
        with self.assertRaisesRegex(ValueError, "parent directory must exist"):
            workspace.new_output_dir(self.root, "a/b/c")
        self.assertFalse((self.root / "a").exists())
        made = workspace.new_output_dir(self.root, "a/b/c", parents=True)
        self.assertEqual(made, self.root / "a/b/c")
        self.assertTrue(made.is_dir())

    @needs_symlinks
    def test_output_may_not_leave_the_root_through_a_link(self):
        (self.root / "via").symlink_to(self.outside, target_is_directory=True)
        for parents in (False, True):
            with self.subTest(parents=parents), self.assertRaisesRegex(
                    ValueError, "inside the workspace"):
                workspace.new_output_dir(self.root, "via/new", parents=parents)
        self.assertEqual(list(self.outside.iterdir()), [self.outside / "secret.png"])

    def test_output_path_validates_without_creating(self):
        self.assertEqual(workspace.output_path(self.root, "later"), self.root / "later")
        self.assertFalse((self.root / "later").exists())

    def test_rel_is_posix_and_confined(self):
        self.assertEqual(workspace.rel(self.root, self.root / "maps" / "a.png"), "maps/a.png")
        with self.assertRaises(ValueError):
            workspace.rel(self.root, self.outside / "secret.png")


class RemoveTreeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tree = Path(folder.name) / "tree"
        (self.tree / "a/b").mkdir(parents=True)
        (self.tree / "a/b/file.txt").write_text("x")
        (self.tree / "top.txt").write_text("x")

    def test_removes_the_tree_and_reports_nothing_left(self):
        self.assertEqual(workspace.remove_tree(self.tree), [])
        self.assertFalse(self.tree.exists())

    def test_accepts_text_paths_and_missing_paths(self):
        self.assertEqual(workspace.remove_tree(str(self.tree)), [])
        self.assertEqual(workspace.remove_tree(self.tree), [])

    def test_reports_everything_that_could_not_be_removed(self):
        with patch.object(shutil, "rmtree"):
            left = workspace.remove_tree(self.tree)
        self.assertEqual(left[-1], str(self.tree))
        self.assertEqual(
            set(left),
            {str(self.tree), str(self.tree / "a"), str(self.tree / "a/b"),
             str(self.tree / "a/b/file.txt"), str(self.tree / "top.txt")},
        )
        self.assertEqual(left[:-1], sorted(left[:-1], reverse=True))

    def test_a_read_only_file_does_not_stop_removal(self):
        # Windows refuses to delete a file with the read-only attribute. Emulate that on
        # every platform (and for root, which ignores permission bits) by making unlink
        # fail for a file whose write bit is clear.
        target = self.tree / "top.txt"
        target.chmod(stat.S_IREAD)
        real = os.unlink

        def unlink(path, *args, dir_fd=None, **kwargs):
            if os.path.basename(str(path)) == "top.txt" and not (
                    os.stat(path, dir_fd=dir_fd).st_mode & stat.S_IWUSR):
                raise PermissionError(13, "read-only file", str(path))
            return real(path, *args, dir_fd=dir_fd, **kwargs)

        with patch.object(os, "unlink", unlink):
            self.assertEqual(workspace.remove_tree(self.tree), [])
        self.assertFalse(self.tree.exists())

    def test_without_the_unlock_handler_that_file_would_be_left_behind(self):
        target = self.tree / "top.txt"
        target.chmod(stat.S_IREAD)
        real = os.unlink

        def unlink(path, *args, dir_fd=None, **kwargs):
            if os.path.basename(str(path)) == "top.txt" and not (
                    os.stat(path, dir_fd=dir_fd).st_mode & stat.S_IWUSR):
                raise PermissionError(13, "read-only file", str(path))
            return real(path, *args, dir_fd=dir_fd, **kwargs)

        with patch.object(os, "unlink", unlink):
            shutil.rmtree(self.tree, ignore_errors=True)
        self.assertTrue(target.exists(), "the emulation no longer reproduces the failure")

    def test_the_unlock_handler_clears_the_read_only_bit_and_retries(self):
        target = self.tree / "top.txt"
        target.chmod(stat.S_IREAD)
        retried = []
        workspace._unlock(self.tree)(retried.append, str(target), PermissionError())
        self.assertEqual(retried, [str(target)])
        self.assertTrue(os.access(target, os.W_OK))

    def test_the_unlock_handler_never_touches_the_parent_of_the_tree(self):
        parent = self.tree.parent
        before = stat.S_IMODE(parent.stat().st_mode)
        workspace._unlock(self.tree)(lambda path: None, str(self.tree), PermissionError())
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), before)


class NewOutputDirectoryTests(unittest.TestCase):
    """The context manager that makes an output directory and cleans it up on failure."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def test_keeps_the_directory_when_the_body_succeeds(self):
        with workspace.new_output_directory(self.root, "out") as made:
            self.assertEqual(made, self.root / "out")
            (made / "file.txt").write_text("x")
        self.assertEqual((self.root / "out/file.txt").read_text(), "x")

    def test_removes_the_directory_and_its_content_when_the_body_raises(self):
        for error in (RuntimeError("boom"), KeyboardInterrupt(), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as caught, \
                        workspace.new_output_directory(self.root, "out") as made:
                    (made / "sub").mkdir()
                    (made / "sub/file.txt").write_text("x")
                    raise error
                self.assertIs(caught.exception, error)
                self.assertFalse((self.root / "out").exists())

    def test_never_touches_a_directory_that_already_exists(self):
        (self.root / "used").mkdir()
        (self.root / "used/keep.txt").write_text("x")
        with self.assertRaises(FileExistsError), \
                workspace.new_output_directory(self.root, "used"):
            self.fail("the body must not run")
        self.assertEqual((self.root / "used/keep.txt").read_text(), "x")

    def test_applies_the_output_rules(self):
        with self.assertRaisesRegex(ValueError, "inside the workspace"), \
                workspace.new_output_directory(self.root, "../escape"):
            self.fail("the body must not run")

    def test_can_create_missing_parents(self):
        with workspace.new_output_directory(self.root, "a/b", parents=True) as made:
            self.assertEqual(made, self.root / "a/b")
        self.assertTrue((self.root / "a/b").is_dir())

    def test_leftovers_are_named_in_the_error(self):
        with patch.object(shutil, "rmtree"), \
                self.assertRaises(RuntimeError) as caught, \
                workspace.new_output_directory(self.root, "out") as made:
            (made / "partial.png").write_text("x")
            raise ValueError("cook failed")
        message = str(caught.exception)
        self.assertIn("ValueError: cook failed", message)
        self.assertIn("could not remove the incomplete output", message)
        self.assertIn(str(self.root / "out/partial.png"), message)
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_a_runtime_error_keeps_its_own_text(self):
        with patch.object(shutil, "rmtree"), self.assertRaises(RuntimeError) as caught, \
                workspace.new_output_directory(self.root, "out"):
            raise RuntimeError("sbscooker failed")
        self.assertTrue(str(caught.exception).startswith("sbscooker failed; could not remove"))

    def test_leftovers_after_a_cancellation_are_a_note_on_the_original_error(self):
        error = asyncio.CancelledError()
        with patch.object(shutil, "rmtree"), self.assertRaises(asyncio.CancelledError) as caught, \
                workspace.new_output_directory(self.root, "out"):
            raise error
        self.assertIs(caught.exception, error)
        self.assertIn(str(self.root / "out"), "\n".join(error.__notes__))

    def test_a_long_list_of_leftovers_is_shortened(self):
        with patch.object(shutil, "rmtree"), self.assertRaises(RuntimeError) as caught, \
                workspace.new_output_directory(self.root, "out") as made:
            for index in range(30):
                (made / f"{index:02}.png").write_text("x")
            raise ValueError("boom")
        self.assertIn("and 21 more", str(caught.exception))


class RemoveScratchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tree = Path(folder.name) / "scratch"
        (self.tree / "sub").mkdir(parents=True)
        (self.tree / "sub/file.txt").write_text("x")

    def test_removes_the_tree_without_a_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            workspace.remove_scratch(self.tree)
        self.assertFalse(self.tree.exists())

    def test_warns_about_what_is_left(self):
        with patch.object(shutil, "rmtree"), self.assertWarns(RuntimeWarning) as caught:
            workspace.remove_scratch(self.tree)
        self.assertIn(str(self.tree / "sub/file.txt"), str(caught.warning))
        self.assertTrue(self.tree.exists())


PAYLOAD_INPUT_PROBE = """
import sys
from pathlib import Path
namespace = {}
exec(compile(sys.stdin.read(), "cavi_painter_operations", "exec"), namespace)
try:
    namespace["color_texture"](Path(sys.argv[1]), "maps/missing.png", Path(sys.argv[1]))
except Exception as exc:
    print(type(exc).__name__ + ": " + str(exc))
"""


class PayloadPathRuleTests(unittest.TestCase):
    """The Painter payload, run as the client sends it, applies the workspace path rules."""

    def test_a_missing_input_is_refused_with_the_workspace_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "maps").mkdir()
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", PAYLOAD_INPUT_PROBE, str(root)],
                input=client.SOURCE, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"ValueError: {workspace.INPUT_MESSAGE}")


if __name__ == "__main__":
    unittest.main()
