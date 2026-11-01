"""Operations and batch jobs that run inside Sampler's embedded Python.

launch.py starts Sampler with a generated driver script that imports this module and
calls ``execute_in_sampler``, ``execute_job`` or ``preflight_job``. Sampler's interpreter
has no third-party packages and cannot see the host-side modules, so this module and
everything it imports stay standard library only and never import launch, runner, lock,
batch, job or anything under core except workspace. tests/test_import_closure.py
enforces both.
"""

import json
import math
import re
import shutil
import time
from pathlib import Path

from .. import receipts
from ..core.workspace import existing, input_file, new_output_directory, rel
from . import names as filenames
from . import targets
from .names import (
    BLEND_MODES,
    EXPORT_FORMATS,
    IMPORT_OPTIONS,
    MATERIAL_TYPES,
    PROJECT,
    RESOURCE,
    USAGE,
)

IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".exr", ".hdr", ".bmp", ".psd"}
RESOURCE_FILES = {".sbsar", ".sbs"}
USAGE_LABEL = re.compile(r"^[^@\x00-\x1f\x7f]{1,64}$")
_BATCH = {"active": False}
_CURRENT = {"path": None}
# The installed Sampler's version, supplied by the host with each request.
_APP = {"version": None}
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


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _missing_maps(files: list[Path], expected) -> list[str]:
    names = [_squash(item.stem) for item in files]
    return [name for name in expected if not any(_squash(name) in item for item in names)]


def _wait_files(directory: Path, timeout: float = 600, expected=(), poll: float = 0.2,
                grace: float = 3.0, settle: float = 1.0) -> list[Path]:
    """Wait for a finished export: every expected map present and no file still growing.

    Files count as finished once no size has changed for ``settle`` seconds, so a pause
    in Sampler's flush longer than one poll does not end the wait. Files that are settled
    but still lack an expected map fail after ``grace`` seconds instead of waiting out the
    full timeout, because the export controller has already returned.
    """
    deadline = time.monotonic() + timeout
    previous, changed_at, settled_since = None, time.monotonic(), None
    while True:
        files = sorted(
            item for item in directory.rglob("*")
            if item.is_file() and not item.is_symlink() and item.stat().st_size
        )
        sizes = {item: item.stat().st_size for item in files}
        now = time.monotonic()
        missing = _missing_maps(files, expected)
        if sizes != previous:
            changed_at, settled_since = now, None
        elif files and now - changed_at >= min(settle, timeout):
            if not missing:
                return files
            settled_since = settled_since or now
            if now - settled_since >= min(grace, timeout):
                break
        previous = sizes
        if now >= deadline:
            break
        time.sleep(poll)
    if not files:
        raise RuntimeError("Sampler exported no files")
    if missing:
        raise RuntimeError(
            "Sampler export is incomplete; missing maps: " + ", ".join(missing)
            + "; found: " + ", ".join(item.name for item in files[:16])
        )
    raise RuntimeError("Sampler export files were still changing when the wait ended")


def _display(root: Path, path) -> str | None:
    if not path:
        return None
    try:
        return rel(root, path)
    except (OSError, ValueError):
        return str(path)


def _name(value, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        return filenames.plain_name(value)
    except ValueError:
        raise ValueError(f"{label} must be a plain file name") from None


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
    path = input_file(root, name, PROJECT)
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
        raise RuntimeError(f"Sampler did not open {rel(root, path)}")
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


def _save_job_project(ssa, path: Path) -> None:
    before = path.stat().st_mtime_ns if path.is_file() else None
    ssa.save_project_as(str(path))
    if before is None:
        _wait_file(path)
    else:
        _wait_changed(path, before)


def _resource(kind: str, name: str, getter):
    found = [item for item in getter(name) if _resource_name(item) == name]
    if not found:
        available = sorted({_resource_name(item) for item in getter("")})[:64]
        raise ValueError(f"{kind} {name!r} not found; available: {', '.join(available)}")
    return found[0]


def _inspect_project(ssa, root, args) -> dict:
    path = _open(ssa, root, args.get("project"))
    assets = [_asset(item) for item in ssa.get_project_assets()]
    return {
        "project": rel(root, path) if path else None,
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
        "project": rel(root, path) if path else None,
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


_LAYER_ACTIONS = (
    "set_parameters", "apply_preset", "set_channels", "set_blend_parameters", "remove_layer",
)


class _Preflight:
    """Checks a batch job's steps against a source project without changing it."""

    def __init__(self, ssa, job: dict):
        self.ssa = ssa
        listed = [(_get(item, "name"), item) for item in ssa.get_project_assets()]
        self.assets = dict(listed)
        names = [name for name, _ in listed]
        self.ambiguous = {name for name in names if names.count(name) > 1}
        self.virtual_assets = set()
        self.virtual_layers = {name: set() for name in self.assets}
        self.unknown_layers = set()
        self.removed = {name: set() for name in self.assets}
        self.layers_of = {}
        self.problems, self.warnings = [], []
        self.declared, self.undeclared_import = set(), False
        for step in job["steps"]:
            if isinstance(step, dict) and step.get("action") == "import_resource":
                if step.get("resources"):
                    self.declared.update(step["resources"])
                else:
                    self.undeclared_import = True

    def asset_list(self) -> str:
        return ", ".join(sorted(self.assets)[:64])

    def check(self, index: int, step) -> None:
        if not isinstance(step, dict):
            self.problems.append(f"step {index}: must be an object")
            return
        action = step.get("action")
        prefix = f"step {index} ({action})"
        if action == "create_asset":
            self.create_asset(step, prefix)
            return
        if action == "import_resource":
            return
        if action in ("insert_filter", "insert_material"):
            self.resource_exists(step, action, prefix)
        if action == "export":
            self.export(step, prefix)
            return
        asset_name = step.get("asset")
        if not asset_name:
            self.problems.append(f"{prefix}: asset is required")
        elif asset_name in self.virtual_assets:
            self.step_on_created_asset(step, action, asset_name, prefix)
        elif asset_name not in self.assets:
            self.problems.append(
                f"{prefix}: asset {asset_name!r} not found; assets: {self.asset_list()}")
        elif asset_name in self.ambiguous:
            self.problems.append(
                f"{prefix}: asset {asset_name!r} is ambiguous; rename one of the duplicates")
        elif action in ("import_images", "import_textures"):
            self.unknown_layers.add(asset_name)
        elif action in ("insert_filter", "insert_material"):
            layer = step.get("filter") if action == "insert_filter" else step.get("material")
            self.virtual_layers.setdefault(asset_name, set()).add(layer)
        elif action in ("set_metadata", "activate_channels", "deactivate_channels"):
            return
        elif action in _LAYER_ACTIONS:
            self.layer_step(step, action, asset_name, prefix)
        else:
            self.problems.append(f"{prefix}: unknown action")

    def create_asset(self, step: dict, prefix: str) -> None:
        name = step.get("name")
        if name in self.assets or name in self.virtual_assets:
            self.problems.append(f"{prefix}: asset {name!r} already exists")
            return
        self.virtual_assets.add(name)
        self.virtual_layers.setdefault(name, set())
        self.unknown_layers.discard(name)
        self.removed.setdefault(name, set())

    def resource_exists(self, step: dict, action: str, prefix: str) -> None:
        name = step.get("filter") if action == "insert_filter" else step.get("material")
        if name in self.declared:
            return
        if self.undeclared_import:
            self.warnings.append(
                f"{prefix}: resource check skipped for {name!r}; "
                "imported resources are undeclared"
            )
            return
        getter = self.ssa.get_filters if action == "insert_filter" else self.ssa.get_materials
        kind = "filter" if action == "insert_filter" else "material"
        if not any(_resource_name(item) == name for item in getter(name)):
            self.problems.append(f"{prefix}: {kind} {name!r} not found")

    def export(self, step: dict, prefix: str) -> None:
        names = ([step.get("asset")] if step.get("asset") else []) + (step.get("assets") or [])
        for name in names:
            if name not in self.assets and name not in self.virtual_assets:
                self.problems.append(
                    f"{prefix}: asset {name!r} not found; assets: {self.asset_list()}")
        if step.get("all_assets") and not self.assets and not self.virtual_assets:
            self.problems.append(f"{prefix}: all_assets export has no assets")

    def step_on_created_asset(self, step, action, asset_name, prefix) -> None:
        self.warnings.append(
            f"{prefix}: asset {asset_name!r} is created earlier; structure not preflighted"
        )
        if action == "insert_filter":
            self.virtual_layers.setdefault(asset_name, set()).add(step.get("filter"))
        elif action == "insert_material":
            self.virtual_layers.setdefault(asset_name, set()).add(step.get("material"))
        elif action in ("import_images", "import_textures"):
            self.unknown_layers.add(asset_name)

    def layers(self, asset_name: str) -> list:
        # The source project is not changed during preflight, so list each asset once.
        if asset_name not in self.layers_of:
            self.layers_of[asset_name] = list(self.assets[asset_name].get_layers())
        return self.layers_of[asset_name]

    def layer_step(self, step: dict, action: str, asset_name: str, prefix: str) -> None:
        layer_name, position = step.get("layer"), step.get("position")
        layers = self.layers(asset_name)
        matches = [
            item for item in layers
            if _get(item, "name") == layer_name
            and (position is None or _get(item, "position") == position)
        ]
        if len(matches) > 1:
            self.problems.append(f"{prefix}: layer {layer_name!r} is ambiguous; provide position")
        elif matches:
            _LAYER_CHECKS[action](self, step, asset_name, matches[0], prefix)
        elif layer_name in self.removed.get(asset_name, set()):
            self.problems.append(f"{prefix}: layer {layer_name!r} was removed earlier")
        elif (layer_name in self.virtual_layers.get(asset_name, set())
              or asset_name in self.unknown_layers):
            self.warnings.append(
                f"{prefix}: layer {layer_name!r} is created earlier; structure not preflighted"
            )
        else:
            self.problems.append(
                f"{prefix}: layer {layer_name!r} not found; layers: "
                + ", ".join(f"{_get(item, 'name')}@{_get(item, 'position')}"
                            for item in layers[:64])
            )

    def collect(self, prefix: str, check) -> None:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 -- every problem is collected, not raised
            self.problems.append(f"{prefix}: {exc}")

    def set_parameters(self, step, asset_name, layer, prefix) -> None:
        for identifier, value in (step.get("parameters") or {}).items():
            self.collect(prefix, lambda i=identifier, v=value: _parameter_value(
                _parameter(layer, i), v))

    def apply_preset(self, step, asset_name, layer, prefix) -> None:
        presets = _get(layer, "presets", []) or []
        if step.get("preset") not in presets:
            self.problems.append(
                f"{prefix}: preset {step.get('preset')!r} not found; presets: "
                + ", ".join(map(str, presets[:64]))
            )

    def set_channels(self, step, asset_name, layer, prefix) -> None:
        asset = self.assets[asset_name]
        for name in (step.get("applies_to") or []) + (step.get("blend_applies_to") or []):
            self.collect(prefix, lambda n=name: _resolve_usage(asset, n))

    def set_blend_parameters(self, step, asset_name, layer, prefix) -> None:
        blend = list(_get(layer, "blend_parameters", []) or [])
        if not blend:
            self.problems.append(f"{prefix}: layer has no blend parameters")
            return
        for identifier, value in (step.get("parameters") or {}).items():
            self.collect(prefix, lambda i=identifier, v=value: _parameter_value(
                _parameter_in(blend, i), v))

    def remove_layer(self, step, asset_name, layer, prefix) -> None:
        self.removed.setdefault(asset_name, set()).add(step.get("layer"))
        self.virtual_layers.get(asset_name, set()).discard(step.get("layer"))


_LAYER_CHECKS = {
    "set_parameters": _Preflight.set_parameters,
    "apply_preset": _Preflight.apply_preset,
    "set_channels": _Preflight.set_channels,
    "set_blend_parameters": _Preflight.set_blend_parameters,
    "remove_layer": _Preflight.remove_layer,
}


def preflight_job(job: dict, workspace_path: str, project_path: str) -> dict:
    import substance_sampler as ssa

    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        raise TypeError("Job must be an object with steps")
    root = existing(workspace_path)
    project = input_file(root, project_path, PROJECT)
    if not ssa.open_project(str(project)):
        raise RuntimeError("Sampler could not open the preflight project")
    ssa.wait_for_computation()
    probe = _probe(ssa, root, {})
    check = _Preflight(ssa, job)
    for name in (job.get("project") or {}).get("assets") or []:
        if name not in check.assets:
            check.problems.append(
                f"asset {name!r} from project.assets is not in the source project"
            )
    for index, step in enumerate(job["steps"]):
        check.check(index, step)
    return {
        "project": rel(root, project),
        "probe": probe,
        "problems": check.problems,
        "warnings": check.warnings,
        "assets": sorted(check.assets),
    }


def _create_project(ssa, root, args) -> dict:
    name = _name(args.get("name"), "name")
    started = time.monotonic()
    with new_output_directory(root, args.get("directory")) as directory:
        project = directory / f"{name}.ssa"
        ssa.create_project(name, str(project))
        if not list(ssa.get_project_assets()):
            ssa.create_asset(name, ssa.MaterialModel.asm,
                             ssa.PredefinedPreset.base_material, select_asset=True)
        ssa.save_project_as(str(project))
        _wait_file(project)
        ssa.wait_for_computation()
        manifest_path = receipts.write_manifest(
            directory, "sampler", "create_project", root, outputs=[project],
            parameters={"name": name}, seconds=time.monotonic() - started,
            version=_APP["version"],
        )
    return {
        "project": rel(root, project),
        "directory": rel(root, directory),
        "name": name,
        "assets": [_asset(item) for item in ssa.get_project_assets()],
        "manifest": rel(root, manifest_path),
    }


def _save_as(ssa, root, args) -> dict:
    source = _open(ssa, root, args.get("project"))
    if source is None:
        raise ValueError("project is required for action=save_as")
    name = _name(args.get("name") or source.stem, "name")
    started = time.monotonic()
    with new_output_directory(root, args.get("directory")) as directory:
        target = directory / f"{name}.ssa"
        ssa.save_project_as(str(target))
        _wait_file(target)
        manifest_path = receipts.write_manifest(
            directory, "sampler", "save_as", root, inputs=[source], outputs=[target],
            parameters={"name": name}, seconds=time.monotonic() - started,
            version=_APP["version"],
        )
    return {
        "source": rel(root, source),
        "project": rel(root, target),
        "directory": rel(root, directory),
        "manifest": rel(root, manifest_path),
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
        "project": rel(root, path),
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
    return {"project": rel(root, path), "deleted": name, "saved": True}


def _import_images(ssa, root, args) -> dict:
    names = _list(args.get("images"), "images")
    paths = [input_file(root, name, IMAGES) for name in names]
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
        "project": rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "images": [rel(root, item) for item in paths],
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
        paths[usage] = input_file(root, name, IMAGES)
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
        "project": rel(root, project),
        "asset": _jsonable(_get(asset, "name")),
        "layer": _layer(layer),
        "images": {usage: rel(root, path) for usage, path in paths.items()},
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
    resource = _resource("Filter", name, ssa.get_filters)
    layer = asset.insert_filter(resource, position, insert_above=insert_above)
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": rel(root, project),
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
    resource = _resource("Material", name, ssa.get_materials)
    layer = asset.insert_material(
        resource, position, getattr(ssa.BlendMode, blend), insert_above=insert_above
    )
    ssa.wait_for_computation()
    _save(ssa)
    return {
        "project": rel(root, project),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
        "project": rel(root, path),
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
            input_file(root, project, PROJECT)
    path = input_file(root, args.get("path"), RESOURCE_FILES)
    source_manifest = args.get("source_manifest")
    expected = args.get("expected_sha256")
    if expected is not None:
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError("expected_sha256 must be 64 hexadecimal characters")
        expected = expected.lower()
    source, manifest_path = None, None
    if source_manifest is not None:
        manifest_path = input_file(root, source_manifest, {".json"})
        source = receipts.verify_file(receipts.load(manifest_path), root, path, "outputs")
    if expected is not None:
        actual = receipts.record(path, root)
        if actual["sha256"] != expected:
            raise ValueError("resource sha256 does not match expected_sha256")
        source = source or actual
    resources = ssa.import_resources(str(path))
    if source is not None and receipts.sha256_file(path) != source["sha256"]:
        # Sampler read the file after it was hashed; refuse rather than report it verified.
        raise ValueError(f"{rel(root, path)} changed while it was imported; nothing was saved")
    result = {
        "path": rel(root, path),
        "resources": [
            {"name": _resource_name(item), "label": _jsonable(_get(item, "label"))}
            for item in resources
        ],
    }
    if source_manifest is not None or expected is not None:
        result.update(verified=True, source=source)
        if manifest_path is not None:
            result["source_manifest"] = rel(root, manifest_path)
    return result


def _export(ssa, root, args) -> dict:
    started = time.monotonic()
    fmt = args.get("format", "sbsar")
    wait_seconds = args.get("wait_seconds", 600)
    if type(wait_seconds) not in (int, float) or not 1 <= wait_seconds <= 3600:
        raise ValueError("wait_seconds must be between 1 and 3600")
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"format must be one of: {', '.join(EXPORT_FORMATS)}")
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
        _resource("Export preset", preset_name, ssa.get_export_presets)
        if preset_name else None
    )
    directory_name = args.get("directory")
    if not isinstance(directory_name, str) or not directory_name:
        raise ValueError("directory is required for action=export")
    dry_run = bool(args.get("dry_run", True))
    plan = {
        "project": rel(root, project),
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
    with new_output_directory(root, directory_name) as destination:
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
        expected = ()
        if target_name is not None and channels is None and args.get("preset") is None:
            # Only the target's own preset promises its map set.
            expected = targets.target(target_name).maps
        files = _wait_files(destination, wait_seconds, expected)
        manifest_path = receipts.write_manifest(
            destination, "sampler", "export", root, inputs=[project], outputs=files,
            parameters=plan, seconds=time.monotonic() - started,
            version=_APP["version"],
        )
    return {
        **plan,
        "directory": rel(root, destination),
        "files": [rel(root, item) for item in files],
        "manifest": rel(root, manifest_path),
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
            directory = rel(root, asset_dir / kind)
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


def execute_job(job: dict, workspace_path: str, version: str | None = None) -> dict:
    import substance_sampler as ssa

    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        raise TypeError("Job must be an object with steps")
    _APP["version"] = version
    root = existing(workspace_path)
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
            source_path = input_file(root, source, PROJECT)
            copy_name = _name(
                project_spec.get("name") or source_path.stem, "project name"
            )
            project_path = job_dir / f"{copy_name}.ssa"
            shutil.copy2(source_path, project_path)
            if not ssa.open_project(str(project_path)):
                raise RuntimeError("Sampler could not open the copied project")
            ssa.wait_for_computation()
            source_rel = rel(root, source_path)
            unsaved = False
        else:
            project_name = _name(project_spec.get("name"), "project name")
            project_path = job_dir / f"{project_name}.ssa"
            # Sampler writes nothing until the project is saved.
            ssa.create_project(project_name, str(project_path))
            unsaved = True
        project = rel(root, project_path)
        _CURRENT["path"] = project_path.resolve()
        created = project_spec.get("asset")
        if created is not None:
            _create_asset(ssa, root, {
                "project": project, "name": created.get("name"),
                "model": created.get("model", "asm"), "preset": created.get("preset"),
            })
            unsaved = True
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
                if action == "export" and unsaved:
                    # Export receipts record the project, so it is saved as exported.
                    ssa.wait_for_computation()
                    _save_job_project(ssa, project_path)
                    unsaved = False
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
                unsaved = True
            results.append({
                "index": index, "action": action, "seconds": seconds,
                "result": _summary(result),
            })
        ssa.wait_for_computation()
        if unsaved:
            _save_job_project(ssa, project_path)
    finally:
        _BATCH["active"] = False
        _CURRENT["path"] = None
    reply = {
        "directory": rel(root, job_dir),
        "project": project,
        "source": source_rel,
        "assets": [_asset(item) for item in ssa.get_project_assets()],
        "steps": results,
    }
    if probe is not None:
        reply["probe"] = probe
    return reply



def execute_in_sampler(operation: str, arguments: dict, workspace_path: str,
                       version: str | None = None) -> dict:
    import substance_sampler as ssa

    if not isinstance(arguments, dict):
        raise TypeError("Parameters must be an object")
    _APP["version"] = version
    root = existing(workspace_path)
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
