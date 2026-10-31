"""Designer binary discovery and output parsing."""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core import proc

TOOLS = ("sbscooker", "sbsrender", "substance3d_baker", "sbsupdater")
MAC_APP = Path("/Applications/Adobe Substance 3D Designer/Adobe Substance 3D Designer.app")
DEFAULT_DIRS = {
    "darwin": MAC_APP / "Contents/MacOS",
    "win32": Path(r"C:\Program Files\Adobe\Adobe Substance 3D Designer"),
    "linux": Path("/opt/Adobe/Adobe_Substance_3D_Designer"),
}


@dataclass
class Installation:
    bin_dir: Path
    source: str

    def binary(self, name: str) -> Path | None:
        path = self.bin_dir / (name + (".exe" if sys.platform == "win32" else ""))
        return path if path.is_file() and os.access(path, os.X_OK) else None

    def require(self, name: str) -> Path:
        path = self.binary(name)
        if path is None:
            raise RuntimeError(
                f"{name} not found in {self.bin_dir}; set CAVI_DESIGNER_APP or CAVI_DESIGNER_BIN_DIR"
            )
        return path

    @property
    def resources(self) -> Path | None:
        for candidate in (self.bin_dir.parent / "Resources", self.bin_dir / "resources",
                          self.bin_dir.parent / "resources"):
            if (candidate / "packages").is_dir():
                return candidate.resolve()
        return None


def installation() -> Installation:
    if os.environ.get("CAVI_DESIGNER_BIN_DIR"):
        return Installation(Path(os.environ["CAVI_DESIGNER_BIN_DIR"]).expanduser(),
                            "CAVI_DESIGNER_BIN_DIR")
    if os.environ.get("CAVI_DESIGNER_APP"):
        app = Path(os.environ["CAVI_DESIGNER_APP"]).expanduser()
        macos = app / "Contents/MacOS"
        return Installation(macos if macos.is_dir() else app, "CAVI_DESIGNER_APP")
    platform = "linux" if sys.platform.startswith("linux") else sys.platform
    return Installation(DEFAULT_DIRS.get(platform, DEFAULT_DIRS["linux"]), "default")


def failure(result: proc.Result, name: str) -> str:
    return f"{name} failed with exit {result.returncode}: {proc.diagnostics(result)}"


def parsed_stdout(result: proc.Result, name: str) -> str:
    """The stdout of a run whose report is parsed. A cut report is an error, not a short one."""
    if result.stdout_truncated:
        raise RuntimeError(
            f"{name} printed more than {proc.STDOUT_LIMIT} bytes; its report was truncated "
            "and cannot be read"
        )
    return result.stdout


async def version(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        result = await proc.run([path, "--version"], 20)
    except (OSError, TimeoutError):
        return None
    match = re.search(r"version\s+(\S+)", result.stdout + result.stderr)
    return match.group(1) if match else None


def parse_info(text: str) -> list[dict]:
    graphs = []
    for raw in text.splitlines():
        fields = raw.strip().split()
        if not fields:
            continue
        if fields[0] == "GRAPH-URL" and len(fields) == 2:
            graphs.append({"url": fields[1], "inputs": [], "outputs": []})
        elif graphs and fields[0] == "INPUT" and len(fields) >= 2:
            graphs[-1]["inputs"].append(
                {"id": fields[1], "type": fields[2] if len(fields) > 2 else ""})
        elif graphs and fields[0] == "OUTPUT" and len(fields) >= 2:
            graphs[-1]["outputs"].append(
                {"id": fields[1], "usage": fields[2] if len(fields) > 2 else ""})
    return graphs


def parse_report(text: str) -> list:
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.rstrip("\r\n") == "[":
            try:
                report, _ = json.JSONDecoder().raw_decode(text, offset)
            except ValueError:
                pass
            else:
                if isinstance(report, list):
                    return report
        offset += len(line)
    raise RuntimeError("sbsrender produced no JSON report")
