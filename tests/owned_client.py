"""Claim an explicit fixture-owned session for existing operation tests."""

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
