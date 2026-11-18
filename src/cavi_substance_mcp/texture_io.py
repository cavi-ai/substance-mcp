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
            reader = png.Reader(file=stream)
            reader.preamble()
            if reader.width > MAX_EDGE or reader.height > MAX_EDGE:
                raise ValueError(f"{path.name} exceeds {MAX_EDGE} px")
            width, height, flat, info = reader.read_flat()
            # Pillow decodes multichannel PNG16 as 8-bit. Keep every source sample.
            samples = np.frombuffer(flat, dtype=np.uint16)
            return np.divide(samples, 65535.0, dtype=np.float32).reshape(
                height, width, info["planes"])
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


FILTER_ROWS = 64


def _filter_rows(rows: np.ndarray, above: np.ndarray, step: int) -> np.ndarray:
    """Each row as its PNG filter type byte and filtered bytes, using the filter with the
    least sum of absolute signed bytes; ``above`` is the row before the first one and
    ``step`` the bytes per pixel."""
    rows = rows.astype(np.int16)
    up = np.concatenate([above[None].astype(np.int16), rows[:-1]])
    left, corner = np.zeros_like(rows), np.zeros_like(rows)
    left[:, step:], corner[:, step:] = rows[:, :-step], up[:, :-step]
    guess = left + up - corner
    to_left, to_up, to_corner = (np.abs(guess - item) for item in (left, up, corner))
    paeth = np.where((to_left <= to_up) & (to_left <= to_corner), left,
                     np.where(to_up <= to_corner, up, corner))
    candidates = (np.stack([rows, rows - left, rows - up, rows - ((left + up) >> 1),
                            rows - paeth]) & 0xFF).astype(np.uint8)
    cost = np.abs(candidates.view(np.int8).astype(np.int16)).sum(axis=2, dtype=np.int64)
    choice = cost.argmin(axis=0)
    chosen = candidates[choice, np.arange(len(rows))]
    return np.concatenate([choice[:, None].astype(np.uint8), chosen], axis=1)


def write_png16(path: Path, pixels: np.ndarray):
    height, width, channels = pixels.shape
    color = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    samples = pixels.astype(">u2").reshape(height, width * channels).view(np.uint8)
    compressor = zlib.compressobj(6)
    parts, above = [], np.zeros(samples.shape[1], dtype=np.uint8)
    for start in range(0, height, FILTER_ROWS):
        rows = samples[start:start + FILTER_ROWS]
        parts.append(compressor.compress(_filter_rows(rows, above, channels * 2).tobytes()))
        above = rows[-1]
    parts.append(compressor.flush())

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
        + chunk(b"IDAT", b"".join(parts))
        + chunk(b"IEND", b"")
    )
