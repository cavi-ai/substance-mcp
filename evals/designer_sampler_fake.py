"""Explicitly fake Sampler for CI: runs the real worker against a stub substance_sampler."""

import json
import runpy
import sys
import time
import types
from pathlib import Path

from PIL import Image

VERSION = "0.0.0-fake"
FILTERS = ["Make It Tile", "Delighter (AI Powered)", "Equalize"]
CHANNELS = {"BaseColor": ("RGB", (150, 80, 60)), "Normal": ("RGB", (128, 128, 255)),
            "Roughness": ("L", 180), "Metallic": ("L", 0), "Height": ("L", 128),
            "AmbientOcclusion": ("L", 230)}
TILED = {"Opacity": ("L", 255), "SpecularLevel": ("L", 128)}


class Resource:
    def __init__(self, name):
        self.name = name


class Layer:
    def __init__(self, name):
        self.name = name


class Asset:
    def __init__(self, name):
        self.name = name
        self.layers = []
        self.images = []

    def get_layers(self):
        return [Layer(name) for name in self.layers]

    def import_images(self, images, position, import_option, resolution_options=None, **_):
        self.images = list(images)
        self.size = resolution_options.document_resolution[0] if resolution_options else 2048
        self.layers += ["Image import", import_option]
        if any("hang" in Path(item).name for item in images):
            time.sleep(600)
        if any("fail" in Path(item).name for item in images):
            raise RuntimeError("fake import failure")
        return Layer(import_option)

    def insert_filter(self, filter, position, insert_above=True):
        self.layers.insert(position + int(insert_above), filter.name)
        return Layer(filter.name)

    def export_material(self, path, name="", format="sbsar", w_resolution=0, h_resolution=0,
                        controller=None, **_):
        if any("silent" in Path(item).name for item in self.images) or self.name == "silent":
            return path
        channels = dict(CHANNELS)
        if "Make It Tile" in self.layers:
            channels.update(TILED)
        for channel, (mode, value) in channels.items():
            target = Path(path) / f"{name or self.name}_{channel}.{format}"
            if format == "exr":
                target.write_bytes(b"fake-exr")
            else:
                Image.new(mode, (w_resolution, h_resolution), value).save(target)
        return path


class ExportController:
    def wait(self):
        return None


class ImageResolutionOptions:
    def __init__(self, add_crop=False, upscale=False, policy="forced",
                 document_resolution=(2048, 2048)):
        self.policy = policy
        self.document_resolution = list(document_resolution)


def module():
    state = {"assets": [], "path": None}
    stub = types.ModuleType("substance_sampler")

    def create_project(name, path):
        state["assets"], state["path"] = [], path

    def create_asset(name, *_args, select_asset=False, **_kwargs):
        asset = Asset(name)
        state["assets"].append(asset)
        return asset

    def open_project(path):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return ""
        state["assets"] = [Asset(name) for name in data.get("assets", [])]
        for asset in state["assets"]:
            asset.layers = ["Image import", "image_to_material_B2M"]
        state["path"] = path
        return path

    def save_project_as(path):
        Path(path).write_text(json.dumps({"assets": [a.name for a in state["assets"]]}))
        return path

    names = {
        "create_project": create_project,
        "create_asset": create_asset,
        "open_project": open_project,
        "save_project_as": save_project_as,
        "get_project_assets": lambda: list(state["assets"]),
        "get_filters": lambda name="": [Resource(n) for n in FILTERS if name.lower() in n.lower()],
        "wait_for_computation": lambda: None,
        "ExportController": ExportController,
        "ImageResolutionOptions": ImageResolutionOptions,
        "ImageImportResolutionPolicy": types.SimpleNamespace(forced="forced"),
        "ImageImportOption": types.SimpleNamespace(
            image_to_material_AI_powered="Image to Material (AI Powered)",
            image_to_material_B2M="Image to Material (B2M)",
            multiangle_to_material="Multiangle To Material"),
        "MaterialExportFormat": types.SimpleNamespace(png="png", exr="exr", tif="tif"),
    }
    for key, value in names.items():
        setattr(stub, key, value)
    return stub


def main(argv):
    if "--idle" in argv:
        time.sleep(120)
        return
    if "--run-script-silent" not in argv:
        print("fake Sampler only runs --run-script-silent jobs", file=sys.stderr)
        raise SystemExit(2)
    script = argv[argv.index("--run-script-silent") + 1]
    extra = [argv[i + 1] for i, item in enumerate(argv) if item in ("-S", "--script-arg")]
    job = json.loads(Path(extra[0]).read_text()) if extra else {}
    if any("noscript" in Path(item).name for item in job.get("images", [])):
        print("[error] fake: script skipped", file=sys.stderr)
        return
    sys.modules["substance_sampler"] = module()
    sys.argv = [script, *extra]
    runpy.run_path(script, run_name="__main__")


def install(root: Path) -> Path:
    app = Path(root) / "Fake Sampler.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    (app / "Contents/Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
        "<key>CFBundleExecutable</key><string>Adobe Substance 3D Sampler</string>"
        f"<key>CFBundleShortVersionString</key><string>{VERSION}</string>"
        "</dict></plist>")
    executable = macos / "Adobe Substance 3D Sampler"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "import designer_sampler_fake\ndesigner_sampler_fake.main(sys.argv[1:])\n")
    executable.chmod(0o755)
    return app
