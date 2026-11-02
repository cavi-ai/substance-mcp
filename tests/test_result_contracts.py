import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ResultContractTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_compaction_preserves_property_names_and_literal_values(self):
        from cavi_substance_mcp.core.contracts import compact_schema

        schema = {"title": "Arguments",
                  "properties": {"title": {"title": "Title", "type": "string"}},
                  "const": {"title": "literal"}, "default": {"title": "default"}}
        compact = compact_schema(schema)
        self.assertIn("title", compact["properties"])
        self.assertEqual(compact["const"], schema["const"])
        self.assertEqual(compact["default"], schema["default"])

    async def test_catalog_omits_redundant_field_titles(self):
        from cavi_substance_mcp.painter import server

        tools = await server.mcp.list_tools()
        for tool in tools:
            for field in tool.input_schema.get("properties", {}).values():
                self.assertNotIn("title", field)

    async def test_real_stdio_contracts_and_errors(self):
        for module, status in (("painter", "painter_status"), ("designer", "designer_status"),
                               ("sampler", "sampler_status")):
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_substance_mcp." + module + ".server"])
            async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
                await client.initialize()
                tools = {t.name: t for t in (await client.list_tools()).tools}
                self.assertTrue(tools[status].output_schema.get("properties"))
                if module == "painter":
                    for tool in ("session_control", "export_textures"):
                        self.assertTrue(tools[tool].output_schema.get("properties"))
                if module == "designer":
                    self.assertTrue(tools["bake"].output_schema.get("required"))
                failed = await client.call_tool("not_a_tool", {})
                self.assertTrue(failed.is_error)
                self.assertFalse(failed.structured_content["error"]["retry_safe"])

    def test_status_contracts_preserve_unavailable_reason(self):
        from cavi_substance_mcp.core.contracts import PainterStatus, SamplerStatus

        state = {"connected": False, "reason": "Start remote scripting"}
        self.assertEqual(PainterStatus.model_validate(state).model_dump(), state)
        with self.assertRaises(ValueError):
            PainterStatus.model_validate({"connected": "yes"})
        with self.assertRaises(ValueError):
            SamplerStatus.model_validate({"available": True})

    def test_bake_maps_are_named_workspace_paths(self):
        from cavi_substance_mcp.core.contracts import BakeResult

        result = {"directory": "bake", "maps": {"normal": "bake/normal.png"},
                  "manifest": "bake/manifest.json"}
        self.assertEqual(BakeResult.model_validate(result).model_dump(), result)

    def test_unknown_outcome_is_not_retryable(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from cavi_substance_mcp.core.contracts import failure

        error = ToolError("Inspect outputs")
        error.__cause__ = TimeoutError("Native request timed out")
        result = failure("export_textures", error)
        self.assertEqual(result.structured_content["error"]["outcome"], "unknown")
        self.assertFalse(result.structured_content["error"]["retry_safe"])
