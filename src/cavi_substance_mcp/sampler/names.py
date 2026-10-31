"""File-name rules shared by the job model (host) and the operations (inside Sampler).

Stdlib only: this module is imported by code that runs in Sampler's embedded Python.
"""

import re

PLAIN = re.compile(r'^[^/\\<>:"|?*\x00-\x1f\x7f]{1,96}$')
# Device names Windows refuses as a file name, with or without an extension.
WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{digit}" for digit in "123456789¹²³"),
    *(f"LPT{digit}" for digit in "123456789¹²³"),
})
# Files a batch job writes at the top of its own directory.
JOB_FILES = ("manifest.json", "job.json", "failure.json", "progress.jsonl")


def plain_name(value: str) -> str:
    """Return the stripped name, or raise ValueError if it is not usable on any platform."""
    value = value.strip()
    if (
        not PLAIN.fullmatch(value)
        or value in (".", "..")
        or value.endswith(".")
        or value.split(".", 1)[0].rstrip(" ").upper() in WINDOWS_RESERVED
    ):
        raise ValueError("name must be a plain file name")
    return value


def output_directory(value: str) -> str:
    """Check every component of a new output directory (already a safe relative path)."""
    for part in value.split("/"):
        try:
            plain_name(part)
        except ValueError:
            raise ValueError(
                f"directory component {part!r} is not a portable file name "
                "(no < > : \" | ? *, trailing dot or Windows device names such as CON)"
            ) from None
    return value


# Vocabularies shared by the job model and server (host) and the operations (in Sampler).
PROJECT = frozenset({".ssa"})
RESOURCE = re.compile(r"^[^@\x00-\x1f\x7f]{1,128}$")
USAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
EXPORT_FORMATS = ("sbsar", "sbs", "png", "jpg", "tga", "tif", "exr")
MATERIAL_TYPES = ("standard", "decal", "atlas")
IMPORT_OPTIONS = (
    "image_to_material_B2M",
    "image_to_material_AI_powered",
    "multiangle_to_material",
    "use_as_bitmap",
    "as_channels",
    "HDR_merge",
    "use_as_environment",
)
BLEND_MODES = (
    "blend_ao",
    "blend_color_id",
    "blend_curvature",
    "blend_custom_mask",
    "blend_opacity",
    "blend_height",
    "blend_normal",
    "blend_per_channel",
)
