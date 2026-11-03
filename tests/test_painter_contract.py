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


if __name__ == "__main__":
    unittest.main()
