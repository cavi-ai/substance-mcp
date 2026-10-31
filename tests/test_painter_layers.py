"""Layer-edit validation that must hold against the simulated Painter API."""

import unittest

from test_texturing import Fixture

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


if __name__ == "__main__":
    unittest.main()
