"""Command-line startup and status for the installed Painter application."""

import argparse
import json
import plistlib
import subprocess

from .runtime import runtime_status, start_painter


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cavi-substance-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime", help="Launch Painter with remote scripting or inspect it")
    runtime.add_argument("action", choices=("start", "status"))
    args = parser.parse_args(argv)
    try:
        result = start_painter() if args.action == "start" else runtime_status()
        print(json.dumps(result))
        return 0 if result.get("connected") or result.get("launch_requested") else 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError,
            plistlib.InvalidFileException) as exc:
        print(json.dumps({"connected": False, "launch_requested": False, "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
