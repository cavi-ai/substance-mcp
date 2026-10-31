"""Host side of a Sampler launch: the request file, the workspace lock and the process.

The code that runs inside Sampler is operations.py. This module starts Sampler with a
generated driver script, hands it a request file and reads the response back.
"""

import asyncio
import contextlib
import contextvars
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from ..core import proc, workspace
from . import runner
from .errors import AppRunning
from .lock import WorkspaceLock
from .operations import PROJECT

RESPONSE_LIMIT = 8 * 1024 * 1024
LOCK_WAIT = 60
WORK_PREFIX = ".cavi-work-"
# Operations that save the project they open. They run on a disposable copy so a
# failure or a timeout kill can never leave the user's .ssa half written.
MUTATING = frozenset({
    "create_asset", "delete_asset", "import_images", "import_textures", "insert_filter",
    "insert_material", "set_parameters", "apply_preset", "set_metadata",
    "activate_channels", "deactivate_channels", "set_channels", "set_blend_parameters",
    "remove_layer",
})
_HELD = contextvars.ContextVar("cavi_sampler_held_locks", default=frozenset())

DRIVER = '''import json
import sys
from pathlib import Path

PACKAGE_ROOT = {package_root!r}
REQUEST = Path({request!r})
RESPONSE = Path({response!r})


def main():
    data = json.loads(REQUEST.read_text(encoding="utf-8"))
    try:
        sys.path.insert(0, PACKAGE_ROOT)
        from cavi_substance_mcp.sampler.operations import (
            execute_in_sampler, execute_job, preflight_job,
        )

        if "preflight" in data:
            result = preflight_job(
                data["preflight"], data["workspace"], data["project"]
            )
        elif "job" in data:
            result = execute_job(data["job"], data["workspace"], data.get("version"))
        else:
            result = execute_in_sampler(
                data["operation"], data.get("arguments") or {{}}, data["workspace"],
                data.get("version"),
            )
    except BaseException as exc:
        RESPONSE.write_text(
            json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}}),
            encoding="utf-8",
        )
    else:
        RESPONSE.write_text(
            json.dumps({{"ok": True, "result": result}}, default=str), encoding="utf-8"
        )


main()
'''


def status() -> dict:
    found = runner.installation()
    root = workspace.root()
    scripts = runner.scripts_dir()
    return {
        "available": found.executable is not None,
        "executable": str(found.executable) if found.executable else None,
        "source": found.source,
        "version": found.version,
        "scripts_dir": str(scripts),
        "scripts_dir_exists": scripts.is_dir(),
        "running": runner.running(),
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
    }


async def _tail_progress(path: Path, callback, stop: asyncio.Event):
    seen = 0
    while True:
        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines[seen:]:
                seen += 1
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                with contextlib.suppress(Exception):
                    callback(event)
        if stop.is_set():
            return
        await asyncio.sleep(0.2)


@contextlib.asynccontextmanager
async def exclusive(root, lock_timeout: float = LOCK_WAIT):
    """Hold the workspace lock for one or more Sampler launches.

    Every launch goes through this, so single-operation tools, preflight checks and
    batch jobs never run Sampler concurrently. Re-entrant within one task so a batch
    job can hold the lock across its directory setup and the launch itself.
    """
    root = Path(root)
    key = str(root.resolve())
    if key in _HELD.get():
        yield
        return
    async with WorkspaceLock(root, lock_timeout):
        token = _HELD.set(_HELD.get() | {key})
        try:
            yield
        finally:
            _HELD.reset(token)


async def _run_payload(payload: dict, timeout_seconds: float, progress_file=None,
                       on_progress=None, lock_timeout: float = LOCK_WAIT,
                       aliases=None) -> dict:
    async with exclusive(payload["workspace"], lock_timeout):
        # Checked under the lock, immediately before launching, so two of our own
        # launches cannot race each other between the check and the start.
        if runner.running():
            raise AppRunning(
                "Sampler is already running. Save and quit it; Sampler runs startup "
                "scripts only when no other instance is active."
            )
        return await _launch(payload, timeout_seconds, progress_file, on_progress, aliases)


async def _launch(payload: dict, timeout_seconds: float, progress_file=None,
                  on_progress=None, aliases=None) -> dict:
    # The version probe is part of the caller's budget, not an addition to it.
    budget = time.monotonic() + timeout_seconds
    version = await asyncio.to_thread(
        lambda: runner.installation().version_within(timeout_seconds))
    if (remaining := budget - time.monotonic()) <= 0:
        raise TimeoutError(f"Sampler exceeded {timeout_seconds:g} s before it was started")
    job = Path(tempfile.mkdtemp(prefix="cavi-sampler-"))
    request, response, script = job / "request.json", job / "response.json", job / "run.py"
    stop = asyncio.Event()
    tailer = None
    if progress_file is not None and on_progress is not None:
        tailer = asyncio.create_task(_tail_progress(Path(progress_file), on_progress, stop))
    try:
        request.write_text(
            json.dumps({**payload, "version": version}, allow_nan=False), encoding="utf-8"
        )
        script.write_text(
            DRIVER.format(
                package_root=str(Path(__file__).resolve().parents[2]),
                request=str(request),
                response=str(response),
            ),
            encoding="utf-8",
        )
        started = time.monotonic()
        result = await runner.run_script(script, remaining, job)
        deadline = time.monotonic() + 2
        while not response.is_file() and result.returncode == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if not response.is_file():
            if result.returncode != 0:
                raise RuntimeError(runner.failure(result))
            raise RuntimeError(
                "Sampler exited without running the script. Quit any running Sampler window, "
                "then retry. " + proc.diagnostics(result)
            )
        if response.stat().st_size > RESPONSE_LIMIT:
            raise RuntimeError("Sampler response exceeds size limit")
        try:
            payload = json.loads(response.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Sampler returned an unreadable response") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            detail = str(
                payload.get("error") if isinstance(payload, dict) else payload
                or "Sampler operation failed"
            )
            detail = _unalias(detail, aliases)
            for kind in (ValueError, FileExistsError, TypeError, RuntimeError):
                prefix = f"{kind.__name__}: "
                if detail.startswith(prefix):
                    raise kind(detail[len(prefix):])
            raise RuntimeError(detail)
        if result.returncode != 0:
            # A response file proves the script ran, not that Sampler shut down cleanly.
            raise RuntimeError(runner.failure(result))
        reply = payload.get("result")
        if not isinstance(reply, dict):
            raise TypeError("Sampler operation returned an unexpected response")
        reply["seconds"] = round(time.monotonic() - started, 3)
        return reply
    finally:
        if tailer is not None:
            stop.set()
            await tailer
        workspace.remove_scratch(job)


def _unalias(value, aliases):
    if not aliases:
        return value
    if isinstance(value, str):
        for old, new in aliases.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_unalias(item, aliases) for item in value]
    if isinstance(value, dict):
        return {key: _unalias(item, aliases) for key, item in value.items()}
    return value


async def _copy_file(source: Path, target: Path) -> None:
    """Copy without blocking the event loop or leaving a stray thread on cancellation."""
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(8 * 1024 * 1024):
            writer.write(chunk)
            await asyncio.sleep(0)
    shutil.copystat(source, target)


async def _invoke_on_copy(root: Path, operation: str, arguments: dict,
                          timeout_seconds: float, lock_timeout: float) -> dict:
    """Run a mutating operation on a copy and replace the project only on success."""
    original = workspace.input_file(root, arguments["project"], PROJECT)
    work = original.with_name(f"{WORK_PREFIX}{uuid.uuid4().hex[:12]}-{original.name}")
    aliases = {workspace.rel(root, work): workspace.rel(root, original)}
    try:
        await _copy_file(original, work)
        reply = await _run_payload(
            {"operation": operation, "workspace": str(root),
             "arguments": {**arguments, "project": workspace.rel(root, work)}},
            timeout_seconds, lock_timeout=lock_timeout, aliases=aliases,
        )
        if reply.get("saved"):
            if not work.is_file() or work.stat().st_size == 0:
                raise RuntimeError("Sampler reported a save but wrote no project data")
            os.replace(work, original)
    finally:
        work.unlink(missing_ok=True)
    return _unalias(reply, aliases)


async def invoke(operation: str, arguments: dict, timeout_seconds: float,
                 workspace_path=None, lock_timeout: float = LOCK_WAIT) -> dict:
    # Every launch takes the lock file in the workspace, so even a read-only operation
    # needs the directory: the default is created, an explicit one must exist.
    root = workspace.root(workspace_path, create=True)
    if operation in MUTATING and arguments.get("project") is not None:
        # Hold the lock across copy, run and replace so two edits of one project
        # cannot lose each other's changes.
        async with exclusive(root, lock_timeout):
            return await _invoke_on_copy(
                root, operation, arguments, timeout_seconds, lock_timeout)
    return await _run_payload(
        {"operation": operation, "arguments": arguments, "workspace": str(root)},
        timeout_seconds, lock_timeout=lock_timeout,
    )


async def invoke_preflight(job: dict, timeout_seconds: float, workspace_path,
                           project: str, lock_timeout: float = LOCK_WAIT) -> dict:
    root = workspace.existing(workspace_path)
    return await _run_payload(
        {"preflight": job, "workspace": str(root), "project": project}, timeout_seconds,
        lock_timeout=lock_timeout,
    )


async def invoke_job(job: dict, timeout_seconds: float, workspace_path=None,
                     progress_file=None, on_progress=None,
                     lock_timeout: float = LOCK_WAIT) -> dict:
    root = workspace.root(workspace_path, create=True)
    return await _run_payload(
        {"job": job, "workspace": str(root)}, timeout_seconds,
        progress_file=progress_file, on_progress=on_progress, lock_timeout=lock_timeout,
    )


