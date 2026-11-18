"""The Substance workspace root and the directory helpers every tool shares.

The path rules are ``cavi_mcp_core.workspace``. Standard library otherwise: inside Painter
and Sampler, ``core.embedded.bootstrap`` installs that module before this one is imported.
"""

import os
import shutil
import stat
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path

from cavi_mcp_core import workspace as shared
from cavi_mcp_core.workspace import INPUT_MESSAGE as INPUT_MESSAGE
from cavi_mcp_core.workspace import OUTPUT_MESSAGE as OUTPUT_MESSAGE
from cavi_mcp_core.workspace import existing as existing
from cavi_mcp_core.workspace import input_file as input_file
from cavi_mcp_core.workspace import new_output_dir as new_output_dir
from cavi_mcp_core.workspace import output_path as output_path
from cavi_mcp_core.workspace import rel as rel

ENV = "CAVI_SUBSTANCE_WORKSPACE"


def default_root() -> Path:
    return Path.home() / "Documents" / "SubstanceMCP"


def root(explicit=None, *, create: bool = False) -> Path:
    """The resolved workspace root: ``explicit``, else ``$CAVI_SUBSTANCE_WORKSPACE``, else the
    default. Only ``create`` makes anything, and only the default."""
    return shared.root(ENV, explicit, default=default_root(), create=create)


def existing_root(explicit=None) -> Path:
    """The configured root for an operation that reads from the workspace."""
    return shared.existing_root(ENV, explicit, default=default_root())


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


# How many leftover entries an error message names before it counts the rest.
LEFTOVERS_SHOWN = 10


def _listing(left: list[str]) -> str:
    more = len(left) - LEFTOVERS_SHOWN
    return ", ".join(left[:LEFTOVERS_SHOWN]) + (f" and {more} more" if more > 0 else "")


def remove_scratch(path) -> None:
    """Remove a scratch or abandoned directory that has no error to carry a report.

    ``remove_tree``, but anything that is still there afterwards is reported as a
    ``RuntimeWarning`` instead of being dropped.
    """
    left = remove_tree(path)
    if left:
        warnings.warn(f"could not remove {path}: {_listing(left)}", RuntimeWarning, stacklevel=2)


@contextmanager
def new_output_directory(workspace, name, *, parents: bool = False):
    """Create the new output directory ``name`` and remove it again if the body raises.

    This is ``new_output_dir`` for an operation that fills the directory: whatever
    ``BaseException`` ends the body, cancellation included, the directory and everything in
    it are removed before the error continues. A directory that already existed is never
    touched, because it is refused before the body runs.

    When something cannot be removed, the leftovers are reported with the error. An
    ``Exception`` is replaced by a ``RuntimeError`` (chained to the original) whose text
    names them, because that text is what the tool's caller sees; any other
    ``BaseException`` is re-raised as it is, with the leftovers as a note.
    """
    path = new_output_dir(workspace, name, parents=parents)
    try:
        yield path
    except BaseException as exc:
        left = remove_tree(path)
        if not left:
            raise
        note = f"could not remove the incomplete output: {_listing(left)}"
        if not isinstance(exc, Exception):
            exc.add_note(note)
            raise
        reason = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
        raise RuntimeError(f"{reason}; {note}") from exc
