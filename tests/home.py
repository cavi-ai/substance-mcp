"""Point the user's home directory at a scratch folder for the duration of a test."""

import tempfile
from pathlib import Path

from env import patched_environment


def isolate_home(case):
    """Give ``case`` an empty home and no CAVI_SUBSTANCE_WORKSPACE; returns the home path.

    Both HOME and USERPROFILE are set because Path.home() reads one or the other by
    platform.
    """
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    patcher = patched_environment(
        drop=("CAVI_SUBSTANCE_WORKSPACE",), add={"HOME": folder.name, "USERPROFILE": folder.name})
    patcher.start()
    case.addCleanup(patcher.stop)
    return Path(folder.name).resolve()
