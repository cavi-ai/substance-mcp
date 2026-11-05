"""Where the Substance applications install, for the lookups in ``cavi_mcp_core.apps``.

Host side only. Every lookup follows the same order:

1. an environment override (``CAVI_DESIGNER_BIN_DIR``, ``CAVI_SAMPLER_BIN`` and so on),
   which is final: a wrong override is reported as not found, never replaced;
2. the platform's default install location (``DEFAULTS``);
3. ``shutil.which`` on ``PATH``.
"""

import os
from pathlib import Path, PurePosixPath

from cavi_mcp_core import apps

# Where each application installs by default. ``{programs}`` is Windows' Program Files.
DEFAULTS = {
    "designer": {
        "darwin": "/Applications/Adobe Substance 3D Designer/"
                  "Adobe Substance 3D Designer.app/Contents/MacOS",
        "win32": r"{programs}\Adobe\Adobe Substance 3D Designer",
        "linux": "/opt/Adobe/Adobe_Substance_3D_Designer",
    },
    "sampler": {
        "darwin": "/Applications/Adobe Substance 3D Sampler/Adobe Substance 3D Sampler.app",
        "win32": r"{programs}\Adobe\Adobe Substance 3D Sampler\Adobe Substance 3D Sampler.exe",
        "linux": "/opt/Adobe/Adobe_Substance_3D_Sampler/Adobe Substance 3D Sampler",
    },
    "painter": {
        "darwin": "/Applications/Adobe Substance 3D Painter/Adobe Substance 3D Painter.app",
        "win32": r"{programs}\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
        "linux": "/opt/Adobe/Adobe_Substance_3D_Painter/Adobe Substance 3D Painter",
    },
}


def default_location(key: str) -> Path:
    """The default install location of ``key`` on this platform (it may not exist)."""
    table = DEFAULTS[key]
    text = table.get(apps.platform_name(), table["linux"])
    programs = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    text = text.format(programs=programs)
    # A macOS or Linux location keeps forward slashes even when built on Windows.
    return Path(text) if text.startswith(programs) or os.sep == "/" else PurePosixPath(text)


def find_directory(key: str, probe: str, env_dir: str, env_app: str) -> apps.Found:
    """``apps.find_directory`` at the default location of ``key``."""
    return apps.find_directory(default_location(key), probe, env_dir, env_app)


def find_app(key: str, which: str, env_app: str | None) -> apps.Found:
    """``apps.find_app`` at the default location of ``key``."""
    return apps.find_app(default_location(key), which, env_app)


def find_executable(key: str, which: str, env_bin: str | None, env_app: str | None) -> apps.Found:
    """``apps.find_executable`` at the default location of ``key``."""
    return apps.find_executable(default_location(key), which, env_bin, env_app)
