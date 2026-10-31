"""The code that runs inside Painter and Sampler may use only the standard library.

Painter and Sampler run our Python in their own embedded interpreters, which have none of
this package's dependencies and cannot see the host-side modules. Importing each in-app
module with ``python -I -S`` and only ``src/`` on the path proves there is no third-party
import at module level, and the loaded-module allow-lists keep host-only modules (process
runner, discovery, lock, job model, MCP servers) out of the closure.
"""

import ast
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from cavi_substance_mcp.painter import client

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "cavi_substance_mcp"
MINIMUM = (3, 11)

# Entry module -> the package modules its import may load.
IN_APP = {
    "cavi_substance_mcp.receipts": {
        "cavi_substance_mcp", "cavi_substance_mcp.receipts",
    },
    "cavi_substance_mcp.core.workspace": {
        "cavi_substance_mcp", "cavi_substance_mcp.core", "cavi_substance_mcp.core.workspace",
    },
    "cavi_substance_mcp.sampler.targets": {
        "cavi_substance_mcp", "cavi_substance_mcp.sampler", "cavi_substance_mcp.sampler.targets",
    },
    "cavi_substance_mcp.sampler.names": {
        "cavi_substance_mcp", "cavi_substance_mcp.sampler", "cavi_substance_mcp.sampler.names",
    },
    "cavi_substance_mcp.sampler.operations": {
        "cavi_substance_mcp", "cavi_substance_mcp.core", "cavi_substance_mcp.core.workspace",
        "cavi_substance_mcp.receipts", "cavi_substance_mcp.sampler",
        "cavi_substance_mcp.sampler.names", "cavi_substance_mcp.sampler.operations",
        "cavi_substance_mcp.sampler.targets",
    },
}
# Source files whose every import statement, even one inside a function, is checked.
# Value: top-level names the host application provides to code running inside it.
APPLICATION_MODULES = {
    "painter/operations.py": {"substance_painter", "PySide6"},
    "receipts.py": set(),
    "core/workspace.py": set(),
    "sampler/names.py": set(),
    "sampler/targets.py": set(),
    "sampler/operations.py": {"substance_sampler"},
}

# The probes report on themselves so that "standard library" means the probe
# interpreter's own list: a private module such as _sha512 exists only in some versions.
REPORT = """
def report():
    tops = {{name.split(".")[0] for name in sys.modules}} - {{"__main__"}}
    print(json.dumps({{
        "package": sorted(
            name for name in sys.modules if name.split(".")[0] == "cavi_substance_mcp"
        ),
        "third_party": sorted(tops - set(sys.stdlib_module_names) - {{"cavi_substance_mcp"}}),
    }}))
"""
IMPORT_PROBE = """
import importlib, json, sys
sys.path.insert(0, {src!r})
for name in {modules!r}:
    importlib.import_module(name)
""" + REPORT + "report()"
PAYLOAD_PROBE = """
import json, sys
sys.path.insert(0, {src!r})
namespace = {{}}
exec(compile(sys.stdin.read(), "cavi_painter_operations", "exec"), namespace)
assert callable(namespace["execute"])
""" + REPORT + "report()"


def interpreters():
    """This interpreter, plus a 3.11 one when the machine has it (the oldest supported)."""
    found = {sys.executable: sys.version_info[:2]}
    other = shutil.which("python3.11")
    if other and other != sys.executable:
        found[other] = MINIMUM
    return found


def run(executable, code, stdin=None):
    return subprocess.run(
        [executable, "-I", "-S", "-c", code], input=stdin, capture_output=True, text=True,
        timeout=120, check=False,
    )


def loaded(result):
    return json.loads(result.stdout.strip().splitlines()[-1])


class IsolationTests(unittest.TestCase):
    def test_the_probe_cannot_see_third_party_packages(self):
        # Guards every other test here: if site-packages were importable the probes
        # below would pass for the wrong reason.
        for executable in interpreters():
            with self.subTest(executable=executable):
                result = run(executable, "import pydantic")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ModuleNotFoundError", result.stderr)


class ClosureTests(unittest.TestCase):
    def test_each_in_app_module_imports_with_the_standard_library_only(self):
        for executable in interpreters():
            for module, allowed in IN_APP.items():
                with self.subTest(executable=executable, module=module):
                    result = run(executable, IMPORT_PROBE.format(src=str(SRC), modules=[module]))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = loaded(result)
                    self.assertEqual(report["third_party"], [])
                    self.assertEqual(sorted(set(report["package"]) - allowed), [])

    def test_the_painter_payload_runs_as_client_builds_it(self):
        for executable in interpreters():
            with self.subTest(executable=executable):
                result = run(executable, PAYLOAD_PROBE.format(src=str(SRC)), client.SOURCE)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = loaded(result)
                self.assertEqual(report["third_party"], [])
                self.assertEqual(report["package"], [])


def imported_names(path):
    """Every (top-level module, relative level, full module text) a file imports."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(item.name.split(".")[0], 0, item.name) for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                found.append((module.split(".")[0], node.level, module))
            else:
                # "from . import runner" imports the submodule runner.
                found += [(item.name, node.level, item.name) for item in node.names]
    return found


class StaticImportTests(unittest.TestCase):
    """The dynamic probes cannot see an import that is only executed inside a function."""

    def test_no_import_statement_reaches_outside_the_standard_library(self):
        for relative, provided in APPLICATION_MODULES.items():
            for top, level, module in imported_names(PACKAGE / relative):
                with self.subTest(file=relative, module=module or "."):
                    if level:
                        continue  # relative imports are checked against the closure below
                    self.assertTrue(
                        top in sys.stdlib_module_names or top in provided,
                        f"{relative} imports {module}, which is not standard library",
                    )

    def test_the_painter_payload_imports_nothing_from_the_package(self):
        # The one exception is the host-side fallback for the receipts module, which the
        # payload bundle defines before the operations run.
        for top, level, module in imported_names(PACKAGE / "painter/operations.py"):
            with self.subTest(module=module):
                if level:
                    self.assertEqual((level, module or "receipts"), (2, "receipts"))
                else:
                    self.assertNotEqual(top, "cavi_substance_mcp")

    def test_relative_imports_stay_inside_the_in_app_set(self):
        in_app = {"receipts", "core", "core.workspace", "sampler.names", "sampler.targets",
                  "sampler"}
        for relative in APPLICATION_MODULES:
            package = Path(relative).parent.as_posix().replace(".", "")
            for _top, level, module in imported_names(PACKAGE / relative):
                if not level:
                    continue
                with self.subTest(file=relative, module=module or "."):
                    base = [] if package == "" else package.split("/")
                    parts = base[: len(base) - (level - 1)] if level > 1 else base
                    target = ".".join([*parts, *([module] if module else [])])
                    self.assertIn(target, in_app | {""})


class SyntaxFloorTests(unittest.TestCase):
    def test_in_app_sources_parse_as_python_3_11(self):
        for relative in APPLICATION_MODULES:
            with self.subTest(file=relative):
                source = (PACKAGE / relative).read_text(encoding="utf-8")
                ast.parse(source, relative, feature_version=MINIMUM)

    def test_the_payload_compiles_as_the_client_sends_it(self):
        ast.parse(client.SOURCE, "cavi_painter_operations", feature_version=MINIMUM)
        compile(client.SOURCE, "cavi_painter_operations", "exec")

    def test_a_real_python_3_11_compiles_every_in_app_source_when_available(self):
        executable = shutil.which("python3.11")
        if executable is None:
            self.skipTest("no python3.11 on PATH; the 3.11 CI legs compile these natively")
        for relative in APPLICATION_MODULES:
            with self.subTest(file=relative):
                code = ("import sys; "
                        "compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')")
                result = subprocess.run(
                    [executable, "-I", "-S", "-c", code, str(PACKAGE / relative)],
                    capture_output=True, text=True, timeout=60, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
