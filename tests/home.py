"""Point the user's home directory at a scratch folder for the duration of a test."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def isolate_home(case):
    """Give ``case`` an empty home and no CAVI_SUBSTANCE_WORKSPACE; returns the home path.

    Both HOME and USERPROFILE are set because Path.home() reads one or the other by
    platform.
    """
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    environment = {key: value for key, value in os.environ.items()
                   if key != "CAVI_SUBSTANCE_WORKSPACE"}
    environment.update(HOME=folder.name, USERPROFILE=folder.name)
    patcher = patch.dict(os.environ, environment, clear=True)
    patcher.start()
    case.addCleanup(patcher.stop)
    return Path(folder.name).resolve()
