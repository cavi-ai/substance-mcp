# Designer tools

`cavi-substance-designer-mcp` is a stdio server for Substance 3D Designer's headless
CLIs (`sbscooker`, `sbsrender`, `substance3d_baker`) and native SDK graph authoring.

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
| `graph` | `create`, `edit`, `inspect`, `catalog`; new output `directory`, optional source `path`, structured `recipe` |
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
- PNG16 color and grayscale samples retain their precision during packing.
- The evals run against fake CLIs (`evals/designer_fake_cli.py`); native tests need
  Designer and, for baking, Blender. See [Testing and evaluation](../reference/testing.md).

## Response contracts and recovery

Status tools publish declared fields; additional existing details are retained.
`designer_status` declares `packages_dir`, `package_count` and `templates` in its
response schema. Without installed resources these are `null`, `0` and `[]`.
Anticipated failures preserve readable text and add versioned error details with
code, operation, outcome, retry_safe and next_action. Transport/protocol failures
retain an unknown outcome even when caused by JSON decoding. Inspect the
application and outputs before deciding whether another mutation is appropriate.
Unexpected defects remain opaque.

## Editable graph authoring

`graph` runs Designer with its supported startup-script interface. Each request
uses an owned batch instance and writes into a new workspace directory. It
preserves an already-running editor by refusing to launch over it. Mutations
save a new `.sbs` package; the input package is checked for changes afterward.

`create` accepts a graph `identifier`, `resolution`, `precision`, `seed` and
`operations`. New graphs default to 16-bit float. `edit` preserves source
precision, size and seed unless the recipe explicitly changes them. Operations
include `add_node`, `instance`, `set`, `connect`, `disconnect`, `remove_node`,
`expose`, `set_input` and `output`. Property values use their native SDK types;
texture inputs are connected through their ports.

Use `catalog` to discover native definition IDs. An exact definition query also
returns input/output property types, defaults and connectability. `path` may name
an installed library such as `builtin:packages/noise_clouds_3_v2`; the response
includes resource URLs suitable for `instance`. `inspect` reopens a saved package
and reports nodes, connections, graph inputs and actual precision. Creation
returns `node_ids` so later edits can address the saved native identifiers.

For example, pass this recipe to `graph(action="create", directory=...)`:

```json
{
  "identifier": "PaintedMetal",
  "precision": "16f",
  "operations": [
    {"action": "add_node", "key": "paint", "definition": "sbs::compositing::uniform",
     "values": {"outputcolor": [0.1, 0.3, 0.6, 1.0]}},
    {"action": "expose", "node": "paint", "property": "outputcolor", "identifier": "paintColor"},
    {"action": "output", "key": "base", "source": "paint",
     "source_port": "unique_filter_output", "identifier": "BaseColor",
     "usage": "baseColor", "color_space": "sRGB"}
  ]
}
```

Feed the returned `package` to `material(action="cook")`, then render the
archive. Rendering defaults to 16-bit output; EXR supports explicit `16f` or
`32f`. Graph authoring records source/output hashes and built-in dependencies.
Native tests verify changed parameter outputs, saved wiring, library instances
and preservation of 32-bit float graph precision through editing.
