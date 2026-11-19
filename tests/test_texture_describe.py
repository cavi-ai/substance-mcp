"""Texture descriptions report each file's stored channel format."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import OpenEXR
import tifffile

from cavi_substance_mcp import texture_io


class DescribeTests(unittest.TestCase):
    def test_png_tiff_and_exr_report_stored_depth_type_size_and_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            texture_io.write_png16(root / "base.png", np.zeros((2, 3, 3), np.float32))
            tifffile.imwrite(root / "height.tif", np.zeros((2, 3), np.float32))
            for name, dtype in (("half.exr", np.float16), ("full.exr", np.float32)):
                channels = {key: np.zeros((2, 3), dtype) for key in "RGBA"}
                with OpenEXR.File({}, channels) as image:
                    image.write(str(root / name))
            expected = {
                "base.png": ("png", 16, "uint", 3),
                "height.tif": ("tiff", 32, "float", 1),
                "half.exr": ("exr", 16, "float", 4),
                "full.exr": ("exr", 32, "float", 4),
            }
            for name, (kind, depth, sample, channels) in expected.items():
                with self.subTest(name=name):
                    self.assertEqual(texture_io.describe(root / name), {
                        "format": kind, "bit_depth": depth, "sample_type": sample,
                        "width": 3, "height": 2, "channels": channels})


if __name__ == "__main__":
    unittest.main()
