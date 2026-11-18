"""Typed stdio MCP surface for Substance 3D Sampler startup scripts."""

import asyncio
from typing import Annotated, Literal

from cavi_mcp_core.server import DESTRUCTIVE, READ_ONLY, WRITES, Server
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

from ..core import workspace
from ..core.actions import action_schema, checked
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
            tools[index] = tool.model_copy(update={"input_schema": {
                **tool.input_schema, **action_schema(INSPECT_PARAMS)}})
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
ProjectPath = Annotated[str, Field(description='.ssa path')]
NewDirectory = Annotated[str, Field(description='New path')]
AssetName = Annotated[str, Field(min_length=1, max_length=128)]
PlainName = Annotated[str, Field(min_length=1, max_length=96)]
Query = Annotated[str, Field(max_length=128)]
ImportOption = Literal[IMPORT_OPTIONS]
BlendMode = Literal[BLEND_MODES]
ExportFormat = Literal[EXPORT_FORMATS]
MaterialType = Literal[MATERIAL_TYPES]
TargetName = Literal[tuple(TARGETS)]
ParameterValue = bool | int | float | str | list[bool | int | float | str]
JobFile = Annotated[str, Field(description='Job JSON')]
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
# action -> (required, optional) arguments besides action and timeout_seconds
INSPECT_PARAMS = {
    "catalog": (set(), {"query"}),
    "project": ({"project"}, set()),
    "asset": ({"project", "asset"}, set()),
}
PROJECT_PARAMS = {
    "create": ({"name"}, set()),
    "save_as": ({"project"}, {"name"}),
}
_PLACE = {"position", "insert_above"}
ASSET_PARAMS = {
    "create": ({"name"}, {"model", "preset", "empty"}),
    "delete": ({"asset", "confirm"}, set()),
    "import_resource": ({"path"}, {"resources", "source_manifest", "expected_sha256"}),
    "set_metadata": ({"asset", "metadata"}, set()),
    "activate_channels": ({"asset", "channels"}, set()),
    "deactivate_channels": ({"asset", "channels"}, set()),
    "import_images": ({"asset", "images"}, {*_PLACE, "import_option"}),
    "import_textures": ({"asset", "images"}, _PLACE),
    "insert_filter": ({"asset", "filter"}, _PLACE),
    "insert_material": ({"asset", "material"}, {*_PLACE, "blend_mode"}),
}
ASSET_DEFAULTS = {"model": "asm", "empty": False, "position": 0,
                  "import_option": "use_as_bitmap", "blend_mode": "blend_normal",
                  "confirm": False}
LAYER_PARAMS = {
    "set_parameters": ({"parameters"}, {"position"}),
    "apply_preset": ({"preset"}, {"position"}),
    "set_channels": (set(), {"position", "applies_to", "blend_applies_to"}),
    "set_blend_parameters": ({"parameters"}, {"position"}),
    "remove": ({"confirm"}, {"position"}),
}
LAYER_OPERATIONS = {"remove": "remove_layer"}


@mcp.tool(annotations=READ_ONLY)
async def sampler_status() -> SamplerStatus:
    """Sampler version, paths and running state"""
    return await asyncio.to_thread(launch.status)


@mcp.tool(annotations=READ_ONLY)
async def sampler_inspect(
    action: Annotated[
        Literal["catalog", "project", "asset"],
        Field(description='Inspection'),
    ],
    # Omitted, not null, when the action does not use them.
    project: Annotated[str, Field(description='.ssa path')] = None,
    asset: Annotated[AssetName, Field(description='Asset name')] = None,
    query: Annotated[Query, Field(description='Search')] = "",
    timeout_seconds: Annotated[Timeout, Field(description='Timeout')] = 600,
) -> SamplerInspection:
    """Inspect projects, assets and resource lists."""
    accepted = checked(action, {"project": project, "asset": asset, "query": query},
                       INSPECT_PARAMS, {"query": ""})
    arguments = {key: value for key, value in accepted.items() if value}
    operation = {"project": "inspect_project", "asset": "inspect_asset",
                 "catalog": "catalog"}[action]
    return await launch.invoke(operation, arguments, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_project(
    action: Annotated[Literal["create", "save_as"], Field(description='Operation')],
    directory: NewDirectory,
    name: Annotated[
        PlainName | None,
        Field(description='New name'),
    ] = None,
    project: Annotated[
        str | None, Field(description='.ssa path')
    ] = None,
    timeout_seconds: Annotated[Timeout, Field(description='Timeout')] = 600,
) -> SamplerProjectResult:
    """Create/copy a project in a new directory."""
    accepted = checked(action, {"name": name, "project": project}, PROJECT_PARAMS)
    operation = "create_project" if action == "create" else "save_as"
    return await launch.invoke(operation, {
        "directory": directory,
        **{key: value for key, value in accepted.items() if value is not None},
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
        Field(description='Operation'),
    ],
    project: ProjectPath,
    asset: Annotated[
        AssetName | None,
        Field(description='Asset name'),
    ] = None,
    name: Annotated[PlainName | None, Field(description='New name')] = None,
    model: Annotated[
        Literal["asm", "openpbr"], Field(description='Material model')
    ] = "asm",
    preset: Annotated[
        str | None, Field(max_length=128, description='Asset preset')
    ] = None,
    empty: Annotated[
        StrictBool, Field(description='Empty stack')
    ] = False,
    metadata: Annotated[
        AssetMetadata | None, Field(description='Nonempty metadata')
    ] = None,
    channels: Annotated[
        list[ChannelName] | None,
        Field(
            min_length=1, max_length=64, description='Channel names'
        ),
    ] = None,
    path: Annotated[
        str | None, Field(description='Source .sbs/.sbsar')
    ] = None,
    source_manifest: Annotated[
        str | None, Field(description='Receipt')
    ] = None,
    expected_sha256: Annotated[
        str | None, Field(description='SHA-256')
    ] = None,
    resources: Annotated[
        list[ResourceNameArg] | None,
        Field(
            min_length=1, max_length=64, description='Resource names'
        ),
    ] = None,
    images: Annotated[
        list[str] | dict[str, str] | None,
        Field(description='Paths or usage:path'),
    ] = None,
    position: Annotated[
        StrictInt, Field(ge=0, le=100000, description='Insertion index')
    ] = 0,
    import_option: Annotated[
        ImportOption, Field(description='Image conversion')
    ] = "use_as_bitmap",
    insert_above: Annotated[
        StrictBool | None, Field(description='Insert above')
    ] = None,
    filter: Annotated[
        PlainName | None, Field(description='Filter name')
    ] = None,
    material: Annotated[
        PlainName | None, Field(description='Material name')
    ] = None,
    blend_mode: Annotated[
        BlendMode, Field(description='Material blend')
    ] = "blend_normal",
    confirm: Annotated[StrictBool, Field(description='delete: true')] = False,
    timeout_seconds: Annotated[Timeout, Field(description='Timeout')] = 600,
) -> SamplerAssetResult:
    """Edit a copy; save before replacing the project."""
    given = {key: value for key, value in locals().items()
             if key not in ("action", "project", "timeout_seconds")}
    accepted = checked(action, given, ASSET_PARAMS, ASSET_DEFAULTS)
    if action == "delete" and not confirm:
        raise ValueError("confirm must be true for action=delete")
    if action == "set_metadata" and not metadata.model_dump(exclude_none=True):
        raise ValueError("metadata must contain at least one field")
    if action == "import_images" and not isinstance(images, list):
        raise ValueError("images must be a list of workspace images")
    if action == "import_textures" and not isinstance(images, dict):
        raise ValueError("images must map usages to workspace images")
    arguments = {"project": project, **{key: value for key, value in accepted.items()
                                        if value is not None and key not in ("confirm",
                                                                             "metadata")}}
    if action == "set_metadata":
        arguments.update(metadata.model_dump(exclude_none=True))
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
        Field(description='Operation'),
    ],
    project: ProjectPath,
    asset: Annotated[AssetName, Field(description='Asset name')],
    layer: Annotated[str, Field(min_length=1, max_length=128, description='Layer name')],
    position: Annotated[
        StrictInt | None, Field(ge=0, le=100000, description='Layer index')
    ] = None,
    parameters: Annotated[
        dict[str, ParameterValue] | None,
        Field(max_length=64, description='Parameter values'),
    ] = None,
    preset: Annotated[
        str | None, Field(max_length=128, description='Preset name')
    ] = None,
    applies_to: Annotated[
        list[ChannelName] | None,
        Field(min_length=1, max_length=64, description='Channels'),
    ] = None,
    blend_applies_to: Annotated[
        list[ChannelName] | None,
        Field(min_length=1, max_length=64, description='Blend channels'),
    ] = None,
    confirm: Annotated[StrictBool, Field(description='remove: true')] = False,
    timeout_seconds: Annotated[Timeout, Field(description='Timeout')] = 600,
) -> SamplerLayerResult:
    """Edit layer channels, parameters or presets."""
    given = {key: value for key, value in locals().items()
             if key not in ("action", "project", "asset", "layer", "timeout_seconds")}
    accepted = checked(action, given, LAYER_PARAMS, {"confirm": False})
    if "parameters" in accepted and not parameters:
        raise ValueError("parameters must be a non-empty object")
    if action == "set_channels" and applies_to is None and blend_applies_to is None:
        raise ValueError("set_channels needs applies_to or blend_applies_to")
    if action == "remove" and not confirm:
        raise ValueError("confirm must be true for action=remove")
    arguments = {"project": project, "asset": asset, "layer": layer,
                 **{key: value for key, value in accepted.items()
                    if value is not None and key != "confirm"}}
    return await launch.invoke(LAYER_OPERATIONS.get(action, action), arguments, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def sampler_export(
    project: ProjectPath,
    directory: NewDirectory,
    asset: Annotated[
        AssetName | None, Field(description='Selected/first if omitted')
    ] = None,
    target: Annotated[
        TargetName | None, Field(description='Export target')
    ] = None,
    format: Annotated[
        ExportFormat | None,
        Field(description='sbsar; png with target'),
    ] = None,
    preset: Annotated[str | None, Field(max_length=128, description='Preset name')] = None,
    channels: Annotated[
        list[str] | None, Field(max_length=64, description='Export channels')
    ] = None,
    resolution: Annotated[
        StrictInt | None, Field(ge=64, le=8192, description='Map size')
    ] = None,
    material_type: Annotated[
        MaterialType, Field(description='Variant')
    ] = "standard",
    subfolder: Annotated[StrictBool, Field(description='Asset folder')] = False,
    wait_seconds: Annotated[
        StrictInt, Field(ge=1, le=3600, description='Output wait')
    ] = 600,
    dry_run: Annotated[StrictBool, Field(description='False exports')] = True,
    timeout_seconds: Annotated[
        Timeout, Field(description='Timeout')
    ] = 600,
) -> SamplerExportResult:
    """Preview/export maps into a new directory."""
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
    dry_run: Annotated[StrictBool, Field(description='False runs')] = True,
    check_sampler: Annotated[
        StrictBool, Field(description='Check app catalog')
    ] = False,
    check_project: Annotated[
        StrictBool, Field(description='Inspect project')
    ] = False,
    check_build: Annotated[
        StrictBool, Field(description='Check temp build')
    ] = False,
    timeout_seconds: Annotated[Timeout, Field(description='Timeout')] = 1800,
) -> SamplerBuildResult:
    """Validate a job; dry_run=false executes it."""
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
        Field(min_length=1, max_length=32, description='Usage:image path'),
    ],
    pack: Annotated[PackSpec, Field(description='Channel packing')],
) -> SamplerPackResult:
    """Pack texture channels in a new directory."""
    return await packer.pack(directory, sources, pack)


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
