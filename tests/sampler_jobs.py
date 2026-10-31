"""Sampler batch jobs for tests: a job builder and a synchronous runner."""

import asyncio

from cavi_substance_mcp.sampler import batch


def run_job_file(*args, **kwargs) -> dict:
    """``batch.run_job_file_async`` to completion, with the same arguments."""
    return asyncio.run(batch.run_job_file_async(*args, **kwargs))


def job_data(directory="jobs/rock", target="unreal", asset="Rock", project_asset=True,
             **changes) -> dict:
    """A valid batch job: import an image, add and tune a filter, apply a preset, export.

    ``changes`` replace top-level keys of the job (``steps=[...]``, ``project={...}``).
    The image ``photos/albedo.png`` has to exist in the workspace to run it.
    """
    project = {"name": "RockProject"}
    if project_asset:
        project["asset"] = {"name": asset, "model": "openpbr", "preset": "base_material"}
    data = {
        "schema": 1,
        "directory": directory,
        "project": project,
        "steps": [
            {"action": "import_images", "asset": asset, "images": ["photos/albedo.png"],
             "position": 0, "import_option": "use_as_bitmap"},
            {"action": "insert_filter", "asset": asset, "filter": "Tiling", "position": 1},
            {"action": "set_parameters", "asset": asset, "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "apply_preset", "asset": asset, "layer": "Tiling", "preset": "Wet"},
            {"action": "export", "asset": asset, "target": target, "resolution": 1024},
        ],
    }
    data.update(changes)
    return data
