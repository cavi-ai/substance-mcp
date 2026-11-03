"""Compatibility fixtures for the action-dependent native response branches."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pydantic import ValidationError

from cavi_substance_mcp.core import contracts

ASSET = {
    "name": "Stone",
    "type": "material",
    "material_model": "pbr",
    "resolution": [1024, 1024],
    "metadata": {"author": None},
    "layer_count": 0,
}
PACKED = {
    "name": "roughness",
    "path": "out/roughness.png",
    "width": 8,
    "height": 8,
    "channels": "RGB",
    "bit_depth": 8,
}


def cases():
    return {
        "PainterProject": [
            {"requested": "open_project", "path": "prop.spp", "loading_may_be_pending": True},
            {"closed": True, "already_closed": True},
            {"closed": False},
            {"saved": True, "file": "prop.spp", "needs_saving": False},
            {"path": "copy/prop.spp", "bytes": 128, "manifest": "copy/manifest.json"},
            {
                "connected": True,
                "open": True,
                "busy": False,
                "workspace": "/workspace",
                "ready": True,
                "waiting_on": [],
                "waited_seconds": 0.1,
                "file": None,
            },
        ],
        "PainterInspection": [
            {"texture_sets": []},
            {"presets": []},
            {"resources": [], "truncated": False},
            {"layers": [], "node_count": 0, "truncated": False},
        ],
        "PainterLayerResult": [
            {
                "uid": 1,
                "name": "Fill",
                "texture_set": "Body",
                "base_color": [1.0, 1.0, 1.0],
                "roughness": 0.5,
                "metallic": 0.0,
            },
            {"deleted": 1, "name": "Fill"},
            {"uid": 1, "mask": "black"},
            {
                "uid": 1,
                "mask_added": False,
                "resource_url": "resource://fill",
                "parameters": {},
                "effects": [],
            },
            {"uid": 1, "name": "Fill", "visible": True, "channels": [None], "changed": ["opacity"]},
        ],
        "PainterBakeResult": [
            {
                "started": True,
                "texture_sets": ["Body"],
                "maps": {"Body": ["normal"]},
                "disabled_texture_sets": [],
                "next": "bake action=status until busy is false",
            },
            {
                "cancel_requested": True,
                "running": True,
                "next": "bake action=status until busy is false",
            },
            {
                "busy": False,
                "running": False,
                "reconciled": False,
                "status": None,
                "progress": None,
                "seconds_since_start": None,
                "texture_sets": [],
                "maps": {},
                "mesh_maps": {},
            },
        ],
        "DesignerMaterial": [
            {"sbsar": "stone.sbsar", "graphs": [], "presets": None},
            {
                "sbsar": "stone.sbsar",
                "source": "packages/stone",
                "graphs": [],
                "manifest": "manifest.json",
            },
            {
                "directory": "out",
                "graph": "pkg://stone",
                "maps": {"roughness": "out/rough.png"},
                "outputs": [],
                "report": [],
                "manifest": "out/manifest.json",
                "seconds": 0.1,
            },
            {
                "directory": "out",
                "packed": [PACKED],
                "sources": ["roughness"],
                "manifest": "out/manifest.json",
            },
        ],
        "SamplerInspection": [
            {"project": "stone.ssa", "project_path": None, "assets": [ASSET]},
            {"project": "stone.ssa", "asset": ASSET, "layers": []},
            {
                "filters": [],
                "materials": [],
                "texture_generators": [],
                "export_presets": [],
                "material_models": [],
                "predefined_presets": [],
                "import_options": [],
                "blend_modes": [],
                "material_types": [],
                "texture_formats": [],
                "b2m_available": False,
                "export_targets": {},
            },
        ],
        "SamplerProjectResult": [
            {
                "project": "new/stone.ssa",
                "directory": "new",
                "name": "stone",
                "assets": [ASSET],
                "manifest": "new/manifest.json",
            },
            {
                "source": "stone.ssa",
                "project": "copy/stone.ssa",
                "directory": "copy",
                "manifest": "copy/manifest.json",
            },
        ],
        "SamplerAssetResult": [
            {"project": "stone.ssa", "asset": ASSET, "saved": True},
            {"project": "stone.ssa", "deleted": "Stone", "saved": True},
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "layer": {},
                "images": ["color.png"],
                "saved": True,
            },
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "layer": {},
                "images": {"baseColor": "color.png"},
                "saved": True,
            },
            {
                "path": "stone.sbsar",
                "resources": [],
                "verified": True,
                "source": {"sha256": "a" * 64},
            },
        ],
        "SamplerLayerResult": [
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "layer": "Filter",
                "parameters": {"scale": 1},
                "changed": False,
                "saved": False,
            },
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "layer": {},
                "preset": "Default",
                "changed": True,
                "saved": True,
            },
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "layer": {},
                "applies_to": None,
                "blend_applies_to": [1],
                "saved": True,
            },
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "removed": "Filter",
                "position": 0,
                "saved": True,
            },
        ],
        "SamplerExportResult": [
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "directory": "out",
                "format": "png",
                "target": None,
                "material_type": "standard",
                "preset": None,
                "channels": None,
                "resolution": None,
                "wait_seconds": 0,
                "dry_run": True,
                "files": [],
            },
            {
                "project": "stone.ssa",
                "asset": "Stone",
                "directory": "out",
                "format": "png",
                "target": "unity",
                "material_type": "standard",
                "preset": "Unity",
                "channels": ["roughness"],
                "resolution": 1024,
                "wait_seconds": 60,
                "dry_run": False,
                "files": ["out/rough.png"],
                "manifest": "out/manifest.json",
            },
        ],
        "SamplerBuildResult": [
            {
                "directory": "build",
                "project": "Stone",
                "assets": ["Stone"],
                "inputs": ["color.png"],
                "steps": ["export"],
                "job": "job.json",
                "source": None,
                "dry_run": True,
            },
            {
                "status": "success",
                "job": "build/job.json",
                "job_sha256": "a" * 64,
                "project": "build/stone.ssa",
                "source": None,
                "assets": [ASSET],
                "steps": [{"index": 0, "action": "export", "result": {}}],
                "schema": 1,
                "app": "sampler",
                "operation": "build",
                "inputs": [],
                "outputs": [],
                "version": None,
                "started": "2026-11-02T00:00:00Z",
                "seconds": 1.0,
                "parameters": {},
                "warnings": [],
                "provenance": [],
            },
        ],
        "SamplerPackResult": [
            {
                "directory": "out",
                "maps": [PACKED],
                "sources": ["roughness"],
                "manifest": "out/manifest.json",
            }
        ],
    }


class NativeResponseShapes(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_pack_results_through_both_mcp_servers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("L", (8, 8), 96).save(root / "roughness.png")
            spec = {
                "maps": [
                    {
                        "name": "roughness",
                        "format": "png",
                        "bit_depth": 8,
                        "channels": {"R": "roughness", "G": "roughness", "B": "roughness"},
                    }
                ]
            }
            for module, tool, field in (
                ("designer", "material", "packed"),
                ("sampler", "sampler_pack", "maps"),
            ):
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "cavi_substance_mcp." + module + ".server"],
                    env={**os.environ, "CAVI_SUBSTANCE_WORKSPACE": str(root)},
                )
                async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
                    await client.initialize()
                    args = {
                        "directory": module,
                        "sources": {"roughness": "roughness.png"},
                        "pack": spec,
                    }
                    if module == "designer":
                        args["action"] = "pack"
                    result = await client.call_tool(tool, args)
                    self.assertFalse(result.is_error, result.content)
                    body = result.structured_content
                    self.assertEqual(body[field][0]["width"], 8)
                    self.assertEqual(body[field][0]["channels"], "RGB")
                    self.assertEqual(body["sources"], ["roughness"])
                    receipt = json.loads((root / body["manifest"]).read_text())
                    self.assertEqual(receipt["operation"], "pack")
                    self.assertTrue((root / body[field][0]["path"]).is_file())

    def test_action_shapes_round_trip_without_wrappers_or_inserted_nulls(self):
        for name, records in cases().items():
            model = getattr(contracts, name)
            schema = Draft202012Validator(model.model_json_schema())
            compact = Draft202012Validator(
                contracts.compact_schema(model.model_json_schema(), output=True))
            for original in records:
                with self.subTest(model=name, keys=sorted(original)):
                    record = {**copy.deepcopy(original), "extension": {"preserved": [1, None]}}
                    schema.validate(record)
                    compact.validate(record)
                    self.assertEqual(
                        model.model_validate(record).model_dump(mode="json", by_alias=True), record
                    )
            with self.subTest(model=name), self.assertRaises(ValidationError):
                model.model_validate({})
            self.assertFalse(schema.is_valid({}))
            self.assertFalse(compact.is_valid({}))

    def test_null_anchors_and_invalid_scalar_types_are_rejected(self):
        for model, record in [
            (contracts.PainterProject, {"closed": None}),
            (contracts.PainterLayerResult, {"uid": "not-an-integer"}),
            (contracts.SamplerAssetResult, {"path": None, "resources": []}),
        ]:
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate(record)
            self.assertFalse(Draft202012Validator(model.model_json_schema()).is_valid(record))
