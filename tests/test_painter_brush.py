"""Brush input validation and partial-gesture cleanup."""

import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError
from test_texturing import Fixture

from cavi_substance_mcp.painter import native_brush as brush
from cavi_substance_mcp.painter import server
from cavi_substance_mcp.painter.client import PainterClient


class StrokeTests(unittest.TestCase):
    def test_points_preserve_pressure_and_normalized_coordinates(self):
        self.assertEqual(brush.points([[0.2, 0.3, 0.4], [0.7, 0.8, 1.0]]),
                         [(0.2, 0.3, 0.4), (0.7, 0.8, 1.0)])

    def test_invalid_paths_are_refused_before_input(self):
        for value in (None, [], [[0.5, 0.5, 1]], [[0, 0.5, 1]] * 2,
                      [[1, 0.5, 1]] * 2, [[0.5, 0.5, float('nan')]] * 2,
                      [[0.5, 0.5, True]] * 2, [[0.5, 0.5, -1]] * 2,
                      [[0.5, 0.5]] * 2, [[0.5, 0.5, 1]] * 129):
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                brush.points(value)

    def test_finite_gesture_always_releases_after_a_failed_drag(self):
        events = []

        def send(kind, point):
            events.append((kind, point))
            if kind == 6:
                raise RuntimeError('drag failed')

        with self.assertRaisesRegex(RuntimeError, 'outcome uncertain'):
            brush.gesture([(0.2, 0.3, 1), (0.4, 0.5, 0.5)], send, lambda: None)
        self.assertEqual([kind for kind, _ in events], [2, 1, 6, 2])

    def test_guard_failure_before_press_sends_no_input(self):
        events = []

        def refuse():
            raise RuntimeError('selection changed')

        with self.assertRaisesRegex(RuntimeError, 'selection changed'):
            brush.gesture([(0.2, 0.3, 1), (0.4, 0.5, 1)],
                          lambda *event: events.append(event), refuse)
        self.assertEqual(events, [])

    def test_one_stroke_is_a_finite_press_drag_release_sequence(self):
        events = []
        brush.gesture([(0.2, 0.3, 1), (0.4, 0.5, 0.7)],
                      lambda kind, point: events.append((kind, point)), lambda: None)
        self.assertEqual([kind for kind, _ in events], [2, 1, 6, 2])
        self.assertEqual(events[2][1][2], 0.7)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.context = {'token': 'ctx', 'expires': 100, 'lease_id': 'mine', 'input_serial': 3}
        self.state = {'context': self.context, 'input_serial': 3}
        self.params = {'context_token': 'ctx', 'lease_id': 'mine'}

    def test_context_is_consumed_before_input_and_cannot_replay(self):
        self.assertIs(brush.take_context(self.state, self.params, lambda: 10), self.context)
        self.assertIsNone(self.state['context'])
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            brush.take_context(self.state, self.params, lambda: 10)

    def test_foreign_lease_cannot_consume_context(self):
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            brush.take_context(self.state, dict(self.params, lease_id='foreign'), lambda: 10)
        self.assertIs(self.state['context'], self.context)

    def test_expiry_and_intervening_input_reject_without_a_stroke(self):
        for now, serial in ((100, 3), (10, 4)):
            self.state.update(context=self.context, input_serial=serial)
            with self.assertRaisesRegex(RuntimeError, 'changed|expired'):
                brush.take_context(self.state, self.params, lambda now=now: now)
            self.assertIsNone(self.state['context'])


class PaintLayerTests(Fixture):
    def test_paint_layer_is_editable_inspectable_and_deletable(self):
        group = self.edit('group', name='Surface')
        layer = self.edit('paint', name='Wear', parent_uid=group['uid'])
        self.assertIsInstance(self.sim.nodes[layer['uid']],
                              self.sim.module.layerstack.PaintLayerNode)
        tree = self.client.call('layer_stack', {'texture_set': 'Body'})
        self.assertEqual(tree['layers'][0]['layers'][0]['uid'], layer['uid'])
        self.edit('delete', uid=layer['uid'])
        self.assertNotIn(layer['uid'], self.sim.nodes)

    def test_duplicate_name_refuses_without_an_extra_layer(self):
        self.edit('paint', name='Wear')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.edit('paint', name='Wear')
        self.assertEqual(len(self.sim.layers), 1)


class BrushContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_actions_do_not_reach_painter(self):
        with patch.object(PainterClient, 'call') as call:
            for args in ({'action': 'prepare'}, {'action': 'stroke'},
                         {'action': 'stroke', 'context_token': 'ctx',
                          'points': [[.5, .5, 1]]}):
                with self.assertRaises(ToolError):
                    await server.mcp.call_tool('brush', args)
            call.assert_not_called()

    async def test_stroke_preserves_path_and_lease_at_transport_boundary(self):
        result = {'dispatched': True, 'points': 2, 'uid': 1,
                  'texture_set': 'Body', 'next': 'Inspect'}
        with patch.object(PainterClient, 'call', return_value=result) as call:
            await server.mcp.call_tool('brush', {'action': 'stroke', 'context_token': 'ctx',
                                              'points': [[.2, .3, .4], [.6, .7, 1.0]],
                                              'lease_id': 'mine'})
        args, kwargs = call.call_args
        self.assertEqual(args, ('brush', {'action': 'stroke', 'context_token': 'ctx',
                                        'points': [(0.2, 0.3, 0.4), (0.6, 0.7, 1.0)]}))
        self.assertEqual(kwargs['lease_id'], 'mine')


if __name__ == '__main__':
    unittest.main()
