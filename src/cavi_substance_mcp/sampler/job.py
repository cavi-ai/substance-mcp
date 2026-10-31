"""Versioned Sampler batch job model and validation."""

import json
import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .targets import OUTPUT_KINDS, TEXTURE_FORMATS, target

CONTROL = re.compile(r"[\x00-\x1f\x7f]")
PLAIN = re.compile(r"^[^/\\\x00-\x1f\x7f]{1,96}$")
RESOURCE = re.compile(r"^[^@\x00-\x1f\x7f]{1,128}$")
USAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

PlainName = Annotated[str, Field(min_length=1, max_length=96)]
ResourceName = Annotated[str, Field(min_length=1, max_length=128)]
UsageName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
UsageLabel = Annotated[str, Field(min_length=1, max_length=64)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_$][A-Za-z0-9_.$]{0,63}$")]
TargetName = Literal["unreal", "unity", "blender", "generic"]
OutputKind = Literal["sbsar", "textures"]
TextureFormat = Literal["png", "tga", "tif", "exr"]
ParameterValue = bool | int | float | str | list[bool | int | float | str]


class JobValidationError(ValueError):
    """Raised when a job file is not a valid batch job."""


def plain_name(value: str) -> str:
    value = value.strip()
    if not PLAIN.fullmatch(value) or value in (".", "..") or value.endswith("."):
        raise ValueError("name must be a plain file name")
    return value


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or CONTROL.search(value):
        raise ValueError("path must be a non-empty printable string")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("path must stay inside the workspace")
    if ":" in path.parts[0]:
        raise ValueError("path must be relative")
    return value


def usage_label(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 64 or CONTROL.search(value):
        raise ValueError("usage label must be a short printable string")
    return value


def resource_name(value: str) -> str:
    value = value.strip()
    if not RESOURCE.fullmatch(value) or value.startswith("-"):
        raise ValueError("resource name must be printable")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetSpec(StrictModel):
    name: PlainName
    model: Literal["asm", "openpbr"] = "asm"
    preset: ResourceName | None = None

    _name = field_validator("name")(plain_name)
    _preset = field_validator("preset")(lambda value: None if value is None else resource_name(value))


class ProjectSpec(StrictModel):
    name: PlainName | None = None
    source: str | None = None
    assets: Annotated[list[PlainName], Field(min_length=1, max_length=64)] | None = None
    asset: AssetSpec | None = None

    _name = field_validator("name")(lambda value: None if value is None else plain_name(value))
    _source = field_validator("source")(lambda value: None if value is None else relative_path(value))
    _assets = field_validator("assets")(lambda values: None if values is None else [
        plain_name(item) for item in values
    ])

    @model_validator(mode="after")
    def project_rules(self):
        if self.source is not None:
            if self.asset is not None:
                raise ValueError("source projects cannot declare project.asset")
        elif self.name is None:
            raise ValueError("name is required for a new project")
        if self.assets is not None and self.source is None:
            raise ValueError("assets is only valid with source")
        if self.assets is not None and len(set(self.assets)) != len(self.assets):
            raise ValueError("assets must be unique")
        return self


class ImportImages(StrictModel):
    action: Literal["import_images"]
    asset: PlainName
    images: Annotated[list[str], Field(min_length=1, max_length=256)]
    position: Annotated[int, Field(ge=0, le=100000)] = 0
    import_option: Literal[
        "image_to_material_B2M", "image_to_material_AI_powered", "multiangle_to_material",
        "use_as_bitmap", "as_channels", "HDR_merge", "use_as_environment",
    ] = "use_as_bitmap"
    insert_above: bool = False

    _asset = field_validator("asset")(plain_name)
    _images = field_validator("images")(lambda values: [relative_path(item) for item in values])


class ImportTextures(StrictModel):
    action: Literal["import_textures"]
    asset: PlainName
    images: Annotated[dict[UsageName, str], Field(min_length=1, max_length=64)]
    position: Annotated[int, Field(ge=0, le=100000)] = 0
    insert_above: bool = True

    _asset = field_validator("asset")(plain_name)
    _images = field_validator("images")(lambda values: {
        usage: relative_path(item) for usage, item in values.items()
    })


class CreateAsset(StrictModel):
    action: Literal["create_asset"]
    name: PlainName
    model: Literal["asm", "openpbr"] = "asm"
    preset: ResourceName | None = None
    empty: bool = False

    _name = field_validator("name")(plain_name)
    _preset = field_validator("preset")(lambda value: None if value is None else resource_name(value))


class SetMetadata(StrictModel):
    action: Literal["set_metadata"]
    asset: PlainName
    author: Annotated[str | None, Field(max_length=128)] = None
    category: Annotated[str | None, Field(max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    tags: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=64)] | None = None
    physical_size: tuple[float, float, float] | None = None
    custom_metadata: Annotated[dict[str, bool | int | float | str | None], Field(max_length=64)] | None = None

    _asset = field_validator("asset")(plain_name)

    @model_validator(mode="after")
    def has_values(self):
        if all(value is None for value in (
            self.author, self.category, self.description, self.tags,
            self.physical_size, self.custom_metadata,
        )):
            raise ValueError("set_metadata needs at least one field")
        return self


class ActivateChannels(StrictModel):
    action: Literal["activate_channels"]
    asset: PlainName
    channels: Annotated[list[UsageName], Field(min_length=1, max_length=64)]

    _asset = field_validator("asset")(plain_name)


class DeactivateChannels(StrictModel):
    action: Literal["deactivate_channels"]
    asset: PlainName
    channels: Annotated[list[UsageName], Field(min_length=1, max_length=64)]

    _asset = field_validator("asset")(plain_name)


class SetChannels(StrictModel):
    action: Literal["set_channels"]
    asset: PlainName
    layer: ResourceName
    position: Annotated[int | None, Field(ge=0, le=100000)] = None
    applies_to: Annotated[list[UsageLabel], Field(min_length=1, max_length=64)] | None = None
    blend_applies_to: Annotated[list[UsageLabel], Field(min_length=1, max_length=64)] | None = None

    _asset = field_validator("asset")(plain_name)
    _layer = field_validator("layer")(resource_name)
    _applies = field_validator("applies_to")(lambda values: None if values is None else [
        usage_label(item) for item in values
    ])
    _blend = field_validator("blend_applies_to")(lambda values: None if values is None else [
        usage_label(item) for item in values
    ])

    @model_validator(mode="after")
    def has_channels(self):
        if self.applies_to is None and self.blend_applies_to is None:
            raise ValueError("set_channels needs applies_to or blend_applies_to")
        return self


class SetBlendParameters(StrictModel):
    action: Literal["set_blend_parameters"]
    asset: PlainName
    layer: ResourceName
    position: Annotated[int | None, Field(ge=0, le=100000)] = None
    parameters: Annotated[dict[Identifier, ParameterValue], Field(min_length=1, max_length=64)]

    _asset = field_validator("asset")(plain_name)
    _layer = field_validator("layer")(resource_name)


class InsertFilter(StrictModel):
    action: Literal["insert_filter"]
    asset: PlainName
    filter: ResourceName
    position: Annotated[int, Field(ge=0, le=100000)] = 0
    insert_above: bool = True

    _asset = field_validator("asset")(plain_name)
    _filter = field_validator("filter")(resource_name)


class InsertMaterial(StrictModel):
    action: Literal["insert_material"]
    asset: PlainName
    material: ResourceName
    position: Annotated[int, Field(ge=0, le=100000)] = 0
    blend_mode: Literal[
        "blend_ao", "blend_color_id", "blend_curvature", "blend_custom_mask", "blend_opacity",
        "blend_height", "blend_normal", "blend_per_channel",
    ] = "blend_normal"
    insert_above: bool = True

    _asset = field_validator("asset")(plain_name)
    _material = field_validator("material")(resource_name)


class SetParameters(StrictModel):
    action: Literal["set_parameters"]
    asset: PlainName
    layer: ResourceName
    position: Annotated[int | None, Field(ge=0, le=100000)] = None
    parameters: Annotated[dict[Identifier, ParameterValue], Field(min_length=1, max_length=64)]

    _asset = field_validator("asset")(plain_name)
    _layer = field_validator("layer")(resource_name)


class ApplyPreset(StrictModel):
    action: Literal["apply_preset"]
    asset: PlainName
    layer: ResourceName
    position: Annotated[int | None, Field(ge=0, le=100000)] = None
    preset: ResourceName

    _asset = field_validator("asset")(plain_name)
    _layer = field_validator("layer")(resource_name)
    _preset = field_validator("preset")(resource_name)


class RemoveLayer(StrictModel):
    action: Literal["remove_layer"]
    asset: PlainName
    layer: ResourceName
    position: Annotated[int | None, Field(ge=0, le=100000)] = None
    confirm: Literal[True]

    _asset = field_validator("asset")(plain_name)
    _layer = field_validator("layer")(resource_name)


class ImportResource(StrictModel):
    action: Literal["import_resource"]
    path: str
    resources: Annotated[list[ResourceName], Field(min_length=1, max_length=64)] | None = None

    _path = field_validator("path")(relative_path)
    _resources = field_validator("resources")(lambda values: None if values is None else [
        resource_name(item) for item in values
    ])


class Export(StrictModel):
    action: Literal["export"]
    asset: PlainName | None = None
    assets: Annotated[list[PlainName], Field(min_length=1, max_length=64)] | None = None
    all_assets: bool = False
    target: TargetName = "generic"
    preset: ResourceName | None = None
    outputs: Annotated[list[OutputKind], Field(min_length=1, max_length=2)] = Field(
        default_factory=lambda: list(OUTPUT_KINDS)
    )
    resolution: Annotated[int | None, Field(ge=64, le=8192)] = None
    texture_format: TextureFormat = "png"
    channels: Annotated[list[UsageName], Field(min_length=1, max_length=64)] | None = None
    directory: str = "exports"
    wait_seconds: Annotated[int, Field(ge=1, le=3600)] = 600

    _asset = field_validator("asset")(lambda value: None if value is None else plain_name(value))
    _assets = field_validator("assets")(lambda values: None if values is None else [
        plain_name(item) for item in values
    ])
    _preset = field_validator("preset")(lambda value: None if value is None else resource_name(value))
    _directory = field_validator("directory")(relative_path)

    @field_validator("outputs")
    @classmethod
    def unique_outputs(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("outputs must be unique")
        return values

    @model_validator(mode="after")
    def one_asset_selector(self):
        selected = int(self.asset is not None) + int(self.assets is not None) + int(self.all_assets)
        if selected != 1:
            raise ValueError("export needs exactly one of asset, assets or all_assets")
        if self.channels is not None and len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must be unique")
        return self


Step = Annotated[
    ImportImages | ImportTextures | CreateAsset | SetMetadata | ActivateChannels
    | DeactivateChannels | InsertFilter | InsertMaterial | SetParameters | ApplyPreset
    | SetChannels | SetBlendParameters | RemoveLayer | ImportResource | Export,
    Field(discriminator="action"),
]


class Job(StrictModel):
    schema_version: Literal[1] = Field(alias="schema")
    directory: str
    project: ProjectSpec
    steps: Annotated[list[Step], Field(min_length=1, max_length=64)]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    _directory = field_validator("directory")(relative_path)

    @model_validator(mode="after")
    def references(self):
        strict_assets = True
        known = set()
        if self.project.source is not None:
            if self.project.assets is None:
                strict_assets = False
            else:
                known = set(self.project.assets)
        elif self.project.asset is not None:
            if self.project.asset.name == self.project.name:
                raise ValueError("project asset name must differ from the project name")
            known.add(self.project.asset.name)
        directories = set()
        exports = 0
        for index, step in enumerate(self.steps):
            if isinstance(step, CreateAsset):
                if strict_assets and step.name in known:
                    raise ValueError(f"asset {step.name!r} is created twice")
                known.add(step.name)
                continue
            if isinstance(step, Export):
                exports += 1
                if step.directory in directories:
                    raise ValueError(f"export directory {step.directory!r} is reused")
                directories.add(step.directory)
                names = ([step.asset] if step.asset else []) + (step.assets or [])
                if strict_assets:
                    for name in names:
                        if name not in known:
                            raise ValueError(
                                f"export references asset {name!r} before it is created"
                            )
                    if step.all_assets and not known:
                        raise ValueError("all_assets export needs at least one asset")
                continue
            if (
                isinstance(step, (
                    ImportImages, ImportTextures, SetMetadata, ActivateChannels,
                    DeactivateChannels, InsertFilter, InsertMaterial, SetParameters,
                    ApplyPreset, SetChannels, SetBlendParameters, RemoveLayer,
                ))
                and strict_assets
                and step.asset not in known
            ):
                raise ValueError(
                    f"step {index} references asset {step.asset!r} before it is created"
                )
        if exports == 0:
            raise ValueError("job needs at least one export step")
        return self


def load_job(path) -> Job:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobValidationError(f"cannot read job: {exc}") from exc
    try:
        return Job.model_validate(data)
    except ValueError as exc:
        raise JobValidationError(str(exc)) from exc


def job_schema() -> dict:
    return Job.model_json_schema(by_alias=True)


def input_paths(job: Job) -> list[str]:
    found = [job.project.source] if job.project.source is not None else []
    for step in job.steps:
        if isinstance(step, ImportImages):
            found.extend(step.images)
        elif isinstance(step, ImportTextures):
            found.extend(step.images.values())
        elif isinstance(step, ImportResource):
            found.append(step.path)
    return sorted(set(found))


def validate_paths(job: Job, workspace) -> dict:
    root = workspace.expanduser().resolve(strict=True)
    directory = (root / job.directory).resolve()
    if directory == root or not directory.is_relative_to(root):
        raise JobValidationError("job directory must remain inside the workspace")
    if directory.exists():
        raise JobValidationError("job directory already exists; choose a new directory")
    if not directory.parent.is_dir():
        raise JobValidationError("job directory parent must exist")
    if job.project.source is not None:
        source = (root / job.project.source).resolve()
        if (
            not source.is_relative_to(root)
            or not source.is_file()
            or source.suffix.lower() != ".ssa"
        ):
            raise JobValidationError(
                "project source must be an existing .ssa inside the workspace"
            )
    for name in input_paths(job):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise JobValidationError(f"input {name!r} must be an existing workspace file")
    for step in job.steps:
        if isinstance(step, Export):
            target(step.target)
            if step.texture_format not in TEXTURE_FORMATS:
                raise JobValidationError(f"unsupported texture format {step.texture_format}")
    return {
        "directory": directory.relative_to(root).as_posix(),
        "project": job.project.name,
        "assets": [job.project.asset.name] if job.project.asset else [],
        "inputs": input_paths(job),
        "steps": [step.action for step in job.steps],
    }
