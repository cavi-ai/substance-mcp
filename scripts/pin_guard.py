"""Fail a pull request that adds or moves a pinned dependency or changes how consumers are pinned.

Consumers pin substance-mcp main through the pin-consumers job. This repository pins nothing by
hand: no submodule, no git dependency in pyproject.toml, uv.lock or package.json.

Usage: pin_guard.py BASE HEAD, in a checkout that holds both commits.
"""

import json
import re
import subprocess
import sys
import tomllib

GUARD = "scripts/pin_guard.py"
JOBS = {".github/workflows/ci.yml": ("pin-guard", "pin-consumers")}
GIT_PACKAGE = re.compile(r"^(git\+|git@|git:|github:|ssh://|https?://)|\.git(#|$)")


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def show(rev, path):
    return git("show", f"{rev}:{path}")


def job(text, name):
    """The lines of one top-level workflow job, or None when it is absent."""
    lines = (text or "").splitlines()
    if f"  {name}:" not in lines:
        return None
    start = lines.index(f"  {name}:")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^ {0,2}\S", lines[i])),
               len(lines))
    return lines[start:end]


def git_pins(rev):
    """Git-sourced dependencies declared or resolved at one commit."""
    project = tomllib.loads(show(rev, "pyproject.toml") or "")
    lock = tomllib.loads(show(rev, "uv.lock") or "")
    npm = json.loads(show(rev, "package.json") or "{}")
    return {
        "pyproject.toml": project.get("tool", {}).get("uv", {}).get("sources", {}),
        "uv.lock": {p["name"]: p["source"]["git"] for p in lock.get("package", [])
                    if "git" in p.get("source", {})},
        "package.json": {name: value for group in ("dependencies", "devDependencies")
                         for name, value in npm.get(group, {}).items()
                         if GIT_PACKAGE.search(value)},
    }


def violations(base, head):
    found = []
    before = show(base, GUARD)
    if before is not None and before != show(head, GUARD):
        found.append((GUARD, "changes the pin guard"))
    for path, names in JOBS.items():
        for name in names:
            before = job(show(base, path), name)
            if before is not None and before != job(show(head, path), name):
                found.append((path, f"changes the {name} job"))
    if show(base, ".gitmodules") != show(head, ".gitmodules"):
        found.append((".gitmodules", "adds or moves a submodule"))
    for line in (git("diff", "--raw", "--no-renames", base, head) or "").splitlines():
        meta, path = line.split("\t", 1)
        if "160000" in meta.split()[:2]:
            found.append((path, "adds or moves a submodule"))
    before, after = git_pins(base), git_pins(head)
    found += [(path, "adds or moves a git dependency pin") for path in before
              if before[path] != after[path]]
    return found


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base = (git("merge-base", argv[0], argv[1]) or "").strip()
    if not base:
        print(f"::error::No merge base for {argv[0]} and {argv[1]}")
        return 1
    found = violations(base, argv[1])
    for path, message in found:
        print(f"::error file={path}::{path} {message}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
