"""Expose the self-contained Painter payload's ownership controller."""

__all__ = ["BREAK_IDLE_SECONDS", "LEASE_ATTR", "Lease", "shared_lease", "time"]

if globals().get("__package__"):
    from .painter_operations import BREAK_IDLE_SECONDS, LEASE_ATTR, Lease, shared_lease, time
