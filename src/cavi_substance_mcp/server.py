"""Typed stdio MCP surface for Substance Painter."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictInt

from .client import PainterClient

mcp = FastMCP(
    "cavi-substance-mcp",
    instructions=(
        "Painter must be installed, licensed, and running with remote scripting enabled. "
        "If disconnected, use cavi-substance-mcp runtime start on macOS to launch Painter. "
        "Inspect status first; claim session_control ownership and pass lease_id on edits. Export dry runs are the default; use a new output directory. "
        "Designer is not supported. Never retry uncertain mutations automatically."
    ),
)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
CLOSING = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
PathInput = Annotated[
    str, Field(description="Existing input file relative to the configured workspace.")
]
NewDirectory = Annotated[
    str,
    Field(description="New directory relative to the workspace; existing directories are refused."),
]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
UnitValue = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
TextureSetName = Annotated[str, Field(min_length=1, max_length=128)]


@mcp.tool(annotations=READ_ONLY)
def painter_status() -> dict[str, Any]:
    """Check connectivity, project state and output workspace without changing a project."""
    try:
        return PainterClient().call("status")
    except ConnectionError as exc:
        return {"connected": False, "reason": str(exc)}


@mcp.tool(annotations=MUTATING)
def open_project(
    path: PathInput,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Open a workspace .spp only if Painter currently has no project open."""
    return PainterClient().call("open_project", {"path": path}, lease_id=lease_id)


@mcp.tool(annotations=MUTATING)
def create_project(
    mesh_path: PathInput,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Create a project from a workspace mesh only if no project is already open."""
    return PainterClient().call("create_project", {"path": mesh_path}, lease_id=lease_id)


@mcp.tool(annotations=READ_ONLY)
def texture_sets() -> dict[str, Any]:
    """List the current project's texture sets."""
    return PainterClient().call("texture_sets")


@mcp.tool(annotations=READ_ONLY)
def layer_stack(texture_set: TextureSetName) -> dict[str, Any]:
    """Inspect root layer names, IDs and types in the named Texture Set's default stack."""
    return PainterClient().call("layer_stack", {"texture_set": texture_set})


@mcp.tool(annotations=MUTATING)
def create_fill_layer(
    texture_set: TextureSetName,
    name: Annotated[str, Field(min_length=1, max_length=63)],
    base_color: Annotated[
        tuple[UnitValue, UnitValue, UnitValue], Field(description="sRGB RGB, 0–1.")
    ],
    roughness: UnitValue = 0.5,
    metallic: UnitValue = 0.0,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Add a named root fill for color, roughness and metallic; refuse duplicate names."""
    return PainterClient().call(
        "create_fill_layer",
        {
            "texture_set": texture_set,
            "name": name,
            "base_color": base_color,
            "roughness": roughness,
            "metallic": metallic,
        },
        lease_id=lease_id,
    )


@mcp.tool(annotations=MUTATING)
def create_texture_fill(
    texture_set: TextureSetName,
    name: Annotated[str, Field(min_length=1, max_length=63)],
    base_color_texture: PathInput,
    roughness: UnitValue = .5,
    metallic: UnitValue = 0,
    lease_id: str | None = None,
) -> dict[str, Any]:
    """Create an sRGB bitmap color fill with raw roughness and metallic; embed the PNG in the project."""
    return PainterClient().call("create_texture_fill", {
        "texture_set": texture_set, "name": name, "base_color_texture": base_color_texture,
        "roughness": roughness, "metallic": metallic,
    }, lease_id=lease_id)


@mcp.tool(annotations=CLOSING)
def close_project(
    expected_file: Annotated[
        str | None,
        Field(description="Exact file from painter_status; null for an unsaved project."),
    ],
    discard_unsaved: Annotated[
        StrictBool,
        Field(description="Explicit true permits discarding the inspected project's edits."),
    ] = False,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Close the inspected project; refuse unsaved edits unless explicitly discarded."""
    return PainterClient().call(
        "close_project",
        {"expected_file": expected_file, "discard_unsaved": discard_unsaved},
        lease_id=lease_id,
    )


@mcp.tool(annotations=READ_ONLY)
def export_presets() -> dict[str, Any]:
    """List Painter's predefined texture export presets and their URLs."""
    return PainterClient().call("export_presets")


@mcp.tool(annotations=MUTATING)
def save_project_copy(
    directory: NewDirectory,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Save project.spp in a new workspace directory, preserving the open project's location."""
    return PainterClient().call("save_copy", {"directory": directory}, lease_id=lease_id)


@mcp.tool(annotations=MUTATING)
def export_textures(
    directory: NewDirectory,
    preset: Annotated[
        str, Field(description="Exact predefined preset URL returned by export_presets.")
    ],
    dry_run: Annotated[
        StrictBool,
        Field(
            description="True previews paths without writing; only explicit false exports textures."
        ),
    ] = True,
    lease_id: Annotated[
        str | None, Field(description="Edit lease from session_control claim.")
    ] = None,
) -> dict[str, Any]:
    """Preview or export textures to a new workspace directory using a listed preset URL."""
    return PainterClient().call(
        "export_textures",
        {"directory": directory, "preset": preset, "dry_run": dry_run},
        lease_id=lease_id,
    )


@mcp.tool(annotations=MUTATING)
def session_control(
    action: Literal["inspect", "claim", "renew", "release"],
    owner: Annotated[str | None, Field(max_length=128)] = None,
    expected_project: Annotated[
        str | None, Field(description="Exact project_id from inspect.")
    ] = None,
    lease_id: Annotated[str | None, Field(max_length=128)] = None,
    ttl_seconds: Annotated[StrictInt, Field(ge=30, le=3600)] = 600,
) -> dict[str, Any]:
    """Inspect ownership or claim, renew or release an application edit lease.

    Claim returns a private lease_id. Supply it on every edit. Expiry never closes
    a project. Release does not save or discard changes.
    """
    return PainterClient().call(
        "session_control",
        {
            "action": action,
            "owner": owner,
            "expected_project": expected_project,
            "lease_id": lease_id,
            "ttl_seconds": ttl_seconds,
        },
    )


def main():
    import sys

    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
