"""Sampler executable discovery and bounded silent-script execution."""

import asyncio
import os
import plistlib
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAC_APP = Path("/Applications/Adobe Substance 3D Sampler/Adobe Substance 3D Sampler.app")
MAC_EXE = MAC_APP / "Contents/MacOS/Adobe Substance 3D Sampler"
DEFAULT_EXECUTABLES = {
    "darwin": MAC_EXE,
    "win32": Path(r"C:\Program Files\Adobe\Adobe Substance 3D Sampler\Adobe Substance 3D Sampler.exe"),
    "linux": Path("/opt/Adobe/Adobe_Substance_3D_Sampler/Adobe Substance 3D Sampler"),
}
STDOUT_LIMIT = 1024 * 1024
STDERR_LIMIT = 1024 * 1024


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


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool


async def _drain(stream, limit: int):
    kept, size, truncated = [], 0, False
    while chunk := await stream.read(65536):
        if size < limit:
            kept.append(chunk[: limit - size])
        truncated = truncated or size + len(chunk) > limit
        size += len(chunk)
    return b"".join(kept), truncated


def _kill(process):
    if process.returncode is not None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


async def _collect(process):
    out, err, _ = await asyncio.gather(
        _drain(process.stdout, STDOUT_LIMIT), _drain(process.stderr, STDERR_LIMIT), process.wait()
    )
    return out, err


async def run_script(script: Path, timeout: float, job_dir: Path) -> Result:
    executable = installation().require()
    env = dict(os.environ)
    env["SAMPLER_SCRIPT_PATH"] = str(job_dir)
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    process = await asyncio.create_subprocess_exec(
        str(executable), "--skip-version-check", "--run-script-silent", str(script),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=job_dir,
        start_new_session=sys.platform != "win32",
    )
    try:
        (out, cut), (err, _) = await asyncio.wait_for(_collect(process), timeout)
    except TimeoutError:
        _kill(process)
        await process.wait()
        raise TimeoutError(f"Sampler exceeded {timeout:g} s and was killed") from None
    finally:
        _kill(process)
    return Result(process.returncode, out.decode(errors="replace"),
                  err.decode(errors="replace"), cut)


def diagnostics(result: Result) -> str:
    lines = [line for line in (result.stderr + "\n" + result.stdout).splitlines()
             if line.strip() and "[info]" not in line.lower()]
    return " | ".join(lines[-6:])[-1500:] or "no diagnostics"


def failure(result: Result) -> str:
    return f"Sampler exited {result.returncode}: {diagnostics(result)}"
