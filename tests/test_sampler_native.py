import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_substance_mcp.sampler import operations, runner


class NativeSamplerTests(unittest.TestCase):
    def setUp(self):
        if runner.installation().executable is None:
            self.skipTest("Substance 3D Sampler is not installed")

    def test_status(self):
        status = operations.status()
        self.assertTrue(status["available"])
        self.assertTrue(Path(status["executable"]).is_file())

    def test_create_and_inspect(self):
        if not os.environ.get("CAVI_SAMPLER_NATIVE"):
            self.skipTest("set CAVI_SAMPLER_NATIVE=1 to launch Sampler")
        if runner.running():
            self.skipTest("Sampler is already running; quit it before the native test")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": folder}),
        ):
            created = asyncio.run(operations.invoke(
                "create_project", {"directory": "projects", "name": "Cavi"}, 300
            ))
            self.assertEqual(created["project"], "projects/Cavi.ssa")
            inspected = asyncio.run(operations.invoke(
                "inspect_project", {"project": "projects/Cavi.ssa"}, 300
            ))
            self.assertEqual(inspected["project"], "projects/Cavi.ssa")


if __name__ == "__main__":
    unittest.main()
