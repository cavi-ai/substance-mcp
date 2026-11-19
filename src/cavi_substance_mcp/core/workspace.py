"""The Substance workspace root and the directory helpers every tool shares.

The path rules and the output and removal helpers are ``cavi_mcp_core.workspace``. Standard
library otherwise: inside Painter and Sampler, ``core.embedded.bootstrap`` installs that
module before this one is imported.
"""

from pathlib import Path

from cavi_mcp_core import workspace as shared
from cavi_mcp_core.workspace import INPUT_MESSAGE as INPUT_MESSAGE
from cavi_mcp_core.workspace import OUTPUT_MESSAGE as OUTPUT_MESSAGE
from cavi_mcp_core.workspace import existing as existing
from cavi_mcp_core.workspace import input_file as input_file
from cavi_mcp_core.workspace import new_output_dir as new_output_dir
from cavi_mcp_core.workspace import new_output_directory as new_output_directory
from cavi_mcp_core.workspace import output_path as output_path
from cavi_mcp_core.workspace import rel as rel
from cavi_mcp_core.workspace import remove_scratch as remove_scratch
from cavi_mcp_core.workspace import remove_tree as remove_tree

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
