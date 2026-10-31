import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import install

from cavi_substance_mcp.sampler import server

TIMEOUT = 30
JOB = {
    "schema": 1,
    "directory": "jobs/rock",
    "project": {"name": "RockProject", "asset": {"name": "Rock", "model": "openpbr"}},
    "steps": [
        {"action": "insert_filter", "asset": "Rock", "filter": "Tiling", "position": 0},
        {"action": "export", "asset": "Rock", "outputs": ["sbsar"]},
    ],
}


class ServerContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "jobs").mkdir(parents=True)
        (self.workspace / "jobs/rock.json").write_text(json.dumps(JOB))
        app = install(root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_", "FAKE_SAMPLER"))}
        env.update({"CAVI_SAMPLER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def run_tool(self, tool, **arguments):
        return asyncio.run(tool(**arguments))

    def test_build_defaults_to_a_dry_run(self):
        schema = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        self.assertIs(schema["sampler_build"].inputSchema["properties"]["dry_run"]["default"],
                      True)
        plan = self.run_tool(server.sampler_build, job="jobs/rock.json")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["directory"], "jobs/rock")
        self.assertFalse((self.workspace / "jobs/rock").exists())

    def test_build_writes_only_when_dry_run_is_explicitly_false(self):
        manifest = self.run_tool(server.sampler_build, job="jobs/rock.json", dry_run=False)
        self.assertEqual(manifest["status"], "success")
        self.assertTrue((self.workspace / "jobs/rock/manifest.json").is_file())

    def test_checks_work_without_naming_dry_run(self):
        plan = self.run_tool(server.sampler_build, job="jobs/rock.json", check_sampler=True)
        self.assertTrue(plan["dry_run"])
        self.assertIn("sampler", plan)
        self.assertFalse((self.workspace / "jobs/rock").exists())
        with self.assertRaisesRegex(ValueError, "require dry_run=true"):
            self.run_tool(server.sampler_build, job="jobs/rock.json", dry_run=False,
                          check_sampler=True)


if __name__ == "__main__":
    unittest.main()
