"""Implicit material output must retain high precision; low precision is explicit."""

import tempfile
import unittest
from pathlib import Path

import png

from cavi_substance_mcp.designer import server
from cavi_substance_mcp.packing import PackMap, PackSpec, pack
from cavi_substance_mcp.painter import operations


class PrecisionDefaults(unittest.IsolatedAsyncioTestCase):
    async def test_designer_render_schema_defaults_to_sixteen_bits(self):
        tools = await server.mcp.list_tools()
        material = next(tool for tool in tools if tool.name == 'material')
        self.assertEqual(material.input_schema['properties']['bit_depth']['default'], '16')

    def test_shared_channel_pack_defaults_to_sixteen_bits(self):
        item = PackMap(name='Surface', channels={'RGB': 'baseColor'})
        self.assertEqual(item.bit_depth, 16)

    def test_painter_custom_maps_default_to_sixteen_bits(self):
        result = operations.export_maps([{'name': 'Color', 'channels': {'RGB': 'baseColor'}}],
                                        None, None)
        self.assertEqual(result[0]['parameters']['bitDepth'], '16')

    def test_low_precision_formats_need_explicit_depth(self):
        for fmt in ('jpeg', 'tga'):
            with self.subTest(fmt=fmt), self.assertRaisesRegex(ValueError, 'explicit'):
                operations.export_maps([{'name': 'Color', 'format': fmt,
                                         'channels': {'RGB': 'baseColor'}}], None, None)

    def test_explicit_eight_bit_outputs_remain_available(self):
        self.assertEqual(PackMap(name='Runtime', channels={'RGB': 'baseColor'},
                                 bit_depth=8).bit_depth, 8)
        result = operations.export_maps([{'name': 'Color', 'format': 'tga', 'bit_depth': 8,
                                         'channels': {'RGB': 'baseColor'}}], None, None)
        self.assertEqual(result[0]['parameters']['bitDepth'], '8')

    def test_default_pack_keeps_samples_below_an_eight_bit_step(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'Source.png'
            with source.open('wb') as stream:
                png.Writer(2, 1, greyscale=True, bitdepth=16).write(stream, [[1, 2]])
            output = root / 'output'
            output.mkdir()
            spec = PackSpec(maps=[PackMap(name='Packed', channels={'RGB': 'height'})])
            pack(spec, {'height': source}, output)
            with (output / 'Packed.png').open('rb') as stream:
                _, _, rows, info = png.Reader(file=stream).asDirect()
                self.assertEqual(info['bitdepth'], 16)
                self.assertEqual(list(list(rows)[0]), [1, 1, 1, 2, 2, 2])
