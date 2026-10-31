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
        data = job_data()
        data["project"] = {"source": "projects/source.ssa",
                           "asset": {"name": "Rock"}}
        cases.append(data)
        data = job_data()
        data["project"] = {"name": "RockProject", "assets": ["Rock"]}
        cases.append(data)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                job.Job.model_validate(case)

    def test_backslash_paths_normalize(self):
        self.assertEqual(job.relative_path("photos\\albedo.png"), "photos/albedo.png")
        data = job_data()
        data["steps"][0]["images"] = ["photos\\albedo.png"]
        parsed = job.Job.model_validate(data)
        self.assertEqual(parsed.steps[0].images[0], "photos/albedo.png")

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

    def source_seed(self, name, material=True):
        directory = f"jobs/{name}"
        seed = job_data(directory=directory)
        steps = [
            seed["steps"][0],
            {"action": "insert_filter", "asset": "Rock", "filter": "Tiling", "position": 1},
        ]
        if material:
            steps.append({"action": "insert_material", "asset": "Rock",
                          "material": "Brick", "position": 0})
        steps.append({"action": "export", "asset": "Rock", "target": "unreal",
                      "resolution": 64})
        seed["steps"] = steps
        batch.run_job_file(self.write_job(f"{name}-seed.json", seed), self.workspace)
        return f"{directory}/RockProject.ssa"

    def source_job(self, name, source, steps):
        return self.write_job(f"{name}.json", {
            "schema": 1,
            "directory": f"jobs/{name}",
            "project": {"source": source, "name": "RockCopy", "assets": ["Rock"]},
            "steps": steps,
        })

    def test_validate_with_project_success(self):
        source = self.source_seed("preflight-seed")
        digest = (self.workspace / source).read_bytes()
        path = self.source_job("preflight", source, [
            {"action": "set_metadata", "asset": "Rock", "author": "Eval"},
            {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "apply_preset", "asset": "Rock", "layer": "Tiling",
             "preset": "Wet"},
            {"action": "set_channels", "asset": "Rock", "layer": "Tiling",
             "applies_to": ["baseColor"]},
            {"action": "set_blend_parameters", "asset": "Rock", "layer": "Brick",
             "parameters": {"Opacity": 0.5}},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ])
        plan = batch.validate_with_project(path, self.workspace)
        self.assertEqual(plan["project_check"], "ok")
        self.assertEqual(plan["project_warnings"], [])
        self.assertTrue(plan["sampler"]["targets"]["unity"]["available"])
        self.assertFalse((self.workspace / "jobs/preflight").exists())
        self.assertEqual((self.workspace / source).read_bytes(), digest)
        self.assertFalse(list(self.workspace.glob(".cavi-preflight-*")))

    def test_validate_with_project_failures(self):
        source = self.source_seed("bad-seed")
        digest = (self.workspace / source).read_bytes()
        cases = {
            "asset": {"action": "set_metadata", "asset": "Missing", "author": "Eval"},
            "layer": {"action": "set_parameters", "asset": "Rock", "layer": "Missing",
                      "parameters": {"Tiling": 3}},
            "parameter": {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
                          "parameters": {"Missing": 3}},
            "preset": {"action": "apply_preset", "asset": "Rock", "layer": "Tiling",
                       "preset": "Missing"},
            "channel": {"action": "set_channels", "asset": "Rock", "layer": "Tiling",
                        "applies_to": ["Missing"]},
            "filter": {"action": "insert_filter", "asset": "Rock",
                       "filter": "Missing Filter", "position": 2},
            "blend": {"action": "set_blend_parameters", "asset": "Rock",
                      "layer": "Brick", "parameters": {"Missing": 0.5}},
        }
        for name, step in cases.items():
            with self.subTest(name=name):
                path = self.source_job(f"bad-{name}", source, [
                    step,
                    {"action": "export", "asset": "Rock", "target": "unity",
                     "directory": "unity"},
                ])
                with self.assertRaises(job.JobValidationError):
                    batch.validate_with_project(path, self.workspace)
                self.assertEqual((self.workspace / source).read_bytes(), digest)
                self.assertFalse(list(self.workspace.glob(".cavi-preflight-*")))

    def test_cli_check_project(self):
        source = self.source_seed("cli-preflight-seed", material=False)
        path = self.source_job("cli-preflight", source, [
            {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "validate", path, "--workspace", str(self.workspace),
                "--check-project", "--json",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["project_check"], "ok")

    def test_validate_with_project_new_job_not_applicable(self):
        path = self.write_job("new-project.json", job_data(directory="jobs/new-project"))
        plan = batch.validate_with_project(path, self.workspace)
        self.assertEqual(plan["project_check"], "not_applicable")
        self.assertIn("sampler", plan)
        self.assertFalse((self.workspace / "jobs/new-project").exists())

    def test_progress_and_timings(self):
        path = self.write_job("progress.json", job_data(directory="jobs/progress"))
        events = []
        manifest = asyncio.run(batch.run_job_file_async(
            path, self.workspace, on_progress=events.append
        ))
        statuses = [event.get("status") for event in events]
        self.assertIn("start", statuses)
        self.assertIn("ok", statuses)
        self.assertTrue(all("seconds" in step for step in manifest["steps"]))
        progress = self.workspace / "jobs/progress/progress.jsonl"
        self.assertTrue(progress.is_file())
        self.assertTrue(progress.read_text().strip())
        outputs = [item["path"] for item in manifest["outputs"]]
        self.assertNotIn("jobs/progress/progress.jsonl", outputs)

    def test_cli_verbose_progress(self):
        path = self.write_job("verbose.json", job_data(directory="jobs/verbose"))
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = cli.main([
                "run", path, "--workspace", str(self.workspace), "--verbose", "--json",
            ])
        self.assertEqual(code, 0)
        self.assertIn("step 1/", error.getvalue())
        self.assertIn("ok", error.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "success")

    def test_cli_queue(self):
        first = self.write_job("queue1.json", job_data(directory="jobs/queue1"))
        second = self.write_job("queue2.json", job_data(directory="jobs/queue2"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "run", first, second, "--workspace", str(self.workspace), "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["jobs"]), 2)
        self.assertTrue((self.workspace / "jobs/queue1/manifest.json").is_file())
        self.assertTrue((self.workspace / "jobs/queue2/manifest.json").is_file())

    def test_cli_queue_continue_on_error(self):
        (self.workspace / "jobs/bad-existing").mkdir()
        bad = self.write_job("bad.json", job_data(directory="jobs/bad-existing"))
        good = self.write_job("queue-good.json", job_data(directory="jobs/queue-good"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "run", bad, good, "--workspace", str(self.workspace), "--json",
                "--continue-on-error",
            ])
        self.assertEqual(code, 2)
        data = json.loads(output.getvalue())
        self.assertEqual(data["status"], "failed")
        self.assertEqual(len(data["failures"]), 1)
        self.assertEqual(len(data["jobs"]), 1)
        self.assertTrue((self.workspace / "jobs/queue-good/manifest.json").is_file())

    def test_exit_codes(self):
        path = self.write_job("exit.json", job_data(directory="jobs/exit"))
        with (
            patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(self.workspace / "missing")}),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(cli.main(["run", path, "--workspace", str(self.workspace)]), 3)
        with (
            patch.object(batch.runner, "running", return_value=True),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(cli.main(["run", path, "--workspace", str(self.workspace)]), 4)
        lock = batch.WorkspaceLock(self.workspace, 0)
        lock.__enter__()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([
                    "run", path, "--workspace", str(self.workspace),
                    "--lock-timeout", "0.05",
                ]), 4)
        finally:
            lock.__exit__()
        slow = self.workspace / "slow-sampler"
        slow.write_text("#!/bin/sh\nsleep 30\n")
        slow.chmod(0o755)
        with (
            patch.dict(os.environ, {"CAVI_SAMPLER_BIN": str(slow)}),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(cli.main([
                "run", path, "--workspace", str(self.workspace), "--timeout", "0.5",
            ]), 6)

    def test_wait_for_app(self):
        with (
            patch.object(batch.runner, "running", side_effect=[True, False, False, False]),
            patch.object(batch.time, "sleep", return_value=None),
        ):
            batch.app_available(1)

    def test_check_sampler_warns_on_source(self):
        source = self.source_seed("warn-seed", material=False)
        path = self.source_job("warn", source, [
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ])
        plan = batch.validate_with_sampler(path, self.workspace)
        self.assertTrue(any("check-project" in item
                            for item in plan.get("preflight_warnings", [])))

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

    def test_source_project_export_only(self):
        seed = job_data(directory="jobs/seed-export")
        seed["steps"] = [
            seed["steps"][0],
            {"action": "export", "asset": "Rock", "target": "unreal", "resolution": 64},
        ]
        batch.run_job_file(self.write_job("seed-export.json", seed), self.workspace)
        source = self.workspace / "jobs/seed-export/RockProject.ssa"
        digest = source.read_bytes()
        data = {
            "schema": 1,
            "directory": "jobs/source-export",
            "project": {"source": "jobs/seed-export/RockProject.ssa", "name": "RockCopy"},
            "steps": [
                {"action": "export", "asset": "Rock", "target": "unity",
                 "directory": "unity"},
            ],
        }
        manifest = batch.run_job_file(self.write_job("source-export.json", data),
                                      self.workspace)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["source"], "jobs/seed-export/RockProject.ssa")
        self.assertEqual(manifest["project"], "jobs/source-export/RockCopy.ssa")
        self.assertTrue((self.workspace / "jobs/source-export/RockCopy.ssa").is_file())
        self.assertEqual(source.read_bytes(), digest)

    def test_source_project_copy_and_authoring(self):
        seed = job_data(directory="jobs/seed")
        seed["steps"] = [
            seed["steps"][0],
            {"action": "export", "asset": "Rock", "target": "unreal", "resolution": 64},
        ]
        seed_path = self.write_job("seed.json", seed)
        batch.run_job_file(seed_path, self.workspace)
        source = self.workspace / "jobs/seed/RockProject.ssa"
        digest = source.read_bytes()
        data = {
            "schema": 1,
            "directory": "jobs/source-copy",
            "project": {"source": "jobs/seed/RockProject.ssa", "name": "RockCopy",
                        "assets": ["Rock"]},
            "steps": [
                {"action": "set_metadata", "asset": "Rock", "author": "Cavi",
                 "category": "rock", "tags": ["game"], "physical_size": [1, 2, 3]},
                {"action": "insert_filter", "asset": "Rock", "filter": "Tiling",
                 "position": 1},
                {"action": "set_channels", "asset": "Rock", "layer": "Tiling",
                 "applies_to": ["baseColor"]},
                {"action": "insert_material", "asset": "Rock", "material": "Brick",
                 "position": 0},
                {"action": "set_blend_parameters", "asset": "Rock", "layer": "Brick",
                 "parameters": {"Opacity": 0.5}},
                {"action": "export", "asset": "Rock", "target": "unity",
                 "directory": "unity"},
            ],
        }
        path = self.write_job("source.json", data)
        manifest = batch.run_job_file(path, self.workspace)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["source"], "jobs/seed/RockProject.ssa")
        self.assertEqual(manifest["project"], "jobs/source-copy/RockCopy.ssa")
        self.assertEqual(source.read_bytes(), digest)
        outputs = [row["path"] for row in manifest["outputs"]]
        self.assertIn("jobs/source-copy/RockCopy.ssa", outputs)
        metadata = next(step for step in manifest["steps"]
                        if step["action"] == "set_metadata")
        self.assertEqual(metadata["result"]["metadata"]["author"], "Cavi")

    def test_validate_with_missing_filter(self):
        data = job_data(directory="jobs/missing-filter")
        data["steps"].insert(-1, {"action": "insert_filter", "asset": "Rock",
                                  "filter": "Missing Filter", "position": 2})
        path = self.write_job("missing-filter.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "filter .* not available"):
            batch.validate_with_sampler(path, self.workspace)

    def test_validate_with_missing_material(self):
        data = job_data(directory="jobs/missing-material")
        data["steps"].insert(-1, {"action": "insert_material", "asset": "Rock",
                                  "material": "Missing Material", "position": 2})
        path = self.write_job("missing-material.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "material .* not available"):
            batch.validate_with_sampler(path, self.workspace)

    def test_check_build_success(self):
        path = self.write_job("build-check.json", job_data(directory="jobs/build-check"))
        plan = batch.check_build(path, self.workspace)
        self.assertEqual(plan["build_check"]["status"], "ok")
        self.assertEqual(plan["build_check"]["exports_skipped"], 1)
        self.assertTrue(plan["build_check"]["steps"])
        self.assertIn("sampler", plan)
        self.assertFalse((self.workspace / "jobs/build-check").exists())
        self.assertFalse(list(self.workspace.glob(".cavi-build-*")))

    def test_check_build_failure(self):
        data = job_data(directory="jobs/build-bad")
        data["steps"] = [
            {"action": "set_parameters", "asset": "Rock", "layer": "Missing",
             "parameters": {"value": 1}},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ]
        path = self.write_job("build-bad.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "Missing"):
            batch.check_build(path, self.workspace)
        self.assertFalse((self.workspace / "jobs/build-bad").exists())
        self.assertFalse(list(self.workspace.glob(".cavi-build-*")))

    def test_check_build_source_project(self):
        source = self.source_seed("build-seed", material=False)
        digest = (self.workspace / source).read_bytes()
        path = self.source_job("build-source", source, [
            {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ])
        plan = batch.check_build(path, self.workspace)
        self.assertEqual(plan["build_check"]["status"], "ok")
        self.assertEqual(plan["build_check"]["exports_skipped"], 1)
        self.assertEqual((self.workspace / source).read_bytes(), digest)
        self.assertFalse((self.workspace / "jobs/build-source").exists())
        self.assertFalse(list(self.workspace.glob(".cavi-build-*")))

    def test_check_build_refuses_import_resource(self):
        data = job_data(directory="jobs/build-resource")
        data["steps"] = [
            {"action": "import_resource", "path": "assets/custom.sbsar"},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ]
        path = self.write_job("build-resource.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "import_resource"):
            batch.check_build(path, self.workspace)
        self.assertFalse(list(self.workspace.glob(".cavi-build-*")))

    def test_cli_check_build(self):
        path = self.write_job("cli-build.json", job_data(directory="jobs/cli-build"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main([
                "validate", path, "--workspace", str(self.workspace),
                "--check-build", "--json",
            ])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["build_check"]["status"], "ok")
        self.assertEqual(data["build_check"]["exports_skipped"], 1)
        self.assertFalse((self.workspace / "jobs/cli-build").exists())

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
            {"action": "import_resource", "path": "assets/custom.sbsar",
             "resources": ["custom"]},
            {"action": "import_images", "asset": "Rock", "images": ["photos/albedo.png"],
             "position": 0},
            {"action": "insert_filter", "asset": "Rock", "filter": "custom", "position": 1},
            {"action": "export", "asset": "Rock", "target": "blender"},
        ]
        path = self.write_job("resources.json", data)
        plan = batch.validate_with_sampler(path, self.workspace)
        self.assertTrue(plan["imports_resources"])
        self.assertEqual(plan["imported_resources"], ["custom"])
        self.assertFalse(plan["preflight_skip_resources"])
        manifest = batch.run_job_file(path, self.workspace)
        actions = [step["action"] for step in manifest["steps"]]
        self.assertEqual(actions[:3], ["create_asset", "import_resource", "import_images"])
        self.assertEqual(manifest["steps"][1]["result"]["resources"][0]["name"], "custom")

    def test_import_resource_declared_preflight(self):
        data = {
            "schema": 1,
            "directory": "jobs/resource-check",
            "project": {"name": "ResourceProject",
                        "asset": {"name": "Rock"}},
            "steps": [
                {"action": "import_resource", "path": "assets/custom.sbsar",
                 "resources": ["custom"]},
                {"action": "import_images", "asset": "Rock",
                 "images": ["photos/albedo.png"], "position": 0},
                {"action": "insert_filter", "asset": "Rock", "filter": "Missing Filter",
                 "position": 1},
                {"action": "export", "asset": "Rock", "target": "generic",
                 "outputs": ["sbsar"]},
            ],
        }
        path = self.write_job("resource-check.json", data)
        with self.assertRaisesRegex(job.JobValidationError, "filter .* not available"):
            batch.validate_with_sampler(path, self.workspace)

    def test_import_resource_undeclared_preflight_skips(self):
        data = {
            "schema": 1,
            "directory": "jobs/resource-skip",
            "project": {"name": "ResourceProject",
                        "asset": {"name": "Rock"}},
            "steps": [
                {"action": "import_resource", "path": "assets/custom.sbsar"},
                {"action": "import_images", "asset": "Rock",
                 "images": ["photos/albedo.png"], "position": 0},
                {"action": "insert_filter", "asset": "Rock", "filter": "Missing Filter",
                 "position": 1},
                {"action": "export", "asset": "Rock", "target": "generic",
                 "outputs": ["sbsar"]},
            ],
        }
        path = self.write_job("resource-skip.json", data)
        plan = batch.validate_with_sampler(path, self.workspace)
        self.assertTrue(plan["preflight_skip_resources"])
        self.assertIn("preflight_warnings", plan)

    def slug_job(self, directory, names, exports):
        steps = []
        for name in names:
            steps.append({"action": "create_asset", "name": name})
            steps.append({"action": "insert_filter", "asset": name, "filter": "Tiling",
                          "position": 0})
        return {
            "schema": 1,
            "directory": directory,
            "project": {"name": "Slugs"},
            "steps": steps + exports,
        }

    def test_export_names_that_change_when_slugged(self):
        names = ["Rock 01", "Photo PBR", "Mossy (rock) #2", "Caf\u00e9 Stone"]
        slugs = ["Rock_01", "Photo_PBR", "Mossy_rock_2", "Caf_Stone"]
        data = self.slug_job("jobs/slugs", names, [
            {"action": "export", "asset": "Rock 01", "outputs": ["sbsar"],
             "directory": "single"},
            {"action": "export", "assets": names, "outputs": ["sbsar"],
             "directory": "several"},
            {"action": "export", "all_assets": True, "outputs": ["sbsar"],
             "directory": "everything"},
        ])
        manifest = batch.run_job_file(self.write_job("slugs.json", data), self.workspace)
        outputs = {row["path"] for row in manifest["outputs"]}
        self.assertIn("jobs/slugs/single/Rock_01/sbsar/Rock 01.sbsar", outputs)
        for selector in ("several", "everything"):
            for name, slug in zip(names, slugs, strict=True):
                self.assertIn(f"jobs/slugs/{selector}/{slug}/sbsar/{name}.sbsar", outputs)

    def test_export_slug_collisions_are_refused(self):
        for first, second in (("A B", "A_B"), ("Rock", "rock")):
            with self.subTest(first=first, second=second):
                data = self.slug_job(f"jobs/clash-{len(first)}{first[0]}", [first, second], [
                    {"action": "export", "assets": [first, second], "outputs": ["sbsar"]},
                ])
                path = self.write_job(f"clash-{len(first)}{first[0]}.json", data)
                with self.assertRaisesRegex(ValueError, "share export directory"):
                    batch.run_job_file(path, self.workspace)
                self.assertFalse((self.workspace / data["directory"]).exists())

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
