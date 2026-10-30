"""Typed stdio MCP surface for Substance Painter."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .client import PainterClient

mcp = FastMCP(
    "cavi-substance-mcp",
    instructions=(
        "Requires licensed Painter with remote scripting; `cavi-substance-mcp runtime start` "
        "launches it on macOS. Call painter_status first. Claim session_control and pass "
        "lease_id on edits. After open/create/reload_mesh, use project wait_ready. Bakes are "
        "async: poll bake status. Exports default to dry runs into a new directory. Never "
        "retry uncertain mutations automatically."
    ),
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
CLOSING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
LeaseId = Annotated[str | None, Field(max_length=128, description="From session_control claim.")]
WorkspaceFile = Annotated[str | None, Field(description="Existing file relative to the workspace.")]
NewDirectory = Annotated[str, Field(description="New directory relative to the workspace.")]
UnitValue = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
TextureSetName = Annotated[str, Field(min_length=1, max_length=128)]


def need(value, name, action):
    if value is None:
        raise ValueError(f"{name} is required for action={action}")
    return value


@mcp.tool(annotations=READ_ONLY)
def painter_status() -> dict[str, Any]:
    """Connectivity, project, busy and edition state; never changes anything."""
    try:
        return PainterClient().call("status")
    except (ConnectionError, TimeoutError) as exc:
        return {"connected": False, "reason": str(exc)}


@mcp.tool(annotations=MUTATING)
def session_control(
    action: Literal["inspect", "claim", "renew", "release", "break"],
    owner: Annotated[str | None, Field(max_length=128)] = None,
    expected_project: Annotated[str | None, Field(description="project_id from inspect.")] = None,
    lease_id: LeaseId = None,
    ttl_seconds: Annotated[StrictInt, Field(ge=30, le=3600)] = 600,
) -> dict[str, Any]:
    """Edit lease. claim needs owner+expected_project; renew/release need lease_id.
    break needs expected_project and a lease idle >= 300 s. Nothing is saved or closed."""
    return PainterClient().call("session_control", {
        "action": action, "owner": owner, "expected_project": expected_project,
        "lease_id": lease_id, "ttl_seconds": ttl_seconds,
    })


@mcp.tool(annotations=CLOSING)
def project(
    action: Literal["open", "create", "close", "save", "save_copy", "wait_ready", "reload_mesh"],
    path: Annotated[WorkspaceFile, Field(description="open: workspace .spp.")] = None,
    mesh_path: Annotated[WorkspaceFile, Field(description="create/reload_mesh: mesh.")] = None,
    normal_map_format: Literal["OpenGL", "DirectX"] = "OpenGL",
    default_texture_resolution: Literal[256, 512, 1024, 2048, 4096, 8192] | None = None,
    udim: Annotated[StrictBool, Field(description="create: UV tile workflow.")] = False,
    template: Annotated[WorkspaceFile, Field(description="create: workspace .spt.")] = None,
    tangent_space_per_fragment: StrictBool = False,
    preserve_strokes: StrictBool = True,
    expected_file: Annotated[
        str | None, Field(description="close: file from painter_status; null if unsaved.")
    ] = None,
    discard_unsaved: StrictBool = False,
    directory: Annotated[str | None, Field(description="save_copy: new workspace directory.")] = None,
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 60,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """open/create only with no project open; close refuses unsaved edits unless
    discard_unsaved; save overwrites the workspace file; wait_ready polls until editable.
    reload_mesh is async: wait_ready, then painter_status mesh_reload."""
    client = PainterClient()
    if action == "wait_ready":
        return client.wait_ready(timeout_seconds)
    if action == "open":
        return client.call("open_project", {"path": need(path, "path", action)}, lease_id=lease_id)
    if action == "create":
        return client.call("create_project", {
            "path": need(mesh_path, "mesh_path", action),
            "normal_map_format": normal_map_format,
            "default_texture_resolution": default_texture_resolution,
            "udim": udim, "template": template,
            "tangent_space_per_fragment": tangent_space_per_fragment,
        }, lease_id=lease_id)
    if action == "reload_mesh":
        return client.call("reload_mesh", {
            "path": need(mesh_path, "mesh_path", action), "preserve_strokes": preserve_strokes,
        }, lease_id=lease_id)
    if action == "close":
        return client.call("close_project", {
            "expected_file": expected_file, "discard_unsaved": discard_unsaved,
        }, lease_id=lease_id)
    if action == "save":
        return client.call("save", lease_id=lease_id)
    return client.call(
        "save_copy", {"directory": need(directory, "directory", action)}, lease_id=lease_id
    )


@mcp.tool(annotations=READ_ONLY)
def inspect(
    action: Literal["texture_sets", "layer_stack", "export_presets", "shelf", "bake_settings"],
    texture_set: Annotated[
        TextureSetName | None, Field(description="layer_stack; bake_settings filter.")
    ] = None,
    query: Annotated[str, Field(max_length=128, description="shelf search text.")] = "",
    usage: Literal[
        "smart_material", "smart_mask", "material", "generator", "texture", "environment"
    ] | None = None,
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Texture sets, layer tree (groups, masks, effects, blending), export presets,
    shelf resources, or baking parameters."""
    if action == "layer_stack":
        return PainterClient().call(
            "layer_stack", {"texture_set": need(texture_set, "texture_set", action)}
        )
    if action == "shelf":
        return PainterClient().call("shelf", {"query": query, "usage": usage, "limit": limit})
    if action == "bake_settings":
        return PainterClient().call("bake_settings", {"texture_set": texture_set})
    return PainterClient().call(action)


Rgb = tuple[UnitValue, UnitValue, UnitValue]
Uid = Annotated[StrictInt | None, Field(ge=0)]
LAYER_ACTIONS = {"fill": "create_fill_layer", "texture_fill": "create_texture_fill"}


@mcp.tool(annotations=CLOSING)
def layer_edit(
    action: Literal["fill", "texture_fill", "group", "fill_mask", "smart_material", "smart_mask",
                    "generator", "sbsar_fill", "set_properties", "delete"],
    texture_set: TextureSetName,
    name: Annotated[str | None, Field(min_length=1, max_length=63,
                                      description="New layer name; rename in set_properties.")] = None,
    uid: Annotated[Uid, Field(description="Target node.")] = None,
    parent_uid: Annotated[Uid, Field(description="Insert inside this group.")] = None,
    above_uid: Annotated[Uid, Field(description="Insert above this layer.")] = None,
    base_color: Annotated[Rgb | None, Field(description="fill: sRGB 0-1.")] = None,
    base_color_texture: Annotated[
        WorkspaceFile, Field(description="texture_fill: workspace PNG, embedded as sRGB.")
    ] = None,
    roughness: UnitValue = 0.5,
    metallic: UnitValue = 0.0,
    values: Annotated[
        dict[str, UnitValue | Rgb] | None,
        Field(description="fill extras: normal, height, emissive, opacity, ao, user0-7."),
    ] = None,
    mask: Literal["black", "white"] | None = None,
    resource: Annotated[str | None, Field(max_length=256, description="Shelf name or URL.")] = None,
    sbsar: WorkspaceFile = None,
    parameters: Annotated[
        dict[str, float | int | str | bool | list[float]] | None,
        Field(description="sbsar_fill/generator substance parameters."),
    ] = None,
    blend_mode: Annotated[str | None, Field(max_length=32)] = None,
    opacity: UnitValue | None = None,
    channel: Annotated[str | None, Field(max_length=16, description="Default: all.")] = None,
    visible: StrictBool | None = None,
    confirm_name: Annotated[str | None, Field(max_length=63)] = None,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """Edit a Texture Set layer stack. New layers need a unique name; failures roll back.
    delete needs confirm_name unless this lease created the node."""
    if action in LAYER_ACTIONS:
        if action == "fill":
            if base_color_texture is not None:
                raise ValueError("base_color_texture is only valid for action=texture_fill")
            extra = {"base_color": need(base_color, "base_color", action)}
        else:
            if base_color is not None:
                raise ValueError("base_color is only valid for action=fill")
            extra = {"base_color_texture": need(base_color_texture, "base_color_texture", action)}
        return PainterClient().call(LAYER_ACTIONS[action], {
            "texture_set": texture_set, "name": need(name, "name", action),
            "roughness": roughness, "metallic": metallic, "values": values,
            "parent_uid": parent_uid, "above_uid": above_uid, **extra,
        }, lease_id=lease_id)
    if action in {"group", "smart_material", "sbsar_fill"}:
        need(name, "name", action)
    if action in {"fill_mask", "smart_mask", "generator", "set_properties", "delete"}:
        need(uid, "uid", action)
    if action in {"smart_material", "smart_mask", "generator"}:
        need(resource, "resource", action)
    if action == "sbsar_fill":
        need(sbsar, "sbsar", action)
    return PainterClient().call("layer_edit", {
        "action": action, "texture_set": texture_set, "name": name, "uid": uid,
        "parent_uid": parent_uid, "above_uid": above_uid, "mask": mask, "resource": resource,
        "sbsar": sbsar, "parameters": parameters, "blend_mode": blend_mode, "opacity": opacity,
        "channel": channel, "visible": visible, "confirm_name": confirm_name,
    }, lease_id=lease_id)


class OutputMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=128, description="May use $textureSet.")]
    format: Literal["png", "tga", "tiff", "exr", "jpeg"] | None = None
    bit_depth: Literal[8, 16, "16f", "32f"] | None = None
    channels: Annotated[
        dict[Literal["R", "G", "B", "A", "RGB", "L"], str | float],
        Field(description="Map name, 1-roughness, or 0/1."),
    ]


@mcp.tool(annotations=MUTATING)
def export_textures(
    directory: NewDirectory,
    preset: Annotated[str | None, Field(description="URL from inspect export_presets.")] = None,
    maps: Annotated[list[OutputMap] | None, Field(max_length=16,
                                                  description="Channel packing; not with preset.")] = None,
    resolution: Literal[128, 256, 512, 1024, 2048, 4096, 8192] | None = None,
    file_format: Literal["png", "tga", "tiff", "exr", "jpeg"] | None = None,
    bit_depth: Literal[8, 16, "16f", "32f"] | None = None,
    texture_sets: Annotated[list[TextureSetName] | None, Field(max_length=64)] = None,
    udim_tiles: Annotated[list[tuple[StrictInt, StrictInt]] | None,
                          Field(max_length=100, description="[u, v] pairs.")] = None,
    padding: Literal["infinite", "passthrough", "transparent", "color", "diffusion"] = "infinite",
    dilation_distance: Annotated[StrictInt | None, Field(ge=0, le=256)] = None,
    dry_run: Annotated[StrictBool, Field(description="Only explicit false writes files.")] = True,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """Preview or export texture sets into a new workspace directory."""
    return PainterClient().call("export_textures", {
        "directory": directory, "preset": preset,
        "maps": None if maps is None else [m.model_dump(exclude_none=True) for m in maps],
        "resolution": resolution, "file_format": file_format, "bit_depth": bit_depth,
        "texture_sets": texture_sets,
        "udim_tiles": None if udim_tiles is None else [list(tile) for tile in udim_tiles],
        "padding": padding, "dilation_distance": dilation_distance, "dry_run": dry_run,
    }, lease_id=lease_id)


@mcp.tool(annotations=MUTATING)
def bake(
    action: Literal["start", "status"],
    texture_sets: Annotated[list[TextureSetName] | None, Field(max_length=64)] = None,
    maps: list[Literal["normal", "world_space_normal", "id", "ao", "curvature", "position",
                       "thickness", "height", "opacity", "bent_normals"]] | None = None,
    high_poly_meshes: Annotated[list[str] | None, Field(max_length=16,
                                                        description="Workspace meshes.")] = None,
    resolution: Literal[128, 256, 512, 1024, 2048, 4096, 8192] | None = None,
    antialiasing: Literal["none", "2x2", "4x4", "8x8"] | None = None,
    max_frontal: Annotated[float | None, Field(ge=0, le=100, allow_inf_nan=False)] = None,
    max_rear: Annotated[float | None, Field(ge=0, le=100, allow_inf_nan=False)] = None,
    use_cage: StrictBool | None = None,
    cage_mesh: WorkspaceFile = None,
    match: Literal["always", "by_mesh_name"] | None = None,
    low_poly_suffix: Annotated[str | None, Field(max_length=32)] = None,
    high_poly_suffix: Annotated[str | None, Field(max_length=32)] = None,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """Bake mesh maps; start returns at once, poll status until busy is false.
    Unset options keep current settings; unselected sets are disabled for baking."""
    if action == "status":
        return PainterClient().call("bake_status")
    return PainterClient().call("bake", {
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
