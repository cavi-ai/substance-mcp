"""Shared file receipts for cross-application pipelines.

A receipt is ``manifest.json`` in a new output directory. Every path in it is relative
to the receipt root (the workspace), so a receipt verifies on another machine once the
workspace is copied there.

Schema 1 lists workspace files only. Schema 2 additionally lets ``inputs`` carry
``external`` rows for application-supplied resources that live outside the workspace
(for example a Designer built-in template)::

    {"role": "app-resource", "name": "templates/pbr", "sha256": "...", "tool_version": "16.0.7"}

External rows are identified by name and hash and are never looked up on disk when
verifying. Writers emit schema 2 only when a receipt actually contains such a row, so
consumers that only understand schema 1 keep working for every other receipt. Readers
accept both schemas.
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = 1
EXTERNAL_SCHEMA = 2
SUPPORTED_SCHEMAS = (SCHEMA, EXTERNAL_SCHEMA)
EXTERNAL_ROLE = "app-resource"
MANIFEST = "manifest.json"
MAX_CHAIN = 128
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ReceiptError(ValueError):
    """A receipt is unreadable, unsupported, or does not match its files."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(value, label: str) -> str:
    """Reject empty, absolute (POSIX or Windows) and parent-traversing paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReceiptError(f"{label} is empty or invalid")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (
        posix.is_absolute() or windows.drive or windows.root
        or ".." in posix.parts or ".." in windows.parts
    ):
        raise ReceiptError(f"{label} {value!r} is absolute or outside the receipt root")
    return value


def _is_external(row) -> bool:
    return isinstance(row, dict) and "role" in row


def _validate_external(row: dict, role: str, schema: int) -> None:
    if row.get("role") != EXTERNAL_ROLE:
        raise ReceiptError(f"Unsupported receipt {role} role: {row.get('role')!r}")
    if role != "inputs" or schema < EXTERNAL_SCHEMA:
        raise ReceiptError(
            f"Receipt {role} app-resource entries need schema {EXTERNAL_SCHEMA} inputs"
        )
    _relative(row.get("name"), "Receipt app-resource name")
    if not isinstance(row.get("sha256"), str) or not _SHA256.fullmatch(row["sha256"]):
        raise ReceiptError("Receipt app-resource entry needs a SHA-256")
    if row.get("tool_version") is not None and not isinstance(row["tool_version"], str):
        raise ReceiptError("Receipt app-resource tool_version must be a string")


def _validate(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ReceiptError("Receipt must be a JSON object")
    schema = manifest.get("schema")
    if type(schema) is not int or schema not in SUPPORTED_SCHEMAS:
        raise ReceiptError(f"Unsupported receipt schema: {schema!r}")
    for role in ("inputs", "outputs"):
        rows = manifest.get(role, [])
        if not isinstance(rows, list):
            raise ReceiptError(f"Receipt {role} must be a list")
        for row in rows:
            if _is_external(row):
                _validate_external(row, role, schema)
            elif not isinstance(row, dict) or not all(
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
    if not path.is_file():
        raise ReceiptError(f"Cannot record missing file: {path}")
    try:
        display = path.relative_to(root).as_posix()
    except ValueError:
        raise ReceiptError(
            f"Cannot record {path.name}: it is outside the receipt root; "
            "application resources are recorded with app_resource()"
        ) from None
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
        if path in seen:
            continue
        seen.add(path)
        found.append(record(path, root))
    return found


def app_resource(name: str, path: Path, tool_version: str | None = None) -> dict:
    """An ``inputs`` row for a file the application ships, identified by name and hash."""
    _relative(name, "App resource name")
    if "\\" in name:
        raise ReceiptError(f"App resource name {name!r} must use forward slashes")
    path = Path(path)
    if not path.is_file():
        raise ReceiptError(f"Cannot record missing application resource {name}")
    return {
        "role": EXTERNAL_ROLE,
        "name": name,
        "sha256": _sha256(path),
        "tool_version": tool_version,
    }


def build(app: str, operation: str, root: Path, inputs=(), outputs=(),
          parameters=None, started=None, seconds=None, version=None,
          warnings=None, provenance=None, external=()) -> dict:
    external = list(external)
    for row in external:
        _validate_external(row, "inputs", EXTERNAL_SCHEMA)
    return {
        "schema": EXTERNAL_SCHEMA if external else SCHEMA,
        "app": app,
        "operation": operation,
        "version": version,
        "started": started or datetime.now(UTC).isoformat(),
        "seconds": round(seconds, 3) if seconds is not None else None,
        "parameters": parameters or {},
        "inputs": [*records(inputs, root), *external],
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
    """Resolve an entry under the root; it may not be absolute or leave the root."""
    value = _relative(entry.get("path"), "Receipt path")
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ReceiptError(f"Receipt path {value!r} is absolute or outside the receipt root")
    return path


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
            if _is_external(entry):
                continue  # identified by name and hash; there is no workspace file to check
            _verify_entry(_path(entry, root), entry, role)
    return manifest


def find(manifest: dict, root: Path, path: Path, role: str = "outputs") -> dict:
    _validate(manifest)
    target = Path(path).resolve()
    root = Path(root).resolve()
    for entry in manifest.get(role, []):
        if not _is_external(entry) and _path(entry, root) == target:
            return entry
    raise ReceiptError(f"{target} is not listed in receipt {role}")


def verify_file(manifest: dict, root: Path, path: Path,
                role: str = "outputs") -> dict:
    entry = find(manifest, root, path, role)
    _verify_entry(Path(path).resolve(), entry, role)
    return entry


def _links(manifest: dict, root: Path) -> list[Path]:
    """Verify each provenance link of a manifest and return the linked manifests."""
    found = []
    for link in manifest.get("provenance", []):
        linked = _path(link["manifest"], root)
        _verify_entry(linked, link["manifest"], "provenance manifest")
        output = find(load(linked), root, _path(link["file"], root), "outputs")
        if (output["size"], output["sha256"]) != (
            link["file"]["size"], link["file"]["sha256"]
        ):
            raise ReceiptError(f"Provenance file does not match {linked}")
        _verify_entry(_path(link["file"], root), link["file"], "provenance file")
        found.append(linked)
    return found


def chain(path: Path, root: Path) -> list[dict]:
    """Verify a receipt and every receipt it links to, depth first, without recursion."""
    root = Path(root).resolve()
    seen, active, found = set(), set(), []
    # Entries are (manifest path, leaving?). Leaving pops the path off the active set.
    stack = [(Path(path).resolve(), False)]
    while stack:
        key, leaving = stack.pop()
        if leaving:
            active.discard(key)
            seen.add(key)
            continue
        if key in active:
            raise ReceiptError(f"Provenance cycle at {key}")
        if key in seen:
            continue
        if len(found) >= MAX_CHAIN:
            raise ReceiptError(f"Provenance chain has more than {MAX_CHAIN} manifests")
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
        stack.append((key, True))
        stack.extend((linked, False) for linked in reversed(_links(manifest, root)))
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
