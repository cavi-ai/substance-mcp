"""Claim an explicit fixture-owned session for existing operation tests."""

import asyncio
import os
from unittest.mock import patch

from cavi_substance_mcp.painter import server
from cavi_substance_mcp.painter.client import PainterClient


class OwnedPainterClient(PainterClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        state = super().call("session_control", {"action": "inspect"})
        self.lease_id = super().call("session_control", {
            "action": "claim", "owner": "operation-fixture",
            "expected_project": state["project_id"],
        })["lease_id"]

    def call(self, operation, params=None):
        return super().call(operation, params, lease_id=self.lease_id)


def wait_ready(port, workspace, timeout_seconds=10, interval=0.01):
    """Poll Painter the way the ``project`` tool's wait_ready does and return the status."""
    environment = {"CAVI_PAINTER_PORT": str(port)}
    if workspace is not None:
        environment["CAVI_SUBSTANCE_WORKSPACE"] = str(workspace)
    with patch.dict(os.environ, environment):
        return asyncio.run(server.wait_until_ready(timeout_seconds, interval))
