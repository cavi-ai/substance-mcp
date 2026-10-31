"""Layer-edit validation that must hold against the simulated Painter API."""

import unittest

from test_texturing import Fixture

from cavi_substance_mcp import painter_operations as ops
from evals.painter_simulator import BlendingMode


class BlendModeTests(Fixture):
    def test_non_member_attributes_are_not_blend_modes(self):
        group = self.edit("group", name="Wear")
        node = self.sim.layers[0]
        for mode in ("mro", "__members__", "_value2member_map_", "multiply", "", "Multiply "):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    RuntimeError, "Unknown blend_mode"):
                self.edit("set_properties", uid=group["uid"], blend_mode=mode)
            self.assertEqual(node.modes, {})

    def test_real_members_still_apply(self):
        group = self.edit("group", name="Wear")
        self.edit("set_properties", uid=group["uid"], blend_mode="Multiply")
        node = self.sim.layers[0]
        self.assertTrue(node.modes)
        self.assertTrue(all(mode == BlendingMode.Multiply for mode in node.modes.values()))


class RenameTests(Fixture):
    def names(self):
        return [layer.get_name() for layer in self.sim.layers]

    def test_rename_cannot_duplicate_a_sibling_name(self):
        self.edit("group", name="A")
        second = self.edit("group", name="B")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.edit("set_properties", uid=second["uid"], name="A")
        self.assertEqual(sorted(self.names()), ["A", "B"])

    def test_rename_to_the_same_or_a_new_name_is_allowed(self):
        self.edit("group", name="A")
        second = self.edit("group", name="B")
        self.edit("set_properties", uid=second["uid"], name="B", opacity=0.5)
        self.edit("set_properties", uid=second["uid"], name="C")
        self.assertEqual(sorted(self.names()), ["A", "C"])

    def test_uniqueness_is_per_parent(self):
        outer = self.edit("group", name="Outer")
        inner = self.edit("group", name="Inner", parent_uid=outer["uid"])
        self.edit("group", name="Same")
        self.edit("set_properties", uid=inner["uid"], name="Same")
        sibling = self.edit("group", name="Other", parent_uid=outer["uid"])
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.edit("set_properties", uid=sibling["uid"], name="Same")

    def test_failed_rename_changes_nothing_else(self):
        self.edit("group", name="A")
        second = self.edit("group", name="B")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.edit("set_properties", uid=second["uid"], name="A", visible=False,
                      opacity=0.25)
        node = next(layer for layer in self.sim.layers if layer.get_name() == "B")
        self.assertTrue(node.is_visible())
        self.assertEqual(node.opacities, {})


class DeleteTests(Fixture):
    def human_adds_layer(self, group_uid, name="Human layer"):
        """A layer added in the Painter UI, not through this lease."""
        layerstack = self.sim.module.layerstack
        group = layerstack.get_node_by_uid(group_uid)
        layer = layerstack.insert_fill(layerstack.InsertPosition.inside_node(
            group, layerstack.NodeStack.Substack))
        layer.set_name(name)
        return layer

    def fill(self, name, **params):
        return self.client.call("create_fill_layer", {
            "texture_set": "Body", "name": name, "base_color": [0.1, 0.2, 0.3], **params})

    def created_uids(self):
        return set(vars(self.sim.module).get(ops.CREATED_ATTR, {}).get("uids", ()))

    def test_group_that_gained_a_foreign_child_needs_confirmation(self):
        group = self.edit("group", name="Mine")
        foreign = self.human_adds_layer(group["uid"])
        with self.assertRaisesRegex(RuntimeError, "confirm_name"):
            self.edit("delete", uid=group["uid"])
        with self.assertRaisesRegex(RuntimeError, "confirm_name"):
            self.edit("delete", uid=group["uid"], confirm_name="Wrong")
        self.assertEqual(len(self.sim.layers), 1)
        self.assertIn(foreign.uid(), self.sim.nodes)
        deleted = self.edit("delete", uid=group["uid"], confirm_name="Mine")
        self.assertEqual(deleted["deleted"], group["uid"])
        self.assertEqual(self.sim.layers, [])
        self.assertNotIn(foreign.uid(), self.sim.nodes)

    def test_group_built_entirely_by_this_lease_deletes_without_confirmation(self):
        group = self.edit("group", name="Mine")
        child = self.edit("group", name="Inner", parent_uid=group["uid"])
        self.fill("Leaf", parent_uid=child["uid"])
        self.edit("delete", uid=group["uid"])
        self.assertEqual(self.sim.layers, [])
        self.assertEqual(self.sim.nodes, {})

    def test_smart_material_this_lease_inserted_deletes_without_confirmation(self):
        material = self.edit("smart_material", name="Steel", resource="Steel Painted Worn")
        self.assertTrue(material["layers"])
        self.edit("delete", uid=material["uid"])
        self.assertEqual(self.sim.nodes, {})

    def test_foreign_effect_on_an_owned_layer_needs_confirmation(self):
        fill = self.client.call("create_fill_layer", {"texture_set": "Body", "name": "Base",
                                                      "base_color": [0.5, 0.5, 0.5]})
        layerstack = self.sim.module.layerstack
        layer = layerstack.get_node_by_uid(fill["uid"])
        layer.add_mask(layerstack.MaskBackground.Black)
        position = layerstack.InsertPosition.inside_node(layer, layerstack.NodeStack.Mask)
        layerstack.insert_generator_effect(position)
        with self.assertRaisesRegex(RuntimeError, "confirm_name"):
            self.edit("delete", uid=fill["uid"])
        self.edit("delete", uid=fill["uid"], confirm_name="Base")
        self.assertEqual(self.sim.nodes, {})

    def test_deleted_uids_are_forgotten_by_the_lease(self):
        group = self.edit("group", name="Mine")
        child = self.fill("Leaf", parent_uid=group["uid"])
        keep = self.edit("group", name="Keep")
        self.assertEqual(self.created_uids(), {group["uid"], child["uid"], keep["uid"]})
        self.edit("delete", uid=group["uid"])
        self.assertEqual(self.created_uids(), {keep["uid"]})

    def test_confirmed_delete_of_a_foreign_node_also_forgets_its_uids(self):
        group = self.edit("group", name="Mine")
        foreign = self.human_adds_layer(group["uid"])
        self.edit("delete", uid=group["uid"], confirm_name="Mine")
        self.assertNotIn(group["uid"], self.created_uids())
        self.assertNotIn(foreign.uid(), self.created_uids())


if __name__ == "__main__":
    unittest.main()
