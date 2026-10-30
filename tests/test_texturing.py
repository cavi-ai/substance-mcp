import tempfile
import unittest
from pathlib import Path

from owned_client import OwnedPainterClient

from cavi_substance_mcp import painter_operations as ops
from cavi_substance_mcp.client import PainterClient
from evals.painter_simulator import BakingStatus, BlendingMode, MeshMapUsage, PainterSimulator

MASK_MAP = {"name": "T_$textureSet_MaskMap", "format": "png", "bit_depth": 8,
            "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}}


class Fixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        for name in ("high.obj", "cage.obj", "input.obj", "material.sbsar", "template.spt"):
            (self.root / name).write_bytes(b"fixture")
        self.sim = self.enterContext(PainterSimulator(self.root))
        self.sim.setup("open")
        self.client = OwnedPainterClient(port=self.sim.port, workspace=self.root)

    def waiter(self):
        return PainterClient(port=self.sim.port, workspace=self.root)

    def edit(self, action, **params):
        return self.client.call("layer_edit", {"action": action, "texture_set": "Body", **params})


class PackingTests(unittest.TestCase):
    def test_mask_map_translates_to_painter_channels(self):
        maps = ops.export_maps([MASK_MAP], None, None)
        self.assertEqual(maps[0]["parameters"], {"fileFormat": "png", "bitDepth": "8",
                                                 "dithering": False})
        self.assertEqual(maps[0]["channels"], [
            {"destChannel": "R", "srcChannel": "L", "srcMapType": "documentMap",
             "srcMapName": "metallic"},
            {"destChannel": "G", "srcChannel": "L", "srcMapType": "documentMap",
             "srcMapName": "ambientOcclusion"},
            {"destChannel": "B", "srcChannel": "R", "srcMapType": "defaultMap",
             "srcMapName": "black"},
            {"destChannel": "A", "srcChannel": "L", "srcMapType": "virtualMap",
             "srcMapName": "Glossiness"},
        ])

    def test_color_and_mesh_sources(self):
        maps = ops.export_maps([{"name": "C", "channels": {"RGB": "baseColor", "A": 1}},
                                {"name": "M", "channels": {"L": "curvature"}}], "tga", None)
        self.assertEqual([c["srcChannel"] for c in maps[0]["channels"]], ["R", "G", "B", "R"])
        self.assertEqual(maps[0]["channels"][3]["srcMapName"], "white")
        self.assertEqual(maps[1]["channels"][0]["srcMapType"], "meshMap")
        self.assertEqual(maps[1]["parameters"]["fileFormat"], "tga")

    def test_unsupported_sources_are_refused(self):
        for channels, message in [({"L": "1-metallic"}, "cannot invert"),
                                  ({"L": "0.5"}, "constants 0 and 1"),
                                  ({"L": "sheen"}, "Unknown map source"),
                                  ({"RGB": "baseColor", "R": "metallic"}, "twice"),
                                  ({"R": "metallic"}, "must write R, G and B")]:
            with self.subTest(channels=channels), self.assertRaisesRegex(ValueError, message):
                ops.export_maps([{"name": "T", "channels": channels}], None, None)
        for name in ("../x", "a/b", "$evil", ".hidden"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid map name"):
                ops.export_maps([{"name": name, "channels": {"L": "roughness"}}], None, None)


class ExportTests(Fixture):
    def test_custom_maps_and_overrides_reach_the_export_config(self):
        result = self.client.call("export_textures", {
            "directory": "out", "maps": [MASK_MAP], "resolution": 1024, "padding": "diffusion",
            "dilation_distance": 4, "texture_sets": ["Body"], "dry_run": False})
        self.assertEqual(result["files"], [str(self.root / "out/T_Body_MaskMap.png")])
        config = self.sim.exported_config
        self.assertEqual(config["defaultExportPreset"], "cavi_maps")
        self.assertEqual(config["exportParameters"], [{"parameters": {
            "paddingAlgorithm": "diffusion", "dilationDistance": 4, "sizeLog2": 10}}])
        self.assertEqual(config["exportList"], [{"rootPath": "Body"}])

    def test_udim_tiles_filter_requires_existing_tiles(self):
        with self.assertRaisesRegex(RuntimeError, "lacks some requested UV tiles"):
            self.client.call("export_textures", {"directory": "t", "preset":
                             "resource://eval-preset", "udim_tiles": [[1, 0]]})
        self.sim.state = "udim"
        result = self.client.call("export_textures", {"directory": "t", "udim_tiles": [[1, 0]],
                                  "maps": [{"name": "$textureSet.$udim",
                                            "channels": {"L": "height"}}]})
        self.assertEqual(result["files"], [str(self.root / "t/Body.1002.png")])
        self.assertEqual(self.sim.planned_config["exportList"][0]["filter"],
                         {"uvTiles": [[1, 0]]})


class BakeTests(Fixture):
    def start(self, **params):
        return self.client.call("bake", {"maps": ["ao", "curvature"], **params})

    def test_start_applies_parameters_and_status_follows_events(self):
        result = self.start(high_poly_meshes=["high.obj"], resolution=4096, antialiasing="2x2",
                            max_frontal=0.05, use_cage=True, cage_mesh="cage.obj",
                            match="always")
        self.assertTrue(result["started"])
        values = self.sim.bake["owner"].values
        self.assertEqual(values["HipolyMesh"], (self.root / "high.obj").as_uri())
        self.assertFalse(values["LowAsHigh"])
        self.assertEqual(values["OutputSize"], (12, 12))
        self.assertEqual((values["SubSampling"], values["CageMode"], values["FilterMethod"]),
                         (1, 2, 0))
        self.assertEqual(values["CageMesh"], (self.root / "cage.obj").as_uri())
        self.assertEqual(values["MaxHeight"], 0.05)
        self.assertEqual(self.sim.bake["bakers"], [MeshMapUsage.AO, MeshMapUsage.Curvature])
        status = self.client.call("bake_status")
        self.assertTrue(status["busy"])
        self.assertIsNone(status["status"])
        with self.assertRaisesRegex(RuntimeError, "baking"):
            self.edit("group", name="Blocked")
        self.sim.finish_bake(BakingStatus.Success)
        status = self.client.call("bake_status")
        self.assertFalse(status["busy"])
        self.assertEqual(status["status"], "Success")
        self.assertIsNotNone(status["mesh_maps"]["Body"]["ao"])
        self.edit("group", name="After")

    def test_failed_launch_restores_settings(self):
        before = dict(self.sim.bake["owner"].values)
        bakers = list(self.sim.bake["bakers"])
        self.sim.state = "bake-start-failure"
        with self.assertRaisesRegex(RuntimeError, "baking settings restored"):
            self.start(resolution=512, antialiasing="8x8")
        self.assertEqual(self.sim.bake["owner"].values, before)
        self.assertEqual(self.sim.bake["bakers"], bakers)
        self.assertFalse(self.client.call("bake_status")["running"])

    def test_missing_parameter_refuses_before_any_change(self):
        before = dict(self.sim.bake["owner"].values)
        self.sim.state = "bake-legacy-params"
        with self.assertRaisesRegex(RuntimeError, "MaxHeight"):
            self.start(resolution=512, max_frontal=0.2)
        self.assertEqual(self.sim.bake["owner"].values, before)
        self.assertEqual(self.sim.bake_calls, 0)


class LayerTests(Fixture):
    def test_owned_nodes_delete_without_confirmation(self):
        group = self.edit("group", name="Mine")
        self.assertEqual(self.edit("delete", uid=group["uid"])["deleted"], group["uid"])
        foreign = PainterClient(port=self.sim.port, workspace=self.root)
        foreign.call("session_control", {"action": "release", "lease_id": self.client.lease_id})
        other = OwnedPainterClient(port=self.sim.port, workspace=self.root)
        kept = other.call("layer_edit", {"action": "group", "texture_set": "Body",
                                         "name": "Theirs"})
        foreign.call("session_control", {"action": "release", "lease_id": other.lease_id})
        self.client = OwnedPainterClient(port=self.sim.port, workspace=self.root)
        with self.assertRaisesRegex(RuntimeError, "confirm_name"):
            self.edit("delete", uid=kept["uid"])
        self.assertEqual(len(self.sim.layers), 1)

    def test_sbsar_failure_removes_layer_and_reports_resource(self):
        self.sim.state = "parameter-failure"
        with self.assertRaisesRegex(RuntimeError, "new nodes removed.*resource://project/"):
            self.edit("sbsar_fill", name="Rock", sbsar="material.sbsar",
                      parameters={"wear": 0.5})
        self.assertEqual(self.sim.layers, [])
        self.assertEqual(len(self.sim.resources), 1)
        self.sim.state = "empty-sbsar"
        with self.assertRaisesRegex(RuntimeError, "No material graph"):
            self.edit("sbsar_fill", name="Rock", sbsar="material.sbsar")
        self.assertEqual(self.sim.layers, [])

    def test_sbsar_parameters_reach_material_source(self):
        result = self.edit("sbsar_fill", name="Rock", sbsar="material.sbsar",
                           parameters={"wear": 0.25, "$randomseed": True})
        material = self.sim.layers[0].material
        self.assertEqual(material.values["wear"], 0.25)
        self.assertEqual(material.values["$randomseed"], 1)
        self.assertIn("height", result["active_channels"])

    def test_generator_failure_removes_effect_and_added_mask(self):
        fill = self.client.call("create_fill_layer", {"texture_set": "Body", "name": "Base",
                                                      "base_color": [0.5, 0.5, 0.5]})
        with self.assertRaisesRegex(RuntimeError, "added mask removed"):
            self.edit("generator", uid=fill["uid"], resource="Metal Edge Wear",
                      parameters={"tint": [1, 0, 0]})
        layer = self.sim.layers[0]
        self.assertIsNone(layer.mask)
        self.assertEqual(layer.masks, [])

    def test_set_properties_restores_on_failure(self):
        group = self.edit("group", name="Wear")
        self.sim.state = "blend-failure"
        with self.assertRaisesRegex(RuntimeError, "previous values restored"):
            self.edit("set_properties", uid=group["uid"], blend_mode="Multiply", name="Changed")
        node = self.sim.layers[0]
        self.assertEqual(node.get_name(), "Wear")
        self.assertTrue(all(mode == BlendingMode.Normal for mode in node.modes.values()))

    def test_layer_stack_depth_is_bounded(self):
        parent = None
        for depth in range(10):
            parent = self.edit("group", name=f"G{depth}", parent_uid=parent)["uid"]
        listing = self.client.call("layer_stack", {"texture_set": "Body"})
        self.assertTrue(listing["truncated"])
        self.assertEqual(listing["node_count"], ops.MAX_DEPTH)


class ProjectTests(Fixture):
    def test_create_maps_udim_template_and_tangent_space(self):
        self.client.call("session_control", {"action": "release"})
        self.sim.setup("closed")
        self.client = OwnedPainterClient(port=self.sim.port, workspace=self.root)
        self.client.call("create_project", {"path": "input.obj", "udim": True,
                                            "tangent_space_per_fragment": True,
                                            "template": "template.spt"})
        settings = self.sim.created_settings
        self.assertEqual(settings.project_workflow.name, "UVTile")
        self.assertEqual(settings.tangent_space_mode.name, "PerFragment")
        self.assertEqual(self.sim.created_template, str(self.root / "template.spt"))

    def test_reload_mesh_reports_async_result(self):
        result = self.client.call("reload_mesh", {"path": "input.obj",
                                                  "preserve_strokes": False})
        self.assertTrue(result["loading_may_be_pending"])
        self.assertFalse(self.sim.reload_settings.preserve_strokes)
        status = self.waiter().wait_ready(10, 0.01)
        self.assertTrue(status["ready"])
        self.assertEqual(status["mesh_reload"]["status"], "SUCCESS")
        self.sim.state = "reload-error"
        self.client.call("reload_mesh", {"path": "input.obj"})
        self.assertEqual(self.waiter().wait_ready(10, 0.01)["mesh_reload"]["status"], "ERROR")


class ShelfTests(Fixture):
    def test_usage_filter_and_limit(self):
        found = self.client.call("shelf", {"query": "", "usage": "smart_material"})
        self.assertEqual([item["name"] for item in found["resources"]], ["Steel Painted Worn"])
        limited = self.client.call("shelf", {"query": "", "limit": 3})
        self.assertEqual(len(limited["resources"]), 3)
        self.assertTrue(limited["truncated"])


if __name__ == "__main__":
    unittest.main()
