import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from home import isolate_home

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import install

from cavi_substance_mcp import receipts
from cavi_substance_mcp.sampler import operations, runner

TIMEOUT = 30


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "maps").mkdir(parents=True)
        (self.workspace / "assets").mkdir()
        (self.workspace / "maps/basecolor.png").write_bytes(b"fake-png")
        (self.workspace / "maps/normal.png").write_bytes(b"fake-png")
        (self.workspace / "assets/custom.sbsar").write_bytes(b"fake-sbsar")
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
        created_receipt = json.loads(
            (self.workspace / created["manifest"]).read_text()
        )
        self.assertEqual((created_receipt["app"], created_receipt["operation"]),
                         ("sampler", "create_project"))
        self.assertEqual(created_receipt["outputs"][0]["path"], "projects/Rock.ssa")
        copied = self.call("save_as", project="projects/Rock.ssa", directory="copy",
                           name="RockCopy")
        self.assertEqual(copied["project"], "copy/RockCopy.ssa")
        self.assertTrue((self.workspace / "copy/RockCopy.ssa").is_file())
        copied_receipt = json.loads(
            (self.workspace / copied["manifest"]).read_text()
        )
        self.assertEqual((copied_receipt["app"], copied_receipt["operation"]),
                         ("sampler", "save_as"))
        self.assertEqual(copied_receipt["inputs"][0]["path"], "projects/Rock.ssa")

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

    def test_probe_and_catalog(self):
        probe = self.call("probe")
        self.assertTrue(probe["b2m_available"])
        self.assertIn("openpbr", probe["material_models"])
        self.assertIn("base_material", probe["predefined_presets"])
        for name in ("unreal", "unity", "blender"):
            self.assertTrue(probe["targets"][name]["available"])
        catalog = self.call("catalog")
        self.assertIn("image_to_material_B2M", catalog["import_options"])
        self.assertTrue(catalog["export_targets"]["unreal"]["available"])

    def test_create_asset_refuses_duplicate_names(self):
        self.create_project()
        self.call("create_asset", project="projects/Rock.ssa", name="Brick")
        project = self.workspace / "projects/Rock.ssa"
        before = project.read_bytes()
        for name in ("Brick", "Rock"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "already exists"):
                self.call("create_asset", project="projects/Rock.ssa", name=name)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.call("create_asset", project="projects/Rock.ssa", name="Brick", empty=True)
        self.assertEqual(project.read_bytes(), before)
        self.assertEqual(
            [item["name"] for item in
             self.call("inspect_project", project="projects/Rock.ssa")["assets"]],
            ["Rock", "Brick"],
        )

    def test_nonzero_exit_fails_even_when_a_response_was_written(self):
        self.create_project()
        with patch.dict(os.environ, {"FAKE_SAMPLER_EXIT": "3"}):
            with self.assertRaisesRegex(RuntimeError, "Sampler exited 3"):
                self.call("inspect_project", project="projects/Rock.ssa")
            before = (self.workspace / "projects/Rock.ssa").read_bytes()
            with self.assertRaisesRegex(RuntimeError, "Sampler exited 3"):
                self.call("insert_filter", project="projects/Rock.ssa", asset="Rock",
                          filter="Tiling", position=0)
        self.assertEqual((self.workspace / "projects/Rock.ssa").read_bytes(), before)
        self.assertEqual(self.call("inspect_project", project="projects/Rock.ssa")["project"],
                         "projects/Rock.ssa")

    def test_target_export_requires_every_map(self):
        self.create_project()
        arguments = {"project": "projects/Rock.ssa", "asset": "Rock", "target": "unreal",
                     "format": "png", "dry_run": False}
        with (
            patch.dict(os.environ, {"FAKE_SAMPLER_EXPORT": "missing"}),
            self.assertRaisesRegex(RuntimeError, "missing.*occlusionroughnessmetallic"),
        ):
            self.call("export", directory="partial", wait_seconds=1, **arguments)
        self.assertFalse((self.workspace / "partial").exists())
        exported = self.call("export", directory="whole", **arguments)
        self.assertEqual(
            sorted(Path(item).name for item in exported["files"]),
            ["Rock_basecolor.png", "Rock_normal.png", "Rock_occlusionroughnessmetallic.png"],
        )
        generic = self.call("export", directory="raw", project="projects/Rock.ssa",
                            asset="Rock", target="generic", format="png", dry_run=False)
        self.assertTrue(generic["files"])

    def test_export_waits_for_files_to_stop_growing(self):
        self.create_project()
        with patch.dict(os.environ, {"FAKE_SAMPLER_EXPORT": "growing"}):
            exported = self.call("export", project="projects/Rock.ssa", asset="Rock",
                                 directory="grown", format="sbsar", dry_run=False)
        sbsar = self.workspace / "grown/Rock.sbsar"
        self.assertEqual(sbsar.read_text(), "fake" + "-late" * 5)
        manifest = receipts.load(self.workspace / exported["manifest"])
        self.assertEqual(receipts.verify(manifest, self.workspace)["outputs"][0]["size"], 29)

    def test_create_empty_asset(self):
        self.create_project()
        made = self.call("create_asset", project="projects/Rock.ssa", name="Empty", empty=True)
        self.assertEqual(made["asset"]["name"], "Empty")
        self.assertEqual(made["asset"]["layer_count"], 0)
        with self.assertRaisesRegex(ValueError, "empty assets"):
            self.call("create_asset", project="projects/Rock.ssa", name="Bad",
                      empty=True, preset="base_material")

    def test_import_resource(self):
        self.create_project()
        imported = self.call(
            "import_resource", project="projects/Rock.ssa", path="assets/custom.sbsar"
        )
        self.assertEqual(imported["resources"][0]["name"], "custom")
        with self.assertRaisesRegex(ValueError, "inside the workspace"):
            self.call("import_resource", project="missing.ssa", path="assets/custom.sbsar")

    def test_import_resource_receipt(self):
        self.create_project()
        manifest = receipts.build(
            "designer", "cook", self.workspace,
            outputs=[self.workspace / "assets/custom.sbsar"],
        )
        receipts.write(self.workspace / "assets", manifest)
        imported = self.call(
            "import_resource", project="projects/Rock.ssa",
            path="assets/custom.sbsar", source_manifest="assets/manifest.json",
        )
        self.assertTrue(imported["verified"])
        self.assertEqual(imported["source"]["path"], "assets/custom.sbsar")
        self.assertEqual(imported["source_manifest"], "assets/manifest.json")
        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            self.call("import_resource", project="projects/Rock.ssa",
                      path="assets/custom.sbsar", expected_sha256="0" * 64)

    def test_export_wait_seconds(self):
        self.create_project()
        with self.assertRaisesRegex(ValueError, "wait_seconds"):
            self.call("export", project="projects/Rock.ssa", asset="Rock", directory="x",
                      dry_run=False, wait_seconds=0)
        exported = self.call("export", project="projects/Rock.ssa", asset="Rock",
                             directory="waited", format="sbsar", dry_run=False,
                             wait_seconds=1)
        self.assertEqual(exported["files"], ["waited/Rock.sbsar"])
        receipt = json.loads((self.workspace / exported["manifest"]).read_text())
        self.assertEqual((receipt["app"], receipt["operation"]), ("sampler", "export"))
        self.assertEqual(receipt["outputs"][0]["path"], "waited/Rock.sbsar")

    def test_metadata_channels_and_blend(self):
        self.create_project()
        metadata = self.call("set_metadata", project="projects/Rock.ssa", asset="Rock",
                             author="Cavi", category="rock", description="test",
                             tags=["game"], physical_size=[1.0, 2.0, 3.0],
                             custom_metadata={"engine": "unreal"})
        self.assertEqual(metadata["metadata"]["author"], "Cavi")
        self.assertEqual(metadata["metadata"]["tags"], ["game"])
        self.assertEqual(metadata["metadata"]["physical_size"], [1.0, 2.0, 3.0])
        activated = self.call("activate_channels", project="projects/Rock.ssa", asset="Rock",
                              channels=["normal", "height"])
        self.assertTrue(activated["activated"])
        self.assertEqual(activated["channels"], ["normal", "height"])
        deactivated = self.call("deactivate_channels", project="projects/Rock.ssa",
                                asset="Rock", channels=["height"])
        self.assertFalse(deactivated["activated"])
        self.call("insert_filter", project="projects/Rock.ssa", asset="Rock",
                  filter="Tiling", position=0)
        channels = self.call("set_channels", project="projects/Rock.ssa", asset="Rock",
                             layer="Tiling", applies_to=["baseColor"])
        self.assertEqual(channels["applies_to"], ["baseColor"])
        labeled = self.call("set_parameters", project="projects/Rock.ssa", asset="Rock",
                            layer="Tiling", parameters={"Detect Edges": "Off"})
        self.assertEqual(labeled["parameters"]["detect_edges"], 0)
        asset = self.call("inspect_asset", project="projects/Rock.ssa", asset="Rock")
        layer = next(item for item in asset["layers"] if item["name"] == "Tiling")
        with self.assertRaisesRegex(ValueError, "not found"):
            self.call("set_channels", project="projects/Rock.ssa", asset="Rock",
                      layer="Tiling", applies_to=["Missing"])
        edge = next(item for item in layer["parameters"]
                    if item["identifier"] == "detect_edges")
        self.assertEqual(edge["enum_values"], [["Off", 0], ["On", 1]])
        self.assertIn("baseColor", layer["applied_channels"])
        self.call("insert_material", project="projects/Rock.ssa", asset="Rock",
                  material="Brick", position=0)
        blended = self.call("set_blend_parameters", project="projects/Rock.ssa",
                            asset="Rock", layer="Brick", parameters={"Opacity": 0.5})
        self.assertEqual(blended["parameters"]["opacity"], 0.5)

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
        self.assertIn("manifest", exported)
        self.assertFalse((self.workspace / "out/manifest.json").is_symlink())

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

    @staticmethod
    def _script(root, body):
        executable = root / "sampler"
        executable.write_text(f"#!{sys.executable}\nimport signal, sys, time\n{body}")
        executable.chmod(0o755)
        return executable

    def test_timeout_asks_politely_before_killing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "terminated"
            executable = self._script(root, (
                "def stop(*_):\n"
                f"    open({str(marker)!r}, 'w').write('term')\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "time.sleep(30)\n"
            ))
            with (
                patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(executable)}),
                self.assertRaisesRegex(TimeoutError, "exceeded"),
            ):
                asyncio.run(runner.run_script(root / "run.py", 1.5, root))
            self.assertTrue(marker.is_file())

    def test_process_ignoring_sigterm_is_killed_after_the_grace_period(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = self._script(root, (
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(30)\n"
            ))
            started = time.monotonic()
            with (
                patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(executable)}),
                patch.object(runner, "TERM_GRACE", 0.5),
                self.assertRaisesRegex(TimeoutError, "killed"),
            ):
                asyncio.run(runner.run_script(root / "run.py", 1.5, root))
            self.assertLess(time.monotonic() - started, 10)

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

    @staticmethod
    def _windows_executable():
        return runner.Installation(
            Path("/fake/Adobe Substance 3D Sampler.exe"), "test"
        )

    def test_running_windows(self):
        with (
            patch.object(runner.sys, "platform", "win32"),
            patch.object(runner, "installation", return_value=self._windows_executable()),
            patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, '"Adobe Substance 3D Sampler.exe","1234"\n', ""
            )),
        ):
            self.assertTrue(runner.running())
        with (
            patch.object(runner.sys, "platform", "win32"),
            patch.object(runner, "installation", return_value=self._windows_executable()),
            patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "INFO: No tasks are running\n", ""
            )),
        ):
            self.assertFalse(runner.running())

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


class SamplerWorkspaceRootTests(unittest.TestCase):
    def setUp(self):
        self.home = isolate_home(self)
        self.default = self.home / "Documents/SubstanceMCP"
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        patcher = patch.dict(os.environ, {"CAVI_SAMPLER_APP": str(install(Path(scratch.name)))})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, operation, **arguments):
        return asyncio.run(operations.invoke(operation, arguments, TIMEOUT))

    def test_status_does_not_create_the_default_workspace(self):
        status = operations.status()
        self.assertEqual(status["workspace"], str(self.default))
        self.assertFalse(status["workspace_exists"])
        self.assertFalse(self.default.exists())

    def test_a_launch_creates_the_default_workspace(self):
        self.assertTrue(self.call("catalog"))
        self.assertTrue(self.default.is_dir())

    def test_a_missing_explicit_workspace_is_refused_and_not_created(self):
        missing = self.home / "not-there"
        with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(missing)}), \
                self.assertRaisesRegex(ValueError, "CAVI_SUBSTANCE_WORKSPACE.*does not exist"):
            self.call("catalog")
        self.assertFalse(missing.exists())


class SamplerServerTests(unittest.TestCase):
    def test_tools_listed(self):
        from cavi_substance_mcp.sampler.server import mcp

        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            set(tools),
            {"sampler_status", "sampler_inspect", "sampler_project", "sampler_asset",
             "sampler_layer", "sampler_export", "sampler_build", "sampler_pack"},
        )
        self.assertTrue(tools["sampler_status"].annotations.readOnlyHint)
        self.assertTrue(tools["sampler_inspect"].annotations.readOnlyHint)
        self.assertFalse(tools["sampler_asset"].annotations.readOnlyHint)


if __name__ == "__main__":
    unittest.main()
