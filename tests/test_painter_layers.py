"""Layer-edit validation that must hold against the simulated Painter API."""

import unittest

from test_texturing import Fixture

from evals.painter_simulator import BlendingMode


class BlendModeTests(Fixture):
    def test_non_member_attributes_are_not_blend_modes(self):
        group = self.edit("group", name="Wear")
        node = self.sim.layers[0]
        for mode in ("mro", "__members__", "_value2member_map_", "multiply", "", "Multiply "):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "Unknown blend_mode"):
                self.edit("set_properties", uid=group["uid"], blend_mode=mode)
            self.assertEqual(node.modes, {})

    def test_real_members_still_apply(self):
        group = self.edit("group", name="Wear")
        self.edit("set_properties", uid=group["uid"], blend_mode="Multiply")
        node = self.sim.layers[0]
        self.assertTrue(node.modes)
        self.assertTrue(all(mode == BlendingMode.Multiply for mode in node.modes.values()))


if __name__ == "__main__":
    unittest.main()
