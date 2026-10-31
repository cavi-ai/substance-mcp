import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cavi_substance_mcp import packing
from cavi_substance_mcp.sampler import cli, pack

SPEC = {
    "maps": [
        {"name": "Mask", "channels": {"R": "metallic", "G": "ambientOcclusion",
                                     "B": "0", "A": "1-roughness"}},
        {"name": "Base", "channels": {"RGB": "baseColor"}},
    ]
}


class PackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.workspace = Path(self.folder.name) / "ws"
        (self.workspace / "maps").mkdir(parents=True)
        Image.new("RGB", (16, 16), (204, 102, 51)).save(
            self.workspace / "maps/basecolor.png")
        Image.new("L", (16, 16), 64).save(self.workspace / "maps/roughness.png")
        Image.new("L", (16, 16), 255).save(self.workspace / "maps/metallic.png")
        Image.new("L", (16, 16), 200).save(self.workspace / "maps/ao.png")
        self.sources = {
            "baseColor": "maps/basecolor.png",
            "roughness": "maps/roughness.png",
            "metallic": "maps/metallic.png",
            "ambientOcclusion": "maps/ao.png",
        }

    def tearDown(self):
        self.folder.cleanup()

    def test_pack(self):
        result = pack.pack_sync(
            "packed/rock", self.sources, packing.PackSpec.model_validate(SPEC),
            self.workspace,
        )
        self.assertEqual(result["directory"], "packed/rock")
        self.assertEqual({item["path"] for item in result["maps"]},
                         {"packed/rock/Mask.png", "packed/rock/Base.png"})
        self.assertTrue((self.workspace / "packed/rock/Mask.png").is_file())
        self.assertTrue((self.workspace / "packed/rock/Base.png").is_file())
        receipt = json.loads((self.workspace / result["manifest"]).read_text())
        self.assertEqual((receipt["app"], receipt["operation"]), ("sampler", "pack"))
        self.assertTrue(receipt["outputs"])

    def test_existing_directory(self):
        (self.workspace / "packed/existing").mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            pack.pack_sync(
                "packed/existing", self.sources, packing.PackSpec.model_validate(SPEC),
                self.workspace,
            )

    def test_failure_removes_directory(self):
        spec = packing.PackSpec.model_validate({
            "maps": [{"name": "Bad", "channels": {"RGB": "missing"}}],
        })
        with self.assertRaises(ValueError):
            pack.pack_sync("packed/bad", self.sources, spec, self.workspace)
        self.assertFalse((self.workspace / "packed/bad").exists())

    def test_cli_pack(self):
        data = {"directory": "packed/cli", "sources": self.sources, "pack": SPEC}
        (self.workspace / "pack.json").write_text(json.dumps(data))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "pack", "pack.json", "--workspace", str(self.workspace), "--json",
            ])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["directory"], "packed/cli")
        self.assertTrue((self.workspace / "packed/cli/Mask.png").is_file())


if __name__ == "__main__":
    unittest.main()


class PackBudgetTests(unittest.TestCase):
    def test_a_pack_over_the_decoded_size_limit_is_refused_before_decoding(self):
        from unittest.mock import patch

        from PIL import Image

        from cavi_substance_mcp import packing

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "r.png"
            Image.new("L", (64, 64)).save(source)
            spec = packing.PackSpec.model_validate(
                {"maps": [{"name": "M", "channels": {"R": "roughness", "G": "0", "B": "0"}}]})
            with patch.object(packing, "MAX_DECODED_BYTES", 64 * 64 * 16 - 1), \
                    patch.object(packing, "load") as load, \
                    self.assertRaisesRegex(ValueError, "decoded; the limit"):
                packing.pack(spec, {"roughness": source}, Path(folder))
            load.assert_not_called()
