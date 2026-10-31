import asyncio
import unittest

from cavi_substance_mcp.core.mcp import DESTRUCTIVE, READ_ONLY, WRITES, need, reject_foreign
from cavi_substance_mcp.designer import server as designer_server
from cavi_substance_mcp.painter import server as painter_server
from cavi_substance_mcp.sampler import server as sampler_server

# What each tool promises its caller. A change here is a contract change.
EXPECTED = {
    painter_server.mcp: {
        "painter_status": READ_ONLY, "inspect": READ_ONLY,
        "session_control": WRITES, "export_textures": WRITES, "bake": WRITES,
        "project": DESTRUCTIVE, "layer_edit": DESTRUCTIVE,
    },
    designer_server.mcp: {
        "designer_status": READ_ONLY, "material": WRITES, "bake": WRITES,
    },
    sampler_server.mcp: {
        "sampler_status": READ_ONLY, "sampler_inspect": READ_ONLY,
        "sampler_project": WRITES, "sampler_export": WRITES, "sampler_build": WRITES,
        "sampler_pack": WRITES, "sampler_asset": DESTRUCTIVE, "sampler_layer": DESTRUCTIVE,
    },
}


class NeedTests(unittest.TestCase):
    def test_returns_a_value_that_was_passed(self):
        self.assertEqual(need("a.png", "path", "info"), "a.png")
        for value in (0, False, "", []):
            self.assertIs(need(value, "path", "info"), value)

    def test_names_the_argument_and_the_action(self):
        with self.assertRaisesRegex(ValueError, r"^path is required for action=info$"):
            need(None, "path", "info")


class RejectForeignTests(unittest.TestCase):
    def test_accepts_allowed_and_unset_arguments(self):
        reject_foreign("info", {"path": "a.sbsar", "graph": None, "directory": None}, {"path"})
        reject_foreign("info", {}, set())

    def test_falsy_values_still_count_as_passed(self):
        with self.assertRaisesRegex(ValueError, r"^Not valid for action=info: count, flag$"):
            reject_foreign("info", {"flag": False, "count": 0, "path": "a"}, {"path"})

    def test_lists_every_foreign_name_sorted(self):
        with self.assertRaises(ValueError) as caught:
            reject_foreign("cook", {"z": 1, "b": 2, "path": "p", "a": 3}, {"path"})
        self.assertEqual(str(caught.exception), "Not valid for action=cook: a, b, z")


class AnnotationTests(unittest.TestCase):
    def test_the_constants_state_what_the_tools_promise(self):
        self.assertEqual(READ_ONLY.model_dump(exclude_none=True), {
            "read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
            "open_world_hint": False,
        })
        self.assertEqual(WRITES.model_dump(exclude_none=True), {
            "read_only_hint": False, "destructive_hint": False, "open_world_hint": False,
        })
        self.assertEqual(DESTRUCTIVE.model_dump(exclude_none=True), {
            "read_only_hint": False, "destructive_hint": True, "open_world_hint": False,
        })

    def test_every_tool_keeps_its_annotation(self):
        for app, expected in EXPECTED.items():
            with self.subTest(server=app.name):
                tools = {tool.name: tool.annotations for tool in asyncio.run(app.list_tools())}
                self.assertEqual(tools, expected)


if __name__ == "__main__":
    unittest.main()
