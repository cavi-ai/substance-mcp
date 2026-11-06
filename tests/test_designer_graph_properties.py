"""Designer graph settings and annotations follow their native SDK types."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cavi_substance_mcp.designer import graph_native as native


def prop(identifier, kind):
    return SimpleNamespace(getId=lambda: identifier,
                           getType=lambda: SimpleNamespace(getId=lambda: kind))


class Graph:
    def __init__(self, annotation_type='int'):
        self.identifier = 'Material'
        self.annotations = [prop('min', annotation_type), prop('max', annotation_type)]
        self.bounds = {}
        self.inheritance = {'$outputsize': 1, '$format': 1}
        self.values = {}

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
        return prop(identifier, kind)

    def setPropertyValue(self, property_, value):
        pass

    def getPropertyAnnotations(self, property_):
        return self.annotations

    def setPropertyAnnotationValueFromId(self, property_, name, value):
        expected = next(p.getType().getId() for p in self.annotations if p.getId() == name)
        if value[0] != expected or (expected == 'int' and type(value[1]) is not int):
            raise ValueError('SDK InvalidValue: annotation type mismatch')
        self.bounds[name] = value

    def setPropertyInheritanceMethod(self, property_, method):
        self.inheritance[property_.getId()] = method

    def getPropertyInheritanceMethod(self, property_):
        return self.inheritance[property_.getId()]


class NativePropertyTests(unittest.TestCase):
    def setUp(self):
        self.modules = {
            'sd.api.sdproperty': SimpleNamespace(
                SDPropertyCategory=SimpleNamespace(Input=0),
                SDPropertyInheritanceMethod=SimpleNamespace(Absolute=2)),
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
                self.assertEqual(target.inheritance['$outputsize'], 2,
                                 'output size must be absolute before assigning its exponents')
            target.values.update(values)

        with patch.dict(sys.modules, {'sd': sd, 'sd.api.sbs.sdsbscompgraph':
                                     SimpleNamespace(SDSBSCompGraph=Graph)}), \
                patch.object(native, 'set_values', side_effect=set_values), \
                patch.object(native, 'apply', return_value={}), \
                patch.object(native, 'describe', return_value={}):
            native.run({'action': action, 'recipe': recipe, 'output': 'new.sbs',
                        'path': 'existing.sbs' if action == 'edit' else None, 'libraries': {}})
        return graph

    def test_create_sets_absolute_size_and_preserves_precision_seed_defaults(self):
        graph = self.run_graph('create', {})
        self.assertEqual(graph.values, {'$outputsize': [11, 11], '$randomseed': 0,
                                       '$format': 'hdr_low_precision'})
        self.assertEqual(graph.inheritance, {'$outputsize': 2, '$format': 2})

    def test_edit_only_changes_declared_size(self):
        graph = self.run_graph('edit', {'resolution': 2048})
        self.assertEqual(graph.values, {'$outputsize': [11, 11]})
        self.assertEqual(graph.inheritance, {'$outputsize': 2, '$format': 1})

    def test_edit_without_size_keeps_existing_inheritance(self):
        graph = self.run_graph('edit', {'seed': 17})
        self.assertEqual(graph.values, {'$randomseed': 17})
        self.assertEqual(graph.inheritance, {'$outputsize': 1, '$format': 1})
