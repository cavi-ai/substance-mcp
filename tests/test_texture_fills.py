import base64
import struct
import tempfile
import unittest
from pathlib import Path

from cavi_substance_mcp.painter.client import PainterClient
from evals.painter_simulator import ChannelType, PainterSimulator

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0p0AAAAASUVORK5CYII="
)


class TextureFillTests(unittest.TestCase):
    def test_bitmap_fill_imports_color_as_srgb_and_keeps_scalar_channels_raw(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            root = Path(folder)
            (root / "color.png").write_bytes(PNG)
            client = PainterClient(port=simulator.port, workspace=root)
            state = client.call("session_control", {"action": "inspect"})
            token = client.call("session_control", {"action": "claim", "owner": "texture-fixture",
                                "expected_project": state["project_id"]})["lease_id"]
            result = client.call("create_texture_fill", {
                "texture_set": "Body", "name": "Ceramic", "base_color_texture": "color.png",
                "roughness": .3, "metallic": .1,
            }, lease_id=token)
            self.assertEqual(result["color_space"], "sRGB")
            self.assertEqual(result["roughness"], .3)
            self.assertEqual(result["base_color_texture"]["width"], 1)
            self.assertEqual(len(simulator.layers), 1)
            self.assertEqual(len(simulator.resources), 1)
            assigned = {channel.name: source
                        for channel, source in simulator.layers[0].sources.items()}
            assigned.update(Roughness=assigned["SpecularRoughness"],
                            Metallic=assigned["BaseMetalness"])
            self.assertEqual(assigned["BaseColor"].color_space, "sRGB")
            self.assertEqual(assigned["Roughness"].color_space, "Raw")
            self.assertEqual(assigned["Metallic"].color_space, "Raw")
            self.assertEqual(assigned["Roughness"].value[0], .3)
            self.assertEqual(assigned["Metallic"].value[0], .1)
            channels = client.call("layer_stack", {"texture_set": "Body"})["layers"][0]["channels"]
            self.assertEqual(channels["base_color"]["color_space"], "sRGB")
            self.assertEqual(channels["base_color"]["resource_url"], result["resource_url"])
            self.assertEqual(channels["roughness"]["value"], [.3, .3, .3])
            self.assertEqual(channels["roughness"]["color_space"], "Raw")

    def test_failed_bitmap_binding_removes_layer_and_reports_retained_resource(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            root = Path(folder)
            (root / "color.png").write_bytes(PNG)
            simulator.setup("bitmap-failure")
            client = PainterClient(port=simulator.port, workspace=root)
            state = client.call("session_control", {"action": "inspect"})
            token = client.call("session_control", {"action": "claim", "owner": "texture-fixture",
                                "expected_project": state["project_id"]})["lease_id"]
            with self.assertRaisesRegex(RuntimeError, "new layer removed.*resource://project/"):
                client.call("create_texture_fill", {
                    "texture_set": "Body", "name": "Failed", "base_color_texture": "color.png",
                }, lease_id=token)
            self.assertEqual(simulator.layers, [])
            self.assertEqual(len(simulator.resources), 1)

    def test_invalid_bitmap_fails_before_importing_resources_or_creating_layer(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            root = Path(folder)
            (root / "color.png").write_bytes(b"invalid")
            client = PainterClient(port=simulator.port, workspace=root)
            state = client.call("session_control", {"action": "inspect"})
            token = client.call("session_control", {"action": "claim", "owner": "texture-fixture",
                                "expected_project": state["project_id"]})["lease_id"]
            with self.assertRaisesRegex(RuntimeError, "PNG"):
                client.call("create_texture_fill", {
                    "texture_set": "Body", "name": "Invalid", "base_color_texture": "color.png",
                }, lease_id=token)
            self.assertEqual(simulator.layers, [])

    def test_exr_and_tiff_fills_reach_8192_and_bind_float_data_linear(self):
        def tiff(order, kind, width, height, sample_format):
            entries = [(256, kind, width), (257, kind, height), (339, 3, sample_format)]
            packed = b"".join(
                struct.pack(order + "HHI", tag, kind, 1)
                + struct.pack(order + ("HH" if kind == 3 else "I"), value, *([0] * (kind == 3)))
                for tag, kind, value in entries)
            magic = b"II*\0" if order == "<" else b"MM\0*"
            return magic + struct.pack(order + "IH", 8, len(entries)) + packed + b"\0" * 4

        exr = (b"\x76\x2f\x31\x01\x02\0\0\0" + b"compression\0compression\0"
               + struct.pack("<i", 1) + b"\x03" + b"dataWindow\0box2i\0"
               + struct.pack("<i4i", 16, 0, 0, 8191, 4095) + b"\0")
        wide = bytearray(PNG)
        wide[16:20] = struct.pack(">I", 8193)
        files = {"color.exr": exr, "float.tif": tiff("<", 4, 5000, 3000, 3),
                 "ldr.tiff": tiff(">", 3, 640, 480, 1), "wide.png": bytes(wide)}
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            root = Path(folder)
            for name, data in files.items():
                (root / name).write_bytes(data)
            client = PainterClient(port=simulator.port, workspace=root)
            state = client.call("session_control", {"action": "inspect"})
            token = client.call("session_control", {"action": "claim", "owner": "texture-fixture",
                                "expected_project": state["project_id"]})["lease_id"]

            def fill(name, texture):
                return client.call("create_texture_fill", {
                    "texture_set": "Body", "name": name, "base_color_texture": texture,
                }, lease_id=token)

            with self.assertRaisesRegex(RuntimeError, "1 to 8192 pixels"):
                fill("Wide", "wide.png")
            self.assertEqual((simulator.layers, simulator.resources), ([], []))
            found = {name: fill(name, name) for name in ("color.exr", "float.tif", "ldr.tiff")}
            self.assertEqual(
                {name: (result["color_space"], result["base_color_texture"]["width"],
                        result["base_color_texture"]["height"])
                 for name, result in found.items()},
                {"color.exr": ("Working", 8192, 4096), "float.tif": ("Working", 5000, 3000),
                 "ldr.tiff": ("sRGB", 640, 480)})
            bound = {layer.get_name(): layer.sources[ChannelType.BaseColor].color_space
                     for layer in simulator.layers}
            self.assertEqual(bound, {"color.exr": "Working", "float.tif": "Working",
                                     "ldr.tiff": "sRGB"})
