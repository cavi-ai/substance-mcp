"""Host-side channel packing for the Sampler server."""

import asyncio
import re
import time

from .. import packing, receipts
from ..core import workspace


def pack_sync(directory: str, sources: dict[str, str], spec: packing.PackSpec,
              workspace_path=None) -> dict:
    if not isinstance(directory, str) or not directory:
        raise ValueError("directory must be a non-empty workspace-relative path")
    if not isinstance(sources, dict) or not sources or len(sources) > 32:
        raise ValueError("sources must be a non-empty object with at most 32 usages")
    # The sources are read from the workspace, so it has to exist; packing never creates it.
    root = workspace.existing_root(workspace_path)
    files = {}
    for usage, name in sources.items():
        if not isinstance(usage, str) or not re.fullmatch(packing.USAGE, usage):
            raise ValueError(f"Invalid usage name: {usage}")
        files[usage] = workspace.input_file(root, name, packing.READABLE)
    packing.check_spec(spec)
    started = time.monotonic()
    with workspace.new_output_directory(root, directory, parents=True) as destination:
        result = packing.pack(spec, files, destination)
        manifest_path = receipts.write_manifest(
            destination, "sampler", "pack", root, inputs=list(files.values()),
            outputs=[item["path"] for item in result["maps"]],
            parameters={"pack": spec.model_dump(mode="json")},
            seconds=time.monotonic() - started,
        )
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
