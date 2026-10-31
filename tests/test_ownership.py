import tempfile
import unittest
from pathlib import Path

from cavi_substance_mcp.painter.client import PainterClient
from evals.painter_simulator import PainterSimulator


class OwnershipTests(unittest.TestCase):
    def test_cached_clients_can_execute_the_self_contained_status_payload(self):
        from cavi_substance_mcp.painter.client import SOURCE

        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("dirty-unsaved")
            namespace = {}
            exec(SOURCE, namespace)  # noqa: S102 -- trusted payload in simulator
            reply = namespace["execute"]("status", {}, folder)
            self.assertTrue(reply["ok"])
            result = reply["result"]
            self.assertTrue(result["connected"])
            self.assertTrue(result["needs_saving"])
            self.assertTrue(result["open"])

    def test_two_independent_clients_cannot_edit_each_others_unsaved_project(self):
        with tempfile.TemporaryDirectory() as folder, PainterSimulator(Path(folder)) as simulator:
            simulator.setup("dirty-unsaved")
            first = PainterClient(port=simulator.port, workspace=folder)
            second = PainterClient(port=simulator.port, workspace=folder)
            state = first.call("session_control", {"action": "inspect"})
            token = first.call("session_control", {
                "action": "claim", "owner": "agent-A", "expected_project": state["project_id"],
            })["lease_id"]
            args = {"texture_set": "Body", "name": "Owned", "base_color": [0.2, 0.5, 0.8]}
            with self.assertRaisesRegex(RuntimeError, "lease"):
                second.call("create_fill_layer", args)
            with self.assertRaisesRegex(RuntimeError, "agent-A"):
                second.call("session_control", {
                    "action": "claim", "owner": "agent-B", "expected_project": state["project_id"],
                })
            first.call("create_fill_layer", args, lease_id=token)
            self.assertEqual(len(simulator.layers), 1)
            simulator.project_id = "another-unsaved-project"
            with self.assertRaisesRegex(RuntimeError, "project changed"):
                first.call("close_project", {"expected_file": None, "discard_unsaved": True},
                           lease_id=token)
            self.assertTrue(second.call("status")["open"])
            self.assertTrue(second.call("status")["needs_saving"])
            self.assertEqual(len(simulator.layers), 1)
