"""Real Designer SDK authoring through MCP, cooking and rendering the resulting graphs."""

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import png
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cavi_substance_mcp.designer import runner

INSTALL = runner.installation()
EXECUTABLES = ('Adobe Substance 3D Designer', 'Adobe Substance 3D Designer.exe')
AVAILABLE = any((INSTALL.bin_dir / name).is_file() for name in EXECUTABLES)


def samples(path):
    with path.open('rb') as stream:
        w, h, rows, info = png.Reader(file=stream).asDirect()
        if info['bitdepth'] != 16:
            raise AssertionError(f'Expected actual PNG16 output: {info}')
        return np.array(list(rows), np.int32).reshape(h, w, info['planes'])


@unittest.skipUnless(AVAILABLE, 'Designer native application not installed')
class DesignerGraphNativeTests(unittest.TestCase):
    def test_editable_graph_parameters_survive_save_cook_and_render(self):
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(self.exercise(Path(folder)))

    def test_graph_wiring_library_instances_and_higher_precision_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(self.operations_round_trip(Path(folder)))

    async def operations_round_trip(self, root):
        parameters = StdioServerParameters(
            command=sys.executable, args=['-m', 'cavi_substance_mcp.designer.server'],
            env=dict(os.environ, CAVI_SUBSTANCE_WORKSPACE=str(root)))
        async with stdio_client(parameters) as (reader, writer), ClientSession(
                reader, writer, read_timeout_seconds=300) as session:
            await session.initialize()

            async def call(args):
                reply = await session.call_tool('graph', args)
                self.assertFalse(reply.is_error, str(reply.content))
                return reply.structured_content

            catalog = await call({'action': 'catalog', 'directory': 'library',
                                  'path': 'builtin:packages/noise_clouds_3_v2',
                                  'query': 'sbs::compositing::blend'})
            self.assertEqual(catalog['node_schema']['input']['opacitymult']['type'], 'float')
            resource = next(r for r in catalog['resources'] if r['type'] == 'SDSBSCompGraph')
            created = await call({'action': 'create', 'directory': 'created', 'recipe': {
                'identifier': 'LayeredPaint', 'resolution': 32, 'precision': '32f', 'seed': 17,
                'operations': [
                    {'action': 'add_node', 'key': 'color',
                     'definition': 'sbs::compositing::uniform'},
                    {'action': 'add_node', 'key': 'tint',
                     'definition': 'sbs::compositing::uniform'},
                    {'action': 'add_node', 'key': 'blend', 'definition': 'sbs::compositing::blend',
                     'values': {'opacitymult': 0.25}},
                    {'action': 'connect', 'source': 'color', 'source_port': 'unique_filter_output',
                     'target': 'blend', 'target_port': 'source'},
                    {'action': 'connect', 'source': 'tint', 'source_port': 'unique_filter_output',
                     'target': 'blend', 'target_port': 'destination'},
                    {'action': 'disconnect', 'node': 'blend', 'property': 'source'},
                    {'action': 'connect', 'source': 'color', 'source_port': 'unique_filter_output',
                     'target': 'blend', 'target_port': 'source'},
                    {'action': 'expose', 'node': 'blend', 'property': 'opacitymult',
                     'identifier': 'blendAmount', 'minimum': 0, 'maximum': 1},
                    {'action': 'set_input', 'identifier': 'blendAmount', 'value': 0.75},
                    {'action': 'instance', 'key': 'noise',
                     'package': 'builtin:packages/noise_clouds_3_v2', 'resource': resource['url']},
                    {'action': 'add_node', 'key': 'unused',
                     'definition': 'sbs::compositing::uniform'},
                    {'action': 'remove_node', 'node': 'unused'},
                    {'action': 'output', 'key': 'out', 'source': 'blend',
                     'source_port': 'unique_filter_output', 'identifier': 'Color',
                     'usage': 'baseColor'},
                ]}})
            self.assertNotIn('unused', created['node_ids'])
            self.assertEqual(created['graphs'][0]['precision'], 'hdr_high_precision')
            self.assertEqual(created['graphs'][0]['inputs']['$randomseed']['value'], 17)
            edited = await call({'action': 'edit', 'path': created['package'],
                                 'directory': 'edited',
                                 'recipe': {'operations': [
                                     {'action': 'set', 'node': created['node_ids']['color'],
                                      'values': {'outputcolor': [0.7, 0.2, 0.1, 1.0]}}]}})
            self.assertEqual(edited['graphs'][0]['precision'], 'hdr_high_precision')
            self.assertEqual(edited['graphs'][0]['inputs']['blendAmount']['value'], 0.75)

    async def exercise(self, root):
        parameters = StdioServerParameters(
            command=sys.executable, args=['-m', 'cavi_substance_mcp.designer.server'],
            env=dict(os.environ, CAVI_SUBSTANCE_WORKSPACE=str(root)))
        async with stdio_client(parameters) as (reader, writer), ClientSession(
                reader, writer, read_timeout_seconds=300) as session:
            await session.initialize()

            async def call(name, args):
                reply = await session.call_tool(name, args)
                self.assertFalse(reply.is_error, str(reply.content))
                return reply.structured_content

            recipe = {'identifier': 'PaintedMetal', 'resolution': 32, 'operations': [
                {'action': 'add_node', 'key': 'color', 'definition': 'sbs::compositing::uniform',
                 'values': {'outputcolor': [0.1, 0.3, 0.6, 1.0]}},
                {'action': 'expose', 'node': 'color', 'property': 'outputcolor',
                 'identifier': 'paintColor'},
                {'action': 'output', 'key': 'base', 'source': 'color',
                 'source_port': 'unique_filter_output', 'identifier': 'BaseColor',
                 'usage': 'baseColor', 'color_space': 'sRGB'},
            ]}
            source = await call('graph', {'action': 'create', 'directory': 'source',
                                          'recipe': recipe})
            source_path = root / source['package']
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            changed = await call('graph', {'action': 'edit', 'path': source['package'],
                                           'directory': 'candidate', 'recipe': {'operations': [
                                               {'action': 'set_input', 'identifier': 'paintColor',
                                                'value': [0.8, 0.2, 0.1, 1.0]}]}})
            inspected = await call('graph', {'action': 'inspect', 'path': changed['package'],
                                             'directory': 'inspection'})
            self.assertEqual(inspected['graphs'][0]['identifier'], 'PaintedMetal')
            self.assertEqual(inspected['graphs'][0]['precision'], 'hdr_low_precision')
            self.assertIn('paintColor', inspected['graphs'][0]['inputs'])
            self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)
            colors = []
            packages = [('source', source['package']), ('candidate', changed['package'])]
            for label, package in packages:
                cooked = await call('material', {'action': 'cook', 'path': package,
                                                 'directory': label + '-cooked'})
                rendered = await call('material', {'action': 'render', 'path': cooked['sbsar'],
                                                   'directory': label + '-maps', 'resolution': 32})
                colors.append(samples(root / rendered['maps']['baseColor']))
            self.assertGreater(int(np.abs(colors[1] - colors[0]).max()), 1000)
