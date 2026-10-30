"""Sampler script jobs and operations evaluated inside Sampler."""

import asyncio
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path

from ..painter_operations import export_destination, input_file, remove_tree
from . import runner

PROJECT = {".ssa"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".exr", ".hdr", ".bmp", ".psd"}
USAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
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
        from cavi_substance_mcp.sampler.operations import execute_in_sampler

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


async def invoke(operation: str, arguments: dict, timeout_seconds: float) -> dict:
    root = workspace(create=True)
    if runner.running():
        raise RuntimeError(
            "Sampler is already running. Save and quit it; Sampler runs startup scripts only "
            "when no other instance is active."
        )
    job = Path(tempfile.mkdtemp(prefix="cavi-sampler-"))
    request, response, script = job / "request.json", job / "response.json", job / "run.py"
    try:
        request.write_text(
            json.dumps(
                {"operation": operation, "arguments": arguments, "workspace": str(root)},
                allow_nan=False,
            ),
            encoding="utf-8",
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
            for kind in (ValueError, FileExistsError, TypeError, RuntimeError):
                prefix = f"{kind.__name__}: "
                if detail.startswith(prefix):
                    raise kind(detail[len(prefix):])
            raise RuntimeError(detail)
        reply = payload.get("result")
        if not isinstance(reply, dict):
            raise TypeError("Sampler operation returned an unexpected response")
        reply["seconds"] = round(time.monotonic() - started, 3)
        return reply
    finally:
        remove_tree(job)


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


def _enum(value):
    if value is None:
        return None
    name = _get(value, "name")
    if isinstance(name, str):
        return name
    text = str(value)
    return text.rsplit(".", 1)[-1].rsplit(":", 1)[-1].strip("<> ")


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


def _layer(layer) -> dict:
    parameters = []
    for item in list(_get(layer, "parameters", []) or [])[:128]:
        parameters.append({
            "identifier": _jsonable(_get(item, "identifier")),
            "label": _jsonable(_get(item, "label")),
            "type": _enum(_get(item, "type")),
            "widget": _enum(_get(item, "widget_type")),
            "value": _jsonable(_get(item, "value")),
            "default": _jsonable(_get(item, "default_value")),
            "min": _jsonable(_get(item, "min_value")),
            "max": _jsonable(_get(item, "max_value")),
            "visible": bool(_get(item, "visible", False)),
        })
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
    }


def _open(ssa, root: Path, name: str | None) -> Path | None:
    if name is None:
        return None
    path = _input(root, name, PROJECT)
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
    if not ssa.save_project():
        raise RuntimeError("Sampler did not save the project")


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
    return result


def _create_project(ssa, root, args) -> dict:
    name = _name(args.get("name"), "name")
    directory = _new_directory(root, args.get("directory"))
    project = directory / f"{name}.ssa"
    try:
        ssa.create_project(name, str(project))
        ssa.wait_for_computation()
        if not project.is_file():
            produced = sorted(directory.glob("*.ssa"))
            if not produced:
                raise RuntimeError("Sampler created no project file")
            project = produced[0]
    except BaseException:
        remove_tree(directory)
        raise
    return {
        "project": _rel(root, project),
        "directory": _rel(root, directory),
        "name": name,
        "assets": [_asset(item) for item in ssa.get_project_assets()],
    }


def _save_as(ssa, root, args) -> dict:
    source = _open(ssa, root, args.get("project"))
    if source is None:
        raise ValueError("project is required for action=save_as")
    name = _name(args.get("name") or source.stem, "name")
    directory = _new_directory(root, args.get("directory"))
    target = directory / f"{name}.ssa"
    try:
        ssa.save_project_as(str(target))
        if not target.is_file():
            produced = sorted(directory.glob("*.ssa"))
            if not produced:
                raise RuntimeError("Sampler saved no project file")
            target = produced[0]
    except BaseException:
        remove_tree(directory)
        raise
    return {
        "source": _rel(root, source),
        "project": _rel(root, target),
        "directory": _rel(root, directory),
    }


def _create_asset(ssa, root, args) -> dict:
    name = _name(args.get("name"), "name")
    path = _open(ssa, root, args.get("project"))
    if path is None:
        raise ValueError("project is required for action=create")
    asset = ssa.create_asset(name, select_asset=True)
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


def _export(ssa, root, args) -> dict:
    fmt = args.get("format", "sbsar")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of: {', '.join(FORMATS)}")
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
        "material_type": material_type,
        "preset": preset_name,
        "channels": channels,
        "resolution": resolution,
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
        files = sorted(
            item for item in destination.rglob("*")
            if item.is_file() and not item.is_symlink() and item.stat().st_size
        )
        if not files:
            raise RuntimeError("Sampler exported no files")
    except BaseException:
        remove_tree(destination)
        raise
    return {
        **plan,
        "directory": _rel(root, destination),
        "files": [_rel(root, item) for item in files],
    }


def execute_in_sampler(operation: str, arguments: dict, workspace_path: str) -> dict:
    import substance_sampler as ssa

    if not isinstance(arguments, dict):
        raise TypeError("Parameters must be an object")
    root = _root(workspace_path)
    actions = {
        "inspect_project": _inspect_project,
        "inspect_asset": _inspect_asset,
        "catalog": _catalog,
        "create_project": _create_project,
        "save_as": _save_as,
        "create_asset": _create_asset,
        "delete_asset": _delete_asset,
        "import_images": _import_images,
        "import_textures": _import_textures,
        "insert_filter": _insert_filter,
        "insert_material": _insert_material,
        "export": _export,
    }
    try:
        action = actions[operation]
    except KeyError:
        raise ValueError(f"Unknown operation: {operation}") from None
    return action(ssa, root, arguments)
