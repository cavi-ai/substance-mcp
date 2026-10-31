"""Run deterministic Designer evaluations over the real stdio MCP surface."""

import asyncio
import json
from pathlib import Path

import common

APP = "substance-designer"
SERVER = ["-m", "cavi_substance_mcp.designer.server"]


def fixtures(output: Path) -> Path:
    from designer_fake_cli import sbsar
    from PIL import Image

    workspace = common.make_workspace(output, ["maps", "meshes", "existing"])
    outside = output / "outside"
    (outside / "outside.sbsar").write_text(sbsar(["pkg://single"]))
    (outside / "low.fbx").write_text("outside-mesh")
    (workspace / "single.sbsar").write_text(sbsar(["pkg://single"]))
    (workspace / "multi.sbsar").write_text(
        sbsar(["pkg://metallic_roughness", "pkg://metallic_roughness_sheen"]))
    for name, mode in {"broken": "render_fail", "noreport": "render_noreport"}.items():
        data = json.loads(sbsar(["pkg://single"]))
        (workspace / f"{name}.sbsar").write_text(json.dumps({**data, "mode": mode}))
    (workspace / "source.sbs").write_text("fake-sbs")
    (workspace / "silent.sbs").write_text("fake-cook-silent")
    for name, value in {"basecolor": (204, 102, 51), "roughness": 64, "metallic": 255,
                        "ao": 200}.items():
        Image.new("RGB" if isinstance(value, tuple) else "L", (32, 32), value).save(
            workspace / f"maps/{name}.png")
    Image.new("L", (16, 16), 1).save(workspace / "maps/small.png")
    for name, text in {"low": "low", "high": "high", "silent": "fake-bake-silent",
                       "hang": "fake-bake-hang"}.items():
        (workspace / f"meshes/{name}.fbx").write_text(text)
    return workspace


def main():
    mode, evaluator, output = common.start("designer-")
    workspace = fixtures(output)
    env = common.environment(
        output, workspace, drop=("CAVI_DESIGNER_APP", "CAVI_DESIGNER_BIN_DIR"))
    if mode == "simulated":
        from designer_fake_cli import ENV, install

        env[ENV] = str(install(output / "app"))
    else:
        (output / "no-designer").mkdir()
        env["CAVI_DESIGNER_BIN_DIR"] = str(output / "no-designer")
    results = asyncio.run(common.exercise("designer_cases.json", mode, SERVER, env, workspace))
    common.finish(
        mode=mode, evaluator=evaluator, output=output, results=results, env=env, app=APP,
        server=SERVER, manifest_prefix="designer.",
        native_status="fake CLIs or no CLIs; run tests/test_designer_native.py for native",
    )


if __name__ == "__main__":
    main()
