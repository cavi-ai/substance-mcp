"""Command-line startup and status for the installed Painter application."""

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path

from . import receipts
from .runtime import runtime_status, start_painter


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cavi-substance-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime", help="Launch Painter with remote scripting or inspect it")
    runtime.add_argument("action", choices=("start", "status"))
    receipt = commands.add_parser("receipts", help="Verify a cross-application manifest")
    actions = receipt.add_subparsers(dest="receipt_action", required=True)
    verify = actions.add_parser("verify", help="Verify every file recorded in a manifest")
    verify.add_argument("manifest")
    verify.add_argument("--workspace")
    verify.add_argument("--file", help="Verify one file instead of the whole manifest.")
    verify.add_argument("--role", choices=("inputs", "outputs"), default="outputs")
    verify.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "receipts":
        return _verify_receipt(args)
    try:
        result = start_painter() if args.action == "start" else runtime_status()
        print(json.dumps(result))
        return 0 if result.get("connected") or result.get("launch_requested") else 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError,
            plistlib.InvalidFileException) as exc:
        print(json.dumps({"connected": False, "launch_requested": False, "reason": str(exc)}))
        return 1


def _root(value):
    path = Path(value or os.environ.get("CAVI_SUBSTANCE_WORKSPACE")
                or Path.home() / "Documents/CaviGameArt")
    return path.expanduser().resolve()


def _verify_receipt(args):
    root = _root(args.workspace)
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    try:
        manifest_path = manifest_path.resolve(strict=True)
        manifest = receipts.load(manifest_path)
        if args.file:
            target = Path(args.file).expanduser()
            if not target.is_absolute():
                target = root / target
            entry = receipts.verify_file(manifest, root, target, args.role)
            result = {"ok": True, "manifest": _display(manifest_path, root),
                      "role": args.role, "file": entry}
        else:
            receipts.verify(manifest, root)
            result = {"ok": True, "manifest": _display(manifest_path, root),
                      "app": manifest.get("app"), "operation": manifest.get("operation"),
                      "inputs": len(manifest.get("inputs", [])),
                      "outputs": len(manifest.get("outputs", []))}
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
        _print_receipt(result, args.json)
        return 1
    _print_receipt(result, args.json)
    return 0


def _display(path, root):
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _print_receipt(result, as_json):
    if as_json:
        print(json.dumps(result, indent=2))
        return
    if not result.get("ok"):
        print(f"failed: {result.get('error')}")
        return
    print(f"manifest: {result['manifest']}")
    if "file" in result:
        print(f"{result['role']}: {result['file']['path']}")
        return
    print(f"app: {result.get('app')} {result.get('operation')}")
    print(f"inputs: {result.get('inputs')} outputs: {result.get('outputs')}")


if __name__ == "__main__":
    raise SystemExit(main())
