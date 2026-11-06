import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from env import FAKE_SAMPLER, patched_environment
from sampler_fake_cli import ENV, install
from sampler_jobs import job_data

from cavi_substance_mcp.sampler import server

TIMEOUT = 30


class ServerContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "jobs").mkdir(parents=True)
        (self.workspace / "photos").mkdir()
        (self.workspace / "photos/albedo.png").write_bytes(b"fake-png")
        (self.workspace / "jobs/rock.json").write_text(json.dumps(job_data()))
        app = install(root)
        self.env = patched_environment(FAKE_SAMPLER, {
            ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def run_tool(self, tool, **arguments):
        return asyncio.run(tool(**arguments))

    def test_build_defaults_to_a_dry_run(self):
        schema = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        self.assertIs(schema["sampler_build"].input_schema["properties"]["dry_run"]["default"],
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

    def project(self):
        asyncio.run(server.sampler_project(
            action="create", directory="projects", name="Rock", timeout_seconds=TIMEOUT))
        return "projects/Rock.ssa"

    def test_inspect_project_requires_a_project(self):
        with self.assertRaisesRegex(ValueError, "project is required for action=project"):
            self.run_tool(server.sampler_inspect, action="project")
        with self.assertRaisesRegex(ValueError, "project is required for action=asset"):
            self.run_tool(server.sampler_inspect, action="asset", asset="Rock")
        listed = self.run_tool(
            server.sampler_inspect, action="project", project=self.project())
        self.assertEqual(listed["assets"][0]["name"], "Rock")
        self.assertEqual(self.run_tool(server.sampler_inspect, action="catalog")["filters"][:1],
                         ["Brightness/Contrast"])

    def test_inspection_schema_and_stdio_dispatch_agree(self):
        project = self.project()

        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_substance_mcp.sampler.server"],
                env={ENV: os.environ[ENV],
                     "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
            async with stdio_client(params) as (reader, writer), \
                    ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                validator = Draft202012Validator(tools["sampler_inspect"].input_schema)
                discovery = tools["sampler_inspect"].input_schema["properties"]["action"]["enum"][0]
                self.assertEqual(discovery, "catalog")
                for arguments in ({"action": discovery},
                                  {"action": "project", "project": project},
                                  {"action": "asset", "project": project, "asset": "Rock"}):
                    with self.subTest(arguments=arguments):
                        validator.validate(arguments)
                        result = await client.call_tool("sampler_inspect", arguments)
                        self.assertFalse(result.is_error, str(result.content))
                for arguments in ({"action": "project"},
                                  {"action": "asset", "project": project}):
                    with self.subTest(arguments=arguments):
                        self.assertFalse(validator.is_valid(arguments))
                        result = await client.call_tool("sampler_inspect", arguments)
                        self.assertTrue(result.is_error)
                        error = result.structured_content["error"]
                        self.assertEqual(error["code"], "INVALID_ARGUMENT")
                        self.assertEqual(error["outcome"], "rejected")
                        self.assertFalse(error["retry_safe"])

        asyncio.run(exercise())

    def test_export_target_rejects_a_resource_format(self):
        project = self.project()
        for fmt in ("sbsar", "sbs"):
            with self.subTest(format=fmt), self.assertRaisesRegex(ValueError, "target exports"):
                self.run_tool(server.sampler_export, project=project, directory="out",
                              target="unreal", format=fmt)
        plan = self.run_tool(server.sampler_export, project=project, directory="out",
                             target="unreal")
        self.assertEqual(plan["format"], "png")
        plan = self.run_tool(server.sampler_export, project=project, directory="out",
                             target="unreal", format="tga")
        self.assertEqual(plan["format"], "tga")
        plan = self.run_tool(server.sampler_export, project=project, directory="out")
        self.assertEqual(plan["format"], "sbsar")


if __name__ == "__main__":
    unittest.main()
