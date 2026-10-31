"""Host-side channel packing for the Sampler server."""

import asyncio
import re
import time
from pathlib import Path

from .. import packing, receipts
from ..painter_operations import export_destination, input_file, remove_tree
from . import operations


def pack_sync(directory: str, sources: dict[str, str], spec: packing.PackSpec,
              workspace_path=None) -> dict:
    if not isinstance(directory, str) or not directory:
        raise ValueError("directory must be a non-empty workspace-relative path")
    if not isinstance(sources, dict) or not sources or len(sources) > 32:
        raise ValueError("sources must be a non-empty object with at most 32 usages")
    root = Path(workspace_path).expanduser().resolve(strict=True) if workspace_path else (
        operations.workspace()
    )
    files = {}
    for usage, name in sources.items():
        if not isinstance(usage, str) or not re.fullmatch(packing.USAGE, usage):
            raise ValueError(f"Invalid usage name: {usage}")
        try:
            files[usage] = input_file(root, name, packing.READABLE)
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                "Input must be a supported file inside the workspace"
            ) from exc
    packing.check_spec(spec)
    started = time.monotonic()
    candidate = (root / directory).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("Output must remain inside the workspace")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    destination = export_destination(root, directory)
    destination.mkdir()
    try:
        result = packing.pack(spec, files, destination)
        manifest = receipts.build(
            "sampler", "pack", root, inputs=list(files.values()),
            outputs=[item["path"] for item in result["maps"]],
            parameters={"pack": spec.model_dump(mode="json")},
            seconds=time.monotonic() - started,
        )
        manifest_path = receipts.write(destination, manifest)
    except BaseException:
        remove_tree(destination)
        raise
    return {
        "directory": destination.relative_to(root).as_posix(),
        "maps": [
            {**item, "path": item["path"].relative_to(root).as_posix()}
            for item in result["maps"]
        ],
        "sources": result["sources"],
        "manifest": manifest_path.relative_to(root).as_posix(),
    }


async def pack(directory: str, sources: dict[str, str], spec: packing.PackSpec,
               workspace_path=None) -> dict:
    return await asyncio.to_thread(pack_sync, directory, sources, spec, workspace_path)
