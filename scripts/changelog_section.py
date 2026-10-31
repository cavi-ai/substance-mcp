#!/usr/bin/env python3
"""Print one version's section from CHANGELOG.md.

The GitHub Release body is the changelog section, not a hand-written summary:
the notes a reader gets are the notes the repository already reviewed.
`gh release create --notes-file` consumes this output.

Exits non-zero when the version has no section or the section is empty, so a
tag cannot publish a release with a missing body.

    scripts/changelog_section.py 0.6.0 [--file CHANGELOG.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `## 0.6.0 - 2026-10-31`; the date is optional here and enforced by check_release.py.
HEADING = re.compile(r"^## +(?P<version>\S+)(?: +- +\d{4}-\d{2}-\d{2})? *$")
FENCE = re.compile(r"^ {0,3}(```|~~~)")


def section(text: str, version: str) -> str | None:
    """The body under `## <version>`, up to the next `## ` heading or the end of the file."""
    start = None
    in_fence = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.startswith("## "):
            continue
        if start is not None:
            return "\n".join(lines[start:index]).strip()
        match = HEADING.match(line)
        if match and match.group("version") == version:
            start = index + 1
    if start is None:
        return None
    return "\n".join(lines[start:]).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="Version to extract; a leading `v` is ignored.")
    parser.add_argument("--file", type=Path, default=ROOT / "CHANGELOG.md")
    args = parser.parse_args(argv)
    version = args.version.removeprefix("v")
    try:
        text = args.file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.file}: {exc.strerror}", file=sys.stderr)
        return 1
    body = section(text, version)
    if not body:
        print(f"error: {args.file.name} has no section for {version}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
