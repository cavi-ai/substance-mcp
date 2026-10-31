import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from designer_fake_cli import install
from owned_client import OwnedPainterClient
from painter_simulator import PainterSimulator
from sampler_fake_cli import install as install_sampler

from cavi_substance_mcp import cli, painter_operations, receipts
from cavi_substance_mcp.designer import operations as designer
from cavi_substance_mcp.sampler import operations as sampler


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_receipt_records_and_writes(self):
        source = self.root / "source.txt"
        source.write_text("input")
        output = self.root / "out.txt"
        output.write_text("output")
        manifest = receipts.build(
            "test", "write", self.root, inputs=[source, source],
            outputs=[output, self.root / "missing.txt"], seconds=0.25,
        )
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(len(manifest["inputs"]), 1)
        self.assertEqual(len(manifest["outputs"]), 1)
        self.assertEqual(manifest["outputs"][0]["path"], "out.txt")
        path = receipts.write(self.root, manifest)
        self.assertEqual(json.loads(path.read_text()), manifest)
        self.assertEqual(path.name, "manifest.json")

    def test_verify_manifest_and_cli(self):
        output = self.root / "out.txt"
        output.write_text("output")
        manifest = receipts.build("test", "write", self.root, outputs=[output])
        receipts.write(self.root, manifest)
        receipts.verify(manifest, self.root)
        self.assertEqual(receipts.verify_file(manifest, self.root, output)["path"], "out.txt")
        with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(self.root)}):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = cli.main(["receipts", "verify", "manifest.json", "--json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stream.getvalue())["ok"])
            output.write_text("xxxxxx")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = cli.main(["receipts", "verify", "manifest.json", "--json"])
            self.assertEqual(code, 1)
            result = json.loads(stream.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("SHA-256", result["error"])

    def test_shared_and_painter_receipt_schema_match(self):
        output = self.root / "out.txt"
        output.write_text("output")
        shared = receipts.build("test", "write", self.root, outputs=[output])
        painter = painter_operations._receipt_build(
            "test", "write", self.root, outputs=[output]
        )
        self.assertEqual(set(shared), set(painter))
        self.assertEqual(shared["schema"], painter["schema"])

    def test_external_input_is_recorded_with_absolute_path(self):
        with tempfile.TemporaryDirectory() as other:
            source = Path(other) / "builtin.sbs"
            source.write_text("builtin")
            record = receipts.record(source, self.root)
        self.assertEqual(record["path"], str(source.resolve()))
        self.assertEqual(record["size"], 7)

    def test_designer_to_painter_receipt_chain(self):
        workspace = self.root / "ws"
        (workspace / "maps").mkdir(parents=True)
        (workspace / "source.sbs").write_text("fake-sbs")
        app = install(self.root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("CAVI_DESIGNER")}
        env.update({"CAVI_DESIGNER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
        with patch.dict(os.environ, env, clear=True):
            cooked = asyncio.run(designer.cook(
                "source.sbs", None, "designer-out", True, True, 30
            ))
        manifest = json.loads((workspace / cooked["manifest"]).read_text())
        self.assertEqual(manifest["app"], "designer")
        self.assertEqual(manifest["operation"], "cook")
        sbsar = next(item for item in manifest["outputs"]
                     if item["path"].endswith(".sbsar"))
        self.assertEqual(sbsar["path"], cooked["sbsar"])
        with PainterSimulator(workspace) as simulator:
            simulator.setup("open")
            client = OwnedPainterClient(port=simulator.port, workspace=workspace)
            filled = client.call("layer_edit", {
                "action": "sbsar_fill", "texture_set": "Body", "name": "From Designer",
                "sbsar": cooked["sbsar"], "source_manifest": cooked["manifest"],
            })
            self.assertEqual(filled["source"]["path"], cooked["sbsar"])
            self.assertEqual(filled["source"]["sha256"], sbsar["sha256"])
            self.assertTrue(filled["verified"])
            self.assertEqual(filled["source_manifest"], cooked["manifest"])
            presets = client.call("export_presets")["presets"]
            exported = client.call("export_textures", {
                "directory": "painter-out", "preset": presets[0]["url"],
                "dry_run": False,
            })
            painter_manifest = json.loads(Path(exported["manifest"]).read_text())
            self.assertEqual(painter_manifest["app"], "painter")
            self.assertEqual(painter_manifest["operation"], "export")
            self.assertTrue(painter_manifest["outputs"])
            self.assertTrue((workspace / "painter-out/manifest.json").is_file())

    def test_sampler_to_painter_receipt_chain(self):
        workspace = self.root / "ws"
        workspace.mkdir()
        app = install_sampler(self.root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CAVI_SAMPLER", "SAMPLER_"))}
        env.update({"CAVI_SAMPLER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
        with patch.dict(os.environ, env, clear=True):
            created = asyncio.run(sampler.invoke(
                "create_project", {"directory": "projects", "name": "Rock"}, 30
            ))
            exported = asyncio.run(sampler.invoke(
                "export", {
                    "project": created["project"], "directory": "sampler-out",
                    "format": "sbsar", "dry_run": False,
                }, 30
            ))
        manifest = json.loads((workspace / exported["manifest"]).read_text())
        source = next(item for item in manifest["outputs"]
                      if item["path"].endswith(".sbsar"))
        with PainterSimulator(workspace) as simulator:
            simulator.setup("open")
            client = OwnedPainterClient(port=simulator.port, workspace=workspace)
            filled = client.call("layer_edit", {
                "action": "sbsar_fill", "texture_set": "Body", "name": "From Sampler",
                "sbsar": source["path"], "source_manifest": exported["manifest"],
            })
        self.assertTrue(filled["verified"])
        self.assertEqual(filled["source"]["sha256"], source["sha256"])

    def test_sbsar_fill_rejects_unverified_source(self):
        workspace = self.root / "ws"
        workspace.mkdir()
        (workspace / "source.sbs").write_text("fake-sbs")
        app = install(self.root)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("CAVI_DESIGNER")}
        env.update({"CAVI_DESIGNER_APP": str(app),
                    "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
        with patch.dict(os.environ, env, clear=True):
            cooked = asyncio.run(designer.cook(
                "source.sbs", None, "designer-out", True, True, 30
            ))
        with PainterSimulator(workspace) as simulator:
            simulator.setup("open")
            client = OwnedPainterClient(port=simulator.port, workspace=workspace)
            with self.assertRaisesRegex(RuntimeError, "expected_sha256"):
                client.call("layer_edit", {
                    "action": "sbsar_fill", "texture_set": "Body", "name": "Bad hash",
                    "sbsar": cooked["sbsar"], "expected_sha256": "0" * 64,
                })
            (workspace / cooked["sbsar"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "source_manifest"):
                client.call("layer_edit", {
                    "action": "sbsar_fill", "texture_set": "Body", "name": "Bad manifest",
                    "sbsar": cooked["sbsar"], "source_manifest": cooked["manifest"],
                })


if __name__ == "__main__":
    unittest.main()
