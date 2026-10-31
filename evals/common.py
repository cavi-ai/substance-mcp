"""Code shared by the three evaluation runners: run.py, designer_run.py and sampler_run.py.

Development only. It needs the MCP client SDK and the pinned mcpeval evaluator, and it is
not part of the installed package. A runner builds its workspace and environment, then
calls ``exercise`` to play its corpus over stdio and ``finish`` to write the report and run
the evaluator.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
MODES = ["protocol", "simulated"]
# What a case may fail with; anything else is a defect in the runner and propagates.
CASE_ERRORS = (AssertionError, KeyError, TypeError, ValueError, OSError)


def pointer(value, path):
    """The part of ``value`` that a JSON pointer such as ``/files/0/path`` names."""
    for key in path.strip("/").split("/"):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _state(path: Path):
    """What a protected path looks like: its SHA-256, ``link:<target>`` or None when gone."""
    if path.is_symlink():
        return f"link:{os.readlink(path)}"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def fingerprint(*roots) -> dict:
    """The state of every file and symbolic link below ``roots``.

    A link is recorded as itself and never followed. A call that removes or re-points a link
    is therefore noticed, and a link that leaves the tree cannot pull outside content into
    the comparison.
    """
    return {path: _state(path) for root in roots for path in root.rglob("*")
            if path.is_symlink() or path.is_file()}


def assert_untouched(before: dict) -> None:
    """Fail if any path in a ``fingerprint`` was deleted or changed since."""
    for path, state in before.items():
        now = _state(path)
        assert now is not None, f"Failed call deleted {path.name}"
        assert now == state, f"Failed call changed {path.name}"


def load_corpus(name: str) -> list:
    corpus = json.loads((ROOT / "evals" / name).read_text())
    if len({case["id"] for case in corpus}) != len(corpus):
        raise ValueError("Corpus case IDs must be unique")
    return corpus


def check_coverage(corpus: list, tools: dict) -> None:
    """The corpus must cover exactly the listed tools, each in the way its kind needs."""
    expected = {case["tool"] for case in corpus}
    if set(tools) != expected:
        raise AssertionError(f"Tool coverage drift: listed={set(tools)}, corpus={expected}")
    for tool in tools.values():
        covered = [case for case in corpus if case["tool"] == tool.name]
        if not any(case["kind"] == "success" for case in covered):
            raise AssertionError(f"Missing success coverage: {tool.name}")
        if tool.input_schema.get("required") and not any(
            case["kind"] == "invalid" for case in covered
        ):
            raise AssertionError(f"Missing invalid-input coverage: {tool.name}")
        if not (tool.annotations and tool.annotations.read_only_hint) and not any(
            case["kind"] == "safety" for case in covered
        ):
            raise AssertionError(f"Missing safety coverage: {tool.name}")


class Hooks:
    """What a runner adds around each case; the defaults add nothing."""

    def setup(self, case: dict) -> None:
        """Called first, before the workspace is fingerprinted."""

    async def prepare(self, session, tools: dict, case: dict, arguments: dict) -> dict:
        """Return the arguments to call the tool with (a copy of the case's, to edit)."""
        return arguments

    def observe(self, case: dict, arguments: dict, reply) -> None:
        """Called with every reply, error or not."""

    def check_success(self, case: dict, reply) -> None:
        """Extra assertions on a reply the case expects to succeed."""


def _outcome(case: dict, reason: str | None = None) -> dict:
    result = {"id": case["id"], "tool": case["tool"], "kind": case["kind"],
              "passed": reason is None}
    if reason is not None:
        result["reason"] = reason
    return result


async def _run_case(session, tools: dict, case: dict, workspace: Path, hooks: Hooks) -> None:
    hooks.setup(case)
    protected = fingerprint(workspace, workspace.parent / "outside")
    arguments = await hooks.prepare(session, tools, case, dict(case["arguments"]))
    reply = await session.call_tool(case["tool"], arguments)
    hooks.observe(case, arguments, reply)
    error = "\n".join(getattr(item, "text", "") for item in reply.content)
    if case.get("error"):
        assert reply.is_error, "Expected an explicit MCP tool error"
        assert case["error"].lower() in error.lower(), error
        assert_untouched(protected)
    else:
        assert not reply.is_error, error
        data = reply.structured_content
        for path, value in case.get("equals", {}).items():
            assert pointer(data, path) == value, f"Mismatch at {path}: {data}"
        for path in case.get("required", []):
            pointer(data, path)
        hooks.check_success(case, reply)
        for path in case.get("files", []):
            target = workspace / path
            assert target.is_file() and target.stat().st_size > 0, path
    for path in case.get("absent", []):
        assert not (workspace / path).exists(), path


async def exercise(corpus_name: str, mode: str, server: list, env: dict, workspace: Path,
                   hooks: Hooks | None = None) -> list:
    """Play the cases of ``corpus_name`` that belong to ``mode`` against ``server``.

    ``server`` is the argument list after the Python executable. Returns one result per case.
    """
    hooks = hooks or Hooks()
    corpus = load_corpus(corpus_name)
    selected = [case for case in corpus if mode in case["modes"]]
    results = []
    params = StdioServerParameters(command=sys.executable, args=server, env=env)
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=90) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        check_coverage(corpus, tools)
        for case in selected:
            try:
                await _run_case(session, tools, case, workspace, hooks)
                results.append(_outcome(case))
            except CASE_ERRORS as exc:
                results.append(_outcome(case, str(exc)))
    return results


def start(directory_prefix: str = ""):
    """Read the mode from the command line and make this run's output directory.

    Returns ``(mode, evaluator, output)``.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES)
    args = parser.parse_args()
    evaluator = ROOT / "node_modules/.bin/mcpeval"
    if not evaluator.exists():
        parser.error("Install the pinned evaluator with npm ci first")
    output_root = ROOT / ".tmp/evals"
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"{directory_prefix}{args.mode}-", dir=output_root))
    return args.mode, evaluator, output


def make_workspace(output: Path, folders=()) -> Path:
    """Make ``output/workspace`` and ``output/outside``, with a link ``escape`` from the first
    to the second, and the given folders inside the workspace. Returns the workspace."""
    workspace, outside = output / "workspace", output / "outside"
    for folder in (*(workspace / name for name in folders), workspace, outside):
        folder.mkdir(parents=True, exist_ok=True)
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    return workspace


def environment(output: Path, workspace: Path, drop=()) -> dict:
    """The server's environment: this one without the ``drop`` variables, plus the run's own."""
    env = {key: value for key, value in os.environ.items() if key not in drop}
    env.update({"MCPEVAL_HOME": str(output / "store"),
                "CAVI_SUBSTANCE_WORKSPACE": str(workspace)})
    return env


def finish(*, mode: str, evaluator: Path, output: Path, results: list, env: dict, app: str,
           server: list, manifest_prefix: str, native_status: str) -> None:
    """Write ``cases.json``, run mcpeval over the server, print a summary and exit non-zero
    when a case or the evaluator failed."""
    report = {
        "mode": mode,
        "application": app,
        "cases": results,
        "native_evidence": False,
        "native_status": native_status,
        "model_evaluation": "not supported",
    }
    (output / "cases.json").write_text(json.dumps(report, indent=2) + "\n")
    manifest = "protocol" if mode == "protocol" else "core"
    command = [str(evaluator), "probe", "--server", f"{app}-{mode}", "--manifest",
               str(ROOT / f"evals/{manifest_prefix}{manifest}.manifest.json"), "--format", "json"]
    if mode == "protocol":
        command.append("--gate-only")
    command.extend(["--", sys.executable, *server])
    with (output / "mcpeval.json").open("w") as stream:
        evaluated = subprocess.run(command, env=env, cwd=ROOT, stdout=stream, check=False)
    failed = [case for case in results if not case["passed"]]
    print(json.dumps({"mode": mode, "passed": len(results) - len(failed), "failed": failed,
                      "mcpeval_exit": evaluated.returncode, "artifacts": str(output)},
                     indent=2))
    if failed or evaluated.returncode:
        raise SystemExit(1)
