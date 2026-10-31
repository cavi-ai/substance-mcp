"""Sampler script jobs and operations evaluated inside Sampler."""

import asyncio
import contextlib
import contextvars
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from .. import receipts
from ..painter_operations import export_destination, input_file, remove_tree
from . import runner, targets
from .errors import AppRunning
from .lock import WorkspaceLock

PROJECT = {".ssa"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".exr", ".hdr", ".bmp", ".psd"}
RESOURCE_FILES = {".sbsar", ".sbs"}
USAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
USAGE_LABEL = re.compile(r"^[^@\x00-\x1f\x7f]{1,64}$")
NAME = re.compile(r"^[^/\\<>:\"|?*\x00-\x1f\x7f]{1,96}$")
RESOURCE = re.compile(r"^[^@\x00-\x1f\x7f]{1,128}$")
FORMATS = ("sbsar", "sbs", "png", "jpg", "tga", "tif", "exr")
MATERIAL_TYPES = ("standard", "decal", "atlas")
IMPORT_OPTIONS = (
    "image_to_material_B2M",
    "image_to_material_AI_powered",
    "multiangle_to_material",
    "use_as_bitmap",
    "as_channels",
    "HDR_merge",
    "use_as_environment",
)
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
_BATCH = {"active": False}
_CURRENT = {"path": None}
BLEND_MODES = (
    "blend_ao",
    "blend_color_id",
    "blend_curvature",
    "blend_custom_mask",
    "blend_opacity",
    "blend_height",
    "blend_normal",
    "blend_per_channel",
)
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
            result = execute_job(data["job"], data["workspace"])
        else:
            result = execute_in_sampler(
                data["operation"], data.get("arguments") or {{}}, data["workspace"]
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


def workspace(create=False) -> Path:
    root = Path(
        os.environ.get("CAVI_SUBSTANCE_WORKSPACE", Path.home() / "Documents/CaviGameArt")
    ).expanduser()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def status() -> dict:
    found = runner.installation()
    root = workspace()
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
    job = Path(tempfile.mkdtemp(prefix="cavi-sampler-"))
    request, response, script = job / "request.json", job / "response.json", job / "run.py"
    stop = asyncio.Event()
    tailer = None
    if progress_file is not None and on_progress is not None:
        tailer = asyncio.create_task(_tail_progress(Path(progress_file), on_progress, stop))
    try:
        request.write_text(
            json.dumps(payload, allow_nan=False), encoding="utf-8"
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
        result = await runner.run_script(script, timeout_seconds, job)
        deadline = time.monotonic() + 2
        while not response.is_file() and result.returncode == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if not response.is_file():
            if result.returncode != 0:
                raise RuntimeError(runner.failure(result))
            raise RuntimeError(
                "Sampler exited without running the script. Quit any running Sampler window, "
                "then retry. " + runner.diagnostics(result)
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
        remove_tree(job)


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
    original = _input(root, arguments["project"], PROJECT)
    work = original.with_name(f"{WORK_PREFIX}{uuid.uuid4().hex[:12]}-{original.name}")
    aliases = {_rel(root, work): _rel(root, original)}
    try:
        await _copy_file(original, work)
        reply = await _run_payload(
            {"operation": operation, "workspace": str(root),
             "arguments": {**arguments, "project": _rel(root, work)}},
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
    if workspace_path is None:
        root = workspace(create=True)
    else:
        root = Path(workspace_path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
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
    root = Path(workspace_path).expanduser().resolve()
    return await _run_payload(
        {"preflight": job, "workspace": str(root), "project": project}, timeout_seconds,
        lock_timeout=lock_timeout,
    )


async def invoke_job(job: dict, timeout_seconds: float, workspace_path=None,
                     progress_file=None, on_progress=None,
                     lock_timeout: float = LOCK_WAIT) -> dict:
    if workspace_path is None:
        root = workspace(create=True)
    else:
        root = Path(workspace_path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    return await _run_payload(
        {"job": job, "workspace": str(root)}, timeout_seconds,
        progress_file=progress_file, on_progress=on_progress, lock_timeout=lock_timeout,
    )


def _wait_file(path: Path, timeout: float = 120) -> Path:
    deadline = time.monotonic() + timeout
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not path.is_file():
        raise RuntimeError(f"Sampler did not write {path.name}")
    return path


def _wait_changed(path: Path, before: int | None, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_mtime_ns != before:
            return
        time.sleep(0.05)
    raise RuntimeError("Sampler did not finish saving the project")


def _wait_files(directory: Path, timeout: float = 600) -> list[Path]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = sorted(
            item for item in directory.rglob("*")
            if item.is_file() and not item.is_symlink() and item.stat().st_size
        )
        if files:
            return files
        time.sleep(0.1)
    return []


def _root(value) -> Path:
    return Path(value).expanduser().resolve(strict=True)


def _rel(root: Path, path) -> str:
    return Path(path).resolve().relative_to(root).as_posix()


def _display(root: Path, path) -> str | None:
    if not path:
        return None
    try:
        return _rel(root, path)
    except (OSError, ValueError):
        return str(path)


def _input(root: Path, name: str, extensions: set[str]) -> Path:
    try:
        return input_file(root, name, extensions)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Input must be a supported file inside the workspace") from exc


def _new_directory(root: Path, name: str) -> Path:
    path = export_destination(root, name)
    path.mkdir()
    return path


def _name(value, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    value = value.strip()
    if not NAME.fullmatch(value) or value in (".", "..") or value.endswith("."):
        raise ValueError(f"{label} must be a plain file name")
    return value


def _resource_label(value, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    value = value.strip()
    if not RESOURCE.fullmatch(value) or value.startswith("-"):
        raise ValueError(f"{label} must be a printable resource name")
    return value


def _position(value) -> int:
    if type(value) is not int or not 0 <= value <= 100000:
        raise ValueError("position must be a non-negative integer")
    return value


def _list(value, label: str, limit=256) -> list:
    if not isinstance(value, list) or not value or len(value) > limit:
        raise ValueError(f"{label} must be a non-empty list of at most {limit} items")
    return value


def _jsonable(value, depth=0):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if depth > 4:
        return str(value)[:256]
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, depth + 1) for item in value[:256]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _jsonable(item, depth + 1)
            for key, item in list(value.items())[:256]
        }
    if hasattr(value, "id") and hasattr(value, "label"):
        return {"id": _get(value, "id"), "label": _get(value, "label")}
    return str(value)[:256]


def _get(value, name, default=None):
    try:
        return getattr(value, name)
    except Exception:  # noqa: BLE001 -- optional native properties vary by version
        return default


def _call(value, name):
    method = _get(value, name)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:  # noqa: BLE001 -- optional native methods
        return None


def _enum(value):
    if value is None:
        return None
    name = _get(value, "name")
    if isinstance(name, str):
        return name
    text = str(value)
    return text.rsplit(".", 1)[-1].rsplit(":", 1)[-1].strip("<> ")


def _members(enum) -> list[str]:
    members = _get(enum, "__members__")
    if isinstance(members, dict):
        return sorted(str(name) for name in members)
    found = []
    for name in dir(enum):
        if name.startswith("_"):
            continue
        value = _get(enum, name)
        if isinstance(value, enum) or (isinstance(value, str) and value == name):
            found.append(name)
    return sorted(set(found))


def _usage(value):
    if hasattr(value, "id"):
        return _get(value, "id")
    return _enum(value)


def _resource_name(value):
    name = _get(value, "name")
    return name if isinstance(name, str) else str(name)


def _metadata(asset) -> dict:
    metadata = _get(asset, "metadata")
    return {
        "name": _jsonable(_get(metadata, "name")),
        "author": _jsonable(_get(metadata, "author")),
        "category": _jsonable(_get(metadata, "category")),
        "description": _jsonable(_get(metadata, "description")),
        "tags": _jsonable(_get(metadata, "tags")),
        "physical_size": _jsonable(_get(metadata, "physical_size")),
        "custom_metadata": _jsonable(_get(metadata, "custom_metadata")),
    }


def _asset(asset) -> dict:
    return {
        "name": _jsonable(_get(asset, "name")),
        "type": _enum(_get(asset, "type")),
        "material_model": _enum(_get(asset, "material_model")),
        "resolution": _jsonable(_get(asset, "document_resolution")),
        "metadata": _metadata(asset),
        "layer_count": len(_get(asset, "get_layers", list)() or []),
    }


def _parameter_info(item) -> dict:
    return {
        "identifier": _jsonable(_get(item, "identifier")),
        "label": _jsonable(_get(item, "label")),
        "type": _enum(_get(item, "type")),
        "widget": _enum(_get(item, "widget_type")),
        "value": _jsonable(_get(item, "value")),
        "default": _jsonable(_get(item, "default_value")),
        "min": _jsonable(_get(item, "min_value")),
        "max": _jsonable(_get(item, "max_value")),
        "enum_values": _jsonable(_call(item, "enum_values")),
        "value_labels": _jsonable(_call(item, "value_labels")),
        "visible": bool(_get(item, "visible", False)),
    }


def _usage_id(value):
    if isinstance(value, str):
        return value
    return _get(value, "id")


def _usage_label(value):
    if isinstance(value, str):
        return value
    return _get(value, "label")


def _usage_map(asset):
    found = {}
    for layer in asset.get_layers():
        for attribute in ("output_usages", "applied_channels", "blend_applied_channels"):
            for usage in _get(layer, attribute, []) or []:
                uid, label = _usage_id(usage), _usage_label(usage)
                for key in {uid, label}:
                    if not key:
                        continue
                    found.setdefault(key, {})
                    if uid:
                        found[key][uid] = usage
    return found


def _resolve_usage(asset, name: str):
    mapping = _usage_map(asset)
    matches = mapping.get(name, {})
    if not matches:
        known = ", ".join(sorted(mapping)[:64]) or "none"
        raise ValueError(f"Usage {name!r} not found; known: {known}")
    if len(matches) > 1:
        raise ValueError(
            f"Usage {name!r} is ambiguous; use one of: " + ", ".join(sorted(matches))
        )
    return next(iter(matches.values()))


def _usage_labels(layer):
    labels = {}
    for attribute in ("output_usages", "applied_channels", "blend_applied_channels"):
        for usage in _get(layer, attribute, []) or []:
            uid, label = _usage_id(usage), _usage_label(usage)
            if uid and label:
                labels[str(uid)] = label
    return labels


def _layer(layer) -> dict:
    parameters = [
        _parameter_info(item) for item in list(_get(layer, "parameters", []) or [])[:128]
    ]
    blend_parameters = [
        _parameter_info(item) for item in list(_get(layer, "blend_parameters", []) or [])[:128]
    ]
    return {
        "name": _jsonable(_get(layer, "name")),
        "type": _enum(_get(layer, "type")),
        "position": _jsonable(_get(layer, "position")),
        "output_usages": [_usage(item) for item in _get(layer, "output_usages", []) or []],
        "applied_channels": [_usage(item) for item in _get(layer, "applied_channels", []) or []],
        "blend_applied_channels": [
            _usage(item) for item in _get(layer, "blend_applied_channels", []) or []
        ],
        "blend_mode": _enum(_get(layer, "blend_mode")),
        "image_names": _jsonable(_get(layer, "image_names")),
        "presets": _jsonable(_get(layer, "presets")),
        "parameters": parameters,
        "blend_parameters": blend_parameters,
        "usage_labels": _usage_labels(layer),
    }


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return text or "asset"


def _find_layer(asset, name: str, position: int | None = None):
    layers = list(asset.get_layers())
    matches = [item for item in layers if _get(item, "name") == name]
    if position is not None:
        matches = [item for item in matches if _get(item, "position") == position]
    if not matches:
        available = ", ".join(
            f"{_get(item, 'name')}@{_get(item, 'position')}" for item in layers[:64]
        ) or "none"
        raise ValueError(f"Layer {name!r} not found; layers: {available}")
    if len(matches) > 1:
        raise ValueError(f"Layer {name!r} is ambiguous; provide position")
    return matches[0]


def _parameter_in(parameters, identifier: str):
    matches = [item for item in parameters if _get(item, "identifier") == identifier]
    if not matches:
        matches = [item for item in parameters if _get(item, "label") == identifier]
    if not matches:
        available = ", ".join(
            str(_get(item, "identifier")) for item in parameters[:64]
        ) or "none"
        raise ValueError(f"Parameter {identifier!r} not found; parameters: {available}")
    if len(matches) > 1:
        raise ValueError(f"Parameter {identifier!r} is ambiguous")
    return matches[0]


def _parameter(layer, identifier: str):
    return _parameter_in(list(_get(layer, "parameters", []) or []), identifier)


def _bounds(parameter, value) -> None:
    low, high = _get(parameter, "min_value"), _get(parameter, "max_value")
    values = value if isinstance(value, list) else [value]
    for index, item in enumerate(values):
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            continue
        floor = low[index] if isinstance(low, (list, tuple)) and index < len(low) else low
        ceiling = high[index] if isinstance(high, (list, tuple)) and index < len(high) else high
        if isinstance(floor, (int, float)) and item < floor:
            raise ValueError(f"{_get(parameter, 'identifier')} is below {floor}")
        if isinstance(ceiling, (int, float)) and item > ceiling:
            raise ValueError(f"{_get(parameter, 'identifier')} is above {ceiling}")


def _parameter_value(parameter, value):
    kind = _enum(_get(parameter, "type"))
    identifier = _get(parameter, "identifier")
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"{identifier} expects a string")
        return value
    if kind == "image":
        raise ValueError(f"{identifier} is an image parameter and is not supported")
    match = re.fullmatch(r"(float|integer)([1-4])?", kind or "")
    if not match:
        raise ValueError(f"{identifier} has unsupported type {kind}")
    size = int(match.group(2) or 1)
    if size == 1 and match.group(1) == "integer" and isinstance(value, str):
        labels = _call(parameter, "enum_values") or []
        found = None
        for entry in labels:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2 and str(entry[0]) == value:
                found = entry[1]
                break
        if found is None:
            names = ", ".join(str(entry[0]) for entry in labels[:64]
                              if isinstance(entry, (list, tuple)) and entry) or "none"
            raise ValueError(f"{identifier} has no label {value!r}; labels: {names}")
        value = found
    items = value if isinstance(value, list) else [value]
    if len(items) != size:
        raise ValueError(f"{identifier} expects {size} value(s)")
    converted = []
    for item in items:
        if match.group(1) == "integer":
            if isinstance(item, bool):
                item = int(item)
            if type(item) is not int:
                raise ValueError(f"{identifier} expects integers")
        else:
            if isinstance(item, bool) or type(item) not in (int, float) or not math.isfinite(item):
                raise ValueError(f"{identifier} expects finite numbers")
            item = float(item)
        converted.append(item)
    _bounds(parameter, converted)
    return converted[0] if size == 1 else converted


def _open(ssa, root: Path, name: str | None) -> Path | None:
    if name is None:
        return None
    candidate = (root / name).resolve()
    if _CURRENT.get("path") == candidate:
        return candidate
    path = _input(root, name, PROJECT)
    try:
        current = ssa.get_project_path()
    except Exception:  # noqa: BLE001 -- optional native call
        current = ""
    if current:
        try:
            if Path(current).resolve() == path:
                return path
        except OSError:
            pass
    opened = ssa.open_project(str(path))
    if not opened:
        raise RuntimeError(f"Sampler did not open {_rel(root, path)}")
    ssa.wait_for_computation()
    return path


def _find_asset(ssa, name: str | None):
    assets = list(ssa.get_project_assets())
    if name is None:
        selected = ssa.get_selected_asset()
        if selected is not None:
            return selected
        if assets:
            return assets[0]
        raise ValueError("project has no assets")
    matches = [item for item in assets if _get(item, "name") == name]
    if not matches:
        raise ValueError(
            f"Asset {name!r} not found; assets: "
            + ", ".join(sorted({_resource_name(item) for item in assets})[:64])
        )
    if len(matches) > 1:
        raise ValueError(f"Asset {name!r} is ambiguous; rename one of the duplicates")
    return matches[0]


def _save(ssa) -> None:
    if _BATCH["active"]:
        return
    try:
        target = Path(ssa.get_project_path())
    except Exception:  # noqa: BLE001 -- optional native call
        target = None
    before = target.stat().st_mtime_ns if target is not None and target.is_file() else None
    if not ssa.save_project():
        raise RuntimeError("Sampler did not save the project")
    if target is not None:
        if before is None:
            _wait_file(target)
        else:
            _wait_changed(target, before)


def _resource(ssa, kind: str, name: str, getter):
    found = [item for item in getter(name) if _resource_name(item) == name]
    if not found:
        available = sorted({_resource_name(item) for item in getter("")})[:64]
        raise ValueError(f"{kind} {name!r} not found; available: {', '.join(available)}")
    return found[0]


def _inspect_project(ssa, root, args) -> dict:
    path = _open(ssa, root, args.get("project"))
    assets = [_asset(item) for item in ssa.get_project_assets()]
    return {
        "project": _rel(root, path) if path else None,
        "project_path": _display(root, _get(ssa, "get_project_path", lambda: "")()),
        "assets": assets,
    }


def _inspect_asset(ssa, root, args) -> dict:
    name = args.get("asset")
    if name is not None:
        name = _name(name, "asset")
    path = _open(ssa, root, args.get("project"))
    asset = _find_asset(ssa, name)
    return {
        "project": _rel(root, path) if path else None,
        "asset": _asset(asset),
        "layers": [_layer(item) for item in asset.get_layers()],
    }


def _catalog(ssa, root, args) -> dict:
    query = args.get("query", "")
    if not isinstance(query, str) or len(query) > 128:
        raise ValueError("query must be a string of at most 128 characters")
    result = {}
    for key, getter in (
        ("filters", ssa.get_filters),
        ("materials", ssa.get_materials),
        ("texture_generators", ssa.get_texture_generators),
        ("export_presets", ssa.get_export_presets),
    ):
        names = {_resource_name(item) for item in getter(query)}
        result[key] = sorted(names)[:256]
    presets = sorted({_resource_name(item) for item in ssa.get_export_presets("")})
    result["material_models"] = _members(ssa.MaterialModel)
    result["predefined_presets"] = _members(ssa.PredefinedPreset)
    result["import_options"] = _members(ssa.ImageImportOption)
    result["blend_modes"] = _members(ssa.BlendMode)
    result["material_types"] = _members(ssa.MaterialType)
    result["texture_formats"] = _members(ssa.MaterialExportFormat)
    result["b2m_available"] = "image_to_material_B2M" in result["import_options"]
    catalog = targets.catalog()
    for item in catalog.values():
        item["available"] = item["preset"] is None or item["preset"] in presets
    result["export_targets"] = catalog
    return result


def _probe(ssa, root, args) -> dict:
    presets = sorted({_resource_name(item) for item in ssa.get_export_presets("")})
    options = _members(ssa.ImageImportOption)
    catalog = targets.catalog()
    for item in catalog.values():
        item["available"] = item["preset"] is None or item["preset"] in presets
    return {
        "app_version": _jsonable(_get(ssa, "__version__")),
        "export_presets": presets,
        "filters": sorted({_resource_name(item) for item in ssa.get_filters("")})[:2000],
        "materials": sorted({_resource_name(item) for item in ssa.get_materials("")})[:2000],
        "texture_generators": sorted({
            _resource_name(item) for item in ssa.get_texture_generators("")
        })[:2000],
        "import_options": options,
        "material_models": _members(ssa.MaterialModel),
        "predefined_presets": _members(ssa.PredefinedPreset),
        "blend_modes": _members(ssa.BlendMode),
        "material_types": _members(ssa.MaterialType),
        "texture_formats": _members(ssa.MaterialExportFormat),
        "b2m_available": "image_to_material_B2M" in options,
        "targets": catalog,
    }


def preflight_job(job: dict, workspace_path: str, project_path: str) -> dict:
    import substance_sampler as ssa

    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        raise TypeError("Job must be an object with steps")
    root = _root(workspace_path)
    project = _input(root, project_path, PROJECT)
    if not ssa.open_project(str(project)):
        raise RuntimeError("Sampler could not open the preflight project")
    ssa.wait_for_computation()
    probe = _probe(ssa, root, {})
    assets = {_get(item, "name"): item for item in ssa.get_project_assets()}
    virtual_assets = set()
    virtual_layers = {name: set() for name in assets}
    unknown_layers = set()
    removed = {name: set() for name in assets}
    problems, warnings = [], []
    declared, undeclared_import = set(), False
    for step in job["steps"]:
        if isinstance(step, dict) and step.get("action") == "import_resource":
            if step.get("resources"):
                declared.update(step["resources"])
            else:
                undeclared_import = True
    project_spec = job.get("project") or {}
    for name in project_spec.get("assets") or []:
        if name not in assets:
            problems.append(
                f"asset {name!r} from project.assets is not in the source project"
            )
    for index, step in enumerate(job["steps"]):
        if not isinstance(step, dict):
            problems.append(f"step {index}: must be an object")
            continue
        action = step.get("action")
        asset_name = step.get("asset")
        prefix = f"step {index} ({action})"
        if action == "create_asset":
            name = step.get("name")
            if name in assets or name in virtual_assets:
                problems.append(f"{prefix}: asset {name!r} already exists")
            else:
                virtual_assets.add(name)
                virtual_layers.setdefault(name, set())
                unknown_layers.discard(name)
                removed.setdefault(name, set())
            continue
        if action == "import_resource":
            continue
        if action in ("insert_filter", "insert_material"):
            name = step.get("filter") if action == "insert_filter" else step.get("material")
            if name not in declared:
                if undeclared_import:
                    warnings.append(
                        f"{prefix}: resource check skipped for {name!r}; "
                        "imported resources are undeclared"
                    )
                else:
                    getter = ssa.get_filters if action == "insert_filter" else ssa.get_materials
                    kind = "filter" if action == "insert_filter" else "material"
                    if not any(_resource_name(item) == name for item in getter(name)):
                        problems.append(f"{prefix}: {kind} {name!r} not found")
        if action == "export":
            names = ([step.get("asset")] if step.get("asset") else []) + (
                step.get("assets") or []
            )
            for name in names:
                if name not in assets and name not in virtual_assets:
                    problems.append(
                        f"{prefix}: asset {name!r} not found; assets: "
                        + ", ".join(sorted(assets)[:64])
                    )
            if step.get("all_assets") and not assets and not virtual_assets:
                problems.append(f"{prefix}: all_assets export has no assets")
            continue
        if not asset_name:
            problems.append(f"{prefix}: asset is required")
            continue
        if asset_name in virtual_assets:
            warnings.append(
                f"{prefix}: asset {asset_name!r} is created earlier; structure not preflighted"
            )
            if action == "insert_filter":
                virtual_layers.setdefault(asset_name, set()).add(step.get("filter"))
            elif action == "insert_material":
                virtual_layers.setdefault(asset_name, set()).add(step.get("material"))
            elif action in ("import_images", "import_textures"):
                unknown_layers.add(asset_name)
            continue
        asset = assets.get(asset_name)
        if asset is None:
            problems.append(
                f"{prefix}: asset {asset_name!r} not found; assets: "
                + ", ".join(sorted(assets)[:64])
            )
            continue
        if action in ("import_images", "import_textures"):
            unknown_layers.add(asset_name)
            continue
        if action == "insert_filter":
            virtual_layers.setdefault(asset_name, set()).add(step.get("filter"))
            continue
        if action == "insert_material":
            virtual_layers.setdefault(asset_name, set()).add(step.get("material"))
            continue
        if action in ("set_metadata", "activate_channels", "deactivate_channels"):
            continue
        if action in (
            "set_parameters", "apply_preset", "set_channels", "set_blend_parameters",
            "remove_layer",
        ):
            layer_name = step.get("layer")
            position = step.get("position")
            layers = list(asset.get_layers())
            matches = [
                item for item in layers
                if _get(item, "name") == layer_name
                and (position is None or _get(item, "position") == position)
            ]
            if len(matches) > 1:
                problems.append(
                    f"{prefix}: layer {layer_name!r} is ambiguous; provide position"
                )
                continue
            if not matches:
                if layer_name in removed.get(asset_name, set()):
                    problems.append(f"{prefix}: layer {layer_name!r} was removed earlier")
                elif (
                    layer_name in virtual_layers.get(asset_name, set())
                    or asset_name in unknown_layers
                ):
                    warnings.append(
                        f"{prefix}: layer {layer_name!r} is created earlier; "
                        "structure not preflighted"
                    )
                else:
                    problems.append(
                        f"{prefix}: layer {layer_name!r} not found; layers: "
                        + ", ".join(
                            f"{_get(item, 'name')}@{_get(item, 'position')}"
                            for item in layers[:64]
                        )
                    )
                continue
            layer = matches[0]
            if action == "set_parameters":
                for identifier, value in (step.get("parameters") or {}).items():
                    try:
                        _parameter_value(_parameter(layer, identifier), value)
                    except Exception as exc:  # noqa: BLE001 -- collected
                        problems.append(f"{prefix}: {exc}")
            elif action == "apply_preset":
                presets = _get(layer, "presets", []) or []
                if step.get("preset") not in presets:
                    problems.append(
                        f"{prefix}: preset {step.get('preset')!r} not found; presets: "
                        + ", ".join(map(str, presets[:64]))
                    )
            elif action == "set_channels":
                for name in (step.get("applies_to") or []) + (
                    step.get("blend_applies_to") or []
                ):
                    try:
                        _resolve_usage(asset, name)
                    except Exception as exc:  # noqa: BLE001 -- collected
                        problems.append(f"{prefix}: {exc}")
            elif action == "set_blend_parameters":
                blend = list(_get(layer, "blend_parameters", []) or [])
                if not blend:
                    problems.append(f"{prefix}: layer has no blend parameters")
                else:
                    for identifier, value in (step.get("parameters") or {}).items():
                        try:
                            _parameter_value(_parameter_in(blend, identifier), value)
                        except Exception as exc:  # noqa: BLE001 -- collected
                            problems.append(f"{prefix}: {exc}")
            elif action == "remove_layer":
                removed.setdefault(asset_name, set()).add(layer_name)
                virtual_layers.get(asset_name, set()).discard(layer_name)
            continue
        problems.append(f"{prefix}: unknown action")
    return {
        "project": _rel(root, project),
        "probe": probe,
        "problems": problems,
        "warnings": warnings,
        "assets": sorted(assets),
    }


def _create_project(ssa, root, args) -> dict:
    name = _name(args.get("name"), "name")
    directory = _new_directory(root, args.get("directory"))
    project = directory / f"{name}.ssa"
    started = time.monotonic()
    try:
        ssa.create_project(name, str(project))
        if not list(ssa.get_project_assets()):
            ssa.create_asset(name, ssa.MaterialModel.asm,
                             ssa.PredefinedPreset.base_material, select_asset=True)
        ssa.save_project_as(str(project))
        _wait_file(project)
        ssa.wait_for_computation()
        manifest = receipts.build(
            "sampler", "create_project", root, outputs=[project],
            parameters={"name": name}, seconds=time.monotonic() - started,
            version=runner.installation().version,
        )
        manifest_path = receipts.write(directory, manifest)
    except BaseException:
        remove_tree(directory)
        raise
    return {
        "project": _rel(root, project),
        "directory": _rel(root, directory),
        "name": name,
        "assets": [_asset(item) for item in ssa.get_project_assets()],
        "manifest": _rel(root, manifest_path),
    }


def _save_as(ssa, root, args) -> dict:
    source = _open(ssa, root, args.get("project"))
    if source is None:
        raise ValueError("project is required for action=save_as")
    name = _name(args.get("name") or source.stem, "name")
    directory = _new_directory(root, args.get("directory"))
    target = directory / f"{name}.ssa"
    started = time.monotonic()
    try:
        ssa.save_project_as(str(target))
        _wait_file(target)
        manifest = receipts.build(
            "sampler", "save_as", root, inputs=[source], outputs=[target],
            parameters={"name": name}, seconds=time.monotonic() - started,
            version=runner.installation().version,
        )
        manifest_path = receipts.write(directory, manifest)
    except BaseException:
        remove_tree(directory)
        raise
    return {
        "source": _rel(root, source),
        "project": _rel(root, target),
        "directory": _rel(root, directory),
        "manifest": _rel(root, manifest_path),
    }


def _create_asset(ssa, root, args) -> dict:
    name = _name(args.get("name"), "name")
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=create")
    if any(_get(item, "name") == name for item in ssa.get_project_assets()):
        raise ValueError(f"Asset {name!r} already exists in the project")
    model_name = args.get("model") or "asm"
    if model_name not in ("asm", "openpbr"):
        raise ValueError("model must be asm or openpbr")
    model = getattr(ssa.MaterialModel, model_name)
    preset_name = args.get("preset")
    if preset_name is not None:
        preset_name = _resource_label(preset_name, "preset")
    if bool(args.get("empty", False)):
        if model_name != "asm" or preset_name is not None:
            raise ValueError("empty assets do not take model or preset")
        asset = ssa.create_asset(name, select_asset=True)
    elif preset_name is None:
        asset = ssa.create_asset(name, model, ssa.PredefinedPreset.base_material,
                                 select_asset=True)
    else:
        predefined = getattr(
            ssa.PredefinedPreset,
            preset_name.strip().lower().replace(" ", "_").replace("-", "_"),
            None,
        )
        if predefined is not None:
            asset = ssa.create_asset(name, model, predefined, select_asset=True)
        else:
            custom = ssa.get_preset(model, preset_name)
            if custom is None:
                raise ValueError(f"Preset {preset_name!r} not found")
            asset = ssa.create_asset(name, custom, select_asset=True)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _asset(asset),
        "saved": True,
    }


def _delete_asset(ssa, root, args) -> dict:
    name = _name(args.get("asset"), "asset")
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=delete")
    asset = _find_asset(ssa, name)
    removed = bool(ssa.delete_asset(asset))
    if not removed:
        raise RuntimeError(f"Sampler did not delete asset {name!r}")
    ssa.wait_for_computation()
    _save(ssa)
    return {"project": _rel(root, path), "deleted": name, "saved": True}


def _import_images(ssa, root, args) -> dict:
    names = _list(args.get("images"), "images")
    paths = [_input(root, name, IMAGES) for name in names]
    option = args.get("import_option", "use_as_bitmap")
    if option not in IMPORT_OPTIONS:
        raise ValueError(f"import_option must be one of: {', '.join(IMPORT_OPTIONS)}")
    insert_above = bool(args.get("insert_above", False))
    position = _position(args.get("position", 0))
    asset_name = args.get("asset")
    asset_name = _name(asset_name, "asset") if asset_name else None
    project = _open(ssa, root, args.get("project"))
    if project is None:
        raise ValueError("project is required for action=import_images")
    asset = _find_asset(ssa, asset_name)
    layer = asset.import_images(
        [str(item) for item in paths], position, getattr(ssa.ImageImportOption, option),
        insert_above=insert_above,
    )
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "images": [_rel(root, item) for item in paths],
        "saved": True,
    }


def _import_textures(ssa, root, args) -> dict:
    images = args.get("images")
    if not isinstance(images, dict) or not images or len(images) > 64:
        raise ValueError("images must be a non-empty object with at most 64 usages")
    paths = {}
    for usage, name in images.items():
        if not isinstance(usage, str) or not USAGE.fullmatch(usage):
            raise ValueError(f"Invalid usage name: {usage}")
        paths[usage] = _input(root, name, IMAGES)
    insert_above = bool(args.get("insert_above", True))
    position = _position(args.get("position", 0))
    asset_name = args.get("asset")
    asset_name = _name(asset_name, "asset") if asset_name else None
    project = _open(ssa, root, args.get("project"))
    if project is None:
        raise ValueError("project is required for action=import_textures")
    asset = _find_asset(ssa, asset_name)
    layer = asset.import_textures(
        {usage: str(path) for usage, path in paths.items()}, position,
        insert_above=insert_above,
    )
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "images": {usage: _rel(root, path) for usage, path in paths.items()},
        "saved": True,
    }


def _insert_filter(ssa, root, args) -> dict:
    name = _resource_label(args.get("filter"), "filter")
    insert_above = bool(args.get("insert_above", True))
    position = _position(args.get("position", 0))
    asset_name = args.get("asset")
    asset_name = _name(asset_name, "asset") if asset_name else None
    project = _open(ssa, root, args.get("project"))
    if project is None:
        raise ValueError("project is required for action=insert_filter")
    asset = _find_asset(ssa, asset_name)
    resource = _resource(ssa, "Filter", name, ssa.get_filters)
    layer = asset.insert_filter(resource, position, insert_above=insert_above)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "saved": True,
    }


def _insert_material(ssa, root, args) -> dict:
    name = _resource_label(args.get("material"), "material")
    blend = args.get("blend_mode", "blend_normal")
    if blend not in BLEND_MODES:
        raise ValueError(f"blend_mode must be one of: {', '.join(BLEND_MODES)}")
    insert_above = bool(args.get("insert_above", True))
    position = _position(args.get("position", 0))
    asset_name = args.get("asset")
    asset_name = _name(asset_name, "asset") if asset_name else None
    project = _open(ssa, root, args.get("project"))
    if project is None:
        raise ValueError("project is required for action=insert_material")
    asset = _find_asset(ssa, asset_name)
    resource = _resource(ssa, "Material", name, ssa.get_materials)
    layer = asset.insert_material(
        resource, position, getattr(ssa.BlendMode, blend), insert_above=insert_above
    )
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "saved": True,
    }


def _set_parameters(ssa, root, args) -> dict:
    values = args.get("parameters")
    if not isinstance(values, dict) or not values or len(values) > 64:
        raise ValueError("parameters must be a non-empty object with at most 64 entries")
    asset_name = _name(args.get("asset"), "asset")
    layer_name = _resource_label(args.get("layer"), "layer")
    position = args.get("position")
    if position is not None:
        position = _position(position)
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=set_parameters")
    asset = _find_asset(ssa, asset_name)
    layer = _find_layer(asset, layer_name, position)
    applied, changed = {}, False
    for identifier, value in values.items():
        parameter = _parameter(layer, identifier)
        converted = _parameter_value(parameter, value)
        if _jsonable(_get(parameter, "value")) != _jsonable(converted):
            parameter.value = converted
            changed = True
        applied[_get(parameter, "identifier")] = _jsonable(converted)
    ssa.wait_for_computation()
    if changed:
        _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _jsonable(_get(layer, "name")),
        "parameters": applied,
        "changed": changed,
        "saved": changed,
    }


def _apply_preset(ssa, root, args) -> dict:
    asset_name = _name(args.get("asset"), "asset")
    layer_name = _resource_label(args.get("layer"), "layer")
    preset = _resource_label(args.get("preset"), "preset")
    position = args.get("position")
    if position is not None:
        position = _position(position)
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=apply_preset")
    asset = _find_asset(ssa, asset_name)
    layer = _find_layer(asset, layer_name, position)
    presets = _get(layer, "presets", []) or []
    if preset not in presets:
        raise ValueError(
            f"Preset {preset!r} not found on layer; presets: {', '.join(map(str, presets[:64]))}"
        )
    changed = bool(layer.apply_preset(preset))
    ssa.wait_for_computation()
    if changed:
        _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "preset": preset,
        "changed": changed,
        "saved": changed,
    }


def _set_metadata(ssa, root, args) -> dict:
    asset_name = _name(args.get("asset"), "asset")
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=set_metadata")
    asset = _find_asset(ssa, asset_name)
    metadata = _get(asset, "metadata")
    if metadata is None:
        raise RuntimeError("Sampler asset has no metadata object")
    author = args.get("author")
    category = args.get("category")
    description = args.get("description")
    tags = args.get("tags")
    physical = args.get("physical_size")
    custom = args.get("custom_metadata")
    if all(value is None for value in (author, category, description, tags, physical, custom)):
        raise ValueError("set_metadata needs at least one field")
    if author is not None:
        metadata.author = str(author)
    if category is not None:
        metadata.category = str(category)
    if description is not None:
        metadata.description = str(description)
    if tags is not None:
        if not isinstance(tags, list) or len(tags) > 64 or any(
            not isinstance(item, str) or len(item) > 64 for item in tags
        ):
            raise ValueError("tags must be a list of up to 64 short strings")
        metadata.tags = list(tags)
    if physical is not None:
        if (
            not isinstance(physical, (list, tuple))
            or len(physical) != 3
            or any(type(item) not in (int, float) or not math.isfinite(item)
                   for item in physical)
        ):
            raise ValueError("physical_size must be three finite numbers")
        metadata.physical_size = tuple(float(item) for item in physical)
    if custom is not None:
        if not isinstance(custom, dict) or len(custom) > 64:
            raise ValueError("custom_metadata must be an object with at most 64 entries")
        for key, value in custom.items():
            simple = (bool, int, float, str, type(None))
            if not isinstance(key, str) or not isinstance(value, simple):
                raise TypeError("custom_metadata keys and values must be simple JSON values")
        metadata.custom_metadata = dict(custom)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "metadata": _metadata(asset),
        "saved": True,
    }


def _channels(ssa, root, args, activate: bool) -> dict:
    asset_name = _name(args.get("asset"), "asset")
    channels = _list(args.get("channels"), "channels", 64)
    for item in channels:
        if not isinstance(item, str) or not USAGE.fullmatch(item):
            raise ValueError(f"Invalid channel name: {item}")
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for channel changes")
    asset = _find_asset(ssa, asset_name)
    method = _get(asset, "activate_channels" if activate else "deactivate_channels")
    if not callable(method):
        raise TypeError("Sampler asset does not support channel changes")
    method(channels)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "channels": channels,
        "activated": activate,
        "saved": True,
    }


def _set_channels(ssa, root, args) -> dict:
    asset_name = _name(args.get("asset"), "asset")
    layer_name = _resource_label(args.get("layer"), "layer")
    applies = args.get("applies_to")
    blend = args.get("blend_applies_to")
    if applies is None and blend is None:
        raise ValueError("set_channels needs applies_to or blend_applies_to")
    for label, values in (("applies_to", applies), ("blend_applies_to", blend)):
        if values is not None:
            values = _list(values, label, 64)
            for item in values:
                if not isinstance(item, str) or not USAGE_LABEL.fullmatch(item):
                    raise ValueError(f"Invalid usage label: {item}")
            if label == "applies_to":
                applies = values
            else:
                blend = values
    position = args.get("position")
    if position is not None:
        position = _position(position)
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=set_channels")
    asset = _find_asset(ssa, asset_name)
    layer = _find_layer(asset, layer_name, position)
    applies_objects = (
        [_resolve_usage(asset, name) for name in applies] if applies is not None else None
    )
    blend_objects = (
        [_resolve_usage(asset, name) for name in blend] if blend is not None else None
    )
    if applies_objects is not None:
        layer.applies_to(applies_objects)
    if blend_objects is not None:
        layer.blend_applies_to(blend_objects)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "applies_to": [_usage_id(item) for item in applies_objects] if applies_objects else None,
        "blend_applies_to": (
            [_usage_id(item) for item in blend_objects] if blend_objects else None
        ),
        "saved": True,
    }


def _set_blend_parameters(ssa, root, args) -> dict:
    values = args.get("parameters")
    if not isinstance(values, dict) or not values or len(values) > 64:
        raise ValueError("parameters must be a non-empty object with at most 64 entries")
    asset_name = _name(args.get("asset"), "asset")
    layer_name = _resource_label(args.get("layer"), "layer")
    position = args.get("position")
    if position is not None:
        position = _position(position)
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=set_blend_parameters")
    asset = _find_asset(ssa, asset_name)
    layer = _find_layer(asset, layer_name, position)
    blend_parameters = list(_get(layer, "blend_parameters", []) or [])
    if not blend_parameters:
        raise ValueError("layer has no blend parameters")
    applied, changed = {}, False
    for identifier, value in values.items():
        parameter = _parameter_in(blend_parameters, identifier)
        converted = _parameter_value(parameter, value)
        if _jsonable(_get(parameter, "value")) != _jsonable(converted):
            parameter.value = converted
            changed = True
        applied[_get(parameter, "identifier")] = _jsonable(converted)
    ssa.wait_for_computation()
    if changed:
        _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "parameters": applied,
        "changed": changed,
        "saved": changed,
    }


def _remove_layer(ssa, root, args) -> dict:
    asset_name = _name(args.get("asset"), "asset")
    layer_name = _resource_label(args.get("layer"), "layer")
    position = args.get("position")
    if position is not None:
        position = _position(position)
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=remove_layer")
    asset = _find_asset(ssa, asset_name)
    layer = _find_layer(asset, layer_name, position)
    index = _get(layer, "position")
    if type(index) is not int:
        layers = list(asset.get_layers())
        index = next(
            (number for number, item in enumerate(layers)
             if _get(item, "name") == layer_name
             and (position is None or _get(item, "position") == position)),
            None,
        )
    if index is None:
        raise RuntimeError(f"Sampler did not locate layer {layer_name!r}")
    asset.remove_layer(index)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": _rel(root, path),
        "asset": _jsonable(_get(asset, "name")),
        "removed": layer_name,
        "position": index,
        "saved": True,
    }


def _import_resource(ssa, root, args) -> dict:
    project = args.get("project")
    if project is not None:
        candidate = (root / project).resolve()
        if _CURRENT.get("path") != candidate:
            _input(root, project, PROJECT)
    path = _input(root, args.get("path"), RESOURCE_FILES)
    source_manifest = args.get("source_manifest")
    expected = args.get("expected_sha256")
    if expected is not None:
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError("expected_sha256 must be 64 hexadecimal characters")
        expected = expected.lower()
    source, manifest_path = None, None
    if source_manifest is not None:
        manifest_path = _input(root, source_manifest, {".json"})
        source = receipts.verify_file(receipts.load(manifest_path), root, path, "outputs")
    if expected is not None:
        actual = receipts.record(path, root)
        if actual["sha256"] != expected:
            raise ValueError("resource sha256 does not match expected_sha256")
        source = source or actual
    resources = ssa.import_resources(str(path))
    result = {
        "path": _rel(root, path),
        "resources": [
            {"name": _resource_name(item), "label": _jsonable(_get(item, "label"))}
            for item in resources
        ],
    }
    if source_manifest is not None or expected is not None:
        result.update(verified=True, source=source)
        if manifest_path is not None:
            result["source_manifest"] = _rel(root, manifest_path)
    return result


def _export(ssa, root, args) -> dict:
    started = time.monotonic()
    fmt = args.get("format", "sbsar")
    wait_seconds = args.get("wait_seconds", 600)
    if type(wait_seconds) not in (int, float) or not 1 <= wait_seconds <= 3600:
        raise ValueError("wait_seconds must be between 1 and 3600")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of: {', '.join(FORMATS)}")
    target_name = args.get("target")
    if target_name is not None:
        targets.target(target_name)
        if fmt in ("sbsar", "sbs"):
            raise ValueError("target exports textures; use png, tga, tif or exr")
    material_type = args.get("material_type", "standard")
    if material_type not in MATERIAL_TYPES:
        raise ValueError(f"material_type must be one of: {', '.join(MATERIAL_TYPES)}")
    resolution = args.get("resolution")
    if resolution is not None and (type(resolution) is not int or not 64 <= resolution <= 8192):
        raise ValueError("resolution must be an integer from 64 to 8192")
    channels = args.get("channels")
    if channels is not None:
        if not isinstance(channels, list) or not channels or len(channels) > 64:
            raise ValueError("channels must be a non-empty list of at most 64 names")
        for item in channels:
            if not isinstance(item, str) or not USAGE.fullmatch(item):
                raise ValueError(f"Invalid channel name: {item}")
    preset_name = args.get("preset")
    if preset_name is not None:
        preset_name = _resource_label(preset_name, "preset")
    elif target_name is not None:
        preset_name = targets.preset_for(target_name)
    asset_name = args.get("asset")
    asset_name = _name(asset_name, "asset") if asset_name else None
    project = _open(ssa, root, args.get("project"))
    if project is None:
        raise ValueError("project is required for action=export")
    asset = _find_asset(ssa, asset_name)
    preset = (
        _resource(ssa, "Export preset", preset_name, ssa.get_export_presets)
        if preset_name else None
    )
    directory_name = args.get("directory")
    if not isinstance(directory_name, str) or not directory_name:
        raise ValueError("directory is required for action=export")
    dry_run = bool(args.get("dry_run", True))
    plan = {
        "project": _rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "directory": directory_name,
        "format": fmt,
        "target": target_name,
        "material_type": material_type,
        "preset": preset_name,
        "channels": channels,
        "resolution": resolution,
        "wait_seconds": wait_seconds,
        "dry_run": dry_run,
    }
    if dry_run:
        return {**plan, "files": []}
    destination = _new_directory(root, directory_name)
    try:
        ssa.wait_for_computation()
        controller = ssa.ExportController()
        options = {
            "path": str(destination),
            "format": getattr(ssa.MaterialExportFormat, fmt),
            "material_type": getattr(ssa.MaterialType, material_type),
            "channels": channels or [],
            "subfolder": bool(args.get("subfolder", False)),
            "controller": controller,
        }
        if preset is not None:
            options["preset"] = preset
        if resolution:
            options["w_resolution"] = resolution
            options["h_resolution"] = resolution
        asset.export_material(**options)
        controller.wait()
        files = _wait_files(destination, wait_seconds)
        if not files:
            raise RuntimeError("Sampler exported no files")
        manifest = receipts.build(
            "sampler", "export", root, inputs=[project], outputs=files,
            parameters=plan, seconds=time.monotonic() - started,
            version=runner.installation().version,
        )
        manifest_path = receipts.write(destination, manifest)
    except BaseException:
        remove_tree(destination)
        raise
    return {
        **plan,
        "directory": _rel(root, destination),
        "files": [_rel(root, item) for item in files],
        "manifest": _rel(root, manifest_path),
    }


def _job_export(ssa, root: Path, job_dir: Path, project: str, step: dict) -> dict:
    base = (job_dir / step.get("directory", "exports")).resolve()
    if base != job_dir and not base.is_relative_to(job_dir):
        raise ValueError("export directory must stay inside the job directory")
    base.mkdir(parents=True, exist_ok=True)
    if step.get("asset"):
        names = [step["asset"]]
    elif step.get("assets"):
        names = list(dict.fromkeys(step["assets"]))
    elif step.get("all_assets"):
        names = list(dict.fromkeys(
            _get(item, "name") for item in ssa.get_project_assets() if item.get_layers()
        ))
    else:
        raise ValueError("export needs asset, assets or all_assets")
    if not names:
        raise ValueError("export selected no assets")
    target_name = step.get("target", "generic")
    targets.target(target_name)
    preset = step.get("preset")
    if preset is None:
        preset = targets.preset_for(target_name)
    directories, owners = {}, {}
    for name in names:
        slug = _slug(name)
        # Compare case-insensitively: macOS and Windows volumes would merge the folders.
        owner = owners.setdefault(slug.casefold(), name)
        if owner != name:
            raise ValueError(
                f"Assets {owner!r} and {name!r} share export directory {slug!r}"
            )
        directories[name] = slug
    rows = []
    for name in names:
        asset_dir = base / directories[name]
        asset_dir.mkdir(parents=True, exist_ok=True)
        for kind in step.get("outputs") or ["sbsar", "textures"]:
            directory = _rel(root, asset_dir / kind)
            if kind == "sbsar":
                result = _export(ssa, root, {
                    "project": project, "asset": name, "directory": directory,
                    "format": "sbsar", "wait_seconds": step.get("wait_seconds", 600),
                    "dry_run": False,
                })
            else:
                values = {
                    "project": project, "asset": name, "directory": directory,
                    "format": step.get("texture_format", "png"), "target": target_name,
                    "wait_seconds": step.get("wait_seconds", 600), "dry_run": False,
                }
                if preset is not None:
                    values["preset"] = preset
                if step.get("channels"):
                    values["channels"] = step["channels"]
                if step.get("resolution"):
                    values["resolution"] = step["resolution"]
                result = _export(ssa, root, values)
            rows.append({"kind": kind, **result})
    return {
        "target": target_name, "preset": preset, "assets": names, "outputs": rows,
    }


def _summary(result):
    if not isinstance(result, dict):
        return result
    compact = {key: value for key, value in result.items() if key not in ("assets", "layers")}
    layer = result.get("layer")
    if isinstance(layer, dict):
        compact["layer"] = {
            key: layer.get(key) for key in
            ("name", "type", "position", "output_usages", "parameters")
        }
    asset = result.get("asset")
    if isinstance(asset, dict):
        compact["asset"] = {
            key: asset.get(key) for key in
            ("name", "type", "material_model", "resolution", "layer_count")
        }
    outputs = result.get("outputs")
    if isinstance(outputs, list):
        compact["outputs"] = [
            {
                "kind": item.get("kind"),
                "asset": item.get("asset"),
                "directory": item.get("directory"),
                "files": item.get("files", []),
            }
            for item in outputs if isinstance(item, dict)
        ]
    return compact


def _dispatch_step(ssa, root, job_dir, project, step):
    action = step.get("action")
    arguments = {key: value for key, value in step.items() if key != "action"}
    arguments["project"] = project
    if action == "import_images":
        return action, _import_images(ssa, root, arguments)
    if action == "import_textures":
        return action, _import_textures(ssa, root, arguments)
    if action == "create_asset":
        return action, _create_asset(ssa, root, arguments)
    if action == "insert_filter":
        return action, _insert_filter(ssa, root, arguments)
    if action == "insert_material":
        return action, _insert_material(ssa, root, arguments)
    if action == "set_parameters":
        return action, _set_parameters(ssa, root, arguments)
    if action == "apply_preset":
        return action, _apply_preset(ssa, root, arguments)
    if action == "set_metadata":
        return action, _set_metadata(ssa, root, arguments)
    if action == "activate_channels":
        return action, _channels(ssa, root, arguments, True)
    if action == "deactivate_channels":
        return action, _channels(ssa, root, arguments, False)
    if action == "set_channels":
        return action, _set_channels(ssa, root, arguments)
    if action == "set_blend_parameters":
        return action, _set_blend_parameters(ssa, root, arguments)
    if action == "remove_layer":
        return action, _remove_layer(ssa, root, arguments)
    if action == "import_resource":
        return action, _import_resource(ssa, root, arguments)
    if action == "export":
        return action, _job_export(ssa, root, job_dir, project, step)
    raise ValueError(f"Unknown step action: {action}")


def execute_job(job: dict, workspace_path: str) -> dict:
    import substance_sampler as ssa

    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        raise TypeError("Job must be an object with steps")
    root = _root(workspace_path)
    job_dir = (root / str(job.get("directory", ""))).resolve()
    if job_dir == root or not job_dir.is_relative_to(root) or not job_dir.is_dir():
        raise ValueError("job directory must be an existing directory inside the workspace")
    project_spec = job.get("project") or {}
    source = project_spec.get("source")
    source_rel = None
    mutating = {
        "create_asset", "import_images", "import_textures", "insert_filter",
        "insert_material", "set_parameters", "apply_preset", "set_metadata",
        "activate_channels", "deactivate_channels", "set_channels",
        "set_blend_parameters", "remove_layer",
    }
    probe = None
    _BATCH["active"] = True
    try:
        if source is not None:
            source_path = _input(root, source, PROJECT)
            copy_name = _name(
                project_spec.get("name") or source_path.stem, "project name"
            )
            project_path = job_dir / f"{copy_name}.ssa"
            shutil.copy2(source_path, project_path)
            if not ssa.open_project(str(project_path)):
                raise RuntimeError("Sampler could not open the copied project")
            ssa.wait_for_computation()
            source_rel = _rel(root, source_path)
            mutated = False
        else:
            project_name = _name(project_spec.get("name"), "project name")
            project_path = job_dir / f"{project_name}.ssa"
            ssa.create_project(project_name, str(project_path))
            mutated = project_spec.get("asset") is not None
        project = _rel(root, project_path)
        _CURRENT["path"] = project_path.resolve()
        created = project_spec.get("asset")
        if created is not None:
            _create_asset(ssa, root, {
                "project": project, "name": created.get("name"),
                "model": created.get("model", "asm"), "preset": created.get("preset"),
            })
        ssa.wait_for_computation()
        if job.get("_probe"):
            probe = _probe(ssa, root, {})
        results = []
        total = len(job["steps"])
        progress = job_dir / "progress.jsonl"

        def report(event):
            with progress.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, default=str) + "\n")

        for index, step in enumerate(job["steps"]):
            if not isinstance(step, dict):
                raise TypeError(f"Step {index} must be an object")
            action = step.get("action")
            started = time.monotonic()
            report({"step": index, "total": total, "action": action,
                    "status": "start"})
            try:
                action, result = _dispatch_step(
                    ssa, root, job_dir, project, step
                )
            except BaseException as exc:
                report({
                    "step": index, "total": total, "action": action, "status": "error",
                    "seconds": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                raise
            seconds = round(time.monotonic() - started, 3)
            report({"step": index, "total": total, "action": action, "status": "ok",
                    "seconds": seconds})
            if action in mutating:
                mutated = True
            results.append({
                "index": index, "action": action, "seconds": seconds,
                "result": _summary(result),
            })
        ssa.wait_for_computation()
        if source is not None:
            if mutated:
                before = project_path.stat().st_mtime_ns
                ssa.save_project_as(str(project_path))
                _wait_changed(project_path, before)
        else:
            ssa.save_project_as(str(project_path))
            _wait_file(project_path)
    finally:
        _BATCH["active"] = False
        _CURRENT["path"] = None
    reply = {
        "directory": _rel(root, job_dir),
        "project": project,
        "source": source_rel,
        "assets": [_asset(item) for item in ssa.get_project_assets()],
        "steps": results,
    }
    if probe is not None:
        reply["probe"] = probe
    return reply



def execute_in_sampler(operation: str, arguments: dict, workspace_path: str) -> dict:
    import substance_sampler as ssa

    if not isinstance(arguments, dict):
        raise TypeError("Parameters must be an object")
    root = _root(workspace_path)
    actions = {
        "inspect_project": _inspect_project,
        "inspect_asset": _inspect_asset,
        "catalog": _catalog,
        "probe": _probe,
        "create_project": _create_project,
        "save_as": _save_as,
        "create_asset": _create_asset,
        "delete_asset": _delete_asset,
        "import_images": _import_images,
        "import_textures": _import_textures,
        "insert_filter": _insert_filter,
        "insert_material": _insert_material,
        "set_parameters": _set_parameters,
        "apply_preset": _apply_preset,
        "set_metadata": _set_metadata,
        "activate_channels": lambda ssa, root, args: _channels(ssa, root, args, True),
        "deactivate_channels": lambda ssa, root, args: _channels(ssa, root, args, False),
        "set_channels": _set_channels,
        "set_blend_parameters": _set_blend_parameters,
        "remove_layer": _remove_layer,
        "import_resource": _import_resource,
        "export": _export,
    }
    try:
        action = actions[operation]
    except KeyError:
        raise ValueError(f"Unknown operation: {operation}") from None
    return action(ssa, root, arguments)
