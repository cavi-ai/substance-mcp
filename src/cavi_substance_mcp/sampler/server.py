"""Typed stdio MCP surface for Substance 3D Sampler startup scripts."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import batch, operations

mcp = FastMCP(
    "cavi-substance-sampler-mcp",
    instructions=(
        "Runs Sampler startup scripts through --run-script-silent. Quit Sampler first; an "
        "existing instance ignores startup scripts. Paths are relative to "
        "CAVI_SUBSTANCE_WORKSPACE and outputs go to new directories. Call sampler_status first. "
        "Use sampler_build for multi-step batch jobs."
    ),
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
Timeout = Annotated[StrictInt, Field(ge=1, le=3600)]
ProjectPath = Annotated[str, Field(description="Existing .ssa relative to the workspace.")]
NewDirectory = Annotated[str, Field(description="New directory relative to the workspace.")]
AssetName = Annotated[str, Field(min_length=1, max_length=128)]
PlainName = Annotated[str, Field(min_length=1, max_length=96)]
Query = Annotated[str, Field(max_length=128)]
ImportOption = Literal[
    "image_to_material_B2M", "image_to_material_AI_powered", "multiangle_to_material",
    "use_as_bitmap", "as_channels", "HDR_merge", "use_as_environment",
]
BlendMode = Literal[
    "blend_ao", "blend_color_id", "blend_curvature", "blend_custom_mask", "blend_opacity",
    "blend_height", "blend_normal", "blend_per_channel",
]
ExportFormat = Literal["sbsar", "sbs", "png", "jpg", "tga", "tif", "exr"]
MaterialType = Literal["standard", "decal", "atlas"]
TargetName = Literal["unreal", "unity", "blender", "generic"]
ParameterValue = bool | int | float | str | list[bool | int | float | str]
JobFile = Annotated[str, Field(description="Workspace .json batch job.")]
ChannelName = Annotated[str, Field(min_length=1, max_length=64)]
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
    "import_resource": {"project", "path", "resources"},
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


def need(value, name, action):
    if value is None:
        raise ValueError(f"{name} is required for action={action}")
    return value


def extras(action: str, values: dict, params: dict) -> list[str]:
    return sorted({key for key, value in values.items() if value is not None} - params[action])


@mcp.tool(annotations=READ_ONLY)
async def sampler_status() -> dict[str, Any]:
    """Sampler executable, version, scripts folder, workspace and running state."""
    return operations.status()


@mcp.tool(annotations=READ_ONLY)
async def sampler_inspect(
    action: Literal["project", "asset", "catalog"],
    project: ProjectPath | None = None,
    asset: AssetName | None = None,
    query: Query = "",
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
    """project lists assets; asset lists layers; catalog lists resources and targets."""
    given = {"project": project, "asset": asset, "query": query or None}
    unexpected = extras(action, given, INSPECT_PARAMS)
    if unexpected:
        raise ValueError(f"Not valid for action={action}: {', '.join(unexpected)}")
    if action == "asset":
        need(project, "project", action)
        need(asset, "asset", action)
    arguments = {key: value for key, value in given.items() if value is not None}
    operation = {"project": "inspect_project", "asset": "inspect_asset",
                 "catalog": "catalog"}[action]
    return await operations.invoke(operation, arguments, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_project(
    action: Literal["create", "save_as"],
    directory: NewDirectory,
    name: PlainName | None = None,
    project: ProjectPath | None = None,
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
    """create a new .ssa in a new directory; save_as copies an existing project."""
    if action == "create":
        if project is not None:
            raise ValueError("project is only valid for action=save_as")
        need(name, "name", action)
    else:
        need(project, "project", action)
    operation = "create_project" if action == "create" else "save_as"
    return await operations.invoke(operation, {
        key: value for key, value in {
            "directory": directory, "name": name, "project": project,
        }.items() if value is not None
    }, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_asset(
    action: Literal[
        "create", "delete", "import_resource", "set_metadata", "activate_channels",
        "deactivate_channels", "import_images", "import_textures", "insert_filter",
        "insert_material",
    ],
    project: ProjectPath,
    asset: AssetName | None = None,
    name: PlainName | None = None,
    model: Literal["asm", "openpbr"] = "asm",
    preset: Annotated[str | None, Field(max_length=128)] = None,
    empty: StrictBool = False,
    metadata: AssetMetadata | None = None,
    channels: Annotated[list[ChannelName] | None, Field(min_length=1, max_length=64)] = None,
    path: Annotated[str | None, Field(description="import_resource: workspace .sbsar or .sbs.")] = None,
    resources: Annotated[list[ResourceNameArg] | None, Field(min_length=1, max_length=64)] = None,
    images: list[str] | dict[str, str] | None = None,
    position: Annotated[StrictInt, Field(ge=0, le=100000)] = 0,
    import_option: ImportOption = "use_as_bitmap",
    insert_above: StrictBool | None = None,
    filter: PlainName | None = None,
    material: PlainName | None = None,
    blend_mode: BlendMode = "blend_normal",
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
    """Create assets/resources, set metadata or channels, or extend asset layers."""
    values = {
        "project": project, "asset": asset, "name": name, "model": model, "preset": preset,
        "empty": empty, "metadata": metadata, "channels": channels, "path": path,
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
    unexpected = extras(action, values, ASSET_PARAMS)
    if unexpected:
        raise ValueError(f"Not valid for action={action}: {', '.join(unexpected)}")
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
    return await operations.invoke(operation, arguments, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_layer(
    action: Literal[
        "set_parameters", "apply_preset", "set_channels", "set_blend_parameters", "remove",
    ],
    project: ProjectPath,
    asset: AssetName,
    layer: Annotated[str, Field(min_length=1, max_length=128)],
    position: Annotated[StrictInt | None, Field(ge=0, le=100000)] = None,
    parameters: Annotated[dict[str, ParameterValue] | None, Field(max_length=64)] = None,
    preset: Annotated[str | None, Field(max_length=128)] = None,
    applies_to: Annotated[list[ChannelName] | None, Field(min_length=1, max_length=64)] = None,
    blend_applies_to: Annotated[list[ChannelName] | None,
                               Field(min_length=1, max_length=64)] = None,
    confirm: StrictBool = False,
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
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
    return await operations.invoke(operation, {
        key: value for key, value in arguments.items() if value is not None
    }, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_export(
    project: ProjectPath,
    directory: NewDirectory,
    asset: AssetName | None = None,
    target: TargetName | None = None,
    format: ExportFormat = "sbsar",
    preset: Annotated[str | None, Field(max_length=128)] = None,
    channels: Annotated[list[str] | None, Field(max_length=64)] = None,
    resolution: Annotated[StrictInt | None, Field(ge=64, le=8192)] = None,
    material_type: MaterialType = "standard",
    subfolder: StrictBool = False,
    wait_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 600,
    dry_run: Annotated[StrictBool, Field(description="Only explicit false writes files.")] = True,
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
    """Preview or export an asset into a new workspace directory."""
    if target is not None and format in ("sbsar", "sbs"):
        format = "png"
    return await operations.invoke("export", {
        "project": project, "directory": directory, "asset": asset, "target": target,
        "format": format, "preset": preset, "channels": channels, "resolution": resolution,
        "material_type": material_type, "subfolder": subfolder, "wait_seconds": wait_seconds,
        "dry_run": dry_run,
    }, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_build(
    job: JobFile,
    dry_run: StrictBool = False,
    check_sampler: StrictBool = False,
    check_project: StrictBool = False,
    timeout_seconds: Timeout = 1800,
) -> dict[str, Any]:
    """Validate or run a batch job file and return its manifest or plan."""
    if (check_sampler or check_project) and not dry_run:
        raise ValueError("check_sampler and check_project are only valid with dry_run=true")
    root = operations.workspace()
    if dry_run:
        if check_project:
            return {**await batch.validate_with_project_async(job, root), "dry_run": True}
        if check_sampler:
            return {**await batch.validate_with_sampler_async(job, root), "dry_run": True}
        return {**batch.validate_job_file(job, root), "dry_run": True}
    return await batch.run_job_file_async(job, root, timeout_seconds=timeout_seconds)


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
