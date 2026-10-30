"""Typed stdio MCP surface for Substance Painter."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictInt

from .client import PainterClient

mcp = FastMCP(
    "cavi-substance-mcp",
    instructions=(
        "Requires licensed Painter with remote scripting; `cavi-substance-mcp runtime start` "
        "launches it on macOS. Call painter_status first. Claim session_control and pass "
        "lease_id on edits. After open/create, use project wait_ready. Exports default to "
        "dry runs into a new directory. Never retry uncertain mutations automatically."
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
    action: Literal["open", "create", "close", "save", "save_copy", "wait_ready"],
    path: Annotated[WorkspaceFile, Field(description="open: workspace .spp.")] = None,
    mesh_path: Annotated[WorkspaceFile, Field(description="create: workspace mesh.")] = None,
    normal_map_format: Literal["OpenGL", "DirectX"] = "OpenGL",
    default_texture_resolution: Literal[256, 512, 1024, 2048, 4096, 8192] | None = None,
    expected_file: Annotated[
        str | None, Field(description="close: file from painter_status; null if unsaved.")
    ] = None,
    discard_unsaved: StrictBool = False,
    directory: Annotated[str | None, Field(description="save_copy: new workspace directory.")] = None,
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 60,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """open/create only with no project open; close refuses unsaved edits unless
    discard_unsaved; save overwrites the workspace file; wait_ready polls until editable."""
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
    action: Literal["texture_sets", "layer_stack", "export_presets"],
    texture_set: Annotated[TextureSetName | None, Field(description="layer_stack only.")] = None,
) -> dict[str, Any]:
    """List texture sets with channels and resolution, a set's root layers, or export presets."""
    if action == "layer_stack":
        return PainterClient().call(
            "layer_stack", {"texture_set": need(texture_set, "texture_set", action)}
        )
    return PainterClient().call(action)


@mcp.tool(annotations=MUTATING)
def layer_edit(
    action: Literal["fill", "texture_fill"],
    texture_set: TextureSetName,
    name: Annotated[str, Field(min_length=1, max_length=63)],
    base_color: Annotated[
        tuple[UnitValue, UnitValue, UnitValue] | None, Field(description="fill: sRGB 0-1.")
    ] = None,
    base_color_texture: Annotated[
        WorkspaceFile, Field(description="texture_fill: workspace PNG, embedded as sRGB.")
    ] = None,
    roughness: UnitValue = 0.5,
    metallic: UnitValue = 0.0,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """Add a uniquely named root fill layer; failures remove the new layer."""
    if action == "fill":
        if base_color_texture is not None:
            raise ValueError("base_color_texture is only valid for action=texture_fill")
        operation = "create_fill_layer"
        extra = {"base_color": need(base_color, "base_color", action)}
    else:
        if base_color is not None:
            raise ValueError("base_color is only valid for action=fill")
        operation = "create_texture_fill"
        extra = {"base_color_texture": need(base_color_texture, "base_color_texture", action)}
    return PainterClient().call(operation, {
        "texture_set": texture_set, "name": name, "roughness": roughness,
        "metallic": metallic, **extra,
    }, lease_id=lease_id)


@mcp.tool(annotations=MUTATING)
def export_textures(
    directory: NewDirectory,
    preset: Annotated[str, Field(description="Preset URL from inspect export_presets.")],
    dry_run: Annotated[StrictBool, Field(description="Only explicit false writes files.")] = True,
    lease_id: LeaseId = None,
) -> dict[str, Any]:
    """Preview or export all texture sets into a new workspace directory."""
    return PainterClient().call(
        "export_textures",
        {"directory": directory, "preset": preset, "dry_run": dry_run},
        lease_id=lease_id,
    )


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
