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

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from designer_sampler_fake import install

from cavi_substance_mcp.designer import packing, sampler, server

HDRP = {"maps": [
    {"name": "T_MaskMap", "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0",
                                       "A": "1-roughness"}},
    {"name": "T_Normal", "channels": {"RGB": "normal"}}]}
BASE = {"mode": "ai", "tiling": True, "delight": False, "resolution": 256, "format": "png",
        "pack": None, "save_project": False}


class FakeSampler(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.app = install(root / "app")
        self.exe = self.app / "Contents/MacOS/Adobe Substance 3D Sampler"
        self.ws = root / "ws"
        (self.ws / "photos").mkdir(parents=True)
        for name in ("photo", "a", "b", "hang", "fail"):
            Image.new("RGB", (32, 32), (100, 80, 60)).save(self.ws / f"photos/{name}.png")
        (self.ws / "scene.ssa").write_text(json.dumps({"assets": ["rock"]}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CAVI_SAMPLER")}
        env.update({"CAVI_SAMPLER_APP": str(self.app), "CAVI_SUBSTANCE_WORKSPACE": str(self.ws)})
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_job(self, action="image_to_material", timeout=60, **values):
        return asyncio.run(sampler.run(action, {**BASE, **values}, timeout))


class SamplerJobTests(FakeSampler):
    def test_image_to_material_pack_and_save(self):
        out = self.run_job(images=["photos/photo.png"], directory="out", delight=True,
                           pack=packing.PackSpec.model_validate(HDRP), save_project=True)
        self.assertEqual(out["maps"]["baseColor"], "out/baseColor.png")
        self.assertEqual(out["maps"]["ambientOcclusion"], "out/ambientOcclusion.png")
        self.assertEqual(out["layers"][-2:], ["Delighter (AI Powered)", "Make It Tile"])
        self.assertEqual(out["project"], "out/photo.ssa")
        self.assertEqual([p["path"] for p in out["packed"]],
                         ["out/packed/T_MaskMap.png", "out/packed/T_Normal.png"])
        with Image.open(self.ws / "out/packed/T_MaskMap.png") as image:
            self.assertEqual((image.size, image.mode), ((256, 256), "RGBA"))
        self.assertFalse(list(self.ws.glob("out/sampler_*")))

    def test_export_project(self):
        out = self.run_job("export_project", project="scene.ssa", directory="exp", format="tif")
        self.assertEqual(out["asset"], "rock")
        self.assertEqual(out["maps"]["normal"], "exp/normal.tif")

    def test_refuses_when_running(self):
        idle = subprocess.Popen([str(self.exe), "--idle"])
        self.addCleanup(idle.wait)
        self.addCleanup(idle.kill)
        time.sleep(0.5)
        self.assertTrue(sampler.running())
        with self.assertRaisesRegex(RuntimeError, "already running"):
            self.run_job(images=["photos/photo.png"], directory="out")
        self.assertFalse((self.ws / "out").exists())

    def test_licensing_client_is_not_running(self):
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                                   f"--appPath={self.exe}"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        time.sleep(0.5)
        self.assertFalse(sampler.running())

    def test_timeout_kills_and_cleans(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "killed"):
            self.run_job(images=["photos/hang.png"], directory="out", timeout=2)
        self.assertLess(time.monotonic() - started, 30)
        self.assertFalse((self.ws / "out").exists())
        self.assertFalse(sampler.running())

    def test_worker_failure_cleans(self):
        with self.assertRaisesRegex(RuntimeError, "fake import failure"):
            self.run_job(images=["photos/fail.png"], directory="out")
        self.assertFalse((self.ws / "out").exists())

    def test_validation(self):
        cases = [
            ({"images": ["photos/a.png", "photos/b.png"]}, "takes 1-1"),
            ({"images": ["photos/a.png"], "mode": "multiangle"}, "takes 2-8"),
            ({"images": ["photos/a.png", "photos/a.png"], "mode": "multiangle"}, "unique"),
            ({"images": ["photos/a.png"], "format": "exr",
              "pack": packing.PackSpec.model_validate(HDRP)}, "png or tif"),
            ({"images": ["../x.png"]}, "inside the workspace"),
        ]
        for values, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.run_job(directory="out", **values)
        (self.ws / "out").mkdir()
        with self.assertRaises(FileExistsError):
            self.run_job(images=["photos/a.png"], directory="out")

    def test_status(self):
        status = asyncio.run(server.designer_status())["sampler"]
        self.assertEqual(status, {"path": str(self.exe), "version": "0.0.0-fake",
                                  "running": False})


class ServerTests(FakeSampler):
    def call(self, **arguments):
        return asyncio.run(server.mcp.call_tool("sampler", arguments))

    def test_action_params(self):
        for arguments, message in (
            ({"action": "image_to_material", "directory": "x"}, "images is required"),
            ({"action": "export_project", "directory": "x"}, "project is required"),
            ({"action": "export_project", "directory": "x", "project": "scene.ssa",
              "tiling": False}, "Not valid for action=export_project: tiling"),
            ({"action": "image_to_material", "directory": "x", "images": ["photos/a.png"],
              "asset": "rock"}, "Not valid for action=image_to_material: asset"),
        ):
            with self.subTest(message), self.assertRaisesRegex(Exception, message):
                self.call(**arguments)


class NamingTests(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(sampler.usage("sampler_BaseColor"), "baseColor")
        self.assertEqual(sampler.usage("sampler_AmbientOcclusion"), "ambientOcclusion")
        self.assertEqual(sampler.usage("sampler_Anisotropy"), "anisotropy")
        self.assertIsNone(sampler.usage("other_BaseColor"))

    def test_collect_rejects_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "sampler_Normal.png").write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "empty"):
                sampler.collect(Path(folder), "png")


if __name__ == "__main__":
    unittest.main()
