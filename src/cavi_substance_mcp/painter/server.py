"""Typed stdio MCP surface for Substance Painter."""

import threading
import time
from typing import Annotated, Literal, TypeAliasType

import anyio.to_thread
from cavi_mcp_core.server import DESTRUCTIVE, READ_ONLY, WRITES, Server, need
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from ..core.actions import checked
from ..core.contracts import (
    Ownership,
    PainterBakeResult,
    PainterBrushResult,
    PainterInspection,
    PainterLayerResult,
    PainterProject,
    PainterStatus,
    TextureExport,
)
from .client import PainterClient

mcp = Server(
    "cavi-substance-mcp",
    package="cavi-substance-mcp",
    instructions=(
        "Requires licensed Painter with remote scripting; `cavi-substance-mcp runtime start` "
        "launches it on macOS. Call painter_status first. Claim session_control and pass "
        "lease_id on edits. After open/create/reload_mesh, use project wait_ready. Bakes are "
        "async: poll bake status. Exports default to dry runs into a new directory. Never "
        "retry uncertain mutations automatically."
    ),
)
LeaseId = Annotated[
    str | None,
    Field(max_length=128, pattern=r"^[A-Za-z0-9_-]+$", description="Lease."),
]
WorkspaceFile = Annotated[str | None, Field(description="Workspace file.")]
NewDirectory = Annotated[str, Field(description="New directory.")]
UnitValue = TypeAliasType(
    "UnitValue", Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
)
PaintValue = TypeAliasType(
    "PaintValue", Annotated[float, Field(strict=True, allow_inf_nan=False)]
)
Finite = TypeAliasType("Finite", Annotated[float, Field(strict=True, allow_inf_nan=False)])
TextureSetName = Annotated[str, Field(min_length=1, max_length=128)]


async def painter(operation, params=None, *, lease_id=None):
    """Run one blocking Painter call in a worker thread; the client serializes the calls.

    A cancelled request stops waiting at once. A call already sent still finishes inside
    Painter and keeps the client's lock until it does; a call still queued is never sent.
    """
    cancelled = threading.Event()

    def run():
        return PainterClient().call(operation, params, lease_id=lease_id, cancelled=cancelled)

    try:
        return await anyio.to_thread.run_sync(run, abandon_on_cancel=True)
    except BaseException:
        cancelled.set()
        raise


async def wait_until_ready(timeout_seconds, interval=0.5):
    client = PainterClient()
    start = time.monotonic()
    deadline = start + timeout_seconds
    while True:
        result = await anyio.to_thread.run_sync(
            client.readiness, deadline, start, abandon_on_cancel=True
        )
        if result["ready"] or time.monotonic() + interval >= deadline:
            return result
        await anyio.sleep(interval)


@mcp.tool(annotations=READ_ONLY)
async def painter_status() -> PainterStatus:
    """Connectivity, project, and edition state."""
    try:
        return await painter("status")
    except (ConnectionError, TimeoutError) as exc:
        return {"connected": False, "reason": str(exc)}


@mcp.tool(annotations=WRITES)
async def session_control(
    action: Annotated[
        Literal["inspect", "claim", "renew", "release", "break"],
        Field(description="Lease operation."),
    ],
    owner: Annotated[str | None, Field(max_length=128, description="claim: editor name.")] = None,
    expected_project: Annotated[str | None, Field(description="Project id.")] = None,
    lease_id: LeaseId = None,
    ttl_seconds: Annotated[
        StrictInt, Field(ge=30, le=3600, description="Lease seconds.")
    ] = 600,
) -> Ownership:
    """Claim, renew, release, or break a lease."""
    return await painter("session_control", {
        "action": action, "owner": owner, "expected_project": expected_project,
        "lease_id": lease_id, "ttl_seconds": ttl_seconds,
    })


@mcp.tool(annotations=DESTRUCTIVE)
async def project(
    action: Annotated[
        Literal["open", "create", "close", "save", "save_copy", "save_as", "save_template",
                "export_mesh", "update_resources", "display", "wait_ready", "reload_mesh"],
        Field(description="Project operation."),
    ],
    path: Annotated[WorkspaceFile, Field(description="Project file.")] = None,
    mesh_path: Annotated[WorkspaceFile, Field(description="Mesh path.")] = None,
    normal_map_format: Annotated[
        Literal["OpenGL", "DirectX"], Field(description="Normal orientation.")
    ] = "OpenGL",
    default_texture_resolution: Annotated[
        Literal[256, 512, 1024, 2048, 4096, 8192] | None,
        Field(description="Texture size."),
    ] = None,
    source_precision: Annotated[
        Literal["16", "template"], Field(description="Source precision.")
    ] = "16",
    udim: Annotated[StrictBool, Field(description="UV tiles.")] = False,
    template: Annotated[WorkspaceFile, Field(description="Template.")] = None,
    tangent_space_per_fragment: Annotated[
        StrictBool, Field(description="Per-fragment tangents.")
    ] = False,
    preserve_strokes: Annotated[
        StrictBool, Field(description="Reproject strokes.")
    ] = True,
    expected_file: Annotated[
        str | None, Field(description="close: status file.")
    ] = None,
    discard_unsaved: Annotated[
        StrictBool, Field(description="Discard unsaved.")
    ] = False,
    directory: Annotated[
        str | None, Field(description="Output directory.")
    ] = None,
    mesh_format: Annotated[
        Literal["fbx", "obj", "gltf", "usd", "dae", "ply"], Field(description="export_mesh.")
    ] = "fbx",
    display: Annotated[
        dict[str, str] | None,
        Field(description="Environment, LUT, or tone mapping."),
    ] = None,
    timeout_seconds: Annotated[
        StrictInt, Field(ge=1, le=300, description="Timeout (s).")
    ] = 60,
    lease_id: LeaseId = None,
) -> PainterProject:
    """Open, create, save, reload, or display it."""
    if action == "wait_ready":
        return await wait_until_ready(timeout_seconds)
    if action == "open":
        return await painter("open_project", {"path": need(path, "path", action)},
                             lease_id=lease_id)
    if action == "create":
        created = await painter("create_project", {
            "path": need(mesh_path, "mesh_path", action),
            "normal_map_format": normal_map_format,
            "default_texture_resolution": default_texture_resolution,
            "udim": udim, "template": template,
            "tangent_space_per_fragment": tangent_space_per_fragment,
        }, lease_id=lease_id)
        if source_precision == '16':
            ready = await wait_until_ready(timeout_seconds)
            if not ready['ready']:
                raise RuntimeError('Project creation is still pending; source precision has not '
                                   'been configured. Inspect before continuing.')
            created['source_channels'] = await painter('initialize_channels', lease_id=lease_id)
        return created
    if action == "reload_mesh":
        return await painter("reload_mesh", {
            "path": need(mesh_path, "mesh_path", action), "preserve_strokes": preserve_strokes,
        }, lease_id=lease_id)
    if action == "close":
        return await painter("close_project", {
            "expected_file": expected_file, "discard_unsaved": discard_unsaved,
        }, lease_id=lease_id)
    if action in ("save", "update_resources"):
        return await painter(action, lease_id=lease_id)
    if action == "display":
        return await painter("display", {"display": display}, lease_id=lease_id)
    params = {"directory": need(directory, "directory", action)}
    if action == "export_mesh":
        params["format"] = mesh_format
    return await painter(action, params, lease_id=lease_id)


@mcp.tool(annotations=READ_ONLY)
async def inspect(
    action: Annotated[
        Literal["texture_sets", "layer_stack", "export_presets", "shelf", "bake_settings"],
        Field(description="Inspection."),
    ],
    texture_set: Annotated[
        TextureSetName | None, Field(description="Set name filter.")
    ] = None,
    query: Annotated[str, Field(max_length=128, description="shelf search text.")] = "",
    usage: Annotated[
        Literal[
            "smart_material",
            "smart_mask",
            "material",
            "generator",
            "texture",
            "environment",
            "brush",
        ]
        | None,
        Field(description="Shelf category."),
    ] = None,
    limit: Annotated[StrictInt, Field(ge=1, le=100, description="Result limit.")] = 50,
) -> PainterInspection:
    """Texture sets, layers, presets and shelf."""
    if action == "layer_stack":
        return await painter(
            "layer_stack", {"texture_set": need(texture_set, "texture_set", action)}
        )
    if action == "shelf":
        return await painter("shelf", {"query": query, "usage": usage, "limit": limit})
    if action == "bake_settings":
        return await painter("bake_settings", {"texture_set": texture_set})
    return await painter(action)


Rgb = TypeAliasType("Rgb", tuple[UnitValue, UnitValue, UnitValue])
Uid = Annotated[StrictInt | None, Field(ge=0)]
LAYER_ACTIONS = {"fill": "create_fill_layer", "texture_fill": "create_texture_fill"}
_PLACE = {"parent_uid", "above_uid"}
_FILL = {*_PLACE, "roughness", "metallic", "values"}
_SBSAR = {"source_manifest", "expected_sha256"}
# action -> (required, optional) arguments besides action, texture_set and lease_id
LAYER_EDIT_PARAMS = {
    "fill": ({"name", "base_color"}, _FILL),
    "texture_fill": ({"name", "base_color_texture"}, _FILL),
    "paint": ({"name"}, _PLACE),
    "group": ({"name"}, _PLACE),
    "smart_material": ({"name", "resource"}, _PLACE),
    "sbsar_fill": ({"name", "sbsar"}, {*_PLACE, *_SBSAR, "parameters"}),
    "sbsar_update": ({"sbsar", "resource"}, _SBSAR),
    "fill_mask": ({"uid", "mask"}, set()),
    "smart_mask": ({"uid", "resource"}, {"mask"}),
    "generator": ({"uid", "resource"}, {"mask", "parameters"}),
    "set_properties": ({"uid"}, {"name", "visible", "blend_mode", "opacity", "channel"}),
    "delete": ({"uid"}, {"confirm_name"}),
    "levels": ({"uid"}, {"mask", "channel", "parameters"}),
    "filter": ({"uid", "resource"}, {"mask", "parameters"}),
    "anchor_point": ({"uid", "name"}, {"mask"}),
    "color_selection": ({"uid", "resource"}, {"mask", "parameters"}),
    "compare_mask": ({"uid"}, {"mask", "channel", "parameters"}),
    "projection": ({"uid", "projection"}, set()),
}
LAYER_EDIT_DEFAULTS = {"roughness": 0.5, "metallic": 0.0}


def _drop_schema_titles(schema):
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)


class UVTransformation(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_drop_schema_titles)
    scale_mode: Literal["Factors", "CustomPhysicalSize"] | None = None
    scale: Annotated[list[Finite], Field(min_length=2, max_length=2)] | None = None
    rotation: Finite | None = None
    offset: Annotated[list[Finite], Field(min_length=2, max_length=2)] | None = None

    @field_validator("scale_mode", mode="before")
    @classmethod
    def refuse_material_physical_size(cls, value):
        if value == "MaterialPhysicalSize":
            raise ValueError(
                "MaterialPhysicalSize depends on resource physical-size metadata "
                "this server does not read")
        return value


class Projection3D(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_drop_schema_titles)
    offset: Annotated[list[Finite], Field(min_length=3, max_length=3)] | None = None
    rotation: Annotated[list[Finite], Field(min_length=3, max_length=3)] | None = None
    scale: Annotated[list[Finite], Field(min_length=2, max_length=3)] | None = None


class FillProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_drop_schema_titles)
    mode: Literal["UV", "Triplanar", "Planar"]
    filtering_mode: Literal["BilinearHQ", "BilinearSharp", "Nearest"] | None = None
    uv_wrapping_mode: Literal[
        "RepeatNone", "RepeatHorizontally", "RepeatVertically", "Repeat"
    ] | None = None
    shape_crop_mode: Literal["CroppedToShape", "ExtendsOutsideShape"] | None = None
    hardness: Finite | None = None
    uv_transformation: UVTransformation | None = None
    projection_3d: Projection3D | None = None


@mcp.tool(annotations=DESTRUCTIVE)
async def layer_edit(
    action: Annotated[
        Literal[
            "fill",
            "texture_fill",
            "paint",
            "group",
            "fill_mask",
            "smart_material",
            "smart_mask",
            "generator",
            "sbsar_fill",
            "sbsar_update",
            "set_properties",
            "delete",
            "levels",
            "filter",
            "anchor_point",
            "color_selection",
            "compare_mask",
            "projection",
        ],
        Field(description="Action."),
    ],
    texture_set: Annotated[TextureSetName, Field(description="Texture set.")],
    name: Annotated[
        str | None, Field(min_length=1, max_length=63, description="Layer name.")
    ] = None,
    uid: Annotated[Uid, Field(description="Node ID.")] = None,
    parent_uid: Annotated[Uid, Field(description="In group.")] = None,
    above_uid: Annotated[Uid, Field(description="Above layer.")] = None,
    base_color: Annotated[Rgb | None, Field(description="fill: sRGB 0-1.")] = None,
    base_color_texture: Annotated[
        WorkspaceFile, Field(description="texture_fill image.")
    ] = None,
    roughness: Annotated[UnitValue, Field(description="fill: roughness.")] = 0.5,
    metallic: Annotated[UnitValue, Field(description="fill: metallic.")] = 0.0,
    values: Annotated[
        dict[str, UnitValue | Rgb] | None,
        Field(description="Extra channels."),
    ] = None,
    mask: Annotated[
        Literal["black", "white"] | None, Field(description="Mask color.")
    ] = None,
    resource: Annotated[str | None, Field(max_length=256, description="Shelf/URL.")] = None,
    sbsar: WorkspaceFile = None,
    source_manifest: WorkspaceFile = None,
    expected_sha256: Annotated[
        str | None, Field(description="SHA-256.")
    ] = None,
    parameters: Annotated[
        dict[str, float | int | str | bool | list[float]] | None,
        Field(description="Effect settings."),
    ] = None,
    blend_mode: Annotated[
        str | None, Field(max_length=32, description="Blend mode.")
    ] = None,
    opacity: Annotated[
        UnitValue | None, Field(description="Opacity.")
    ] = None,
    channel: Annotated[str | None, Field(max_length=16, description="Default: all.")] = None,
    visible: Annotated[
        StrictBool | None, Field(description="Visibility.")
    ] = None,
    confirm_name: Annotated[
        str | None, Field(max_length=63, description="Current name.")
    ] = None,
    projection: Annotated[
        FillProjection | None,
        Field(description="UV, Triplanar or Planar placement."),
    ] = None,
    lease_id: LeaseId = None,
) -> PainterLayerResult:
    """Edit layers. Projection places a fill layer."""
    arguments = {key: value for key, value in locals().items()
                 if key not in ("action", "texture_set", "lease_id")}
    accepted = checked(action, arguments, LAYER_EDIT_PARAMS, LAYER_EDIT_DEFAULTS)
    if isinstance(projection, FillProjection):
        accepted["projection"] = projection.model_dump(exclude_none=True)
    if action in LAYER_ACTIONS:
        return await painter(LAYER_ACTIONS[action], {"texture_set": texture_set, **accepted},
                             lease_id=lease_id)
    return await painter("layer_edit", {"action": action, "texture_set": texture_set, **accepted},
                         lease_id=lease_id)



_BRUSH = {"settings", "preset", "channel_values", "enabled_channels"}
BRUSH_PARAMS = {
    "settings": (set(), _BRUSH),
    "prepare": ({"texture_set", "uid", "directory"}, {*_BRUSH, "target", "mode"}),
    "stroke": ({"context_token", "points"}, {"uv"}),
}
BRUSH_DEFAULTS = {"target": "content", "mode": "paint", "uv": False}


class BrushSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    size: Annotated[float, Field(strict=True, gt=0, le=1000, allow_inf_nan=False)] | None = None
    flow: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    opacity: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    spacing: Annotated[StrictInt, Field(ge=1, le=1000)] | None = None


@mcp.tool(annotations=DESTRUCTIVE)
async def texture_set_settings(
    texture_set: Annotated[TextureSetName, Field(description="Target texture set.")],
    channel_formats: Annotated[
        dict[
            str, Literal["sRGB8", "L8", "RGB8", "L16", "RGB16", "L16F", "RGB16F", "L32F", "RGB32F"]
        ]
        | None,
        Field(max_length=64, description="Channel format."),
    ] = None,
    resolution: Annotated[
        Literal[256, 512, 1024, 2048, 4096, 8192] | None, Field(description="Texture size.")
    ] = None,
    mesh_maps: Annotated[
        dict[str, str] | None, Field(max_length=10, description="Mesh map file.")
    ] = None,
    lease_id: LeaseId = None,
) -> PainterInspection:
    """Channel formats, resolution, or mesh maps."""
    if not channel_formats and resolution is None and not mesh_maps:
        raise ValueError('Provide channel_formats, resolution or mesh_maps')
    return await painter('texture_set_settings', {'texture_set': texture_set,
                         'channel_formats': channel_formats, 'resolution': resolution,
                         'mesh_maps': mesh_maps}, lease_id=lease_id)


@mcp.tool(annotations=DESTRUCTIVE)
async def brush(
    action: Annotated[
        Literal["prepare", "stroke", "settings"],
        Field(description="Brush action."),
    ],
    texture_set: Annotated[
        TextureSetName | None, Field(description="Texture set.")
    ] = None,
    uid: Annotated[Uid, Field(description="prepare: layer UID.")] = None,
    directory: Annotated[str | None, Field(description="Stroke directory.")] = None,
    context_token: Annotated[
        str | None, Field(max_length=128, description="Stroke token.")
    ] = None,
    points: Annotated[
        list[tuple[UnitValue, UnitValue, UnitValue]] | None,
        Field(min_length=2, max_length=128, description="Stroke points."),
    ] = None,
    uv: Annotated[StrictBool, Field(description="UV points.")] = False,
    settings: Annotated[
        BrushSettings | None, Field(description="Native brush units.")
    ] = None,
    preset: Annotated[
        str | None, Field(max_length=512, description="Shelf brush URL.")
    ] = None,
    channel_values: Annotated[
        dict[str, PaintValue | tuple[PaintValue, PaintValue, PaintValue]] | None,
        Field(max_length=64, description="Channel value."),
    ] = None,
    enabled_channels: Annotated[
        dict[str, StrictBool] | None,
        Field(max_length=64, description="Channel toggles."),
    ] = None,
    target: Annotated[
        Literal["content", "mask"], Field(description="Content or mask.")
    ] = "content",
    mode: Annotated[
        Literal["paint", "erase"], Field(description="Paint or erase.")
    ] = "paint",
    lease_id: LeaseId = None,
) -> PainterBrushResult:
    """Prepare a viewport, then paint one stroke."""
    arguments = {key: value for key, value in locals().items()
                 if key not in ("action", "lease_id")}
    accepted = checked(action, arguments, BRUSH_PARAMS, BRUSH_DEFAULTS)
    if "settings" in accepted:
        accepted["settings"] = settings.model_dump(exclude_none=True) if settings else {}
    return await painter("brush", {"action": action, **accepted}, lease_id=lease_id)


class OutputMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=128, description="Export name.")]
    format: Literal["png", "tga", "tiff", "exr", "jpeg"] | None = None
    bit_depth: Literal[8, 16, "16f", "32f"] | None = None
    channels: Annotated[
        dict[Literal["R", "G", "B", "A", "RGB", "L"], str | float],
        Field(description="Map source."),
    ]


@mcp.tool(annotations=WRITES)
async def export_textures(
    directory: NewDirectory,
    preset: Annotated[str | None, Field(description="Preset URL.")] = None,
    source_manifest: Annotated[WorkspaceFile, Field(description="Source receipt.")] = None,
    source_file: Annotated[WorkspaceFile, Field(description="Source .sbsar.")] = None,
    maps: Annotated[
        list[OutputMap] | None,
        Field(max_length=16, description="Channel pack."),
    ] = None,
    resolution: Annotated[
        Literal[128, 256, 512, 1024, 2048, 4096, 8192] | None,
        Field(description="Texture size."),
    ] = None,
    file_format: Annotated[
        Literal["png", "tga", "tiff", "exr", "jpeg"] | None,
        Field(description="Image format."),
    ] = None,
    bit_depth: Annotated[
        Literal[8, 16, "16f", "32f"] | None, Field(description="Channel precision.")
    ] = None,
    texture_sets: Annotated[
        list[TextureSetName] | None,
        Field(max_length=64, description="Texture sets."),
    ] = None,
    udim_tiles: Annotated[
        list[tuple[StrictInt, StrictInt]] | None, Field(max_length=100, description="[u, v] pairs.")
    ] = None,
    padding: Annotated[
        Literal["infinite", "passthrough", "transparent", "color", "diffusion"],
        Field(description="Padding mode."),
    ] = "infinite",
    dilation_distance: Annotated[
        StrictInt | None, Field(ge=0, le=256, description="Padding pixels.")
    ] = None,
    dry_run: Annotated[StrictBool, Field(description="False writes.")] = True,
    lease_id: LeaseId = None,
) -> TextureExport:
    """Preview or export textures to a directory."""
    if (source_manifest is None) != (source_file is None):
        raise ValueError("source_manifest and source_file must be provided together")
    return await painter("export_textures", {
        "directory": directory, "preset": preset,
        "source_manifest": source_manifest, "source_file": source_file,
        "maps": None if maps is None else [m.model_dump(exclude_none=True) for m in maps],
        "resolution": resolution, "file_format": file_format, "bit_depth": bit_depth,
        "texture_sets": texture_sets,
        "udim_tiles": None if udim_tiles is None else [list(tile) for tile in udim_tiles],
        "padding": padding, "dilation_distance": dilation_distance, "dry_run": dry_run,
    }, lease_id=lease_id)


@mcp.tool(annotations=WRITES)
async def bake(
    action: Annotated[
        Literal["start", "status", "cancel"], Field(description="Bake action.")
    ],
    texture_sets: Annotated[
        list[TextureSetName] | None,
        Field(max_length=64, description="Enabled sets."),
    ] = None,
    maps: Annotated[
        list[
            Literal[
                "normal",
                "world_space_normal",
                "id",
                "ao",
                "curvature",
                "position",
                "thickness",
                "height",
                "opacity",
                "bent_normals",
            ]
        ]
        | None,
        Field(description="Mesh maps."),
    ] = None,
    high_poly_meshes: Annotated[
        list[str] | None, Field(max_length=16, description="Workspace meshes.")
    ] = None,
    resolution: Annotated[
        Literal[128, 256, 512, 1024, 2048, 4096, 8192] | None,
        Field(description="Output size."),
    ] = None,
    antialiasing: Annotated[
        Literal["none", "2x2", "4x4", "8x8"] | None,
        Field(description="Supersampling."),
    ] = None,
    max_frontal: Annotated[
        float | None,
        Field(ge=0, le=100, allow_inf_nan=False, description="Front distance."),
    ] = None,
    max_rear: Annotated[
        float | None,
        Field(ge=0, le=100, allow_inf_nan=False, description="Rear distance."),
    ] = None,
    use_cage: Annotated[StrictBool | None, Field(description="Use cage_mesh.")] = None,
    cage_mesh: WorkspaceFile = None,
    match: Annotated[
        Literal["always", "by_mesh_name"] | None,
        Field(description="High/low matching."),
    ] = None,
    low_poly_suffix: Annotated[
        str | None, Field(max_length=32, description="Low mesh suffix.")
    ] = None,
    high_poly_suffix: Annotated[
        str | None, Field(max_length=32, description="High mesh suffix.")
    ] = None,
    lease_id: LeaseId = None,
) -> PainterBakeResult:
    """Start a bake, then poll until it finishes."""
    if action == "status":
        return await painter("bake_status")
    if action == "cancel":
        return await painter("bake_cancel", lease_id=lease_id)
    return await painter("bake", {
        "texture_sets": texture_sets, "maps": maps, "high_poly_meshes": high_poly_meshes,
        "resolution": resolution, "antialiasing": antialiasing, "max_frontal": max_frontal,
        "max_rear": max_rear, "use_cage": use_cage, "cage_mesh": cage_mesh, "match": match,
        "low_poly_suffix": low_poly_suffix, "high_poly_suffix": high_poly_suffix,
    }, lease_id=lease_id)


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
