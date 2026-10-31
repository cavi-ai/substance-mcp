"""Designer CLI operations confined to the workspace."""

import asyncio
import math
import re
import time
from pathlib import Path

from .. import packing, receipts
from ..core import proc, workspace
from . import runner

GRAPH = re.compile(r"^pkg://[A-Za-z0-9_./-]{1,128}$")
PRESET = re.compile(r"^[^@\-\x00-\x1f\x7f][^@\x00-\x1f\x7f]{0,127}$")
BUILTIN = re.compile(r"^(templates|packages)/([A-Za-z0-9_-]{1,96})$")
MESHES = {".fbx", ".obj", ".ply", ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz", ".abc"}
RENDER_DEPTHS = {"png": {"8", "16"}, "tga": {"8"}, "jpg": {"8"}, "tif": {"8", "16", "16f", "32f"},
                 "exr": {"16f", "32f"}}
RESERVED = {"$outputsize", "$randomseed"}
BAKERS = {
    "normal": "Normal.Raytraced",
    "ambient_occlusion": "AmbientOcclusion.Raytraced",
    "curvature": "Curvature.Raytraced",
    "thickness": "Thickness.Raytraced",
    "position": "Position.Raytraced",
    "world_normal": "Normal.TangentToWorld",
    "bent_normal": "Normal.BentNormals",
    "id": "Color.Raytraced",
    "height": "Height.Raytraced",
    "opacity": "Opacity.Raytraced",
}
ORIENTED = {"normal", "bent_normal"}
LOW_ONLY = {"world_normal"}


def rel(path: Path) -> str:
    return workspace.rel(workspace.root(), path)


def new_directory(name: str):
    """A context manager that makes the new output directory and removes it on failure."""
    return workspace.new_output_directory(workspace.root(create=True), name)


def source(name: str, extensions: set[str]) -> Path:
    return workspace.input_file(workspace.root(), name, extensions)


async def status() -> dict:
    install = runner.installation()
    paths = {name: install.binary(name) for name in runner.TOOLS}
    versions = await asyncio.gather(*(runner.version(path) for path in paths.values()))
    resources = install.resources
    packages = resources / "packages" if resources else None
    templates = resources / "templates" if resources else None
    root = workspace.root()
    return {
        "available": all(paths[name] for name in ("sbscooker", "sbsrender", "substance3d_baker")),
        "source": install.source,
        "bin_dir": str(install.bin_dir),
        "binaries": {name: {"path": str(path) if path else None, "version": found}
                     for (name, path), found in zip(paths.items(), versions, strict=True)},
        "packages_dir": str(packages) if packages else None,
        "package_count": len(list(packages.glob("*.sbs"))) if packages else 0,
        "templates": sorted(p.stem for p in templates.glob("*.sbs")) if templates else [],
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
    }


async def graphs_of(sbsar: Path, timeout: float) -> list[dict]:
    tool = runner.installation().require("sbsrender")
    result = await proc.run([tool, "info", sbsar], timeout)
    if result.returncode:
        raise RuntimeError(runner.failure(result, "sbsrender info"))
    graphs = runner.parse_info(runner.parsed_stdout(result, "sbsrender info"))
    if not graphs:
        raise RuntimeError("sbsrender info listed no graphs")
    return graphs


async def info(sbsar: str, timeout: float) -> dict:
    path = source(sbsar, {".sbsar"})
    return {"sbsar": rel(path), "graphs": await graphs_of(path, timeout), "presets": None}


def builtin_file(name: str) -> Path:
    match = BUILTIN.fullmatch(name)
    if not match:
        raise ValueError("builtin must look like templates/<name> or packages/<name>")
    resources = runner.installation().resources
    if resources is None:
        raise RuntimeError("Designer Resources directory not found")
    folder = (resources / match.group(1)).resolve()
    path = (folder / f"{match.group(2)}.sbs").resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise ValueError(f"Built-in {name} not found")
    return path


async def cook(path, builtin, directory, expose_output_size, expose_random_seed, timeout):
    if (path is None) == (builtin is None):
        raise ValueError("cook needs exactly one of path or builtin")
    sbs = builtin_file(builtin) if builtin else source(path, {".sbs"})
    tool = runner.installation().require("sbscooker")
    with new_directory(directory) as dest:
        started = time.monotonic()
        result = await proc.run([
            tool, "--inputs", sbs, "--output-path", dest,
            "--expose-output-size", "1" if expose_output_size else "0",
            "--expose-random-seed", "1" if expose_random_seed else "0",
        ], timeout)
        sbsar = dest / f"{sbs.stem}.sbsar"
        if result.returncode:
            raise RuntimeError(runner.failure(result, "sbscooker"))
        if not sbsar.is_file() or sbsar.stat().st_size == 0:
            raise RuntimeError("sbscooker exited 0 but wrote no .sbsar")
        graphs = await graphs_of(sbsar, timeout)
        version = await runner.version(tool)
        # A built-in template lives in the Designer install, not the workspace: record
        # its name and hash instead of an absolute path that breaks on other machines.
        manifest_path = receipts.write_manifest(
            dest, "designer", "cook", workspace.root(),
            inputs=[] if builtin else [sbs], outputs=[sbsar],
            external=[receipts.app_resource(builtin, sbs, version)] if builtin else [],
            parameters={"source": builtin or rel(sbs),
                        "expose_output_size": expose_output_size,
                        "expose_random_seed": expose_random_seed},
            seconds=time.monotonic() - started, version=version,
        )
    return {"sbsar": rel(sbsar), "source": builtin or rel(sbs), "graphs": graphs,
            "manifest": rel(manifest_path)}


def _number(value, integer: bool) -> str:
    if integer:
        if type(value) is not int:
            raise ValueError("Integer input expects integers")
        return str(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Values must be finite numbers")
    return repr(float(value))


def value_arg(spec: dict, value) -> str:
    match = re.fullmatch(r"(FLOAT|INTEGER)([1-4])", spec["type"])
    if not match:
        raise ValueError(f"Input {spec['id']} has unsupported type {spec['type']}")
    items = value if isinstance(value, list) else [value]
    if len(items) != int(match.group(2)):
        raise ValueError(f"Input {spec['id']} expects {match.group(2)} value(s)")
    joined = ",".join(_number(item, match.group(1) == "INTEGER") for item in items)
    return f"{spec['id']}@{joined}"


def pick_graph(graphs: list[dict], graph: str | None) -> dict:
    if graph is None:
        if len(graphs) != 1:
            raise ValueError("graph is required; sbsar has: "
                             + ", ".join(g["url"] for g in graphs[:32]))
        return graphs[0]
    if not GRAPH.fullmatch(graph):
        raise ValueError("graph must match pkg://name")
    for item in graphs:
        if item["url"] == graph:
            return item
    raise ValueError(f"Graph {graph} not in sbsar; available: "
                     + ", ".join(g["url"] for g in graphs[:32]))


async def render(sbsar, graph, directory, values, resolution, seed, preset, outputs, fmt,
                 bit_depth, spec, timeout):
    path = source(sbsar, {".sbsar"})
    if bit_depth not in RENDER_DEPTHS[fmt]:
        raise ValueError(f"bit_depth {bit_depth} unsupported for {fmt}")
    if spec is not None:
        packing.check_spec(spec)
        if fmt not in ("png", "tga", "tif") or bit_depth not in ("8", "16"):
            raise ValueError("pack needs png, tga or tif renders at bit_depth 8 or 16")
    if preset is not None and not PRESET.fullmatch(preset):
        raise ValueError("preset must be printable, without '@' or a leading '-'")
    selected = pick_graph(await graphs_of(path, timeout), graph)
    inputs = {item["id"]: item for item in selected["inputs"]}
    output_ids = {item["id"] for item in selected["outputs"]}
    args = ["render", "--input", path, "--input-graph", selected["url"],
            "--output-name", "{outputNodeName}", "--output-format", fmt,
            "--output-bit-depth", bit_depth]
    for key, value in (values or {}).items():
        if key in RESERVED:
            raise ValueError(f"Use resolution/seed instead of {key}")
        if key not in inputs:
            raise ValueError(f"Unknown input {key}; graph inputs: "
                             + ", ".join(sorted(set(inputs) - RESERVED)[:64]))
        args += ["--set-value", value_arg(inputs[key], value)]
    if resolution is not None:
        if "$outputsize" not in inputs:
            raise ValueError("Graph does not expose $outputsize")
        exponent = resolution.bit_length() - 1
        args += ["--set-value", f"$outputsize@{exponent},{exponent}"]
    if seed is not None:
        if "$randomseed" not in inputs:
            raise ValueError("Graph does not expose $randomseed")
        args += ["--set-value", f"$randomseed@{seed}"]
    if preset is not None:
        args += ["--use-preset", preset]
    for output in outputs or []:
        if output not in output_ids:
            raise ValueError(f"Unknown output {output}; graph outputs: "
                             + ", ".join(sorted(output_ids)[:64]))
        args += ["--input-graph-output", output]
    tool = runner.installation().require("sbsrender")
    with new_directory(directory) as dest:
        started = time.monotonic()
        result = await proc.run([tool, *args, "--output-path", dest], timeout)
        if result.returncode:
            raise RuntimeError(runner.failure(result, "sbsrender render"))
        report = runner.parse_report(runner.parsed_stdout(result, "sbsrender render"))
        produced, maps = [], {}
        for entry in report:
            for output in entry.get("outputs", []) if isinstance(entry, dict) else []:
                file = Path(str(output.get("value", ""))).resolve()
                if not file.is_relative_to(dest) or not file.is_file() or not file.stat().st_size:
                    raise RuntimeError(f"Output {output.get('identifier')} missing or empty")
                usages = [u for u in output.get("usages", []) if isinstance(u, str) and u]
                produced.append({"id": output.get("identifier"), "label": output.get("label"),
                                 "usages": usages, "file": rel(file)})
                for key in usages or [output.get("identifier")]:
                    maps.setdefault(key, file)
        if not produced:
            raise RuntimeError("sbsrender reported no outputs")
        reply = {"directory": rel(dest), "graph": selected["url"],
                 "maps": {key: rel(file) for key, file in maps.items()}, "outputs": produced,
                 "report": report}
        packed_paths = []
        if spec is not None:
            (dest / "packed").mkdir()
            packed = await asyncio.to_thread(packing.pack, spec, maps, dest / "packed")
            packed_paths = [item["path"] for item in packed["maps"]]
            reply["packed"] = _packed(packed)
        manifest_path = receipts.write_manifest(
            dest, "designer", "render", workspace.root(), inputs=[path],
            outputs=[*maps.values(), *packed_paths],
            parameters={"graph": selected["url"], "values": values,
                        "resolution": resolution, "seed": seed, "preset": preset,
                        "outputs": outputs, "format": fmt, "bit_depth": bit_depth},
            seconds=time.monotonic() - started, version=await runner.version(tool),
        )
        reply["manifest"] = rel(manifest_path)
    reply["seconds"] = round(time.monotonic() - started, 3)
    return reply


def _packed(result: dict) -> list[dict]:
    return [{**item, "path": rel(item["path"])} for item in result["maps"]]


async def pack(sources: dict[str, str], spec, directory: str) -> dict:
    if not sources:
        raise ValueError("pack needs sources: {usage: workspace image}")
    files = {}
    for usage, name in sources.items():
        if not re.fullmatch(packing.USAGE, usage):
            raise ValueError(f"Invalid usage name: {usage}")
        files[usage] = source(name, packing.READABLE)
    packing.check_spec(spec)
    with new_directory(directory) as dest:
        started = time.monotonic()
        result = await asyncio.to_thread(packing.pack, spec, files, dest)
        manifest_path = receipts.write_manifest(
            dest, "designer", "pack", workspace.root(), inputs=list(files.values()),
            outputs=[item["path"] for item in result["maps"]],
            parameters={"pack": spec.model_dump(mode="json")},
            seconds=time.monotonic() - started,
        )
    return {"directory": rel(dest), "packed": _packed(result), "sources": result["sources"],
            "manifest": rel(manifest_path)}


def bake_args(name, low, high, cage, dest, options) -> list:
    command = BAKERS[name]
    args = [command, "--inputs", low, "--output_path", dest, "--output_name", name,
            "--output_size", f"{options['resolution']},{options['resolution']}",
            "--output_format", options["format"]]
    if options.get("udim") is not None:
        args += ["--udim", str(options["udim"])]
    if name not in LOW_ONLY:
        args += ["--high_scene_paths", high] if high else ["--use_lowdef_as_highdef", "true"]
        if options.get("use_cage"):
            args += ["--use_cage", "true", "--cage_scene_path", cage]
        else:
            if options.get("max_frontal") is not None:
                args += ["--projection.max_height", repr(float(options["max_frontal"]))]
            if options.get("max_rear") is not None:
                args += ["--projection.max_depth", repr(float(options["max_rear"]))]
        if options.get("match_by_name"):
            args += ["--projection.mesh_match_mode", "match_mesh_name",
                     "--name_suffix_low", options["suffix_low"],
                     "--name_suffix_high", options["suffix_high"]]
    if name in ORIENTED:
        args += ["--output_texture_orientation", options["normal_format"].lower()]
    return args


async def bake(low, high, cage, maps, directory, options, timeout):
    if len(set(maps)) != len(maps):
        raise ValueError("maps must be unique")
    if options.get("use_cage") and cage is None:
        raise ValueError("use_cage needs cage")
    if cage is not None and not options.get("use_cage"):
        raise ValueError("cage is only used with use_cage=true")
    low_path = source(low, MESHES)
    high_path = source(high, MESHES) if high else None
    cage_path = source(cage, MESHES) if cage else None
    tool = runner.installation().require("substance3d_baker")
    files, timings = {}, {}
    with new_directory(directory) as dest:
        started_at = time.monotonic()
        for name in maps:
            started = time.monotonic()
            args = bake_args(name, low_path, high_path, cage_path, dest, options)
            result = await proc.run([tool, *args], timeout, cwd=dest)
            if result.returncode:
                raise RuntimeError(runner.failure(result, f"{BAKERS[name]}"))
            target = dest / f"{name}.{options['format']}"
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError(f"{BAKERS[name]} exited 0 but wrote no {target.name}: "
                                   + proc.diagnostics(result))
            files[name] = rel(target)
            timings[name] = round(time.monotonic() - started, 3)
        manifest_path = receipts.write_manifest(
            dest, "designer", "bake", workspace.root(),
            inputs=[path for path in (low_path, high_path, cage_path) if path is not None],
            outputs=[dest / f"{name}.{options['format']}" for name in maps],
            parameters=options, seconds=time.monotonic() - started_at,
            version=await runner.version(tool),
        )
    return {"directory": rel(dest), "maps": files,
            "commands": {name: BAKERS[name] for name in maps}, "seconds": timings,
            "high_used": {name: bool(high_path) and name not in LOW_ONLY for name in maps},
            "manifest": rel(manifest_path)}
