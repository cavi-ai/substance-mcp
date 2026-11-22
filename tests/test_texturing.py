import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from owned_client import OwnedPainterClient, wait_ready

from cavi_substance_mcp.painter import operations as ops
from cavi_substance_mcp.painter.client import PainterClient
from evals.painter_simulator import (
    BakingStatus,
    BlendingMode,
    MeshMapUsage,
    PainterSimulator,
    ResourceID,
)

PNG_HEADER = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 64, 64) + bytes(5)

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
                                {"name": "M", "channels": {"L": "curvature"}}], "tga", "8")
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

    def test_effects_reach_content_or_mask_with_their_settings(self):
        fill = self.client.call("create_fill_layer", {"texture_set": "Body", "name": "Base",
                                                      "base_color": [0.5, 0.5, 0.5]})
        uid = fill["uid"]
        levels = self.edit("levels", uid=uid, parameters={"gamma": [1.2, 1.0, 0.8], "clamp": True})
        blur = self.edit("filter", uid=uid, resource="Blur", parameters={"wear": 0.25})
        anchor = self.edit("anchor_point", uid=uid, name="Wear anchor")
        masked = self.edit("levels", uid=uid, mask="white", parameters={"output_max": 0.5})
        select = self.edit("color_selection", uid=uid, resource="Grunge Map 001",
                           parameters={"colors": "#ff0000,#00ff80", "tolerance": 0.2})
        compare = self.edit("compare_mask", uid=uid, parameters={
            "operation": "WithinTolerance", "left_operand": "ThisMask", "constant": 0.25})
        layer = self.sim.layers[0]
        content = {type(node).__name__: node for node in layer.content}
        mask = {type(node).__name__: node for node in layer.masks}
        self.assertEqual(set(content),
                         {"LevelsEffectNode", "FilterEffectNode", "AnchorPointEffectNode"})
        self.assertEqual(set(mask), {"LevelsEffectNode", "ColorSelectionEffectNode",
                                     "CompareMaskEffectNode"})
        self.assertEqual((levels["stack"], masked["stack"], masked["mask_added"]),
                         ("content", "mask", True))
        self.assertEqual(content["LevelsEffectNode"].params.gamma, (1.2, 1.0, 0.8))
        self.assertTrue(content["LevelsEffectNode"].params.clamp)
        self.assertEqual(mask["LevelsEffectNode"].params.output_max, 0.5)
        self.assertEqual(content["FilterEffectNode"].source.values["wear"], 0.25)
        self.assertEqual(blur["resource_url"], content["FilterEffectNode"].source.resource_id.url())
        self.assertEqual(content["AnchorPointEffectNode"].get_name(), "Wear anchor")
        self.assertEqual(anchor["effects"][0]["name"], "Wear anchor")
        selection = mask["ColorSelectionEffectNode"].params
        self.assertEqual([color.value for color in selection.colors],
                         [(1.0, 0.0, 0.0), (0.0, 1.0, 128 / 255)])
        self.assertEqual((selection.tolerance, select["mask_added"]), (0.2, False))
        compared = mask["CompareMaskEffectNode"].params
        self.assertEqual((compared.operation.name, compared.left_operand.name, compared.constant),
                         ("WithinTolerance", "ThisMask", 0.25))
        self.assertEqual(compare["parameters"], ["constant", "left_operand", "operation"])
        with self.assertRaisesRegex(RuntimeError, "Unknown levels parameters: hue"):
            self.edit("levels", uid=uid, parameters={"hue": 0.5})
        self.assertEqual(len(layer.content), 3)

    def test_sbsar_update_swaps_the_graph_and_outdated_resources_refresh(self):
        first = self.edit("sbsar_fill", name="Rock", sbsar="material.sbsar",
                          parameters={"wear": 0.25})
        (self.root / "material.sbsar").write_bytes(b"recooked")
        updated = self.edit("sbsar_update", sbsar="material.sbsar", resource=first["resource_url"])
        material = self.sim.layers[0].material
        self.assertNotEqual(updated["resource_url"], first["resource_url"])
        self.assertEqual((updated["replaced"], material.resource_id.url(), material.values["wear"]),
                         (first["resource_url"], updated["resource_url"], 0.25))
        self.assertEqual(self.sim.imports[-1]["bytes"], b"recooked")
        with self.assertRaisesRegex(RuntimeError, "not used in the project"):
            self.edit("sbsar_update", sbsar="material.sbsar", resource=first["resource_url"])
        self.sim.outdated = {ResourceID.from_url(updated["resource_url"]):
                             ResourceID.from_url(first["resource_url"])}
        refreshed = self.client.call("update_resources", {})
        self.assertEqual(refreshed["updated"],
                         [{"old": updated["resource_url"], "new": first["resource_url"]}])
        self.assertEqual(material.resource_id.url(), first["resource_url"])
        self.assertEqual(self.client.call("update_resources", {})["updated"], [])

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


class ProjectionTests(Fixture):
    def fill(self, name="Base"):
        return self.client.call("create_fill_layer", {
            "texture_set": "Body", "name": name, "base_color": [0.2, 0.5, 0.8]})

    def test_projection_enums_support_native_binding_shape(self):
        uid = self.fill()["uid"]
        layerstack = self.sim.module.layerstack
        mode = SimpleNamespace(__members__=layerstack.ProjectionMode.__members__)
        axis = SimpleNamespace(__members__=layerstack.SymmetryAxis.__members__)
        filtering = SimpleNamespace(__members__=layerstack.FilteringMode.__members__)
        with (patch.object(layerstack, "ProjectionMode", mode),
              patch.object(layerstack, "SymmetryAxis", axis),
              patch.object(layerstack, "FilteringMode", filtering)):
            result = self.edit("projection", uid=uid,
                               projection={"mode": "Planar", "filtering_mode": "Nearest"},
                               symmetry={"mode": "Mirror", "enabled": True, "axis": "Y"})
        self.assertEqual(result["projection"]["mode"], "Planar")
        self.assertEqual(result["projection"]["filtering_mode"], "Nearest")
        self.assertEqual(result["projection"]["symmetry"]["axis"], "Y")

    def test_uv_scale_rotation_and_offset_round_trip(self):
        uid = self.fill()["uid"]
        placed = self.edit("projection", uid=uid, projection={
            "mode": "UV",
            "filtering_mode": "Nearest",
            "uv_wrapping_mode": "Repeat",
            "uv_transformation": {
                "scale_mode": "CustomPhysicalSize",
                "scale": [2, 0.5],
                "rotation": 15,
                "offset": [0.1, -0.2],
            },
        })
        params = self.sim.layers[0].get_projection_parameters()
        self.assertEqual(placed["uid"], uid)
        self.assertEqual(placed["projection"], {
            "mode": "UV",
            "filtering_mode": "Nearest",
            "uv_wrapping_mode": "Repeat",
            "uv_transformation": {
                "scale_mode": "CustomPhysicalSize",
                "scale": [2.0, 0.5],
                "rotation": 15.0,
                "offset": [0.1, -0.2],
            },
        })
        transform = params.uv_transformation
        self.assertEqual((transform.scale, transform.rotation, transform.offset),
                         ([2.0, 0.5], 15.0, [0.1, -0.2]))
        with self.assertRaisesRegex(RuntimeError, "shape_crop_mode"):
            self.edit("projection", uid=uid, projection={
                "mode": "UV", "shape_crop_mode": "CroppedToShape"})
        kept = self.sim.layers[0].get_projection_parameters().uv_transformation
        self.assertEqual(kept.scale, [2.0, 0.5])
        masked = self.edit("smart_mask", uid=uid, resource="Edges Dirt")
        effect = next(item["uid"] for item in masked["effects"] if item["type"] == "FillEffect")
        again = self.edit("projection", uid=effect, projection={
            "mode": "UV",
            "uv_transformation": {"scale": [3, 4], "rotation": 1, "offset": [0, 1]},
        })
        self.assertEqual(again["projection"]["uv_transformation"]["scale"], [3.0, 4.0])
        self.assertEqual(again["projection"]["uv_transformation"]["offset"], [0.0, 1.0])

    def test_triplanar_refuses_offset_and_accepts_hardness_and_scale(self):
        uid = self.fill()["uid"]
        with self.assertRaisesRegex(RuntimeError, "unavailable for Triplanar"):
            self.edit("projection", uid=uid, projection={
                "mode": "Triplanar", "uv_transformation": {"offset": [0.1, 0.2]}})
        with self.assertRaisesRegex(RuntimeError, "uv_wrapping_mode"):
            self.edit("projection", uid=uid, projection={
                "mode": "Triplanar", "uv_wrapping_mode": "Repeat"})
        self.assertEqual(self.sim.layers[0].get_projection_mode().name, "UV")
        placed = self.edit("projection", uid=uid, projection={
            "mode": "Triplanar",
            "hardness": 0.25,
            "projection_3d": {"scale": [1, 2, 3]},
        })
        self.assertEqual(placed["projection"]["mode"], "Triplanar")
        self.assertEqual(placed["projection"]["hardness"], 0.25)
        self.assertEqual(placed["projection"]["projection_3d"]["scale"], [1.0, 2.0, 3.0])
        params = self.sim.layers[0].get_projection_parameters()
        self.assertIsNone(params.uv_transformation.offset)
        self.assertEqual(params.projection_3d.scale, [1.0, 2.0, 3.0])

    def test_planar_refuses_hardness_and_accepts_3d_offset(self):
        uid = self.fill()["uid"]
        with self.assertRaisesRegex(RuntimeError, "hardness"):
            self.edit("projection", uid=uid, projection={"mode": "Planar", "hardness": 0.4})
        with self.assertRaisesRegex(RuntimeError, "depth_culling"):
            self.edit("projection", uid=uid, projection={
                "mode": "Planar", "depth_culling": True})
        with self.assertRaisesRegex(RuntimeError, "Z scaling"):
            self.edit("projection", uid=uid, projection={
                "mode": "Planar", "projection_3d": {"scale": [1, 2, 3]}})
        self.assertEqual(self.sim.layers[0].get_projection_mode().name, "UV")
        placed = self.edit("projection", uid=uid, projection={
            "mode": "Planar",
            "uv_wrapping_mode": "RepeatHorizontally",
            "projection_3d": {"offset": [1, 2, 3], "scale": [4, 5]},
        })
        self.assertEqual(placed["projection"]["mode"], "Planar")
        self.assertEqual(placed["projection"]["projection_3d"]["offset"], [1.0, 2.0, 3.0])
        self.assertEqual(placed["projection"]["projection_3d"]["scale"], [4.0, 5.0])
        spatial = self.sim.layers[0].get_projection_parameters().projection_3d
        self.assertEqual(spatial.offset, [1.0, 2.0, 3.0])
        self.assertEqual(spatial.scale, [4.0, 5.0])

    def test_unknown_mode_and_material_physical_size_are_refused(self):
        uid = self.fill()["uid"]
        with self.assertRaisesRegex(RuntimeError, "UV, Triplanar, Planar"):
            self.edit("projection", uid=uid, projection={"mode": "Spherical"})
        with self.assertRaisesRegex(RuntimeError, "physical-size metadata"):
            self.edit("projection", uid=uid, projection={
                "mode": "UV",
                "uv_transformation": {"scale_mode": "MaterialPhysicalSize", "scale": [1, 1]},
            })
        self.assertEqual(self.sim.layers[0].get_projection_mode().name, "UV")

    def test_non_fill_uid_is_refused(self):
        paint = self.edit("paint", name="Paint")
        with self.assertRaisesRegex(RuntimeError, "TypeError: uid must be a fill"):
            self.edit("projection", uid=paint["uid"], projection={"mode": "UV"})
        self.assertFalse(hasattr(self.sim.layers[0], "projection_mode"))

    def test_failed_set_restores_the_previous_projection(self):
        uid = self.fill()["uid"]
        self.edit("projection", uid=uid, projection={
            "mode": "UV", "uv_transformation": {"scale": [2, 3], "rotation": 10}})
        self.sim.state = "projection-failure"
        with self.assertRaisesRegex(RuntimeError, "previous values restored"):
            self.edit("projection", uid=uid, projection={
                "mode": "Planar", "projection_3d": {"scale": [4, 5]}})
        node = self.sim.layers[0]
        self.assertEqual(node.get_projection_mode().name, "UV")
        transform = node.get_projection_parameters().uv_transformation
        self.assertEqual((transform.scale, transform.rotation), ([2.0, 3.0], 10.0))

    def test_layer_stack_reports_the_projection_mode(self):
        uid = self.fill()["uid"]
        listing = self.client.call("layer_stack", {"texture_set": "Body"})
        self.assertEqual(listing["layers"][0]["projection_mode"], "UV")
        self.edit("projection", uid=uid, projection={"mode": "Planar"})
        listing = self.client.call("layer_stack", {"texture_set": "Body"})
        self.assertEqual(listing["layers"][0]["projection_mode"], "Planar")

    def _guard_symmetry(self, node):
        called = []

        def guard(name):
            def wrapped(*_args, **_kwargs):
                called.append(name)
                raise AssertionError(name)
            return wrapped

        node.is_symmetry_enabled = guard("is_symmetry_enabled")
        node.set_symmetry_enabled = guard("set_symmetry_enabled")
        node.get_symmetry_parameters = guard("get_symmetry_parameters")
        node.set_symmetry_parameters = guard("set_symmetry_parameters")
        return called

    def test_triplanar_mirror_sets_parameters_then_enables(self):
        uid = self.fill()["uid"]
        node = self.sim.layers[0]
        order = []
        set_parameters = node.set_symmetry_parameters
        set_enabled = node.set_symmetry_enabled

        def parameters(params):
            order.append(("parameters", node.is_symmetry_enabled(), params))
            set_parameters(params)

        def enabled(flag):
            order.append(("enabled", flag))
            set_enabled(flag)

        node.set_symmetry_parameters = parameters
        node.set_symmetry_enabled = enabled
        placed = self.edit("projection", uid=uid, projection={"mode": "Triplanar"}, symmetry={
            "mode": "Mirror",
            "enabled": True,
            "axis": "X",
            "flip_u": True,
            "flip_v": False,
            "axis_position": [-10, 0, 10],
        })
        self.assertEqual([step[0] for step in order], ["parameters", "enabled"])
        self.assertFalse(order[0][1])
        recorded = order[0][2]
        self.assertEqual(type(recorded).__name__, "MirrorSymmetryParams")
        self.assertEqual(recorded.axis.name, "X")
        self.assertEqual((recorded.flip_u, recorded.flip_v), (True, False))
        self.assertEqual(recorded.axis_position, [-10.0, 0.0, 10.0])
        self.assertFalse(hasattr(recorded, "count"))
        self.assertEqual(order[1][1], True)
        self.assertEqual(placed["projection"]["symmetry"], {
            "mode": "Mirror",
            "enabled": True,
            "axis": "X",
            "flip_u": True,
            "flip_v": False,
            "axis_position": [-10.0, 0.0, 10.0],
        })
        self.assertTrue(node.is_symmetry_enabled())
        self.assertEqual(node.get_symmetry_parameters().axis.name, "X")

    def test_radial_sets_count_and_angle_span(self):
        uid = self.fill()["uid"]
        placed = self.edit("projection", uid=uid, projection={"mode": "Planar"}, symmetry={
            "mode": "Radial", "enabled": True, "count": 16, "angle_span": 360,
        })
        self.assertEqual(placed["projection"]["symmetry"], {
            "mode": "Radial", "enabled": True, "count": 16, "angle_span": 360.0,
        })
        node = self.sim.layers[0]
        params = node.get_symmetry_parameters()
        self.assertEqual(type(params).__name__, "RadialSymmetryParams")
        self.assertEqual((params.count, params.angle_span), (16, 360.0))
        self.assertTrue(node.is_symmetry_enabled())
        disabled = self.edit("projection", uid=uid, projection={"mode": "Planar"}, symmetry={
            "mode": "Radial", "enabled": False, "count": 2, "angle_span": -360,
        })
        self.assertEqual(disabled["projection"]["symmetry"], {
            "mode": "Radial", "enabled": False, "count": 2, "angle_span": -360.0,
        })
        self.assertFalse(node.is_symmetry_enabled())
        self.assertEqual(node.get_symmetry_parameters().count, 2)

    def test_fill_effect_accepts_radial_symmetry(self):
        uid = self.fill()["uid"]
        masked = self.edit("smart_mask", uid=uid, resource="Edges Dirt")
        effect = next(item["uid"] for item in masked["effects"] if item["type"] == "FillEffect")
        placed = self.edit("projection", uid=effect, projection={"mode": "Triplanar"}, symmetry={
            "mode": "Radial", "enabled": True, "count": 2, "angle_span": 90,
        })
        self.assertEqual(placed["projection"]["symmetry"]["mode"], "Radial")
        self.assertEqual(placed["projection"]["symmetry"]["count"], 2)
        node = self.sim.nodes[effect]
        self.assertEqual(node.get_symmetry_parameters().angle_span, 90.0)
        self.assertTrue(node.is_symmetry_enabled())

    def test_mirror_refuses_count_and_angle_span(self):
        uid = self.fill()["uid"]
        node = self.sim.layers[0]
        for symmetry in (
            {"mode": "Mirror", "enabled": True, "count": 4},
            {"mode": "Mirror", "enabled": True, "angle_span": 90},
        ):
            called = self._guard_symmetry(node)
            with self.subTest(symmetry=sorted(symmetry)):
                with self.assertRaisesRegex(RuntimeError, "symmetry mode=Mirror"):
                    self.edit("projection", uid=uid, projection={"mode": "Triplanar"},
                              symmetry=symmetry)
                self.assertEqual(called, [])
                self.assertEqual(node.get_projection_mode().name, "UV")

    def test_uv_refuses_symmetry_without_calling_it(self):
        uid = self.fill()["uid"]
        self.edit("projection", uid=uid, projection={"mode": "Triplanar"})
        node = self.sim.layers[0]
        self.assertFalse(node.is_symmetry_enabled())
        called = self._guard_symmetry(node)
        with self.assertRaisesRegex(RuntimeError, "3D projection"):
            self.edit("projection", uid=uid, projection={"mode": "UV"}, symmetry={
                "mode": "Mirror", "enabled": True, "axis": "Z",
            })
        self.assertEqual(called, [])
        self.assertEqual(node.get_projection_mode().name, "Triplanar")

    def test_symmetry_ranges_are_refused_before_the_api(self):
        uid = self.fill()["uid"]
        node = self.sim.layers[0]
        cases = (
            ({"mode": "Mirror", "enabled": True, "axis_position": [10.1, 0, 0]}, "axis_position"),
            ({"mode": "Mirror", "enabled": True, "axis_position": [0, 0, -10.1]}, "axis_position"),
            ({"mode": "Radial", "enabled": True, "count": 1}, "count"),
            ({"mode": "Radial", "enabled": True, "count": 17}, "count"),
            ({"mode": "Radial", "enabled": False, "angle_span": -360.1}, "angle_span"),
            ({"mode": "Radial", "enabled": False, "angle_span": 360.1}, "angle_span"),
        )
        for symmetry, message in cases:
            called = self._guard_symmetry(node)
            with self.subTest(symmetry=symmetry):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.edit("projection", uid=uid, projection={"mode": "Planar"},
                              symmetry=symmetry)
                self.assertEqual(called, [])
                self.assertEqual(node.get_projection_mode().name, "UV")

    def test_failed_symmetry_set_restores_the_previous_symmetry(self):
        uid = self.fill()["uid"]
        self.edit("projection", uid=uid, projection={
            "mode": "Triplanar",
            "hardness": 0.25,
            "projection_3d": {"scale": [1, 2, 3]},
        }, symmetry={
            "mode": "Mirror",
            "enabled": True,
            "axis": "Y",
            "flip_u": True,
            "flip_v": False,
            "axis_position": [1, 2, 3],
        })
        self.sim.state = "symmetry-failure"
        with self.assertRaisesRegex(RuntimeError, "previous values restored"):
            self.edit("projection", uid=uid, projection={
                "mode": "Planar",
                "projection_3d": {"scale": [4, 5]},
            }, symmetry={
                "mode": "Radial",
                "enabled": False,
                "count": 8,
                "angle_span": 90,
            })
        node = self.sim.layers[0]
        self.assertEqual(node.get_projection_mode().name, "Triplanar")
        params = node.get_projection_parameters()
        self.assertEqual(params.hardness, 0.25)
        self.assertEqual(params.projection_3d.scale, [1.0, 2.0, 3.0])
        self.assertTrue(node.is_symmetry_enabled())
        symmetry = node.get_symmetry_parameters()
        self.assertEqual(type(symmetry).__name__, "MirrorSymmetryParams")
        self.assertEqual(symmetry.axis.name, "Y")
        self.assertEqual((symmetry.flip_u, symmetry.flip_v), (True, False))
        self.assertEqual(symmetry.axis_position, [1.0, 2.0, 3.0])
        self.assertFalse(hasattr(symmetry, "count"))


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
        status = wait_ready(self.sim.port, self.root, 10, 0.01)
        self.assertTrue(status["ready"])
        self.assertEqual(status["mesh_reload"]["status"], "SUCCESS")
        self.sim.state = "reload-error"
        self.client.call("reload_mesh", {"path": "input.obj"})
        with self.assertRaisesRegex(RuntimeError, "Mesh reload failed"):
            wait_ready(self.sim.port, self.root, 10, 0.01)
        self.assertEqual(self.waiter().call("status")["mesh_reload"]["status"], "ERROR")

    def test_mesh_reload_result_belongs_to_one_project(self):
        self.sim.state = "reload-error"
        self.client.call("reload_mesh", {"path": "input.obj"})
        with self.assertRaisesRegex(RuntimeError, "Mesh reload failed"):
            wait_ready(self.sim.port, self.root, 10, 0.01)
        self.sim.state = "open"
        self.client.call("close_project", {"expected_file": "simulated-original.spp"})
        self.assertIsNone(self.waiter().call("status")["mesh_reload"])


    def test_save_as_template_and_mesh_export_write_receipted_directories(self):
        saved = self.client.call("save_as", {"directory": "saved"})
        template = self.client.call("save_template", {"directory": "templates"})
        mesh = self.client.call("export_mesh", {"directory": "mesh", "format": "obj"})
        self.assertEqual([saved["path"], template["path"], mesh["path"]],
                         [str(self.root / "saved/project.spp"),
                          str(self.root / "templates/Body.spt"), str(self.root / "mesh/mesh.obj")])
        self.assertEqual(self.waiter().call("status")["file"], saved["path"])
        self.assertEqual(template["texture_set"], "Body")
        self.assertEqual(self.sim.mesh_exports, [(mesh["path"], "BaseMesh")])
        for result in (saved, template, mesh):
            self.assertTrue(Path(result["manifest"]).is_file())
            self.assertEqual(result["bytes"], Path(result["path"]).stat().st_size)
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.client.call("export_mesh", {"directory": "mesh", "format": "obj"})
        with self.assertRaisesRegex(RuntimeError, "format must be one of"):
            self.client.call("export_mesh", {"directory": "other", "format": "stl"})
        self.assertFalse((self.root / "other").exists())

    def test_display_sets_environment_lut_and_tone_mapping_and_restores_on_failure(self):
        before = self.client.call("display", {})
        self.assertEqual(before, {"environment": "resource://starter_assets/Panorama",
                                  "color_lut": None, "tone_mapping": "Linear"})
        after = self.client.call("display", {"display": {
            "environment": "Studio", "color_lut": "Film LUT", "tone_mapping": "ACES"}})
        self.assertEqual(after, {"environment": "resource://starter_assets/Studio",
                                 "color_lut": "resource://starter_assets/Film LUT",
                                 "tone_mapping": "ACES"})
        with self.assertRaisesRegex(RuntimeError, "tone_mapping must be one of Linear, ACES"):
            self.client.call("display", {"display": {"tone_mapping": "Filmic"}})
        with self.assertRaisesRegex(RuntimeError, "No environment named Grunge Map 001"):
            self.client.call("display", {"display": {"environment": "Grunge Map 001"}})
        self.sim.state = "display-failure"
        with self.assertRaisesRegex(RuntimeError, "previous display restored"):
            self.client.call("display", {"display": {"environment": "Panorama",
                                                     "tone_mapping": "Linear"}})
        self.sim.state = "open"
        self.assertEqual(self.client.call("display", {}), after)

    def test_mesh_maps_take_workspace_images_and_restore_on_failure(self):
        (self.root / "normal.png").write_bytes(PNG_HEADER)
        (self.root / "ao.png").write_bytes(PNG_HEADER)
        result = self.client.call("texture_set_settings", {
            "texture_set": "Body", "mesh_maps": {"normal": "normal.png", "ao": "ao.png"}})
        self.assertEqual({key: result["mesh_maps"][key] for key in ("normal", "ao")},
                         {"normal": self.sim.mesh_maps[MeshMapUsage.Normal].url(),
                          "ao": self.sim.mesh_maps[MeshMapUsage.AO].url()})
        self.assertTrue(all(item["usage"].name == "TEXTURE" and Path(item["path"]).parent
                            != self.root for item in self.sim.imports))
        assigned = dict(self.sim.mesh_maps)
        self.sim.state = "mesh-map-failure"
        with self.assertRaisesRegex(RuntimeError, "previous mesh maps restored.*kept"):
            self.client.call("texture_set_settings", {
                "texture_set": "Body", "mesh_maps": {"normal": "ao.png", "ao": "normal.png"}})
        self.assertEqual(self.sim.mesh_maps, assigned)
        self.sim.state = "open"
        with self.assertRaisesRegex(RuntimeError, "Unknown mesh map world; use normal, "):
            self.client.call("texture_set_settings", {
                "texture_set": "Body", "mesh_maps": {"world": "ao.png"}})


class ShelfTests(Fixture):
    def test_usage_filter_and_limit(self):
        found = self.client.call("shelf", {"query": "", "usage": "smart_material"})
        self.assertEqual([item["name"] for item in found["resources"]], ["Steel Painted Worn"])
        limited = self.client.call("shelf", {"query": "", "limit": 3})
        self.assertEqual(len(limited["resources"]), 3)
        self.assertTrue(limited["truncated"])


if __name__ == "__main__":
    unittest.main()
