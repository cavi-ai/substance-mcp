"""Precision-preserving material texture IO shared by packing and asset handoffs."""
import struct
import zlib
from pathlib import Path

import numpy as np
import OpenEXR
import png
import tifffile
from PIL import Image

MAX_EDGE = 8192

def dimensions(path: Path) -> tuple[int, int]:
    if path.suffix.lower() == ".exr":
        with OpenEXR.File(str(path), header_only=True) as image:
            if len(image.parts) != 1:
                raise ValueError("Channel packing requires a single-part EXR")
            header = image.header()
            if (header["type"] not in (OpenEXR.scanlineimage, OpenEXR.tiledimage)
                    or not 1 <= len(header["channels"]) <= 4):
                raise ValueError("EXR packing requires a flat image with 1..4 channels")
            low, high = header["dataWindow"]
            width, height = (high - low + 1).tolist()
    elif path.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(path) as image:
            if len(image.pages) != 1:
                raise ValueError("Channel packing requires a single-page TIFF")
            page = image.pages[0]
            if not 1 <= page.samplesperpixel <= 4:
                raise ValueError("TIFF packing requires 1..4 samples per pixel")
            width, height = page.imagewidth, page.imagelength
    else:
        with Image.open(path) as image:
            width, height = image.size
    if not 1 <= width <= MAX_EDGE or not 1 <= height <= MAX_EDGE:
        raise ValueError(f"{path.name} exceeds {MAX_EDGE} px or has invalid dimensions")
    return width, height


def precise_load(path: Path):
    dimensions(path)
    if path.suffix.lower() == ".exr":
        with OpenEXR.File(str(path), separate_channels=True) as image:
            channels = image.channels()
            names = [n for n in ("R", "G", "B", "A") if n in channels]
            if not names and len(channels) == 1:
                names = list(channels)
            if not names or (len(names) > 1 and names[:3] != ["R", "G", "B"]):
                raise ValueError("EXR requires RGB[A] or one scalar channel")
            data = np.stack([channels[n].pixels for n in names], axis=-1)
            if data.dtype.kind != "f":
                raise ValueError("EXR source channels must use half or full float")
    else:
        with tifffile.TiffFile(path) as image:
            page = image.pages[0]
            # Only grayscale and RGB[A], with no palette/CMYK interpretation.
            if int(page.photometric) not in (1, 2):
                raise ValueError("TIFF source must be grayscale or RGB[A]")
            data = page.asarray()
            if int(page.planarconfig) == 2:
                data = np.moveaxis(data, 0, -1)
    if data.ndim == 2:
        data = data[:, :, None]
    if data.ndim != 3 or data.shape[2] not in (1, 2, 3, 4):
        raise ValueError("Source must have 1..4 interleaved channels")
    if data.dtype.kind == "u" and data.dtype.itemsize in (1, 2):
        return data.astype(np.float32) / np.iinfo(data.dtype).max
    if data.dtype.kind == "f":
        return data.astype(np.float32)
    raise ValueError("Source requires unsigned 8/16-bit or floating samples")


def load(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".exr", ".tif", ".tiff"):
        return precise_load(path)
    with path.open("rb") as stream:
        header = stream.read(26)
        if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) == 26 and header[24] == 16:
            stream.seek(0)
            width, height, rows, info = png.Reader(file=stream).read()
            if width > MAX_EDGE or height > MAX_EDGE:
                raise ValueError(f"{path.name} exceeds {MAX_EDGE} px")
            pixels = np.empty((height, width * info["planes"]), dtype=np.float32)
            for index, row in enumerate(rows):
                pixels[index] = row
            # Pillow decodes multichannel PNG16 as 8-bit. Keep every source sample.
            return pixels.reshape(height, width, info["planes"]) / 65535.0
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


def write_png16(path: Path, pixels: np.ndarray):
    height, width, channels = pixels.shape
    color = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    rows = pixels.astype(">u2").reshape(height, width * channels)
    raw = b"".join(b"\0" + row.tobytes() for row in rows)

    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 16, color, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def describe(path: Path) -> dict:
    """Read original channel storage, without inferring color interpretation."""
    width, height = dimensions(path)
    suffix = path.suffix.lower()
    if suffix == ".exr":
        # Header-only File channels do not retain a usable pixel array for type().
        # InputFile exposes the on-disk channel storage directly without decoding pixels.
        image = OpenEXR.InputFile(str(path))
        try:
            channels = image.header()["channels"]
            types = [channel.type.v for channel in channels.values()]
            if any(value == 0 for value in types):
                raise ValueError("Material EXR channels must use half or full float")
            depth = 32 if any(value == 2 for value in types) else 16
            return {"format": "exr", "bit_depth": depth, "sample_type": "float",
                    "width": width, "height": height, "channels": len(channels)}
        finally:
            image.close()
    if suffix in (".tif", ".tiff"):
        with tifffile.TiffFile(path) as image:
            page = image.pages[0]
            return {"format": "tiff", "bit_depth": page.dtype.itemsize * 8,
                    "sample_type": "float" if page.dtype.kind == "f" else "uint",
                    "width": width, "height": height, "channels": page.samplesperpixel}
    with path.open("rb") as stream:
        width, height, _, info = png.Reader(file=stream).read()
        return {"format": "png", "bit_depth": info["bitdepth"], "sample_type": "uint",
                "width": width, "height": height, "channels": info["planes"]}
