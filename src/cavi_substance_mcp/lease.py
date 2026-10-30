"""Expose the self-contained Painter payload's ownership controller.

The package guard also keeps this file harmless when an already-running MCP
client still prepends it to the remote payload.
"""

__all__ = ["Lease", "time"]

if globals().get("__package__"):
    from .painter_operations import Lease, time
