"""Fixed Sampler startup script: sys.argv[1] is the job JSON written by designer.sampler."""

import json
import sys
import traceback
from pathlib import Path

IMPORTS = {
    "ai": "image_to_material_AI_powered",
    "b2m": "image_to_material_B2M",
    "multiangle": "multiangle_to_material",
}
DELIGHT = "Delighter (AI Powered)"
TILING = "Make It Tile"
PREFIX = "sampler"


def find_filter(ssa, name):
    for item in ssa.get_filters(name):
        if item.name == name:
            return item
    raise RuntimeError(f"Sampler filter {name!r} not found")


def pick_asset(ssa, name):
    assets = list(ssa.get_project_assets())
    if name is not None:
        assets = [item for item in assets if item.name == name]
        if not assets:
            raise ValueError(f"Asset {name!r} not found in project")
    if len(assets) != 1:
        found = ", ".join(item.name for item in ssa.get_project_assets()[:32])
        raise ValueError(f"Project needs exactly one asset or an asset name; has: {found}")
    return assets[0]


def image_to_material(ssa, job):
    size = job["resolution"]
    ssa.create_project(job["name"], job["scratch"])
    ssa.wait_for_computation()
    asset = ssa.create_asset(job["name"], select_asset=True)
    options = ssa.ImageResolutionOptions(
        policy=ssa.ImageImportResolutionPolicy.forced, document_resolution=[size, size])
    asset.import_images(job["images"], 0, getattr(ssa.ImageImportOption, IMPORTS[job["mode"]]),
                        resolution_options=options)
    ssa.wait_for_computation()
    for wanted, name in ((job["delight"], DELIGHT), (job["tiling"], TILING)):
        if wanted:
            top = len(asset.get_layers()) - 1
            asset.insert_filter(find_filter(ssa, name), top, insert_above=True)
            ssa.wait_for_computation()
    return asset


def export(ssa, asset, job):
    controller = ssa.ExportController()
    asset.export_material(
        path=job["output"], name=PREFIX, format=getattr(ssa.MaterialExportFormat, job["format"]),
        w_resolution=job["resolution"], h_resolution=job["resolution"], controller=controller)
    controller.wait()


def run(ssa, job):
    if job["action"] == "image_to_material":
        asset = image_to_material(ssa, job)
    else:
        if not ssa.open_project(job["project"]):
            raise RuntimeError("Sampler could not open the project")
        ssa.wait_for_computation()
        asset = pick_asset(ssa, job.get("asset"))
    export(ssa, asset, job)
    saved = None
    if job.get("save_as"):
        saved = ssa.save_project_as(job["save_as"]) or None
    return {"asset": asset.name, "layers": [layer.name for layer in asset.get_layers()],
            "saved": saved}


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = Path(job["result"])
    try:
        import substance_sampler as ssa

        reply = {"ok": True, **run(ssa, job)}
    except BaseException as exc:  # noqa: BLE001 -- reported to the server
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                 "trace": traceback.format_exc()[-2000:]}
    result.write_text(json.dumps(reply, default=str), encoding="utf-8")


main()
