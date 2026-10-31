import asyncio
import contextlib
import hashlib
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

from designer_fake_cli import ENV as DESIGNER_ENV
from designer_fake_cli import install
from env import DESIGNER, SAMPLER, patched_environment
from owned_client import OwnedPainterClient
from painter_simulator import PainterSimulator
from sampler_fake_cli import ENV as SAMPLER_ENV
from sampler_fake_cli import install as install_sampler

from cavi_substance_mcp import receipts, receipts_cli
from cavi_substance_mcp.designer import operations as designer
from cavi_substance_mcp.painter import operations as painter_operations
from cavi_substance_mcp.sampler import launch as sampler


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
            outputs=[output], seconds=0.25,
        )
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(len(manifest["inputs"]), 1)
        self.assertEqual(len(manifest["outputs"]), 1)
        self.assertEqual(manifest["outputs"][0]["path"], "out.txt")
        path = receipts.write(self.root, manifest)
        self.assertEqual(json.loads(path.read_text()), manifest)
        self.assertEqual(path.name, "manifest.json")

    def test_sha256_file_hashes_in_blocks(self):
        small = self.root / "small.bin"
        small.write_bytes(b"abc")
        large = self.root / "large.bin"
        large.write_bytes(b"x" * (3 * 1024 * 1024 + 5))
        self.assertEqual(receipts.sha256_file(small), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(receipts.sha256_file(str(large)),
                         hashlib.sha256(b"x" * (3 * 1024 * 1024 + 5)).hexdigest())

    def test_user_json_is_capped_at_8_mib(self):
        self.assertEqual(receipts.MAX_JSON_BYTES, 8 * 1024 * 1024)
        with patch.object(receipts, "MAX_JSON_BYTES", 2 * 1024 * 1024):
            small = self.root / "manifest.json"
            small.write_text(json.dumps({"schema": 1, "pad": "x" * (2 * 1024 * 1024)}))
            with self.assertRaisesRegex(receipts.ReceiptError, r"larger than 2 MiB"):
                receipts.load(small)

    def test_a_receipt_at_the_cap_still_loads(self):
        text = json.dumps({"schema": 1, "pad": ""})
        size = 4096
        padded = json.dumps({"schema": 1, "pad": "x" * (size - len(text))})
        self.assertEqual(len(padded), size)
        path = self.root / "manifest.json"
        path.write_text(padded)
        with patch.object(receipts, "MAX_JSON_BYTES", size):
            self.assertEqual(receipts.load(path)["schema"], 1)
        with patch.object(receipts, "MAX_JSON_BYTES", size - 1), \
                self.assertRaisesRegex(receipts.ReceiptError, "larger than"):
            receipts.load(path)

    def test_the_cap_is_checked_before_the_file_is_parsed(self):
        path = self.root / "manifest.json"
        path.write_text("[" * 5000)
        with patch.object(receipts, "MAX_JSON_BYTES", 1000), \
                self.assertRaisesRegex(receipts.ReceiptError, "larger than"):
            receipts.load(path)

    def test_write_manifest_builds_and_writes_atomically(self):
        source = self.root / "source.txt"
        source.write_text("input")
        directory = self.root / "out"
        directory.mkdir()
        (directory / "result.txt").write_text("output")
        path = receipts.write_manifest(
            directory, "test", "write", self.root, inputs=[source],
            outputs=[directory / "result.txt"], parameters={"a": 1}, seconds=0.5,
            version="1.2",
        )
        self.assertEqual(path, directory / "manifest.json")
        written = json.loads(path.read_text())
        self.assertEqual(written["parameters"], {"a": 1})
        self.assertEqual(written["version"], "1.2")
        self.assertEqual([row["path"] for row in written["inputs"]], ["source.txt"])
        self.assertEqual([row["path"] for row in written["outputs"]], ["out/result.txt"])
        receipts.verify(written, self.root)
        self.assertEqual(sorted(item.name for item in directory.iterdir()),
                         ["manifest.json", "result.txt"])

    def test_write_manifest_writes_nothing_when_a_file_is_missing(self):
        directory = self.root / "out"
        directory.mkdir()
        with self.assertRaises(receipts.ReceiptError):
            receipts.write_manifest(directory, "test", "write", self.root,
                                    outputs=[directory / "missing.txt"])
        self.assertEqual(list(directory.iterdir()), [])

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
                code = receipts_cli.main(["verify", "manifest.json", "--json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stream.getvalue())["ok"])
            output.write_text("xxxxxx")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = receipts_cli.main(["verify", "manifest.json", "--json"])
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

    def test_designer_to_painter_receipt_chain(self):
        workspace = self.root / "ws"
        (workspace / "maps").mkdir(parents=True)
        (workspace / "source.sbs").write_text("fake-sbs")
        app = install(self.root)
        with patched_environment(DESIGNER, {
                DESIGNER_ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(workspace)}):
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
                "source_manifest": cooked["manifest"], "source_file": cooked["sbsar"],
                "dry_run": False,
            })
            painter_manifest = json.loads(Path(exported["manifest"]).read_text())
            self.assertEqual(painter_manifest["app"], "painter")
            self.assertEqual(painter_manifest["operation"], "export")
            self.assertTrue(painter_manifest["outputs"])
            self.assertEqual(painter_manifest["provenance"][0]["file"]["sha256"],
                             sbsar["sha256"])
            self.assertTrue((workspace / "painter-out/manifest.json").is_file())
            chain = receipts.chain(Path(exported["manifest"]), workspace)
            self.assertEqual([step["app"] for step in chain], ["painter", "designer"])
            with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(workspace)}):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    code = receipts_cli.main([
                        "chain", exported["manifest"], "--json",
                    ])
                self.assertEqual(code, 0)
                self.assertEqual([step["app"] for step in
                                  json.loads(stream.getvalue())["chain"]],
                                 ["painter", "designer"])
            (workspace / cooked["sbsar"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(receipts.ReceiptError, "Size mismatch|SHA-256"):
                receipts.chain(Path(exported["manifest"]), workspace)

    def test_sampler_to_painter_receipt_chain(self):
        workspace = self.root / "ws"
        workspace.mkdir()
        app = install_sampler(self.root)
        with patched_environment(SAMPLER, {
                SAMPLER_ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(workspace)}):
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
            presets = client.call("export_presets")["presets"]
            painter = client.call("export_textures", {
                "directory": "painter-out", "preset": presets[0]["url"],
                "source_manifest": exported["manifest"], "source_file": source["path"],
                "dry_run": False,
            })
        self.assertEqual([step["app"] for step in
                          receipts.chain(Path(painter["manifest"]), workspace)],
                         ["painter", "sampler"])

    def test_sbsar_fill_rejects_unverified_source(self):
        workspace = self.root / "ws"
        workspace.mkdir()
        (workspace / "source.sbs").write_text("fake-sbs")
        app = install(self.root)
        with patched_environment(DESIGNER, {
                DESIGNER_ENV: str(app), "CAVI_SUBSTANCE_WORKSPACE": str(workspace)}):
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
