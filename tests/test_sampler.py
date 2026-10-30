import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import install

from cavi_substance_mcp.sampler import operations, runner

TIMEOUT = 30


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "maps").mkdir(parents=True)
        (self.workspace / "maps/basecolor.png").write_bytes(b"fake-png")
        (self.workspace / "maps/normal.png").write_bytes(b"fake-png")
        (self.workspace / "outside").mkdir()
        (self.workspace / "outside/escape.ssa").write_text("{}")
        app = install(root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_"))}
        env.update({"CAVI_SAMPLER_APP": str(app), "CAVI_SUBSTANCE_WORKSPACE":
                    str(self.workspace)})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def call(self, operation, **arguments):
        return asyncio.run(operations.invoke(operation, arguments, TIMEOUT))

    def create_project(self):
        return self.call("create_project", directory="projects", name="Rock")

    def test_status(self):
        status = operations.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["version"], "0.0.0")
        self.assertFalse(status["running"])
        self.assertEqual(status["workspace"], str(self.workspace.resolve()))

    def test_create_inspect_and_save_as(self):
        created = self.create_project()
        self.assertEqual(created["project"], "projects/Rock.ssa")
        self.assertTrue((self.workspace / "projects/Rock.ssa").is_file())
        inspected = self.call("inspect_project", project="projects/Rock.ssa")
        self.assertEqual(inspected["project"], "projects/Rock.ssa")
        self.assertEqual([item["name"] for item in inspected["assets"]], ["Rock"])
        copied = self.call("save_as", project="projects/Rock.ssa", directory="copy",
                           name="RockCopy")
        self.assertEqual(copied["project"], "copy/RockCopy.ssa")
        self.assertTrue((self.workspace / "copy/RockCopy.ssa").is_file())

    def test_asset_create_delete_and_catalog(self):
        self.create_project()
        made = self.call("create_asset", project="projects/Rock.ssa", name="Brick")
        self.assertEqual(made["asset"]["name"], "Brick")
        self.assertTrue(made["saved"])
        self.assertEqual(
            [item["name"] for item in
             self.call("inspect_project", project="projects/Rock.ssa")["assets"]],
            ["Rock", "Brick"],
        )
        removed = self.call("delete_asset", project="projects/Rock.ssa", asset="Brick")
        self.assertEqual(removed["deleted"], "Brick")
        catalog = self.call("catalog")
        self.assertIn("Tiling", catalog["filters"])
        self.assertIn("Blender Cycles/Eevee", catalog["export_presets"])

    def test_layer_mutations_and_export(self):
        self.create_project()
        imported = self.call(
            "import_images", project="projects/Rock.ssa", asset="Rock",
            images=["maps/basecolor.png"], position=0, import_option="use_as_bitmap",
        )
        self.assertEqual(imported["layer"]["name"], "Image Import")
        textures = self.call(
            "import_textures", project="projects/Rock.ssa", asset="Rock",
            images={"basecolor": "maps/basecolor.png", "normal": "maps/normal.png"},
            position=0, insert_above=True,
        )
        self.assertEqual(textures["layer"]["output_usages"], ["basecolor", "normal"])
        filtered = self.call(
            "insert_filter", project="projects/Rock.ssa", asset="Rock",
            filter="Equalize", position=1,
        )
        self.assertEqual(filtered["layer"]["name"], "Equalize")
        material = self.call(
            "insert_material", project="projects/Rock.ssa", asset="Rock",
            material="Brick", position=0, blend_mode="blend_normal",
        )
        self.assertEqual(material["layer"]["name"], "Brick")
        layers = self.call("inspect_asset", project="projects/Rock.ssa", asset="Rock")
        self.assertEqual(len(layers["layers"]), 4)
        self.assertEqual(
            {item["type"] for item in layers["layers"]},
            {"layer_image_import", "layer_material", "layer_filter"},
        )

    def test_resource_names_may_contain_slashes(self):
        self.create_project()
        filtered = self.call(
            "insert_filter", project="projects/Rock.ssa", asset="Rock",
            filter="Brightness/Contrast", position=0,
        )
        self.assertEqual(filtered["layer"]["name"], "Brightness/Contrast")
        preview = self.call(
            "export", project="projects/Rock.ssa", asset="Rock", directory="preview",
            preset="Blender Cycles/Eevee", dry_run=True,
        )
        self.assertEqual(preview["preset"], "Blender Cycles/Eevee")
        self.assertFalse((self.workspace / "preview").exists())

    def test_export_dry_run_and_files(self):
        self.create_project()
        self.call("insert_filter", project="projects/Rock.ssa", asset="Rock",
                  filter="Equalize", position=0)
        preview = self.call(
            "export", project="projects/Rock.ssa", asset="Rock", directory="out",
            format="sbsar", dry_run=True,
        )
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["files"], [])
        self.assertFalse((self.workspace / "out").exists())
        exported = self.call(
            "export", project="projects/Rock.ssa", asset="Rock", directory="out",
            format="sbsar", dry_run=False,
        )
        self.assertEqual(exported["files"], ["out/Rock.sbsar"])
        self.assertTrue((self.workspace / "out/Rock.sbsar").is_file())

    def test_export_failure_removes_directory(self):
        self.create_project()
        self.call("create_asset", project="projects/Rock.ssa", name="fail")
        with self.assertRaisesRegex(RuntimeError, "fake export failure"):
            self.call("export", project="projects/Rock.ssa", asset="fail", directory="bad",
                      format="png", dry_run=False)
        self.assertFalse((self.workspace / "bad").exists())

    def test_paths_and_existing_outputs_are_refused(self):
        self.create_project()
        for path in ("../outside.ssa", "/etc/passwd", "missing.ssa"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("inspect_project", project=path)
        (self.workspace / "existing").mkdir()
        with self.assertRaises(FileExistsError):
            self.call("export", project="projects/Rock.ssa", asset="Rock",
                      directory="existing", dry_run=False)
        with self.assertRaises(ValueError):
            self.call("import_images", project="projects/Rock.ssa", asset="Rock",
                      images=["../outside.png"], position=0)

    def test_invalid_action_arguments(self):
        self.create_project()
        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            self.call("unknown")
        with self.assertRaisesRegex(ValueError, "not found"):
            self.call("insert_filter", project="projects/Rock.ssa", asset="Rock",
                      filter="Missing", position=0)


class SamplerRunnerTests(unittest.TestCase):
    def test_timeout_kills_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "sampler"
            executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            executable.chmod(0o755)
            with (
                patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(executable)}),
                self.assertRaisesRegex(TimeoutError, "killed"),
            ):
                asyncio.run(runner.run_script(root / "run.py", 0.5, root))

    def test_app_bundle_on_linux(self):
        with tempfile.TemporaryDirectory() as folder:
            app = install(Path(folder))
            with (
                patch.dict(os.environ, {"CAVI_SAMPLER_APP": str(app)}),
                patch.object(runner.sys, "platform", "linux"),
            ):
                found = runner.installation()
                self.assertTrue(found.executable.is_file())
                self.assertEqual(found.version, "0.0.0")

    def test_installation_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "sampler"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            with patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(executable)}):
                found = runner.installation()
                self.assertEqual(found.executable, executable)
                self.assertEqual(found.source, "CAVI_SAMPLER_BIN")


class SamplerServerTests(unittest.TestCase):
    def test_tools_listed(self):
        from cavi_substance_mcp.sampler.server import mcp

        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            set(tools),
            {"sampler_status", "sampler_inspect", "sampler_project", "sampler_asset",
             "sampler_layer", "sampler_export", "sampler_build"},
        )
        self.assertTrue(tools["sampler_status"].annotations.readOnlyHint)
        self.assertTrue(tools["sampler_inspect"].annotations.readOnlyHint)
        self.assertFalse(tools["sampler_asset"].annotations.readOnlyHint)


if __name__ == "__main__":
    unittest.main()
