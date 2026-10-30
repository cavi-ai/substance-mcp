"""Trusted operations evaluated inside Painter's Python interpreter."""

import hashlib
import math
import os
import secrets
import struct
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Lease:
    def __init__(self):
        self.lock = threading.RLock()
        self.token = None
        self.owner = None
        self.project = None
        self.expires = 0.0

    def status(self, project):
        return {
            "active": self.token is not None and time.monotonic() < self.expires,
            "owner": self.owner,
            "project_id": project,
            "claimed_project_id": self.project,
            "expires_in": max(0.0, self.expires - time.monotonic()),
        }

    def require(self, token, project):
        if (
            not isinstance(token, str)
            or self.token is None
            or not secrets.compare_digest(token, self.token)
        ):
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
                token = params.get("lease_id")
                if (
                    not isinstance(token, str)
                    or self.token is None
                    or not secrets.compare_digest(token, self.token)
                ):
                    raise RuntimeError("A matching lease_id is required to release ownership")
                self.token, self.owner, self.project, self.expires = None, None, None, 0.0
                return self.status(project)
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
                if self.token is not None and time.monotonic() < self.expires:
                    raise RuntimeError(f"Application is owned by {self.owner}; wait for release")
                self.token, self.owner, self.project = secrets.token_urlsafe(32), owner, project
            self.expires = time.monotonic() + ttl
            result = self.status(project)
            if action == "claim":
                result["lease_id"] = self.token
            return result

    @contextmanager
    def edit(self, token, identity):
        with self.lock:
            self.require(token, identity())
            yield
            # Owned saves and project transitions may change the project identity.
            self.project = identity()


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


def execute(operation: str, params: dict, workspace: str) -> dict:
    import substance_painter
    from substance_painter import project

    # setdefault keeps one application-local lease even for concurrent remote clients.
    session = vars(substance_painter).setdefault(
        "_cavi_mcp_lease", Lease()
    )

    def identity():
        value = (os.getpid(), str(project.get_uuid()) if project.is_open() else None,
                 project.file_path() if project.is_open() else None)
        return hashlib.sha256(repr(value).encode()).hexdigest()

    if operation == "session_control":
        return session.control(params, identity)
    if operation in {"status", "texture_sets", "layer_stack", "export_presets"} or (
        operation == "export_textures" and params.get("dry_run", True) is True
    ):
        return _execute(operation, params, workspace)
    with session.edit(params.get("lease_id"), identity):
        return _execute(operation, params, workspace)


def _execute(operation: str, params: dict, workspace: str) -> dict:
    import substance_painter
    from substance_painter import export, project, textureset

    root = Path(workspace).resolve(strict=True)
    if operation == "status":
        opened = project.is_open()
        return {
            "connected": True,
            "api_version": str(substance_painter.__version__),
            "open": opened,
            "file": project.file_path() if opened else None,
            "needs_saving": project.needs_saving() if opened else False,
            "busy": project.is_busy(),
            "workspace": str(root),
        }
    if operation in {"open_project", "create_project"}:
        if project.is_open():
            raise RuntimeError("A project is already open; close it in Painter before replacing it")
        if operation == "open_project":
            path = input_file(root, params["path"], {".spp"})
            project.open(str(path))
        else:
            path = input_file(root, params["path"], {".fbx", ".obj", ".gltf", ".glb"})
            project.create(mesh_file_path=str(path))
        return {"requested": operation, "path": str(path), "loading_may_be_pending": True}
    if operation == "close_project":
        if not project.is_open():
            return {"closed": True, "already_closed": True}
        if project.is_busy():
            raise RuntimeError("Painter must be idle before closing a project")
        if project.file_path() != params["expected_file"]:
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
        from substance_painter import colormanagement, layerstack

        stack = textureset.Stack.from_name(params["texture_set"])
        nodes = layerstack.get_root_layer_nodes(stack)
        if operation == "layer_stack":
            return {"layers": [{"uid": n.uid(), "name": n.get_name(),
                                 "type": type(n).__name__} for n in nodes]}
        name = params["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 63:
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
        bitmap, imported = None, None
        if operation == "create_texture_fill":
            from substance_painter import resource

            bitmap, payload = color_texture(root, params["base_color_texture"])
            with tempfile.TemporaryDirectory(prefix=".cavi-bitmap-", dir=root) as folder:
                snapshot = Path(folder) / "color.png"
                snapshot.write_bytes(payload)
                imported = resource.import_project_resource(
                    str(snapshot), resource.Usage.TEXTURE, name="cavi_color_" + uuid.uuid4().hex
                ).identifier()
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
                    source = node.set_source(channels.BaseColor, imported)
                    source.set_color_space(colormanagement.GenericColorSpace.sRGB)
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
                raise RuntimeError(
                    f"Texture fill failed ({type(exc).__name__}: {exc}); {cleanup}. "
                    "Imported resource remains: " + imported.url() + ". Inspect before retrying."
                ) from exc
            if node is not None and cleanup.startswith("layer cleanup failed"):
                raise RuntimeError(f"Fill failed ({exc}); {cleanup}") from exc
            raise
        if bitmap:
            return {"name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
                    "base_color_texture": bitmap, "resource_url": imported.url(), "color_space": "sRGB",
                    "roughness": roughness, "metallic": metallic}
        return {
            "name": node.get_name(), "uid": node.uid(), "texture_set": params["texture_set"],
            "base_color": list(node.get_source(channels.BaseColor).get_color().value),
            "roughness": node.get_source(channels.Roughness).get_color().value[0],
            "metallic": node.get_source(channels.Metallic).get_color().value[0],
        }
    if operation == "texture_sets":
        return {"texture_sets": [{"name": item.name(), "channels": {
            channel.name: value.format().name for channel, value in textureset.Stack.from_name(item.name()).all_channels().items()
        }} for item in textureset.all_texture_sets()]}
    if operation == "export_presets":
        return {
            "presets": [
                {"name": item.name, "url": item.url}
                for item in export.list_predefined_export_presets()
            ]
        }
    if operation == "save_copy":
        folder = export_destination(root, params["directory"])
        folder.mkdir(mode=0o700)
        path = folder / "project.spp"
        project.save_as_copy(str(path), project.ProjectSaveMode.Full)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Painter did not produce a saved project copy")
        return {"path": str(path), "bytes": path.stat().st_size}
    if operation == "export_textures":
        folder = export_destination(root, params["directory"])
        presets = {item.url for item in export.list_predefined_export_presets()}
        if params["preset"] not in presets:
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
