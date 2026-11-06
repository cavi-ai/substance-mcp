"""Advertised arguments stay usable across typed catalog compaction."""

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from cavi_substance_mcp.designer import server as designer
from cavi_substance_mcp.painter import server as painter
from cavi_substance_mcp.sampler import server as sampler

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_top_level_parameter_has_a_description(self):
        for app in (painter, designer, sampler):
            for tool in await app.mcp.list_tools():
                for name, prop in tool.input_schema.get('properties', {}).items():
                    with self.subTest(tool=tool.name, parameter=name):
                        self.assertTrue(prop.get('description'))

    async def test_success_corpus_keeps_its_flat_argument_contract(self):
        for app, corpus in ((painter, 'cases.json'), (designer, 'designer_cases.json'),
                            (sampler, 'sampler_cases.json')):
            schemas = {tool.name: tool.input_schema for tool in await app.mcp.list_tools()}
            for schema in schemas.values():
                Draft202012Validator.check_schema(schema)
            for case in json.loads((ROOT / 'evals' / corpus).read_text()):
                if case['kind'] == 'success':
                    with self.subTest(case=case['id']):
                        arguments = dict(case['arguments'])
                        if arguments.get('lease_id') == '$lease':
                            # PainterHooks resolves this corpus placeholder before dispatch.
                            arguments['lease_id'] = 'valid_lease'
                        Draft202012Validator(schemas[case['tool']]).validate(arguments)

    async def test_sampler_inspect_advertises_conditional_nonnull_requirements(self):
        tools = {tool.name: tool for tool in await sampler.mcp.list_tools()}
        validate = Draft202012Validator(tools['sampler_inspect'].input_schema).is_valid
        for arguments in ({'action': 'project'}, {'action': 'project', 'project': None},
                          {'action': 'asset', 'project': 'rock.ssa'},
                          {'action': 'asset', 'asset': 'Rock'},
                          {'action': 'asset', 'project': 'rock.ssa', 'asset': None}):
            with self.subTest(arguments=arguments):
                self.assertFalse(validate(arguments))
        for arguments in ({'action': 'catalog'},
                          {'action': 'project', 'project': 'rock.ssa'},
                          {'action': 'asset', 'project': 'rock.ssa', 'asset': 'Rock'}):
            with self.subTest(arguments=arguments):
                self.assertTrue(validate(arguments))
