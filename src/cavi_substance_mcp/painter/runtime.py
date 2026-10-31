"""Launch the installed Painter application without closing existing projects."""

import plistlib
import subprocess
import sys
from pathlib import Path

from ..core import discovery
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
    located = discovery.find_app("painter", "Adobe Substance 3D Painter", "CAVI_PAINTER_APP")
    if located.path is None:
        raise RuntimeError("Painter not found; set CAVI_PAINTER_APP to its .app bundle")
    app = located.path.expanduser().resolve()
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
    running = discovery.running(executable)
    if running is None:
        raise RuntimeError("Cannot tell whether Painter is already running (pgrep failed)")
    if running:
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
        "reason": (
            "Painter launch requested; inspect status after startup or license prompts finish"
        ),
    }
