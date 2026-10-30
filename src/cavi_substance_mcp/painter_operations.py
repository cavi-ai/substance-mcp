"""Trusted operations evaluated inside Painter's Python interpreter."""

from pathlib import Path


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


def execute(operation: str, params: dict, workspace: str) -> dict:
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
    if operation == "texture_sets":
        return {"texture_sets": [{"name": item.name()} for item in textureset.all_texture_sets()]}
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
