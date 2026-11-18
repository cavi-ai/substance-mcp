"""Tool-contract checks for the Painter MCP server's input schemas."""

import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from cavi_substance_mcp.painter import server
from cavi_substance_mcp.painter.client import PainterClient


class LeaseIdContractTests(unittest.IsolatedAsyncioTestCase):
    async def lease_schemas(self):
        found = {}
        for tool in await server.mcp.list_tools():
            schema = tool.input_schema["properties"].get("lease_id")
            if schema is not None:
                found[tool.name] = schema
        return found

    async def test_every_lease_id_parameter_is_a_bounded_token_pattern(self):
        schemas = await self.lease_schemas()
        self.assertGreaterEqual(len(schemas), 5)
        for name, schema in schemas.items():
            branch = (next(item for item in schema["anyOf"] if item.get("type") == "string")
                      if "anyOf" in schema else schema)
            self.assertEqual(branch["pattern"], "^[A-Za-z0-9_-]+$", name)
            self.assertEqual(branch["maxLength"], 128, name)
            validator = Draft202012Validator(schema)
            for valid in (None, "Abc-_123", "x" * 128):
                self.assertTrue(validator.is_valid(valid), (name, valid))
            for invalid in ("töken", "has space", "semi;colon", "x" * 129, "", 1, True):
                self.assertFalse(validator.is_valid(invalid), (name, invalid))

    async def test_malformed_lease_ids_are_rejected_before_reaching_painter(self):
        reached = []
        with patch.object(PainterClient, "call", lambda *a, **k: reached.append(a) or {
                "saved": True, "file": "prop.spp", "needs_saving": False}):
            for value in ("töken", "has space", "semi;colon", "x" * 129, ""):
                with self.subTest(value=value), self.assertRaisesRegex(ToolError, "lease_id"):
                    await server.mcp.call_tool("project", {"action": "save", "lease_id": value})
            await server.mcp.call_tool("project", {"action": "save", "lease_id": "Abc-_123"})
        self.assertEqual(len(reached), 1)


class BakeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_bake_offers_start_status_and_cancel(self):
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}
        self.assertEqual(tools["bake"].input_schema["properties"]["action"]["enum"],
                         ["start", "status", "cancel"])

    async def test_cancel_forwards_the_lease_to_the_bake_cancel_operation(self):
        calls = []

        def spy(self, operation, params=None, *, lease_id=None, timeout=None, cancelled=None):
            calls.append((operation, params, lease_id))
            return {"cancel_requested": True, "running": True,
                    "next": "bake action=status until busy is false"}

        with patch.object(PainterClient, "call", spy):
            await server.mcp.call_tool("bake", {"action": "cancel", "lease_id": "abc"})
        self.assertEqual(calls, [("bake_cancel", None, "abc")])


class ForeignArgumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_arguments_an_action_ignores_are_refused_before_painter(self):
        cases = [
            ("layer_edit", {"action": "delete", "uid": 3, "base_color": [0.1, 0.2, 0.3]},
             "delete: base_color"),
            ("layer_edit", {"action": "levels", "uid": 3, "resource": "Blur"},
             "levels: resource"),
            ("layer_edit", {"action": "fill", "name": "A", "base_color": [0, 0, 0], "uid": 3},
             "fill: uid"),
            ("layer_edit", {"action": "paint", "name": "A", "roughness": 0.2},
             "paint: roughness"),
            ("brush", {"action": "settings", "mode": "erase"}, "settings: mode"),
        ]
        reached = []
        with patch.object(PainterClient, "call", lambda *a, **k: reached.append(a) or {"uid": 1}):
            for name, arguments, foreign in cases:
                if name == "layer_edit":
                    arguments = {"texture_set": "Body", **arguments}
                with self.subTest(foreign=foreign), self.assertRaisesRegex(
                        ToolError, f"Not valid for action={foreign}$"):
                    await server.mcp.call_tool(name, arguments)
        self.assertEqual(reached, [])


class ProjectRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_and_settings_actions_reach_their_painter_operations(self):
        calls = []
        replies = {"display": {"environment": None, "color_lut": None, "tone_mapping": "ACES"},
                   "update_resources": {"updated": []},
                   "texture_set_settings": {"texture_sets": []}}

        def spy(self, operation, params=None, *, lease_id=None, timeout=None, cancelled=None):
            calls.append((operation, params))
            return replies.get(operation, {"path": "p", "bytes": 1, "manifest": "m"})

        requests = [
            ("project", {"action": "save_as", "directory": "saved"}),
            ("project", {"action": "save_template", "directory": "templates"}),
            ("project", {"action": "export_mesh", "directory": "mesh", "mesh_format": "gltf"}),
            ("project", {"action": "update_resources"}),
            ("project", {"action": "display", "display": {"tone_mapping": "ACES"}}),
            ("texture_set_settings", {"texture_set": "Body", "mesh_maps": {"ao": "ao.png"}}),
        ]
        with patch.object(PainterClient, "call", spy):
            for name, arguments in requests:
                await server.mcp.call_tool(name, {**arguments, "lease_id": "abc"})
        self.assertEqual(calls, [
            ("save_as", {"directory": "saved"}),
            ("save_template", {"directory": "templates"}),
            ("export_mesh", {"directory": "mesh", "format": "gltf"}),
            ("update_resources", None),
            ("display", {"display": {"tone_mapping": "ACES"}}),
            ("texture_set_settings", {"texture_set": "Body", "channel_formats": None,
                                      "resolution": None, "mesh_maps": {"ao": "ao.png"}}),
        ])


if __name__ == "__main__":
    unittest.main()
