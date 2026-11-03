"""Declared response fields and conservative transport failure details."""

from typing import Any, ClassVar, Literal

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, TextContent
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_serializer,
    model_validator,
)


class Result(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    @model_serializer(mode="wrap")
    def preserve_shape(self, handler):
        """Keep omitted optional fields omitted, preserving the existing wire shape."""
        present = set(self.model_fields_set)
        for name in self.model_fields_set:
            field = type(self).model_fields.get(name)
            if field is not None and field.alias:
                present.add(field.alias)
        return {key: value for key, value in handler(self).items() if key in present}


class UnknownToolError(ToolError):
    """The requested name was absent before any tool execution."""


def failure(name, error):
    cause = error
    while (isinstance(cause, ToolError) and not isinstance(cause, UnexpectedToolError)
           and cause.__cause__ is not None):
        cause = cause.__cause__
    missing_tool = isinstance(cause, UnknownToolError)
    rejected = missing_tool or isinstance(cause, (ValueError, ValidationError, FileNotFoundError))
    code = (
        "TOOL_NOT_FOUND"
        if missing_tool
        else "INVALID_ARGUMENT"
        if isinstance(cause, (ValueError, ValidationError))
        else "INPUT_NOT_FOUND"
        if isinstance(cause, FileNotFoundError)
        else "OUTCOME_UNKNOWN"
    )
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=str(error))],
        structuredContent={
            "error": {
                "version": 1,
                "code": code,
                "operation": name,
                "outcome": "rejected" if rejected else "unknown",
                "retry_safe": False,
                "next_action": "list_tools"
                if missing_tool
                else "inspect_inputs"
                if rejected
                else "inspect_application_and_outputs",
            }
        },
    )


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


class VariantResult(Result):
    """Validate existing action responses without adding wire discriminator fields."""

    shapes: ClassVar[tuple[tuple[str, ...], ...]] = ()
    nullable_shape_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def require_shape(cls, value):
        if isinstance(value, dict):
            fields = {field.alias or name for name, field in cls.model_fields.items()}
            if any(
                item is None and key in fields and key not in cls.nullable_shape_fields
                for key, item in value.items()
            ):
                raise ValueError("Result contains null for a non-null response field")
            if cls.shapes and not any(all(key in value for key in shape) for shape in cls.shapes):
                raise ValueError("Result is missing the fields of an action response")
        return value

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        for key, field in schema.get("properties", {}).items():
            field.pop("default", None)
            if key in cls.nullable_shape_fields:
                continue
            variants = field.get("anyOf")
            if variants and {"type": "null"} in variants:
                rest = [item for item in variants if item != {"type": "null"}]
                field.pop("anyOf")
                field.update(rest[0] if len(rest) == 1 else {"anyOf": rest})
            if not field or set(field) <= {"title"}:
                field["not"] = {"type": "null"}
        if cls.shapes:
            schema["anyOf"] = [{"required": list(shape)} for shape in cls.shapes]
        return schema


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
              ("dispatched", "points", "uid", "texture_set", "next"))
    prepared: Literal[True] | None = None
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
        ("project", "asset", "layer", "saved"),
        ("project", "asset", "removed", "position", "saved"),
    )
    nullable_shape_fields = frozenset({"asset", "layer", "applies_to", "blend_applies_to"})
    project: str | None = None
    asset: Any = None
    layer: Record | str | None = None
    saved: bool | None = None
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
        ("directory", "project", "assets", "inputs", "steps", "job", "source", "dry_run"),
        (
            "status",
            "job",
            "job_sha256",
            "project",
            "source",
            "assets",
            "steps",
            "inputs",
            "outputs",
            "schema",
            "app",
            "operation",
        ),
    )
    nullable_shape_fields = frozenset({"project", "source", "version", "seconds"})
    directory: str | None = None
    project: str | None = None
    assets: list[str | Record] | None = None
    inputs: list[str | Record] | None = None
    steps: list[str | Record] | None = None
    job: str | None = None
    source: str | None = None
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


def compact_union(schema):
    """Shorten equivalent union forms; output defaults are documentation only."""
    branches = schema.get("anyOf", [])
    if branches and all(
        set(item) == {"type"} and isinstance(item["type"], str) for item in branches
    ):
        types = list(dict.fromkeys(item["type"] for item in branches))
        if "number" in types and "integer" in types:
            types.remove("integer")
        schema.pop("anyOf")
        schema["type"] = types[0] if len(types) == 1 else types
    elif len(branches) == 2 and {"type": "null"} in branches:
        other = next(item for item in branches if item != {"type": "null"})
        safe = {
            "type",
            "items",
            "additionalProperties",
            "properties",
            "required",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "multipleOf",
            "minItems",
            "maxItems",
            "uniqueItems",
            "minLength",
            "maxLength",
            "pattern",
            "format",
            "minProperties",
            "maxProperties",
            "enum",
            "const",
        }
        if isinstance(other.get("type"), str) and set(other) <= safe:
            schema.pop("anyOf")
            schema.update(other)
            schema["type"] = [other["type"], "null"]
            if "const" in schema:
                schema["enum"] = [schema.pop("const"), None]
            elif "enum" in schema:
                schema["enum"] = [*schema["enum"], None]
    return schema


def compact_schema(schema, *, output=False):
    """Remove redundant schema titles without changing property names or literal data."""
    if not isinstance(schema, dict):
        return schema
    result = {
        key: value
        for key, value in schema.items()
        if key != "title" and not (output and key == "default")
    }
    if result.get("additionalProperties") is True:
        result.pop("additionalProperties")
    for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
        if key in result:
            result[key] = {
                name: compact_schema(value, output=output) for name, value in result[key].items()
            }
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if key in result:
            result[key] = [compact_schema(value, output=output) for value in result[key]]
    for key in (
        "items",
        "additionalProperties",
        "not",
        "if",
        "then",
        "else",
        "contains",
        "unevaluatedProperties",
        "propertyNames",
        "contentSchema",
    ):
        if key in result:
            result[key] = compact_schema(result[key], output=output)
    return compact_union(result)
