"""Sampler executable discovery and bounded silent-script execution."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core import discovery, proc

# Sampler's output is only diagnostics (the response travels in a file).
OUTPUT_LIMIT = 1024 * 1024


@dataclass
class Installation:
    executable: Path | None
    source: str

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
        """Blocks for up to a probe's timeout the first time; call from a thread in async code."""
        return discovery.version(self.executable)


def installation() -> Installation:
    found = discovery.find_executable(
        "sampler", "Adobe Substance 3D Sampler", "CAVI_SAMPLER_BIN", "CAVI_SAMPLER_APP")
    return Installation(found.path, found.source)


def scripts_dir() -> Path:
    """Where Sampler keeps its startup scripts; CAVI_SAMPLER_SCRIPT_PATH overrides the guess."""
    override = os.environ.get("CAVI_SAMPLER_SCRIPT_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path.home() / "Library/Application Support" if sys.platform == "darwin" else (
            Path.home() / ".local/share")
    return base / "Adobe/Adobe Substance 3D Sampler/scripts"


def running() -> bool | None:
    return discovery.running(installation().executable)


async def run_script(script: Path, timeout: float, job_dir: Path, extra=()) -> proc.Result:
    executable = installation().require()
    env = dict(os.environ)
    # Adobe's own variable: Sampler 4.0 and later read it to load scripts at startup.
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
