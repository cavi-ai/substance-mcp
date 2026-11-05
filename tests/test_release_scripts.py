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

    def write(self, *, version=None, source="vcs", changelog="0.6.0 - 2026-10-31",
              name="cavi-substance-mcp"):
        static = f'version = "{version}"\n' if version else 'dynamic = ["version"]\n'
        (self.root / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\n{static}\n[tool.hatch.version]\nsource = "{source}"\n',
            encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## Unreleased\n\n## {changelog}\n\n- Notes.\n", encoding="utf-8")

    def check(self, tag="v0.6.0"):
        return check_release.check(self.root, tag)

    def test_a_tag_with_its_changelog_section_passes(self):
        problems, summary = self.check()
        self.assertEqual(problems, [])
        self.assertEqual(summary, "tag v0.6.0 and CHANGELOG.md agree on 0.6.0")

    def test_no_tag_checks_the_version_configuration_only(self):
        self.assertEqual(self.check(None), ([], "no tag: the version comes from the git tag"))

    def test_a_tag_that_is_not_v_x_y_z_fails(self):
        for tag in ("0.6.0", "v0.6.0-rc1", "v0.6", "v06.0.0"):
            with self.subTest(tag=tag):
                self.assertEqual(self.check(tag), ([f"tag {tag} is not vX.Y.Z"], ""))

    def test_a_static_version_fails_with_or_without_a_tag(self):
        self.write(version="0.6.0")
        for tag in ("v0.6.0", None):
            with self.subTest(tag=tag):
                problems, _ = self.check(tag)
                self.assertTrue(any("static version" in p for p in problems), problems)
                self.assertTrue(any('dynamic must list "version"' in p for p in problems), problems)

    def test_another_version_source_fails(self):
        self.write(source="regex")
        problems, _ = self.check()
        self.assertEqual(problems, ['pyproject.toml: [tool.hatch.version] source must be "vcs"'])

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

    def test_every_problem_is_reported_together(self):
        self.write(version="0.6.0", changelog="0.4.0 - 2026-01-01")
        problems, _ = self.check()
        self.assertEqual(len(problems), 3)

    def test_unreadable_sources_are_reported_not_raised(self):
        (self.root / "CHANGELOG.md").unlink()
        (self.root / "pyproject.toml").write_text("not [valid toml", encoding="utf-8")
        problems, _ = self.check()
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("pyproject.toml: cannot read"))
        self.assertTrue(problems[1].startswith("CHANGELOG.md: cannot read"))

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
        self.assertTrue(err.startswith("error: CHANGELOG.md has no `## 1.0.0"))

    def test_the_repository_itself_takes_its_version_from_the_tag(self):
        self.assertEqual(check_release.version_configuration(SCRIPTS.parent), [])


if __name__ == "__main__":
    unittest.main()
