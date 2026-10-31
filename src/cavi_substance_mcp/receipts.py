"""Shared file receipts for cross-application pipelines."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
MANIFEST = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def record(path: Path, root: Path) -> dict:
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        display = path.relative_to(root).as_posix()
    except ValueError:
        display = str(path)
    return {
        "path": display,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def records(paths, root: Path) -> list[dict]:
    seen, found = set(), []
    root = Path(root).resolve()
    for item in paths:
        path = Path(item).resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        found.append(record(path, root))
    return found


def build(app: str, operation: str, root: Path, inputs=(), outputs=(),
          parameters=None, started=None, seconds=None, version=None,
          warnings=None) -> dict:
    return {
        "schema": SCHEMA,
        "app": app,
        "operation": operation,
        "version": version,
        "started": started or datetime.now(UTC).isoformat(),
        "seconds": round(seconds, 3) if seconds is not None else None,
        "parameters": parameters or {},
        "inputs": records(inputs, root),
        "outputs": records(outputs, root),
        "warnings": list(warnings or []),
    }


def write(directory: Path, manifest: dict) -> Path:
    path = Path(directory) / MANIFEST
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path
