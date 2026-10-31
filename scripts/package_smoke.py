#!/usr/bin/env python3
"""Build the distributions, install the wheel into a clean venv, and run what a user runs.

The unit tests import the source tree. This proves the artifact: that the wheel
carries the module `painter/client.py` reads at import time, that neither distribution
ships tests, evals or repository tooling, that both carry the LICENSE, and that
each installed server script answers an MCP `initialize` and `tools/list` over
stdio with the package version and the expected tool count, and that the receipts script
verifies a manifest.

    uv run --locked python scripts/package_smoke.py              # build, then check
    uv run --locked python scripts/package_smoke.py --dist dist  # check what a release built

Standard library only, so it runs unchanged on Linux, macOS and Windows. Needs `uv` on PATH
(or in $UV). The wheel is installed against the dependency versions in uv.lock, so a new
upstream release cannot fail this gate; the nightly workflow covers newer versions.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 90  # seconds per MCP request or subprocess; first start-up imports numpy and pillow

# console script -> tool count
SERVERS = {
    "cavi-substance-mcp": 7,
    "cavi-substance-designer-mcp": 3,
    "cavi-substance-sampler-mcp": 8,
}
RECEIPTS_SCRIPT = "cavi-substance-receipts"
# The files that run inside Painter and Sampler are loaded from the installed package, and
# receipts_cli.py is the entry point of RECEIPTS_SCRIPT.
REQUIRED_IN_WHEEL = (
    "cavi_substance_mcp/painter/operations.py",
    "cavi_substance_mcp/core/workspace.py",
    "cavi_substance_mcp/sampler/operations.py",
    "cavi_substance_mcp/receipts_cli.py",
)
FORBIDDEN_IN_WHEEL = ("tests/", "evals/")
FORBIDDEN_IN_SDIST = (
    ".github/",
    ".tmp/",
    "package.json",
    "package-lock.json",
    "uv.lock",
    "docs/plans/",
)


class Failure(Exception):
    """A check did not hold; the message says why."""


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def run(self, name, function):
        try:
            result = function()
        except Failure as exc:
            print(f"FAIL {name}: {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 - a crashed check is reported, not fatal
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
        else:
            print(f"PASS {name}", flush=True)
            return result
        self.failed.append(name)
        return None


def expect(condition, message):
    if not condition:
        raise Failure(message)


def uv_command() -> str:
    found = os.environ.get("UV") or shutil.which("uv")
    if not found:
        raise SystemExit("error: uv is required (https://docs.astral.sh/uv/)")
    return found


def clean_environment(**extra: str) -> dict[str, str]:
    """The caller's environment minus anything that could pull in the source tree or its venv."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"}
    }
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def run(command, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("timeout", 600)
    result = subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **kwargs,
    )
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
        raise Failure(f"{' '.join(str(p) for p in command[:3])} exited {result.returncode}:\n"
                      + "\n".join(tail))
    return result


def venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


class StdioSession:
    """Newline-delimited JSON-RPC against a server process, with a timeout on every read."""

    def __init__(self, command, env, cwd):
        self.command = [str(part) for part in command]
        self.stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            env=env,
            cwd=cwd,
        )
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.next_id = 0
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()

    def _pump(self) -> None:
        for line in iter(self.process.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message: dict) -> None:
        payload = json.dumps({"jsonrpc": "2.0", **message}).encode("utf-8") + b"\n"
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def notify(self, method: str) -> None:
        self._send({"method": method})

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure(f"{method}: no response within {TIMEOUT}s{self._stderr_tail()}")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise Failure(f"{method}: server exited before responding{self._stderr_tail()}")
            try:
                reply = json.loads(line)
            except ValueError:
                raise Failure(f"{method}: stdout is not JSON-RPC: {line[:200]!r}") from None
            if reply.get("id") != self.next_id:
                continue  # a notification, or a server-to-client request
            if "error" in reply:
                raise Failure(f"{method}: JSON-RPC error {reply['error']}")
            return reply["result"]

    def _stderr_tail(self) -> str:
        self.stderr.flush()
        self.stderr.seek(0)
        tail = self.stderr.read().decode("utf-8", "replace").strip().splitlines()[-10:]
        return ("\nserver stderr:\n" + "\n".join(tail)) if tail else ""

    def close(self) -> None:
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        finally:
            self.pump.join(timeout=5)
            self.process.stdout.close()
            self.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def handshake(command, env, cwd) -> tuple[dict, list[dict]]:
    """`initialize`, the `initialized` notification, then every page of `tools/list`."""
    with StdioSession(command, env, cwd) as session:
        initialized = session.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "package-smoke", "version": "0"},
            },
        )
        session.notify("notifications/initialized")
        tools: list[dict] = []
        cursor = None
        while True:
            page = session.request("tools/list", {"cursor": cursor} if cursor else None)
            tools += page["tools"]
            cursor = page.get("nextCursor")
            if not cursor:
                return initialized, tools


def only(directory: Path, pattern: str) -> Path:
    found = sorted(directory.glob(pattern))
    expect(len(found) == 1, f"expected exactly one {pattern} in {directory}, found {len(found)}")
    return found[0]


def wheel_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return archive.namelist()


def sdist_names(sdist: Path) -> list[str]:
    """Member paths with the leading `<name>-<version>/` directory removed."""
    with tarfile.open(sdist) as archive:
        return [name.split("/", 1)[1] for name in archive.getnames() if "/" in name]


def check_wheel_contents(wheel: Path) -> None:
    names = wheel_names(wheel)
    for required in REQUIRED_IN_WHEEL:
        expect(required in names, f"wheel is missing {required} (read at import time)")
    stray = [n for n in names if any(n.startswith(p) or f"/{p}" in n for p in FORBIDDEN_IN_WHEEL)]
    expect(not stray, f"wheel ships test or eval files: {stray[:5]}")


def check_license(wheel: Path, sdist: Path) -> None:
    in_wheel = [n for n in wheel_names(wheel) if ".dist-info/" in n and Path(n).name == "LICENSE"]
    in_sdist = [n for n in sdist_names(sdist) if n == "LICENSE"]
    missing = [label for label, found in (("wheel", in_wheel), ("sdist", in_sdist)) if not found]
    expect(not missing, f"LICENSE is missing from the {' and '.join(missing)}")


def check_sdist_allowlist(sdist: Path) -> None:
    names = sdist_names(sdist)
    stray = sorted(
        {p for p in FORBIDDEN_IN_SDIST for n in names if n == p.rstrip("/") or n.startswith(p)}
    )
    expect(not stray, f"sdist ships repository tooling: {', '.join(stray)}")


def install(uv: str, wheel: Path, venv: Path, python: Path, constraints: Path, cwd: Path) -> bool:
    """Install into a fresh venv, pinning dependencies to uv.lock like `uv sync --locked`."""
    run([uv, "venv", "--python", sys.executable, venv], cwd=cwd)
    run([uv, "export", "--locked", "--no-dev", "--no-emit-project", "--no-hashes", "--quiet",
         "--output-file", constraints], cwd=ROOT)
    run([uv, "pip", "install", "--python", python, "--constraint", constraints, "--quiet", wheel],
        cwd=cwd)
    return True


def check_install_isolation(python: Path, venv: Path, version: str, cwd: Path, env: dict) -> None:
    probe = (
        "import importlib.metadata as m, cavi_substance_mcp as p;"
        "print(p.__file__); print(m.version('cavi-substance-mcp'))"
    )
    file, installed = run([python, "-I", "-c", probe], cwd=cwd, env=env).stdout.split("\n")[:2]
    expect(
        venv.resolve() in Path(file).resolve().parents,
        f"imported {file}, which is outside the clean venv {venv}",
    )
    expect(installed == version, f"installed metadata says {installed}, pyproject says {version}")


def check_server(script: Path, name: str, count: int, version: str, env: dict, cwd: Path) -> None:
    initialized, tools = handshake([script], env, cwd)
    info = initialized["serverInfo"]
    expect(info["name"] == name, f"serverInfo.name is {info['name']!r}, want {name!r}")
    expect(
        info["version"] == version,
        f"serverInfo.version is {info['version']!r}, want the package version {version!r}",
    )
    names = sorted(tool["name"] for tool in tools)
    expect(len(names) == count, f"{len(names)} tools listed, want {count}: {names}")


def check_runtime_status(script: Path, env: dict, cwd: Path) -> None:
    """Offline, `runtime status` reports connected=false (exit 1) rather than crashing."""
    result = subprocess.run(
        [str(script), "runtime", "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT,
        env=env,
        cwd=cwd,
        check=False,
    )
    expect(
        result.returncode in (0, 1),
        f"exited {result.returncode}: {result.stderr.strip()[-300:]}",
    )
    try:
        status = json.loads(result.stdout)
    except ValueError:
        raise Failure(f"stdout is not JSON: {result.stdout[:200]!r}") from None
    expect(isinstance(status, dict) and isinstance(status.get("connected"), bool),
           f"no boolean `connected` in {status!r}")


def invoke(script: Path, arguments: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT,
        env=env,
        cwd=cwd,
        check=False,
    )


def check_receipts_script(script: Path, python: Path, env: dict, workspace: Path, cwd: Path) -> None:
    """The receipts script verifies a real manifest and its chain, and rejects a changed file."""
    seed = (
        "import sys; from pathlib import Path; from cavi_substance_mcp import receipts;"
        "root = Path(sys.argv[1]); out = root / 'handoff'; out.mkdir();"
        "(out / 'a.txt').write_text('smoke');"
        "receipts.write(out, receipts.build('smoke', 'check', root, outputs=[out / 'a.txt']))"
    )
    run([python, "-I", "-c", seed, workspace], cwd=cwd, env=env)

    def receipt(action: str) -> tuple[int, dict]:
        result = invoke(script, [action, "handoff/manifest.json", "--json"], env, cwd)
        try:
            return result.returncode, json.loads(result.stdout)
        except ValueError:
            raise Failure(
                f"{action}: stdout is not JSON: {result.stdout[:200]!r} {result.stderr[-200:]!r}"
            ) from None

    code, body = receipt("verify")
    expect(code == 0 and body.get("ok") is True and body.get("outputs") == 1,
           f"verify exited {code} with {body!r}, want exit 0 and one verified output")
    code, body = receipt("chain")
    steps = [step.get("app") for step in body.get("chain", [])]
    expect(code == 0 and body.get("ok") is True and steps == ["smoke"],
           f"chain exited {code} with {body!r}, want exit 0 and the one smoke manifest")
    (workspace / "handoff" / "a.txt").write_text("changed")
    code, body = receipt("verify")
    expect(code == 1 and body.get("ok") is False and body.get("error"),
           f"verify of a changed file exited {code} with {body!r}, want exit 1 and an error")


def check_no_painter_receipts_subcommand(script: Path, env: dict, cwd: Path) -> None:
    """Receipts moved to their own script; the Painter script must refuse the old subcommand."""
    result = invoke(script, ["receipts", "verify", "handoff/manifest.json"], env, cwd)
    expect(result.returncode == 2 and "invalid choice" in result.stderr,
           f"exited {result.returncode}, want an argparse refusal (2): {result.stderr.strip()[-300:]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist",
        type=Path,
        help="Check the wheel and sdist already built in this directory instead of building.",
    )
    args = parser.parse_args(argv)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    uv = uv_command()

    scratch_root = ROOT / ".tmp"
    scratch_root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="package-smoke-", dir=scratch_root)).resolve()
    # The venv, the workspace and the working directory live outside the repository so nothing
    # in the source tree can reach sys.path or the default workspace.
    outside = Path(tempfile.mkdtemp(prefix="package-smoke-venv-")).resolve()
    report = Report()

    if args.dist:
        dist = args.dist.resolve()
    else:
        dist = work / "dist"
        print(f"building {project['name']} {version} with uv build", flush=True)
        run([uv, "build", "--out-dir", dist, ROOT], cwd=ROOT)
    wheel = only(dist, "*.whl")
    sdist = only(dist, "*.tar.gz")
    print(f"checking {wheel.name} and {sdist.name}", flush=True)

    report.run("wheel contains the in-app modules, no tests/ or evals/",
               lambda: check_wheel_contents(wheel))
    report.run("wheel and sdist contain LICENSE", lambda: check_license(wheel, sdist))
    report.run("sdist excludes " + ", ".join(FORBIDDEN_IN_SDIST),
               lambda: check_sdist_allowlist(sdist))

    venv = outside / "venv"
    python = venv_executable(venv, "python")
    if report.run("install the wheel into a clean venv",
                  lambda: install(uv, wheel, venv, python, work / "constraints.txt", outside)):
        workspace = outside / "workspace"
        workspace.mkdir()
        env = clean_environment(CAVI_SUBSTANCE_WORKSPACE=str(workspace))
        report.run("installed package is the wheel, not the source tree",
                   lambda: check_install_isolation(python, venv, version, outside, env))
        for script_name, count in SERVERS.items():
            script = venv_executable(venv, script_name)
            report.run(
                f"{script_name}: initialize + tools/list -> {count} tools, version {version}",
                lambda script=script, name=script_name, count=count: check_server(
                    script, name, count, version, env, outside),
            )
        report.run("cavi-substance-mcp runtime status prints JSON offline",
                   lambda: check_runtime_status(venv_executable(venv, "cavi-substance-mcp"),
                                                env, outside))
        report.run("cavi-substance-mcp no longer has a receipts subcommand",
                   lambda: check_no_painter_receipts_subcommand(
                       venv_executable(venv, "cavi-substance-mcp"), env, outside))
        report.run(f"{RECEIPTS_SCRIPT}: verify and chain a manifest, reject a changed file",
                   lambda: check_receipts_script(venv_executable(venv, RECEIPTS_SCRIPT), python,
                                                 env, workspace, outside))
    return finish(report, work, outside)


def finish(report: Report, work: Path, outside: Path) -> int:
    if report.failed:
        print(f"\n{len(report.failed)} check(s) failed; artifacts kept in {work} and {outside}",
              file=sys.stderr)
        return 1
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)
    print("\npackage smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
