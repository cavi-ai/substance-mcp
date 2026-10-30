"""Typed stdio MCP surface for Substance 3D Designer's headless CLIs."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StringConstraints

from . import operations
from .packing import USAGE, PackSpec

mcp = FastMCP(
    "cavi-substance-designer-mcp",
    instructions=(
        "Runs Designer's sbscooker, sbsrender and substance3d_baker. Paths are relative to "
        "CAVI_SUBSTANCE_WORKSPACE; outputs go to new directories. Call designer_status first."
    ),
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
Number = StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]
Resolution = Literal[32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
Timeout = Annotated[StrictInt, Field(ge=1, le=3600)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Suffix = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.][A-Za-z0-9_.-]{0,31}$")]
MapName = Literal["normal", "ambient_occlusion", "curvature", "thickness", "position",
                  "world_normal", "bent_normal", "id", "height", "opacity"]
ACTION_PARAMS = {
    "info": {"path"},
    "cook": {"path", "builtin", "directory", "expose_output_size", "expose_random_seed"},
    "render": {"path", "graph", "directory", "values", "resolution", "seed", "preset",
               "outputs", "format", "bit_depth", "pack"},
    "pack": {"directory", "pack", "sources"},
}
DEFAULTS = {"format": "png", "bit_depth": "8", "expose_output_size": True,
            "expose_random_seed": True}


def need(value, name, action):
    if value is None:
        raise ValueError(f"{name} is required for action={action}")
    return value


@mcp.tool(annotations=READ_ONLY)
async def designer_status() -> dict[str, Any]:
    """Resolved Designer CLIs, versions, built-in packages and workspace."""
    return await operations.status()


@mcp.tool(annotations=WRITES)
async def material(
    action: Literal["info", "cook", "render", "pack"],
    path: Annotated[str | None, Field(description="info/render: .sbsar; cook: .sbs.")] = None,
    builtin: Annotated[str | None, Field(
        description="cook: templates/<name> or packages/<name>.")] = None,
    directory: Annotated[str | None, Field(description="New workspace directory.")] = None,
    graph: Annotated[str | None, Field(description="pkg://name; needed if several.")] = None,
    values: Annotated[dict[Identifier, Number | Annotated[list[Number], Field(
        min_length=1, max_length=4)]] | None, Field(max_length=64)] = None,
    resolution: Resolution | None = None,
    seed: Annotated[StrictInt | None, Field(ge=0, le=2147483647)] = None,
    preset: Annotated[str | None, Field(max_length=128)] = None,
    outputs: Annotated[list[Identifier] | None, Field(max_length=64)] = None,
    format: Literal["png", "exr", "tga", "tif", "jpg"] = "png",
    bit_depth: Literal["8", "16", "16f", "32f"] = "8",
    pack: Annotated[PackSpec | None, Field(description="render/pack channel packing.")] = None,
    sources: Annotated[dict[Annotated[str, StringConstraints(pattern=f"^{USAGE}$")], str] | None,
                       Field(max_length=32, description="pack: usage -> image.")] = None,
    expose_output_size: StrictBool = True,
    expose_random_seed: StrictBool = True,
    timeout_seconds: Timeout = 600,
) -> dict[str, Any]:
    """info lists graphs; cook .sbs -> .sbsar; render outputs (+optional pack); pack maps."""
    given = {key for key, value in locals().items()
             if key not in ("action", "timeout_seconds") and value is not None
             and value != DEFAULTS.get(key)}
    extra = given - ACTION_PARAMS[action]
    if extra:
        raise ValueError(f"Not valid for action={action}: {', '.join(sorted(extra))}")
    if action == "info":
        return await operations.info(need(path, "path", action), timeout_seconds)
    if action == "cook":
        return await operations.cook(path, builtin, need(directory, "directory", action),
                                     expose_output_size, expose_random_seed, timeout_seconds)
    if action == "render":
        return await operations.render(
            need(path, "path", action), graph, need(directory, "directory", action), values,
            resolution, seed, preset, outputs, format, bit_depth, pack, timeout_seconds)
    return await operations.pack(need(sources, "sources", action), need(pack, "pack", action),
                                 need(directory, "directory", action))


@mcp.tool(annotations=WRITES)
async def bake(
    low: Annotated[str, Field(description="Workspace low-poly mesh.")],
    maps: Annotated[list[MapName], Field(min_length=1, max_length=10)],
    directory: Annotated[str, Field(description="New workspace directory.")],
    high: Annotated[str | None, Field(description="High-poly mesh; else low is used.")] = None,
    cage: Annotated[str | None, Field(description="Cage mesh; needs use_cage.")] = None,
    use_cage: StrictBool = False,
    resolution: Resolution = 2048,
    normal_format: Literal["OpenGL", "DirectX"] = "OpenGL",
    max_frontal: Annotated[StrictFloat | StrictInt | None, Field(
        ge=0, le=10, description="Fraction of bbox.")] = None,
    max_rear: Annotated[StrictFloat | StrictInt | None, Field(ge=0, le=10)] = None,
    udim: Annotated[StrictInt | None, Field(ge=1001, le=1999)] = None,
    match_by_name: StrictBool = False,
    suffix_low: Suffix = "_low",
    suffix_high: Suffix = "_high",
    format: Literal["png", "tga", "tif", "exr"] = "png",
    timeout_seconds: Annotated[Timeout, Field(description="Per map.")] = 600,
) -> dict[str, Any]:
    """Bake mesh maps sequentially; each output is verified."""
    options = {"resolution": resolution, "normal_format": normal_format, "use_cage": use_cage,
               "max_frontal": max_frontal, "max_rear": max_rear, "udim": udim,
               "match_by_name": match_by_name, "suffix_low": suffix_low,
               "suffix_high": suffix_high, "format": format}
    return await operations.bake(low, high, cage, maps, directory, options, timeout_seconds)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
