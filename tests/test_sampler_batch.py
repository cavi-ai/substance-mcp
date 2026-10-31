import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import install

from cavi_substance_mcp.sampler import batch, cli, job, targets

TIMEOUT = 60


def job_data(directory="jobs/rock", target="unreal", asset="Rock", project_asset=True):
    project = {"name": "RockProject"}
    if project_asset:
        project["asset"] = {"name": asset, "model": "openpbr", "preset": "base_material"}
    return {
        "schema": 1,
        "directory": directory,
        "project": project,
        "steps": [
            {"action": "import_images", "asset": asset, "images": ["photos/albedo.png"],
             "position": 0, "import_option": "use_as_bitmap"},
            {"action": "insert_filter", "asset": asset, "filter": "Tiling", "position": 1},
            {"action": "set_parameters", "asset": asset, "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "apply_preset", "asset": asset, "layer": "Tiling", "preset": "Wet"},
            {"action": "export", "asset": asset, "target": target, "resolution": 1024},
        ],
    }


class JobModelTests(unittest.TestCase):
    def test_schema_and_defaults(self):
        parsed = job.Job.model_validate(job_data())
        self.assertEqual(parsed.schema_version, 1)
        self.assertEqual(parsed.steps[-1].target, "unreal")
        self.assertEqual(parsed.steps[-1].outputs, ["sbsar", "textures"])
        self.assertEqual(parsed.steps[-1].wait_seconds, 600)
        self.assertIn("schema", job.job_schema()["properties"])

    def test_invalid_paths_and_references(self):
        cases = []
        data = job_data(directory="/absolute")
        cases.append(data)
        data = job_data(directory="jobs/../escape")
        cases.append(data)
        data = job_data()
        data["steps"][-1]["directory"] = "exports"
        data["steps"].append({**data["steps"][-1], "target": "unity"})
        cases.append(data)
        data = job_data()
        data["steps"][0]["asset"] = "Missing"
        cases.append(data)
        data = job_data()
        data["steps"] = [step for step in data["steps"] if step["action"] != "export"]
        cases.append(data)
        data = job_data()
        data["steps"][-1]["asset"] = "Missing"
        cases.append(data)
        data = job_data()
        data["project"]["name"] = "Rock"
        data["project"]["asset"]["name"] = "Rock"
        cases.append(data)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                job.Job.model_validate(case)

    def test_remove_layer_requires_confirmation(self):
        data = job_data()
        data["steps"].insert(-1, {"action": "remove_layer", "asset": "Rock", "layer": "Tiling"})
        with self.assertRaises(ValueError):
            job.Job.model_validate(data)


class TargetTests(unittest.TestCase):
    def test_engine_defaults(self):
        self.assertEqual(targets.preset_for("unreal"), "Unreal Engine 4")
        self.assertEqual(targets.preset_for("unity"), "Unity HD Render Pipeline Standard")
        self.assertEqual(targets.preset_for("blender"), "Blender Cycles/Eevee")
        self.assertIsNone(targets.preset_for("generic"))
        self.assertEqual(targets.preset_for("unreal", "Custom"), "Custom")
        with self.assertRaises(ValueError):
            targets.target("nope")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        for folder in ("photos", "jobs", "assets"):
            (self.workspace / folder).mkdir(parents=True)
        for name in ("albedo.png", "normal.png"):
            (self.workspace / "photos" / name).write_bytes(b"fake-png")
        (self.workspace / "assets/custom.sbsar").write_bytes(b"fake-sbsar")
        app = install(root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_"))}
        env.update({"CAVI_SAMPLER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def write_job(self, name, data):
        path = self.workspace / "jobs" / name
        path.write_text(json.dumps(data))
        return f"jobs/{name}"

    def test_run_unreal_manifest(self):
        path = self.write_job("rock.json", job_data())
        manifest = batch.run_job_file(path, self.workspace)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["project"], "jobs/rock/RockProject.ssa")
        self.assertEqual(manifest["job"], "jobs/rock/job.json")
        outputs = [row["path"] for row in manifest["outputs"]]
        self.assertIn("jobs/rock/RockProject.ssa", outputs)
        self.assertTrue(any(path.endswith(".sbsar") for path in outputs))
        self.assertTrue(any(path.endswith(".png") for path in outputs))
        self.assertEqual([row["path"] for row in manifest["inputs"]],
                         ["photos/albedo.png"])
        export = next(step for step in manifest["steps"] if step["action"] == "export")
        self.assertEqual(export["result"]["target"], "unreal")
        self.assertEqual(export["result"]["preset"], "Unreal Engine 4")
        self.assertTrue((self.workspace / "jobs/rock/manifest.json").is_file())

    def test_all_engine_targets(self):
        for target in ("unreal", "unity", "blender", "generic"):
            with self.subTest(target=target):
                path = self.write_job(f"{target}.json", job_data(
                    directory=f"jobs/{target}", target=target))
                manifest = batch.run_job_file(path, self.workspace)
                export = next(step for step in manifest["steps"]
                              if step["action"] == "export")
                self.assertEqual(export["result"]["target"], target)
                expected = targets.preset_for(target)
                self.assertEqual(export["result"]["preset"], expected)

    def test_validate_does_not_create_directory(self):
        path = self.write_job("preview.json", job_data(directory="jobs/preview"))
        plan = batch.validate_job_file(path, self.workspace)
        self.assertNotIn("status", plan)
        self.assertEqual(plan["targets"][0]["target"], "unreal")
        self.assertFalse((self.workspace / "jobs/preview").exists())

    def test_empty_asset_b2m_job(self):
        data = {
            "schema": 1,
            "directory": "jobs/b2m",
            "project": {"name": "B2MProject"},
            "steps": [
                {"action": "create_asset", "name": "Photo", "empty": True},
                {"action": "import_images", "asset": "Photo",
                 "images": ["photos/albedo.png"], "position": 0,
                 "import_option": "image_to_material_B2M"},
                {"action": "export", "asset": "Photo", "target": "generic",
                 "outputs": ["sbsar"]},
            ],
        }
        path = self.write_job("b2m.json", data)
        manifest = batch.run_job_file(path, self.workspace)
        self.assertEqual(manifest["status"], "success")
        actions = [step["action"] for step in manifest["steps"]]
        self.assertEqual(actions, ["create_asset", "import_images", "export"])

    def test_validate_with_sampler(self):
        path = self.write_job("check.json", job_data(directory="jobs/check"))
        plan = batch.validate_with_sampler(path, self.workspace)
        self.assertIn("sampler", plan)
        self.assertTrue(plan["sampler"]["targets"]["unreal"]["available"])
        self.assertIn("use_as_bitmap", plan["import_options"])
        self.assertFalse((self.workspace / "jobs/check").exists())

    def test_validate_with_missing_preset(self):
        data = job_data(directory="jobs/missing-preset")
        data["steps"][-1]["preset"] = "Missing Preset"
        path = self.write_job("missing-preset.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "not installed"):
            batch.validate_with_sampler(path, self.workspace)

    def test_failure_cleanup_and_keep(self):
        data = job_data(directory="jobs/fail")
        data["project"]["asset"]["name"] = "fail"
        data["steps"] = [
            step for step in data["steps"] if step["action"] in ("import_images", "export")
        ]
        for step in data["steps"]:
            step["asset"] = "fail"
        path = self.write_job("fail.json", data)
        with self.assertRaisesRegex(RuntimeError, "fake export failure"):
            batch.run_job_file(path, self.workspace)
        self.assertFalse((self.workspace / "jobs/fail").exists())
        with self.assertRaisesRegex(RuntimeError, "fake export failure"):
            batch.run_job_file(path, self.workspace, keep_on_failure=True)
        self.assertTrue((self.workspace / "jobs/fail/failure.json").is_file())
        self.assertFalse((self.workspace / "jobs/fail/manifest.json").exists())

    def test_existing_directory_refused(self):
        (self.workspace / "jobs/existing").mkdir()
        path = self.write_job("existing.json", job_data(directory="jobs/existing"))
        with self.assertRaisesRegex(job.JobValidationError, "already exists"):
            batch.run_job_file(path, self.workspace)

    def test_import_resource_and_create_asset(self):
        data = job_data(directory="jobs/resources", target="blender", project_asset=False)
        data["steps"] = [
            {"action": "create_asset", "name": "Rock", "model": "openpbr",
             "preset": "base_material"},
            {"action": "import_resource", "path": "assets/custom.sbsar"},
            {"action": "import_images", "asset": "Rock", "images": ["photos/albedo.png"],
             "position": 0},
            {"action": "insert_filter", "asset": "Rock", "filter": "custom", "position": 1},
            {"action": "export", "asset": "Rock", "target": "blender"},
        ]
        path = self.write_job("resources.json", data)
        manifest = batch.run_job_file(path, self.workspace)
        actions = [step["action"] for step in manifest["steps"]]
        self.assertEqual(actions[:3], ["create_asset", "import_resource", "import_images"])
        self.assertEqual(manifest["steps"][1]["result"]["resources"][0]["name"], "custom")

    def test_remove_layer(self):
        data = job_data(directory="jobs/remove")
        data["steps"].insert(-1, {"action": "remove_layer", "asset": "Rock",
                                  "layer": "Tiling", "confirm": True})
        path = self.write_job("remove.json", data)
        manifest = batch.run_job_file(path, self.workspace)
        removed = next(step for step in manifest["steps"] if step["action"] == "remove_layer")
        self.assertEqual(removed["result"]["removed"], "Tiling")

    def test_lock_serializes_jobs(self):
        first = self.write_job("lock1.json", job_data(directory="jobs/lock1"))
        second = self.write_job("lock2.json", job_data(directory="jobs/lock2"))

        async def run():
            return await asyncio.gather(
                batch.run_job_file_async(first, self.workspace, TIMEOUT),
                batch.run_job_file_async(second, self.workspace, TIMEOUT),
            )

        results = asyncio.run(run())
        self.assertEqual([item["status"] for item in results], ["success", "success"])
        self.assertTrue((self.workspace / "jobs/lock1/manifest.json").is_file())
        self.assertTrue((self.workspace / "jobs/lock2/manifest.json").is_file())

    def test_cli_check_sampler_and_doctor_launch(self):
        path = self.write_job("cli-check.json", job_data(directory="jobs/cli-check"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "validate", path, "--workspace", str(self.workspace),
                "--check-sampler", "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertIn("sampler", data)
        self.assertTrue(data["sampler"]["targets"]["unreal"]["available"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "doctor", "--workspace", str(self.workspace), "--launch", "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertIn("probe", data)
        self.assertTrue(data["probe"]["targets"]["unity"]["available"])

    def test_cli_validate_run_and_exit_codes(self):
        path = self.write_job("cli.json", job_data(directory="jobs/cli"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(["validate", path, "--workspace", str(self.workspace), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["targets"][0]["target"], "unreal")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(["run", path, "--workspace", str(self.workspace), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "success")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = cli.main(["validate", "missing.json", "--workspace", str(self.workspace)])
        self.assertEqual(code, 2)
        self.assertIn("inside the workspace", error.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["schema"]), 0)


if __name__ == "__main__":
    unittest.main()
