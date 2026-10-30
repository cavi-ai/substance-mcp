import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from cavi_substance_mcp.designer import sampler, server
from cavi_substance_mcp.sampler import runner

HDRP = {"maps": [
    {"name": "T_Brick_MaskMap", "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0",
                                             "A": "1-roughness"}},
    {"name": "T_Brick_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}},
    {"name": "T_Brick_Normal", "channels": {"RGB": "normal"}}],
    "defaults": {"opacity": 1, "metallic": 0}}


def brick(path: Path, size=1024):
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:size, 0:size]
    row = y // 64
    shifted = x + (row % 2) * 64
    column = shifted // 128
    mortar = ((y % 64) < 5) | ((shifted % 128) < 5)
    tint = rng.uniform(0.8, 1.2, (row.max() + 1, column.max() + 1))[row, column]
    color = np.stack([150 * tint, 72 * tint, 52 * tint], -1)
    color = np.where(mortar[..., None], [176, 170, 160], color)
    color = color + rng.normal(0, 14, (size, size, 1))
    color = color * (0.7 + 0.3 * x / size)[..., None]
    Image.fromarray(np.clip(color, 0, 255).astype(np.uint8)).save(path)


@unittest.skipUnless(runner.installation().executable, "Substance 3D Sampler is not installed")
class NativeSamplerJobTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if sampler.running():
            self.skipTest("Sampler is already running; quit it first")

    def test_photo_to_tiled_hdrp(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            brick(Path(folder) / "brick.png")
            reply = asyncio.run(server.mcp.call_tool("sampler", {
                "action": "image_to_material", "images": ["brick.png"], "directory": "out",
                "tiling": True, "resolution": 1024, "pack": HDRP, "save_project": True}))
            data = reply[1] if isinstance(reply, tuple) else json.loads(reply[0].text)
            dims = {}
            for key, name in {**data["maps"], **{p["name"]: p["path"]
                                                  for p in data["packed"]}}.items():
                with Image.open(Path(folder) / name) as image:
                    dims[key] = image.size
            print(f"\nsampler native: {data['seconds']} s layers={data['layers']} dims={dims}")
            for key in ("baseColor", "normal", "roughness", "height", "ambientOcclusion"):
                self.assertEqual(dims[key], (1024, 1024))
            self.assertEqual(dims["T_Brick_MaskMap"], (1024, 1024))
            self.assertIn("Make It Tile", data["layers"])
            self.assertTrue((Path(folder) / data["project"]).is_file())


if __name__ == "__main__":
    unittest.main()
