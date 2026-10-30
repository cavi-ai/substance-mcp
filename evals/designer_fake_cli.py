"""Explicitly fake Designer CLIs for CI; mirrors observed 16.0.7 behaviour."""

import json
import os
import sys
import time
from pathlib import Path

TOOLS = ("sbscooker", "sbsrender", "substance3d_baker", "sbsupdater")
COMMON = ("--inputs", "--output_path", "--output_name", "--output_size", "--output_format",
          "--udim", "--name_suffix_low", "--name_suffix_high")
PROJECTION = ("--high_scene_paths", "--use_lowdef_as_highdef", "--use_cage", "--cage_scene_path",
              "--projection.max_height", "--projection.max_depth",
              "--projection.mesh_match_mode")
ORIENTED = ("--output_texture_orientation",)
BAKERS = {
    "AmbientOcclusion.Raytraced": PROJECTION, "Color.Raytraced": PROJECTION,
    "Curvature.Raytraced": PROJECTION, "Height.Raytraced": PROJECTION,
    "Normal.BentNormals": PROJECTION + ORIENTED, "Normal.Raytraced": PROJECTION + ORIENTED,
    "Normal.TangentToWorld": (), "Opacity.Raytraced": PROJECTION,
    "Position.Raytraced": PROJECTION, "Thickness.Raytraced": PROJECTION,
}
GRAY_BAKERS = {"AmbientOcclusion.Raytraced", "Curvature.Raytraced", "Height.Raytraced",
               "Opacity.Raytraced", "Thickness.Raytraced"}
INPUTS = [["$outputsize", "INTEGER2"], ["$randomseed", "INTEGER1"],
          ["roughness_level", "FLOAT1"], ["tint", "FLOAT3"], ["tiling", "INTEGER1"]]
OUTPUTS = [["basecolor", "baseColor"], ["normal", "normal"], ["roughness", "roughness"],
           ["metallic", "metallic"], ["ambientocclusion", "ambientOcclusion"],
           ["height", "height"], ["mask", ""]]
PIXELS = {"baseColor": (204, 102, 51), "normal": (128, 128, 255), "roughness": 64,
          "metallic": 255, "ambientOcclusion": 200, "height": 128, "": 10}


def sbsar(graphs, flags=()):
    return json.dumps({"fake_sbsar": 1, "flags": list(flags), "graphs": [
        {"url": url, "inputs": INPUTS, "outputs": OUTPUTS} for url in graphs]})


def install(root: Path) -> Path:
    app = Path(root) / "Fake Designer.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    for kind, names in {"templates": ["01_02_pbr_metallic_roughness"],
                        "packages": ["brick_1"]}.items():
        (app / "Contents/Resources" / kind).mkdir(parents=True)
        for name in names:
            (app / "Contents/Resources" / kind / f"{name}.sbs").write_text("fake-sbs")
    for tool in TOOLS:
        script = macos / tool
        script.write_text(
            f"#!{sys.executable}\nimport runpy\n"
            f"runpy.run_path({str(Path(__file__).resolve())!r}, "
            f"init_globals={{'FAKE_TOOL': {tool!r}}}, run_name='__main__')\n")
        script.chmod(0o755)
    return app


def options(argv):
    pairs, index = [], 0
    while index < len(argv):
        key = argv[index]
        if key in ("-q", "-v"):
            index += 1
            continue
        if index + 1 >= len(argv):
            fail(f"Missing value for {key}", 1)
        pairs.append((key, argv[index + 1]))
        index += 2
    return pairs


def fail(message, code):
    print(f"[ERROR][Fake]{message}", file=sys.stderr)
    raise SystemExit(code)


def log(tool, argv):
    if os.environ.get("CAVI_FAKE_DESIGNER_LOG"):
        with open(os.environ["CAVI_FAKE_DESIGNER_LOG"], "a") as stream:
            stream.write(json.dumps([tool, *argv]) + "\n")


def image(path, size, value):
    from PIL import Image

    path = Path(path)
    if path.suffix == ".exr":
        path.write_bytes(b"v/1\x01fake-exr")
        return
    mode = "RGB" if isinstance(value, tuple) else "L"
    Image.new(mode, (size, size), value).save(path)


def cooker(argv):
    args = dict(options(argv))
    source, dest = Path(args.get("--inputs", "")), Path(args.get("--output-path", ""))
    if not source.is_file():
        fail(f"Can't open {source}", 4)
    text = source.read_text(errors="replace")
    if "fake-cook-fail" in text:
        fail("Cooking failed", 4)
    if not dest.is_dir():
        fail(f"I/O Error: can't write file '{dest / source.stem}.sbsar'", 4)
    if "fake-cook-silent" in text:
        return
    graphs = ["pkg://metallic_roughness", "pkg://metallic_roughness_sheen"]
    flags = [f"{k}={v}" for k, v in args.items() if k.startswith("--expose")]
    (dest / f"{source.stem}.sbsar").write_text(sbsar(graphs, flags))


def load_sbsar(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        fail(f"Can't load {path}", 1)


def render(argv):
    if argv and argv[0] == "info":
        data = load_sbsar(argv[-1])
        for graph in data["graphs"]:
            print(f"GRAPH-URL {graph['url']}")
            for ident, kind in graph["inputs"]:
                print(f"  INPUT {ident} {kind}")
            for ident, usage in graph["outputs"]:
                print(f"  OUTPUT {ident} {usage}")
            print()
        return
    if not argv or argv[0] != "render":
        fail("Unknown command", 1)
    pairs = options(argv[1:])
    args = dict(pairs)
    data = load_sbsar(args.get("--input", ""))
    if data.get("mode") == "render_fail":
        fail("Engine failure", 3)
    if data.get("mode") == "render_hang":
        time.sleep(60)
    size = 256
    for key, value in pairs:
        if key == "--set-value":
            ident, _, raw = value.partition("@")
            try:
                numbers = [float(part) for part in raw.split(",")]
            except ValueError:
                raise SystemExit(47) from None
            if ident == "$outputsize":
                size = 2 ** int(numbers[0])
    graphs = data["graphs"]
    if "--input-graph" in args:
        graphs = [g for g in graphs if g["url"] == args["--input-graph"]]
        if not graphs:
            fail(f"Cannot find graph \"{args['--input-graph']}\"", 1)
    wanted = [value for key, value in pairs if key == "--input-graph-output"]
    dest = Path(args.get("--output-path", "."))
    fmt = args.get("--output-format", "png")
    report = []
    for graph in graphs:
        outputs = []
        for ident, usage in graph["outputs"]:
            if wanted and ident not in wanted:
                continue
            name = args.get("--output-name", "{outputNodeName}").replace(
                "{outputNodeName}", ident)
            target = dest / f"{name}.{fmt}"
            image(target, size, PIXELS[usage])
            outputs.append({"identifier": ident, "label": ident.title(), "type": "image",
                            "uid": 1, "usages": [usage] if usage else [],
                            "value": str(target.resolve())})
        if not outputs:
            fail("No graph or outputs are found from the given values to compute", 1)
        report.append({"identifier": graph["url"][6:], "outputs": outputs})
    print("[2026-01-01 00:00:00.000] [info] [fake.cpp:1] Selected device: Fake.")
    if data.get("mode") != "render_noreport":
        print(json.dumps(report, indent=4))
    print("[2026-01-01 00:00:00.001] [info] [fake.cpp:2] Done.")


def baker(argv):
    if not argv or argv[0] not in BAKERS:
        fail(f"Unknown command '{argv[0] if argv else ''}'", 1)
    command = argv[0]
    pairs = options(argv[1:])
    allowed = COMMON + BAKERS[command]
    for key, _ in pairs:
        if key not in allowed:
            fail(f"Unknown option '{key[2:]}'.", 1)
    args = dict(pairs)
    low = Path(args.get("--inputs", ""))
    if not low.is_file():
        return
    text = low.read_text(errors="replace")
    if "fake-bake-silent" in text:
        return
    if "fake-bake-hang" in text:
        time.sleep(60)
    width = int(args.get("--output_size", "2048,2048").split(",")[0])
    target = Path(args["--output_path"]) / f"{args['--output_name']}.{args['--output_format']}"
    image(target, width, 230 if command in GRAY_BAKERS else (128, 128, 255))


def main():
    tool = globals()["FAKE_TOOL"]
    argv = sys.argv[1:]
    log(tool, argv)
    if "--version" in argv:
        print("version 0.0.0-fake (build 0 commit 0x0)")
        return
    {"sbscooker": cooker, "sbsrender": render, "substance3d_baker": baker,
     "sbsupdater": lambda _: None}[tool](argv)


if __name__ == "__main__":
    main()
