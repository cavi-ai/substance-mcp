import asyncio
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from cavi_substance_mcp.core.mcp import Server
from cavi_substance_mcp.designer import server as designer_server
from cavi_substance_mcp.painter import server as painter_server
from cavi_substance_mcp.sampler import server as sampler_server


def call(app, name, arguments):
    return asyncio.run(app.call_tool(name, arguments))


class AnticipatedErrorTests(unittest.TestCase):
    """The MCP SDK hides an exception's text unless it is a ToolError."""

    def assert_reports(self, app, name, arguments, text):
        with self.assertRaises(ToolError) as caught:
            call(app, name, arguments)
        self.assertIn(text, str(caught.exception))

    def test_painter_validation_reaches_the_caller(self):
        self.assert_reports(painter_server.mcp, "inspect", {"action": "layer_stack"},
                            "texture_set is required for action=layer_stack")

    def test_designer_validation_reaches_the_caller(self):
        self.assert_reports(designer_server.mcp, "material",
                            {"action": "info", "directory": "out"},
                            "directory")

    def test_sampler_validation_reaches_the_caller(self):
        self.assert_reports(sampler_server.mcp, "sampler_inspect", {"action": "project"},
                            "project")

    def test_a_defect_keeps_its_details_on_the_server(self):
        app = Server("defect-test")

        @app.tool()
        async def broken() -> dict:
            raise TypeError("internal detail")

        with self.assertRaises(ToolError) as caught:
            call(app, "broken", {})
        self.assertNotIn("internal detail", str(caught.exception))

    def test_decorated_tools_stay_directly_callable(self):
        with self.assertRaisesRegex(ValueError, "texture_set is required"):
            asyncio.run(painter_server.inspect(action="layer_stack"))


if __name__ == "__main__":
    unittest.main()
