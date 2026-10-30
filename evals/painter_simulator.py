"""Explicitly simulated Painter API behind its real loopback scripting protocol.

Executes the production operation source. This fixture establishes operation and
transport behavior against the stated API assumptions, never native compatibility.
"""

import base64
import dataclasses
import json
import re
import sys
import threading
import urllib.parse
from contextlib import contextmanager
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

# Painter 12.2 legacy names are aliases of OpenPBR members (probed read-only).
ChannelType = Enum("ChannelType", [
    ("BaseColor", 1), ("SpecularRoughness", 2), ("Roughness", 2), ("BaseMetalness", 3),
    ("Metallic", 3), ("Normal", 4), ("Height", 5), ("EmissionColor", 6), ("Emissive", 6),
    ("Opacity", 7), ("AO", 8), *[(f"User{i}", 9 + i) for i in range(8)]])
MeshMapUsage = Enum("MeshMapUsage", ["AO", "BentNormals", "Curvature", "Height", "ID", "Normal",
                                     "Opacity", "Position", "Thickness", "WorldSpaceNormal"])
# Member names probed read-only from Painter 12.2.
BlendingMode = Enum("BlendingMode", [
    "Color", "ColorBurn", "ColorDodge", "Darken", "Difference", "Disable", "Divide", "Exclusion",
    "HardLight", "InverseDivide", "InverseSubtract", "Lighten", "LinearBurn", "LinearDodge",
    "LinearLight", "Multiply", "Normal", "NormalMapCombine", "NormalMapDetail",
    "NormalMapInverseDetail", "Overlay", "Passthrough", "PinLight", "Replace", "Saturation",
    "Screen", "SignedAddition", "SoftLight", "Subtract", "Tint", "Value", "VividLight"])
MaskBackground = Enum("MaskBackground", ["Black", "White"])
NodeStack = Enum("NodeStack", ["Substack", "Content", "Mask"])
BakingStatus = Enum("BakingStatus", ["Success", "Cancel", "Fail"])
CurvatureMethod = Enum("CurvatureMethod", ["FromMesh", "FromNormalMap"])
ProjectWorkflow = Enum("ProjectWorkflow", ["Default", "TextureSetPerUVTile", "UVTile"])
TangentSpace = Enum("TangentSpace", ["PerVertex", "PerFragment"])
ReloadMeshStatus = Enum("ReloadMeshStatus", ["SUCCESS", "ERROR"])
Usage = Enum("Usage", {name: name.lower() for name in [
    "ALPHA", "BASE_MATERIAL", "BRUSH", "COLOR_LUT", "EMITTER", "ENVIRONMENT", "EXPORT", "FILTER",
    "FONT", "GENERATOR", "PARTICLE", "PROCEDURAL", "RECEIVER", "SHADER", "SMART_MASK",
    "SMART_MATERIAL", "TEXTURE", "TOOL"]})
ResourceType = Enum("Type", ["IMAGE", "SUBSTANCE", "SUBSTANCE_PACKAGE", "SMART_MATERIAL",
                             "SMART_MASK", "PRESET"])
SourceMode = SimpleNamespace(Split=SimpleNamespace(name="Split"),
                             Material=SimpleNamespace(name="Material"))
STACK_CHANNELS = {ChannelType.BaseColor: "sRGB8", ChannelType.Roughness: "L8",
                  ChannelType.Metallic: "L8", ChannelType.Normal: "RGB16F",
                  ChannelType.Height: "L16F"}
MATERIAL_CHANNELS = {ChannelType.BaseColor, ChannelType.Metallic, ChannelType.Roughness,
                     ChannelType.Normal, ChannelType.Height}
# Painter export JSON contract from the bundled python-doc json_config.
DOCUMENT_MAPS = {"basecolor", "height", "specular", "opacity", "emissive", "displacement",
                 "glossiness", "roughness", "anisotropylevel", "anisotropyangle", "transmissive",
                 "scattering", "reflection", "ior", "metallic", "normal", "ambientocclusion",
                 "diffuse", "specularlevel", "blendingmask", *[f"user{i}" for i in range(8)]}
MESH_MAPS = {"ambient_occlusion", "id", "curvature", "normal_base", "world_space_normals",
             "position", "thickness", "bent_normals", "height", "opacity"}
VIRTUAL_MAPS = {"Normal_OpenGL", "Normal_DirectX", "AO_Mixed", "Diffuse", "Specular",
                "Glossiness", "Unity4Diff", "Unity4Gloss", "reflection", "1/ior", "Glossiness2",
                "f0", "View_2D"}
FORMATS = {"png": {"8", "16"}, "tga": {"8"}, "tiff": {"8", "16", "32f"}, "exr": {"16f", "32f"},
           "jpeg": {"8"}}
PADDINGS = {"passthrough", "color", "transparent", "diffusion", "infinite"}


@dataclasses.dataclass(frozen=True)
class ResourceID:
    context: str
    name: str
    version: str = None

    @classmethod
    def from_url(cls, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "resource" or not parsed.path.startswith("/"):
            raise ValueError("Invalid resource URL")
        version = urllib.parse.parse_qs(parsed.query).get("version", [None])[0]
        return cls(parsed.netloc, parsed.path[1:], version)

    def url(self):
        suffix = f"?version={self.version}" if self.version else ""
        return f"resource://{self.context}/{self.name}{suffix}"


class Resource:
    def __init__(self, context, name, usages, kind, category="", children=(), version=None):
        self.rid = ResourceID(context, name, version)
        self._usages, self.kind, self._category = list(usages), kind, category
        self._children = list(children)

    def identifier(self):
        return self.rid

    def usages(self):
        return list(self._usages)

    def gui_name(self):
        return self.rid.name

    def type(self):
        return self.kind

    def category(self):
        return self._category

    def children(self):
        return list(self._children)


@dataclasses.dataclass(frozen=True)
class Property:
    handle: tuple
    kind: str
    choices: tuple = ()

    def value(self):
        return self.handle[0].values[self.handle[1]]

    def name(self):
        return "Detail." + self.handle[1]

    def short_name(self):
        return self.handle[1]

    def label(self):
        return self.handle[1]

    def widget_type(self):
        return self.kind

    def enum_values(self):
        return dict(self.choices)

    def enum_value(self, label):
        return self.enum_values()[label]


class Owner:
    def __init__(self, values):
        self.values = values


class StopSource:
    def __bool__(self):
        return True


class Dispatcher:
    def __init__(self):
        self.callbacks = {}

    def connect_strong(self, event_cls, callback):
        self.callbacks.setdefault(event_cls, []).append(callback)

    connect = connect_strong

    def disconnect(self, event_cls, callback):
        self.callbacks.get(event_cls, []).remove(callback)

    def _trigger(self, event):
        for callback in list(self.callbacks.get(type(event), [])):
            callback(event)


@dataclasses.dataclass(frozen=True)
class BakingProcessEnded:
    status: BakingStatus


@dataclasses.dataclass(frozen=True)
class BakingProcessProgress:
    progress: float


@dataclasses.dataclass(frozen=True)
class ProjectEditionEntered:
    pass


@dataclasses.dataclass(frozen=True)
class Position:
    kind: str
    node: object = None
    where: object = None


class PainterSimulator:
    def __init__(self, workspace):
        self.workspace = workspace
        self.state = "open"
        self.opened = True
        self.project_id = "simulated-project-A"
        self.loading = 0
        self.reload_callback = None
        self.next_uid = 100
        self.nodes = {}
        self.layers = []
        self.resources = []
        self.bake_running = False
        self.bake_calls = 0
        self.mesh_maps = {}
        self.last_channel = None
        self.modifying = False
        self.reset_bake()

    def reset_bake(self):
        self.bake = {"enabled": True, "curvature": CurvatureMethod.FromMesh,
                     "bakers": [MeshMapUsage.Normal, MeshMapUsage.WorldSpaceNormal,
                                MeshMapUsage.AO, MeshMapUsage.Curvature],
                     "owner": Owner({
                         "OutputSize": (11, 11), "DilationWidth": 16, "LowAsHigh": True,
                         "HipolyMesh": "", "CageMode": 0, "CageMesh": "", "MaxHeight": 0.01,
                         "MaxDepth": 0.01, "RelativeScale": True, "FilterMethod": 0,
                         "LowpolySuffix": "_low", "HipolySuffix": "_high", "SubSampling": 0})}

    def setup(self, state):
        if state == "bake-ended":
            self.finish_bake(BakingStatus.Success)
            self.state = "open"
            return
        self.state = state
        self.opened = state != "closed"
        self.layers = []
        self.nodes = {}
        self.next_uid = 100
        self.resources = []
        self.modifying = False
        self.last_channel = None
        self.loading = 3 if state == "loading" else 0

    def finish_bake(self, status):
        if not self.bake_running:
            return
        self.bake_running = False
        if status == BakingStatus.Success and self.bake["enabled"]:
            for usage in self.bake["bakers"]:
                self.mesh_maps[usage] = ResourceID("project", f"Body_{usage.name}_baked")
        self.module.event.DISPATCHER._trigger(BakingProcessProgress(1.0))
        self.module.event.DISPATCHER._trigger(BakingProcessEnded(status))

    def register(self, node):
        node.uid_value = self.next_uid
        self.next_uid += 1
        self.nodes[node.uid_value] = node
        return node

    def __enter__(self):
        module = ModuleType("substance_painter")
        module.__version__ = "simulated"
        self.module = module
        self.previous = sys.modules.get("substance_painter")
        sys.modules["substance_painter"] = module
        self_simulator = sim = self
        self.shelf = [
            Resource("starter_assets", "Steel Painted Worn", [Usage.SMART_MATERIAL],
                     ResourceType.SMART_MATERIAL, "metal"),
            Resource("starter_assets", "Edges Dirt", [Usage.SMART_MASK], ResourceType.SMART_MASK),
            Resource("starter_assets", "Metal Edge Wear", [Usage.GENERATOR],
                     ResourceType.SUBSTANCE, "generator", version="9bed7d8b.sbsar"),
            Resource("starter_assets", "Dirt", [Usage.GENERATOR], ResourceType.SUBSTANCE),
            Resource("user_assets", "Dirt", [Usage.GENERATOR], ResourceType.SUBSTANCE),
            Resource("starter_assets", "Concrete Asphalt", [Usage.BASE_MATERIAL],
                     ResourceType.SUBSTANCE, "concrete"),
            Resource("starter_assets", "Grunge Map 001", [Usage.TEXTURE], ResourceType.IMAGE),
            Resource("starter_assets", "Panorama", [Usage.ENVIRONMENT], ResourceType.IMAGE),
        ]

        def everything():
            found = []
            for item in [*self.shelf, *self.resources]:
                found.extend([item, *item.children()])
            return found

        def retrieve(identifier):
            return [item for item in everything() if item.rid.context == identifier.context
                    and item.rid.name == identifier.name
                    and identifier.version in (None, item.rid.version)]

        def search(query):
            return [item for item in everything() if query.lower() in item.gui_name().lower()]

        def import_resource(path, usage, name=None, group=None):
            assert usage in (Usage.TEXTURE, Usage.BASE_MATERIAL) and Path(path).is_file()
            if self.state == "import-failure":
                raise RuntimeError("simulated import failure")
            if self.state == "locked-bitmap-failure":
                Path(path).parent.chmod(0o500)
            name = name or Path(path).stem
            if usage == Usage.TEXTURE:
                item = Resource("project", name, [usage], ResourceType.IMAGE)
            else:
                graphs = [] if self.state == "empty-sbsar" else [
                    Resource("project", name + "/graph", [usage], ResourceType.SUBSTANCE)]
                item = Resource("project", name, [usage], ResourceType.SUBSTANCE_PACKAGE,
                                children=graphs)
            self.resources.append(item)
            return item

        module.resource = SimpleNamespace(
            import_project_resource=import_resource, Usage=Usage, Type=ResourceType,
            ResourceID=ResourceID, Resource=SimpleNamespace(retrieve=retrieve), search=search,
        )

        class Bitmap:
            def __init__(self, identifier):
                self.resource_id = identifier
                self.color_space = None

            def set_color_space(self, value):
                if value not in self.list_available_color_spaces():
                    raise ValueError("Invalid colorspace " + str(value))
                self.color_space = value

            def list_available_color_spaces(self):
                return ["sRGB"] if self_simulator.last_channel == ChannelType.BaseColor else ["Raw"]

            def get_color_space(self):
                return self.color_space

        class UniformColor:
            def __init__(self, color):
                self.color = color

            def get_color(self):
                return self.color

        class Substance:
            def __init__(self, identifier):
                self.resource_id = identifier
                self.values = {"$randomseed": 0, "wear": 0.5, "tint": (1.0, 1.0, 1.0)}

            def get_properties(self):
                kinds = {"$randomseed": "RandomSeed", "wear": "Slider", "tint": "Color"}
                return {key: Property((self, key), kinds[key]) for key in self.values}

            def get_parameters(self):
                return dict(self.values)

            def set_parameters(self, values):
                if sim.state == "parameter-failure":
                    raise RuntimeError("simulated parameter failure")
                for key, value in values.items():
                    if key not in self.values:
                        raise KeyError(f"Property '{key}' not found")
                    if type(value) is bool:
                        raise TypeError("expected int32 but got bool")
                    self.values[key] = value

        module.source = SimpleNamespace(SourceMode=SourceMode, SourceBitmap=Bitmap,
                                        SourceUniformColor=UniformColor, SourceSubstance=Substance)

        class Channel:
            def __init__(self, fmt):
                self.fmt = fmt

            def format(self):
                return SimpleNamespace(name=self.fmt)

            def is_color(self):
                return self.fmt.startswith(("sRGB", "RGB"))

        class TextureSet:
            material_id = 1

            def name(self):
                return "Body"

            def get_resolution(self):
                return SimpleNamespace(width=2048, height=2048)

            def has_uv_tiles(self):
                return sim.state == "udim"

            def all_uv_tiles(self):
                if sim.state != "udim":
                    return [SimpleNamespace(u=0, v=0)]
                return [SimpleNamespace(u=0, v=0), SimpleNamespace(u=1, v=0)]

            def get_mesh_map_resource(self, usage):
                return sim.mesh_maps.get(usage)

        texture_set = TextureSet()
        stack = SimpleNamespace(stack_id=7, all_channels=lambda: {
            channel: Channel(fmt) for channel, fmt in STACK_CHANNELS.items()})

        def from_name(name):
            if name != "Body":
                raise ValueError("Texture Set not found")
            return stack

        def texture_set_named(name):
            from_name(name)
            return texture_set

        module.textureset = SimpleNamespace(
            all_texture_sets=lambda: [texture_set],
            Stack=SimpleNamespace(from_name=from_name),
            TextureSet=SimpleNamespace(from_name=texture_set_named),
            ChannelType=ChannelType, MeshMapUsage=MeshMapUsage,
        )

        class Node:
            layer = False

            def __init__(self, name):
                sim.register(self)
                self.name, self.visible = name, True
                self.container, self.parent, self.in_mask = None, None, False
                self.modes = {}
                self.opacities = {}

            def uid(self):
                return self.uid_value

            def get_name(self):
                return self.name

            def set_name(self, name):
                if sim.state == "rename-failure" and name.startswith("Fail"):
                    raise RuntimeError("simulated rename failure")
                self.name = name

            def is_visible(self):
                return self.visible

            def set_visible(self, visible):
                self.visible = visible

            def get_texture_set(self):
                return texture_set

            def get_parent(self):
                return self.parent

            def has_blending(self):
                return True

            def is_in_mask_stack(self):
                return self.in_mask

            def check_channel(self, channel):
                if self.in_mask != (channel is None):
                    raise ValueError("Channel must be None exactly for mask nodes")
                if channel is not None and channel not in STACK_CHANNELS:
                    raise ValueError("Channel not in stack")

            def get_blending_mode(self, channel=None):
                self.check_channel(channel)
                return self.modes.get(channel, BlendingMode.Normal)

            def set_blending_mode(self, mode, channel=None):
                self.check_channel(channel)
                if sim.state == "blend-failure" and channel == ChannelType.Height:
                    raise RuntimeError("simulated blending failure")
                self.modes[channel] = mode

            def get_opacity(self, channel=None):
                self.check_channel(channel)
                return self.opacities.get(channel, 1.0)

            def set_opacity(self, opacity, channel=None):
                self.check_channel(channel)
                if not 0 <= opacity <= 1:
                    raise ValueError("Opacity out of range")
                self.opacities[channel] = opacity

            def children_nodes(self):
                return []

        class LayerNode(Node):
            layer = True

            def __init__(self, name):
                super().__init__(name)
                self.mask, self.mask_enabled, self.masks, self.content = None, True, [], []

            def has_mask(self):
                return self.mask is not None

            def add_mask(self, background):
                if self.mask is not None:
                    raise ValueError("A mask is already set")
                self.mask = background

            def remove_mask(self):
                if self.mask is None:
                    raise ValueError("No mask")
                for effect in list(self.masks):
                    delete_node(effect)
                self.mask = None

            def get_mask_background(self):
                if self.mask is None:
                    raise ValueError("No mask")
                return self.mask

            def is_mask_enabled(self):
                if self.mask is None:
                    raise ValueError("No mask")
                return self.mask_enabled

            def mask_effects(self):
                return list(self.masks)

            def content_effects(self):
                return list(self.content)

            def children_nodes(self):
                return [*self.masks, *self.content]

        class FillSource:
            source_mode = SourceMode.Split

            def init_fill(self):
                self.sources, self.material = {}, None
                self.active = set()

            @property
            def active_channels(self):
                return set(self.active)

            @active_channels.setter
            def active_channels(self, channels):
                if not set(channels) <= set(STACK_CHANNELS):
                    raise ValueError("Channel not in stack")
                self.active = set(channels)

            def set_source(self, channel, color):
                self_simulator.last_channel = channel
                if self.material is not None:
                    raise ValueError("Fill is in material mode")
                if channel not in self.active:
                    raise RuntimeError("EditionContextException: channel not active")
                if self_simulator.state == "fill-failure" and channel == ChannelType.Roughness:
                    raise RuntimeError("simulated source assignment failure")
                if (self_simulator.state in {"bitmap-failure", "locked-bitmap-failure"}
                        and isinstance(color, ResourceID)):
                    raise RuntimeError("simulated bitmap assignment failure")
                source = Bitmap(color) if isinstance(color, ResourceID) else color
                self.sources[channel] = source
                return source

            def get_source(self, channel):
                if self.material is not None:
                    raise RuntimeError("Fill is in material mode")
                source = self.sources[channel]
                return source if isinstance(source, Bitmap) else UniformColor(source)

            def set_material_source(self, identifier):
                found = retrieve(identifier)
                if not found or Usage.BASE_MATERIAL not in found[0].usages():
                    raise ValueError("Not a material resource")
                if found[0].type() == ResourceType.SUBSTANCE_PACKAGE:
                    raise ValueError("A package is not a material graph")
                self.material = Substance(identifier)
                self.source_mode = SourceMode.Material
                self.active = MATERIAL_CHANNELS & set(STACK_CHANNELS)
                return self.material

            def get_material_source(self):
                if self.material is None:
                    raise RuntimeError("Fill is not in material mode")
                return self.material

        class FillLayer(FillSource, LayerNode):
            def __init__(self):
                super().__init__("Fill layer")
                self.init_fill()

        class GroupLayer(LayerNode):
            def __init__(self, name="Folder 1"):
                super().__init__(name)
                self.children = []

            def sub_layers(self):
                return list(self.children)

            def children_nodes(self):
                return [*super().children_nodes(), *self.children]

        class FillEffect(FillSource, Node):
            def __init__(self, name="Fill"):
                super().__init__(name)
                self.init_fill()

        class GeneratorEffect(Node):
            def __init__(self, name="Generator"):
                super().__init__(name)
                self.source = None

            def get_source(self):
                return self.source

            def set_source(self, identifier):
                found = retrieve(identifier)
                if not found or Usage.GENERATOR not in found[0].usages():
                    raise ValueError("Not a generator resource")
                self.source = Substance(identifier)
                return self.source

        def target(position, layer):
            node = position.node
            if position.kind == "stack":
                if not layer:
                    raise ValueError("Only layers go at the top of a stack")
                return self.layers, None, False
            if position.kind == "above":
                if node.uid() not in self.nodes or node.layer != layer:
                    raise ValueError("Invalid insert position")
                return node.container, node.parent, node.in_mask
            if node.uid() not in self.nodes:
                raise ValueError("Invalid insert position")
            if position.where == NodeStack.Substack and layer and isinstance(node, GroupLayer):
                return node.children, node, False
            if position.where == NodeStack.Content and not layer and node.layer:
                return node.content, node, False
            if position.where == NodeStack.Mask and not layer and node.layer:
                if node.mask is None:
                    raise ValueError("Layer has no mask")
                return node.masks, node, True
            raise ValueError("Invalid insert position")

        def place(position, factory):
            layer = position.kind == "stack" or (
                position.kind == "above" and position.node.layer) or (
                position.kind == "inside" and position.where == NodeStack.Substack)
            container, parent, in_mask = target(position, layer)
            node = factory(layer)
            index = container.index(position.node) if position.kind == "above" else 0
            container.insert(index, node)
            node.container, node.parent, node.in_mask = container, parent, in_mask
            return node

        def insert_fill(position):
            return place(position, lambda layer: FillLayer() if layer else FillEffect())

        def insert_group(position):
            def make(layer):
                if not layer:
                    raise ValueError("Groups are layers")
                return GroupLayer()
            return place(position, make)

        def resolved(identifier, usage):
            found = retrieve(identifier)
            if not found or usage not in found[0].usages():
                raise ValueError(f"Resource lacks usage {usage.name}")
            return found[0]

        def insert_smart_material(position, identifier):
            item = resolved(identifier, Usage.SMART_MATERIAL)

            def make(layer):
                if not layer:
                    raise ValueError("Smart materials are layers")
                group = GroupLayer(item.gui_name())
                child = FillLayer()
                child.container, child.parent = group.children, group
                group.children.append(child)
                return group
            return place(position, make)

        def insert_smart_mask(position, identifier):
            resolved(identifier, Usage.SMART_MASK)
            if position.kind == "stack" or (position.kind == "inside"
                                            and position.where != NodeStack.Mask):
                raise ValueError("Smart masks go in a mask stack")
            generator = place(position, lambda layer: GeneratorEffect("Mask Builder - Legacy"))
            fill = place(position, lambda layer: FillEffect("Grunge Concrete"))
            return [fill, generator]

        def insert_generator_effect(position, identifier=None):
            item = None if identifier is None else resolved(identifier, Usage.GENERATOR)

            def make(layer):
                if layer:
                    raise ValueError("Generators are effects")
                effect = GeneratorEffect(item.gui_name() if item else "Generator")
                if item is not None:
                    effect.set_source(identifier)
                return effect
            return place(position, make)

        def delete_node(node):
            if node.uid() not in self.nodes:
                raise ValueError("Unknown node")
            if sim.state == "delete-failure":
                raise RuntimeError("simulated delete failure")
            for child in node.children_nodes():
                delete_node(child)
            node.container.remove(node)
            del self.nodes[node.uid()]

        def get_node_by_uid(uid):
            if uid not in self.nodes:
                raise ValueError("Invalid node uid")
            return self.nodes[uid]

        module.colormanagement = SimpleNamespace(
            Color=lambda r, g, b, color_space=None: SimpleNamespace(
                value=(r, g, b), value_raw=(r, g, b), color_space=color_space),
            GenericColorSpace=SimpleNamespace(sRGB="sRGB", Raw="Raw"),
            LegacyColorSpace=SimpleNamespace(sRGB="srgb"),
        )

        @contextmanager
        def modification(name):
            self.modifying = True
            try:
                yield
            finally:
                self.modifying = False

        module.layerstack = SimpleNamespace(
            Node=Node, LayerNode=LayerNode, FillLayerNode=FillLayer, GroupLayerNode=GroupLayer,
            FillEffectNode=FillEffect, GeneratorEffectNode=GeneratorEffect,
            BlendingMode=BlendingMode, MaskBackground=MaskBackground, NodeStack=NodeStack,
            get_root_layer_nodes=lambda requested: list(self.layers),
            get_node_by_uid=get_node_by_uid,
            InsertPosition=SimpleNamespace(
                from_textureset_stack=lambda requested: Position("stack"),
                above_node=lambda node: Position("above", node),
                inside_node=lambda node, where: Position("inside", node, where)),
            insert_fill=insert_fill, insert_group=insert_group,
            insert_smart_material=insert_smart_material, insert_smart_mask=insert_smart_mask,
            insert_generator_effect=insert_generator_effect,
            delete_node=delete_node,
            ScopedModification=modification,
        )

        def open_project(*args, **kwargs):
            self.opened = True
            self.project_id += "-opened"

        def close_project():
            self.opened = False

        def create_project(mesh_file_path, mesh_map_file_paths=None, template_file_path=None,
                           settings=None):
            assert isinstance(settings, Settings)
            self.created_settings = settings
            self.created_template = template_file_path
            open_project()

        def save_copy(path, mode):
            Path(path).write_bytes(b"simulated-spp")

        def file_path():
            if self.state == "dirty-unsaved":
                return None
            if self.state.startswith("workspace"):
                return str(self.workspace.resolve() / "input.spp")
            return "simulated-original.spp"

        def save():
            assert self.state != "busy" and file_path()
            Path(file_path()).write_bytes(b"simulated-saved-spp")
            if self.state == "workspace-dirty":
                self.state = "workspace-saved"

        def busy():
            if self.loading:
                self.loading -= 1
                if not self.loading and self.reload_callback:
                    callback, self.reload_callback = self.reload_callback, None
                    callback(ReloadMeshStatus.ERROR if self.state == "reload-error"
                             else ReloadMeshStatus.SUCCESS)
                return True
            return self.state == "busy" or self.bake_running

        @dataclasses.dataclass
        class Settings:
            default_save_path: str = None
            normal_map_format: object = None
            tangent_space_mode: object = None
            project_workflow: object = None
            export_path: str = None
            default_texture_resolution: int = None
            import_cameras: bool = None

        @dataclasses.dataclass
        class MeshReloadingSettings:
            import_cameras: bool = True
            preserve_strokes: bool = True

        def reload_mesh(path, settings, callback):
            if not self.opened or busy():
                raise RuntimeError("ProjectError: no project or busy")
            assert isinstance(settings, MeshReloadingSettings) and Path(path).is_file()
            self.reload_settings = settings
            self.reload_callback = callback
            self.loading = 2

        module.project = SimpleNamespace(
            is_open=lambda: self.opened,
            get_uuid=lambda: self.project_id,
            is_busy=busy,
            is_in_edition_state=lambda: self.opened and not self.loading,
            file_path=file_path,
            needs_saving=lambda: self.state in {"dirty", "dirty-unsaved", "workspace-dirty"},
            open=open_project,
            create=create_project,
            close=close_project,
            save=save,
            save_as_copy=save_copy,
            reload_mesh=reload_mesh,
            Settings=Settings,
            MeshReloadingSettings=MeshReloadingSettings,
            ReloadMeshStatus=ReloadMeshStatus,
            ProjectWorkflow=ProjectWorkflow,
            TangentSpace=TangentSpace,
            NormalMapFormat=SimpleNamespace(OpenGL="OpenGL", DirectX="DirectX"),
            ProjectSaveMode=SimpleNamespace(Full="Full"),
        )

        dispatcher = Dispatcher()
        module.event = SimpleNamespace(
            DISPATCHER=dispatcher, BakingProcessEnded=BakingProcessEnded,
            BakingProcessProgress=BakingProcessProgress,
            ProjectEditionEntered=ProjectEditionEntered)

        combos = {"CageMode": (("Distance-based", 0), ("Automatic (experimental)", 1),
                               ("Custom file", 2)),
                  # Labels below are simulator assumptions; the payload resolves by token.
                  "FilterMethod": (("Always", 0), ("By Mesh Name", 1)),
                  "SubSampling": (("None", 0), ("Subsampling 2x2", 1), ("Subsampling 4x4", 2),
                                  ("Subsampling 8x8", 3))}

        @dataclasses.dataclass(frozen=True)
        class BakingParameters:
            material_id: int

            @staticmethod
            def from_texture_set(item):
                return BakingParameters(item.material_id)

            @staticmethod
            def from_texture_set_name(name):
                return BakingParameters(texture_set_named(name).material_id)

            def common(self):
                owner = sim.bake["owner"]
                hidden = {"MaxHeight"} if sim.state == "bake-legacy-params" else set()
                return {key: Property((owner, key), "Combobox" if key in combos else "Slider",
                                      combos.get(key, ()))
                        for key in owner.values if key not in hidden}

            def baker(self, usage):
                return {}

            @staticmethod
            def set(values):
                for prop, value in values.items():
                    key, current = prop.short_name(), prop.value()
                    if key in combos and value not in dict(combos[key]).values():
                        raise ValueError(f"Bad enum value for {key}")
                    if key == "OutputSize" and (not isinstance(value, tuple) or any(
                            type(v) is not int or not 7 <= v <= 13 for v in value)):
                        raise ValueError("OutputSize expects log2 pairs")
                    if key in {"HipolyMesh", "CageMesh"} and value and any(
                            not url.startswith("file:///") for url in value.split("|")):
                        raise ValueError(f"{key} expects file URLs")
                    if type(value) is not type(current):
                        raise ValueError(f"Bad value type for {key}")
                for prop, value in values.items():
                    prop.handle[0].values[prop.short_name()] = value

            def get_curvature_method(self):
                return sim.bake["curvature"]

            def is_textureset_enabled(self):
                return sim.bake["enabled"]

            def set_textureset_enabled(self, enable):
                sim.bake["enabled"] = enable

            def get_enabled_bakers(self):
                return list(sim.bake["bakers"])

            def set_enabled_bakers(self, usages):
                assert all(isinstance(usage, MeshMapUsage) for usage in usages)
                sim.bake["bakers"] = list(usages)

        def bake_selected():
            if sim.state == "bake-start-failure":
                raise RuntimeError("simulated bake launch failure")
            sim.bake_running = True
            sim.bake_calls += 1
            if sim.state == "bake-instant":
                sim.finish_bake(BakingStatus.Success)
            return StopSource()

        module.baking = SimpleNamespace(
            BakingParameters=BakingParameters, MeshMapUsage=MeshMapUsage,
            BakingStatus=BakingStatus, CurvatureMethod=CurvatureMethod,
            bake_selected_textures_async=bake_selected,
        )
        success, failure = SimpleNamespace(name="Success"), SimpleNamespace(name="Error")

        def parameters(config, item):
            resolved = dict(item.get("parameters", {}))
            for rule in config.get("exportParameters", []):
                if not rule.get("filter"):
                    resolved.update(rule.get("parameters", {}))
            return resolved

        def custom_plan(config, preset, folder):
            planned = {}
            for entry in config["exportList"]:
                if entry["rootPath"] != "Body":
                    raise ValueError("Invalid rootPath")
                tiles = entry.get("filter", {}).get("uvTiles") or [[0, 0]]
                for item in preset["maps"]:
                    dests = set()
                    for channel in item["channels"]:
                        kind, source = channel["srcMapType"], channel["srcMapName"]
                        valid = {"documentMap": source.lower() in DOCUMENT_MAPS,
                                 "meshMap": source in MESH_MAPS,
                                 "virtualMap": source in VIRTUAL_MAPS,
                                 "defaultMap": source in {"black", "white"}}.get(kind, False)
                        if not valid or channel["srcChannel"] not in {"L", "R", "G", "B", "A"}:
                            raise ValueError(f"Invalid map source {kind}/{source}")
                        dests.add(channel["destChannel"])
                    if dests - {"A"} not in ({"R", "G", "B"}, {"L"}):
                        raise ValueError("Invalid destination channels")
                    params = parameters(config, item)
                    fmt, depth = params.get("fileFormat"), params.get("bitDepth")
                    if depth not in FORMATS.get(fmt, ()):
                        raise ValueError(f"Unsupported {fmt} bit depth {depth}")
                    for u, v in tiles:
                        name = (item["fileName"].replace("$textureSet", "Body")
                                .replace("$mesh", "mesh").replace("$project", "project")
                                .replace("$udim", str(1001 + u + 10 * v) if entry.get("filter")
                                         else "").replace("$colorSpace", ""))
                        name = re.sub(r"[()]", "", name)
                        planned.setdefault("Body", []).append(str(folder / f"{name}.{fmt}"))
            return planned

        def plan(config):
            if type(config.get("exportShaderParams")) is not bool:
                raise ValueError("@/exportShaderParams, Expected a boolean value")
            rules = [item.get("parameters", {}) for item in config.get("exportParameters", [])
                     if not item.get("filter")]
            padding = [rule["paddingAlgorithm"] for rule in rules if "paddingAlgorithm" in rule]
            if not padding:
                raise ValueError("padding algorithm could not be resolved")
            if padding[-1] not in PADDINGS:
                raise ValueError("Unknown padding algorithm")
            if padding[-1] in {"color", "transparent", "diffusion"} and not any(
                    "dilationDistance" in rule for rule in rules):
                raise ValueError("dilation distance could not be resolved")
            for rule in rules:
                size = rule.get("sizeLog2")
                if size is not None and (type(size) is not int or not 7 <= size <= 13):
                    raise ValueError("Invalid sizeLog2")
            folder = Path(config["exportPath"])
            custom = {item["name"]: item for item in config.get("exportPresets", [])}
            if config.get("defaultExportPreset") in custom:
                return custom_plan(config, custom[config["defaultExportPreset"]], folder)
            if config.get("defaultExportPreset") != "resource://eval-preset":
                raise ValueError("Unknown export preset")
            if self.state == "empty-plan":
                return {}
            if self.state == "escape-plan":
                return {"Body": [str(folder.parent / "escaped.png")]}
            if self.state == "omitted-export":
                return {"Body": [str(folder / "Body.png"), str(folder / "Body_AO.png")]}
            return {"Body": [str(folder / "Body.png")]}

        def export(config):
            self.exported_config = config
            outputs = plan(config)
            if self.state == "omitted-export":
                outputs = {"Body": outputs["Body"][:1]}
            for paths in outputs.values():
                for path in paths:
                    Path(path).write_bytes(
                        b"" if self.state == "empty-export" else b"simulated-png"
                    )
            return SimpleNamespace(
                status=failure if self.state == "failed-export" else success,
                message="simulated export",
                textures=outputs,
            )

        def listed(config):
            self.planned_config = config
            return plan(config)

        module.export = SimpleNamespace(
            list_predefined_export_presets=lambda: [
                SimpleNamespace(name="Eval", url="resource://eval-preset")
            ],
            list_project_textures=listed,
            export_project_textures=export,
            ExportStatus=SimpleNamespace(Success=success),
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                assert self.path == "/run.json"
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                expression = base64.b64decode(request["python"], validate=True).decode()
                self.server.requests.append(len(expression))
                try:
                    result = eval(expression)
                except Exception as exc:  # noqa: BLE001 -- mirror the remote scripting error boundary
                    result = {"error": {"description": f"{type(exc).__name__}: {exc}"}}
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests = self.requests = []
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.previous is None:
            sys.modules.pop("substance_painter", None)
        else:
            sys.modules["substance_painter"] = self.previous
