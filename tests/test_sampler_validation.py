import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sampler_jobs import job_data

from cavi_substance_mcp import receipts
from cavi_substance_mcp.sampler import batch, job, names, operations

ROOT = Path(__file__).resolve().parents[1]
BAD_NAMES = ["A:B", "x*y", "a<b", "a>b", 'say"hi"', "a|b", "what?", "CON", "con", "NUL.txt",
             "Aux", "PRN", "COM1", "lpt9", "COM1.ssa", "trailing.", "..", "a/b", "a\\b",
             "tab\tname"]
GOOD_NAMES = ["Rock 01", "Photo PBR", "Café", "a-b_c.d (1)", "Mossy #2", "COM", "CONSOLE",
              "COM10"]


class JobFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "job.json"

    def test_a_job_file_loads(self):
        self.path.write_text(json.dumps(job_data()))
        self.assertEqual(job.load_job(self.path).directory, "jobs/rock")

    def test_a_job_file_over_8_mib_is_refused_before_it_is_parsed(self):
        self.path.write_text(json.dumps(job_data()) + " " * (8 * 1024 * 1024))
        with self.assertRaisesRegex(
                job.JobValidationError, r"cannot read job: .*larger than 8 MiB"):
            job.load_job(self.path)

    def test_the_limit_is_the_receipt_limit(self):
        self.path.write_text(json.dumps(job_data()))
        with patch.object(receipts, "MAX_JSON_BYTES", 100), \
                self.assertRaisesRegex(job.JobValidationError, "larger than"):
            job.load_job(self.path)


class NameParityTests(unittest.TestCase):
    def test_validator_and_runtime_agree(self):
        for name in BAD_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    names.plain_name(name)
                with self.assertRaises(ValueError):
                    operations._name(name, "name")
                with self.assertRaises(ValueError):
                    job.plain_name(name)
        for name in GOOD_NAMES:
            with self.subTest(name=name):
                self.assertEqual(names.plain_name(name), name)
                self.assertEqual(operations._name(name, "name"), name)
                self.assertEqual(job.plain_name(name), name)

    def test_job_rejects_every_unusable_name_up_front(self):
        for name in BAD_NAMES:
            if not name.strip() or "\t" in name:
                continue
            sites = {
                "project name": lambda n: job_data(project={"name": n}),
                "project asset": lambda n: job_data(
                    project={"name": "RockProject", "asset": {"name": n}}),
                "create_asset": lambda n: job_data(steps=[
                    {"action": "create_asset", "name": n},
                    {"action": "export", "asset": "Rock"}]),
                "step asset": lambda n: job_data(steps=[
                    {"action": "insert_filter", "asset": n, "filter": "Tiling"},
                    {"action": "export", "asset": "Rock"}]),
                "export asset": lambda n: job_data(steps=[
                    {"action": "export", "asset": n}]),
                "export assets": lambda n: job_data(steps=[
                    {"action": "export", "assets": ["Rock", n]}]),
                "source assets": lambda n: job_data(project={
                    "source": "projects/Rock.ssa", "name": "Copy", "assets": [n]}),
            }
            for site, build in sites.items():
                with self.subTest(name=name, site=site), self.assertRaises(ValueError):
                    job.Job.model_validate(build(name))

    def test_source_stem_becomes_the_copy_name(self):
        for source in ("projects/CON.ssa", "projects/a*b.ssa"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                job.Job.model_validate(job_data(
                    project={"source": source},
                    steps=[{"action": "export", "all_assets": True}],
                ))

    def test_good_names_still_validate(self):
        for name in GOOD_NAMES:
            with self.subTest(name=name):
                job.Job.model_validate(job_data(
                    project={"name": "RockProject", "asset": {"name": name}},
                    steps=[{"action": "export", "asset": name}],
                ))


class DirectoryTests(unittest.TestCase):
    def test_export_directory_cannot_shadow_job_files(self):
        for directory in ("manifest.json", "job.json", "RockProject.ssa", "rockproject.SSA",
                          "progress.jsonl", "failure.json", "manifest.json/inner"):
            with self.subTest(directory=directory), self.assertRaisesRegex(
                    ValueError, "collides with a job file"):
                job.Job.model_validate(job_data(steps=[
                    {"action": "export", "asset": "Rock", "directory": directory}]))

    def test_source_jobs_reserve_the_copy_name(self):
        data = job_data(
            project={"source": "projects/Rock.ssa"},
            steps=[{"action": "export", "all_assets": True, "directory": "Rock.ssa"}],
        )
        with self.assertRaisesRegex(ValueError, "collides with a job file"):
            job.Job.model_validate(data)

    def test_directory_components_must_be_portable(self):
        for directory in ("a:b", "x*y", "jobs/CON", "jobs/nul.txt", "ends.", "a<b/c"):
            with self.subTest(directory=directory):
                with self.assertRaises(ValueError):
                    job.Job.model_validate(job_data(directory=directory))
                with self.assertRaises(ValueError):
                    job.Job.model_validate(job_data(steps=[
                        {"action": "export", "asset": "Rock", "directory": directory}]))

    def test_case_insensitive_directory_reuse_is_refused(self):
        with self.assertRaisesRegex(ValueError, "reused"):
            job.Job.model_validate(job_data(steps=[
                {"action": "export", "asset": "Rock", "directory": "Exports"},
                {"action": "export", "asset": "Rock", "directory": "exports"}]))

    def test_validate_reports_the_problem_without_launching_sampler(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "jobs").mkdir()
            path = root / "jobs/bad.json"
            path.write_text(json.dumps(job_data(steps=[
                {"action": "export", "asset": "Rock", "directory": "manifest.json"}])))
            with self.assertRaisesRegex(job.JobValidationError, "collides with a job file"):
                batch.validate_job_file("jobs/bad.json", root)


class ExampleTests(unittest.TestCase):
    def test_every_example_job_validates(self):
        found = sorted((ROOT / "examples/sampler").glob("*/job.json"))
        self.assertGreaterEqual(len(found), 4)
        for path in found:
            with self.subTest(example=path.parent.name):
                loaded = job.load_job(path)
                self.assertTrue(job.input_paths(loaded) is not None)
                self.assertEqual(json.loads(path.read_text())["schema"], 1)


if __name__ == "__main__":
    unittest.main()
