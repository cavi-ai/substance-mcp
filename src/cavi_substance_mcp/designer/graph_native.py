"""Declarative graph operations inside Designer's embedded Python interpreter."""

import importlib
import math


def unpack(value):
    if value is None:
        return None
    if hasattr(value, 'getValueId'):
        return value.getValueId()
    if not hasattr(value, 'get'):
        return None
    raw = value.get()
    if raw is None or type(raw) in (str, bool, int, float):
        return raw
    if hasattr(raw, '_fields_'):
        return [getattr(raw, name) for name, _ in raw._fields_]
    return None


def value_for(type_id, value):
    from sd.api import sdbasetypes
    from sd.api.sdvalueenum import SDValueEnum

    if type_id.startswith('sbs::'):
        if type(value) is int:
            return SDValueEnum.sFromValue(type_id, value)
        if isinstance(value, str):
            return SDValueEnum.sFromValueId(type_id, value)
        raise ValueError(f'{type_id} needs an enum identifier or integer')
    numeric = type_id.startswith(('float', 'int')) or type_id in ('ColorRGB', 'ColorRGBA')
    values = value if isinstance(value, list) else [value]
    if numeric and any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError(f'{type_id} needs finite numeric values')
    constructors = {'float': 'Float', 'int': 'Int', 'bool': 'Bool', 'string': 'String',
                    'ColorRGB': 'ColorRGB', 'ColorRGBA': 'ColorRGBA',
                    **{f'{base}{n}': f'{base.title()}{n}'
                       for base in ('float', 'int') for n in (2, 3, 4)}}
    if type_id not in constructors:
        raise ValueError(f'No JSON value conversion for native type {type_id}')
    if type_id == 'bool' and type(value) is not bool:
        raise ValueError('bool property needs true or false')
    if type_id == 'string' and not isinstance(value, str):
        raise ValueError('string property needs a string')
    if type_id.startswith('int') and any(type(v) is not int for v in values):
        raise ValueError('integer properties need integer values')
    suffix = constructors[type_id]
    factory = getattr(importlib.import_module('sd.api.sdvalue' + suffix.lower()),
                      'SDValue' + suffix)
    if type_id in ('float', 'int', 'bool', 'string'):
        return factory.sNew(value)
    count = 4 if type_id == 'ColorRGBA' else 3 if type_id == 'ColorRGB' else int(type_id[-1])
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f'{type_id} needs {count} components')
    return factory.sNew(getattr(sdbasetypes, type_id)(*value))


def properties(node, category):
    result = {}
    for prop in node.getProperties(category):
        kind = prop.getType()
        item = {'type': kind.getId() if kind else None,
                'value': unpack(node.getPropertyValue(prop)),
                'connectable': prop.isConnectable(), 'read_only': prop.isReadOnly()}
        result[prop.getId()] = item
    return result


def set_values(node, values):
    from sd.api.sdproperty import SDPropertyCategory

    converted = []
    for key, value in values.items():
        prop = node.getPropertyFromId(key, SDPropertyCategory.Input)
        if prop is None or prop.isReadOnly():
            raise ValueError(f'No writable input property {key}')
        if hasattr(node, 'getPropertyGraph') and node.getPropertyGraph(prop) is not None:
            raise ValueError(f'{key} has a parameter function; edit its exposed graph input')
        converted.append((prop, value_for(prop.getType().getId(), value)))
    for prop, value in converted:
        node.setPropertyValue(prop, value)


def expose(graph, node, spec):
    from sd.api.sdproperty import SDPropertyCategory
    from sd.api.sdvaluestring import SDValueString

    prop = node.getPropertyFromId(spec['property'], SDPropertyCategory.Input)
    if prop is None or node.getPropertyGraph(prop) is not None:
        raise ValueError('Exposed source must be an existing constant input')
    if graph.getPropertyFromId(spec['identifier'], SDPropertyCategory.Input) is not None:
        raise ValueError('Graph input identifier already exists')
    type_id = prop.getType().getId()
    default = unpack(node.getPropertyValue(prop))
    exposed_type = {'ColorRGB': 'float3', 'ColorRGBA': 'float4'}.get(type_id, type_id)
    suffix = {'float': 'Float', 'int': 'Int', 'bool': 'Bool', 'string': 'String',
              **{f'{base}{n}': f'{base.title()}{n}'
                 for base in ('float', 'int') for n in (2, 3, 4)}}.get(exposed_type)
    if suffix is None:
        raise ValueError(f'Cannot expose native property type {type_id} through a scalar input')
    factory = getattr(importlib.import_module('sd.api.sdtype' + suffix.lower()), 'SDType' + suffix)
    graph_input = graph.newProperty(spec['identifier'], factory.sNew(), SDPropertyCategory.Input)
    graph.setPropertyValue(graph_input, value_for(exposed_type, default))
    if spec.get('label'):
        graph.setPropertyAnnotationValueFromId(graph_input, 'label',
                                              SDValueString.sNew(spec['label']))
    annotation_types = {p.getId(): p.getType().getId()
                        for p in graph.getPropertyAnnotations(graph_input)}
    bounds = []
    for name, field in (('min', 'minimum'), ('max', 'maximum')):
        if spec.get(field) is not None:
            if name not in annotation_types:
                raise ValueError(f'Exposed input has no {name} annotation')
            kind, value = annotation_types[name], spec[field]
            if kind == 'int':
                if not math.isfinite(value) or value != math.trunc(value):
                    raise ValueError(f'{name} annotation needs an integer bound')
                value = int(value)
            bounds.append((name, value_for(kind, value)))
    for name, value in bounds:
        graph.setPropertyAnnotationValueFromId(graph_input, name, value)
    fn = node.newPropertyGraph(prop, 'SDSBSFunctionGraph')
    getter = {'float': 'float1', 'int': 'integer1', 'int2': 'integer2',
              'int3': 'integer3', 'int4': 'integer4'}.get(exposed_type, exposed_type)
    get_node = fn.newNode('sbs::function::get_' + getter)
    get_node.setInputPropertyValueFromId('__constant__', SDValueString.sNew(spec['identifier']))
    fn.setOutputNode(get_node, True)


def apply(graph, operations, package_manager, libraries):
    from sd.api.sdbasetypes import float2
    from sd.api.sdproperty import SDPropertyCategory
    from sd.api.sdtypeusage import SDTypeUsage
    from sd.api.sdusage import SDUsage
    from sd.api.sdvaluearray import SDValueArray
    from sd.api.sdvaluestring import SDValueString
    from sd.api.sdvalueusage import SDValueUsage

    aliases = {}

    def node(key):
        found = aliases.get(key) or graph.getNodeFromId(key)
        if found is None:
            raise ValueError(f'Graph node not found: {key}')
        return found

    def register(key, item, position):
        if key in aliases or graph.getNodeFromId(key) is not None:
            raise ValueError(f'Node key already exists: {key}')
        aliases[key] = item
        item.setPosition(float2(*position))
        return item

    for spec in operations:
        action = spec['action']
        if action == 'add_node':
            item = register(spec['key'], graph.newNode(spec['definition']), spec['position'])
            set_values(item, spec['values'])
        elif action == 'instance':
            library = package_manager.loadUserPackage(libraries[spec['package']], False, False)
            resource = library.findResourceFromUrl(spec['resource'].split('?', 1)[0])
            if resource is None:
                raise ValueError(f'Library resource not found: {spec["resource"]}')
            item = register(spec['key'], graph.newInstanceNode(resource), spec['position'])
            set_values(item, spec['values'])
        elif action == 'set':
            set_values(node(spec['node']), spec['values'])
        elif action == 'set_input':
            set_values(graph, {spec['identifier']: spec['value']})
        elif action == 'connect':
            node(spec['source']).newPropertyConnectionFromId(
                spec['source_port'], node(spec['target']), spec['target_port'])
        elif action == 'disconnect':
            item = node(spec['node'])
            prop = item.getPropertyFromId(spec['property'], SDPropertyCategory.Input)
            if prop is None:
                raise ValueError('Input property not found')
            item.deletePropertyConnections(prop)
        elif action == 'remove_node':
            removed = node(spec['node'])
            identifier = removed.getIdentifier()
            aliases = {key: item for key, item in aliases.items()
                       if item.getIdentifier() != identifier}
            graph.deleteNode(removed)
        elif action == 'expose':
            expose(graph, node(spec['node']), spec)
        elif action == 'output':
            item = register(spec['key'], graph.newNode('sbs::compositing::output'),
                            spec['position'])
            item.setAnnotationPropertyValueFromId('identifier',
                                                 SDValueString.sNew(spec['identifier']))
            usages = SDValueArray.sNew(SDTypeUsage.sNew(), 0)
            usages.pushBack(SDValueUsage.sNew(SDUsage.sNew(
                spec['usage'], spec['components'], spec['color_space'])))
            item.setAnnotationPropertyValueFromId('usages', usages)
            node(spec['source']).newPropertyConnectionFromId(spec['source_port'], item,
                                                             'inputNodeOutput')
            graph.setOutputNode(item, True)
        else:
            raise ValueError(f'Unknown graph edit {action}')
    return {key: item.getIdentifier() for key, item in aliases.items()}


def describe(graph):
    from sd.api.sdproperty import SDPropertyCategory

    nodes = []
    for item in graph.getNodes():
        position = item.getPosition()
        inputs = properties(item, SDPropertyCategory.Input)
        connections = []
        for prop in item.getProperties(SDPropertyCategory.Output):
            for connection in item.getPropertyConnections(prop):
                connections.append({'output': prop.getId(),
                                    'target': connection.getInputPropertyNode().getIdentifier(),
                                    'input': connection.getInputProperty().getId()})
        definition = item.getDefinition()
        nodes.append({'id': item.getIdentifier(),
                      'definition': definition.getId() if definition else None,
                      'position': [position.x, position.y], 'inputs': inputs,
                      'outputs': properties(item, SDPropertyCategory.Output),
                      'annotations': properties(item, SDPropertyCategory.Annotation),
                      'connections': connections})
    return {'identifier': graph.getIdentifier(),
            'precision': unpack(graph.getInputPropertyValueFromId('$format')),
            'inputs': properties(graph, SDPropertyCategory.Input), 'nodes': nodes}


def run(request):
    import sd
    from sd.api.sbs.sdsbscompgraph import SDSBSCompGraph
    from sd.api.sdbasetypes import int2
    from sd.api.sdproperty import SDPropertyCategory, SDPropertyInheritanceMethod

    manager = sd.getContext().getSDApplication().getPackageMgr()
    recipe, action = request['recipe'], request['action']
    if request.get('path'):
        package = manager.loadUserPackage(request['path'], False, False)
    else:
        package = manager.newUserPackage()
    graphs = [r for r in package.getChildrenResources(True) if isinstance(r, SDSBSCompGraph)]
    if action == 'create':
        graph = SDSBSCompGraph.sNew(package)
        graph.setIdentifier(recipe.get('identifier', 'material'))
        graphs = [graph]
    elif graphs:
        selected = [g for g in graphs if g.getIdentifier() == recipe.get('identifier')]
        if len(selected) == 1:
            graph = selected[0]
        elif len(graphs) == 1:
            graph = graphs[0]
        elif action == 'edit':
            raise ValueError('Choose the graph identifier when the package has several graphs')
        else:
            graph = graphs[0]
    else:
        graph = SDSBSCompGraph.sNew(package)
    if action == 'catalog':
        definitions = sorted(d.getId() for d in graph.getNodeDefinitions()
                              if request['query'].lower() in d.getId().lower())
        result = {'definitions': definitions[request['offset']:
                                             request['offset'] + request['limit']],
                  'total': len(definitions), 'offset': request['offset']}
        if request['query'] in definitions:
            item = graph.newNode(request['query'])
            from sd.api.sdproperty import SDPropertyCategory
            result['node_schema'] = {
                category.name.lower(): properties(item, category) for category in
                (SDPropertyCategory.Input, SDPropertyCategory.Output)}
        result['resources'] = [{'identifier': r.getIdentifier(),
                                'url': r.getUrl().split('?', 1)[0],
                                'type': r.getClassName()}
                               for r in package.getChildrenResources(True)]
    else:
        if action in ('create', 'edit'):
            manager.savePackageAs(package, request['output'])
            if action == 'create' or 'resolution' in recipe:
                resolution = recipe.get('resolution', 2048)
                graph.setDefaultParentSize(int2(resolution, resolution))
                prop = graph.getPropertyFromId('$outputsize', SDPropertyCategory.Input)
                inheritance = SDPropertyInheritanceMethod.RelativeToParent
                graph.setPropertyInheritanceMethod(prop, inheritance)
                if graph.getPropertyInheritanceMethod(prop) != inheritance:
                    raise ValueError('Graph output size did not accept parent inheritance')
                # Keep the editable working size in the graph's default parent.
                # Absolute base parameters suppress the cooker's $outputsize input.
                set_values(graph, {'$outputsize': [0, 0]})
            if action == 'create' or 'seed' in recipe:
                set_values(graph, {'$randomseed': recipe.get('seed', 0)})
            if action == 'create' or 'precision' in recipe:
                fmt = {'8': 'low_precision', '16': 'high_precision',
                       '16f': 'hdr_low_precision', '32f': 'hdr_high_precision'}[
                           recipe.get('precision', '16f')]
                prop = graph.getPropertyFromId('$format', SDPropertyCategory.Input)
                graph.setPropertyInheritanceMethod(prop, SDPropertyInheritanceMethod.Absolute)
                set_values(graph, {'$format': fmt})
            aliases = apply(graph, recipe.get('operations', []), manager, request['libraries'])
            manager.savePackageAs(package, request['output'])
        else:
            aliases = {}
        result = {'graphs': [describe(g) for g in graphs], 'node_ids': aliases}
    manager.unloadUserPackage(package)
    return result
