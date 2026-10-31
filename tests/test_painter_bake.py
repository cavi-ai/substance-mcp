"""Bake bookkeeping: lost end events, grace period and cancellation."""

import unittest

from test_texturing import Fixture

from cavi_substance_mcp.client import PainterClient
from evals.painter_simulator import BakingProcessProgress, BakingStatus


class LostEndEventTests(Fixture):
    def start(self):
        return self.client.call("bake", {"maps": ["ao"]})

    def lose_the_end_event(self):
        self.start()
        self.sim.drop_bake_end = True
        self.sim.finish_bake(BakingStatus.Success)

    def test_idle_painter_with_a_lost_end_event_is_reconciled_after_the_grace_period(self):
        self.lose_the_end_event()
        # Inside the grace period the bake is still believed to run.
        self.assertTrue(self.waiter().call("status")["baking"])
        with self.assertRaisesRegex(RuntimeError, "baking"):
            self.edit("group", name="TooSoon")
        self.sim.age_bake(60)
        self.assertFalse(self.waiter().call("status")["baking"])
        status = self.client.call("bake_status")
        self.assertFalse(status["running"])
        self.assertFalse(status["busy"])
        self.assertTrue(status["reconciled"])
        self.assertEqual(status["status"], "Unknown")
        # Nothing stays locked out: edits, reads and a new bake all work again.
        self.edit("group", name="After")
        self.assertEqual(self.client.call("texture_sets")["texture_sets"][0]["name"], "Body")
        self.assertEqual(len(self.client.call("layer_stack", {"texture_set": "Body"})["layers"]), 1)
        self.assertTrue(self.start()["started"])
        self.assertFalse(self.client.call("bake_status")["reconciled"])

    def test_reads_are_not_locked_out_forever(self):
        self.lose_the_end_event()
        self.sim.age_bake(60)
        for operation, params in (("texture_sets", {}), ("layer_stack", {"texture_set": "Body"}),
                                  ("export_presets", {}), ("bake_settings", {})):
            with self.subTest(operation=operation):
                self.assertTrue(self.client.call(operation, params))

    def test_a_busy_painter_is_never_reconciled(self):
        self.start()
        self.sim.age_bake(600)
        self.assertTrue(self.sim.bake_running)
        self.assertTrue(self.waiter().call("status")["baking"])
        status = self.client.call("bake_status")
        self.assertTrue(status["running"])
        self.assertFalse(status["reconciled"])
        with self.assertRaisesRegex(RuntimeError, "baking"):
            self.edit("group", name="Blocked")

    def test_recent_progress_keeps_an_idle_looking_bake_alive(self):
        self.lose_the_end_event()
        self.sim.age_bake(60)
        self.sim.module.event.DISPATCHER._trigger(BakingProcessProgress(0.5))
        self.assertTrue(self.waiter().call("status")["baking"])
        self.sim.age_bake(60)
        self.assertFalse(self.waiter().call("status")["baking"])


class CancelTests(Fixture):
    def test_cancel_uses_the_stop_handle_kept_from_bake_start(self):
        self.client.call("bake", {"maps": ["ao"]})
        result = self.client.call("bake_cancel")
        self.assertTrue(result["cancel_requested"])
        self.assertTrue(self.sim.stop_source.stop_requested())
        status = self.client.call("bake_status")
        self.assertFalse(status["running"])
        self.assertEqual(status["status"], "Cancel")
        self.edit("group", name="After")
        self.assertNotIn("stop", str(status))

    def test_cancel_needs_the_lease(self):
        self.client.call("bake", {"maps": ["ao"]})
        with self.assertRaisesRegex(RuntimeError, "lease"):
            PainterClient(port=self.sim.port, workspace=self.root).call("bake_cancel")
        self.assertFalse(self.sim.stop_source.stop_requested())
        self.assertTrue(self.sim.bake_running)

    def test_cancel_without_a_running_bake_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "No bake is running"):
            self.client.call("bake_cancel")

    def test_cancel_after_the_bake_finished_is_refused(self):
        self.client.call("bake", {"maps": ["ao"]})
        self.sim.finish_bake(BakingStatus.Success)
        with self.assertRaisesRegex(RuntimeError, "No bake is running"):
            self.client.call("bake_cancel")

    def test_refused_stop_request_is_an_error_and_leaves_the_bake_running(self):
        self.client.call("bake", {"maps": ["ao"]})
        self.sim.stop_allowed = False
        with self.assertRaisesRegex(RuntimeError, "could not stop"):
            self.client.call("bake_cancel")
        self.assertTrue(self.client.call("bake_status")["running"])

    def test_cancel_during_a_lost_end_event_window_does_not_hide_the_bake(self):
        self.client.call("bake", {"maps": ["ao"]})
        self.sim.drop_bake_end = True
        self.sim.stop_allowed = False
        self.sim.finish_bake(BakingStatus.Success)
        with self.assertRaisesRegex(RuntimeError, "could not stop"):
            self.client.call("bake_cancel")
        self.sim.age_bake(60)
        with self.assertRaisesRegex(RuntimeError, "No bake is running"):
            self.client.call("bake_cancel")


if __name__ == "__main__":
    unittest.main()
