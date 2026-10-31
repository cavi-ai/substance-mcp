"""Shared channel-packing contract: validation and numpy/Pillow writer."""

import re
import struct
import zlib
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

USAGE = r"[A-Za-z][A-Za-z0-9_]{0,63}"
SOURCE = rf"^(?:1-)?{USAGE}$|^(?:0(?:\.[0-9]{{1,6}})?|1(?:\.0{{1,6}})?|\.[0-9]{{1,6}})$"
NAME = r"^[A-Za-z0-9_-]{1,96}$"
MAX_EDGE = 8192
# Decoded sources are float32 RGBA at most; refuse a pack that would need more than this.
MAX_DECODED_BYTES = 2 * 1024**3
READABLE = {".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg"}

Source = Annotated[str, StringConstraints(pattern=SOURCE)]
UsageName = Annotated[str, StringConstraints(pattern=f"^{USAGE}$")]


class PackMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(pattern=NAME)]
    format: Literal["png", "tga", "tif"] = "png"
    bit_depth: Annotated[Literal[8, 16], Field(description="16: png only.")] = 8
    channels: Annotated[
        dict[Literal["R", "G", "B", "A", "RGB"], Source],
        Field(description="Source: usage, 1-usage, or constant 0..1."),
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
        if item.bit_depth == 16 and item.format != "png":
            raise ValueError(f"{item.name}: bit_depth 16 requires png")
        if item.name.lower() in names:
            raise ValueError(f"Duplicate map name: {item.name}")
        names.add(item.name.lower())


def load(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        width, height = image.size
        if width > MAX_EDGE or height > MAX_EDGE:
            raise ValueError(f"{path.name} exceeds {MAX_EDGE} px")
        mode = image.mode
        if mode in ("I;16", "I;16B", "I;16L", "I"):
            data = np.asarray(image).astype(np.float32) / 65535.0
        elif mode == "F":
            data = np.asarray(image, dtype=np.float32)
        else:
            if mode not in ("L", "LA", "RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() or mode == "P" else "RGB")
            data = np.asarray(image).astype(np.float32) / 255.0
    data = np.clip(data, 0.0, 1.0)
    return data[:, :, None] if data.ndim == 2 else data


def _plane(data: np.ndarray, rgb: bool) -> np.ndarray:
    if rgb:
        return data[:, :, :3] if data.shape[2] >= 3 else np.repeat(data[:, :, :1], 3, axis=2)
    return data[:, :, :1]


def _write_png16(path: Path, pixels: np.ndarray):
    height, width, channels = pixels.shape
    color = {1: 0, 3: 2, 4: 6}[channels]
    rows = pixels.astype(">u2").reshape(height, width * channels)
    raw = b"".join(b"\0" + row.tobytes() for row in rows)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 16, color, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


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
                        f"{item.name}: source usage '{usage}' is missing and has no default")
                if usage in sources:
                    needed.add(usage)
    if not needed:
        raise ValueError("Packing needs at least one image source")
    decoded = 0
    for usage in sorted(needed):
        with Image.open(sources[usage]) as image:
            decoded += image.size[0] * image.size[1] * 4 * 4
    if decoded > MAX_DECODED_BYTES:
        raise ValueError(
            f"Sources need {decoded / 1024**3:.1f} GiB decoded; the limit is "
            f"{MAX_DECODED_BYTES / 1024**3:.0f} GiB. Pack fewer or smaller maps per call.")
    last_use = {}
    for index, item in enumerate(spec.maps):
        for source in item.channels.values():
            last_use[source.removeprefix("1-")] = index
    size = None
    for usage in sorted(needed):
        cache[usage] = load(sources[usage])
        shape = cache[usage].shape[:2]
        if size is None:
            size = shape
        elif shape != size:
            raise ValueError(
                f"Source resolutions differ: {usage} is {shape[1]}x{shape[0]}, "
                f"expected {size[1]}x{size[0]}")
    height, width = size

    def resolve(source: str, rgb: bool) -> np.ndarray:
        invert = source.startswith("1-") and re.fullmatch(USAGE, source[2:])
        usage = source[2:] if invert else source
        if not re.fullmatch(USAGE, usage):
            value = np.float32(float(usage))
        elif usage in cache:
            value = _plane(cache[usage], rgb)
        else:
            value = np.float32(spec.defaults[usage])
        value = 1.0 - value if invert else value
        return np.broadcast_to(value, (height, width, 3 if rgb else 1))

    written = []
    for index, item in enumerate(spec.maps):
        channels = item.channels
        planes = ([(channels["RGB"], True)] if "RGB" in channels
                  else [(channels[key], False) for key in "RGB"])
        if "A" in channels:
            planes.append((channels["A"], False))
        scale, dtype = (65535.0, np.uint16) if item.bit_depth == 16 else (255.0, np.uint8)
        # Quantize one plane at a time into the integer image instead of building a
        # float32 copy of the whole map.
        pixels = np.empty((height, width, 4 if "A" in channels else 3), dtype=dtype)
        offset = 0
        for source, rgb in planes:
            depth = 3 if rgb else 1
            pixels[:, :, offset:offset + depth] = np.rint(resolve(source, rgb) * scale)
            offset += depth
        for usage in [key for key, last in last_use.items() if last == index]:
            cache.pop(usage, None)
        target = directory / f"{item.name}.{item.format}"
        if item.bit_depth == 16:
            _write_png16(target, pixels)
        else:
            Image.fromarray(pixels).save(target)
        written.append({"name": item.name, "path": target, "width": width, "height": height,
                        "channels": "RGBA" if "A" in channels else "RGB",
                        "bit_depth": item.bit_depth})
    return {"maps": written, "sources": sorted(needed)}
