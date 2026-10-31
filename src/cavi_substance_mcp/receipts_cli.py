"""Command-line verification of the receipts every Substance server writes."""

import argparse
import json
from pathlib import Path

from . import receipts
from .core import workspace


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cavi-substance-receipts")
    actions = parser.add_subparsers(dest="receipt_action", required=True)
    verify = actions.add_parser("verify", help="Verify every file recorded in a manifest")
    verify.add_argument("manifest")
    verify.add_argument("--workspace")
    verify.add_argument("--file", help="Verify one file instead of the whole manifest.")
    verify.add_argument("--role", choices=("inputs", "outputs"), default="outputs")
    verify.add_argument("--json", action="store_true")
    chain = actions.add_parser("chain", help="Verify a manifest and its provenance chain")
    chain.add_argument("manifest")
    chain.add_argument("--workspace")
    chain.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    return _chain_receipt(args) if args.receipt_action == "chain" else _verify_receipt(args)


def _verify_receipt(args):
    root = workspace.root(args.workspace)
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


def _chain_receipt(args):
    root = workspace.root(args.workspace)
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    try:
        manifest_path = manifest_path.resolve(strict=True)
        result = {"ok": True, "chain": receipts.chain(manifest_path, root)}
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
    if "chain" in result:
        for step in result["chain"]:
            print(f"{step['path']}: {step.get('app')} {step.get('operation')}")
        return
    print(f"app: {result.get('app')} {result.get('operation')}")
    print(f"inputs: {result.get('inputs')} outputs: {result.get('outputs')}")


if __name__ == "__main__":
    raise SystemExit(main())
