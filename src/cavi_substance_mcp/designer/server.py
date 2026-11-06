"""Typed stdio MCP surface for native Designer graphs and headless material tools."""

from typing import Annotated, Literal

from cavi_mcp_core.server import READ_ONLY, WRITES, Server, need, reject_foreign
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StringConstraints

from ..core.contracts import BakeResult, DesignerGraphResult, DesignerMaterial, DesignerStatus
from ..packing import USAGE, PackSpec
from . import operations
from .graph import invoke as graph_invoke
from .graph_models import GraphRecipe

mcp = Server(
    "cavi-substance-designer-mcp",
    package="cavi-substance-mcp",
    instructions=(
        "Runs Designer's sbscooker, sbsrender and substance3d_baker. Paths are relative to "
        "CAVI_SUBSTANCE_WORKSPACE; outputs go to new directories. Call designer_status first."
    ),
)
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
DEFAULTS = {"format": "png", "bit_depth": "16", "expose_output_size": True,
            "expose_random_seed": True}


@mcp.tool(annotations=READ_ONLY)
async def designer_status() -> DesignerStatus:
    """Resolved Designer CLIs, versions, built-in packages and workspace."""
    return await operations.status()


@mcp.tool(annotations=WRITES)
async def material(
    action: Annotated[
        Literal["info", "cook", "render", "pack"], Field(description="Material operation.")
    ],
    path: Annotated[str | None, Field(description="info/render: .sbsar; cook: .sbs.")] = None,
    builtin: Annotated[
        str | None, Field(description="cook: templates/<name> or packages/<name>.")
    ] = None,
    directory: Annotated[str | None, Field(description="New directory.")] = None,
    graph: Annotated[str | None, Field(description="pkg://name; needed if several.")] = None,
    values: Annotated[
        dict[Identifier, Number | Annotated[list[Number], Field(min_length=1, max_length=4)]]
        | None,
        Field(max_length=64, description="render: input values."),
    ] = None,
    resolution: Annotated[
        Resolution | None, Field(description="render: output texture size.")
    ] = None,
    seed: Annotated[
        StrictInt | None, Field(ge=0, le=2147483647, description="render: random seed.")
    ] = None,
    preset: Annotated[str | None, Field(max_length=128, description="render: preset name.")] = None,
    outputs: Annotated[
        list[Identifier] | None,
        Field(max_length=64, description="render: outputs; null=all."),
    ] = None,
    format: Annotated[
        Literal["png", "exr", "tga", "tif", "jpg"],
        Field(description="render: output image format."),
    ] = "png",
    bit_depth: Annotated[
        Literal["8", "16", "16f", "32f"], Field(description="render: precision.")
    ] = "16",
    pack: Annotated[PackSpec | None, Field(description="render/pack channel packing.")] = None,
    sources: Annotated[
        dict[Annotated[str, StringConstraints(pattern=f"^{USAGE}$")], str] | None,
        Field(max_length=32, description="pack: usage -> image."),
    ] = None,
    expose_output_size: Annotated[
        StrictBool, Field(description="cook: expose size.")
    ] = True,
    expose_random_seed: Annotated[
        StrictBool, Field(description="cook: expose seed.")
    ] = True,
    timeout_seconds: Annotated[Timeout, Field(description="Timeout (s).")] = 600,
) -> DesignerMaterial:
    """info lists graphs; cook .sbs -> .sbsar; render outputs (+optional pack); pack maps."""
    reject_foreign(action, {
        key: value for key, value in locals().items()
        if key not in ("action", "timeout_seconds") and value != DEFAULTS.get(key)
    }, ACTION_PARAMS[action])
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
    low: Annotated[str, Field(description="Low mesh path.")],
    maps: Annotated[
        list[MapName], Field(min_length=1, max_length=10, description="Mesh maps to bake.")
    ],
    directory: Annotated[str, Field(description="New directory.")],
    high: Annotated[str | None, Field(description="High mesh; else low.")] = None,
    cage: Annotated[str | None, Field(description="Cage mesh; needs use_cage.")] = None,
    use_cage: Annotated[StrictBool, Field(description="Use the supplied cage mesh.")] = False,
    resolution: Annotated[Resolution, Field(description="Texture size.")] = 2048,
    normal_format: Annotated[
        Literal["OpenGL", "DirectX"], Field(description="Normal map orientation.")
    ] = "OpenGL",
    max_frontal: Annotated[
        StrictFloat | StrictInt | None, Field(ge=0, le=10, description="Fraction of bbox.")
    ] = None,
    max_rear: Annotated[
        StrictFloat | StrictInt | None,
        Field(ge=0, le=10, description="Rear distance / bounds."),
    ] = None,
    udim: Annotated[
        StrictInt | None, Field(ge=1001, le=1999, description="UDIM tile to bake.")
    ] = None,
    match_by_name: Annotated[
        StrictBool, Field(description="Match mesh suffixes.")
    ] = False,
    suffix_low: Annotated[Suffix, Field(description="Low mesh name suffix.")] = "_low",
    suffix_high: Annotated[Suffix, Field(description="High mesh name suffix.")] = "_high",
    format: Annotated[
        Literal["png", "tga", "tif", "exr"], Field(description="Image format.")
    ] = "exr",
    timeout_seconds: Annotated[Timeout, Field(description="Per map.")] = 600,
) -> BakeResult:
    """Bake mesh maps sequentially; each output is verified."""
    options = {"resolution": resolution, "normal_format": normal_format, "use_cage": use_cage,
               "max_frontal": max_frontal, "max_rear": max_rear, "udim": udim,
               "match_by_name": match_by_name, "suffix_low": suffix_low,
               "suffix_high": suffix_high, "format": format}
    return await operations.bake(low, high, cage, maps, directory, options, timeout_seconds)


@mcp.tool(annotations=WRITES)
async def graph(
    action: Annotated[
        Literal["create", "edit", "inspect", "catalog"], Field(description="Graph operation.")
    ],
    directory: Annotated[str, Field(description="New output directory.")],
    path: Annotated[str | None, Field(description="Workspace .sbs for edit/inspect.")] = None,
    recipe: Annotated[
        GraphRecipe | None, Field(description="create/edit: operations.")
    ] = None,
    query: Annotated[str, Field(max_length=128, description="catalog: node ID search text.")] = "",
    offset: Annotated[StrictInt, Field(ge=0, description="catalog: first result index.")] = 0,
    limit: Annotated[StrictInt, Field(ge=1, le=500, description="catalog: maximum results.")] = 100,
    timeout_seconds: Annotated[
        StrictInt, Field(ge=1, le=600, description="Timeout (s).")
    ] = 300,
) -> DesignerGraphResult:
    """Author SBS graphs in a dedicated native batch. Edits copy the source and preserve
    unspecified settings. Refuses running Designer; cook/render the saved package with material."""
    return await graph_invoke(action, directory, path, recipe, query, offset, limit,
                              timeout_seconds)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
