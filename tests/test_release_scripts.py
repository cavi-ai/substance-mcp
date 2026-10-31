import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_release = load("check_release")
changelog_section = load("changelog_section")

CHANGELOG = """# Changelog

## Unreleased

- Not shipped yet.

## 0.6.0 - 2026-10-31

### Added

- A feature.

```
## 0.5.0 - 2026-01-01 (inside a fence, not a heading)
```

### Fixed

- A bug.

## 0.5.0 - 2026-09-01

- Older.

## 0.4.0

- Predates dated headings.
"""


def run(module, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ChangelogSectionTests(unittest.TestCase):
    def test_returns_the_body_up_to_the_next_version_heading(self):
        body = changelog_section.section(CHANGELOG, "0.6.0")
        self.assertTrue(body.startswith("### Added"))
        self.assertTrue(body.endswith("- A bug."))
        self.assertIn("## 0.5.0 - 2026-01-01 (inside a fence", body)
        self.assertNotIn("Older.", body)

    def test_last_section_runs_to_the_end_of_the_file(self):
        body = changelog_section.section(CHANGELOG, "0.4.0")
        self.assertEqual(body, "- Predates dated headings.")

    def test_versions_match_exactly(self):
        self.assertIsNone(changelog_section.section(CHANGELOG, "0.6"))
        self.assertIsNone(changelog_section.section(CHANGELOG, "0.6.00"))
        self.assertIsNone(changelog_section.section(CHANGELOG, "9.9.9"))

    def test_command_line_prints_the_section_and_accepts_a_tag_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "CHANGELOG.md"
            path.write_text(CHANGELOG, encoding="utf-8")
            code, out, err = run(changelog_section, "v0.5.0", "--file", str(path))
        self.assertEqual((code, out, err), (0, "- Older.\n", ""))

    def test_command_line_fails_for_a_missing_or_empty_section(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n## 1.0.0 - 2026-01-01\n\n## 0.9.0 - 2025-12-01\n\n- x\n",
                encoding="utf-8",
            )
            for version in ("1.0.0", "2.0.0"):
                with self.subTest(version=version):
                    code, out, err = run(changelog_section, version, "--file", str(path))
                    self.assertEqual((code, out), (1, ""))
                    self.assertIn(f"no section for {version}", err)

    def test_command_line_reports_an_unreadable_file(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, err = run(changelog_section, "1.0.0", "--file", str(Path(folder) / "none"))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("cannot read", err)


class CheckReleaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.write()

    def write(self, *, pyproject="0.6.0", lock="0.6.0", changelog="0.6.0 - 2026-10-31",
              name="cavi-substance-mcp"):
        (self.root / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{pyproject}"\n', encoding="utf-8")
        (self.root / "uv.lock").write_text(
            'version = 1\n\n[[package]]\nname = "mcp"\nversion = "1.30.0"\n\n'
            f'[[package]]\nname = "cavi-substance-mcp"\nversion = "{lock}"\n'
            'source = { editable = "." }\n', encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## {changelog}\n\n- Notes.\n", encoding="utf-8")

    def check(self, tag="v0.6.0"):
        return check_release.check(self.root, tag)

    def test_everything_agreeing_passes_with_and_without_a_tag(self):
        for tag in ("v0.6.0", None):
            with self.subTest(tag=tag):
                problems, summary = self.check(tag)
                self.assertEqual(problems, [])
                self.assertIn("agree on 0.6.0", summary)

    def test_a_tag_that_is_not_v_plus_the_version_fails(self):
        for tag in ("v0.6.1", "0.6.0", "v0.6.0-rc1"):
            with self.subTest(tag=tag):
                problems, _ = self.check(tag)
                self.assertEqual(len(problems), 1)
                self.assertIn(f"tag {tag} does not match", problems[0])
                self.assertIn("want v0.6.0", problems[0])

    def test_no_tag_still_requires_the_other_sources_to_agree(self):
        self.write(lock="0.5.0")
        problems, _ = self.check(None)
        self.assertEqual(len(problems), 1)
        self.assertIn("uv.lock records cavi-substance-mcp 0.5.0", problems[0])

    def test_a_stale_lockfile_fails(self):
        self.write(pyproject="0.7.0", lock="0.6.0", changelog="0.7.0 - 2026-11-01")
        problems, _ = self.check("v0.7.0")
        self.assertEqual(len(problems), 1)
        self.assertIn("run `uv lock`", problems[0])

    def test_a_missing_changelog_section_fails(self):
        self.write(changelog="0.5.0 - 2026-09-01")
        problems, _ = self.check()
        self.assertEqual(problems, ["CHANGELOG.md has no `## 0.6.0 - YYYY-MM-DD` section"])

    def test_a_changelog_heading_needs_a_date(self):
        for heading in ("0.6.0", "0.6.0 - Unreleased", "0.6.0 (2026-10-31)"):
            with self.subTest(heading=heading):
                self.write(changelog=heading)
                problems, _ = self.check()
                self.assertEqual(len(problems), 1)
                self.assertIn("no `## 0.6.0 - YYYY-MM-DD` section", problems[0])

    def test_an_impossible_changelog_date_fails(self):
        self.write(changelog="0.6.0 - 2026-13-45")
        problems, _ = self.check()
        self.assertEqual(problems, ["CHANGELOG.md: '2026-13-45' is not a calendar date"])

    def test_a_duplicated_changelog_section_fails(self):
        (self.root / "CHANGELOG.md").write_text(
            "## 0.6.0 - 2026-10-31\n\n- a\n\n## 0.6.0 - 2026-10-30\n\n- b\n", encoding="utf-8")
        problems, _ = self.check()
        self.assertEqual(problems, ["CHANGELOG.md has 2 sections for 0.6.0"])

    def test_every_disagreement_is_reported_together(self):
        self.write(lock="0.5.0", changelog="0.4.0 - 2026-01-01")
        problems, _ = self.check("v9.9.9")
        self.assertEqual(len(problems), 3)

    def test_unreadable_sources_are_reported_not_raised(self):
        (self.root / "CHANGELOG.md").unlink()
        (self.root / "uv.lock").write_text("not [valid toml", encoding="utf-8")
        problems, _ = self.check()
        self.assertEqual(len(problems), 2)
        self.assertTrue(any(p.startswith("uv.lock: cannot read version") for p in problems))
        self.assertTrue(any(p.startswith("CHANGELOG.md: cannot read") for p in problems))

    def test_a_foreign_project_name_fails(self):
        self.write(name="something-else")
        problems, summary = self.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("not 'cavi-substance-mcp'", problems[0])
        self.assertEqual(summary, "")

    def test_command_line_exit_codes_and_messages(self):
        code, out, err = run(check_release, "--root", str(self.root), "--tag", "v0.6.0")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("ok: tag v0.6.0"))
        code, out, err = run(check_release, "--root", str(self.root), "--tag", "v1.0.0")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: tag v1.0.0 does not match"))

    def test_the_repository_itself_agrees_on_pyproject_and_lock(self):
        root = SCRIPTS.parent
        self.assertEqual(check_release.pyproject_version(root), check_release.lock_version(root))


if __name__ == "__main__":
    unittest.main()
