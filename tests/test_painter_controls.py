"""Painter source precision and brush setting validation."""
import unittest

from cavi_substance_mcp.painter import native_brush


class BrushSettingsTests(unittest.TestCase):
    def test_valid_controls_keep_native_units(self):
        self.assertEqual(native_brush.check_settings({'size':12.5,'flow':75,'opacity':80,
                                                      'spacing':10}),
                         {'size':12.5,'flow':75,'opacity':80,'spacing':10})

    def test_unknown_nonfinite_and_out_of_range_controls_are_rejected(self):
        for value in ({'size':0},{'size':float('nan')},{'flow':101},{'opacity':True},
                      {'spacing':0},{'code':'bad'}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                native_brush.check_settings(value)

class SourcePrecisionTests(unittest.TestCase):
    def test_creation_upgrades_eight_bit_and_preserves_higher_precision(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        from cavi_substance_mcp.painter import operations

        class Channel:
            def __init__(self, depth, color):
                self.depth, self.color, self.changes = depth, color, []
            def bit_depth(self):
                return self.depth
            def is_color(self):
                return self.color
            def edit(self, value):
                self.changes.append(value)

        rgb, scalar, hdr = Channel(8, True), Channel(8, False), Channel(32, True)
        stack = SimpleNamespace(all_channels=lambda: {'color':rgb,'roughness':scalar,'hdr':hdr})
        material = SimpleNamespace(all_stacks=lambda:[stack])
        module = SimpleNamespace(all_texture_sets=lambda:[material],
                                 ChannelFormat=SimpleNamespace(RGB16='RGB16',L16='L16'))
        with patch.dict(sys.modules, substance_painter=SimpleNamespace(textureset=module)), \
                patch.object(operations,'_texture_sets',return_value={'texture_sets':[]}):
            operations._initialize_channels(None,{},None,None)
        self.assertEqual(rgb.changes,['RGB16'])
        self.assertEqual(scalar.changes,['L16'])
        self.assertEqual(hdr.changes,[])


class ChannelContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_height_reaches_the_native_control_without_clamping(self):
        from unittest.mock import patch

        from cavi_substance_mcp.painter import server
        from cavi_substance_mcp.painter.client import PainterClient

        with patch.object(PainterClient, 'call', return_value={'settings': {}}) as call:
            await server.mcp.call_tool('brush', {'action': 'settings',
                'channel_values': {'Height': -.234567}, 'enabled_channels': {'Height': True},
                'lease_id': 'owned'})
        self.assertEqual(call.call_args.args[1]['channel_values']['Height'], -.234567)

    async def test_nonfinite_channel_values_never_reach_the_native_control(self):
        from unittest.mock import patch

        from mcp.server.mcpserver.exceptions import ToolError

        from cavi_substance_mcp.painter import server
        from cavi_substance_mcp.painter.client import PainterClient

        with patch.object(PainterClient, 'call') as call:
            with self.assertRaises(ToolError):
                await server.mcp.call_tool('brush', {'action': 'settings',
                    'channel_values': {'Height': float('nan')}, 'lease_id': 'owned'})
            call.assert_not_called()
