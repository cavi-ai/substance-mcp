#!/usr/bin/env python3
"""Prove that one release has one version before anything is built or published.

A `v*` tag publishes the wheel, the sdist and a GitHub Release. If the tag,
`pyproject.toml`, the `uv.lock` entry for this package and the CHANGELOG disagree,
the tag ships a mislabelled artifact. This is the gate: it names every
disagreement and exits non-zero.

    scripts/check_release.py --tag v0.6.0   # tag push: tag must equal "v" + version
    scripts/check_release.py                # no tag (dry run): version agreement only
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "cavi-substance-mcp"
# `## 0.6.0 - 2026-10-31`
HEADING = re.compile(r"^## +(?P<version>\S+) +- +(?P<date>\d{4}-\d{2}-\d{2}) *$")
FENCE = re.compile(r"^ {0,3}(```|~~~)")


def pyproject_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("name") != PACKAGE:
        found = project.get("name")
        raise ValueError(f"pyproject.toml: project name is {found!r}, not {PACKAGE!r}")
    if "version" not in project:
        raise ValueError("pyproject.toml: [project] has no static version")
    return project["version"]


def lock_version(root: Path) -> str:
    """The version `uv.lock` records for this package; `uv sync --locked` refuses a stale one."""
    packages = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8")).get("package", [])
    versions = [entry.get("version") for entry in packages if entry.get("name") == PACKAGE]
    if len(versions) != 1 or not versions[0]:
        raise ValueError(f"uv.lock: expected one versioned entry for {PACKAGE}, found {versions}")
    return versions[0]


def changelog_dates(root: Path, version: str) -> list[str]:
    """Every `## <version> - YYYY-MM-DD` heading in CHANGELOG.md, as the dates it carries."""
    in_fence = False
    dates = []
    for line in (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (match := HEADING.match(line)) and match["version"] == version:
            dates.append(match["date"])
    return dates


def check(root: Path, tag: str | None) -> tuple[list[str], str]:
    """Return the disagreements and a one-line summary of what agreed."""
    problems: list[str] = []
    sources: dict[str, str] = {}
    for label, read in (("pyproject.toml", pyproject_version), ("uv.lock", lock_version)):
        try:
            sources[label] = read(root)
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
            problems.append(f"{label}: cannot read version: {exc}")
    expected = sources.get("pyproject.toml")
    if expected is None:
        return problems, ""
    if sources.get("uv.lock", expected) != expected:
        problems.append(
            f"uv.lock records {PACKAGE} {sources['uv.lock']} but pyproject.toml says {expected}; "
            "run `uv lock` and commit the result"
        )
    if tag is not None and tag != f"v{expected}":
        problems.append(
            f"tag {tag} does not match pyproject.toml version {expected} (want v{expected})"
        )
    try:
        dates = changelog_dates(root, expected)
    except OSError as exc:
        problems.append(f"CHANGELOG.md: cannot read: {exc.strerror}")
    else:
        if not dates:
            problems.append(f"CHANGELOG.md has no `## {expected} - YYYY-MM-DD` section")
        elif len(dates) > 1:
            problems.append(f"CHANGELOG.md has {len(dates)} sections for {expected}")
        else:
            try:
                date.fromisoformat(dates[0])
            except ValueError:
                problems.append(f"CHANGELOG.md: {dates[0]!r} is not a calendar date")
    subject = f"tag {tag}, " if tag else "no tag (version agreement only), "
    return problems, f"{subject}pyproject.toml, uv.lock and CHANGELOG.md agree on {expected}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="Release tag, for example v0.6.0. Omit for a dry run.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root (for tests).")
    args = parser.parse_args(argv)
    problems, summary = check(args.root, args.tag or None)
    if problems:
        print("\n".join(f"error: {problem}" for problem in problems), file=sys.stderr)
        return 1
    print(f"ok: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
