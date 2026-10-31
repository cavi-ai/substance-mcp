import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from cavi_substance_mcp.sampler import batch, job, launch, runner


class NativeSamplerTests(unittest.TestCase):
    def setUp(self):
        if runner.installation().executable is None:
            self.skipTest("Substance 3D Sampler is not installed")

    def _workspace(self, folder):
        workspace = Path(folder)
        (workspace / "maps").mkdir()
        (workspace / "jobs").mkdir()
        Image.new("RGB", (256, 256), (200, 100, 50)).save(workspace / "maps/basecolor.png")
        return workspace

    def _write_job(self, workspace, name, target, import_option="use_as_bitmap",
                   outputs=None, empty_asset=False):
        if empty_asset:
            project = {"name": f"{name}Project"}
            steps = [
                {"action": "create_asset", "name": "Cavi", "empty": True},
            ]
        else:
            project = {
                "name": f"{name}Project",
                "asset": {"name": "Cavi", "model": "openpbr", "preset": "base_material"},
            }
            steps = []
        steps.extend([
            {"action": "import_images", "asset": "Cavi",
             "images": ["maps/basecolor.png"], "position": 0,
             "import_option": import_option},
            {"action": "export", "asset": "Cavi", "target": target, "resolution": 256},
        ])
        if outputs is not None:
            steps[-1]["outputs"] = outputs
        data = {"schema": 1, "directory": f"jobs/{name}", "project": project, "steps": steps}
        path = workspace / "jobs" / f"{name}.json"
        path.write_text(json.dumps(data))
        return f"jobs/{name}.json"

    def test_probe_targets_and_b2m(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with tempfile.TemporaryDirectory() as folder:
            workspace = self._workspace(folder)
            probe = asyncio.run(launch.invoke("probe", {}, 300))
            self.assertTrue(probe["b2m_available"])
            self.assertIn("openpbr", probe["material_models"])
            self.assertIn("base_material", probe["predefined_presets"])
            available = [
                target for target in ("unity", "blender")
                if probe["targets"][target]["available"]
            ]
            self.assertTrue(available, "none of the Unity or Blender presets are installed")
            for target in available:
                with self.subTest(target=target):
                    job = self._write_job(workspace, target, target)
                    manifest = batch.run_job_file(job, workspace, timeout_seconds=600)
                    self.assertEqual(manifest["status"], "success")
                    paths = [item["path"] for item in manifest["outputs"]]
                    self.assertTrue(any(item.endswith(".sbsar") for item in paths))
                    self.assertTrue(any(item.endswith(".png") for item in paths))
            job = self._write_job(
                workspace, "b2m", "generic", import_option="image_to_material_B2M",
                outputs=["sbsar"], empty_asset=True,
            )
            manifest = batch.run_job_file(job, workspace, timeout_seconds=600)
            self.assertEqual(manifest["status"], "success")
            paths = [item["path"] for item in manifest["outputs"]]
            self.assertTrue(any(item.endswith(".sbsar") for item in paths))

    def test_status(self):
        status = launch.status()
        self.assertTrue(status["available"])
        self.assertTrue(Path(status["executable"]).is_file())

    def test_create_and_inspect(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            created = asyncio.run(launch.invoke(
                "create_project", {"directory": "projects", "name": "Cavi"}, 300
            ))
            self.assertEqual(created["project"], "projects/Cavi.ssa")
            inspected = asyncio.run(launch.invoke(
                "inspect_project", {"project": "projects/Cavi.ssa"}, 300
            ))
            self.assertEqual(inspected["project"], "projects/Cavi.ssa")
            copied = asyncio.run(launch.invoke("save_as", {
                "project": "projects/Cavi.ssa", "directory": "copy", "name": "CaviCopy",
            }, 300))
            self.assertEqual(copied["project"], "copy/CaviCopy.ssa")
            self.assertTrue((Path(folder) / "copy/CaviCopy.ssa").is_file())

    def test_authoring_operations(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            created = asyncio.run(launch.invoke(
                "create_project", {"directory": "projects", "name": "Authoring"}, 300
            ))
            inserted = asyncio.run(launch.invoke("insert_filter", {
                "project": created["project"], "asset": "Authoring",
                "filter": "Tiling", "position": 0,
            }, 300))
            labels = [item["label"] for item in inserted["layer"]["parameters"]]
            self.assertIn("Threshold", labels)
            changed = asyncio.run(launch.invoke("set_parameters", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "parameters": {"Threshold": 0.1},
            }, 300))
            self.assertAlmostEqual(changed["parameters"]["edge_threshold"], 0.1, places=5)
            choices = [
                item for item in inserted["layer"]["parameters"]
                if item["type"] == "integer" and item["enum_values"]
            ]
            if choices:
                parameter = choices[0]
                label, value = parameter["enum_values"][0]
                labeled = asyncio.run(launch.invoke("set_parameters", {
                    "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                    "parameters": {parameter["identifier"]: label},
                }, 300))
                self.assertEqual(labeled["parameters"][parameter["identifier"]], value)
            applied = asyncio.run(launch.invoke("apply_preset", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "preset": "Tiling",
            }, 300))
            self.assertEqual(applied["preset"], "Tiling")
            removed = asyncio.run(launch.invoke("remove_layer", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "confirm": True,
            }, 300))
            self.assertEqual(removed["removed"], "Tiling")

    def test_source_project_authoring(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            workspace = self._workspace(folder)
            seed_data = {
                "schema": 1,
                "directory": "jobs/seed",
                "project": {"name": "seedProject",
                            "asset": {"name": "Cavi", "model": "openpbr",
                                      "preset": "base_material"}},
                "steps": [
                    {"action": "import_textures", "asset": "Cavi",
                     "images": {"basecolor": "maps/basecolor.png",
                                "normal": "maps/basecolor.png"}, "position": 0},
                    {"action": "insert_filter", "asset": "Cavi", "filter": "Tiling",
                     "position": 1},
                    {"action": "export", "asset": "Cavi", "target": "unreal",
                     "resolution": 256, "outputs": ["sbsar"]},
                ],
            }
            seed = workspace / "jobs/seed.json"
            seed.write_text(json.dumps(seed_data))
            batch.run_job_file("jobs/seed.json", workspace, timeout_seconds=600)
            source = workspace / "jobs/seed/seedProject.ssa"
            digest = source.read_bytes()
            seeded = asyncio.run(launch.invoke(
                "inspect_asset", {"project": "jobs/seed/seedProject.ssa", "asset": "Cavi"},
                300,
            ))
            seed_tiling = next(layer for layer in seeded["layers"]
                               if layer["name"] == "Tiling")
            channel = seed_tiling["applied_channels"][0]
            channel_label = seed_tiling.get("usage_labels", {}).get(channel, channel)
            data = {
                "schema": 1,
                "directory": "jobs/source",
                "project": {"source": "jobs/seed/seedProject.ssa", "name": "CaviCopy",
                            "assets": ["Cavi"]},
                "steps": [
                    {"action": "set_metadata", "asset": "Cavi", "author": "Cavi",
                     "category": "rock", "tags": ["game"], "physical_size": [1, 2, 3]},
                    {"action": "set_channels", "asset": "Cavi", "layer": "Tiling",
                     "applies_to": [channel_label]},
                    {"action": "export", "asset": "Cavi", "target": "unity",
                     "directory": "unity"},
                ],
            }
            path = workspace / "jobs/source.json"
            path.write_text(json.dumps(data))
            manifest = batch.run_job_file("jobs/source.json", workspace, timeout_seconds=600)
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["source"], "jobs/seed/seedProject.ssa")
            self.assertEqual(manifest["project"], "jobs/source/CaviCopy.ssa")
            self.assertEqual(source.read_bytes(), digest)
            inspected = asyncio.run(launch.invoke(
                "inspect_asset", {"project": manifest["project"], "asset": "Cavi"}, 300
            ))
            self.assertEqual(inspected["asset"]["metadata"]["category"], "rock")
            self.assertEqual(inspected["asset"]["metadata"]["tags"], ["game"])
            tiling = next(layer for layer in inspected["layers"] if layer["name"] == "Tiling")
            self.assertIn(channel, tiling["applied_channels"])
            activated = asyncio.run(launch.invoke("activate_channels", {
                "project": manifest["project"], "asset": "Cavi", "channels": [channel],
            }, 300))
            self.assertTrue(activated["activated"])

    def test_build_check(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            workspace = self._workspace(folder)
            data = {
                "schema": 1,
                "directory": "jobs/build",
                "project": {"name": "BuildProject",
                            "asset": {"name": "Cavi", "model": "openpbr",
                                      "preset": "base_material"}},
                "steps": [
                    {"action": "import_images", "asset": "Cavi",
                     "images": ["maps/basecolor.png"], "position": 0,
                     "import_option": "use_as_bitmap"},
                    {"action": "insert_filter", "asset": "Cavi", "filter": "Tiling",
                     "position": 1},
                    {"action": "set_parameters", "asset": "Cavi", "layer": "Tiling",
                     "parameters": {"Threshold": 0.1}},
                    {"action": "export", "asset": "Cavi", "target": "unreal",
                     "resolution": 256},
                ],
            }
            (workspace / "jobs/build.json").write_text(json.dumps(data))
            plan = batch.check_build("jobs/build.json", workspace, timeout_seconds=600)
            self.assertEqual(plan["build_check"]["status"], "ok")
            self.assertEqual(plan["build_check"]["exports_skipped"], 1)
            self.assertFalse((workspace / "jobs/build").exists())
            self.assertFalse(list(workspace.glob(".cavi-build-*")))
            bad = json.loads(json.dumps(data))
            bad["directory"] = "jobs/build-bad"
            bad["steps"] = [
                {"action": "set_parameters", "asset": "Cavi", "layer": "Missing",
                 "parameters": {"Threshold": 0.1}},
                {"action": "export", "asset": "Cavi", "target": "unreal",
                 "resolution": 256},
            ]
            (workspace / "jobs/build-bad.json").write_text(json.dumps(bad))
            with self.assertRaises(job.JobValidationError):
                batch.check_build("jobs/build-bad.json", workspace,
                                  timeout_seconds=600)
            self.assertFalse((workspace / "jobs/build-bad").exists())
            self.assertFalse(list(workspace.glob(".cavi-build-*")))

    def test_project_preflight(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            workspace = self._workspace(folder)
            seed_data = {
                "schema": 1,
                "directory": "jobs/seed",
                "project": {"name": "seedProject",
                            "asset": {"name": "Cavi", "model": "openpbr",
                                      "preset": "base_material"}},
                "steps": [
                    {"action": "import_textures", "asset": "Cavi",
                     "images": {"basecolor": "maps/basecolor.png",
                                "normal": "maps/basecolor.png"}, "position": 0},
                    {"action": "insert_filter", "asset": "Cavi", "filter": "Tiling",
                     "position": 1},
                    {"action": "export", "asset": "Cavi", "target": "unreal",
                     "resolution": 256, "outputs": ["sbsar"]},
                ],
            }
            (workspace / "jobs/seed.json").write_text(json.dumps(seed_data))
            batch.run_job_file("jobs/seed.json", workspace, timeout_seconds=600)
            source = workspace / "jobs/seed/seedProject.ssa"
            digest = source.read_bytes()
            seeded = asyncio.run(launch.invoke(
                "inspect_asset", {"project": "jobs/seed/seedProject.ssa", "asset": "Cavi"},
                300,
            ))
            tiling = next(layer for layer in seeded["layers"] if layer["name"] == "Tiling")
            channel_label = tiling["usage_labels"][tiling["applied_channels"][0]]
            data = {
                "schema": 1,
                "directory": "jobs/preflight",
                "project": {"source": "jobs/seed/seedProject.ssa", "name": "CaviCopy",
                            "assets": ["Cavi"]},
                "steps": [
                    {"action": "set_metadata", "asset": "Cavi", "author": "Cavi"},
                    {"action": "set_parameters", "asset": "Cavi", "layer": "Tiling",
                     "parameters": {"Threshold": 0.1}},
                    {"action": "apply_preset", "asset": "Cavi", "layer": "Tiling",
                     "preset": "Tiling"},
                    {"action": "set_channels", "asset": "Cavi", "layer": "Tiling",
                     "applies_to": [channel_label]},
                    {"action": "export", "asset": "Cavi", "target": "unity",
                     "directory": "unity"},
                ],
            }
            (workspace / "jobs/preflight.json").write_text(json.dumps(data))
            plan = batch.validate_with_project("jobs/preflight.json", workspace,
                                               timeout_seconds=600)
            self.assertEqual(plan["project_check"], "ok")
            self.assertFalse((workspace / "jobs/preflight").exists())
            self.assertEqual(source.read_bytes(), digest)
            self.assertFalse(list(workspace.glob(".cavi-preflight-*")))
            bad = json.loads(json.dumps(data))
            bad["directory"] = "jobs/preflight-bad"
            bad["steps"] = [
                {"action": "set_parameters", "asset": "Cavi", "layer": "Missing",
                 "parameters": {"Threshold": 0.1}},
                {"action": "export", "asset": "Cavi", "target": "unity",
                 "directory": "unity"},
            ]
            (workspace / "jobs/preflight-bad.json").write_text(json.dumps(bad))
            with self.assertRaises(job.JobValidationError):
                batch.validate_with_project("jobs/preflight-bad.json", workspace,
                                            timeout_seconds=600)
            self.assertEqual(source.read_bytes(), digest)
            self.assertFalse(list(workspace.glob(".cavi-preflight-*")))

    def test_blend_parameters(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            probe = asyncio.run(launch.invoke("probe", {}, 300))
            if "Asphalt" not in probe["materials"]:
                self.skipTest("Asphalt material is not installed")
            created = asyncio.run(launch.invoke("create_project", {
                "directory": "projects", "name": "Blend",
            }, 300))
            asyncio.run(launch.invoke("insert_material", {
                "project": created["project"], "asset": "Blend", "material": "Asphalt",
                "position": 0, "blend_mode": "blend_opacity",
            }, 300))
            inserted = asyncio.run(launch.invoke("insert_material", {
                "project": created["project"], "asset": "Blend", "material": "Concrete Cast",
                "position": 0, "blend_mode": "blend_opacity",
            }, 300))
            parameters = inserted["layer"]["blend_parameters"]
            choices = [
                item for item in parameters
                if item["type"] in ("float", "integer")
                and not str(item["identifier"]).startswith("$")
                and (item["min"] is None or item["max"] is None
                     or item["max"] > item["min"])
            ]
            if not choices:
                self.skipTest("material layer has no settable scalar blend parameter")
            parameter = choices[0]
            if parameter["type"] == "float":
                value = 0.5
            else:
                low = int(parameter["min"] or 0)
                high = int(parameter["max"] or low + 1)
                value = min(high, max(low, low + 1))
            changed = asyncio.run(launch.invoke("set_blend_parameters", {
                "project": created["project"], "asset": "Blend",
                "layer": inserted["layer"]["name"],
                "parameters": {parameter["identifier"]: value},
            }, 300))
            self.assertIn(parameter["identifier"], changed["parameters"])
            self.assertAlmostEqual(
                float(changed["parameters"][parameter["identifier"]]), float(value)
            )

    def test_batch_job(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            (workspace / "maps").mkdir()
            (workspace / "jobs").mkdir()
            Image.new("RGB", (64, 64), (200, 100, 50)).save(workspace / "maps/basecolor.png")
            data = {
                "schema": 1,
                "directory": "jobs/unreal",
                "project": {"name": "CaviProject",
                            "asset": {"name": "Cavi", "model": "openpbr",
                                      "preset": "base_material"}},
                "steps": [
                    {"action": "import_images", "asset": "Cavi",
                     "images": ["maps/basecolor.png"], "position": 0,
                     "import_option": "use_as_bitmap"},
                    {"action": "export", "asset": "Cavi", "target": "unreal",
                     "resolution": 64},
                ],
            }
            job_path = workspace / "jobs/unreal.json"
            job_path.write_text(json.dumps(data))
            manifest = batch.run_job_file("jobs/unreal.json", workspace,
                                          timeout_seconds=600)
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["project"], "jobs/unreal/CaviProject.ssa")
            paths = [item["path"] for item in manifest["outputs"]]
            self.assertTrue(any(item.endswith(".sbsar") for item in paths))
            self.assertTrue(any(item.endswith(".png") for item in paths))
            self.assertTrue((workspace / "jobs/unreal/manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
