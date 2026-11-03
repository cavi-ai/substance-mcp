"""Validation cases for Sampler's Cloth Weave parameter metadata."""

import unittest
from types import SimpleNamespace

from cavi_substance_mcp.sampler.operations import _parameter_value


def parameter(kind, widget, low, high, choices=()):
    return SimpleNamespace(
        identifier="cloth_parameter", type=kind, widget_type=widget,
        min_value=low, max_value=high, enum_values=lambda: list(choices),
    )


class SamplerParameterValuesTests(unittest.TestCase):
    def test_color_accepts_finite_components_with_zero_placeholder_bounds(self):
        color = parameter("float3", "color", [0, 0, 0], [0, 0, 0])
        self.assertEqual(_parameter_value(color, [.9, .89, .87]), [.9, .89, .87])

    def test_color_preserves_real_component_bounds(self):
        color = parameter("float3", "color", [0, 0, 0], [1, 1, 1])
        with self.assertRaisesRegex(ValueError, "above"):
            _parameter_value(color, [.9, 1.2, .87])

    def test_color_rejects_nonfinite_components(self):
        color = parameter("float3", "color", [0, 0, 0], [0, 0, 0])
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                _parameter_value(color, [.9, value, .87])

    def test_combobox_accepts_declared_value_despite_zero_placeholder_bounds(self):
        choices = [("Unicolor", 1), ("Bicolor", 2), ("None", 3)]
        combo = parameter("integer", "combobox", 0, 0, choices)
        self.assertEqual(_parameter_value(combo, 3), 3)
        self.assertEqual(_parameter_value(combo, "None"), 3)

    def test_combobox_rejects_undeclared_numeric_value(self):
        combo = parameter("integer", "combobox", 0, 10, [("Off", 0), ("On", 1)])
        with self.assertRaisesRegex(ValueError, "choice"):
            _parameter_value(combo, 2)

    def test_toggle_accepts_only_zero_and_one_with_placeholder_bounds(self):
        toggle = parameter("integer", "toggle_button", 0, 0)
        self.assertEqual(_parameter_value(toggle, True), 1)
        self.assertEqual(_parameter_value(toggle, False), 0)
        with self.assertRaisesRegex(ValueError, "zero or one"):
            _parameter_value(toggle, 2)

    def test_zero_bound_on_a_numeric_slider_is_still_enforced(self):
        slider = parameter("float", "slider", 0, 0)
        with self.assertRaisesRegex(ValueError, "above"):
            _parameter_value(slider, .5)


if __name__ == "__main__":
    unittest.main()
