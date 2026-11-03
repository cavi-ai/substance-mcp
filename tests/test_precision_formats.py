"""Material channel IO preserves samples across high precision formats."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import OpenEXR
import tifffile

from cavi_substance_mcp import packing


class PrecisionTests(unittest.TestCase):
    def test_tiff16_and_exr_sources_pack_without_8bit_quantization(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = np.array([[[1, 2, 3], [257, 1025, 65534]]], np.uint16)
            tifffile.imwrite(root / "source.tif", source, photometric="rgb")
            loaded = packing.load(root / "source.tif")
            np.testing.assert_array_equal(np.rint(loaded * 65535).astype(np.uint16), source)
            with OpenEXR.File(
                {},
                {
                    "R": source[:, :, 0].astype(np.float32) / 65535,
                    "G": source[:, :, 1].astype(np.float32) / 65535,
                    "B": source[:, :, 2].astype(np.float32) / 65535,
                },
            ) as f:
                f.write(str(root / "source.exr"))
            for name in ("source.tif", "source.exr"):
                dest = root / name.replace(".", "-")
                dest.mkdir()
                spec = packing.PackSpec(
                    maps=[{"name": "packed", "format": "tif", "channels": {"RGB": "base"}}]
                )
                packing.pack(spec, {"base": root / name}, dest)
                np.testing.assert_array_equal(tifffile.imread(dest / "packed.tif"), source)

    def test_hdr_and_nonfinite_cannot_silently_clip_into_integer_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in (2.0, float("nan")):
                tifffile.imwrite(
                    root / "source.tif", np.full((2, 2, 3), value, np.float32), photometric="rgb"
                )
                with self.assertRaises(ValueError):
                    packing.pack(
                        packing.PackSpec(maps=[{"name": "map", "channels": {"RGB": "base"}}]),
                        {"base": root / "source.tif"},
                        root,
                    )
