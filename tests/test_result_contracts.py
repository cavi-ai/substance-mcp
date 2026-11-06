import itertools
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ResultContractTests(unittest.IsolatedAsyncioTestCase):
    def test_common_required_fields_preserve_sampler_response_acceptance(self):
        from jsonschema import Draft202012Validator
        from pydantic import ValidationError

        from cavi_substance_mcp.core.contracts import SamplerBuildResult, SamplerLayerResult

        models = [
            (SamplerLayerResult,
             (("project", "asset", "layer", "saved"),
              ("project", "asset", "removed", "position", "saved")),
             [{"project": "rock.ssa", "asset": None, "layer": None, "saved": True},
              {"project": "rock.ssa", "asset": "Rock", "removed": "Layer",
               "position": 0, "saved": True}]),
            (SamplerBuildResult,
             (("directory", "project", "assets", "inputs", "steps", "job", "source", "dry_run"),
              ("status", "job", "job_sha256", "project", "source", "assets", "steps", "inputs",
               "outputs", "schema", "app", "operation")),
             [{"directory": "out", "project": None, "assets": [], "inputs": [], "steps": [],
               "job": "job.json", "source": None, "dry_run": True},
              {"status": "success", "job": "job.json", "job_sha256": "digest",
               "project": "rock.ssa", "source": None, "assets": [], "steps": [], "inputs": [],
               "outputs": [], "schema": 1, "app": "sampler", "operation": "build"}]),
        ]
        for model, previous_shapes, examples in models:
            current = model.model_json_schema()
            previous = {**current, "anyOf": [{"required": list(s)} for s in previous_shapes]}
            previous.pop("required", None)
            old_validator, new_validator = map(Draft202012Validator, (previous, current))
            for example in examples:
                keys = list(example)
                # Exercise every missing-field combination, plus null at every field.
                candidates = [
                    {key: example[key] for key, keep in zip(keys, mask, strict=True) if keep}
                    for mask in itertools.product((False, True), repeat=len(keys))
                ]
                candidates.extend({**example, key: None} for key in keys)
                for value in candidates:
                    with self.subTest(model=model.__name__, fields=list(value)):
                        expected = old_validator.is_valid(value)
                        self.assertEqual(new_validator.is_valid(value), expected)
                        try:
                            model.model_validate(value)
                            accepted = True
                        except ValidationError:
                            accepted = False
                        self.assertEqual(accepted, expected)

    def test_nested_decode_failure_keeps_transport_outcome_unknown(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from cavi_substance_mcp.core.contracts import failure

        transport = RuntimeError("Invalid response; inspect Painter")
        transport.__cause__ = ValueError("Malformed response JSON")
        wrapper = ToolError("Inspect outputs")
        wrapper.__cause__ = transport
        result = failure("export", wrapper)
        self.assertEqual(result.structured_content["error"]["outcome"], "unknown")
        self.assertEqual(result.structured_content["error"]["next_action"],
                         "inspect_application_and_outputs")

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
                for name, tool in tools.items():
                    self.assertTrue(tool.output_schema.get("properties"), name)
                self.assertTrue(tools[status].output_schema.get("properties"))
                if module == "painter":
                    for tool in ("session_control", "export_textures"):
                        self.assertTrue(tools[tool].output_schema.get("properties"))
                if module == "designer":
                    self.assertTrue(tools["bake"].output_schema.get("required"))
                failed = await client.call_tool("not_a_tool", {})
                self.assertTrue(failed.is_error)
                self.assertFalse(failed.structured_content["error"]["retry_safe"])
                self.assertEqual(failed.structured_content["error"]["code"], "TOOL_NOT_FOUND")
                self.assertEqual(failed.structured_content["error"]["outcome"], "rejected")
                self.assertEqual(failed.structured_content["error"]["next_action"], "list_tools")
                self.assertIs(tools[status].input_schema["additionalProperties"], False)
                unexpected = await client.call_tool(status, {"unexpected": True})
                self.assertTrue(unexpected.is_error)
                self.assertEqual(unexpected.structured_content["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(unexpected.structured_content["error"]["outcome"], "rejected")
                self.assertFalse(unexpected.structured_content["error"]["retry_safe"])
                if module == "designer":
                    for _ in range(2):
                        rejected = await client.call_tool("material", {"action": "info"})
                        self.assertTrue(rejected.is_error)
                        error = rejected.structured_content["error"]
                        self.assertEqual(error["code"], "INVALID_ARGUMENT")
                        self.assertEqual(error["outcome"], "rejected")
                        self.assertFalse(error["retry_safe"])
                    self.assertFalse((await client.call_tool(status, {})).is_error)

    async def test_known_tool_error_text_does_not_imply_missing_dispatch(self):
        from cavi_mcp_core.server import Server
        from mcp.server.mcpserver import Context
        from mcp.server.mcpserver.exceptions import ToolError

        server = Server("dispatch-test")

        @server.tool()
        async def existing() -> dict:
            raise ToolError("Unknown tool: inside native application")

        result = await server.call_tool("existing", {}, Context(mcp_server=server))
        self.assertEqual(result.structured_content["error"]["code"], "OUTCOME_UNKNOWN")
        self.assertEqual(result.structured_content["error"]["outcome"], "unknown")

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


class OutputFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_validation_keeps_mutation_outcome_unknown(self):
        from cavi_mcp_core.server import Server
        from mcp.server.mcpserver import Context

        from cavi_substance_mcp.core.contracts import Result

        class Receipt(Result):
            path: str

        calls = []
        app = Server("output-test")

        @app.tool()
        async def mutate() -> Receipt:
            calls.append(1)
            return {"path": 1, "private_detail": "hidden"}

        with self.assertLogs(level="ERROR"):
            reply = await app.call_tool("mutate", {}, Context(mcp_server=app))
        self.assertEqual(calls, [1])
        self.assertTrue(reply.is_error)
        self.assertEqual(reply.structured_content["error"]["code"], "OUTCOME_UNKNOWN")
        self.assertFalse(reply.structured_content["error"]["retry_safe"])
        self.assertNotIn("private_detail", str(reply.content))

    def test_compact_unions_preserve_acceptance_and_nonnull_defaults(self):
        from jsonschema import Draft202012Validator

        from cavi_substance_mcp.core.contracts import compact_schema

        schemas = [
            {"anyOf": [{"type": "integer"}, {"type": "number"}, {"type": "null"}],
             "default": None},
            {"anyOf": [{"type": "string", "minLength": 2}, {"type": "null"}]},
            {"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]},
            {"anyOf": [{"type": "boolean", "const": True}, {"type": "null"}]},
            {"type": "object", "additionalProperties": True,
             "properties": {"title": {"type": "string", "default": "title"}}},
        ]
        for schema in schemas:
            for output in [False, True]:
                compact = compact_schema(schema, output=output)
                if not output and schema.get("default") is not None:
                    self.assertEqual(compact["default"], schema["default"])
                elif schema.get("default") is None:
                    self.assertNotIn("default", compact)
                for value in [None, True, False, 1, 1.5, "a", "ab", "b", [], {},
                              {"title": "x", "extra": 1}, {"title": 1}]:
                    self.assertEqual(Draft202012Validator(schema).is_valid(value),
                                     Draft202012Validator(compact).is_valid(value))
