# Designer tools

`cavi-substance-designer-mcp` is a stdio server for Substance 3D Designer's headless
CLIs (`sbscooker`, `sbsrender`, `substance3d_baker`). No Designer UI or lease.

```json
{"mcpServers": {"substance-designer": {"command": "cavi-substance-designer-mcp"}}}
```

- Binaries: `CAVI_DESIGNER_BIN_DIR`, else `CAVI_DESIGNER_APP` (`.app` path), else the
  standard install dir for macOS, Windows or Linux, else `sbsrender` on `PATH`. An
  override is final: a wrong one is reported as not found, not replaced.
- Paths are relative to `CAVI_SUBSTANCE_WORKSPACE`; outputs go to new directories only.
  Failed calls remove the directory they created.

| Tool | Actions / inputs |
| --- | --- |
| `designer_status` | binaries, versions, packages dir, templates, workspace |
| `material` | `info` sbsar graphs; `cook` `.sbs` or `builtin` (`templates/<name>`, `packages/<name>`); `render` graph + `values`, `resolution`, `seed`, `preset`, `outputs`, `format`, `bit_depth`, optional `pack`; `pack` `sources` + `pack` |
| `bake` | `low`, optional `high`/`cage`, `maps`, `resolution`, `normal_format`, distances, `udim`, name matching |

## Bake maps

`normal` Normal.Raytraced, `ambient_occlusion` AmbientOcclusion.Raytraced,
`curvature` Curvature.Raytraced, `thickness` Thickness.Raytraced, `position`
Position.Raytraced, `world_normal` Normal.TangentToWorld (low mesh only), `bent_normal`
Normal.BentNormals, `id` Color.Raytraced, `height` Height.Raytraced, `opacity`
Opacity.Raytraced. One baker run per map; each output file is verified.

`material pack` uses the shared [channel packing](../guides/packing.md) spec.

## Limitations

- Presets are not listed (`sbsrender info` does not expose them) and an unknown preset
  name is ignored by `sbsrender`.
- 16-bit RGB/RGBA sources are read at 8-bit precision; 16-bit grayscale is exact.
- The evals run against fake CLIs (`evals/designer_fake_cli.py`); native tests need
  Designer and, for baking, Blender. See [Testing and evaluation](../reference/testing.md).
