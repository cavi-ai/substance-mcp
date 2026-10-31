"""Tool-contract checks for the Painter MCP server's input schemas."""

import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from cavi_substance_mcp import server
from cavi_substance_mcp.client import PainterClient


class LeaseIdContractTests(unittest.IsolatedAsyncioTestCase):
    def lease_schemas(self):
        found = {}
        for tool in server.mcp._tool_manager.list_tools():
            schema = tool.parameters["properties"].get("lease_id")
            if schema is not None:
                found[tool.name] = schema
        return found

    def test_every_lease_id_parameter_is_a_bounded_token_pattern(self):
        schemas = self.lease_schemas()
        self.assertGreaterEqual(len(schemas), 5)
        for name, schema in schemas.items():
            branch = next(item for item in schema["anyOf"] if item.get("type") == "string")
            self.assertEqual(branch["pattern"], "^[A-Za-z0-9_-]+$", name)
            self.assertEqual(branch["maxLength"], 128, name)

    async def test_malformed_lease_ids_are_rejected_before_reaching_painter(self):
        reached = []
        with patch.object(PainterClient, "call", lambda *a, **k: reached.append(a) or {}):
            for value in ("töken", "has space", "semi;colon", "x" * 129, ""):
                with self.subTest(value=value), self.assertRaisesRegex(ToolError, "lease_id"):
                    await server.mcp.call_tool("project", {"action": "save", "lease_id": value})
            await server.mcp.call_tool("project", {"action": "save", "lease_id": "Abc-_123"})
        self.assertEqual(len(reached), 1)


class BakeContractTests(unittest.IsolatedAsyncioTestCase):
    def test_bake_offers_start_status_and_cancel(self):
        tool = server.mcp._tool_manager.get_tool("bake")
        self.assertEqual(tool.parameters["properties"]["action"]["enum"],
                         ["start", "status", "cancel"])

    async def test_cancel_forwards_the_lease_to_the_bake_cancel_operation(self):
        calls = []

        def spy(self, operation, params=None, *, lease_id=None, timeout=None):
            calls.append((operation, params, lease_id))
            return {"cancel_requested": True}

        with patch.object(PainterClient, "call", spy):
            await server.mcp.call_tool("bake", {"action": "cancel", "lease_id": "abc"})
        self.assertEqual(calls, [("bake_cancel", None, "abc")])


if __name__ == "__main__":
    unittest.main()
