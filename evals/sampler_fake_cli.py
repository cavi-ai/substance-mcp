"""Explicitly fake Sampler executable for CI; mirrors the documented Python API."""

import json
import os
import sys
import threading
import time
import types
from pathlib import Path

# The variable that points the host at an install made by install().
ENV = "CAVI_SAMPLER_BIN"
VERSION = "0.0.0-fake"
FILTERS = [
    "Equalize", "Tiling", "Brightness/Contrast", "Grain", "Make It Tile",
    "Delighter (AI Powered)",
]
MATERIALS = ["Brick", "Fabric", "Metal"]
GENERATORS = ["Noise", "Gradient", "Cells"]
PRESETS = ["Unreal Engine 4", "Blender Cycles/Eevee", "Unity HD Render Pipeline Standard"]
PRESET_MAPS = {
    "Unreal Engine 4": ["basecolor", "normal", "occlusionroughnessmetallic"],
    "Unity HD Render Pipeline Standard": ["basecolor", "normal", "maskmap"],
    "Blender Cycles/Eevee": ["basecolor", "normal", "roughness", "metallic"],
}


class Resource:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"Resource({self.name})"


class Usage:
    def __init__(self, name):
        self.id = name
        self.label = name


class Parameter:
    def __init__(self, identifier, label, value=None, kind="float"):
        self.identifier = identifier
        self.label = label
        self._value = value
        self._data = None
        self.type = kind
        self.widget_type = "slider" if kind == "float" else "no_widget"
        self.default_value = value
        self.min_value = -1000.0 if kind.startswith(("float", "integer")) else None
        self.max_value = 1000.0 if kind.startswith(("float", "integer")) else None
        self.visible = True

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        if self._data is not None:
            self._data[2] = value

    def enum_values(self):
        if self.type.startswith("integer"):
            return [["Off", 0], ["On", 1]]
        return []

    def value_labels(self):
        size = {"float2": 2, "float3": 3, "float4": 4,
                "integer2": 2, "integer3": 3, "integer4": 4}.get(self.type)
        return ["XYZW"[index] for index in range(size)] if size else []


class Layer:
    def __init__(self, data, state=None):
        self.data = data
        self.state = state

    @property
    def name(self):
        return self.data["name"]

    @property
    def type(self):
        return self.data["type"]

    @property
    def position(self):
        return self.data["position"]

    @property
    def output_usages(self):
        return list(self.data.get("output_usages", []))

    @property
    def applied_channels(self):
        return list(self.data.get("applied_channels", []))

    @property
    def blend_applied_channels(self):
        return list(self.data.get("blend_applied_channels", []))

    @property
    def blend_mode(self):
        return self.data.get("blend_mode")

    @property
    def image_names(self):
        return list(self.data.get("image_names", []))

    @property
    def presets(self):
        return list(self.data.get("presets", []))

    @property
    def blend_parameters(self):
        found = []
        for item in self.data.get("blend_parameters", []):
            parameter = Parameter(*item)
            parameter._data = item
            found.append(parameter)
        return found

    def applies_to(self, channel_names):
        self.data["applied_channels"] = [
            getattr(item, "id", str(item)) for item in channel_names
        ]
        if self.state is not None:
            self.state.save()
        return True

    def blend_applies_to(self, channel_names):
        self.data["blend_applied_channels"] = [
            getattr(item, "id", str(item)) for item in channel_names
        ]
        if self.state is not None:
            self.state.save()
        return True

    def apply_preset(self, preset_name):
        if preset_name not in self.presets:
            return False
        self.data["preset"] = preset_name
        return True

    @property
    def parameters(self):
        found = []
        for item in self.data.get("parameters", []):
            parameter = Parameter(*item)
            parameter._data = item
            found.append(parameter)
        return found


class Metadata:
    def __init__(self, data):
        self.data = data
        self.data.setdefault("author", "fake")
        self.data.setdefault("category", "material")
        self.data.setdefault("description", "fake asset")
        self.data.setdefault("tags", [])
        self.data.setdefault("physical_size", [1.0, 1.0, 1.0])
        self.data.setdefault("custom_metadata", {})

    @property
    def name(self):
        return self.data["name"]

    @property
    def author(self):
        return self.data['author']

    @author.setter
    def author(self, value):
        self.data['author'] = value

    @property
    def category(self):
        return self.data['category']

    @category.setter
    def category(self, value):
        self.data['category'] = value

    @property
    def description(self):
        return self.data['description']

    @description.setter
    def description(self, value):
        self.data['description'] = value

    @property
    def tags(self):
        return self.data['tags']

    @tags.setter
    def tags(self, value):
        self.data['tags'] = value

    @property
    def physical_size(self):
        return tuple(self.data['physical_size'])

    @physical_size.setter
    def physical_size(self, value):
        self.data['physical_size'] = list(value)

    @property
    def custom_metadata(self):
        return self.data['custom_metadata']

    @custom_metadata.setter
    def custom_metadata(self, value):
        self.data['custom_metadata'] = value


class Asset:
    def __init__(self, state, data):
        self.state = state
        self.data = data

    @property
    def name(self):
        return self.data["name"]

    @name.setter
    def name(self, value):
        self.data["name"] = value

    @property
    def type(self):
        return "material"

    @property
    def material_model(self):
        return self.data.get("material_model", "asm")

    @property
    def document_resolution(self):
        return tuple(self.data.get("resolution", [1024, 1024]))

    @property
    def metadata(self):
        return Metadata(self.data)

    @property
    def resource(self):
        return Resource(self.name)

    def get_layers(self):
        return [Layer(item, self.state) for item in self.data.get("layers", [])]

    def _insert(self, layer, position, insert_above):
        layers = self.data.setdefault("layers", [])
        index = max(0, min(position, len(layers)))
        if insert_above and index < len(layers):
            index += 1
        layers.insert(index, layer)
        for number, item in enumerate(layers):
            item["position"] = number
        self.state.save()
        return Layer(layer, self.state)

    def import_images(self, images, position, import_option, insert_above=False, **_):
        layer = {
            "name": "Image Import",
            "type": "layer_image_import",
            "position": position,
            "output_usages": ["baseColor"],
            "applied_channels": ["baseColor"],
            "image_names": [Path(item).stem for item in images],
            "parameters": [["Input Image", "Input Image", os.path.basename(images[0]), "image"]],
        }
        return self._insert(layer, position, insert_above)

    def import_textures(self, images, position, insert_above=True, **_):
        layer = {
            "name": "Texture Import",
            "type": "layer_image_import",
            "position": position,
            "output_usages": sorted(images),
            "applied_channels": sorted(images),
            "image_names": [Path(item).stem for item in images.values()],
            "parameters": [],
        }
        return self._insert(layer, position, insert_above)

    def insert_filter(self, filter, position, insert_above=True, **_):
        layer = {
            "name": filter.name,
            "type": "layer_filter",
            "position": position,
            "output_usages": ["baseColor"],
            "applied_channels": ["baseColor"],
            "parameters": [["opacity", "Opacity", 1.0, "float"],
                           ["tiling", "Tiling", 1.0, "float"],
                           ["detect_edges", "Detect Edges", 1, "integer"]],
            "presets": ["Default", "Wet"],
        }
        return self._insert(layer, position, insert_above)

    def insert_material(self, material, position, blend_mode, insert_above=True, **_):
        layer = {
            "name": material.name,
            "type": "layer_material",
            "position": position,
            "output_usages": ["baseColor", "normal", "roughness", "metallic"],
            "blend_mode": blend_mode,
            "blend_applied_channels": ["baseColor"],
            "parameters": [],
            "blend_parameters": [["opacity", "Opacity", 1.0, "float"]],
            "presets": ["Default"],
        }
        return self._insert(layer, position, insert_above)

    def remove_layer(self, index):
        del self.data["layers"][index]
        self.state.save()

    def export_material(self, path, name="", format="sbsar", channels=(), controller=None,
                        w_resolution=0, h_resolution=0, **options):
        if "fail" in self.name.lower():
            raise RuntimeError("fake export failure")
        if "slow" in self.name.lower():
            time.sleep(float(os.environ.get("FAKE_SAMPLER_SLOW", "30")))
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        name = name or self.name
        if "silent" in self.name.lower():
            return str(target)
        fmt = str(format)
        mode = os.environ.get("FAKE_SAMPLER_EXPORT")
        if fmt in ("sbsar", "sbs"):
            written = [target / f"{name}.{fmt}"]
        else:
            preset = options.get("preset")
            maps = channels or PRESET_MAPS.get(
                getattr(preset, "name", None), ["basecolor", "normal", "roughness", "metallic"]
            )
            if mode == "missing":
                maps = maps[:-1]
            written = [target / f"{name}_{channel}.{fmt}" for channel in maps]
        for item in written:
            item.write_text("fake")
        if mode == "growing":
            # Sampler flushes large maps after the controller reports completion.
            def finish():
                for _ in range(5):
                    time.sleep(0.08)
                    for item in written:
                        with item.open("a") as stream:
                            stream.write("-late")

            threading.Thread(target=finish).start()
        if controller is not None:
            controller.finished = True
        return str(target)

    def activate_channels(self, channels):
        pass

    def deactivate_channels(self, channels):
        pass

    def flatten(self):
        pass


class ExportController:
    def __init__(self):
        self.finished = False

    def cancel(self):
        pass

    def on_canceled(self):
        pass

    def on_failed(self):
        pass

    def on_finished(self):
        pass

    def wait(self):
        return None

    def wait_for(self, _timeout):
        return "no_timeout"


class State:
    def __init__(self):
        self.path = None
        self.data = {"assets": [], "selected": None}
        self.imported = []

    def load(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        return str(self.path)

    def save(self):
        if self.path is not None:
            self.path.write_text(json.dumps(self.data))

    def assets(self):
        return [Asset(self, item) for item in self.data["assets"]]

    def selected(self):
        if not self.data["assets"]:
            return None
        name = self.data.get("selected")
        for item in self.data["assets"]:
            if item["name"] == name:
                return Asset(self, item)
        return Asset(self, self.data["assets"][0])

    def find(self, name):
        for item in self.data["assets"]:
            if item["name"] == name:
                return Asset(self, item)
        return None


def module():
    state = State()
    module = types.ModuleType("substance_sampler")

    def create_project(name, path):
        state.path = Path(path)
        state.data = {"assets": [], "selected": None}
        state.save()

    def open_project(path):
        if not Path(path).is_file():
            return ""
        return state.load(path)

    def save_project():
        if state.path is None:
            return False
        mode = os.environ.get("FAKE_SAMPLER_SAVE")
        if mode == "fail":
            return False
        if mode == "hang":
            # A save that is killed halfway leaves a truncated project behind.
            state.path.write_text('{"assets": [')
            time.sleep(60)
        state.save()
        return True

    def save_project_as(path):
        state.path = Path(path)
        state.save()
        return str(state.path)

    def get_project_assets():
        return state.assets()

    def get_selected_asset():
        return state.selected()

    def create_asset(name, *args, select_asset=False, **_kwargs):
        item = {"name": name, "layers": [], "resolution": [1024, 1024]}
        if args:
            item["material_model"] = str(args[0])
        if len(args) > 1:
            item["preset"] = getattr(args[1], "name", str(args[1]))
        state.data["assets"].append(item)
        if select_asset:
            state.data["selected"] = name
        state.save()
        return Asset(state, item)

    def delete_asset(asset):
        before = len(state.data["assets"])
        state.data["assets"] = [item for item in state.data["assets"] if item["name"] != asset.name]
        state.save()
        return len(state.data["assets"]) < before

    def get_preset(model=None, preset_name=""):
        return Resource(preset_name)

    def resources(names, query):
        names = list(names) + [item.name for item in state.imported]
        found = [Resource(name) for name in names if query.lower() in name.lower()]
        return found

    def import_resources(path):
        # A sibling "<file>.swap" stands for a file replaced while Sampler imports it.
        swap = Path(str(path) + ".swap")
        if swap.exists():
            Path(path).write_bytes(swap.read_bytes())
        found = [Resource(Path(path).stem)]
        state.imported.extend(found)
        return found

    def wait_for_computation():
        pass

    def run_in_main_thread(callback):
        callback()

    def clear_render_cache():
        pass

    class MaterialExportFormat:
        sbsar = "sbsar"
        sbs = "sbs"
        exr = "exr"
        png = "png"
        jpg = "jpg"
        tga = "tga"
        tif = "tif"

    class MaterialType:
        standard = "standard"
        decal = "decal"
        atlas = "atlas"

    class ImageImportOption:
        image_to_material_B2M = "image_to_material_B2M"
        image_to_material_AI_powered = "image_to_material_AI_powered"
        multiangle_to_material = "multiangle_to_material"
        use_as_bitmap = "use_as_bitmap"
        as_channels = "as_channels"
        HDR_merge = "HDR_merge"
        use_as_environment = "use_as_environment"

    class BlendMode:
        blend_ao = "blend_ao"
        blend_color_id = "blend_color_id"
        blend_curvature = "blend_curvature"
        blend_custom_mask = "blend_custom_mask"
        blend_opacity = "blend_opacity"
        blend_height = "blend_height"
        blend_normal = "blend_normal"
        blend_per_channel = "blend_per_channel"

    class AssetType:
        material = "material"

    class MaterialModel:
        asm = "asm"
        openpbr = "openpbr"

    class PredefinedPreset:
        base_material = "base_material"
        anisotropy = "anisotropy"
        coating = "coating"
        fuzz = "fuzz"
        subsurface = "subsurface"
        transparent = "transparent"

    for name, value in {
        "create_project": create_project,
        "open_project": open_project,
        "save_project": save_project,
        "save_project_as": save_project_as,
        "get_project_path": lambda: str(state.path) if state.path else "",
        "get_project_assets": get_project_assets,
        "get_selected_asset": get_selected_asset,
        "create_asset": create_asset,
        "delete_asset": delete_asset,
        "get_preset": get_preset,
        "get_filters": lambda name="": resources(FILTERS, name),
        "get_materials": lambda name="": resources(MATERIALS, name),
        "get_texture_generators": lambda name="": resources(GENERATORS, name),
        "get_export_presets": lambda name="": resources(PRESETS, name),
        "import_resources": import_resources,
        "wait_for_computation": wait_for_computation,
        "run_in_main_thread": run_in_main_thread,
        "clear_render_cache": clear_render_cache,
        "ImageImportOption": ImageImportOption,
        "BlendMode": BlendMode,
        "MaterialExportFormat": MaterialExportFormat,
        "MaterialType": MaterialType,
        "AssetType": AssetType,
        "MaterialModel": MaterialModel,
        "PredefinedPreset": PredefinedPreset,
        "ExportController": ExportController,
        "Asset": Asset,
        "Layer": Layer,
        "Resource": Resource,
        "Parameter": Parameter,
        "Usage": Usage,
    }.items():
        setattr(module, name, value)
    return module


LAUNCHER = (
    "import runpy, sys\n"
    "sys.path.insert(0, {evals!r})\n"
    "import sampler_fake_cli\n"
    "args = sys.argv[1:]\n"
    "if '--version' in args:\n"
    "    print(sampler_fake_cli.VERSION)\n"
    "    raise SystemExit(0)\n"
    "if '--run-script-silent' in args or '--run-script' in args:\n"
    "    index = args.index('--run-script-silent' if '--run-script-silent' in args "
    "else '--run-script')\n"
    "    script = args[index + 1]\n"
    "    sys.modules['substance_sampler'] = sampler_fake_cli.module()\n"
    "    runpy.run_path(script, run_name='__main__')\n"
    "    import os\n"
    "    raise SystemExit(int(os.environ.get('FAKE_SAMPLER_EXIT', '0')))\n"
)


def install(root: Path) -> Path:
    """Install the fake Sampler under ``root``; return the executable ``ENV`` must name.

    One launcher file, not a macOS bundle, so the same install works on every platform: a
    shebang script on POSIX, a ``.cmd`` wrapper on Windows (see launcher.write_launcher).
    """
    from launcher import write_launcher  # only the test process installs; the tool never needs it

    return write_launcher(
        Path(root) / "fake-sampler", "Adobe Substance 3D Sampler",
        LAUNCHER.format(evals=str(Path(__file__).resolve().parent)))


def main():
    if "--version" in sys.argv[1:]:
        print(VERSION)
        return
    print("fake Sampler only runs through tests/test_sampler.py", file=sys.stderr)


if __name__ == "__main__":
    main()
