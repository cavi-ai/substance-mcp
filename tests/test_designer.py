import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from home import isolate_home
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from designer_fake_cli import ENV, install, sbsar
from env import DESIGNER, patched_environment
from launcher import write_launcher

from cavi_substance_mcp import packing
from cavi_substance_mcp.core import discovery, proc
from cavi_substance_mcp.designer import operations, runner

HDRP = {"maps": [
    {"name": "T_Rock_MaskMap", "format": "png", "bit_depth": 8,
     "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}},
    {"name": "T_Rock_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}},
    {"name": "T_Rock_Normal", "channels": {"RGB": "normal"}}]}
OPTIONS = {"resolution": 32, "normal_format": "OpenGL", "use_cage": False, "max_frontal": None,
           "max_rear": None, "udim": None, "match_by_name": False, "suffix_low": "_low",
           "suffix_high": "_high", "format": "png"}


def spec(data):
    return packing.PackSpec.model_validate(data)


class ParsingTests(unittest.TestCase):
    def test_info_blocks(self):
        graphs = runner.parse_info(
            "GRAPH-URL pkg://a\n  INPUT $outputsize INTEGER2\n  OUTPUT basecolor baseColor\n"
            "  OUTPUT Brick \n\nGRAPH-URL pkg://b\n  OUTPUT x y\n")
        self.assertEqual([g["url"] for g in graphs], ["pkg://a", "pkg://b"])
        self.assertEqual(graphs[0]["inputs"], [{"id": "$outputsize", "type": "INTEGER2"}])
        self.assertEqual(graphs[0]["outputs"][1], {"id": "Brick", "usage": ""})

    def test_report_after_engine_logs(self):
        text = ("[2026-10-30 17:58:49.941] [info] [sal.cpp:258] 1 adapter\n[\n"
                '    {"identifier": "g", "outputs": [{"value": "a [b]"}]}\n]\n'
                "[2026-10-30 17:58:50.000] [info] done\n")
        self.assertEqual(runner.parse_report(text)[0]["identifier"], "g")

    def test_report_missing(self):
        with self.assertRaises(RuntimeError):
            runner.parse_report("[2026] [info] no json\n")


class InstallationTests(unittest.TestCase):
    def test_bin_dir_wins(self):
        with patch.dict(os.environ, {"CAVI_DESIGNER_BIN_DIR": "/x", "CAVI_DESIGNER_APP": "/y"}):
            self.assertEqual(runner.installation().bin_dir, Path("/x"))

    def test_platform_defaults(self):
        with patched_environment(DESIGNER):
            # shutil.which cannot run as another platform, and nothing is installed anyway.
            with patch.object(discovery.sys, "platform", "win32"), \
                    patch.object(discovery.shutil, "which", return_value=None):
                found = runner.installation()
                self.assertEqual(found.bin_dir, Path(
                    r"C:\Program Files\Adobe\Adobe Substance 3D Designer"))
            with patch.object(discovery.sys, "platform", "linux"), \
                    patch.object(discovery.shutil, "which", return_value=None):
                self.assertEqual(str(runner.installation().bin_dir),
                                 "/opt/Adobe/Adobe_Substance_3D_Designer")

    def test_path_is_searched_after_the_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            tools = write_launcher(Path(folder) / "bin", "sbsrender", "print(1)\n").parent
            with patched_environment(DESIGNER, {"PATH": str(tools)}), patch.object(
                    discovery, "default_location", return_value=Path(folder) / "nowhere"):
                found = runner.installation()
            self.assertEqual((found.source, found.bin_dir), ("PATH", tools.resolve()))
            self.assertTrue(found.binary("sbsrender"))

    def test_an_existing_default_beats_path(self):
        with tempfile.TemporaryDirectory() as folder:
            default = write_launcher(Path(folder) / "default", "sbsrender", "print(1)\n").parent
            other = write_launcher(Path(folder) / "bin", "sbsrender", "print(1)\n").parent
            with patched_environment(DESIGNER, {"PATH": str(other)}), patch.object(
                    discovery, "default_location", return_value=default):
                found = runner.installation()
            self.assertEqual((found.source, found.bin_dir), ("default", default))

    def test_resources_sit_beside_the_fake_install(self):
        with tempfile.TemporaryDirectory() as folder:
            bin_dir = install(Path(folder))
            with patch.dict(os.environ, {ENV: str(bin_dir)}):
                found = runner.installation()
                self.assertTrue(found.binary("sbsrender"))
                self.assertTrue(found.resources.samefile((bin_dir.parent / "resources").resolve()))
                self.assertTrue(operations.builtin_file("packages/brick_1").is_file())
                for bad in ("packages/../templates/x", "other/brick_1", "packages/a/b"):
                    with self.assertRaises(ValueError):
                        operations.builtin_file(bad)

    def test_a_macos_bundle_keeps_its_tools_and_resources_where_designer_puts_them(self):
        # The layout of a real macOS install, built by hand: nothing here is executed.
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "Adobe Substance 3D Designer.app"
            macos = write_launcher(app / "Contents/MacOS", "sbsrender", "print(1)\n").parent
            (app / "Contents/Resources/packages").mkdir(parents=True)
            with patched_environment(DESIGNER, {"CAVI_DESIGNER_APP": str(app)}):
                found = runner.installation()
            self.assertEqual((found.source, found.bin_dir), ("CAVI_DESIGNER_APP", macos))
            self.assertTrue(found.binary("sbsrender"))
            self.assertEqual(found.resources, (app / "Contents/Resources").resolve())


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.sources = {}
        for usage, value in {"metallic": 255, "ambientOcclusion": 200, "roughness": 64,
                             "baseColor": (204, 102, 51), "normal": (128, 128, 255)}.items():
            path = self.root / f"{usage}.png"
            Image.new("RGB" if isinstance(value, tuple) else "L", (8, 4), value).save(path)
            self.sources[usage] = path

    def tearDown(self):
        self.folder.cleanup()

    def run_pack(self, data, sources=None):
        out = self.root / "out"
        out.mkdir(exist_ok=True)
        return packing.pack(spec(data), sources or self.sources, out)

    def test_hdrp_values(self):
        result = self.run_pack(HDRP | {"defaults": {"opacity": 1}})
        mask = np.asarray(Image.open(self.root / "out/T_Rock_MaskMap.png"))
        self.assertEqual(mask.shape, (4, 8, 4))
        self.assertEqual(mask[0, 0].tolist(), [255, 200, 0, 191])
        base = Image.open(self.root / "out/T_Rock_BaseColor.png")
        self.assertEqual((base.mode, base.getpixel((0, 0))), ("RGBA", (204, 102, 51, 255)))
        normal = Image.open(self.root / "out/T_Rock_Normal.png")
        self.assertEqual((normal.mode, normal.getpixel((1, 1))), ("RGB", (128, 128, 255)))
        self.assertEqual(result["maps"][0]["width"], 8)

    def test_gray_from_rgb_uses_red_and_rgb_from_gray_repeats(self):
        self.run_pack({"maps": [{"name": "M", "channels": {
            "R": "baseColor", "G": "1-baseColor", "B": "0.5"}},
            {"name": "G", "channels": {"RGB": "1-roughness"}}]})
        self.assertEqual(Image.open(self.root / "out/M.png").getpixel((0, 0)), (204, 51, 128))
        self.assertEqual(Image.open(self.root / "out/G.png").getpixel((0, 0)), (191, 191, 191))

    def test_png16(self):
        path = self.root / "ao16.png"
        Image.fromarray(np.full((4, 8), 40000, np.uint16)).save(path)
        self.run_pack({"maps": [{"name": "M16", "bit_depth": 16, "channels": {
            "R": "ao", "G": "1-ao", "B": "1", "A": "0"}}]}, {"ao": path})
        raw = (self.root / "out/M16.png").read_bytes()
        self.assertEqual(raw[24:26], bytes([16, 6]))
        import zlib
        idat = raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8]
        row = zlib.decompress(idat)[: 1 + 8 * 8]
        pixel = np.frombuffer(row[1:9], ">u2").tolist()
        self.assertEqual(pixel, [40000, 65535 - 40000, 65535, 0])

    def test_errors(self):
        cases = [
            ({"maps": [{"name": "M", "channels": {"RGB": "baseColor", "A": "opacity"}}]},
             "no default"),
            ({"maps": [{"name": "M", "channels": {"RGB": "baseColor", "G": "normal"}}]},
             "cannot be combined"),
            ({"maps": [{"name": "M", "channels": {"R": "metallic", "A": "1"}}]}, "all of R, G, B"),
            ({"maps": [{"name": "M", "channels": {"RGB": "1"}}]}, "at least one image"),
            ({"maps": [{"name": "M", "channels": {"RGB": "normal"}},
                       {"name": "m", "channels": {"RGB": "normal"}}]}, "Duplicate"),
        ]
        for data, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.run_pack(data)
        small = self.root / "small.png"
        Image.new("L", (2, 2)).save(small)
        with self.assertRaisesRegex(ValueError, "resolutions differ"):
            self.run_pack({"maps": [{"name": "M", "channels": {
                "R": "metallic", "G": "roughness", "B": "0"}}]},
                {"metallic": self.sources["metallic"], "roughness": small})

    def test_schema_rejects(self):
        for data in ({"maps": []}, {"maps": [{"name": "a b", "channels": {"RGB": "x"}}]},
                     {"maps": [{"name": "a", "channels": {"RGB": "1.5"}}]},
                     {"maps": [{"name": "a", "channels": {"L": "x"}}]},
                     {"maps": [{"name": "a", "channels": {"RGB": "x"}}], "defaults": {"x": 2}},
                     {"maps": [{"name": f"m{i}", "channels": {"RGB": "x"}} for i in range(17)]}):
            with self.subTest(data), self.assertRaises(ValueError):
                spec(data)


class FakeCliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "meshes").mkdir(parents=True)
        self.log = root / "log.jsonl"
        app = install(root)
        self.env = patched_environment(DESIGNER, {
            ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace),
            "CAVI_FAKE_DESIGNER_LOG": str(self.log)})
        self.env.start()
        (self.workspace / "m.sbsar").write_text(sbsar(["pkg://g"]))
        for name in ("low", "high", "cage"):
            (self.workspace / f"meshes/{name}.fbx").write_text(name)

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def calls(self):
        # Receipts record each tool's version, so `--version` probes are not part of the work.
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [call for call in calls if call[1:] != ["--version"]]

    def test_status(self):
        status = asyncio.run(operations.status())
        self.assertTrue(status["available"])
        self.assertEqual(status["templates"], ["01_02_pbr_metallic_roughness"])
        self.assertEqual(status["binaries"]["sbscooker"]["version"], "0.0.0-fake")

    def test_a_truncated_info_listing_is_refused(self):
        with patch.object(proc, "STDOUT_LIMIT", 20), \
                self.assertRaisesRegex(RuntimeError, "truncated"):
            asyncio.run(operations.info("m.sbsar", 30))

    def test_cook_flags(self):
        result = asyncio.run(operations.cook(
            None, "packages/brick_1", "c", True, False, 30))
        self.assertEqual(result["sbsar"], "c/brick_1.sbsar")
        cook = self.calls()[0]
        self.assertEqual(cook[0], "sbscooker")
        self.assertIn("--expose-random-seed", cook)
        self.assertEqual(cook[cook.index("--expose-random-seed") + 1], "0")

    def test_render_arguments_and_pack(self):
        result = asyncio.run(operations.render(
            "m.sbsar", None, "r", {"roughness_level": 0.25, "tint": [1, 0, 0.5]}, 64, 9, "Wet",
            None, "png", "8", spec(HDRP | {"defaults": {"opacity": 0.5}}), 30))
        command = self.calls()[-1]
        self.assertEqual(command[:2], ["sbsrender", "render"])
        values = [command[i + 1] for i, item in enumerate(command) if item == "--set-value"]
        self.assertEqual(values, ["roughness_level@0.25", "tint@1.0,0.0,0.5",
                                  "$outputsize@6,6", "$randomseed@9"])
        self.assertEqual(command[command.index("--input-graph") + 1], "pkg://g")
        self.assertEqual(command[command.index("--use-preset") + 1], "Wet")
        self.assertEqual(result["maps"]["baseColor"], "r/basecolor.png")
        image = Image.open(self.workspace / "r/packed/T_Rock_BaseColor.png")
        self.assertEqual((image.size, image.getpixel((0, 0))), ((64, 64), (204, 102, 51, 128)))

    def test_render_failure_cleans_up(self):
        data = json.loads(sbsar(["pkg://g"]))
        (self.workspace / "bad.sbsar").write_text(json.dumps({**data, "mode": "render_fail"}))
        with self.assertRaisesRegex(RuntimeError, "exit 3"):
            asyncio.run(operations.render("bad.sbsar", None, "r", None, None, None, None,
                                          None, "png", "8", None, 30))
        self.assertFalse((self.workspace / "r").exists())

    def test_bake_arguments(self):
        options = OPTIONS | {"max_frontal": 0.2, "max_rear": 0.1, "udim": 1002}
        result = asyncio.run(operations.bake(
            "meshes/low.fbx", "meshes/high.fbx", None,
            ["normal", "world_normal", "ambient_occlusion"], "b", options, 30))
        normal, world, ao = self.calls()
        self.assertEqual(normal[1], "Normal.Raytraced")
        self.assertEqual(normal[normal.index("--output_texture_orientation") + 1], "opengl")
        self.assertEqual(normal[normal.index("--projection.max_height") + 1], "0.2")
        self.assertEqual(normal[normal.index("--udim") + 1], "1002")
        self.assertNotIn("--high_scene_paths", world)
        self.assertNotIn("--output_texture_orientation", ao)
        self.assertEqual(set(result["maps"]), {"normal", "world_normal", "ambient_occlusion"})

    def test_bake_low_only_cage_and_directx(self):
        options = OPTIONS | {"use_cage": True, "normal_format": "DirectX", "match_by_name": True}
        asyncio.run(operations.bake("meshes/low.fbx", None, "meshes/cage.fbx",
                                    ["bent_normal"], "b", options, 30))
        command = self.calls()[0]
        self.assertIn("--use_lowdef_as_highdef", command)
        self.assertEqual(command[command.index("--cage_scene_path") + 1],
                         str((self.workspace / "meshes/cage.fbx").resolve()))
        self.assertEqual(command[command.index("--output_texture_orientation") + 1], "directx")
        self.assertEqual(command[command.index("--name_suffix_low") + 1], "_low")

    def test_bake_missing_output_and_timeout(self):
        (self.workspace / "meshes/silent.fbx").write_text("fake-bake-silent")
        (self.workspace / "meshes/hang.fbx").write_text("fake-bake-hang")
        with self.assertRaisesRegex(RuntimeError, "wrote no normal.png"):
            asyncio.run(operations.bake("meshes/silent.fbx", None, None, ["normal"], "s",
                                        OPTIONS, 30))
        with self.assertRaisesRegex(TimeoutError, "killed"):
            asyncio.run(operations.bake("meshes/hang.fbx", None, None, ["normal"], "h",
                                        OPTIONS, 1))
        self.assertFalse((self.workspace / "s").exists())
        self.assertFalse((self.workspace / "h").exists())


class TruncatedReportTests(unittest.TestCase):
    def test_a_cut_report_is_an_error_not_a_short_list(self):
        cut = proc.Result(0, "GRAPH-URL pkg://a\n", "", stdout_truncated=True)
        with self.assertRaisesRegex(RuntimeError, "sbsrender info.*truncated"):
            runner.parsed_stdout(cut, "sbsrender info")
        whole = proc.Result(0, "GRAPH-URL pkg://a\n", "")
        self.assertEqual(runner.parsed_stdout(whole, "sbsrender info"), whole.stdout)

    def test_failure_text_says_when_output_was_cut(self):
        cut = proc.Result(2, "", "engine failed\n", stderr_truncated=True)
        self.assertRegex(runner.failure(cut, "sbscooker"), "exit 2: engine failed.*truncated")


class WorkspaceRootTests(unittest.TestCase):
    def setUp(self):
        self.home = isolate_home(self)
        self.default = self.home / "Documents/SubstanceMCP"

    def test_default_workspace_is_created_for_an_output_directory(self):
        with operations.new_directory("out") as made:
            self.assertEqual(made, self.default / "out")
            self.assertTrue(made.is_dir())

    def test_reading_never_creates_the_default_workspace(self):
        status = asyncio.run(operations.status())
        self.assertEqual(status["workspace"], str(self.default))
        self.assertFalse(status["workspace_exists"])
        with self.assertRaises(ValueError):
            operations.source("in.sbsar", {".sbsar"})
        self.assertFalse(self.default.exists())

    def test_missing_explicit_workspace_is_refused_and_not_created(self):
        missing = self.home / "not-there"
        with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(missing)}):
            with self.assertRaisesRegex(
                    ValueError, "CAVI_SUBSTANCE_WORKSPACE.*does not exist"
            ), operations.new_directory("out"):
                self.fail("the body must not run")
            self.assertFalse(asyncio.run(operations.status())["workspace_exists"])
        self.assertFalse(missing.exists())


class ServerTests(unittest.TestCase):
    def test_tools_listed(self):
        from cavi_substance_mcp.designer.server import mcp

        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), {"designer_status", "material", "bake"})
        self.assertTrue(tools["designer_status"].annotations.read_only_hint)
        actions = tools["material"].input_schema["properties"]["action"]["enum"]
        self.assertEqual(actions, ["info", "cook", "render", "pack"])


if __name__ == "__main__":
    unittest.main()
