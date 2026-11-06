"""Declared response fields and conservative transport failure details."""

from typing import Any, Literal

from cavi_mcp_core.contracts import Result as Result
from cavi_mcp_core.contracts import UnknownToolError as UnknownToolError
from cavi_mcp_core.contracts import VariantResult as VariantResult
from cavi_mcp_core.contracts import failure as failure
from cavi_mcp_core.schema import compact_schema as compact_schema
from cavi_mcp_core.schema import compact_union as compact_union
from pydantic import Field


class PainterStatus(Result):
    connected: bool
    reason: str | None = None
    api_version: str | None = None
    open: bool | None = None
    file: str | None = None
    needs_saving: bool | None = None
    busy: bool | None = None
    workspace: str | None = None


class Ownership(Result):
    active: bool
    owner: str | None
    project_id: str
    claimed_project_id: str | None
    expires_in: float
    lease_id: str | None = None


class Binary(Result):
    path: str | None
    version: str | None


class DesignerStatus(Result):
    available: bool
    source: str
    bin_dir: str
    binaries: dict[str, Binary]
    workspace: str
    workspace_exists: bool


class SamplerStatus(Result):
    available: bool
    executable: str | None
    source: str
    version: str | None
    scripts_dir: str
    scripts_dir_exists: bool
    running: bool
    workspace: str
    workspace_exists: bool


class TextureExport(Result):
    dry_run: bool
    files: list[str]
    status: str | None = None
    omitted_files: list[str] | None = None
    manifest: str | None = None
    provenance: list[dict[str, Any]] | None = None


class BakeResult(Result):
    directory: str
    maps: dict[str, str]
    manifest: str


Number = int | float
Record = dict[str, Any]


class PainterProject(VariantResult):
    shapes = (
        ("requested", "path", "loading_may_be_pending"),
        ("closed",),
        ("saved", "file", "needs_saving"),
        ("path", "bytes", "manifest"),
        ("connected", "ready", "waiting_on", "waited_seconds"),
    )
    nullable_shape_fields = frozenset({"file", "default_texture_resolution", "template"})
    requested: str | None = None
    path: str | None = None
    loading_may_be_pending: bool | None = None
    next: str | None = None
    closed: bool | None = None
    already_closed: bool | None = None
    saved: bool | None = None
    file: str | None = None
    needs_saving: bool | None = None
    bytes: int | None = None
    manifest: str | None = None
    connected: bool | None = None
    reason: str | None = None
    busy: bool | None = None
    ready: bool | None = None
    waiting_on: list[str] | None = None
    waited_seconds: Number | None = None


class PainterInspection(VariantResult):
    shapes = (
        ("texture_sets",),
        ("presets",),
        ("resources", "truncated"),
        ("layers", "node_count", "truncated"),
    )
    texture_sets: list[Record] | None = None
    presets: list[Record] | None = None
    resources: list[Record] | None = None
    truncated: bool | None = None
    layers: list[Record] | None = None
    node_count: int | None = None


class PainterLayerResult(VariantResult):
    shapes = (("uid",), ("deleted", "name"))
    uid: int | None = None
    deleted: int | None = None
    name: str | None = None
    texture_set: str | None = None
    mask: str | None = None


class PainterBrushResult(VariantResult):
    shapes = (("prepared", "context_token", "preview", "viewport"),
              ("settings",),
              ("dispatched", "points", "uid", "texture_set", "next"))
    prepared: Literal[True] | None = None
    settings: Record | None = None
    context_token: str | None = None
    preview: str | None = None
    viewport: Record | None = None
    dispatched: Literal[True] | None = None
    points: int | None = None
    uid: int | None = None
    texture_set: str | None = None
    next: str | None = None


class PainterBakeResult(VariantResult):
    shapes = (
        ("started", "texture_sets", "maps", "disabled_texture_sets", "next"),
        ("cancel_requested", "running", "next"),
        (
            "busy",
            "running",
            "reconciled",
            "status",
            "progress",
            "seconds_since_start",
            "texture_sets",
            "maps",
        ),
    )
    nullable_shape_fields = frozenset({"status", "progress", "seconds_since_start"})
    started: Literal[True] | None = None
    texture_sets: list[str] | None = None
    maps: dict[str, list[str]] | None = None
    disabled_texture_sets: list[str] | None = None
    next: str | None = None
    cancel_requested: Literal[True] | None = None
    running: bool | None = None
    busy: bool | None = None
    reconciled: bool | None = None
    status: str | None = None
    progress: Any = None
    seconds_since_start: Number | None = None
    mesh_maps: Record | None = None


class PackedOutput(Result):
    name: str
    path: str
    width: int
    height: int
    channels: Literal["RGB", "RGBA"]
    bit_depth: Literal[8, 16]


class DesignerMaterial(VariantResult):
    shapes = (
        ("sbsar", "graphs"),
        ("directory", "maps", "outputs", "manifest"),
        ("directory", "packed", "sources", "manifest"),
    )
    nullable_shape_fields = frozenset({"presets"})
    sbsar: str | None = None
    graphs: list[Record] | None = None
    source: str | None = None
    manifest: str | None = None
    directory: str | None = None
    maps: dict[str, str] | None = None
    outputs: list[Record] | None = None
    packed: list[PackedOutput] | None = None


class DesignerGraphResult(Result):
    action: Literal['create', 'edit', 'inspect', 'catalog']
    directory: str
    manifest: str
    report: str
    package: str | None = None
    graphs: list[Record] | None = None
    node_ids: dict[str, str] | None = None
    definitions: list[str] | None = None
    total: int | None = None
    offset: int | None = None
    node_schema: Record | None = None
    resources: list[Record] | None = None


class SamplerInspection(VariantResult):
    shapes = (
        ("project", "project_path", "assets"),
        ("project", "asset", "layers"),
        (
            "filters",
            "materials",
            "export_presets",
            "export_targets",
        ),
    )
    nullable_shape_fields = frozenset({"project", "project_path"})
    project: str | None = None
    project_path: str | None = None
    assets: list[Record] | None = None
    asset: Record | None = None
    layers: list[Record] | None = None
    filters: list[str] | None = None
    materials: list[str] | None = None
    export_presets: list[str] | None = None
    export_targets: dict[str, Record] | None = None


class SamplerProjectResult(Result):
    project: str
    directory: str
    manifest: str
    assets: list[Record] | None = None


class SamplerAssetResult(VariantResult):
    shapes = (("project", "asset", "saved"), ("project", "deleted", "saved"), ("path", "resources"))
    nullable_shape_fields = frozenset({"asset"})
    project: str | None = None
    asset: Record | str | None = None
    saved: bool | None = None
    deleted: str | None = None
    path: str | None = None
    resources: list[Record] | None = None
    verified: Literal[True] | None = None
    source: Record | None = None
    source_manifest: str | None = None


class SamplerLayerResult(VariantResult):
    shapes = (
        ("layer",),
        ("removed", "position"),
    )
    nullable_shape_fields = frozenset({"asset", "layer", "applies_to", "blend_applies_to"})
    project: str
    asset: Any
    layer: Record | str | None = None
    saved: bool
    removed: str | None = None
    position: int | None = None


class SamplerExportResult(Result):
    project: str
    asset: Any
    directory: str
    format: str
    target: str | None
    dry_run: bool
    files: list[str]
    manifest: str | None = None


class SamplerBuildResult(VariantResult):
    shapes = (
        ("directory", "dry_run"),
        (
            "status",
            "job_sha256",
            "outputs",
            "schema",
            "app",
            "operation",
        ),
    )
    nullable_shape_fields = frozenset({"project", "source", "version", "seconds"})
    directory: str | None = None
    project: str | None
    assets: list[str | Record]
    inputs: list[str | Record]
    steps: list[str | Record]
    job: str
    source: str | None
    dry_run: Literal[True] | None = None
    schema_version: int | None = Field(default=None, alias="schema")
    app: Literal["sampler"] | None = None
    operation: Literal["build"] | None = None
    outputs: list[Record] | None = None
    status: Literal["success"] | None = None
    job_sha256: str | None = None


class SamplerPackResult(Result):
    directory: str
    maps: list[PackedOutput]
    sources: list[str]
    manifest: str
