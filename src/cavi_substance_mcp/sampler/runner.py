"""Sampler executable discovery and bounded silent-script execution."""

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core import proc

MAC_APP = Path("/Applications/Adobe Substance 3D Sampler/Adobe Substance 3D Sampler.app")
MAC_EXE = MAC_APP / "Contents/MacOS/Adobe Substance 3D Sampler"
DEFAULT_EXECUTABLES = {
    "darwin": MAC_EXE,
    "win32": Path(
        r"C:\Program Files\Adobe\Adobe Substance 3D Sampler\Adobe Substance 3D Sampler.exe"
    ),
    "linux": Path("/opt/Adobe/Adobe_Substance_3D_Sampler/Adobe Substance 3D Sampler"),
}
# Sampler's output is only diagnostics (the response travels in a file).
OUTPUT_LIMIT = 1024 * 1024


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


@dataclass
class Installation:
    executable: Path | None
    source: str
    app: Path | None = None

    def require(self) -> Path:
        if self.executable is None or not self.executable.is_file():
            raise RuntimeError(
                "Sampler executable not found; set CAVI_SAMPLER_BIN or CAVI_SAMPLER_APP"
            )
        if sys.platform != "win32" and not os.access(self.executable, os.X_OK):
            raise RuntimeError("Sampler executable is not runnable; check file permissions")
        return self.executable

    @property
    def version(self) -> str | None:
        if self.app is None:
            return None
        try:
            with (self.app / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None
        value = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
        return value if isinstance(value, str) else None


def _from_app(app: Path, source: str) -> Installation:
    app = app.expanduser()
    macos = app / "Contents/MacOS"
    if app.is_dir() and macos.is_dir():
        try:
            with (app / "Contents/Info.plist").open("rb") as stream:
                name = plistlib.load(stream).get("CFBundleExecutable")
        except (OSError, plistlib.InvalidFileException, ValueError):
            name = None
        candidate = macos / (name if isinstance(name, str) and name else app.stem)
    else:
        candidate = app
    return Installation(candidate if candidate.is_file() else None, source, app)


def installation() -> Installation:
    if os.environ.get("CAVI_SAMPLER_BIN"):
        value = Path(os.environ["CAVI_SAMPLER_BIN"]).expanduser()
        if value.is_dir():
            value = value / DEFAULT_EXECUTABLES.get(_platform(), DEFAULT_EXECUTABLES["linux"]).name
        return Installation(value if value.is_file() else None, "CAVI_SAMPLER_BIN")
    if os.environ.get("CAVI_SAMPLER_APP"):
        return _from_app(Path(os.environ["CAVI_SAMPLER_APP"]), "CAVI_SAMPLER_APP")
    default = DEFAULT_EXECUTABLES.get(_platform(), DEFAULT_EXECUTABLES["linux"])
    return _from_app(default.parents[2] if default == MAC_EXE else default,
                     "default")


def scripts_dir() -> Path:
    override = os.environ.get("SAMPLER_SCRIPT_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path.home() / "Library/Application Support" if sys.platform == "darwin" else (
            Path.home() / ".local/share")
    return base / "Adobe/Adobe Substance 3D Sampler/scripts"


def running() -> bool | None:
    target = _normal_path(installation().executable)
    if target is None:
        return False
    if sys.platform.startswith("linux"):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if _normal_path((entry / "exe").resolve()) == target:
                    return True
            except OSError:
                continue
        return False
    if sys.platform == "win32":
        try:
            output = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {target.name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=True, timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return target.name.lower() in output.lower()
    if sys.platform != "darwin":
        return None
    try:
        output = subprocess.run(
            ["/bin/ps", "-axo", "comm="],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return any(_normal_path(Path(line.strip())) == target for line in output.splitlines())


def _normal_path(path):
    if path is None:
        return None
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


async def run_script(script: Path, timeout: float, job_dir: Path, extra=()) -> proc.Result:
    executable = installation().require()
    env = dict(os.environ)
    env["SAMPLER_SCRIPT_PATH"] = str(job_dir)
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return await proc.run(
        [executable, "--skip-version-check", "--run-script-silent", script, *extra],
        timeout, cwd=job_dir, env=env, stdout_limit=OUTPUT_LIMIT, stderr_limit=OUTPUT_LIMIT,
        label="Sampler",
    )


def failure(result: proc.Result) -> str:
    return f"Sampler exited {result.returncode}: {proc.diagnostics(result)}"
