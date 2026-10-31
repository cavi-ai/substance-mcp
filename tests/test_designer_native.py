import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from cavi_substance_mcp.designer import runner

INSTALL = runner.installation()
NATIVE = all(INSTALL.binary(name) for name in ("sbscooker", "sbsrender", "substance3d_baker"))
if NATIVE:
    NATIVE = "fake" not in (asyncio.run(runner.version(INSTALL.binary("sbsrender"))) or "fake")
BLENDER = os.environ.get("BLENDER") or shutil.which("blender") or "/opt/homebrew/bin/blender"
HDRP = {"maps": [
    {"name": "T_Rock_MaskMap", "format": "png", "bit_depth": 8,
     "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}},
    {"name": "T_Rock_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}},
    {"name": "T_Rock_Normal", "channels": {"RGB": "normal"}}], "defaults": {"opacity": 1}}
MESHES = """
import bpy, sys
out = sys.argv[sys.argv.index('--') + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
def unwrap(obj, path):
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
    bpy.ops.object.select_all(action='DESELECT')
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.context.active_object.name = 'cube_low'
unwrap(bpy.context.active_object, out + '/low.fbx')
bpy.ops.mesh.primitive_cube_add(size=2)
high = bpy.context.active_object
high.name = 'cube_high'
high.modifiers.new('bevel', 'BEVEL').width = 0.15
high.modifiers['bevel'].segments = 4
high.modifiers.new('subd', 'SUBSURF').levels = 2
bpy.ops.object.convert(target='MESH')
unwrap(high, out + '/high.fbx')
"""


def pixels(path):
    return np.asarray(Image.open(path)).astype(np.int32)


@unittest.skipUnless(NATIVE, "Substance 3D Designer CLIs not installed")
class DesignerNativeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.workspace = Path(self.folder.name) / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.folder.cleanup()

    async def session_calls(self, calls):
        env = dict(os.environ, CAVI_SUBSTANCE_WORKSPACE=str(self.workspace))
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "cavi_substance_mcp.designer.server"], env=env)
        results = []
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer, read_timeout_seconds=900) as session,
        ):
            await session.initialize()
            for tool, arguments in calls:
                started = time.monotonic()
                reply = await session.call_tool(tool, arguments)
                text = "\n".join(getattr(item, "text", "") for item in reply.content)
                self.assertFalse(reply.is_error, text)
                print(f"\n[native] {tool} {arguments.get('action', '')}: "
                      f"{time.monotonic() - started:.2f} s", file=sys.stderr)
                results.append(reply.structured_content)
        return results

    def test_cook_render_pack(self):
        status, cooked, rendered = asyncio.run(self.session_calls([
            ("designer_status", {}),
            ("material", {"action": "cook", "builtin": "templates/01_02_pbr_metallic_roughness",
                          "directory": "cooked"}),
            ("material", {"action": "render", "path": "cooked/01_02_pbr_metallic_roughness.sbsar",
                          "graph": "pkg://metallic_roughness", "directory": "rendered",
                          "resolution": 1024, "pack": HDRP}),
        ]))
        self.assertTrue(status["available"])
        self.assertIn("pkg://metallic_roughness", [g["url"] for g in cooked["graphs"]])
        maps = {key: pixels(self.workspace / path) for key, path in rendered["maps"].items()}
        packed = {item["name"]: pixels(self.workspace / item["path"])
                  for item in rendered["packed"]}
        for image in packed.values():
            self.assertEqual(image.shape[:2], (1024, 1024))
        mask = packed["T_Rock_MaskMap"]
        self.assertEqual(mask.shape, (1024, 1024, 4))
        gray = {key: value if value.ndim == 2 else value[:, :, 0] for key, value in maps.items()}
        np.testing.assert_array_equal(mask[:, :, 0], gray["metallic"])
        np.testing.assert_array_equal(mask[:, :, 1], gray["ambientOcclusion"])
        self.assertEqual(int(mask[:, :, 2].max()), 0)
        np.testing.assert_array_equal(mask[:, :, 3], 255 - gray["roughness"])
        base = packed["T_Rock_BaseColor"]
        np.testing.assert_array_equal(base[:, :, :3], maps["baseColor"][:, :, :3])
        self.assertEqual(int(base[:, :, 3].min()), 255)
        np.testing.assert_array_equal(packed["T_Rock_Normal"], maps["normal"][:, :, :3])

    @unittest.skipUnless(Path(BLENDER).is_file(), "Blender not installed")
    def test_bake_normal_and_ao(self):
        meshes = self.workspace / "meshes"
        meshes.mkdir()
        subprocess.run([BLENDER, "--background", "--factory-startup", "--python-expr", MESHES,
                        "--", str(meshes)], check=True, capture_output=True, timeout=120)
        (baked,) = asyncio.run(self.session_calls([
            ("bake", {"low": "meshes/low.fbx", "high": "meshes/high.fbx",
                      "maps": ["normal", "ambient_occlusion"], "directory": "baked",
                      "resolution": 1024, "max_frontal": 0.1, "max_rear": 0.1}),
        ]))
        print(f"[native] bake seconds: {baked['seconds']}", file=sys.stderr)
        normal = pixels(self.workspace / baked["maps"]["normal"])
        ao = np.asarray(Image.open(self.workspace / baked["maps"]["ambient_occlusion"]))
        self.assertEqual(normal.shape[:2], (1024, 1024))
        self.assertEqual(ao.shape[:2], (1024, 1024))
        self.assertGreater(normal[:, :, 2].mean(), 200)
        self.assertGreater(normal[:, :, 0].std(), 5)
        self.assertGreater(ao.mean(), 0)


if __name__ == "__main__":
    unittest.main()
