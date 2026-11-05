#!/usr/bin/env python3
"""Prove that one release tag names one version before anything is built or published.

The version comes from the git tag (hatch-vcs): a `vX.Y.Z` tag builds `X.Y.Z` and publishes
the wheel, the sdist and a GitHub Release. This gate refuses a tag that is not `vX.Y.Z`, a
CHANGELOG without exactly one dated section for it, and a project that does not read its
version from the tag. It names every problem and exits non-zero.

    scripts/check_release.py --tag v0.6.0   # tag push
    scripts/check_release.py                # no tag (dry run): the version configuration only
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
TAG = re.compile(r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
# `## 0.6.0 - 2026-10-31`
HEADING = re.compile(r"^## +(?P<version>\S+) +- +(?P<date>\d{4}-\d{2}-\d{2}) *$")
FENCE = re.compile(r"^ {0,3}(```|~~~)")


def version_configuration(root: Path) -> list[str]:
    """What keeps pyproject.toml from taking its version from the git tag."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    if project.get("name") != PACKAGE:
        found = project.get("name")
        raise ValueError(f"pyproject.toml: project name is {found!r}, not {PACKAGE!r}")
    problems = []
    if "version" in project:
        problems.append("pyproject.toml: a static version; the version comes from the git tag")
    if "version" not in project.get("dynamic", []):
        problems.append('pyproject.toml: [project] dynamic must list "version"')
    if data.get("tool", {}).get("hatch", {}).get("version", {}).get("source") != "vcs":
        problems.append('pyproject.toml: [tool.hatch.version] source must be "vcs"')
    return problems


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
    """Return the problems and a one-line summary of what was checked."""
    try:
        problems = version_configuration(root)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        problems = [f"pyproject.toml: cannot read: {exc}"]
    except ValueError as exc:
        return [str(exc)], ""
    if tag is None:
        return problems, "no tag: the version comes from the git tag"
    match = TAG.match(tag)
    if match is None:
        return [*problems, f"tag {tag} is not vX.Y.Z"], ""
    version = match["version"]
    try:
        dates = changelog_dates(root, version)
    except OSError as exc:
        problems.append(f"CHANGELOG.md: cannot read: {exc.strerror}")
    else:
        if not dates:
            problems.append(f"CHANGELOG.md has no `## {version} - YYYY-MM-DD` section")
        elif len(dates) > 1:
            problems.append(f"CHANGELOG.md has {len(dates)} sections for {version}")
        else:
            try:
                date.fromisoformat(dates[0])
            except ValueError:
                problems.append(f"CHANGELOG.md: {dates[0]!r} is not a calendar date")
    return problems, f"tag {tag} and CHANGELOG.md agree on {version}"


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
