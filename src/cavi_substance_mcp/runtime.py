"""Launch the installed Painter application without closing existing projects."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .client import PainterClient


def runtime_status():
    try:
        return PainterClient().call("status")
    except (ConnectionError, TimeoutError) as exc:
        return {"connected": False, "reason": str(exc)}


def start_painter():
    status = runtime_status()
    if status.get("connected"):
        return {**status, "launch_requested": False}
    if sys.platform != "darwin":
        raise RuntimeError("Automatic Painter launch is currently supported on macOS")
    app = Path(os.environ.get(
        "CAVI_PAINTER_APP",
        "/Applications/Adobe Substance 3D Painter/Adobe Substance 3D Painter.app",
    )).expanduser().resolve()
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    executable = info.get("CFBundleExecutable", "")
    if (
        info.get("CFBundleIdentifier") != "com.adobe.Adobe-Substance-3D-Painter"
        or not isinstance(executable, str)
        or not executable
        or Path(executable).name != executable
    ):
        raise ValueError("CAVI_PAINTER_APP must identify Adobe Substance 3D Painter")
    executable = app / "Contents/MacOS" / executable
    processes = subprocess.run(
        ["/bin/ps", "-axo", "pid=,comm="],
        capture_output=True, text=True, check=True, timeout=10,
    )
    for line in processes.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) == 2 and Path(fields[1]).resolve() == executable.resolve():
            raise RuntimeError(
                "Painter is already running without a reachable scripting connection. "
                "Save and quit it, then run runtime start again; no process was stopped."
            )
    subprocess.run(
        ["/usr/bin/open", "-a", str(app), "--args", "--enable-remote-scripting"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return {
        "connected": False,
        "launch_requested": True,
        "app": str(app),
        "reason": "Painter launch requested; inspect status after startup or license prompts finish",
    }
