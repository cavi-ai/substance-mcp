"""Designer graph requests must be typed and validated before native execution."""

import unittest

from pydantic import ValidationError

from cavi_substance_mcp.designer.graph_models import GraphRecipe


class RecipeTests(unittest.TestCase):
    def test_new_graph_defaults_to_float_precision(self):
        recipe = GraphRecipe.model_validate({'identifier': 'PaintedMetal'})
        self.assertEqual(recipe.precision, '16f')

    def test_connections_and_property_edits_are_structured(self):
        recipe = GraphRecipe.model_validate({'identifier': 'Material', 'operations': [
            {'action': 'add_node', 'key': 'color', 'definition': 'sbs::compositing::uniform',
             'values': {'outputcolor': [0.1, 0.3, 0.6, 1.0]}},
            {'action': 'output', 'key': 'out', 'source': 'color',
             'source_port': 'unique_filter_output', 'identifier': 'BaseColor',
             'usage': 'baseColor'},
        ]})
        self.assertEqual(recipe.operations[0].key, 'color')

    def test_invalid_operations_are_rejected_before_designer_starts(self):
        for operation in ({'action': 'execute', 'python': 'anything'},
                          {'action': 'add_node', 'key': 'x'},
                          {'action': 'connect', 'source': 'x'},
                          {'action': 'set', 'node': 'x', 'values': {'roughness': float('nan')}},
                          {'action': 'remove_node', 'node': '../file'}):
            with self.subTest(operation=str(operation)), self.assertRaises(ValidationError):
                GraphRecipe.model_validate({'identifier': 'Material', 'operations': [operation]})
