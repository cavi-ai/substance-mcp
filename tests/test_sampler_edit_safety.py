import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from env import FAKE_SAMPLER, patched_environment
from sampler_fake_cli import ENV, install

from cavi_substance_mcp.sampler import launch, server

TIMEOUT = 30


class EditSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        self.workspace.mkdir()
        app = install(root)
        self.env = patched_environment(FAKE_SAMPLER, {
            ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env.start()
        self.call("create_project", directory="projects", name="Rock")
        self.project = self.workspace / "projects/Rock.ssa"

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def call(self, operation, timeout=TIMEOUT, **arguments):
        return asyncio.run(launch.invoke(operation, arguments, timeout))

    def leftovers(self):
        return sorted(item.name for item in self.workspace.rglob(".cavi-work-*"))

    def add_filter(self, **env):
        with patch.dict(os.environ, env):
            return self.call("insert_filter", project="projects/Rock.ssa", asset="Rock",
                             filter="Tiling", position=0)

    def test_edit_goes_through_a_copy_that_replaces_the_project(self):
        before = self.project.read_bytes()
        real = os.replace
        seen = []

        def replace(source, target):
            seen.append((Path(source), Path(target), self.project.read_bytes()))
            return real(source, target)

        with patch.object(launch.os, "replace", side_effect=replace):
            made = self.add_filter()
        self.assertEqual(made["project"], "projects/Rock.ssa")
        self.assertEqual(len(seen), 1)
        source, target, original = seen[0]
        self.assertEqual(target, self.project)
        self.assertEqual(source.parent, self.project.parent)
        self.assertEqual(original, before)
        self.assertNotEqual(self.project.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])
        listed = self.call("inspect_asset", project="projects/Rock.ssa", asset="Rock")
        self.assertEqual([item["name"] for item in listed["layers"]], ["Tiling"])

    def test_failed_save_leaves_the_project_untouched(self):
        before = self.project.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "did not save"):
            self.add_filter(FAKE_SAMPLER_SAVE="fail")
        self.assertEqual(self.project.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])

    def test_timeout_during_save_cannot_corrupt_the_project(self):
        before = self.project.read_bytes()
        with patch.dict(os.environ, {"FAKE_SAMPLER_SAVE": "hang"}), \
                self.assertRaisesRegex(TimeoutError, "killed"):
            self.call("insert_filter", timeout=2, project="projects/Rock.ssa",
                      asset="Rock", filter="Tiling", position=0)
        self.assertEqual(self.project.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])

    def test_unchanged_edit_does_not_rewrite_the_project(self):
        self.add_filter()
        before = self.project.stat()
        content = self.project.read_bytes()
        result = self.call("set_parameters", project="projects/Rock.ssa", asset="Rock",
                           layer="Tiling", parameters={"Tiling": 1.0})
        self.assertFalse(result["changed"])
        self.assertEqual(result["project"], "projects/Rock.ssa")
        self.assertEqual(self.project.read_bytes(), content)
        self.assertEqual(self.project.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.leftovers(), [])

    def test_missing_project_is_refused_before_launching_sampler(self):
        with patch.object(launch.runner, "run_script") as run_script, \
                self.assertRaisesRegex(ValueError, "inside the workspace"):
            self.call("insert_filter", project="projects/Missing.ssa", asset="Rock",
                      filter="Tiling", position=0)
        run_script.assert_not_called()

    def test_asset_delete_requires_confirmation(self):
        arguments = {"action": "delete", "project": "projects/Rock.ssa", "asset": "Rock"}
        with self.assertRaisesRegex(ValueError, "confirm"):
            asyncio.run(server.sampler_asset(**arguments))
        with self.assertRaisesRegex(ValueError, "confirm"):
            asyncio.run(server.sampler_asset(**arguments, confirm=False))
        with self.assertRaisesRegex(ValueError, "confirm"):
            asyncio.run(server.sampler_asset(
                action="create", project="projects/Rock.ssa", name="Other", confirm=True))
        removed = asyncio.run(server.sampler_asset(**arguments, confirm=True))
        self.assertEqual(removed["deleted"], "Rock")

    def test_destructive_tools_are_annotated(self):
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        for name in ("sampler_asset", "sampler_layer"):
            self.assertTrue(tools[name].annotations.destructive_hint, name)
            self.assertFalse(tools[name].annotations.read_only_hint, name)
        for name in ("sampler_project", "sampler_export", "sampler_build", "sampler_pack"):
            self.assertFalse(tools[name].annotations.destructive_hint, name)


if __name__ == "__main__":
    unittest.main()
