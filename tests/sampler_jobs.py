"""Run Sampler batch jobs from synchronous test code."""

import asyncio

from cavi_substance_mcp.sampler import batch


def run_job_file(*args, **kwargs) -> dict:
    """``batch.run_job_file_async`` to completion, with the same arguments."""
    return asyncio.run(batch.run_job_file_async(*args, **kwargs))
