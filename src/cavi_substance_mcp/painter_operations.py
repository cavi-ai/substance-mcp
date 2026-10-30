"""Trusted operations evaluated inside Painter's Python interpreter."""

import hashlib
import math
import os
import secrets
import shutil
import struct
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# Bump when Lease state or methods change.
LEASE_VERSION = 2
LEASE_ATTR = f"_cavi_mcp_lease_v{LEASE_VERSION}"
OLDER_LEASE_ATTRS = ("_cavi_mcp_lease",)
BREAK_IDLE_SECONDS = 300
READ_ONLY = frozenset({"status", "texture_sets", "layer_stack", "export_presets"})
NORMAL_MAP_FORMATS = ("OpenGL", "DirectX")
RLOCK_TYPE = type(threading.RLock())


class Lease:
    VERSION = LEASE_VERSION

    def __init__(self):
        self.lock = threading.RLock()
        self.token = None
        self.owner = None
        self.project = None
        self.expires = 0.0
        self.touched = 0.0

    def adopt(self, old):
        # State only; an older payload's methods are never called.
        lock = getattr(old, "lock", None)
        if isinstance(lock, RLOCK_TYPE):
            self.lock = lock
        with self.lock:
            token = getattr(old, "token", None)
            if isinstance(token, str):
                self.token = token
                self.owner = getattr(old, "owner", None)
                self.project = getattr(old, "project", None)
                self.expires = float(getattr(old, "expires", 0.0))
                self.touched = float(getattr(old, "touched", time.monotonic()))

    def clear(self):
        self.token, self.owner, self.project, self.expires = None, None, None, 0.0

    def active(self):
        return self.token is not None and time.monotonic() < self.expires

    def status(self, project):
        now = time.monotonic()
        return {
            "active": self.active(),
            "owner": self.owner,
            "project_id": project,
            "claimed_project_id": self.project,
            "expires_in": max(0.0, self.expires - now),
            "idle_seconds": max(0.0, now - self.touched) if self.token else 0.0,
        }

    def matches(self, token):
        return (isinstance(token, str) and self.token is not None
                and secrets.compare_digest(token, self.token))

    def require(self, token, project):
        if not self.matches(token):
            raise RuntimeError("A valid lease_id is required; inspect and claim the session")
        if time.monotonic() >= self.expires:
            raise RuntimeError("The edit lease expired; inspect and claim the session again")
        if project != self.project:
            raise RuntimeError("The project changed; release the lease and inspect before claiming")

    def control(self, params, project):
        with self.lock:
            project = project() if callable(project) else project
            action = params.get("action")
            if action == "inspect":
                return self.status(project)
            if action == "release":
                if not self.matches(params.get("lease_id")):
                    raise RuntimeError("A matching lease_id is required to release ownership")
                self.clear()
                return self.status(project)
            if action == "break":
                if params.get("expected_project") != project:
                    raise RuntimeError("The inspected project changed; inspect the session again")
                if not self.active():
                    raise RuntimeError("No active lease to break; claim the session instead")
                idle = time.monotonic() - self.touched
                if idle < BREAK_IDLE_SECONDS:
                    raise RuntimeError(
                        f"Lease of {self.owner} was used {idle:.0f} s ago; "
                        f"break requires {BREAK_IDLE_SECONDS} s idle"
                    )
                previous = self.owner
                self.clear()
                return {**self.status(project), "previous_owner": previous}
            if action not in {"claim", "renew"}:
                raise ValueError("Unknown session action")
            ttl = params.get("ttl_seconds", 600)
            if type(ttl) is not int or not 30 <= ttl <= 3600:
                raise ValueError("Lease duration must be an integer from 30 to 3600 seconds")
            if action == "renew":
                self.require(params.get("lease_id"), project)
            else:
                owner = params.get("owner")
                if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
                    raise ValueError("Owner must be a nonempty label of at most 128 characters")
                if params.get("expected_project") != project:
                    raise RuntimeError("The inspected project changed; inspect the session again")
                if self.active():
                    raise RuntimeError(f"Application is owned by {self.owner}; wait for release")
                self.token, self.owner, self.project = secrets.token_urlsafe(32), owner, project
            now = time.monotonic()
            self.expires, self.touched = now + ttl, now
            result = self.status(project)
            if action == "claim":
                result["lease_id"] = self.token
            return result

    @contextmanager
    def edit(self, token, identity):
        with self.lock:
            self.require(token, identity())
            self.touched = time.monotonic()
            try:
                yield
                # Owned saves and project transitions may change the project identity.
                self.project = identity()
            finally:
                self.touched = time.monotonic()


def shared_lease(module):
    registry = vars(module)
    with registry.setdefault("_cavi_mcp_lease_lock", threading.RLock()):
        current = registry.get(LEASE_ATTR)
        if getattr(current, "VERSION", 0) >= LEASE_VERSION:
            return current
        lease = Lease()
        for name in (LEASE_ATTR, *OLDER_LEASE_ATTRS):
            if registry.get(name) is not None:
                lease.adopt(registry[name])
                break
        # Older clients keep resolving their attribute to the single current lease.
        for name in (LEASE_ATTR, *OLDER_LEASE_ATTRS):
            registry[name] = lease
        return lease


def required(params, key, kind=str):
    if key not in params:
        raise ValueError(f"Missing required parameter: {key}")
    value = params[key]
    if kind is not None and not isinstance(value, kind):
        raise ValueError(f"Parameter {key} has the wrong type")
    return value


def unit_value(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Channel values must be finite numbers between 0 and 1")
    return float(value)


def export_destination(workspace: Path, name: str) -> Path:
    root = workspace.expanduser().resolve(strict=True)
    path = (root / name).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Output must remain inside the workspace")
    if path.exists():
        raise FileExistsError("Output directory already exists; choose a new directory")
    if not path.parent.is_dir():
        raise ValueError("Output parent directory must exist")
    return path


def input_file(workspace: Path, name: str, extensions: set[str]) -> Path:
    root = workspace.expanduser().resolve(strict=True)
    path = (root / name).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in extensions:
        raise ValueError("Input must be a supported file inside the workspace")
    return path


def color_texture(workspace, name):
    path = input_file(workspace, name, {".png"})
    with path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("PNG exceeds 16 MiB")
    if len(data) < 29 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[8:16] != b"\0\0\0\rIHDR":
        raise ValueError("Expected a PNG color texture")
    width, height = struct.unpack(">II", data[16:24])
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError("PNG dimensions must be 1 to 4096 pixels")
    return {"path": str(path), "width": width, "height": height,
            "sha256": hashlib.sha256(data).hexdigest()}, data


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
    if not path.exists():
        return []
    try:
        left = sorted((str(item) for item in path.rglob("*")), reverse=True)
    except OSError:
        left = []
    return [*left, str(path)]


def texture_resolution(value):
    if value is None:
        return None
    if type(value) is not int or value < 256 or value > 8192 or value & (value - 1):
        raise ValueError("default_texture_resolution must be a power of two from 256 to 8192")
    return value


def execute(operation: str, params: dict, workspace: str) -> dict:
    try:
        return {"ok": True, "result": dispatch(operation, params, workspace)}
    except Exception as exc:  # noqa: BLE001 -- envelope boundary
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def dispatch(operation: str, params: dict, workspace: str) -> dict:
    import substance_painter
    from substance_painter import project

    if not isinstance(params, dict):
        raise TypeError("Parameters must be an object")
    session = shared_lease(substance_painter)

    def identity():
        value = (os.getpid(), str(project.get_uuid()) if project.is_open() else None,
                 project.file_path() if project.is_open() else None)
        return hashlib.sha256(repr(value).encode()).hexdigest()

    if operation == "session_control":
        return session.control(params, identity)
    if operation in READ_ONLY or (
        operation == "export_textures" and params.get("dry_run", True) is True
    ):
        return _execute(operation, params, workspace)
    with session.edit(params.get("lease_id"), identity):
        return _execute(operation, params, workspace)


def _execute(operation: str, params: dict, workspace: str) -> dict:
    import substance_painter
    from substance_painter import export, project, textureset

    def root():
        return Path(workspace).resolve(strict=True)

    if operation == "status":
        opened = project.is_open()
        return {
            "connected": True,
            "api_version": str(substance_painter.__version__),
            "open": opened,
            "file": project.file_path() if opened else None,
            "needs_saving": project.needs_saving() if opened else False,
            "busy": project.is_busy(),
            "edition_state": bool(project.is_in_edition_state()) if opened else False,
            "workspace": str(Path(workspace)),
            "workspace_exists": Path(workspace).is_dir(),
        }
    if operation in {"open_project", "create_project"}:
        if project.is_open():
            raise RuntimeError("A project is already open; close it in Painter before replacing it")
        if operation == "open_project":
            path = input_file(root(), required(params, "path"), {".spp"})
            project.open(str(path))
            return {"requested": operation, "path": str(path), "loading_may_be_pending": True}
        path = input_file(root(), required(params, "path"), {".fbx", ".obj", ".gltf", ".glb"})
        normal = params.get("normal_map_format", "OpenGL")
        if normal not in NORMAL_MAP_FORMATS:
            raise ValueError("normal_map_format must be OpenGL or DirectX")
        resolution = texture_resolution(params.get("default_texture_resolution"))
        settings = project.Settings(
            normal_map_format=getattr(project.NormalMapFormat, normal),
            default_texture_resolution=resolution,
        )
        project.create(mesh_file_path=str(path), settings=settings)
        return {"requested": operation, "path": str(path), "loading_may_be_pending": True,
                "normal_map_format": normal, "default_texture_resolution": resolution}
    if operation == "close_project":
        if not project.is_open():
            return {"closed": True, "already_closed": True}
        if project.is_busy():
            raise RuntimeError("Painter must be idle before closing a project")
        if project.file_path() != required(params, "expected_file", (str, type(None))):
            raise RuntimeError("The open project changed; inspect status before closing it")
        discard = params.get("discard_unsaved", False)
        if type(discard) is not bool:
            raise ValueError("discard_unsaved must be a boolean")
        if project.needs_saving() and not discard:
            raise RuntimeError("Project has unsaved changes; save it or explicitly discard them")
        project.close()
        return {"closed": not project.is_open()}
    if not project.is_open() or project.is_busy():
        raise RuntimeError("Painter must have an open project and be idle")
    if operation in {"layer_stack", "create_fill_layer", "create_texture_fill"}:
        return _layers(operation, params, root)
    if operation == "texture_sets":
        sets = []
        for item in textureset.all_texture_sets():
            resolution = item.get_resolution()
            sets.append({
                "name": item.name(),
                "resolution": {"width": resolution.width, "height": resolution.height},
                "channels": {
                    channel.name: value.format().name for channel, value
                    in textureset.Stack.from_name(item.name()).all_channels().items()
                },
            })
        return {"texture_sets": sets}
    if operation == "export_presets":
        return {
            "presets": [
                {"name": item.name, "url": item.url}
                for item in export.list_predefined_export_presets()
            ]
        }
    if operation == "save":
        path = project.file_path()
        if not path:
            raise RuntimeError("Project was never saved; use save_copy or save it in Painter")
        if not Path(path).resolve().is_relative_to(root()):
            raise RuntimeError("Project file is outside the workspace; save it in Painter")
        project.save()
        return {"saved": True, "file": project.file_path(), "needs_saving": project.needs_saving()}
    if operation == "save_copy":
        folder = export_destination(root(), required(params, "directory"))
        folder.mkdir(mode=0o700)
        path = folder / "project.spp"
        project.save_as_copy(str(path), project.ProjectSaveMode.Full)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Painter did not produce a saved project copy")
        return {"path": str(path), "bytes": path.stat().st_size}
    if operation == "export_textures":
        folder = export_destination(root(), required(params, "directory"))
        presets = {item.url for item in export.list_predefined_export_presets()}
        if required(params, "preset") not in presets:
            raise ValueError("Choose a predefined preset URL returned by export_presets")
        config = {
            "exportShaderParams": False,
            "exportPath": str(folder),
            "defaultExportPreset": params["preset"],
            "exportList": [{"rootPath": item.name()} for item in textureset.all_texture_sets()],
            "exportParameters": [{"parameters": {"paddingAlgorithm": "infinite"}}],
        }
        planned = export.list_project_textures(config)
        files = [Path(path).resolve() for paths in planned.values() for path in paths]
        if not files or any(
            not path.is_relative_to(folder) or path == folder or path.exists() for path in files
        ):
            raise ValueError("Preset produced empty, existing, or out-of-directory output paths")
        if params.get("dry_run", True):
            return {"dry_run": True, "files": [str(path) for path in files]}
        folder.mkdir(mode=0o700)
        result = export.export_project_textures(config)
        actual = [Path(path).resolve() for paths in result.textures.values() for path in paths]
        if result.status != export.ExportStatus.Success:
            raise RuntimeError(
                f"Export status: {result.status.name}; {result.message}. Partial output may exist."
            )
        if not actual or any(
            not path.is_relative_to(folder) or not path.is_file() or path.stat().st_size == 0
            for path in actual
        ):
            raise RuntimeError(
                "Export output does not match the requested directory; inspect partial output"
            )
        return {
            "dry_run": False,
            "status": result.status.name,
            "files": [str(path) for path in actual],
            "omitted_files": sorted(str(path) for path in set(files) - set(actual)),
        }
    raise ValueError(f"Unknown operation: {operation}")


def _layers(operation, params, root):
    from substance_painter import colormanagement, layerstack, textureset

    stack = textureset.Stack.from_name(required(params, "texture_set"))
    nodes = layerstack.get_root_layer_nodes(stack)
    if operation == "layer_stack":
        from substance_painter import source

        layers = []
        for node in nodes:
            item = {"uid": node.uid(), "name": node.get_name(), "type": type(node).__name__}
            if isinstance(node, layerstack.FillLayerNode):
                item["source_mode"] = node.source_mode.name
                if node.source_mode == source.SourceMode.Split:
                    item["channels"] = {}
                    for label, channel in (("base_color", textureset.ChannelType.BaseColor),
                                           ("roughness", textureset.ChannelType.Roughness),
                                           ("metallic", textureset.ChannelType.Metallic)):
                        if channel not in node.active_channels:
                            continue
                        value = node.get_source(channel)
                        description = {"type": type(value).__name__}
                        if isinstance(value, source.SourceBitmap):
                            description.update(resource_url=value.resource_id.url(),
                                               color_space=str(value.get_color_space()))
                        elif isinstance(value, source.SourceUniformColor):
                            color = value.get_color()
                            description.update(value=list(color.value_raw),
                                               color_space=str(color.color_space))
                        item["channels"][label] = description
            layers.append(item)
        return {"layers": layers}
    name = required(params, "name")
    if not name.strip() or len(name) > 63:
        raise ValueError("Layer name must be nonempty and at most 63 characters")
    if any(n.get_name() == name for n in nodes):
        raise ValueError("A root layer with this name already exists")
    color = params.get("base_color", [1, 1, 1])
    if not isinstance(color, (list, tuple)) or len(color) != 3:
        raise ValueError("Base color must contain three values between 0 and 1")
    color = [unit_value(value) for value in color]
    roughness = unit_value(params.get("roughness", 0.5))
    metallic = unit_value(params.get("metallic", 0.0))
    channels = textureset.ChannelType
    active = {channels.BaseColor, channels.Roughness, channels.Metallic}
    if not active.issubset(stack.all_channels()):
        raise ValueError("Texture Set must have BaseColor, Roughness and Metallic channels")
    bitmap, imported, leftovers = None, None, []
    if operation == "create_texture_fill":
        from substance_painter import resource

        bitmap, payload = color_texture(root(), required(params, "base_color_texture"))
        folder = Path(tempfile.mkdtemp(prefix=".cavi-bitmap-", dir=root()))
        try:
            snapshot = folder / "color.png"
            snapshot.write_bytes(payload)
            imported = resource.import_project_resource(
                str(snapshot), resource.Usage.TEXTURE, name="cavi_color_" + uuid.uuid4().hex
            ).identifier()
        except Exception as exc:
            left = remove_tree(folder)
            note = f"; could not remove: {', '.join(left)}" if left else ""
            raise RuntimeError(f"Texture import failed ({type(exc).__name__}: {exc}){note}") from exc
        # Painter embeds the bitmap on import.
        leftovers = remove_tree(folder)
    node = None
    try:
        position = layerstack.InsertPosition.from_textureset_stack(stack)
        with layerstack.ScopedModification(f"Create fill layer {name}"):
            node = layerstack.insert_fill(position)
            node.set_name(name)
            node.active_channels = active
            for channel, value in [(channels.Roughness, roughness), (channels.Metallic, metallic)]:
                node.set_source(channel, colormanagement.Color(
                    value, value, value, colormanagement.GenericColorSpace.Raw))
            # Bind color last: Painter 12.2 otherwise exposes the most recent
            # scalar channel's data color spaces for the bitmap source.
            if imported:
                bound = node.set_source(channels.BaseColor, imported)
                bound.set_color_space(colormanagement.GenericColorSpace.sRGB)
            else:
                node.set_source(channels.BaseColor, colormanagement.Color(
                    *color, colormanagement.GenericColorSpace.sRGB))
    except Exception as exc:
        cleanup = "no layer created"
        if node is not None:
            try:
                with layerstack.ScopedModification("Remove failed fill"):
                    layerstack.delete_node(node)
                cleanup = "new layer removed"
            except Exception as cleanup_error:  # noqa: BLE001 -- preserve native partial-effect details
                cleanup = f"layer cleanup failed ({cleanup_error}); inspect the layer stack"
        if imported:
            left = remove_tree(Path(leftovers[-1])) if leftovers else []
            kept = [f"imported resource {imported.url()} (no Painter API removes it)", *left]
            raise RuntimeError(
                f"Texture fill failed ({type(exc).__name__}: {exc}); {cleanup}; "
                f"temporary files {'kept' if left else 'removed'}. "
                f"Could not remove: {', '.join(kept)}. Inspect before retrying."
            ) from exc
        if node is not None and cleanup.startswith("layer cleanup failed"):
            raise RuntimeError(f"Fill failed ({exc}); {cleanup}") from exc
        raise
    if bitmap:
        result = {"name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
                  "base_color_texture": bitmap, "resource_url": imported.url(),
                  "color_space": "sRGB", "roughness": roughness, "metallic": metallic}
        if leftovers:
            result["cleanup_failed"] = leftovers
        return result
    return {
        "name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
        "base_color": list(node.get_source(channels.BaseColor).get_color().value),
        "roughness": node.get_source(channels.Roughness).get_color().value[0],
        "metallic": node.get_source(channels.Metallic).get_color().value[0],
    }
