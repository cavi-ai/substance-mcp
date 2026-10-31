"""Shared file receipts for cross-application pipelines."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
MANIFEST = "manifest.json"


class ReceiptError(ValueError):
    """A receipt is unreadable, unsupported, or does not match its files."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ReceiptError("Receipt must be a JSON object")
    if manifest.get("schema") != SCHEMA:
        raise ReceiptError(f"Unsupported receipt schema: {manifest.get('schema')!r}")
    for role in ("inputs", "outputs"):
        rows = manifest.get(role, [])
        if not isinstance(rows, list):
            raise ReceiptError(f"Receipt {role} must be a list")
        for row in rows:
            if not isinstance(row, dict) or not all(
                key in row for key in ("path", "size", "sha256")
            ):
                raise ReceiptError(f"Receipt {role} entry is incomplete")
    provenance = manifest.get("provenance", [])
    if not isinstance(provenance, list):
        raise ReceiptError("Receipt provenance must be a list")
    for link in provenance:
        if not isinstance(link, dict):
            raise ReceiptError("Receipt provenance entry must be an object")
        for key in ("manifest", "file"):
            entry = link.get(key)
            if not isinstance(entry, dict) or not all(
                field in entry for field in ("path", "size", "sha256")
            ):
                raise ReceiptError(f"Receipt provenance {key} entry is incomplete")
    return manifest


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
          warnings=None, provenance=None) -> dict:
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
        "provenance": list(provenance or []),
    }


def load(path: Path) -> dict:
    path = Path(path)
    try:
        return _validate(json.loads(path.read_text(encoding="utf-8")))
    except ReceiptError:
        raise
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"Cannot read receipt {path}: {exc}") from exc


def _path(entry: dict, root: Path) -> Path:
    value = entry.get("path")
    if not isinstance(value, str) or not value:
        raise ReceiptError("Receipt path is empty or invalid")
    path = Path(value)
    return (path if path.is_absolute() else Path(root) / path).resolve()


def _verify_entry(path: Path, entry: dict, label: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReceiptError(f"Missing {label} file {path}: {exc}") from exc
    if size != entry["size"]:
        raise ReceiptError(f"Size mismatch for {path}: {size} != {entry['size']}")
    if _sha256(path) != entry["sha256"]:
        raise ReceiptError(f"SHA-256 mismatch for {path}")


def verify(manifest: dict, root: Path, roles=("inputs", "outputs")) -> dict:
    _validate(manifest)
    root = Path(root).resolve()
    for role in roles:
        for entry in manifest.get(role, []):
            _verify_entry(_path(entry, root), entry, role)
    return manifest


def find(manifest: dict, root: Path, path: Path, role: str = "outputs") -> dict:
    _validate(manifest)
    target = Path(path).resolve()
    root = Path(root).resolve()
    for entry in manifest.get(role, []):
        if _path(entry, root) == target:
            return entry
    raise ReceiptError(f"{target} is not listed in receipt {role}")


def verify_file(manifest: dict, root: Path, path: Path,
                role: str = "outputs") -> dict:
    entry = find(manifest, root, path, role)
    _verify_entry(Path(path).resolve(), entry, role)
    return entry


def chain(path: Path, root: Path) -> list[dict]:
    root = Path(root).resolve()
    seen, active, found = set(), set(), []

    def visit(manifest_path: Path):
        key = Path(manifest_path).resolve()
        if key in active:
            raise ReceiptError(f"Provenance cycle at {key}")
        if key in seen:
            return
        active.add(key)
        manifest = load(key)
        verify(manifest, root)
        found.append({
            "path": _display(key, root),
            "app": manifest.get("app"),
            "operation": manifest.get("operation"),
            "inputs": len(manifest.get("inputs", [])),
            "outputs": len(manifest.get("outputs", [])),
        })
        for link in manifest.get("provenance", []):
            linked = _path(link["manifest"], root)
            _verify_entry(linked, link["manifest"], "provenance manifest")
            output = find(load(linked), root, _path(link["file"], root), "outputs")
            if (output["size"], output["sha256"]) != (
                link["file"]["size"], link["file"]["sha256"]
            ):
                raise ReceiptError(f"Provenance file does not match {linked}")
            _verify_entry(_path(link["file"], root), link["file"], "provenance file")
            visit(linked)
        active.remove(key)
        seen.add(key)

    visit(Path(path))
    return found


def _display(path: Path, root: Path) -> str:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def load_and_verify(path: Path, root: Path, roles=("inputs", "outputs")) -> dict:
    return verify(load(path), root, roles)


def write(directory: Path, manifest: dict) -> Path:
    path = Path(directory) / MANIFEST
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return path
