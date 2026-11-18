"""Shared channel-packing contract: validation and numpy/Pillow writer."""

import re
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import tifffile
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from .texture_io import dimensions, load, write_png16

_write_png16 = write_png16

USAGE = r"[A-Za-z][A-Za-z0-9_]{0,63}"
SOURCE = rf"^(?:(?:1-)?{USAGE}|[01]|0?\.[0-9]{{1,6}}|1\.0{{1,6}})$"
NAME = r"^[A-Za-z0-9_-]{1,96}$"
# Refuse a pack whose peak working memory would exceed this.
MAX_DECODED_BYTES = 2 * 1024**3
# Peak bytes per pixel: every source stays decoded as float32 RGBA (16) while maps are
# written; on top of that, decoding one source holds up to two more float32 RGBA copies
# and writing one map holds its integer image, one float32 RGB plane and the encoder's
# big-endian copy (at most 32 for either).
SOURCE_BYTES, WORKING_BYTES = 16, 32
READABLE = {".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".exr"}

Source = Annotated[str, StringConstraints(pattern=SOURCE)]
UsageName = Annotated[str, StringConstraints(pattern=f"^{USAGE}$")]


class PackMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(pattern=NAME)]
    format: Literal["png", "tga", "tif"] = "png"
    bit_depth: Annotated[Literal[8, 16], Field(description="16: png/tif")] = 16
    srgb: StrictBool = False
    channels: Annotated[
        dict[Literal["R", "G", "B", "A", "RGB"], Source],
        Field(description="usage, 1-usage or 0..1"),
    ]


class PackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maps: Annotated[list[PackMap], Field(min_length=1, max_length=16)]
    defaults: dict[UsageName, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]] = {}


def check_spec(spec: PackSpec):
    names = set()
    for item in spec.maps:
        keys = set(item.channels)
        if "RGB" in keys and keys & {"R", "G", "B"}:
            raise ValueError(f"{item.name}: RGB cannot be combined with R, G or B")
        if "RGB" not in keys and not {"R", "G", "B"} <= keys:
            raise ValueError(f"{item.name}: channels need RGB or all of R, G, B")
        if item.bit_depth == 16 and item.format not in ("png", "tif"):
            raise ValueError(f"{item.name}: bit_depth 16 requires png or tif")
        if item.name.lower() in names:
            raise ValueError(f"Duplicate map name: {item.name}")
        names.add(item.name.lower())


def _plane(data: np.ndarray, rgb: bool) -> np.ndarray:
    if rgb:
        return data[:, :, :3] if data.shape[2] >= 3 else np.repeat(data[:, :, :1], 3, axis=2)
    return data[:, :, :1]


def _encode_srgb(plane: np.ndarray) -> None:
    """Linear 0..1 samples to sRGB-encoded 0..1 in place (IEC 61966-2-1), in row blocks
    so the temporaries stay small."""
    for rows in np.array_split(plane, max(1, len(plane) // 256)):
        dark = rows <= 0.0031308
        linear_part = rows[dark] * np.float32(12.92)
        np.power(rows, np.float32(1 / 2.4), out=rows)
        rows *= np.float32(1.055)
        rows -= np.float32(0.055)
        rows[dark] = linear_part


def pack(spec: PackSpec, sources: dict[str, Path], directory: Path) -> dict:
    check_spec(spec)
    cache: dict[str, np.ndarray] = {}
    needed = set()
    for item in spec.maps:
        for source in item.channels.values():
            usage = source.removeprefix("1-")
            if re.fullmatch(USAGE, usage):
                if usage not in sources and usage not in spec.defaults:
                    raise ValueError(
                        f"{item.name}: source usage '{usage}' is missing and has no default"
                    )
                if usage in sources:
                    needed.add(usage)
    if not needed:
        raise ValueError("Packing needs at least one image source")
    counts = [width * height for width, height in (dimensions(sources[usage])
                                                   for usage in sorted(needed))]
    decoded = SOURCE_BYTES * sum(counts) + WORKING_BYTES * max(counts)
    if decoded > MAX_DECODED_BYTES:
        raise ValueError(
            f"Sources need {decoded / 1024**3:.1f} GiB decoded; the limit is "
            f"{MAX_DECODED_BYTES / 1024**3:.0f} GiB. Pack fewer or smaller maps per call."
        )
    last_use = {}
    for index, item in enumerate(spec.maps):
        for source in item.channels.values():
            last_use[source.removeprefix("1-")] = index
    size = None
    for usage in sorted(needed):
        cache[usage] = load(sources[usage])
        # NaN fails both comparisons; infinities fail one.
        if not (cache[usage].min() >= 0 and cache[usage].max() <= 1):
            raise ValueError(
                f"{usage}: integer channel packing requires finite normalized 0..1 samples; "
                "remap HDR/signed values explicitly"
            )
        shape = cache[usage].shape[:2]
        if size is None:
            size = shape
        elif shape != size:
            raise ValueError(
                f"Source resolutions differ: {usage} is {shape[1]}x{shape[0]}, "
                f"expected {size[1]}x{size[0]}"
            )
    height, width = size

    def resolve(source: str, rgb: bool, out: np.ndarray) -> np.ndarray:
        invert = source.startswith("1-") and re.fullmatch(USAGE, source[2:])
        usage = source[2:] if invert else source
        if not re.fullmatch(USAGE, usage):
            value = np.float32(float(usage))
        elif usage in cache:
            value = _plane(cache[usage], rgb)
        else:
            value = np.float32(spec.defaults[usage])
        if invert:
            np.subtract(np.float32(1), value, out=out)
        else:
            out[...] = value
        return out

    # One float32 plane reused for every channel group, quantized in place.
    buffer = np.empty((height, width, 3), dtype=np.float32)
    written = []
    for index, item in enumerate(spec.maps):
        channels = item.channels
        planes = (
            [(channels["RGB"], True)]
            if "RGB" in channels
            else [(channels[key], False) for key in "RGB"]
        )
        color = len(planes)
        if "A" in channels:
            planes.append((channels["A"], False))
        scale, dtype = (65535.0, np.uint16) if item.bit_depth == 16 else (255.0, np.uint8)
        # Quantize one plane at a time into the integer image instead of building a
        # float32 copy of the whole map.
        pixels = np.empty((height, width, 4 if "A" in channels else 3), dtype=dtype)
        offset = 0
        for number, (source, rgb) in enumerate(planes):
            depth = 3 if rgb else 1
            plane = resolve(source, rgb, buffer[:, :, :depth])
            if item.srgb and number < color:
                _encode_srgb(plane)
            plane *= np.float32(scale)
            pixels[:, :, offset : offset + depth] = np.rint(plane, out=plane)
            offset += depth
        for usage in [key for key, last in last_use.items() if last == index]:
            cache.pop(usage, None)
        target = directory / f"{item.name}.{item.format}"
        if item.bit_depth == 16 and item.format == "tif":
            tifffile.imwrite(target, pixels, photometric="rgb", metadata=None)
        elif item.bit_depth == 16:
            _write_png16(target, pixels)
        else:
            Image.fromarray(pixels).save(target)
        written.append(
            {
                "name": item.name,
                "path": target,
                "width": width,
                "height": height,
                "channels": "RGBA" if "A" in channels else "RGB",
                "bit_depth": item.bit_depth,
            }
        )
    return {"maps": written, "sources": sorted(needed)}
