"""Trusted operations evaluated inside Painter's Python interpreter."""

import hashlib
import math
import os
import re
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
READ_ONLY = frozenset({"status", "texture_sets", "layer_stack", "export_presets", "shelf",
                       "bake_settings", "bake_status"})
NORMAL_MAP_FORMATS = ("OpenGL", "DirectX")
RLOCK_TYPE = type(threading.RLock())
BAKE_ATTR = "_cavi_mcp_bake_v1"
RELOAD_ATTR = "_cavi_mcp_reload_v1"
CREATED_ATTR = "_cavi_mcp_created_v1"
MESH_EXTENSIONS = {".fbx", ".obj", ".gltf", ".glb"}
HIGH_POLY_EXTENSIONS = {".fbx", ".obj", ".dae", ".ply", ".usd", ".usda", ".usdc", ".usdz",
                        ".gltf", ".glb"}
CHANNELS = {"base_color": "BaseColor", "roughness": "Roughness", "metallic": "Metallic",
            "normal": "Normal", "height": "Height", "emissive": "Emissive", "opacity": "Opacity",
            "ao": "AO", **{f"user{i}": f"User{i}" for i in range(8)}}
BAKE_MAPS = {"normal": "Normal", "world_space_normal": "WorldSpaceNormal", "id": "ID",
             "ao": "AO", "curvature": "Curvature", "position": "Position",
             "thickness": "Thickness", "height": "Height", "opacity": "Opacity",
             "bent_normals": "BentNormals"}
SHELF_USAGES = {"smart_material": "SMART_MATERIAL", "smart_mask": "SMART_MASK",
                "material": "BASE_MATERIAL", "generator": "GENERATOR", "texture": "TEXTURE",
                "environment": "ENVIRONMENT"}
ANTIALIASING = ("none", "2x2", "4x4", "8x8")
MAX_DEPTH = 8
MAX_NODES = 512
# Painter export JSON: srcMapType documentMap/meshMap/virtualMap names; True = color source.
DOCUMENT_MAPS = {
    "basecolor": ("baseColor", True), "height": ("height", False),
    "specular": ("specular", True), "opacity": ("opacity", False),
    "emissive": ("emissive", True), "displacement": ("displacement", False),
    "glossiness": ("glossiness", False), "roughness": ("roughness", False),
    "anisotropylevel": ("anisotropylevel", False), "anisotropyangle": ("anisotropyangle", False),
    "transmissive": ("transmissive", False), "scattering": ("scattering", False),
    "reflection": ("reflection", False), "ior": ("ior", False), "metallic": ("metallic", False),
    "normal": ("normal", True), "ambientocclusion": ("ambientOcclusion", False),
    "diffuse": ("diffuse", True), "specularlevel": ("specularlevel", False),
    "blendingmask": ("blendingmask", False),
    **{f"user{i}": (f"user{i}", False) for i in range(8)},
}
MESH_MAPS = {"ambient_occlusion": False, "curvature": False, "thickness": False, "id": True,
             "position": True, "world_space_normals": True, "normal_base": True,
             "bent_normals": True}
VIRTUAL_MAPS = {"normal_opengl": ("Normal_OpenGL", True), "normal_directx": ("Normal_DirectX", True),
                "ao_mixed": ("AO_Mixed", False)}
EXPORT_FORMATS = {"png": ("8", "16"), "tga": ("8",), "tiff": ("8", "16", "32f"),
                  "exr": ("16f", "32f"), "jpeg": ("8",)}
PADDING = ("infinite", "passthrough", "transparent", "color", "diffusion")
DILATED_PADDING = ("transparent", "color", "diffusion")
NAME_TOKENS = {"$textureSet", "$mesh", "$project", "$udim", "$sceneMaterial", "$colorSpace",
               "$uvTileName"}
CUSTOM_PRESET = "cavi_maps"


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


def power_of_two(value, name, low=256, high=8192):
    if type(value) is not int or not low <= value <= high or value & (value - 1):
        raise ValueError(f"{name} must be a power of two from {low} to {high}")
    return value.bit_length() - 1


def flag(params, key, default):
    value = params.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def names_list(params, key, limit=64):
    value = params.get(key)
    if value is None:
        return None
    if (not isinstance(value, list) or not 1 <= len(value) <= limit
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{key} must be a list of 1 to {limit} unique nonempty strings")
    return value


def plain(value):
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return str(value)


def shared(module, name):
    registry = vars(module)
    with registry.setdefault("_cavi_mcp_lease_lock", threading.RLock()):
        return registry.setdefault(name, {})


def file_url(path):
    try:
        from PySide6.QtCore import QUrl
    except ImportError:
        return path.as_uri()
    return QUrl.fromLocalFile(str(path)).toString()


def remember(module, params, uids):
    created = shared(module, CREATED_ATTR)
    key = hashlib.sha256(str(params.get("lease_id")).encode()).hexdigest()
    if created.get("lease") != key:
        created.clear()
        created.update(lease=key, uids=set())
    created["uids"].update(uids)


def created_by_lease(module, params, uid):
    created = shared(module, CREATED_ATTR)
    key = hashlib.sha256(str(params.get("lease_id")).encode()).hexdigest()
    return created.get("lease") == key and uid in created.get("uids", ())


def bake_state(module):
    state = shared(module, BAKE_ATTR)
    if not state.get("connected"):
        from substance_painter import event

        def ended(evt, state=state):
            state.update(running=False, status=getattr(evt.status, "name", str(evt.status)),
                         ended=time.monotonic())

        def progress(evt, state=state):
            state["progress"] = float(evt.progress)

        event.DISPATCHER.connect_strong(event.BakingProcessEnded, ended)
        event.DISPATCHER.connect_strong(event.BakingProcessProgress, progress)
        state.update(connected=True, running=False, status=None)
    return state


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
        reload = vars(substance_painter).get(RELOAD_ATTR) or {}
        return {
            "connected": True,
            "api_version": str(substance_painter.__version__),
            "open": opened,
            "file": project.file_path() if opened else None,
            "needs_saving": project.needs_saving() if opened else False,
            "busy": project.is_busy(),
            "edition_state": bool(project.is_in_edition_state()) if opened else False,
            "baking": bool((vars(substance_painter).get(BAKE_ATTR) or {}).get("running")),
            "mesh_reload": {key: reload.get(key) for key in ("pending", "status", "path")}
            if reload else None,
            "workspace": str(Path(workspace)),
            "workspace_exists": Path(workspace).is_dir(),
        }
    if operation == "bake_status":
        return _bake_status(substance_painter)
    if operation == "shelf":
        return _shelf(params)
    if operation in {"open_project", "create_project"}:
        if project.is_open():
            raise RuntimeError("A project is already open; close it in Painter before replacing it")
        if operation == "open_project":
            path = input_file(root(), required(params, "path"), {".spp"})
            project.open(str(path))
            return {"requested": operation, "path": str(path), "loading_may_be_pending": True}
        path = input_file(root(), required(params, "path"), MESH_EXTENSIONS)
        normal = params.get("normal_map_format", "OpenGL")
        if normal not in NORMAL_MAP_FORMATS:
            raise ValueError("normal_map_format must be OpenGL or DirectX")
        resolution = texture_resolution(params.get("default_texture_resolution"))
        udim = flag(params, "udim", False)
        per_fragment = flag(params, "tangent_space_per_fragment", False)
        template = params.get("template")
        template = None if template is None else input_file(root(), template, {".spt"})
        settings = project.Settings(
            normal_map_format=getattr(project.NormalMapFormat, normal),
            default_texture_resolution=resolution,
            project_workflow=project.ProjectWorkflow.UVTile if udim else None,
            tangent_space_mode=project.TangentSpace.PerFragment if per_fragment else None,
        )
        project.create(mesh_file_path=str(path), settings=settings,
                       template_file_path=None if template is None else str(template))
        return {"requested": operation, "path": str(path), "loading_may_be_pending": True,
                "normal_map_format": normal, "default_texture_resolution": resolution,
                "udim": udim, "tangent_space_per_fragment": per_fragment,
                "template": None if template is None else str(template)}
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
    if (vars(substance_painter).get(BAKE_ATTR) or {}).get("running"):
        raise RuntimeError("Painter is baking and not idle; poll bake action=status")
    if not project.is_open() or project.is_busy():
        raise RuntimeError("Painter must have an open project and be idle")
    if operation in {"layer_stack", "create_fill_layer", "create_texture_fill"}:
        return _layers(operation, params, root)
    if operation == "layer_edit":
        return _layer_edit(params, root)
    if operation == "bake":
        return _bake(params, root)
    if operation == "bake_settings":
        return _bake_settings(params)
    if operation == "reload_mesh":
        path = input_file(root(), required(params, "path"), MESH_EXTENSIONS)
        preserve = flag(params, "preserve_strokes", True)
        state = shared(substance_painter, RELOAD_ATTR)

        def loaded(status, state=state):
            state.update(pending=False, status=getattr(status, "name", str(status)))

        state.update(pending=True, status=None, path=str(path))
        try:
            project.reload_mesh(str(path), project.MeshReloadingSettings(preserve_strokes=preserve),
                                loaded)
        except Exception:
            state.update(pending=False, status="NOT_STARTED")
            raise
        return {"requested": operation, "path": str(path), "preserve_strokes": preserve,
                "loading_may_be_pending": True,
                "next": "project wait_ready, then painter_status mesh_reload"}
    if operation == "texture_sets":
        sets = []
        for item in textureset.all_texture_sets():
            resolution = item.get_resolution()
            sets.append({
                "name": item.name(),
                "resolution": {"width": resolution.width, "height": resolution.height},
                "uv_tiles": [[tile.u, tile.v] for tile in item.all_uv_tiles()]
                if item.has_uv_tiles() else None,
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
        return _export(params, root)
    raise ValueError(f"Unknown operation: {operation}")


def texture_sets_named(textureset, names):
    known = {item.name(): item for item in textureset.all_texture_sets()}
    if names is None:
        return list(known.values())
    missing = [name for name in names if name not in known]
    if missing:
        raise ValueError(f"Texture Set not found: {', '.join(missing)}")
    return [known[name] for name in names]


def map_source(value):
    if type(value) in (int, float) or (isinstance(value, str) and re.fullmatch(
            r"\s*[0-9.]+\s*", value)):
        number = float(value)
        if number not in (0.0, 1.0):
            raise ValueError("Painter export supports only the constants 0 and 1")
        return "defaultMap", "white" if number else "black", False
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Channel sources must be a map name, 1-<map> or 0/1")
    text = value.strip().lower()
    if text.startswith("1-"):
        if text[2:].strip() == "roughness":
            return "virtualMap", "Glossiness", False
        raise ValueError(f"Painter export cannot invert {value[2:].strip()}; only 1-roughness "
                         "(Glossiness) is supported")
    if text in DOCUMENT_MAPS:
        return ("documentMap", *DOCUMENT_MAPS[text])
    if text in MESH_MAPS:
        return "meshMap", text, MESH_MAPS[text]
    if text in VIRTUAL_MAPS:
        return ("virtualMap", *VIRTUAL_MAPS[text])
    raise ValueError(f"Unknown map source {value}")


def bit_depth(value):
    if value is None:
        return None
    text = str(value) if type(value) is int else value
    if text not in {"8", "16", "16f", "32f"}:
        raise ValueError("bit_depth must be 8, 16, 16f or 32f")
    return text


def file_format(value, depth):
    if value is None:
        return None
    if value not in EXPORT_FORMATS:
        raise ValueError(f"file_format must be one of {', '.join(EXPORT_FORMATS)}")
    if depth is not None and depth not in EXPORT_FORMATS[value]:
        raise ValueError(f"{value} supports bit depths {', '.join(EXPORT_FORMATS[value])}")
    return value


def export_maps(specs, default_format, default_depth):
    if not isinstance(specs, list) or not 1 <= len(specs) <= 16:
        raise ValueError("maps must list 1 to 16 output maps")
    maps, names = [], set()
    for spec in specs:
        if not isinstance(spec, dict) or not set(spec) <= {"name", "format", "bit_depth",
                                                           "channels"}:
            raise ValueError("Each map needs name and channels, optionally format and bit_depth")
        name = spec.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_\-.()$]{1,128}", name)
                or name.startswith(".") or ".." in name
                or not set(re.findall(r"\$[A-Za-z]*", name)) <= NAME_TOKENS):
            raise ValueError(f"Invalid map name {name!r}; use letters, digits, _-.() and "
                             f"{', '.join(sorted(NAME_TOKENS))}")
        if name in names:
            raise ValueError(f"Duplicate map name {name}")
        names.add(name)
        depth = bit_depth(spec.get("bit_depth")) or default_depth
        fmt = spec.get("format") or default_format or "png"
        depth = depth or EXPORT_FORMATS.get(fmt, ("8",))[0]
        file_format(fmt, depth)
        channels = spec.get("channels")
        if not isinstance(channels, dict) or not channels:
            raise ValueError(f"Map {name} needs channels")
        entries, targets = [], []
        for key, value in channels.items():
            if key not in {"R", "G", "B", "A", "RGB", "L"}:
                raise ValueError("Channel keys must be R, G, B, A, RGB or L")
            kind, source, color = map_source(value)
            for target in (["R", "G", "B"] if key == "RGB" else [key]):
                if target in targets:
                    raise ValueError(f"Map {name} writes {target} twice")
                targets.append(target)
                if kind == "defaultMap":
                    channel = "R"
                elif color and target in {"R", "G", "B"}:
                    channel = target
                else:
                    channel = "L"
                entries.append({"destChannel": target, "srcChannel": channel,
                                "srcMapType": kind, "srcMapName": source})
        if set(targets) - {"A"} not in ({"R", "G", "B"}, {"L"}):
            raise ValueError(f"Map {name} must write R, G and B, or L, plus optional A")
        maps.append({"fileName": name, "channels": entries,
                     "parameters": {"fileFormat": fmt, "bitDepth": depth, "dithering": False}})
    return maps


def _export(params, root):
    from substance_painter import export, textureset

    folder = export_destination(root(), required(params, "directory"))
    preset, specs = params.get("preset"), params.get("maps")
    if (preset is None) == (specs is None):
        raise ValueError("Pass exactly one of preset or maps")
    depth = bit_depth(params.get("bit_depth"))
    fmt = file_format(params.get("file_format"), depth)
    padding = params.get("padding", "infinite")
    if padding not in PADDING:
        raise ValueError(f"padding must be one of {', '.join(PADDING)}")
    dilation = params.get("dilation_distance")
    if dilation is not None and (type(dilation) is not int or not 0 <= dilation <= 256):
        raise ValueError("dilation_distance must be an integer from 0 to 256")
    if dilation is not None and padding not in DILATED_PADDING:
        raise ValueError(f"dilation_distance needs padding {', '.join(DILATED_PADDING)}")
    rule = {"paddingAlgorithm": padding}
    if padding in DILATED_PADDING:
        rule["dilationDistance"] = 16 if dilation is None else dilation
    if params.get("resolution") is not None:
        rule["sizeLog2"] = power_of_two(params["resolution"], "resolution", 128, 8192)
    sets = texture_sets_named(textureset, names_list(params, "texture_sets"))
    tiles = params.get("udim_tiles")
    if tiles is not None:
        if (not isinstance(tiles, list) or not 1 <= len(tiles) <= 100 or any(
                not isinstance(tile, list) or len(tile) != 2
                or any(type(c) is not int or not 0 <= c <= 99 for c in tile) for tile in tiles)):
            raise ValueError("udim_tiles must list 1 to 100 [u, v] integer pairs")
        for item in sets:
            available = ({(tile.u, tile.v) for tile in item.all_uv_tiles()}
                         if item.has_uv_tiles() else set())
            if not {tuple(tile) for tile in tiles} <= available:
                raise ValueError(f"Texture Set {item.name()} lacks some requested UV tiles")
    config = {
        "exportShaderParams": False,
        "exportPath": str(folder),
        "exportList": [{"rootPath": item.name(), **({"filter": {"uvTiles": tiles}} if tiles
                                                    else {})} for item in sets],
        "exportParameters": [{"parameters": rule}],
    }
    if specs is None:
        presets = {item.url for item in export.list_predefined_export_presets()}
        if preset not in presets:
            raise ValueError("Choose a predefined preset URL returned by export_presets")
        config["defaultExportPreset"] = preset
        if fmt:
            rule["fileFormat"] = fmt
        if depth:
            rule["bitDepth"] = depth
    else:
        config["defaultExportPreset"] = CUSTOM_PRESET
        config["exportPresets"] = [{"name": CUSTOM_PRESET,
                                    "maps": export_maps(specs, fmt, depth)}]
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


def _shelf(params):
    from substance_painter import resource

    query = params.get("query", "")
    if not isinstance(query, str) or len(query) > 128:
        raise ValueError("query must be a string of at most 128 characters")
    usage = params.get("usage")
    if usage is not None and usage not in SHELF_USAGES:
        raise ValueError(f"usage must be one of {', '.join(SHELF_USAGES)}")
    limit = params.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    wanted = None if usage is None else getattr(resource.Usage, SHELF_USAGES[usage])
    labels = {getattr(resource.Usage, name): label for label, name in SHELF_USAGES.items()}
    found, truncated = [], False
    for item in resource.search(query):
        usages = item.usages()
        if wanted is not None and wanted not in usages:
            continue
        if len(found) == limit:
            truncated = True
            break
        found.append({"name": item.gui_name(), "url": item.identifier().url(),
                      "usages": [labels.get(u, u.name.lower()) for u in usages],
                      "type": item.type().name.lower(), "category": item.category()})
    return {"resources": found, "truncated": truncated}


def find_resource(resource, spec, usage):
    wanted = getattr(resource.Usage, SHELF_USAGES[usage])
    if not isinstance(spec, str) or not spec.strip() or len(spec) > 256:
        raise ValueError(f"resource must name a {usage} or give its resource:// URL")
    if spec.startswith("resource://"):
        found = resource.Resource.retrieve(resource.ResourceID.from_url(spec))
        if not found:
            raise ValueError(f"Resource not found: {spec}")
        item = found[0]
    else:
        matches = {}
        for item in resource.search(spec):
            rid = item.identifier()
            if spec in (item.gui_name(), rid.name) and wanted in item.usages():
                matches.setdefault((rid.context, rid.name), item)
        if not matches:
            raise ValueError(f"No {usage} named {spec}; use inspect action=shelf")
        if len(matches) > 1:
            urls = [item.identifier().url() for item in list(matches.values())[:5]]
            raise ValueError(f"Ambiguous {usage} name; pass one URL: {', '.join(urls)}")
        item = next(iter(matches.values()))
    if wanted not in item.usages():
        raise ValueError(f"Resource is not a {usage}")
    return item


def check_parameters(values):
    if values is None:
        return {}
    if not isinstance(values, dict) or len(values) > 64:
        raise ValueError("parameters must be an object with at most 64 entries")
    converted = {}
    for key, value in values.items():
        if type(value) is bool:
            value = int(value)
        elif isinstance(value, list):
            if not 1 <= len(value) <= 4 or any(
                    type(x) not in (int, float) or not math.isfinite(x) for x in value):
                raise ValueError(f"Parameter {key} lists must hold 1 to 4 finite numbers")
            value = tuple(value)
        elif type(value) is float and not math.isfinite(value):
            raise ValueError(f"Parameter {key} must be finite")
        elif type(value) not in (int, float, str):
            raise ValueError(f"Parameter {key} has an unsupported type")
        converted[key] = value
    return converted


def apply_parameters(source, values):
    if not values:
        return []
    properties = source.get_properties()
    for key in values:
        if key not in properties:
            raise ValueError(f"Unknown parameter {key}; available: "
                             f"{', '.join(sorted(properties)[:30])}")
        if properties[key].widget_type() == "Color":
            raise ValueError(f"Color parameter {key} is not supported")
    source.set_parameters(values)
    return sorted(values)


def node_by_uid(layerstack, uid, texture_set, label="uid"):
    if type(uid) is not int:
        raise ValueError(f"{label} must be an integer")
    try:
        node = layerstack.get_node_by_uid(uid)
    except ValueError as exc:
        raise ValueError(f"No layer stack node with {label} {uid}") from exc
    if node.get_texture_set().name() != texture_set:
        raise ValueError(f"Node {uid} belongs to another Texture Set")
    return node


def insert_position(layerstack, stack, params):
    texture_set = params["texture_set"]
    parent, above = params.get("parent_uid"), params.get("above_uid")
    if parent is not None and above is not None:
        raise ValueError("Use parent_uid or above_uid, not both")
    if parent is not None:
        node = node_by_uid(layerstack, parent, texture_set, "parent_uid")
        if not isinstance(node, layerstack.GroupLayerNode):
            raise ValueError("parent_uid must be a group layer")
        return (layerstack.InsertPosition.inside_node(node, layerstack.NodeStack.Substack),
                node.sub_layers())
    if above is not None:
        node = node_by_uid(layerstack, above, texture_set, "above_uid")
        if not isinstance(node, layerstack.LayerNode):
            raise ValueError("above_uid must be a layer, not an effect")
        owner = node.get_parent()
        return (layerstack.InsertPosition.above_node(node),
                owner.sub_layers() if owner is not None else layerstack.get_root_layer_nodes(stack))
    return (layerstack.InsertPosition.from_textureset_stack(stack),
            layerstack.get_root_layer_nodes(stack))


def new_name(params, siblings):
    name = required(params, "name")
    if not name.strip() or len(name) > 63:
        raise ValueError("Layer name must be nonempty and at most 63 characters")
    if any(node.get_name() == name for node in siblings):
        raise ValueError("A layer with this name already exists at that position")
    return name


def channel_type(textureset, stack, label):
    if label not in CHANNELS:
        raise ValueError(f"Unknown channel {label}; use {', '.join(CHANNELS)}")
    channel = getattr(textureset.ChannelType, CHANNELS[label])
    if channel not in stack.all_channels():
        raise ValueError(f"Texture Set has no {label} channel; add it in Painter first")
    return channel


def channel_label(channel):
    from substance_painter import textureset

    # ChannelType aliases: Roughness is SpecularRoughness, Metallic is BaseMetalness.
    for label, name in CHANNELS.items():
        if channel == getattr(textureset.ChannelType, name):
            return label
    return channel.name


def uniform_color(colormanagement, stack, channel, value):
    if isinstance(value, (list, tuple)):
        if len(value) != 3:
            raise ValueError("Color values must contain three values between 0 and 1")
        rgb = [unit_value(item) for item in value]
    else:
        rgb = [unit_value(value)] * 3
    srgb = stack.all_channels()[channel].format().name.startswith("sRGB")
    space = colormanagement.GenericColorSpace.sRGB if srgb else colormanagement.GenericColorSpace.Raw
    return colormanagement.Color(*rgb, space)


def discard(layerstack, nodes, title):
    if not nodes:
        return "no node created"
    try:
        with layerstack.ScopedModification(title):
            for node in nodes:
                layerstack.delete_node(node)
        return "new nodes removed"
    except Exception as exc:  # noqa: BLE001 -- report native partial effects
        return f"cleanup failed ({exc}); inspect the layer stack"


def readable(read):
    try:
        return read()
    except Exception:  # noqa: BLE001 -- one unreadable field must not hide the tree
        return None


def describe(node, context, depth):
    layerstack, source = context["layerstack"], context["source"]
    context["count"] += 1
    item = {"uid": node.uid(), "name": node.get_name(), "type": type(node).__name__,
            "visible": node.is_visible()}
    if node.has_blending():
        if node.is_in_mask_stack():
            item["blend_mode"] = readable(lambda: node.get_blending_mode().name)
            item["opacity"] = readable(node.get_opacity)
        else:
            item["blending"] = {
                label: {"mode": readable(lambda c=channel: node.get_blending_mode(c).name),
                        "opacity": readable(lambda c=channel: node.get_opacity(c))}
                for label, channel in context["channels"]}
    if isinstance(node, (layerstack.FillLayerNode, layerstack.FillEffectNode)):
        mode = node.source_mode
        item["source_mode"] = getattr(mode, "name", None)
        if mode == source.SourceMode.Split:
            item["channels"] = {}
            active = node.active_channels
            for label, channel in context["channels"]:
                if channel not in active:
                    continue
                value = readable(lambda c=channel: node.get_source(c))
                description = {"type": type(value).__name__}
                if isinstance(value, source.SourceBitmap):
                    description.update(resource_url=value.resource_id.url(),
                                       color_space=str(value.get_color_space()))
                elif isinstance(value, source.SourceUniformColor):
                    color = value.get_color()
                    description.update(value=list(color.value_raw),
                                       color_space=str(color.color_space))
                item["channels"][label] = description
        elif mode == source.SourceMode.Material:
            material = node.get_material_source()
            item["material_url"] = (material.resource_id.url()
                                    if isinstance(material, source.SourceSubstance) else None)
    if isinstance(node, layerstack.GeneratorEffectNode):
        generator = node.get_source()
        item["resource_url"] = generator.resource_id.url() if generator is not None else None
    if isinstance(node, layerstack.LayerNode):
        if node.has_mask():
            item["mask"] = {"background": node.get_mask_background().name,
                            "enabled": node.is_mask_enabled(),
                            "effects": walk(node.mask_effects(), context, depth + 1)}
        effects = node.content_effects()
        if effects:
            item["effects"] = walk(effects, context, depth + 1)
    if isinstance(node, layerstack.GroupLayerNode):
        item["layers"] = walk(node.sub_layers(), context, depth + 1)
    return item


def walk(nodes, context, depth):
    if depth > MAX_DEPTH and nodes:
        context["truncated"] = True
        return []
    items = []
    for node in nodes:
        if context["count"] >= MAX_NODES:
            context["truncated"] = True
            break
        items.append(describe(node, context, depth))
    return items


def _layers(operation, params, root):
    import substance_painter
    from substance_painter import colormanagement, layerstack, source, textureset

    stack = textureset.Stack.from_name(required(params, "texture_set"))
    if operation == "layer_stack":
        context = {"layerstack": layerstack, "source": source, "count": 0, "truncated": False,
                   "channels": [(channel_label(c), c) for c in stack.all_channels()]}
        layers = walk(layerstack.get_root_layer_nodes(stack), context, 1)
        return {"layers": layers, "node_count": context["count"],
                "truncated": context["truncated"]}
    position, siblings = insert_position(layerstack, stack, params)
    name = new_name(params, siblings)
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
    values = params.get("values") or {}
    if not isinstance(values, dict) or set(values) & {"base_color", "roughness", "metallic"}:
        raise ValueError("values sets extra channels; use base_color, roughness and metallic")
    extra = {channel_type(textureset, stack, label): value for label, value in values.items()}
    extra = {channel: uniform_color(colormanagement, stack, channel, value)
             for channel, value in extra.items()}
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
        with layerstack.ScopedModification(f"Create fill layer {name}"):
            node = layerstack.insert_fill(position)
            node.set_name(name)
            node.active_channels = active | set(extra)
            for channel, value in [(channels.Roughness, roughness), (channels.Metallic, metallic)]:
                node.set_source(channel, colormanagement.Color(
                    value, value, value, colormanagement.GenericColorSpace.Raw))
            for channel, value in extra.items():
                node.set_source(channel, value)
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
    remember(substance_painter, params, [node.uid()])
    extras = {channel_label(channel): list(node.get_source(channel).get_color().value)
              for channel in extra}
    if bitmap:
        result = {"name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
                  "base_color_texture": bitmap, "resource_url": imported.url(),
                  "color_space": "sRGB", "roughness": roughness, "metallic": metallic}
        if extras:
            result["values"] = extras
        if leftovers:
            result["cleanup_failed"] = leftovers
        return result
    result = {
        "name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
        "base_color": list(node.get_source(channels.BaseColor).get_color().value),
        "roughness": node.get_source(channels.Roughness).get_color().value[0],
        "metallic": node.get_source(channels.Metallic).get_color().value[0],
    }
    if extras:
        result["values"] = extras
    return result


def _layer_edit(params, root):
    import substance_painter
    from substance_painter import layerstack, resource, textureset

    action = required(params, "action")
    texture_set = required(params, "texture_set")
    stack = textureset.Stack.from_name(texture_set)
    if action in {"group", "smart_material", "sbsar_fill"}:
        position, siblings = insert_position(layerstack, stack, params)
        name = new_name(params, siblings)
        values = check_parameters(params.get("parameters"))
        if action != "sbsar_fill" and values:
            raise ValueError("parameters apply to sbsar_fill and generator only")
        graphs = []
        if action == "group":
            item = None
        elif action == "smart_material":
            item = find_resource(resource, params.get("resource"), "smart_material")
        else:
            path = input_file(root(), required(params, "sbsar"), {".sbsar"})
            if path.stat().st_size > 512 * 1024 * 1024:
                raise ValueError("sbsar exceeds 512 MiB")
            package = resource.import_project_resource(
                str(path), resource.Usage.BASE_MATERIAL, name="cavi_sbsar_" + uuid.uuid4().hex)
            graphs = ([child for child in package.children()
                       if child.type() == resource.Type.SUBSTANCE]
                      if package.type() == resource.Type.SUBSTANCE_PACKAGE else [package])
            if not graphs:
                raise RuntimeError(f"No material graph in {path.name}; imported resource "
                                   f"{package.identifier().url()} kept (no Painter API removes it)")
            item = graphs[0]
        node, applied = None, []
        try:
            with layerstack.ScopedModification(f"Create {action} {name}"):
                if action == "group":
                    node = layerstack.insert_group(position)
                elif action == "smart_material":
                    node = layerstack.insert_smart_material(position, item.identifier())
                else:
                    node = layerstack.insert_fill(position)
                node.set_name(name)
                if action == "sbsar_fill":
                    material = node.set_material_source(item.identifier())
                    applied = apply_parameters(material, values)
        except Exception as exc:
            cleanup = discard(layerstack, [node] if node is not None else [], "Remove failed layer")
            kept = (f"; imported resource {item.identifier().url()} kept (no Painter API "
                    "removes it)") if action == "sbsar_fill" else ""
            raise RuntimeError(f"{action} failed ({type(exc).__name__}: {exc}); {cleanup}{kept}"
                               ) from exc
        remember(substance_painter, params, [node.uid()])
        result = {"uid": node.uid(), "name": node.get_name(), "type": type(node).__name__,
                  "texture_set": texture_set}
        if item is not None:
            result["resource_url"] = item.identifier().url()
        if action == "smart_material":
            result["layers"] = [child.uid() for child in node.sub_layers()]
        if action == "sbsar_fill":
            result.update(parameters=applied, graphs=len(graphs),
                          active_channels=sorted(channel_label(c) for c in node.active_channels))
        return result
    node = node_by_uid(layerstack, required(params, "uid", None), texture_set)
    if action == "delete":
        if (not created_by_lease(substance_painter, params, node.uid())
                and params.get("confirm_name") != node.get_name()):
            raise RuntimeError("Node was not created by this lease; pass confirm_name equal "
                               "to its current name")
        name = node.get_name()
        with layerstack.ScopedModification(f"Delete {name}"):
            layerstack.delete_node(node)
        return {"deleted": params["uid"], "name": name}
    if action == "set_properties":
        return _set_properties(params, node, stack, layerstack, textureset)
    if not isinstance(node, layerstack.LayerNode):
        raise TypeError("uid must be a layer, not an effect")
    background = params.get("mask")
    if background is not None and background not in {"black", "white"}:
        raise ValueError("mask must be black or white")
    if action == "fill_mask":
        if background is None:
            raise ValueError("mask is required for action=fill_mask")
        if node.has_mask():
            raise ValueError("Layer already has a mask")
        with layerstack.ScopedModification(f"Add mask to {node.get_name()}"):
            node.add_mask(getattr(layerstack.MaskBackground, background.title()))
        return {"uid": node.uid(), "mask": background}
    if action not in {"smart_mask", "generator"}:
        raise ValueError(f"Unknown layer action: {action}")
    item = find_resource(resource, params.get("resource"), action)
    values = check_parameters(params.get("parameters"))
    if action == "smart_mask" and values:
        raise ValueError("parameters apply to sbsar_fill and generator only")
    added_mask, effects, applied = False, [], []
    try:
        with layerstack.ScopedModification(f"Add {action} to {node.get_name()}"):
            if not node.has_mask():
                node.add_mask(getattr(layerstack.MaskBackground, (background or "black").title()))
                added_mask = True
            position = layerstack.InsertPosition.inside_node(node, layerstack.NodeStack.Mask)
            if action == "smart_mask":
                effects = list(layerstack.insert_smart_mask(position, item.identifier()))
            else:
                effects = [layerstack.insert_generator_effect(position, item.identifier())]
                applied = apply_parameters(effects[0].get_source(), values)
    except Exception as exc:
        cleanup = discard(layerstack, effects, "Remove failed effects")
        if added_mask:
            try:
                with layerstack.ScopedModification("Remove added mask"):
                    node.remove_mask()
                cleanup += "; added mask removed"
            except Exception as mask_error:  # noqa: BLE001 -- report native partial effects
                cleanup += f"; mask removal failed ({mask_error})"
        raise RuntimeError(f"{action} failed ({type(exc).__name__}: {exc}); {cleanup}") from exc
    remember(substance_painter, params, [effect.uid() for effect in effects])
    return {"uid": node.uid(), "mask_added": added_mask, "resource_url": item.identifier().url(),
            "parameters": applied,
            "effects": [{"uid": e.uid(), "name": e.get_name(), "type": type(e).__name__}
                        for e in effects]}


def _set_properties(params, node, stack, layerstack, textureset):
    changes = {key: params[key] for key in ("name", "visible", "blend_mode", "opacity")
               if params.get(key) is not None}
    if not changes:
        raise ValueError("Pass name, visible, blend_mode or opacity")
    if "name" in changes and (not isinstance(changes["name"], str) or not changes["name"].strip()
                              or len(changes["name"]) > 63):
        raise ValueError("Layer name must be nonempty and at most 63 characters")
    if "visible" in changes and type(changes["visible"]) is not bool:
        raise ValueError("visible must be a boolean")
    if "opacity" in changes:
        changes["opacity"] = unit_value(changes["opacity"])
    if "blend_mode" in changes:
        mode = changes["blend_mode"]
        if not isinstance(mode, str) or mode.startswith("_") or not hasattr(
                layerstack.BlendingMode, mode):
            raise ValueError("Unknown blend_mode; e.g. Normal, Multiply, Overlay, Screen")
        changes["blend_mode"] = getattr(layerstack.BlendingMode, mode)
    channels = [None]
    if {"blend_mode", "opacity"} & set(changes):
        if not node.has_blending():
            raise ValueError("This node has no blending")
        label = params.get("channel")
        if node.is_in_mask_stack():
            if label is not None:
                raise ValueError("Mask nodes blend without a channel")
        elif label is not None:
            channels = [channel_type(textureset, stack, label)]
        else:
            channels = list(stack.all_channels())
    elif params.get("channel") is not None:
        raise ValueError("channel applies to blend_mode and opacity only")
    before = {"name": node.get_name(), "visible": node.is_visible(),
              "blend_mode": {c: node.get_blending_mode(c) for c in channels}
              if "blend_mode" in changes else {},
              "opacity": {c: node.get_opacity(c) for c in channels} if "opacity" in changes
              else {}}
    done = []
    try:
        with layerstack.ScopedModification(f"Edit {before['name']}"):
            if "name" in changes:
                node.set_name(changes["name"])
                done.append(("name", None))
            if "visible" in changes:
                node.set_visible(changes["visible"])
                done.append(("visible", None))
            for channel in channels:
                if "blend_mode" in changes:
                    node.set_blending_mode(changes["blend_mode"], channel)
                    done.append(("blend_mode", channel))
                if "opacity" in changes:
                    node.set_opacity(changes["opacity"], channel)
                    done.append(("opacity", channel))
    except Exception as exc:
        try:
            with layerstack.ScopedModification("Restore properties"):
                for key, channel in reversed(done):
                    if key == "name":
                        node.set_name(before["name"])
                    elif key == "visible":
                        node.set_visible(before["visible"])
                    elif key == "blend_mode":
                        node.set_blending_mode(before["blend_mode"][channel], channel)
                    else:
                        node.set_opacity(before["opacity"][channel], channel)
            restored = "previous values restored"
        except Exception as restore_error:  # noqa: BLE001 -- report native partial effects
            restored = f"restore failed ({restore_error}); inspect the layer stack"
        raise RuntimeError(f"set_properties failed ({exc}); {restored}") from exc
    return {"uid": node.uid(), "name": node.get_name(), "visible": node.is_visible(),
            "channels": [None if c is None else channel_label(c) for c in channels]
            if {"blend_mode", "opacity"} & set(changes) else [],
            "changed": sorted(changes)}


def common_value(common, key):
    if key not in common:
        raise RuntimeError(f"Painter does not expose baking parameter {key}")
    return common[key]


def enum_choice(prop, key, match):
    labels = prop.enum_values()
    hits = [value for label, value in labels.items() if match(label.lower())]
    if len(hits) != 1:
        raise RuntimeError(f"Cannot map {key} to one of {', '.join(sorted(labels))}")
    return hits[0]


def _bake(params, root):
    import substance_painter
    from substance_painter import baking, project, textureset

    state = bake_state(substance_painter)
    if state.get("running") or project.is_busy():
        raise RuntimeError("Painter is already baking or busy; check bake action=status")
    sets = texture_sets_named(textureset, names_list(params, "texture_sets"))
    maps = names_list(params, "maps", len(BAKE_MAPS))
    if maps is not None and not set(maps) <= set(BAKE_MAPS):
        raise ValueError(f"maps must be from {', '.join(BAKE_MAPS)}")
    usages = None if maps is None else [getattr(baking.MeshMapUsage, BAKE_MAPS[m]) for m in maps]
    meshes = names_list(params, "high_poly_meshes", 16)
    meshes = None if meshes is None else [
        file_url(input_file(root(), mesh, HIGH_POLY_EXTENSIONS)) for mesh in meshes]
    use_cage = params.get("use_cage")
    if use_cage is not None and type(use_cage) is not bool:
        raise ValueError("use_cage must be a boolean")
    cage = params.get("cage_mesh")
    if (cage is not None) != bool(use_cage):
        raise ValueError("cage_mesh is required with use_cage=true and only then")
    cage = None if cage is None else file_url(input_file(root(), cage, HIGH_POLY_EXTENSIONS))
    distances = {}
    for key, name in (("max_frontal", "MaxHeight"), ("max_rear", "MaxDepth")):
        value = params.get(key)
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{key} must be a number from 0 to 100")
            distances[name] = float(value)
    match = params.get("match")
    if match is not None and match not in {"always", "by_mesh_name"}:
        raise ValueError("match must be always or by_mesh_name")
    suffixes = {}
    for key, name in (("low_poly_suffix", "LowpolySuffix"), ("high_poly_suffix", "HipolySuffix")):
        value = params.get(key)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,32}", value):
                raise ValueError(f"{key} must be 1 to 32 letters, digits, _ . or -")
            suffixes[name] = value
    antialiasing = params.get("antialiasing")
    if antialiasing is not None and antialiasing not in ANTIALIASING:
        raise ValueError(f"antialiasing must be one of {', '.join(ANTIALIASING)}")
    size = params.get("resolution")
    size = None if size is None else power_of_two(size, "resolution", 128, 8192)
    plan, previous = {}, {}
    for item in sets:
        bake_params = baking.BakingParameters.from_texture_set(item)
        common = bake_params.common()
        values = {}
        if size is not None:
            values[common_value(common, "OutputSize")] = (size, size)
        if meshes is not None:
            values[common_value(common, "LowAsHigh")] = False
            values[common_value(common, "HipolyMesh")] = "|".join(meshes)
        if use_cage is not None:
            mode = common_value(common, "CageMode")
            label = "custom file" if use_cage else "distance-based"
            values[mode] = enum_choice(mode, "CageMode", lambda text, label=label: text == label)
            if cage is not None:
                values[common_value(common, "CageMesh")] = cage
        for name, value in {**distances, **suffixes}.items():
            values[common_value(common, name)] = value
        if match is not None:
            prop = common_value(common, "FilterMethod")
            token = "always" if match == "always" else "name"
            values[prop] = enum_choice(prop, "FilterMethod", lambda text, t=token: t in text)
        if antialiasing is not None:
            prop = common_value(common, "SubSampling")
            values[prop] = enum_choice(prop, "SubSampling",
                                       lambda text, t=antialiasing: t in text)
        if not (usages or bake_params.get_enabled_bakers()):
            raise ValueError(f"No maps enabled for {item.name()}; pass maps")
        plan.update(values)
        previous.update({prop: prop.value() for prop in values})
    selected = {item.name() for item in sets}
    everything = textureset.all_texture_sets()
    before = {item.name(): (baking.BakingParameters.from_texture_set(item).is_textureset_enabled(),
                            baking.BakingParameters.from_texture_set(item).get_enabled_bakers())
              for item in everything}
    try:
        if plan:
            baking.BakingParameters.set(plan)
        for item in everything:
            bake_params = baking.BakingParameters.from_texture_set(item)
            bake_params.set_textureset_enabled(item.name() in selected)
            if usages is not None and item.name() in selected:
                bake_params.set_enabled_bakers(usages)
        enabled = {item.name(): [usage.name for usage in baking.BakingParameters
                                 .from_texture_set(item).get_enabled_bakers()] for item in sets}
        state.update(running=True, status=None, progress=0.0, started=time.monotonic(),
                     ended=None, texture_sets=sorted(selected), maps=enabled)
        baking.bake_selected_textures_async()
    except Exception as exc:
        state["running"] = False
        try:
            if previous:
                baking.BakingParameters.set(previous)
            for item in everything:
                flag_value, bakers = before[item.name()]
                bake_params = baking.BakingParameters.from_texture_set(item)
                bake_params.set_textureset_enabled(flag_value)
                bake_params.set_enabled_bakers(bakers)
            restored = "baking settings restored"
        except Exception as restore_error:  # noqa: BLE001 -- report native partial effects
            restored = f"restore failed ({restore_error}); inspect baking settings"
        raise RuntimeError(f"Bake failed to start ({type(exc).__name__}: {exc}); {restored}"
                           ) from exc
    return {"started": True, "texture_sets": sorted(selected), "maps": enabled,
            "disabled_texture_sets": sorted(set(before) - selected),
            "next": "bake action=status until busy is false"}


def _bake_status(module):
    from substance_painter import project, textureset

    state = vars(module).get(BAKE_ATTR) or {}
    opened = project.is_open()
    busy = project.is_busy()
    result = {"busy": bool(state.get("running")) or busy, "running": bool(state.get("running")),
              "status": state.get("status"), "progress": state.get("progress"),
              "seconds_since_start": round(time.monotonic() - state["started"], 1)
              if state.get("started") else None,
              "texture_sets": state.get("texture_sets", []), "maps": state.get("maps", {})}
    if opened and not result["busy"]:
        result["mesh_maps"] = mesh_maps(textureset)
    return result


def mesh_maps(textureset, names=None):
    found = {}
    for item in textureset.all_texture_sets():
        if names is not None and item.name() not in names:
            continue
        maps = {}
        for label, usage in BAKE_MAPS.items():
            resource_id = item.get_mesh_map_resource(getattr(textureset.MeshMapUsage, usage))
            maps[label] = resource_id.url() if resource_id is not None else None
        found[item.name()] = maps
    return found


def _bake_settings(params):
    from substance_painter import baking, textureset

    name = params.get("texture_set")
    sets = texture_sets_named(textureset, None if name is None else [name])
    labels = {getattr(baking.MeshMapUsage, value): key for key, value in BAKE_MAPS.items()}
    found = mesh_maps(textureset, {item.name() for item in sets})
    result = []
    for item in sets:
        bake_params = baking.BakingParameters.from_texture_set(item)
        result.append({
            "name": item.name(),
            "enabled": bake_params.is_textureset_enabled(),
            "maps": [labels.get(usage, usage.name) for usage in bake_params.get_enabled_bakers()],
            "curvature_method": bake_params.get_curvature_method().name,
            "common": {key: plain(prop.value()) for key, prop in sorted(bake_params.common().items())},
            "mesh_maps": found[item.name()],
        })
    return {"texture_sets": result}
