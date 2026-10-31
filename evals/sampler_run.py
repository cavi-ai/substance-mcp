"""Run deterministic Sampler evaluations over the real stdio MCP surface."""

import asyncio
import hashlib
import json
from pathlib import Path

import common

APP = "substance-sampler"
SERVER = ["-m", "cavi_substance_mcp.sampler.server"]


def fixtures(output: Path) -> Path:
    workspace = common.make_workspace(output, ["maps", "assets", "jobs", "existing"])
    outside = output / "outside"
    (outside / "outside.ssa").write_text("{}")
    from PIL import Image
    Image.new("RGB", (32, 32), (204, 102, 51)).save(workspace / "maps/basecolor.png")
    Image.new("RGB", (32, 32), (128, 128, 255)).save(workspace / "maps/normal.png")
    Image.new("L", (32, 32), 64).save(workspace / "maps/roughness.png")
    Image.new("L", (32, 32), 255).save(workspace / "maps/metallic.png")
    Image.new("L", (32, 32), 200).save(workspace / "maps/ao.png")
    custom = b"fake-sbsar"
    (workspace / "assets/custom.sbsar").write_bytes(custom)
    (workspace / "assets/manifest.json").write_text(json.dumps({
        "schema": 1, "app": "designer", "operation": "cook",
        "outputs": [{"path": "assets/custom.sbsar", "size": len(custom),
                     "sha256": hashlib.sha256(custom).hexdigest()}],
    }))
    (workspace / "existing/keep.txt").write_text("keep")

    def build(directory, target, asset="Rock"):
        return {
            "schema": 1,
            "directory": directory,
            "project": {"name": "RockProject",
                        "asset": {"name": asset, "model": "openpbr",
                                  "preset": "base_material"}},
            "steps": [
                {"action": "import_images", "asset": asset,
                 "images": ["maps/basecolor.png"], "position": 0,
                 "import_option": "use_as_bitmap"},
                {"action": "insert_filter", "asset": asset, "filter": "Tiling",
                 "position": 1},
                {"action": "set_parameters", "asset": asset, "layer": "Tiling",
                 "parameters": {"Tiling": 3}},
                {"action": "apply_preset", "asset": asset, "layer": "Tiling",
                 "preset": "Wet"},
                {"action": "export", "asset": asset, "target": target,
                 "resolution": 64},
            ],
        }

    jobs = {
        "unreal": build("jobs/unreal", "unreal"),
        "unity": build("jobs/unity", "unity"),
        "blender": build("jobs/blender", "blender"),
        "generic": build("jobs/generic", "generic"),
        "existing": build("existing", "generic"),
        "fail": build("jobs/fail", "generic", asset="fail"),
        "check": build("jobs/check", "unreal"),
        "b2m": {
            "schema": 1,
            "directory": "jobs/b2m",
            "project": {"name": "B2MProject"},
            "steps": [
                {"action": "create_asset", "name": "Photo", "empty": True},
                {"action": "import_images", "asset": "Photo",
                 "images": ["maps/basecolor.png"], "position": 0,
                 "import_option": "image_to_material_B2M"},
                {"action": "export", "asset": "Photo", "target": "generic",
                 "outputs": ["sbsar"]},
            ],
        },
    }
    for name, data in jobs.items():
        (workspace / "jobs" / f"{name}.json").write_text(json.dumps(data))
    invalid = build("jobs/invalid", "generic")
    invalid["directory"] = "../outside/escape"
    (workspace / "jobs/invalid.json").write_text(json.dumps(invalid))
    bad_schema = build("jobs/bad-schema", "generic")
    bad_schema["steps"] = [step for step in bad_schema["steps"]
                           if step["action"] != "export"]
    (workspace / "jobs/bad-schema.json").write_text(json.dumps(bad_schema))
    shadow = build("jobs/shadow", "generic")
    shadow["steps"][-1]["directory"] = "manifest.json"
    (workspace / "jobs/shadow.json").write_text(json.dumps(shadow))
    bad_name = build("jobs/bad-name", "generic", asset="A:B")
    (workspace / "jobs/bad-name.json").write_text(json.dumps(bad_name))
    missing_filter = build("jobs/missing-filter", "generic")
    missing_filter["steps"].insert(-1, {
        "action": "insert_filter", "asset": "Rock", "filter": "Missing Filter",
        "position": 2,
    })
    (workspace / "jobs/missing-filter.json").write_text(json.dumps(missing_filter))
    source = {
        "schema": 1,
        "directory": "jobs/source",
        "project": {"source": "projects/Rock.ssa", "name": "RockCopy",
                    "assets": ["Rock"]},
        "steps": [
            {"action": "set_metadata", "asset": "Rock", "author": "Eval",
             "category": "rock", "tags": ["game"]},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ],
    }
    (workspace / "jobs/source.json").write_text(json.dumps(source))
    preflight = {
        "schema": 1,
        "directory": "jobs/preflight",
        "project": {"source": "projects/Rock.ssa", "name": "RockCopy",
                    "assets": ["Rock"]},
        "steps": [
            {"action": "set_metadata", "asset": "Rock", "author": "Eval"},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ],
    }
    (workspace / "jobs/preflight.json").write_text(json.dumps(preflight))
    preflight_bad = {
        "schema": 1,
        "directory": "jobs/preflight-bad",
        "project": {"source": "projects/Rock.ssa", "name": "RockCopy",
                    "assets": ["Rock"]},
        "steps": [
            {"action": "set_parameters", "asset": "Rock", "layer": "Missing",
             "parameters": {"x": 1}},
            {"action": "export", "asset": "Rock", "target": "unity",
             "directory": "unity"},
        ],
    }
    (workspace / "jobs/preflight-bad.json").write_text(json.dumps(preflight_bad))
    resources = {
        "schema": 1,
        "directory": "jobs/resources",
        "project": {"name": "ResourceProject", "asset": {"name": "Rock"}},
        "steps": [
            {"action": "import_resource", "path": "assets/custom.sbsar",
             "resources": ["custom"]},
            {"action": "import_images", "asset": "Rock",
             "images": ["maps/basecolor.png"], "position": 0},
            {"action": "insert_filter", "asset": "Rock", "filter": "custom",
             "position": 1},
            {"action": "export", "asset": "Rock", "target": "generic",
             "outputs": ["sbsar"]},
        ],
    }
    (workspace / "jobs/resources.json").write_text(json.dumps(resources))
    build_check = {
        "schema": 1,
        "directory": "jobs/build-check",
        "project": {"name": "BuildProject",
                    "asset": {"name": "Rock", "model": "openpbr",
                              "preset": "base_material"}},
        "steps": [
            {"action": "import_images", "asset": "Rock",
             "images": ["maps/basecolor.png"], "position": 0,
             "import_option": "use_as_bitmap"},
            {"action": "insert_filter", "asset": "Rock", "filter": "Tiling",
             "position": 1},
            {"action": "set_parameters", "asset": "Rock", "layer": "Tiling",
             "parameters": {"Tiling": 3}},
            {"action": "export", "asset": "Rock", "target": "unreal",
             "resolution": 64},
        ],
    }
    (workspace / "jobs/build-check.json").write_text(json.dumps(build_check))
    build_bad = {
        "schema": 1,
        "directory": "jobs/build-check-bad",
        "project": {"name": "BuildProject",
                    "asset": {"name": "Rock", "model": "openpbr",
                              "preset": "base_material"}},
        "steps": [
            {"action": "set_parameters", "asset": "Rock", "layer": "Missing",
             "parameters": {"x": 1}},
            {"action": "export", "asset": "Rock", "target": "unreal",
             "resolution": 64},
        ],
    }
    (workspace / "jobs/build-check-bad.json").write_text(json.dumps(build_bad))
    recipes = {
        "ai": {
            "schema": 1, "directory": "jobs/ai",
            "project": {"name": "AI Material"},
            "steps": [
                {"action": "create_asset", "name": "AIMaterial", "empty": True},
                {"action": "import_images", "asset": "AIMaterial",
                 "images": ["maps/basecolor.png"],
                 "import_option": "image_to_material_AI_powered", "position": 0},
                {"action": "insert_filter", "asset": "AIMaterial",
                 "filter": "Delighter (AI Powered)", "position": 1},
                {"action": "insert_filter", "asset": "AIMaterial",
                 "filter": "Make It Tile", "position": 2},
                {"action": "export", "asset": "AIMaterial", "target": "unreal",
                 "resolution": 64},
            ],
        },
        "multiangle": {
            "schema": 1, "directory": "jobs/multiangle",
            "project": {"name": "Multiangle Material"},
            "steps": [
                {"action": "create_asset", "name": "Angles", "empty": True},
                {"action": "import_images", "asset": "Angles",
                 "images": ["maps/basecolor.png", "maps/normal.png"],
                 "import_option": "multiangle_to_material", "position": 0},
                {"action": "insert_filter", "asset": "Angles",
                 "filter": "Make It Tile", "position": 1},
                {"action": "export", "asset": "Angles", "target": "unreal",
                 "resolution": 64},
            ],
        },
    }
    for name, data in recipes.items():
        (workspace / "jobs" / f"{name}.json").write_text(json.dumps(data))
    return workspace


def main():
    mode, evaluator, output = common.start("sampler-")
    workspace = fixtures(output)
    env = common.environment(
        output, workspace, drop=("CAVI_SAMPLER_APP", "CAVI_SAMPLER_BIN"))
    if mode == "simulated":
        from sampler_fake_cli import ENV, install

        env[ENV] = str(install(output / "app"))
    else:
        (output / "no-sampler").mkdir()
        env["CAVI_SAMPLER_BIN"] = str(output / "no-sampler")
    results = asyncio.run(common.exercise("sampler_cases.json", mode, SERVER, env, workspace))
    common.finish(
        mode=mode, evaluator=evaluator, output=output, results=results, env=env, app=APP,
        server=SERVER, manifest_prefix="sampler.",
        native_status=(
            "fake Sampler or no executable; run tests/test_sampler_native.py for native"),
    )


if __name__ == "__main__":
    main()
