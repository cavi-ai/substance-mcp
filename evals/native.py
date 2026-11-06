"""Opt-in headless Designer CLI acceptance and read-only MCP evaluation.

The route runs only installed command-line tools. GUI SDK batches and native UI tests
are excluded because they can activate an application. Skips fail acceptance, and the
installed CLI status and test outcomes remain separate from simulated evidence.
"""

import asyncio
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import common
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TESTS = [
    "test_designer_native.DesignerNativeTests.test_cook_render_pack",
    "test_designer_native.DesignerNativeTests.test_bake_normal_and_ao",
    "test_designer_native.DesignerNativeTests.test_relative_graph_preserves_exposed_render_resolution",
]


class NativeResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.append({"id": test.id(), "passed": True})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes.append({"id": test.id(), "passed": False, "reason": "assertion failed"})

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes.append({"id": test.id(), "passed": False, "reason": "test error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes.append({"id": test.id(), "passed": False, "reason": f"skipped: {reason}"})


async def preflight(app, server, env):
    if app != "substance-designer":
        raise RuntimeError("No proven background native evaluation route for this application")
    tool = "designer_status"
    params = StdioServerParameters(command=sys.executable, args=server, env=env)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        result = await client.call_tool(tool, {})
        if result.is_error:
            raise RuntimeError(str(result.content))
        status = result.structured_content
    if not status.get("available"):
        raise RuntimeError(f"Native application unavailable: {status}")
    if "fake" in json.dumps(status).lower():
        raise RuntimeError("Native evaluation refuses a simulated application")
    return status


def run(app: str, server: list, prefix: str, evaluator: Path, output: Path):
    workspace = common.make_workspace(output)
    env = common.environment(output, workspace)
    status = asyncio.run(preflight(app, server, env))
    (output / "native-application.json").write_text(json.dumps(status, indent=2) + "\n")
    flag, names = "CAVI_DESIGNER_NATIVE", TESTS
    sys.path.insert(0, str(common.ROOT / "tests"))
    try:
        with patch.dict(os.environ, {**env, flag: "1",
                                    "CAVI_NATIVE_EVIDENCE_DIR": str(output / "failures")},
                        clear=True):
            suite = unittest.TestLoader().loadTestsFromNames(names)
            with (output / "native-tests.log").open("w") as stream, \
                    contextlib.redirect_stderr(stream), contextlib.redirect_stdout(stream):
                tested = unittest.TextTestRunner(
                    stream=stream, verbosity=2, failfast=True, resultclass=NativeResult).run(suite)
    finally:
        sys.path.pop(0)
    outcomes = tested.outcomes
    if len(outcomes) != len(names):
        outcomes.append({"id": "native-test-coverage", "passed": False,
                         "reason": "not every selected native test ran"})
    if any(not case["passed"] for case in outcomes):
        (output / "cases.json").write_text(json.dumps({
            "mode": "native", "application": app, "native_application": status,
            "native_evidence": True, "cases": outcomes,
        }, indent=2) + "\n")
        raise SystemExit(f"Native acceptance failed; inspect {output / 'native-tests.log'}")
    manifest = json.loads((common.ROOT / f"evals/{prefix}core.manifest.json").read_text())
    manifest_path = output / "native.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    common.finish(mode="native", evaluator=evaluator, output=output, results=outcomes,
                  env=env, app=app, server=server, manifest_prefix=prefix,
                  native_status="installed application acceptance tests and read-only MCP probes",
                  native_application=status,
                  manifest_path=manifest_path)
