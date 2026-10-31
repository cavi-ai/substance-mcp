import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from designer_fake_cli import ENV as DESIGNER_ENV
from designer_fake_cli import install as install_designer
from env import DESIGNER, patched_environment

from cavi_substance_mcp import receipts, receipts_cli
from cavi_substance_mcp.designer import operations as designer
from cavi_substance_mcp.painter import operations as painter_operations


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Workspace(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name).resolve() / "ws"
        self.root.mkdir()

    def tearDown(self):
        self.folder.cleanup()

    def write(self, name, text="data"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def row(self, path, text="data"):
        return {"path": path, "size": len(text), "sha256": sha(text)}

    def manifest(self, **changes):
        base = {"schema": 1, "app": "t", "operation": "op", "version": None,
                "started": "2026-01-01T00:00:00+00:00", "seconds": 0, "parameters": {},
                "inputs": [], "outputs": [], "warnings": [], "provenance": []}
        base.update(changes)
        return base

    def cli(self, *argv, root=None):
        with patch.dict(os.environ, {"CAVI_SUBSTANCE_WORKSPACE": str(root or self.root)}):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = receipts_cli.main([*argv, "--json"])
        return code, json.loads(stream.getvalue())


class RecordTests(Workspace):
    def test_missing_files_are_an_error_not_silently_dropped(self):
        present = self.write("a.txt")
        for role in ("inputs", "outputs"):
            with self.subTest(role=role), self.assertRaisesRegex(
                    receipts.ReceiptError, "missing"):
                receipts.build("t", "op", self.root, **{role: [present, self.root / "no.txt"]})

    def test_files_outside_the_root_are_never_recorded(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "x.txt"
            outside.write_text("x")
            with self.assertRaisesRegex(receipts.ReceiptError, "outside"):
                receipts.record(outside, self.root)
            with self.assertRaisesRegex(receipts.ReceiptError, "outside"):
                receipts.build("t", "op", self.root, inputs=[outside])

    def test_plain_receipts_stay_schema_one(self):
        manifest = receipts.build("t", "op", self.root, outputs=[self.write("a.txt")])
        self.assertEqual(manifest["schema"], 1)


class AppResourceTests(Workspace):
    def template(self, app):
        template = Path(app) / "templates/pbr.sbs"
        template.parent.mkdir(parents=True)
        template.write_text("template")
        return template

    def test_app_resource_row_has_no_path(self):
        with tempfile.TemporaryDirectory() as app:
            row = receipts.app_resource("templates/pbr", self.template(app), "16.0.7")
        self.assertEqual(row, {"role": "app-resource", "name": "templates/pbr",
                               "sha256": sha("template"), "tool_version": "16.0.7"})

    def test_app_resource_names_are_validated(self):
        with tempfile.TemporaryDirectory() as app:
            template = self.template(app)
            for name in ("", "/Applications/Designer/pbr", "../pbr", "C:\\x\\pbr", "a\\b"):
                with self.subTest(name=name), self.assertRaises(receipts.ReceiptError):
                    receipts.app_resource(name, template, "1")

    def test_receipt_with_app_resource_verifies_after_the_app_is_gone(self):
        output = self.write("out/cooked.sbsar", "cooked")
        with tempfile.TemporaryDirectory() as app:
            row = receipts.app_resource("templates/pbr", self.template(app), "16.0.7")
        manifest = receipts.build("designer", "cook", self.root, outputs=[output],
                                  external=[row], version="16.0.7")
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["inputs"], [row])
        text = json.dumps(manifest)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("/templates/pbr.sbs", text)
        receipts.write(output.parent, manifest)
        moved = self.root.parent / "elsewhere"
        shutil.copytree(self.root, moved)
        loaded = receipts.load(moved / "out/manifest.json")
        receipts.verify(loaded, moved)
        self.assertEqual([step["app"] for step in
                          receipts.chain(moved / "out/manifest.json", moved)], ["designer"])
        code, result = self.cli("chain", "out/manifest.json", root=moved)
        self.assertEqual((code, result["ok"]), (0, True))
        code, result = self.cli("verify", "out/manifest.json", root=moved)
        self.assertEqual((code, result["inputs"], result["outputs"]), (0, 1, 1))

    def test_tampering_with_an_output_is_still_caught(self):
        output = self.write("out.txt")
        row = {"role": "app-resource", "name": "templates/pbr", "sha256": sha("t"),
               "tool_version": None}
        manifest = receipts.build("t", "op", self.root, outputs=[output], external=[row])
        output.write_text("changed!")
        with self.assertRaisesRegex(receipts.ReceiptError, "Size mismatch|SHA-256"):
            receipts.verify(manifest, self.root)

    def test_malformed_app_resource_rows_are_rejected(self):
        for row in ({"role": "app-resource", "name": "n"},
                    {"role": "app-resource", "name": "n", "sha256": "xyz"},
                    {"role": "app-resource", "name": "/abs/n", "sha256": sha("t")},
                    {"role": "other", "name": "n", "sha256": sha("t")}):
            with self.subTest(row=row), self.assertRaises(receipts.ReceiptError):
                receipts.verify(self.manifest(schema=2, inputs=[row]), self.root)


class SchemaTests(Workspace):
    def test_schema_one_receipts_still_verify_and_chain(self):
        self.write("out.txt")
        legacy = self.manifest(inputs=[], outputs=[self.row("out.txt")])
        receipts.verify(legacy, self.root)
        path = receipts.write(self.root, legacy)
        self.assertEqual(receipts.load(path)["schema"], 1)
        self.assertEqual(len(receipts.chain(path, self.root)), 1)

    def test_schema_one_cannot_carry_app_resources(self):
        row = {"role": "app-resource", "name": "n", "sha256": sha("t"), "tool_version": None}
        with self.assertRaisesRegex(receipts.ReceiptError, "schema"):
            receipts.verify(self.manifest(schema=1, inputs=[row]), self.root)

    def test_unknown_schemas_are_refused(self):
        for schema in (0, 3, "2", None):
            with self.subTest(schema=schema), self.assertRaisesRegex(
                    receipts.ReceiptError, "Unsupported receipt schema"):
                receipts.verify(self.manifest(schema=schema), self.root)


class ConfinementTests(Workspace):
    def test_entries_cannot_point_outside_the_root(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "secret.txt"
            outside.write_text("data")
            (self.root.parent / "sibling.txt").write_text("data")
            for value in ("../sibling.txt", str(outside), "sub/../../sibling.txt",
                          "/etc/hostname", "..\\sibling.txt", "C:\\Windows\\win.ini"):
                with self.subTest(path=value):
                    manifest = self.manifest(outputs=[self.row(value)])
                    with self.assertRaisesRegex(
                            receipts.ReceiptError, "outside|absolute|escape"):
                        receipts.verify(manifest, self.root)
            with self.assertRaises(receipts.ReceiptError):
                receipts.verify_file(self.manifest(outputs=[self.row(str(outside))]),
                                     self.root, outside)

    def test_symlinks_cannot_lead_out_of_the_root(self):
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / "secret.txt").write_text("data")
            (self.root / "link").symlink_to(other, target_is_directory=True)
            with self.assertRaisesRegex(receipts.ReceiptError, "outside"):
                receipts.verify(self.manifest(outputs=[self.row("link/secret.txt")]),
                                self.root)

    def test_provenance_cannot_leave_the_root(self):
        with tempfile.TemporaryDirectory() as other:
            foreign = Path(other) / "manifest.json"
            foreign.write_text("{}")
            link = {"manifest": self.row(str(foreign), "{}"),
                    "file": self.row("x.txt")}
            path = receipts.write(self.root, self.manifest(provenance=[link]))
            with self.assertRaises(receipts.ReceiptError):
                receipts.chain(path, self.root)


class ChainTests(Workspace):
    def link_to(self, name, previous):
        """Write a manifest `name` whose output chains to manifest `previous`."""
        output = self.write(f"{name}/out.txt", name)
        provenance = []
        if previous is not None:
            manifest_path = self.root / previous / "manifest.json"
            data = (self.root / previous / "out.txt").read_text()
            provenance = [{
                "manifest": receipts.record(manifest_path, self.root),
                "file": self.row(f"{previous}/out.txt", data),
            }]
        manifest = self.manifest(
            outputs=[self.row(f"{name}/out.txt", name)], provenance=provenance)
        del output
        return receipts.write(self.root / name, manifest)

    def test_chain_follows_links_in_order(self):
        self.link_to("a", None)
        self.link_to("b", "a")
        path = self.link_to("c", "b")
        self.assertEqual([item["path"] for item in receipts.chain(path, self.root)],
                         ["c/manifest.json", "b/manifest.json", "a/manifest.json"])

    def test_shared_ancestors_are_visited_once(self):
        self.link_to("base", None)
        self.link_to("left", "base")
        self.link_to("right", "base")
        links = []
        for name in ("left", "right"):
            links.append({
                "manifest": receipts.record(self.root / name / "manifest.json", self.root),
                "file": self.row(f"{name}/out.txt", name),
            })
        path = receipts.write(self.root, self.manifest(provenance=links))
        found = [item["path"] for item in receipts.chain(path, self.root)]
        self.assertEqual(sorted(found), sorted([
            "manifest.json", "left/manifest.json", "right/manifest.json",
            "base/manifest.json"]))

    def test_every_manifest_is_loaded_and_every_file_hashed_once(self):
        self.link_to("base", None)
        self.link_to("left", "base")
        self.link_to("right", "base")
        links = []
        for name in ("left", "right"):
            links.append({
                "manifest": receipts.record(self.root / name / "manifest.json", self.root),
                "file": self.row(f"{name}/out.txt", name),
            })
        path = receipts.write(self.root, self.manifest(provenance=links))
        expected = receipts.chain(path, self.root)
        loads, hashes = [], []
        real_load, real_hash = receipts.load, receipts.sha256_file

        def load(item):
            loads.append(Path(item).resolve())
            return real_load(item)

        def sha256_file(item):
            hashes.append(Path(item).resolve())
            return real_hash(item)

        with patch.object(receipts, "load", load), patch.object(receipts, "sha256_file",
                                                                 sha256_file):
            self.assertEqual(receipts.chain(path, self.root), expected)
        self.assertEqual(sorted(loads), sorted({
            (self.root / name / "manifest.json").resolve()
            for name in ("left", "right", "base")} | {path.resolve()}))
        self.assertEqual(len(hashes), len(set(hashes)), "a file was hashed more than once")
        self.assertEqual(len(hashes), 6)  # three linked manifests and their three outputs

    def test_a_tampered_shared_file_is_still_caught_through_every_route(self):
        self.link_to("base", None)
        self.link_to("left", "base")
        self.link_to("right", "base")
        links = [{"manifest": receipts.record(self.root / name / "manifest.json", self.root),
                  "file": self.row(f"{name}/out.txt", name)} for name in ("left", "right")]
        path = receipts.write(self.root, self.manifest(provenance=links))
        (self.root / "base/out.txt").write_text("tampered!")
        with self.assertRaisesRegex(receipts.ReceiptError, "mismatch"):
            receipts.chain(path, self.root)

    def test_very_long_chains_fail_cleanly_instead_of_recursing(self):
        previous = None
        for number in range(receipts.MAX_CHAIN + 40):
            path = self.link_to(f"m{number}", previous)
            previous = f"m{number}"
        with self.assertRaisesRegex(receipts.ReceiptError, "more than"):
            receipts.chain(path, self.root)

    def test_traversal_does_not_use_the_python_stack(self):
        previous = None
        for number in range(300):
            path = self.link_to(f"d{number}", previous)
            previous = f"d{number}"
        original = sys.getrecursionlimit()
        sys.setrecursionlimit(len(inspect.stack()) + 100)
        try:
            with patch.object(receipts, "MAX_CHAIN", 1000):
                found = receipts.chain(path, self.root)
        finally:
            sys.setrecursionlimit(original)
        self.assertEqual(len(found), 300)

    def test_cycles_are_detected(self):
        a = self.write("a/out.txt", "a")
        b = self.write("b/out.txt", "b")
        link_a = {"manifest": self.row("a/manifest.json", "{}"), "file": self.row("a/out.txt", "a")}
        link_b = {"manifest": self.row("b/manifest.json", "{}"), "file": self.row("b/out.txt", "b")}
        receipts.write(a.parent, self.manifest(outputs=[self.row("a/out.txt", "a")],
                                               provenance=[link_b]))
        receipts.write(b.parent, self.manifest(outputs=[self.row("b/out.txt", "b")],
                                               provenance=[link_a]))
        # Content hashes cannot be made circular, so skip the hash comparison of the link
        # itself and exercise only the traversal.
        with patch.object(receipts, "_verify_entry", lambda *args: None), \
                self.assertRaisesRegex(receipts.ReceiptError, "cycle"):
            receipts.chain(self.root / "a/manifest.json", self.root)


class PainterManifestTests(Workspace):
    def test_manifests_written_by_painter_verify_and_chain(self):
        upstream = self.write("designer/rock.sbsar", "sbsar")
        upstream_manifest = receipts.write(
            upstream.parent, receipts.build("designer", "cook", self.root, outputs=[upstream]))
        textures = self.write("painter/base.png", "png")
        project = self.write("proj/rock.spp", "spp")
        provenance = [{
            "manifest": painter_operations.receipts.record(upstream_manifest, self.root),
            "file": painter_operations.receipts.record(upstream, self.root),
        }]
        manifest = painter_operations.receipts.build(
            "painter", "export", self.root, inputs=[project], outputs=[textures],
            parameters={"preset": "x"}, seconds=1.5, version="9.1", provenance=provenance,
        )
        path = painter_operations.receipts.write(textures.parent, manifest)
        loaded = receipts.load(path)
        self.assertEqual(loaded["schema"], 1)
        receipts.verify(loaded, self.root)
        self.assertEqual([step["app"] for step in receipts.chain(path, self.root)],
                         ["painter", "designer"])
        code, result = self.cli("verify", "painter/manifest.json")
        self.assertEqual((code, result["app"], result["outputs"]), (0, "painter", 1))
        code, result = self.cli("chain", "painter/manifest.json")
        self.assertEqual(code, 0)
        self.assertEqual([step["app"] for step in result["chain"]], ["painter", "designer"])
        code, result = self.cli("verify", "painter/manifest.json", "--file",
                                "painter/base.png")
        self.assertEqual(code, 0)


class DesignerBuiltinTests(Workspace):
    def setUp(self):
        super().setUp()
        self.app = install_designer(Path(self.folder.name))
        self.env = patched_environment(DESIGNER, {
            DESIGNER_ENV: str(self.app), "CAVI_SUBSTANCE_WORKSPACE": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def cook(self, **options):
        values = {"path": None, "builtin": None, "directory": "cooked",
                  "expose_output_size": True, "expose_random_seed": True, "timeout": 30}
        values.update(options)
        return asyncio.run(designer.cook(
            values["path"], values["builtin"], values["directory"],
            values["expose_output_size"], values["expose_random_seed"], values["timeout"]))

    def test_builtin_template_receipt_is_portable(self):
        cooked = self.cook(builtin="templates/01_02_pbr_metallic_roughness")
        manifest = json.loads((self.root / cooked["manifest"]).read_text())
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["version"], "0.0.0-fake")
        template_sha = hashlib.sha256(b"fake-sbs").hexdigest()
        self.assertEqual(manifest["inputs"], [{
            "role": "app-resource", "name": "templates/01_02_pbr_metallic_roughness",
            "sha256": template_sha, "tool_version": "0.0.0-fake"}])
        self.assertNotIn(str(self.app), json.dumps(manifest))
        shutil.rmtree(self.app.parent)  # the whole fake install, not only its bin directory
        moved = self.root.parent / "other-machine"
        shutil.copytree(self.root, moved)
        steps = receipts.chain(moved / cooked["manifest"], moved)
        self.assertEqual([(step["app"], step["operation"]) for step in steps],
                         [("designer", "cook")])

    def test_workspace_source_receipts_keep_schema_one_and_record_the_tool(self):
        self.write("source.sbs", "fake-sbs")
        cooked = self.cook(path="source.sbs")
        manifest = json.loads((self.root / cooked["manifest"]).read_text())
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["version"], "0.0.0-fake")
        self.assertEqual([item["path"] for item in manifest["inputs"]], ["source.sbs"])


if __name__ == "__main__":
    unittest.main()


class PainterSharedReceiptTests(unittest.TestCase):
    """Painter reads and writes receipts with the shared receipts module."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name).resolve()

    def tearDown(self):
        self.folder.cleanup()

    def test_painter_accepts_a_schema_2_source_manifest(self):
        out = self.root / "cook"
        out.mkdir()
        sbsar = out / "material.sbsar"
        sbsar.write_bytes(b"sbsar")
        template = self.root / "template.sbs"
        template.write_bytes(b"template")
        manifest = receipts.build(
            "designer", "cook", self.root, outputs=[sbsar],
            external=[receipts.app_resource("templates/pbr", template, "16.0")],
        )
        self.assertEqual(manifest["schema"], 2)
        receipts.write(out, manifest)
        entry, path = painter_operations._receipt_source(
            sbsar, self.root, "cook/manifest.json", None)
        self.assertEqual(entry["sha256"], receipts.sha256_file(sbsar))
        self.assertEqual(path, out / "manifest.json")

    def test_painter_receipts_refuse_a_missing_output(self):
        with self.assertRaises(ValueError):
            painter_operations.receipts.build(
                "painter", "export", self.root, outputs=[self.root / "missing.png"])
