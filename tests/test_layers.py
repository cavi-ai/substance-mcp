import tempfile
import unittest
from pathlib import Path

from owned_client import OwnedPainterClient

from evals.painter_simulator import PainterSimulator


class FillLayerTests(unittest.TestCase):
    def test_fill_values_reach_the_named_layer_and_duplicate_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            args = {"texture_set": "Body", "name": "Toon base", "base_color": [0.2, 0.5, 0.8],
                    "roughness": 0.7, "metallic": 0.0}
            result = client.call("create_fill_layer", args)
            self.assertEqual(result["base_color"], [0.2, 0.5, 0.8])
            self.assertEqual(result["roughness"], 0.7)
            sources = {c.name: s.value for c, s in simulator.layers[0].sources.items()}
            self.assertEqual(sources, {"BaseColor": (0.2, 0.5, 0.8),
                                       "Roughness": (0.7, 0.7, 0.7), "Metallic": (0.0, 0.0, 0.0)})
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                client.call("create_fill_layer", args)
            listing = client.call("layer_stack", {"texture_set": "Body"})
            self.assertEqual([item["name"] for item in listing["layers"]], ["Toon base"])

    def test_invalid_color_is_rejected_before_creating_a_layer(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            with self.assertRaisesRegex(RuntimeError, "between 0 and 1"):
                client.call("create_fill_layer", {
                    "texture_set": "Body", "name": "Invalid", "base_color": [2, 0, 0],
                })
            self.assertEqual(simulator.layers, [])

    def test_failed_source_assignment_removes_only_the_new_layer(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("open")
            client = OwnedPainterClient(port=simulator.port, workspace=Path(folder))
            args = {"texture_set": "Body", "name": "Existing", "base_color": [0.2, 0.5, 0.8]}
            client.call("create_fill_layer", args)
            simulator.state = "fill-failure"
            with self.assertRaisesRegex(RuntimeError, "source assignment failure"):
                client.call("create_fill_layer", {**args, "name": "Failed"})
            self.assertEqual([layer.get_name() for layer in simulator.layers], ["Existing"])
