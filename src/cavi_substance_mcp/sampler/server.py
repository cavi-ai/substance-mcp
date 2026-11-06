"""Typed stdio MCP surface for Substance 3D Sampler startup scripts."""

import asyncio
from typing import Annotated, Literal

from cavi_mcp_core.server import DESTRUCTIVE, READ_ONLY, WRITES, Server, need, reject_foreign
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

from ..core import workspace
from ..core.contracts import (
    SamplerAssetResult,
    SamplerBuildResult,
    SamplerExportResult,
    SamplerInspection,
    SamplerLayerResult,
    SamplerPackResult,
    SamplerProjectResult,
    SamplerStatus,
)
from ..packing import USAGE, PackSpec
from . import batch, launch
from . import pack as packer
from .names import BLEND_MODES, EXPORT_FORMATS, IMPORT_OPTIONS, MATERIAL_TYPES
from .targets import TARGETS


class SamplerServer(Server):
    """Advertise action-dependent inputs without changing the flat tool arguments."""

    async def list_tools(self):
        tools = await super().list_tools()
        for index, tool in enumerate(tools):
            if tool.name != "sampler_inspect":
                continue
            requirements = []
            for action, names in (("project", ("project",)), ("asset", ("project", "asset"))):
                requirements.append({
                    "if": {"properties": {"action": {"const": action}}, "required": ["action"]},
                    "then": {"required": list(names),
                             "properties": {name: {"type": "string"} for name in names}},
                })
            tools[index] = tool.model_copy(update={"input_schema": {
                **tool.input_schema, "allOf": requirements,
            }})
        return tools


mcp = SamplerServer(
    "cavi-substance-sampler-mcp",
    package="cavi-substance-mcp",
    instructions=(
        "Runs Sampler startup scripts through --run-script-silent. Quit Sampler first; an "
        "existing instance ignores startup scripts. Paths are relative to "
        "CAVI_SUBSTANCE_WORKSPACE and outputs go to new directories. Call sampler_status first. "
        "Use sampler_build for multi-step batch jobs."
    ),
)
Timeout = Annotated[StrictInt, Field(ge=1, le=3600)]
ProjectPath = Annotated[str, Field(description="Existing .ssa relative to the workspace.")]
NewDirectory = Annotated[str, Field(description="New directory relative to the workspace.")]
AssetName = Annotated[str, Field(min_length=1, max_length=128)]
PlainName = Annotated[str, Field(min_length=1, max_length=96)]
Query = Annotated[str, Field(max_length=128)]
ImportOption = Literal[IMPORT_OPTIONS]
BlendMode = Literal[BLEND_MODES]
ExportFormat = Literal[EXPORT_FORMATS]
MaterialType = Literal[MATERIAL_TYPES]
TargetName = Literal[tuple(TARGETS)]
ParameterValue = bool | int | float | str | list[bool | int | float | str]
JobFile = Annotated[str, Field(description="Workspace .json batch job.")]
ChannelName = Annotated[str, Field(min_length=1, max_length=64)]
PackUsage = Annotated[str, StringConstraints(pattern=f"^{USAGE}$")]
ResourceNameArg = Annotated[str, Field(min_length=1, max_length=128)]


class AssetMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    author: Annotated[str | None, Field(max_length=128)] = None
    category: Annotated[str | None, Field(max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    tags: Annotated[list[str] | None, Field(max_length=64)] = None
    physical_size: tuple[float, float, float] | None = None
    custom_metadata: Annotated[dict[str, bool | int | float | str | None] | None,
                               Field(max_length=64)] = None
INSPECT_PARAMS = {
    "project": {"project"},
    "asset": {"project", "asset"},
    "catalog": {"query"},
}
ASSET_PARAMS = {
    "create": {"project", "name", "model", "preset", "empty"},
    "delete": {"project", "asset"},
    "import_resource": {"project", "path", "resources", "source_manifest", "expected_sha256"},
    "set_metadata": {"project", "asset", "metadata"},
    "activate_channels": {"project", "asset", "channels"},
    "deactivate_channels": {"project", "asset", "channels"},
    "import_images": {"project", "asset", "images", "position", "import_option", "insert_above"},
    "import_textures": {"project", "asset", "images", "position", "insert_above"},
    "insert_filter": {"project", "asset", "filter", "position", "insert_above"},
    "insert_material": {
        "project", "asset", "material", "position", "blend_mode", "insert_above",
    },
}


@mcp.tool(annotations=READ_ONLY)
async def sampler_status() -> SamplerStatus:
    """Sampler executable, version, scripts folder, workspace and running state."""
    return await asyncio.to_thread(launch.status)


@mcp.tool(annotations=READ_ONLY)
async def sampler_inspect(
    action: Annotated[
        Literal["project", "asset", "catalog"],
        Field(description="project needs project; asset needs project and asset."),
    ],
    project: Annotated[
        ProjectPath | None, Field(description="Required for project/asset: workspace .ssa.")
    ] = None,
    asset: Annotated[
        AssetName | None, Field(description="Required for asset: existing asset name.")
    ] = None,
    query: Annotated[Query, Field(description="catalog: resource search text.")] = "",
    timeout_seconds: Annotated[Timeout, Field(description="Sampler timeout in seconds.")] = 600,
) -> SamplerInspection:
    """project lists assets; asset lists layers; catalog lists resources and targets."""
    given = {"project": project, "asset": asset, "query": query or None}
    reject_foreign(action, given, INSPECT_PARAMS[action])
    if action in ("project", "asset"):
        need(project, "project", action)
    if action == "asset":
        need(asset, "asset", action)
    arguments = {key: value for key, value in given.items() if value is not None}
    operation = {"project": "inspect_project", "asset": "inspect_asset",
                 "catalog": "catalog"}[action]
    return await launch.invoke(operation, arguments, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_project(
    action: Annotated[Literal["create", "save_as"], Field(description="Create or copy a project.")],
    directory: NewDirectory,
    name: Annotated[
        PlainName | None,
        Field(description="create: required project name; save_as: optional new name."),
    ] = None,
    project: Annotated[
        ProjectPath | None, Field(description="save_as: required workspace .ssa.")
    ] = None,
    timeout_seconds: Annotated[Timeout, Field(description="Sampler timeout in seconds.")] = 600,
) -> SamplerProjectResult:
    """create a new .ssa in a new directory; save_as copies an existing project."""
    if action == "create":
        if project is not None:
            raise ValueError("project is only valid for action=save_as")
        need(name, "name", action)
    else:
        need(project, "project", action)
    operation = "create_project" if action == "create" else "save_as"
    return await launch.invoke(operation, {
        key: value for key, value in {
            "directory": directory, "name": name, "project": project,
        }.items() if value is not None
    }, timeout_seconds)


@mcp.tool(annotations=DESTRUCTIVE)
async def sampler_asset(
    action: Annotated[
        Literal[
            "create",
            "delete",
            "import_resource",
            "set_metadata",
            "activate_channels",
            "deactivate_channels",
            "import_images",
            "import_textures",
            "insert_filter",
            "insert_material",
        ],
        Field(description="Asset or resource operation."),
    ],
    project: ProjectPath,
    asset: Annotated[
        AssetName | None,
        Field(description="Existing asset; required except create/import_resource."),
    ] = None,
    name: Annotated[PlainName | None, Field(description="create: required asset name.")] = None,
    model: Annotated[
        Literal["asm", "openpbr"], Field(description="create: material model.")
    ] = "asm",
    preset: Annotated[
        str | None, Field(max_length=128, description="create: asset preset.")
    ] = None,
    empty: Annotated[
        StrictBool, Field(description="create: start without default layers.")
    ] = False,
    metadata: Annotated[
        AssetMetadata | None, Field(description="set_metadata: nonempty metadata fields.")
    ] = None,
    channels: Annotated[
        list[ChannelName] | None,
        Field(
            min_length=1, max_length=64, description="activate/deactivate_channels: channel names."
        ),
    ] = None,
    path: Annotated[
        str | None, Field(description="import_resource: workspace .sbsar or .sbs.")
    ] = None,
    source_manifest: Annotated[
        str | None, Field(description="import_resource: source receipt.")
    ] = None,
    expected_sha256: Annotated[
        str | None, Field(description="import_resource: expected source digest.")
    ] = None,
    resources: Annotated[
        list[ResourceNameArg] | None,
        Field(
            min_length=1, max_length=64, description="import_resource: resource names to register."
        ),
    ] = None,
    images: Annotated[
        list[str] | dict[str, str] | None,
        Field(description="import_images: path list; import_textures: usage to path."),
    ] = None,
    position: Annotated[
        StrictInt, Field(ge=0, le=100000, description="Layer insertion index.")
    ] = 0,
    import_option: Annotated[
        ImportOption, Field(description="import_images: conversion method.")
    ] = "use_as_bitmap",
    insert_above: Annotated[
        StrictBool | None, Field(description="Insert above the selected layer.")
    ] = None,
    filter: Annotated[
        PlainName | None, Field(description="insert_filter: required catalog name.")
    ] = None,
    material: Annotated[
        PlainName | None, Field(description="insert_material: required catalog name.")
    ] = None,
    blend_mode: Annotated[
        BlendMode, Field(description="insert_material: layer blend mode.")
    ] = "blend_normal",
    confirm: Annotated[StrictBool, Field(description="Required true for action=delete.")] = False,
    timeout_seconds: Annotated[Timeout, Field(description="Sampler timeout in seconds.")] = 600,
) -> SamplerAssetResult:
    """Create assets/resources, set metadata or channels, or extend asset layers.

    Edits run on a copy that replaces the project only after Sampler saved it.
    """
    values = {
        "project": project, "asset": asset, "name": name, "model": model, "preset": preset,
        "empty": empty, "metadata": metadata, "channels": channels, "path": path,
        "source_manifest": source_manifest, "expected_sha256": expected_sha256,
        "resources": resources, "images": images, "position": position,
        "import_option": import_option,
        "insert_above": insert_above, "filter": filter, "material": material,
        "blend_mode": blend_mode,
    }
    if action != "create":
        values["model"], values["preset"], values["empty"] = None, None, None
    if action != "set_metadata":
        values["metadata"] = None
    if action not in ("activate_channels", "deactivate_channels"):
        values["channels"] = None
    if action != "import_resource":
        values["path"], values["resources"] = None, None
    if action != "import_images":
        values["import_option"] = None
    if action != "insert_material":
        values["blend_mode"] = None
    if action not in ("import_images", "import_textures", "insert_filter", "insert_material"):
        values["position"], values["insert_above"] = None, None
    reject_foreign(action, values, ASSET_PARAMS[action])
    if action == "delete" and not confirm:
        raise ValueError("confirm must be true for action=delete")
    if action != "delete" and confirm:
        raise ValueError("confirm is only valid for action=delete")
    if action == "create":
        need(name, "name", action)
    if action == "import_resource":
        need(path, "path", action)
    if action in ("delete", "set_metadata", "activate_channels", "deactivate_channels",
                  "import_images", "import_textures", "insert_filter", "insert_material"):
        need(asset, "asset", action)
    if action == "set_metadata":
        need(metadata, "metadata", action)
        if not metadata.model_dump(exclude_none=True):
            raise ValueError("metadata must contain at least one field")
    if action in ("activate_channels", "deactivate_channels"):
        need(channels, "channels", action)
    if action == "import_images" and not isinstance(images, list):
        raise ValueError("images must be a list of workspace images")
    if action == "import_textures" and not isinstance(images, dict):
        raise ValueError("images must map usages to workspace images")
    if action == "insert_filter":
        need(filter, "filter", action)
    if action == "insert_material":
        need(material, "material", action)
    arguments = {"project": project}
    if asset is not None:
        arguments["asset"] = asset
    if action == "create":
        arguments["name"] = name
        arguments["model"] = model
        arguments["empty"] = empty
        if preset is not None:
            arguments["preset"] = preset
    if action == "import_resource":
        arguments["path"] = path
        if resources is not None:
            arguments["resources"] = resources
        if source_manifest is not None:
            arguments["source_manifest"] = source_manifest
        if expected_sha256 is not None:
            arguments["expected_sha256"] = expected_sha256
    if action == "set_metadata":
        arguments.update(metadata.model_dump(exclude_none=True))
    if action in ("activate_channels", "deactivate_channels"):
        arguments["channels"] = channels
    if action in ("import_images", "import_textures"):
        arguments["images"] = images
    if action in ("import_images", "import_textures", "insert_filter", "insert_material"):
        arguments["position"] = position
        if insert_above is not None:
            arguments["insert_above"] = insert_above
    if action == "import_images":
        arguments["import_option"] = import_option
    if action == "insert_filter":
        arguments["filter"] = filter
    if action == "insert_material":
        arguments["material"] = material
        arguments["blend_mode"] = blend_mode
    operation = {"create": "create_asset", "delete": "delete_asset"}.get(action, action)
    return await launch.invoke(operation, arguments, timeout_seconds)


@mcp.tool(annotations=DESTRUCTIVE)
async def sampler_layer(
    action: Annotated[
        Literal[
            "set_parameters",
            "apply_preset",
            "set_channels",
            "set_blend_parameters",
            "remove",
        ],
        Field(description="Layer operation."),
    ],
    project: ProjectPath,
    asset: Annotated[AssetName, Field(description="Existing asset name.")],
    layer: Annotated[str, Field(min_length=1, max_length=128, description="Existing layer name.")],
    position: Annotated[
        StrictInt | None, Field(ge=0, le=100000, description="Disambiguating layer index.")
    ] = None,
    parameters: Annotated[
        dict[str, ParameterValue] | None,
        Field(max_length=64, description="set_parameters/set_blend_parameters: nonempty values."),
    ] = None,
    preset: Annotated[
        str | None, Field(max_length=128, description="apply_preset: required preset name.")
    ] = None,
    applies_to: Annotated[
        list[ChannelName] | None,
        Field(min_length=1, max_length=64, description="set_channels: layer channels."),
    ] = None,
    blend_applies_to: Annotated[
        list[ChannelName] | None,
        Field(min_length=1, max_length=64, description="set_channels: blend channels."),
    ] = None,
    confirm: Annotated[StrictBool, Field(description="remove: required true.")] = False,
    timeout_seconds: Annotated[Timeout, Field(description="Sampler timeout in seconds.")] = 600,
) -> SamplerLayerResult:
    """Edit layer parameters, presets, channels or blend parameters."""
    if action == "set_parameters":
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("parameters must be a non-empty object")
        if (
            preset is not None or confirm or applies_to is not None
            or blend_applies_to is not None
        ):
            raise ValueError("only parameters is valid for action=set_parameters")
        arguments = {"project": project, "asset": asset, "layer": layer,
                     "position": position, "parameters": parameters}
    elif action == "apply_preset":
        need(preset, "preset", action)
        if (
            parameters is not None or confirm or applies_to is not None
            or blend_applies_to is not None
        ):
            raise ValueError("only preset is valid for action=apply_preset")
        arguments = {"project": project, "asset": asset, "layer": layer,
                     "position": position, "preset": preset}
    elif action == "set_channels":
        if applies_to is None and blend_applies_to is None:
            raise ValueError("set_channels needs applies_to or blend_applies_to")
        if parameters is not None or preset is not None or confirm:
            raise ValueError("only applies_to and blend_applies_to are valid here")
        arguments = {"project": project, "asset": asset, "layer": layer,
                     "position": position, "applies_to": applies_to,
                     "blend_applies_to": blend_applies_to}
    elif action == "set_blend_parameters":
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("parameters must be a non-empty object")
        if (
            preset is not None or confirm or applies_to is not None
            or blend_applies_to is not None
        ):
            raise ValueError("only parameters is valid for set_blend_parameters")
        arguments = {"project": project, "asset": asset, "layer": layer,
                     "position": position, "parameters": parameters}
    else:
        if not confirm:
            raise ValueError("confirm must be true for action=remove")
        if (
            parameters is not None or preset is not None or applies_to is not None
            or blend_applies_to is not None
        ):
            raise ValueError("only confirm is valid for action=remove")
        arguments = {"project": project, "asset": asset, "layer": layer,
                     "position": position}
    operation = {"set_parameters": "set_parameters", "apply_preset": "apply_preset",
                 "set_channels": "set_channels", "set_blend_parameters": "set_blend_parameters",
                 "remove": "remove_layer"}[action]
    return await launch.invoke(operation, {
        key: value for key, value in arguments.items() if value is not None
    }, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_export(
    project: ProjectPath,
    directory: NewDirectory,
    asset: Annotated[
        AssetName | None, Field(description="Asset name; null selects the selected or first asset.")
    ] = None,
    target: Annotated[
        TargetName | None, Field(description="Texture target with optional channel packing.")
    ] = None,
    format: Annotated[
        ExportFormat | None,
        Field(description="Default sbsar, or png when target is set."),
    ] = None,
    preset: Annotated[str | None, Field(max_length=128, description="Export preset name.")] = None,
    channels: Annotated[
        list[str] | None, Field(max_length=64, description="Texture channels to export.")
    ] = None,
    resolution: Annotated[
        StrictInt | None, Field(ge=64, le=8192, description="Output texture size.")
    ] = None,
    material_type: Annotated[
        MaterialType, Field(description="Target material variant.")
    ] = "standard",
    subfolder: Annotated[StrictBool, Field(description="Create a per-asset subfolder.")] = False,
    wait_seconds: Annotated[
        StrictInt, Field(ge=1, le=3600, description="Wait for output files, in seconds.")
    ] = 600,
    dry_run: Annotated[StrictBool, Field(description="Only explicit false writes files.")] = True,
    timeout_seconds: Annotated[
        Timeout, Field(description="Sampler process timeout in seconds.")
    ] = 600,
) -> SamplerExportResult:
    """Preview or export an asset into a new workspace directory."""
    if target is not None and format in ("sbsar", "sbs"):
        raise ValueError("target exports textures; use png, tga, tif or exr")
    if format is None:
        format = "png" if target is not None else "sbsar"
    return await launch.invoke("export", {
        "project": project, "directory": directory, "asset": asset, "target": target,
        "format": format, "preset": preset, "channels": channels, "resolution": resolution,
        "material_type": material_type, "subfolder": subfolder, "wait_seconds": wait_seconds,
        "dry_run": dry_run,
    }, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_build(
    job: JobFile,
    dry_run: Annotated[StrictBool, Field(description="Only explicit false runs the job.")] = True,
    check_sampler: Annotated[
        StrictBool, Field(description="Dry run: validate against installed resources.")
    ] = False,
    check_project: Annotated[
        StrictBool, Field(description="Dry run: inspect the source project.")
    ] = False,
    check_build: Annotated[
        StrictBool, Field(description="Dry run: build a temporary project and inspect it.")
    ] = False,
    timeout_seconds: Annotated[Timeout, Field(description="Job timeout in seconds.")] = 1800,
) -> SamplerBuildResult:
    """Validate a batch job file (default) or, with dry_run=false, run it."""
    if (check_sampler or check_project or check_build) and not dry_run:
        raise ValueError(
            "check_sampler, check_project and check_build require dry_run=true"
        )
    root = workspace.root()
    if dry_run:
        if check_build:
            return {
                **await batch.check_build_async(job, root, timeout_seconds),
                "dry_run": True,
            }
        if check_project:
            return {**await batch.validate_with_project_async(job, root), "dry_run": True}
        if check_sampler:
            return {**await batch.validate_with_sampler_async(job, root), "dry_run": True}
        return {**batch.validate_job_file(job, root), "dry_run": True}
    return await batch.run_job_file_async(job, root, timeout_seconds=timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_pack(
    directory: NewDirectory,
    sources: Annotated[
        dict[PackUsage, str],
        Field(min_length=1, max_length=32, description="Map usage to workspace image path."),
    ],
    pack: Annotated[PackSpec, Field(description="Channel packing spec.")],
) -> SamplerPackResult:
    """Pack maps into a new workspace directory."""
    return await packer.pack(directory, sources, pack)


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
