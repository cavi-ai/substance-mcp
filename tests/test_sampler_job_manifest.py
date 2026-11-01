import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from env import SAMPLER, patched_environment
from sampler_fake_cli import ENV, install
from sampler_jobs import job_data, run_job_file

from cavi_substance_mcp import receipts
from cavi_substance_mcp.sampler import job, launch


class JobManifestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        for folder in ("photos", "jobs"):
            (self.workspace / folder).mkdir(parents=True)
        (self.workspace / "photos/albedo.png").write_bytes(b"fake-png")
        app = install(root)
        self.env = patched_environment(SAMPLER, {
            ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env.start()
        (self.workspace / "jobs/rock.json").write_text(json.dumps(job_data()))

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def run_job(self):
        return run_job_file("jobs/rock.json", self.workspace)

    def test_manifest_is_a_standard_receipt_without_machine_paths(self):
        manifest = self.run_job()
        on_disk = receipts.load(self.workspace / "jobs/rock/manifest.json")
        self.assertEqual(on_disk, manifest)
        self.assertEqual((manifest["app"], manifest["operation"]), ("sampler", "build"))
        self.assertNotIn("workspace", manifest)
        self.assertNotIn("app_name", manifest)
        self.assertNotIn(str(self.workspace.resolve()), json.dumps(manifest))
        receipts.verify(manifest, self.workspace)

    def test_nested_export_receipts_are_covered_by_the_job_manifest(self):
        manifest = self.run_job()
        nested = [row["path"] for row in manifest["outputs"]
                  if row["path"].endswith("/manifest.json")]
        self.assertTrue(nested, "per-export receipts must be listed as outputs")
        tampered = self.workspace / nested[0]
        tampered.write_text(tampered.read_text().replace("sampler", "tamper", 1))
        with self.assertRaises(receipts.ReceiptError):
            receipts.verify(manifest, self.workspace)

    def export_receipts(self, manifest, directory):
        prefix = f"{manifest['job'].rsplit('/', 1)[0]}/{directory}/"
        return [self.workspace / row["path"] for row in manifest["outputs"]
                if row["path"].startswith(prefix) and row["path"].endswith("/manifest.json")]

    def project_row(self, receipt):
        rows = [row for row in receipts.load(receipt)["inputs"] if row["path"].endswith(".ssa")]
        self.assertEqual(len(rows), 1, receipt)
        return rows[0]

    def test_export_receipts_of_a_new_project_record_the_saved_project(self):
        manifest = self.run_job()
        project = receipts.record(self.workspace / manifest["project"], self.workspace)
        exported = self.export_receipts(manifest, "exports")
        self.assertEqual(len(exported), 2)
        for receipt in exported:
            receipts.chain(receipt, self.workspace)
            self.assertEqual(self.project_row(receipt), project)

    def test_a_change_between_exports_is_saved_before_the_next_export(self):
        data = job_data()
        data["steps"] += [
            {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
             "parameters": {"Tiling": 5}},
            {"action": "export", "asset": "Rock", "target": "blender", "directory": "blender"},
        ]
        (self.workspace / "jobs/rock.json").write_text(json.dumps(data))
        manifest = self.run_job()
        project = receipts.record(self.workspace / manifest["project"], self.workspace)
        for receipt in self.export_receipts(manifest, "blender"):
            receipts.chain(receipt, self.workspace)
            self.assertEqual(self.project_row(receipt), project)
        for receipt in self.export_receipts(manifest, "exports"):
            self.assertNotEqual(self.project_row(receipt)["sha256"], project["sha256"])

    def test_export_receipts_of_a_changed_source_project_record_the_change(self):
        self.run_job()
        data = {
            "schema": 1,
            "directory": "jobs/copy",
            "project": {"source": "jobs/rock/RockProject.ssa", "name": "RockCopy"},
            "steps": [
                {"action": "set_metadata", "asset": "Rock", "author": "Cavi"},
                {"action": "export", "asset": "Rock", "target": "unity"},
            ],
        }
        (self.workspace / "jobs/copy.json").write_text(json.dumps(data))
        manifest = run_job_file("jobs/copy.json", self.workspace)
        project = receipts.record(self.workspace / manifest["project"], self.workspace)
        exported = self.export_receipts(manifest, "exports")
        self.assertEqual(len(exported), 2)
        for receipt in exported:
            receipts.chain(receipt, self.workspace)
            self.assertEqual(self.project_row(receipt), project)

    def test_top_level_bookkeeping_files_are_not_outputs(self):
        manifest = self.run_job()
        outputs = {row["path"] for row in manifest["outputs"]}
        for name in ("manifest.json", "progress.jsonl", "failure.json"):
            self.assertNotIn(f"jobs/rock/{name}", outputs)

    def test_an_input_changed_during_the_run_fails_the_job(self):
        original = launch.invoke_job

        async def edit_input_then_run(*args, **kwargs):
            result = await original(*args, **kwargs)
            (self.workspace / "photos/albedo.png").write_bytes(b"changed")
            return result

        with patch.object(launch, "invoke_job", edit_input_then_run), \
                self.assertRaisesRegex(job.JobValidationError, "input changed"):
            self.run_job()
        self.assertFalse((self.workspace / "jobs/rock").exists())

    def test_failure_record_has_no_machine_paths(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("boom")

        with patch.object(launch, "invoke_job", fail), \
                self.assertRaisesRegex(RuntimeError, "boom"):
            run_job_file("jobs/rock.json", self.workspace, keep_on_failure=True)
        record = json.loads((self.workspace / "jobs/rock/failure.json").read_text())
        self.assertEqual(record["app"], "sampler")
        self.assertNotIn("workspace", record)
        self.assertNotIn(str(self.workspace.resolve()), json.dumps(record))


if __name__ == "__main__":
    unittest.main()
