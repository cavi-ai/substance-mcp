"""The workspace root and the path rules every host-side tool follows.

Standard library only: the Sampler operations that run inside Sampler's embedded Python
import this module, and it must not import anything else from this package.

Painter runs painter_operations.py as a self-contained payload and cannot import this
package, so that file keeps its own copy of ``input_file``, ``output_path`` and
``remove_tree``. tests/test_workspace.py checks that the two copies accept and refuse the
same paths.
"""

import os
import shutil
import stat
import sys
from pathlib import Path

ENV = "CAVI_SUBSTANCE_WORKSPACE"
INPUT_MESSAGE = "Input must be a supported file inside the workspace"
OUTPUT_MESSAGE = "Output must remain inside the workspace"


def default_root() -> Path:
    return Path.home() / "Documents" / "SubstanceMCP"


def root(explicit=None, *, create: bool = False) -> Path:
    """The resolved workspace root: ``explicit``, else ``$CAVI_SUBSTANCE_WORKSPACE``, else the default.

    Nothing is touched unless ``create`` is true, so read-only operations can report a
    workspace that does not exist yet. With ``create`` the default is made on first use,
    but a root chosen explicitly (argument or environment) must already be a directory:
    a mistyped path is refused instead of silently becoming a new empty workspace.
    """
    if explicit is not None and str(explicit) != "":
        path = Path(explicit).expanduser().resolve()
        if create:
            _require_directory(path, f"Workspace {path}", " Create it first.")
        return path
    if os.environ.get(ENV):
        path = Path(os.environ[ENV]).expanduser().resolve()
        if create:
            _require_directory(
                path, f"{ENV} points to {path}, which",
                f" Create it, or unset {ENV} to use the default workspace {default_root()}.")
        return path
    path = default_root()
    if create:
        if path.exists() and not path.is_dir():
            raise ValueError(f"Workspace {path} is not a directory.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create the default workspace {path}: {exc}") from None
    return path.resolve()


def _require_directory(path: Path, subject: str, hint: str = "") -> None:
    if not path.is_dir():
        state = "is not a directory" if path.exists() else "does not exist"
        raise ValueError(f"{subject} {state}.{hint}")


def existing(path) -> Path:
    """Resolve a workspace root that must already be a directory; never creates it."""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f"Workspace {path} does not exist.") from None
    _require_directory(resolved, f"Workspace {resolved}")
    return resolved


def existing_root(explicit=None) -> Path:
    """The configured root for an operation that reads from the workspace."""
    return existing(root(explicit))


def input_file(workspace, name, extensions) -> Path:
    """Resolve ``name`` to an existing file inside the workspace with a supported suffix.

    Symbolic links are followed before the containment check, so a link that points out
    of the workspace is refused. ``extensions`` holds lower-case suffixes with the dot.
    """
    base = existing(workspace)
    if not isinstance(name, str):
        raise ValueError(INPUT_MESSAGE)
    try:
        path = (base / name).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError(INPUT_MESSAGE) from None
    if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() not in extensions:
        raise ValueError(INPUT_MESSAGE)
    return path


def output_path(workspace, name, *, parents: bool = False) -> Path:
    """Validate a new output directory inside the workspace without creating it.

    It may not be the workspace itself, leave it (including through links) or exist, and
    its parent must exist unless ``parents`` is true, in which case the missing parents
    are created.
    """
    base = existing(workspace)
    if not isinstance(name, str):
        raise ValueError(OUTPUT_MESSAGE)
    try:
        path = (base / name).resolve()
    except (OSError, RuntimeError, ValueError):
        raise ValueError(OUTPUT_MESSAGE) from None
    if path == base or not path.is_relative_to(base):
        raise ValueError(OUTPUT_MESSAGE)
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("Output directory already exists; choose a new directory")
    if not path.parent.is_dir():
        raise ValueError("Output parent directory must exist")
    return path


def new_output_dir(workspace, name, *, parents: bool = False) -> Path:
    """Create the new output directory ``name``; ``mkdir`` itself refuses a concurrent creator."""
    path = output_path(workspace, name, parents=parents)
    path.mkdir()
    return path


def rel(workspace, path) -> str:
    """``path`` relative to the workspace, with forward slashes."""
    return Path(path).resolve().relative_to(existing(workspace)).as_posix()


def _unlock(tree: Path):
    """An rmtree error handler: clear read-only bits, then retry once.

    Windows refuses to delete read-only files, and a directory without its write bit
    refuses to give up its entries. Only entries inside ``tree`` are touched, and never
    through a symbolic link.
    """
    tree = Path(tree)

    def handler(function, target, _error):
        path = Path(target)
        try:
            if not path.is_symlink():
                os.chmod(path, stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR)
            parent = path.parent
            if parent != path and (parent == tree or tree in parent.parents):
                os.chmod(parent, stat.S_IRWXU)
            function(target)
        except OSError:
            pass

    return handler


def remove_tree(path) -> list[str]:
    """Remove a directory tree; return what is still there afterwards (empty on success)."""
    path = Path(path)
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_unlock(path))
        else:
            shutil.rmtree(path, onerror=_unlock(path))
    except OSError:
        pass
    if not path.exists():
        return []
    try:
        left = sorted((str(item) for item in path.rglob("*")), reverse=True)
    except OSError:
        left = []
    return [*left, str(path)]
