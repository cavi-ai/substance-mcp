import unittest

from cavi_mcp_core.schema import compact_schema
from jsonschema import Draft202012Validator

from cavi_substance_mcp.core.contracts import PainterLayerResult


class FillAdvertisedContractTests(unittest.TestCase):
    def test_fill_fields_and_other_action_shapes(self):
        schema = PainterLayerResult.model_json_schema()
        compact = compact_schema(schema, output=True)
        fill = {'uid': 1, 'name': 'Fill', 'texture_set': 'Body',
                'base_color': [0.1, 0.2, 0.3], 'roughness': 0.5, 'metallic': 0.0}
        for candidate in (schema, compact):
            for field in ('base_color', 'roughness', 'metallic'):
                self.assertIn(field, candidate['properties'])
            for record in (fill, {'deleted': 1, 'name': 'Fill'}, {'uid': 1, 'mask': 'black'}):
                Draft202012Validator(candidate).validate(record)
            invalid = {**fill, 'roughness': 'invalid'}
            self.assertFalse(Draft202012Validator(candidate).is_valid(invalid))
        self.assertEqual(PainterLayerResult.model_validate(fill).model_dump(mode='json'), fill)

    def test_projection_rides_on_the_uid_shape(self):
        schema = PainterLayerResult.model_json_schema()
        self.assertIn("projection", schema["properties"])
        self.assertIn(("uid",), PainterLayerResult.shapes)
        placed = {"uid": 1, "projection": {
            "mode": "UV", "uv_transformation": {"scale": [2.0, 3.0]}}}
        self.assertEqual(
            PainterLayerResult.model_validate(placed).model_dump(mode="json"), placed)
        self.assertEqual(
            PainterLayerResult.model_validate({"uid": 1}).model_dump(mode="json"), {"uid": 1})
