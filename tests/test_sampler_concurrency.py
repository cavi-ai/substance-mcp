import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import ENV, install

from cavi_substance_mcp.sampler import batch, launch

TIMEOUT = 60


class Launches:
    """Wraps runner.run_script to observe how many Sampler launches overlap."""

    def __init__(self, real, hold=0.0, gate=None):
        self.real, self.hold, self.gate = real, hold, gate
        self.active = self.peak = self.total = 0

    async def __call__(self, *args, **kwargs):
        self.total += 1
        first = self.total == 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None and first:
                await self.gate.wait()
            elif self.hold:
                await asyncio.sleep(self.hold)
            return await self.real(*args, **kwargs)
        finally:
            self.active -= 1


def job(directory, source=None):
    project = {"source": source, "name": "Copy"} if source else {
        "name": "RockProject", "asset": {"name": "Rock", "model": "openpbr"},
    }
    return {
        "schema": 1,
        "directory": directory,
        "project": project,
        "steps": [
            {"action": "insert_filter", "asset": "Rock", "filter": "Tiling", "position": 0},
            {"action": "export", "asset": "Rock", "outputs": ["sbsar"]},
        ],
    }


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        (self.workspace / "jobs").mkdir(parents=True)
        app = install(root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_"))}
        env.update({ENV: str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        created = asyncio.run(launch.invoke(
            "create_project", {"directory": "projects", "name": "Rock"}, TIMEOUT))
        self.project = created["project"]

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def write_job(self, name, data):
        (self.workspace / "jobs" / f"{name}.json").write_text(json.dumps(data))
        return f"jobs/{name}.json"

    def inspect(self, **options):
        return launch.invoke(
            "inspect_project", {"project": self.project}, TIMEOUT, **options)

    def test_two_single_operations_never_overlap(self):
        launches = Launches(launch.runner.run_script, hold=0.2)

        async def scenario():
            return await asyncio.gather(self.inspect(), self.inspect())

        with patch.object(launch.runner, "run_script", launches):
            results = asyncio.run(scenario())
        self.assertEqual([item["project"] for item in results], [self.project] * 2)
        self.assertEqual(launches.total, 2)
        self.assertEqual(launches.peak, 1)

    def test_single_operation_waits_for_a_running_batch(self):
        path = self.write_job("held", job("jobs/held"))
        gate = asyncio.Event()
        launches = Launches(launch.runner.run_script, gate=gate)

        async def scenario():
            running = asyncio.create_task(batch.run_job_file_async(
                path, self.workspace, TIMEOUT))
            for _ in range(100):
                if launches.active:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(launches.active, 1)
            with self.assertRaises(batch.LockTimeout):
                await self.inspect(lock_timeout=0.3)
            gate.set()
            manifest = await running
            return manifest, await self.inspect(lock_timeout=5)

        with patch.object(launch.runner, "run_script", launches):
            manifest, inspected = asyncio.run(scenario())
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(inspected["project"], self.project)
        self.assertEqual(launches.peak, 1)

    def test_batch_waits_for_a_running_single_operation(self):
        path = self.write_job("after", job("jobs/after"))
        gate = asyncio.Event()
        launches = Launches(launch.runner.run_script, gate=gate)

        async def scenario():
            single = asyncio.create_task(self.inspect())
            for _ in range(100):
                if launches.active:
                    break
                await asyncio.sleep(0.05)
            with self.assertRaises(batch.LockTimeout):
                await batch.run_job_file_async(
                    path, self.workspace, TIMEOUT, lock_timeout=0.3)
            gate.set()
            await single

        with patch.object(launch.runner, "run_script", launches):
            asyncio.run(scenario())
        self.assertFalse((self.workspace / "jobs/after").exists())
        self.assertEqual(launches.peak, 1)

    def test_every_launch_path_is_serialised(self):
        seed = self.write_job("seed", job("jobs/seed"))
        batch.run_job_file(seed, self.workspace)
        project_job = self.write_job(
            "project", job("jobs/project", source="jobs/seed/RockProject.ssa"))
        build_job = self.write_job("build", job("jobs/build"))
        run_job = self.write_job("run", job("jobs/run"))
        launches = Launches(launch.runner.run_script, hold=0.15)

        async def scenario():
            return await asyncio.gather(
                self.inspect(),
                batch.probe_sampler_async(self.workspace),
                batch.validate_with_project_async(project_job, self.workspace),
                batch.check_build_async(build_job, self.workspace),
                batch.run_job_file_async(run_job, self.workspace, TIMEOUT),
            )

        with patch.object(launch.runner, "run_script", launches):
            results = asyncio.run(scenario())
        self.assertEqual(results[2]["project_check"], "ok")
        self.assertEqual(results[3]["build_check"]["status"], "ok")
        self.assertEqual(results[4]["status"], "success")
        self.assertEqual(launches.total, 5)
        self.assertEqual(launches.peak, 1)
        self.assertFalse(batch.lock_held(self.workspace))


if __name__ == "__main__":
    unittest.main()
