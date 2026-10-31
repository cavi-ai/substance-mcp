"""sbsar_fill imports exactly the bytes it verified, and bounds what it reads."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_texturing import Fixture

from cavi_substance_mcp.painter import operations as ops

ORIGINAL = b"fixture"


class SwapTests(Fixture):
    def fill(self, **params):
        return self.edit("sbsar_fill", name="Rock", sbsar="material.sbsar", **params)

    def test_swapping_the_file_after_hashing_cannot_change_what_is_imported(self):
        source = self.root / "material.sbsar"
        self.sim.import_hook = lambda path: source.write_bytes(b"swapped-in-after-the-hash")
        digest = hashlib.sha256(ORIGINAL).hexdigest()
        result = self.fill(expected_sha256=digest)
        self.assertTrue(result["verified"])
        self.assertEqual(result["source"]["sha256"], digest)
        imported = self.sim.imports[-1]
        self.assertEqual(imported["bytes"], ORIGINAL)
        self.assertNotEqual(Path(imported["path"]), source)

    @unittest.skipUnless(os.name == "posix", "POSIX directory modes")
    def test_import_reads_from_a_private_directory_that_is_removed_afterwards(self):
        self.fill()
        imported = self.sim.imports[-1]
        self.assertEqual(imported["folder_mode"], 0o700)
        self.assertTrue(Path(imported["path"]).parent.name.startswith(".cavi-sbsar-"))
        self.assertEqual(list(self.root.glob(".cavi-sbsar-*")), [])

    def test_failures_leave_no_copy_behind(self):
        with self.assertRaisesRegex(RuntimeError, "does not match expected_sha256"):
            self.fill(expected_sha256="0" * 64)
        self.assertEqual(self.sim.imports, [])
        self.assertEqual(list(self.root.glob(".cavi-sbsar-*")), [])
        self.sim.state = "import-failure"
        with self.assertRaisesRegex(RuntimeError, "simulated import failure"):
            self.fill()
        self.sim.state = "empty-sbsar"
        with self.assertRaisesRegex(RuntimeError, "No material graph"):
            self.fill()
        self.assertEqual(list(self.root.glob(".cavi-sbsar-*")), [])
        self.assertEqual(self.sim.layers, [])

    def test_unremovable_copy_is_reported_not_hidden(self):
        real = shutil.rmtree

        def stuck(path, *args, **kwargs):
            if not Path(path).name.startswith(".cavi-sbsar-"):
                real(path, *args, **kwargs)

        with patch("shutil.rmtree", stuck):
            result = self.fill()
        self.assertTrue(any(".cavi-sbsar-" in item for item in result["cleanup_failed"]))
        self.assertEqual(len(list(self.root.glob(".cavi-sbsar-*"))), 1)

    def test_manifest_still_matches_the_original_name_and_the_copied_bytes(self):
        manifest = self.root / "material-manifest.json"
        manifest.write_text(json.dumps({"schema": 1, "outputs": [{
            "path": "material.sbsar", "size": len(ORIGINAL),
            "sha256": hashlib.sha256(ORIGINAL).hexdigest()}]}))
        result = self.fill(source_manifest="material-manifest.json")
        self.assertTrue(result["verified"])
        self.assertEqual(result["source_manifest"], "material-manifest.json")
        (self.root / "material.sbsar").write_bytes(b"changed after publishing")
        with self.assertRaisesRegex(RuntimeError, "does not match source_manifest"):
            self.edit("sbsar_fill", name="Rock2", sbsar="material.sbsar",
                      source_manifest="material-manifest.json")


class SizeCapTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.sbsar = self.root / "big.sbsar"
        self.sbsar.write_bytes(b"x" * 100)

    def test_provenance_hashing_enforces_the_size_cap(self):
        (self.root / "m.json").write_text('{"schema": 1, "outputs": []}')
        with patch.object(ops, "SBSAR_LIMIT", 10), self.assertRaisesRegex(
                ValueError, "sbsar exceeds"):
            ops._receipt_provenance("m.json", "big.sbsar", self.root)

    def test_provenance_below_the_cap_still_hashes(self):
        digest = hashlib.sha256(b"x" * 100).hexdigest()
        (self.root / "m.json").write_text(json.dumps({"schema": 1, "outputs": [{
            "path": "big.sbsar", "size": 100, "sha256": digest}]}))
        with patch.object(ops, "SBSAR_LIMIT", 100):
            found = ops._receipt_provenance("m.json", "big.sbsar", self.root)
        self.assertEqual(found[0]["file"]["sha256"], digest)

    def test_a_source_manifest_over_8_mib_is_refused(self):
        from cavi_substance_mcp import receipts

        self.assertEqual(ops.MAX_JSON_BYTES, receipts.MAX_JSON_BYTES)
        self.assertEqual(ops.MAX_JSON_BYTES, 8 * 1024 * 1024)
        (self.root / "m.json").write_text('{"schema": 1, "outputs": []}' + " " * 200)
        with patch.object(ops, "MAX_JSON_BYTES", 100), self.assertRaisesRegex(
                ValueError, r"source_manifest is unreadable: .*larger than"):
            ops._receipt_provenance("m.json", "big.sbsar", self.root)

    def test_a_source_manifest_at_the_cap_is_still_read(self):
        digest = hashlib.sha256(b"x" * 100).hexdigest()
        text = json.dumps({"schema": 1, "outputs": [{
            "path": "big.sbsar", "size": 100, "sha256": digest}]})
        (self.root / "m.json").write_text(text)
        with patch.object(ops, "MAX_JSON_BYTES", len(text)):
            found = ops._receipt_provenance("m.json", "big.sbsar", self.root)
        self.assertEqual(found[0]["file"]["sha256"], digest)

    def test_snapshot_stops_reading_at_the_cap_even_if_the_file_grows(self):
        folder = self.root / "copy"
        folder.mkdir()
        with patch.object(ops, "SBSAR_LIMIT", 10), self.assertRaisesRegex(
                ValueError, "sbsar exceeds"):
            ops.snapshot_sbsar(self.sbsar, folder)


if __name__ == "__main__":
    unittest.main()
