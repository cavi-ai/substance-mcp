import base64
import tempfile
import unittest
from pathlib import Path

from cavi_substance_mcp.painter.client import PainterClient
from evals.painter_simulator import PainterSimulator

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0p0AAAAASUVORK5CYII=")


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
            assigned = {channel.name: source for channel, source in simulator.layers[0].sources.items()}
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
