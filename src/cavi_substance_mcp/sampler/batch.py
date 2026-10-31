"""Host-side execution, locking and manifests for Sampler batch jobs."""

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import job as jobs
from . import operations, runner, targets
from .errors import (  # noqa: F401 -- re-exported for callers of this module
    AppRunning,
    AppUnavailable,
    BatchError,
    JobTimeout,
    LockTimeout,
)
from .lock import WorkspaceLock, lock_held  # noqa: F401 -- re-exported


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
    plan["source"] = loaded.project.source
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
    plan["filters"] = sorted({
        step.filter for step in loaded.steps if isinstance(step, jobs.InsertFilter)
    })
    plan["materials"] = sorted({
        step.material for step in loaded.steps if isinstance(step, jobs.InsertMaterial)
    })
    models = set()
    if loaded.project.asset is not None:
        models.add(loaded.project.asset.model)
    for step in loaded.steps:
        if isinstance(step, jobs.CreateAsset):
            models.add(step.model)
    plan["material_models"] = sorted(models)
    plan["imports_resources"] = any(
        isinstance(step, jobs.ImportResource) for step in loaded.steps
    )
    imported, undeclared = set(), False
    for step in loaded.steps:
        if isinstance(step, jobs.ImportResource):
            if step.resources:
                imported.update(step.resources)
            else:
                undeclared = True
    plan["imported_resources"] = sorted(imported)
    plan["preflight_skip_resources"] = undeclared
    if undeclared:
        plan["preflight_warnings"] = [
            "imported resources are not declared; filter/material checks skipped"
        ]
    return plan


def check_sampler(plan: dict, probe: dict) -> None:
    installed = set(probe.get("export_presets", []))
    options = set(probe.get("import_options", []))
    filters = set(probe.get("filters", []))
    materials = set(probe.get("materials", []))
    models = set(probe.get("material_models", []))
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
    declared = set(plan.get("imported_resources", []))
    skip_resources = bool(plan.get("imports_resources")) and bool(
        plan.get("preflight_skip_resources")
    )
    if plan.get("source") is None and not skip_resources:
        for name in plan.get("filters", []):
            if name not in declared and name not in filters:
                problems.append(f"filter {name!r} is not available")
        for name in plan.get("materials", []):
            if name not in declared and name not in materials:
                problems.append(f"material {name!r} is not available")
    for name in plan.get("material_models", []):
        if name not in models:
            problems.append(f"material model {name!r} is not available")
    if problems:
        raise jobs.JobValidationError("; ".join(problems))


async def probe_sampler_async(workspace_path=None, timeout_seconds: float = 300,
                            lock_timeout: float = 30) -> dict:
    root = _root(workspace_path)
    async with WorkspaceLock(root, lock_timeout):
        app_available(0)
        return await operations.invoke(
            "probe", {}, timeout_seconds, workspace_path=str(root)
        )


def probe_sampler(workspace_path=None, timeout_seconds: float = 300,
                  lock_timeout: float = 30) -> dict:
    return asyncio.run(probe_sampler_async(workspace_path, timeout_seconds, lock_timeout))


async def validate_with_sampler_async(name, workspace_path=None,
                                      timeout_seconds: float = 300) -> dict:
    plan = validate_job_file(name, workspace_path)
    if plan.get("source") is not None:
        plan.setdefault("preflight_warnings", []).append(
            "source-project filters and materials need --check-project"
        )
    probe = await probe_sampler_async(workspace_path, timeout_seconds)
    check_sampler(plan, probe)
    plan["sampler"] = probe
    return plan


def validate_with_sampler(name, workspace_path=None, timeout_seconds: float = 300) -> dict:
    return asyncio.run(validate_with_sampler_async(name, workspace_path, timeout_seconds))


async def validate_with_project_async(name, workspace_path=None,
                                      timeout_seconds: float = 300) -> dict:
    root = _root(workspace_path)
    path = _job_file(name, root)
    loaded = jobs.load_job(path)
    plan = validate_job_file(path, root)
    if loaded.project.source is None:
        probe = await probe_sampler_async(root, timeout_seconds)
        check_sampler(plan, probe)
        plan["sampler"] = probe
        plan["project_check"] = "not_applicable"
        return plan
    temporary = Path(tempfile.mkdtemp(prefix=".cavi-preflight-", dir=root))
    try:
        source = (root / loaded.project.source).resolve()
        copy = temporary / "project.ssa"
        shutil.copy2(source, copy)
        result = await operations.invoke_preflight(
            loaded.model_dump(by_alias=True), timeout_seconds, str(root),
            copy.relative_to(root).as_posix(),
        )
        probe = result.get("probe", {})
        check_sampler(plan, probe)
        plan["sampler"] = probe
        plan["project_check"] = "failed" if result.get("problems") else "ok"
        plan["project_warnings"] = result.get("warnings", [])
        if result.get("problems"):
            raise jobs.JobValidationError("; ".join(result["problems"]))
        return plan
    finally:
        operations.remove_tree(temporary)


def validate_with_project(name, workspace_path=None, timeout_seconds: float = 300) -> dict:
    return asyncio.run(validate_with_project_async(name, workspace_path, timeout_seconds))


def _last_progress_error(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("status") == "error":
            return event
    return None


async def check_build_async(name, workspace_path=None,
                            timeout_seconds: float = 1800) -> dict:
    root = _root(workspace_path)
    path = _job_file(name, root)
    loaded = jobs.load_job(path)
    plan = validate_job_file(path, root)
    if any(isinstance(step, jobs.ImportResource) for step in loaded.steps):
        raise jobs.JobValidationError(
            "build check does not support import_resource; use check_sampler or run the job"
        )
    exports = [step for step in loaded.steps if isinstance(step, jobs.Export)]
    temporary = Path(tempfile.mkdtemp(prefix=".cavi-build-", dir=root))
    job_dir = temporary / "job"
    try:
        job_dir.mkdir()
        payload = loaded.model_dump(by_alias=True)
        payload["directory"] = job_dir.relative_to(root).as_posix()
        payload["steps"] = [
            step for step in payload["steps"] if step.get("action") != "export"
        ]
        payload["_probe"] = True
        try:
            result = await operations.invoke_job(
                payload, timeout_seconds, workspace_path=str(root),
                progress_file=job_dir / "progress.jsonl",
            )
        except Exception as exc:
            event = _last_progress_error(job_dir / "progress.jsonl")
            if isinstance(exc, TimeoutError):
                raise JobTimeout(str(exc)) from exc
            if event is not None:
                raise jobs.JobValidationError(
                    f"build check failed at step {event.get('step')} "
                    f"({event.get('action')}): {event.get('error')}"
                ) from exc
            raise
        probe = result.get("probe")
        if isinstance(probe, dict):
            check_sampler(plan, probe)
            plan["sampler"] = probe
        plan["build_check"] = {
            "status": "ok",
            "steps": result.get("steps", []),
            "exports_skipped": len(exports),
            "warnings": [],
        }
        return plan
    finally:
        operations.remove_tree(temporary)


def check_build(name, workspace_path=None, timeout_seconds: float = 1800) -> dict:
    return asyncio.run(check_build_async(name, workspace_path, timeout_seconds))


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
        "app_name": "sampler",
        "operation": "build",
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
        "source": result.get("source"),
        "assets": result.get("assets", []),
        "steps": result.get("steps", []),
        "inputs": _inputs(root, job),
    }
    manifest["outputs"] = _files(root, job_dir, {"manifest.json", "failure.json", "progress.jsonl"})
    (job_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _failure(root: Path, job_dir: Path, path: Path, error: Exception,
             started: str, seconds: float) -> dict:
    payload = {
        "schema": 1,
        "app_name": "sampler",
        "operation": "build",
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
                             lock_timeout: float = 300, on_progress=None) -> dict:
    root = _root(workspace_path)
    path = _job_file(name, root)
    loaded = jobs.load_job(path)
    jobs.validate_paths(loaded, root)
    started = datetime.now(UTC).isoformat()
    clock = time.monotonic()
    async with WorkspaceLock(root, lock_timeout):
        app_available(wait_for_app)
        job_dir = _job_dir(root, loaded)
        job_dir.mkdir()
        payload = loaded.model_dump(by_alias=True)
        (job_dir / "job.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        try:
            result = await operations.invoke_job(
                payload, timeout_seconds, workspace_path=str(root),
                progress_file=job_dir / "progress.jsonl", on_progress=on_progress,
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


def run_job_file(name, workspace_path=None, timeout_seconds: float = 1800,
                 wait_for_app: float = 0, keep_on_failure: bool = False,
                 lock_timeout: float = 300, on_progress=None) -> dict:
    return asyncio.run(run_job_file_async(
        name, workspace_path, timeout_seconds, wait_for_app, keep_on_failure,
        lock_timeout, on_progress,
    ))
