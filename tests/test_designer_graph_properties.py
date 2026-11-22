"""Designer graph settings and annotations follow their native SDK types."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from cavi_substance_mcp.designer import graph_native as native
from cavi_substance_mcp.designer.graph_models import Expose


def prop(identifier, kind, connectable=False, read_only=False):
    return SimpleNamespace(getId=lambda: identifier,
                           getType=lambda: SimpleNamespace(getId=lambda: kind),
                           isConnectable=lambda: connectable,
                           isReadOnly=lambda: read_only)


class Graph:
    def __init__(self, annotation_type='int', annotations=None):
        self.identifier = 'Material'
        self.annotations = annotations if annotations is not None else [
            prop('min', annotation_type), prop('max', annotation_type),
            prop('label', 'string'), prop('group', 'string'), prop('description', 'string')]
        self.bounds = {}
        self.inheritance = {'$outputsize': 1, '$format': 1}
        self.values = {}
        self.parent_size = None
        self.inputs = []

    @staticmethod
    def sNew(package):
        return package.graph

    def getIdentifier(self):
        return self.identifier

    def setIdentifier(self, identifier):
        self.identifier = identifier

    def getPropertyFromId(self, identifier, category):
        return prop(identifier, 'int2') if identifier.startswith('$') else None

    def newProperty(self, identifier, kind, category):
        created = prop(identifier, kind)
        self.inputs.append(created)
        return created

    def setPropertyValue(self, property_, value):
        raw = value[1] if isinstance(value, tuple) else value
        self.values[property_.getId()] = raw

    def getPropertyAnnotations(self, property_):
        return self.annotations

    def setPropertyAnnotationValueFromId(self, property_, name, value):
        expected = next(p.getType().getId() for p in self.annotations if p.getId() == name)
        if value[0] != expected or (expected == 'int' and type(value[1]) is not int):
            raise ValueError('SDK InvalidValue: annotation type mismatch')
        self.bounds[name] = value

    def getPropertyAnnotationValueFromId(self, property_, name):
        stored = self.bounds.get(name)
        if stored is None:
            return None
        raw = stored[1]
        return SimpleNamespace(get=lambda raw=raw: raw)

    def getProperties(self, category):
        return self.inputs

    def getPropertyValue(self, property_):
        return SimpleNamespace(get=lambda: self.values.get(property_.getId()))

    def getNodes(self):
        return []

    def getInputPropertyValueFromId(self, identifier):
        return SimpleNamespace(get=lambda: self.values.get(identifier))

    def setPropertyInheritanceMethod(self, property_, method):
        self.inheritance[property_.getId()] = method

    def getPropertyInheritanceMethod(self, property_):
        return self.inheritance[property_.getId()]

    def setDefaultParentSize(self, size):
        self.parent_size = size


class NativePropertyTests(unittest.TestCase):
    def setUp(self):
        self.modules = {
            'sd.api.sdproperty': SimpleNamespace(
                SDPropertyCategory=SimpleNamespace(Input=0),
                SDPropertyInheritanceMethod=SimpleNamespace(Absolute=2, RelativeToParent=1)),
            'sd.api.sdbasetypes': SimpleNamespace(int2=lambda x, y: (x, y)),
            'sd.api.sdvaluestring': SimpleNamespace(
                SDValueString=SimpleNamespace(sNew=lambda value: ('string', value))),
        }
        for kind, suffix in [('int', 'Int'), ('float3', 'Float3')]:
            self.modules['sd.api.sdtype' + suffix.lower()] = SimpleNamespace(**{
                'SDType' + suffix: SimpleNamespace(sNew=lambda kind=kind: kind)})
        self.enterContext(patch.dict(sys.modules, self.modules))

    def expose(self, graph, source_type='int', **bounds):
        node = SimpleNamespace(
            getPropertyFromId=lambda key, category: prop(key, source_type),
            getPropertyGraph=lambda property_: None,
            getPropertyValue=lambda property_: SimpleNamespace(get=lambda: 4),
            newPropertyGraph=lambda *args: SimpleNamespace(
                newNode=lambda key: SimpleNamespace(setInputPropertyValueFromId=lambda *a: None),
                setOutputNode=lambda *args: None),
        )
        with patch.object(native, 'value_for', side_effect=lambda kind, value: (kind, value)):
            native.expose(graph, node, {'property': 'tiling', 'identifier': 'Tiling', **bounds})

    def test_integer_annotation_bounds_keep_integer_values(self):
        graph = Graph()
        self.expose(graph, minimum=1, maximum=16.0)
        self.assertEqual(graph.bounds, {'min': ('int', 1), 'max': ('int', 16)})

    def test_fractional_integer_bounds_are_rejected(self):
        graph = Graph()
        with self.assertRaisesRegex(ValueError, 'integer'):
            self.expose(graph, minimum=1, maximum=2.5)
        self.assertEqual(graph.bounds, {})

    def test_vector_input_uses_scalar_annotation_type(self):
        graph = Graph('float')
        self.expose(graph, source_type='float3', minimum=0.125, maximum=1)
        self.assertEqual(graph.bounds, {'min': ('float', 0.125), 'max': ('float', 1)})

    def test_group_and_description_are_string_annotations(self):
        graph = Graph(annotations=[prop('min', 'int'), prop('max', 'int'),
                                   prop('group', 'string'), prop('description', 'string')])
        self.expose(graph, group='Surface', description='Tile count')
        self.assertEqual(graph.bounds['group'], ('string', 'Surface'))
        self.assertEqual(graph.bounds['description'], ('string', 'Tile count'))

    def test_blank_group_is_refused(self):
        base = {'action': 'expose', 'node': 'blend', 'property': 'opacitymult',
                'identifier': 'blendAmount'}
        for group in ('', '   ', '\n\t'):
            with self.subTest(group=group), self.assertRaises(ValidationError):
                Expose.model_validate({**base, 'group': group})
        graph = Graph()
        with self.assertRaisesRegex(ValueError, 'group annotation must not be blank'):
            self.expose(graph, group='   ')
        self.assertNotIn('group', graph.bounds)
        with self.assertRaises(ValidationError):
            Expose.model_validate({**base, 'group': 'g' * 129})
        Expose.model_validate({**base, 'group': 'g' * 128, 'description': 'd' * 128})

    def test_missing_group_annotation_writes_nothing(self):
        graph = Graph(annotations=[prop('min', 'int'), prop('max', 'int'),
                                   prop('label', 'string'), prop('description', 'string')])
        with self.assertRaisesRegex(ValueError, 'no group annotation'):
            self.expose(graph, group='Surface', description='Tile count')
        self.assertEqual(graph.bounds, {})

    def test_describe_includes_annotations_that_were_set(self):
        graph = Graph(annotations=[prop('min', 'int'), prop('max', 'int'),
                                   prop('label', 'string'), prop('group', 'string'),
                                   prop('description', 'string')])
        self.expose(graph, group='Surface', description='Tile count', label='Tiling',
                    minimum=1, maximum=8)
        entry = native.describe(graph)['inputs']['Tiling']
        self.assertEqual(entry['value'], 4)
        self.assertEqual(entry['annotations'], {
            'label': 'Tiling', 'group': 'Surface', 'description': 'Tile count',
            'min': 1, 'max': 8})

    def run_graph(self, action, recipe):
        graph = Graph()
        package = SimpleNamespace(graph=graph, getChildrenResources=lambda recursive: [graph])
        manager = SimpleNamespace(
            newUserPackage=lambda: package, loadUserPackage=lambda *args: package,
            savePackageAs=lambda *args: None, unloadUserPackage=lambda *args: None)
        sd = SimpleNamespace(getContext=lambda: SimpleNamespace(getSDApplication=lambda:
            SimpleNamespace(getPackageMgr=lambda: manager)))

        def set_values(target, values):
            if '$outputsize' in values:
                self.assertEqual(target.inheritance['$outputsize'], 1,
                                 'output size must stay relative so cooking can expose it')
                self.assertEqual(values['$outputsize'], [0, 0])
            target.values.update(values)

        with patch.dict(sys.modules, {'sd': sd, 'sd.api.sbs.sdsbscompgraph':
                                     SimpleNamespace(SDSBSCompGraph=Graph)}), \
                patch.object(native, 'set_values', side_effect=set_values), \
                patch.object(native, 'apply', return_value={}), \
                patch.object(native, 'describe', return_value={}):
            native.run({'action': action, 'recipe': recipe, 'output': 'new.sbs',
                        'path': 'existing.sbs' if action == 'edit' else None, 'libraries': {}})
        return graph

    def test_create_preserves_working_size_without_freezing_cooked_resolution(self):
        graph = self.run_graph('create', {})
        self.assertEqual(graph.values, {'$outputsize': [0, 0], '$randomseed': 0,
                                       '$format': 'hdr_low_precision'})
        self.assertEqual(graph.parent_size, (2048, 2048))
        self.assertEqual(graph.inheritance, {'$outputsize': 1, '$format': 2})

    def test_edit_only_changes_declared_size(self):
        graph = self.run_graph('edit', {'resolution': 2048})
        self.assertEqual(graph.values, {'$outputsize': [0, 0]})
        self.assertEqual(graph.parent_size, (2048, 2048))
        self.assertEqual(graph.inheritance, {'$outputsize': 1, '$format': 1})

    def test_edit_without_size_keeps_existing_inheritance(self):
        graph = self.run_graph('edit', {'seed': 17})
        self.assertEqual(graph.values, {'$randomseed': 17})
        self.assertIsNone(graph.parent_size)
        self.assertEqual(graph.inheritance, {'$outputsize': 1, '$format': 1})
