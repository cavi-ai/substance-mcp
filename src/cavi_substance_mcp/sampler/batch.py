"""Host-side execution, locking and manifests for Sampler batch jobs."""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import job as jobs
from . import operations, runner, targets

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class BatchError(RuntimeError):
    code = 5


class AppUnavailable(BatchError):
    code = 3


class AppRunning(BatchError):
    code = 4


class LockTimeout(BatchError):
    code = 4


class JobTimeout(BatchError, TimeoutError):
    code = 6


def _root(workspace_path=None) -> Path:
    return Path(
        workspace_path or operations.workspace()
    ).expanduser().resolve(strict=True)


def _job_file(name, root: Path) -> Path:
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != ".json":
        raise jobs.JobValidationError("job file must be a JSON file inside the workspace")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path, job_dir: Path, skip: set[str]) -> list[dict]:
    rows = []
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in skip:
            continue
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return rows


def _inputs(root: Path, job: jobs.Job) -> list[dict]:
    rows = []
    for name in jobs.input_paths(job):
        path = (root / name).resolve()
        if not path.is_file():
            raise jobs.JobValidationError(f"input {name!r} does not exist")
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return rows


def _job_dir(root: Path, job: jobs.Job) -> Path:
    path = (root / job.directory).resolve()
    if path == root or not path.is_relative_to(root):
        raise jobs.JobValidationError("job directory must remain inside the workspace")
    if path.exists():
        raise jobs.JobValidationError("job directory already exists; choose a new directory")
    if not path.parent.is_dir():
        raise jobs.JobValidationError("job directory parent must exist")
    return path


class WorkspaceLock:
    def __init__(self, root: Path, timeout: float = 300):
        self.path = root / ".cavi-sampler.lock"
        self.timeout = timeout
        self.fd = None

    def _try(self) -> bool:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == "win32":
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while not self._try():
            if time.monotonic() >= deadline:
                raise LockTimeout("another Sampler batch job holds the workspace lock")
            time.sleep(0.2)
        return self

    def __exit__(self, *_):
        if self.fd is None:
            return
        try:
            if sys.platform == "win32":
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def lock_held(root: Path) -> bool:
    if not root.is_dir() or not (root / ".cavi-sampler.lock").is_file():
        return False
    probe = WorkspaceLock(root, timeout=0)
    if probe._try():
        probe.__exit__()
        return False
    return True


def app_available(wait_seconds: float = 0) -> None:
    found = runner.installation()
    if found.executable is None:
        raise AppUnavailable(
            "Sampler executable not found; set CAVI_SAMPLER_BIN or CAVI_SAMPLER_APP"
        )
    if not runner.running():
        return
    if wait_seconds <= 0:
        raise AppRunning(
            "Sampler is already running. Quit it before running a batch job."
        )
    deadline = time.monotonic() + wait_seconds
    while runner.running() and time.monotonic() < deadline:
        time.sleep(0.5)
    if runner.running():
        raise AppRunning(
            f"Sampler is still running after {wait_seconds:g} s; quit it and retry."
        )


def validate_job_file(name, workspace_path=None) -> dict:
    root = _root(workspace_path)
    path = _job_file(name, root)
    loaded = jobs.load_job(path)
    plan = jobs.validate_paths(loaded, root)
    plan["job"] = path.relative_to(root).as_posix()
    plan["targets"] = [
        {
            "target": step.target,
            "preset": step.preset or targets.preset_for(step.target),
            "assets": [step.asset] if step.asset else step.assets,
            "all_assets": step.all_assets,
            "outputs": step.outputs,
        }
        for step in loaded.steps if isinstance(step, jobs.Export)
    ]
    plan["import_options"] = sorted({
        step.import_option for step in loaded.steps if isinstance(step, jobs.ImportImages)
    })
    return plan


def check_sampler(plan: dict, probe: dict) -> None:
    installed = set(probe.get("export_presets", []))
    options = set(probe.get("import_options", []))
    problems = []
    for item in plan.get("targets", []):
        preset = item.get("preset")
        if preset is not None and preset not in installed:
            problems.append(
                f"export preset {preset!r} is not installed; available: "
                + ", ".join(sorted(installed)[:64])
            )
    for option in plan.get("import_options", []):
        if option not in options:
            problems.append(f"import option {option!r} is not available")
    if problems:
        raise jobs.JobValidationError("; ".join(problems))


def probe_sampler(workspace_path=None, timeout_seconds: float = 300,
                  lock_timeout: float = 30) -> dict:
    root = _root(workspace_path)
    with WorkspaceLock(root, lock_timeout):
        app_available(0)
        return asyncio.run(operations.invoke(
            "probe", {}, timeout_seconds, workspace_path=str(root)
        ))


def validate_with_sampler(name, workspace_path=None, timeout_seconds: float = 300) -> dict:
    plan = validate_job_file(name, workspace_path)
    probe = probe_sampler(workspace_path, timeout_seconds)
    check_sampler(plan, probe)
    plan["sampler"] = probe
    return plan


def doctor(workspace_path=None) -> dict:
    root = Path(
        workspace_path or operations.workspace()
    ).expanduser().resolve()
    writable = False
    if root.is_dir():
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".cavi-doctor-", delete=True):
                writable = True
        except OSError:
            writable = False
    return {
        **operations.status(),
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
        "workspace_writable": writable,
        "lock_held": lock_held(root),
        "targets": targets.catalog(),
    }


def _manifest(root: Path, job_dir: Path, path: Path, job: jobs.Job, result: dict,
              started: str, seconds: float) -> dict:
    found = runner.installation()
    manifest = {
        "schema": 1,
        "status": "success",
        "job": (job_dir / "job.json").relative_to(root).as_posix(),
        "job_sha256": _sha256(job_dir / "job.json"),
        "workspace": str(root),
        "app": {
            "executable": str(found.executable) if found.executable else None,
            "version": found.version,
        },
        "started": started,
        "seconds": round(seconds, 3),
        "project": result.get("project"),
        "assets": result.get("assets", []),
        "steps": result.get("steps", []),
        "inputs": _inputs(root, job),
    }
    manifest["outputs"] = _files(root, job_dir, {"manifest.json", "failure.json"})
    (job_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _failure(root: Path, job_dir: Path, path: Path, error: Exception,
             started: str, seconds: float) -> dict:
    payload = {
        "schema": 1,
        "status": "failed",
        "job": path.relative_to(root).as_posix(),
        "workspace": str(root),
        "started": started,
        "seconds": round(seconds, 3),
        "error": f"{type(error).__name__}: {error}",
    }
    (job_dir / "failure.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


async def run_job_file_async(name, workspace_path=None, timeout_seconds: float = 1800,
                             wait_for_app: float = 0, keep_on_failure: bool = False,
                             lock_timeout: float = 300) -> dict:
    root = _root(workspace_path)
    path = _job_file(name, root)
    loaded = jobs.load_job(path)
    jobs.validate_paths(loaded, root)
    started = datetime.now(UTC).isoformat()
    clock = time.monotonic()
    lock = WorkspaceLock(root, lock_timeout)
    await asyncio.to_thread(lock.__enter__)
    try:
        app_available(wait_for_app)
        job_dir = _job_dir(root, loaded)
        job_dir.mkdir()
        payload = loaded.model_dump(by_alias=True)
        (job_dir / "job.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        try:
            result = await operations.invoke_job(
                payload, timeout_seconds, workspace_path=str(root)
            )
            return _manifest(root, job_dir, path, loaded, result, started,
                             time.monotonic() - clock)
        except Exception as exc:
            if keep_on_failure:
                _failure(root, job_dir, path, exc, started, time.monotonic() - clock)
            else:
                operations.remove_tree(job_dir)
            if isinstance(exc, TimeoutError):
                raise JobTimeout(str(exc)) from exc
            raise
    finally:
        lock.__exit__()


def run_job_file(name, workspace_path=None, timeout_seconds: float = 1800,
                 wait_for_app: float = 0, keep_on_failure: bool = False,
                 lock_timeout: float = 300) -> dict:
    return asyncio.run(run_job_file_async(
        name, workspace_path, timeout_seconds, wait_for_app, keep_on_failure, lock_timeout
    ))
