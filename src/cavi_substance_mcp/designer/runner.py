"""Designer binary discovery and output parsing."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ..core import discovery, proc

TOOLS = ("sbscooker", "sbsrender", "substance3d_baker")


@dataclass
class Installation:
    bin_dir: Path
    source: str

    def binary(self, name: str) -> Path | None:
        return discovery.tool(self.bin_dir, name)

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
    found = discovery.find_directory(
        "designer", "sbsrender", "CAVI_DESIGNER_BIN_DIR", "CAVI_DESIGNER_APP")
    return Installation(found.path, found.source)


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
    return await asyncio.to_thread(discovery.version, path)


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
