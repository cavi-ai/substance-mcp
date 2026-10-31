import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from sampler_fake_cli import install

from cavi_substance_mcp import receipts
from cavi_substance_mcp.sampler import batch, job, launch


def job_data():
    asset = "Rock"
    return {
        "schema": 1,
        "directory": "jobs/rock",
        "project": {"name": "RockProject",
                    "asset": {"name": asset, "model": "openpbr", "preset": "base_material"}},
        "steps": [
            {"action": "import_images", "asset": asset, "images": ["photos/albedo.png"],
             "position": 0, "import_option": "use_as_bitmap"},
            {"action": "export", "asset": asset, "target": "unreal", "resolution": 1024},
        ],
    }


class JobManifestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "ws"
        for folder in ("photos", "jobs"):
            (self.workspace / folder).mkdir(parents=True)
        (self.workspace / "photos/albedo.png").write_bytes(b"fake-png")
        app = install(root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_"))}
        env.update({"CAVI_SAMPLER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(self.workspace)})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        (self.workspace / "jobs/rock.json").write_text(json.dumps(job_data()))

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def run_job(self):
        return batch.run_job_file("jobs/rock.json", self.workspace)

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
            batch.run_job_file("jobs/rock.json", self.workspace, keep_on_failure=True)
        record = json.loads((self.workspace / "jobs/rock/failure.json").read_text())
        self.assertEqual(record["app"], "sampler")
        self.assertNotIn("workspace", record)
        self.assertNotIn(str(self.workspace.resolve()), json.dumps(record))


if __name__ == "__main__":
    unittest.main()
