"""Opt-in native Painter authoring test. Requires an idle app with no project open."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import png
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MESH = ('o Surface\nv -1 -1 0\nv 1 -1 0\nv 1 1 0\nv -1 1 0\n'
        'vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\nusemtl Surface\n'
        'f 1/1/1 2/2/1 3/3/1\nf 1/1/1 3/3/1 4/4/1\n')


def pixels(path):
    with path.open('rb') as stream:
        width, height, rows, info = png.Reader(file=stream).asDirect()
        if info['bitdepth'] != 16:
            raise AssertionError(f'Expected actual 16-bit samples: {info}')
        return np.array(list(rows), np.int32).reshape(height, width, info['planes'])


@unittest.skipUnless(os.environ.get('CAVI_PAINTER_NATIVE_CONTROLS') == '1',
                     'Set CAVI_PAINTER_NATIVE_CONTROLS=1 for an idle licensed macOS Painter')
class PainterControlsNativeTests(unittest.TestCase):
    def test_precision_presets_channels_erase_masks_and_saved_source(self):
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(self.exercise(Path(folder)))

    async def exercise(self, root):
        (root / 'plane.obj').write_text(MESH)
        parameters = StdioServerParameters(
            command=sys.executable, args=['-m', 'cavi_substance_mcp.painter.server'],
            env={**os.environ, 'CAVI_SUBSTANCE_WORKSPACE': str(root)})
        async with stdio_client(parameters) as (reader, writer), ClientSession(
                reader, writer, read_timeout_seconds=300) as session:
            await session.initialize()

            async def call(tool, args):
                reply = await session.call_tool(tool, args)
                self.assertFalse(reply.is_error, str(reply.content))
                return reply.structured_content

            status = await call('painter_status', {})
            self.assertTrue(status['connected'])
            self.assertFalse(status['open'], 'Save and close the current project before this test')
            ownership = await call('session_control', {'action': 'inspect'})
            self.assertFalse(ownership['active'], 'Another client owns Painter')
            lease = await call('session_control', {'action': 'claim',
                              'owner': 'native-controls-test',
                              'expected_project': ownership['project_id'], 'ttl_seconds': 600})
            token = lease['lease_id']
            try:
                await call('project', {'action': 'create', 'mesh_path': 'plane.obj',
                           'default_texture_resolution': 512, 'lease_id': token})
                sets = await call('inspect', {'action': 'texture_sets'})
                self.assertTrue(all('8' not in fmt for item in sets['texture_sets']
                                    for fmt in item['channels'].values()))
                configured = await call('texture_set_settings', {'texture_set': 'Surface',
                    'channel_formats': {'BaseColor': 'RGB32F', 'Height': 'L32F'},
                    'resolution': 512, 'lease_id': token})
                self.assertEqual(configured['texture_sets'][0]['channels']['BaseColor'], 'RGB32F')
                shelf = await call('inspect', {'action': 'shelf', 'usage': 'brush',
                                              'query': 'Basic', 'limit': 100})
                preset = next(r['url'] for r in shelf['resources'] if r['name'] == 'Basic Hard')
                layer = await call('layer_edit', {'action': 'paint', 'texture_set': 'Surface',
                                                 'name': 'Precision paint', 'lease_id': token})
                settings = {'size': 24, 'flow': 100, 'opacity': 100, 'spacing': 8}
                path = [[.4, .46, 1], [.5, .46, 1], [.6, .46, 1]]

                async def export(directory):
                    await call('export_textures', {'directory': directory, 'maps': [
                        {'name': 'Color', 'channels': {'RGB': 'baseColor'}},
                        {'name': 'Roughness', 'channels': {'RGB': 'roughness'}}],
                        'resolution': 512, 'texture_sets': ['Surface'],
                        'dry_run': False, 'lease_id': token})
                    return pixels(root / directory / 'Color.png')

                async def prepare(directory, uid, **extra):
                    return await call('brush', {'action': 'prepare', 'texture_set': 'Surface',
                                      'uid': uid, 'directory': directory, 'preset': preset,
                                      'settings': settings, 'lease_id': token, **extra})

                async def stroke(context):
                    await call('brush', {'action': 'stroke', 'context_token':
                                        context['context_token'], 'points': path,
                                        'lease_id': token})

                before = await export('before')
                context = await prepare('paint-view', layer['uid'], channel_values={
                    'BaseColor': [.20137, .40371, .80719], 'SpecularRoughness': .234567},
                    enabled_channels={'BaseColor': True, 'SpecularRoughness': True,
                                      'BaseMetalness': False, 'Normal': False, 'Height': False})
                self.assertEqual(context['brush_settings'], settings)
                await stroke(context)
                painted = await export('painted')
                changed = np.max(abs(painted-before), axis=2) > 5
                self.assertGreater(int(changed.sum()), 100)
                median = np.median(painted[changed], axis=0) / 65535
                np.testing.assert_allclose(median, [.20137, .40371, .80719], atol=4e-5, rtol=0)
                roughness = pixels(root / 'painted/Roughness.png')
                self.assertAlmostEqual(float(np.median(roughness[changed])/65535), .234567,
                                       delta=2e-5)
                self.assertTrue(np.any(painted[changed] % 257 != 0), 'Precision fell to 8-bit')

                await stroke(await prepare('erase-view', layer['uid'], mode='erase'))
                erased = await export('erased')
                self.assertGreater(int(np.sum(np.max(abs(erased-painted), axis=2) > 5)), 100)
                fill = await call('layer_edit', {'action': 'fill', 'texture_set': 'Surface',
                                 'name': 'Masked finish', 'base_color': [.7, .2, .1],
                                 'lease_id': token})
                await call('layer_edit', {'action': 'fill_mask', 'texture_set': 'Surface',
                           'uid': fill['uid'], 'mask': 'black', 'lease_id': token})
                masked = await export('masked')
                await stroke(await prepare('mask-view', fill['uid'], target='mask'))
                revealed = await export('revealed')
                self.assertGreater(int(np.sum(np.max(abs(revealed-masked), axis=2) > 5)), 100)
                saved = await call('project', {'action': 'save_copy', 'directory': 'saved',
                                              'lease_id': token})
                await call('project', {'action': 'close', 'expected_file': None,
                                      'discard_unsaved': True, 'lease_id': token})
                await call('project', {'action': 'open', 'path': 'saved/project.spp',
                                      'lease_id': token})
                await call('project', {'action': 'wait_ready'})
                reopened = await export('reopened')
                # GPU recomputation can move a boundary sample by one PNG16 unit.
                np.testing.assert_allclose(reopened, revealed, atol=1, rtol=0)
                self.assertGreater(saved['bytes'], 0)
            finally:
                status = await call('painter_status', {})
                if status['open']:
                    await call('project', {'action': 'close', 'expected_file': status['file'],
                                          'discard_unsaved': True, 'lease_id': token})
                await call('session_control', {'action': 'release', 'lease_id': token})
