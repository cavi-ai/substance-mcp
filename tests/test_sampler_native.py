import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from cavi_substance_mcp.sampler import batch, operations, runner


class NativeSamplerTests(unittest.TestCase):
    def setUp(self):
        if runner.installation().executable is None:
            self.skipTest("Substance 3D Sampler is not installed")

    def test_status(self):
        status = operations.status()
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
            created = asyncio.run(operations.invoke(
                "create_project", {"directory": "projects", "name": "Cavi"}, 300
            ))
            self.assertEqual(created["project"], "projects/Cavi.ssa")
            inspected = asyncio.run(operations.invoke(
                "inspect_project", {"project": "projects/Cavi.ssa"}, 300
            ))
            self.assertEqual(inspected["project"], "projects/Cavi.ssa")
            copied = asyncio.run(operations.invoke("save_as", {
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
            created = asyncio.run(operations.invoke(
                "create_project", {"directory": "projects", "name": "Authoring"}, 300
            ))
            inserted = asyncio.run(operations.invoke("insert_filter", {
                "project": created["project"], "asset": "Authoring",
                "filter": "Tiling", "position": 0,
            }, 300))
            labels = [item["label"] for item in inserted["layer"]["parameters"]]
            self.assertIn("Threshold", labels)
            changed = asyncio.run(operations.invoke("set_parameters", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "parameters": {"Threshold": 0.1},
            }, 300))
            self.assertAlmostEqual(changed["parameters"]["edge_threshold"], 0.1, places=5)
            applied = asyncio.run(operations.invoke("apply_preset", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "preset": "Tiling",
            }, 300))
            self.assertEqual(applied["preset"], "Tiling")
            removed = asyncio.run(operations.invoke("remove_layer", {
                "project": created["project"], "asset": "Authoring", "layer": "Tiling",
                "confirm": True,
            }, 300))
            self.assertEqual(removed["removed"], "Tiling")

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
