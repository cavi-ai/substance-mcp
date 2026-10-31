"""Sampler batch jobs: photo/scan to PBR maps through the fixed worker script."""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .. import packing, receipts
from ..painter_operations import remove_tree
from ..sampler import runner
from .operations import new_directory, rel, source, workspace

WORKER = Path(__file__).with_name("sampler_worker.py")
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr"}
COUNTS = {"ai": (1, 1), "b2m": (1, 1), "multiangle": (2, 8)}
PACKABLE = {"png", "tif"}
USAGES = {"BaseColor": "baseColor", "Normal": "normal", "Roughness": "roughness",
          "Metallic": "metallic", "Height": "height", "AmbientOcclusion": "ambientOcclusion",
          "Opacity": "opacity", "SpecularLevel": "specularLevel"}
CHANNEL = re.compile(r"^sampler_([A-Za-z][A-Za-z0-9]{0,63})$")
RESULT_LIMIT = 1024 * 1024


def running() -> bool | None:
    executable = runner.installation().executable
    pgrep = shutil.which("pgrep")
    if executable is None or pgrep is None:
        return None
    try:
        # whole argument only: Adobe's licensing client passes --appPath=<executable>
        pattern = f"(^|[[:space:]]){re.escape(str(executable))}([[:space:]]|$)"
        found = subprocess.run([pgrep, "-f", pattern],
                               capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return found.returncode == 0


async def status() -> dict:
    found = runner.installation()
    return {"path": str(found.executable) if found.executable else None,
            "version": found.version, "running": await asyncio.to_thread(running)}


def usage(stem: str) -> str | None:
    match = CHANNEL.fullmatch(stem)
    if not match:
        return None
    name = match.group(1)
    return USAGES.get(name, name[0].lower() + name[1:])


def collect(dest: Path, fmt: str) -> dict[str, Path]:
    maps = {}
    for item in sorted(dest.glob(f"sampler_*.{fmt}")):
        key = usage(item.stem)
        if key is None or item.is_symlink() or not item.is_file() or not item.stat().st_size:
            raise RuntimeError(f"Sampler wrote an unexpected or empty file: {item.name}")
        target = dest / f"{key}.{fmt}"
        if target.exists():
            raise RuntimeError(f"Duplicate channel {key}")
        item.rename(target)
        maps[key] = target
    if not maps:
        raise RuntimeError("Sampler exported no material maps")
    return maps


async def launch(job: dict, scratch: Path, timeout: float) -> dict:
    job_file, result = scratch / "job.json", scratch / "result.json"
    job_file.write_text(json.dumps({**job, "result": str(result)}), encoding="utf-8")
    outcome = await runner.run_script(
        WORKER, timeout, scratch, ("--ignore-gpu-warning", "-S", str(job_file)))
    if not result.is_file():
        raise RuntimeError("Sampler did not run the worker: " + runner.diagnostics(outcome))
    if result.stat().st_size > RESULT_LIMIT:
        raise RuntimeError("Sampler result exceeds size limit")
    try:
        reply = json.loads(result.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError("Sampler wrote an unreadable result") from exc
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        detail = str(reply.get("error") if isinstance(reply, dict) else reply)
        if detail.startswith("ValueError: "):
            raise ValueError(detail.removeprefix("ValueError: "))
        raise RuntimeError(f"Sampler failed: {detail}")
    return reply


async def run(action: str, values: dict, timeout: float) -> dict:
    fmt, spec = values["format"], values.get("pack")
    if spec is not None:
        packing.check_spec(spec)
        if fmt not in PACKABLE:
            raise ValueError("pack needs format png or tif")
    job = {"action": action, "format": fmt, "resolution": values["resolution"]}
    if action == "image_to_material":
        images, mode = values["images"], values["mode"]
        low, high = COUNTS[mode]
        if not low <= len(images) <= high:
            raise ValueError(f"mode={mode} takes {low}-{high} image(s)")
        if len(set(images)) != len(images):
            raise ValueError("images must be unique")
        paths = [source(name, IMAGES) for name in images]
        job.update(images=[str(path) for path in paths], mode=mode, tiling=values["tiling"],
                   delight=values["delight"], name=re.sub(r"[^A-Za-z0-9_-]", "_",
                                                          paths[0].stem)[:64] or "material")
        if values.get("save_project"):
            job["save_as"] = job["name"] + ".ssa"
    else:
        job.update(project=str(source(values["project"], {".ssa"})), asset=values.get("asset"))
    if running():
        raise RuntimeError("Sampler is already running; save and quit it, then retry")
    runner.installation().require()
    scratch = Path(tempfile.mkdtemp(prefix="cavi-sampler-"))
    job["scratch"] = str(scratch / "scratch.ssa")
    try:
        dest = new_directory(values["directory"])
    except BaseException:
        remove_tree(scratch)
        raise
    job["output"] = str(dest)
    if job.get("save_as"):
        job["save_as"] = str(dest / job["save_as"])
    started = time.monotonic()
    try:
        reply = await launch(job, scratch, timeout)
        maps = collect(dest, fmt)
        out = {"directory": rel(dest), "maps": {key: rel(path) for key, path in maps.items()},
               "layers": reply.get("layers"), "asset": reply.get("asset"), "project": None,
               "packed": None}
        if job.get("save_as"):
            saved = Path(job["save_as"])
            if not saved.is_file() or not saved.stat().st_size:
                raise RuntimeError("Sampler did not save the project")
            out["project"] = rel(saved)
        if spec is not None:
            (dest / "packed").mkdir()
            packed = await asyncio.to_thread(packing.pack, spec, maps, dest / "packed")
            out["packed"] = [{**item, "path": rel(item["path"])} for item in packed["maps"]]
        inputs = ([Path(item) for item in job.get("images", [])]
                  if action == "image_to_material" else [Path(job["project"])])
        outputs = list(maps.values())
        if job.get("save_as"):
            outputs.append(Path(job["save_as"]))
        if spec is not None:
            outputs.extend(item["path"] for item in packed["maps"])
        manifest = receipts.build(
            "sampler", action, workspace(), inputs=inputs, outputs=outputs,
            parameters={"format": fmt, "resolution": values["resolution"]},
            seconds=time.monotonic() - started,
            version=runner.installation().version,
        )
        manifest_path = receipts.write(dest, manifest)
        out["manifest"] = rel(manifest_path)
    except BaseException:
        remove_tree(dest)
        raise
    finally:
        remove_tree(scratch)
    out["seconds"] = round(time.monotonic() - started, 3)
    return out

