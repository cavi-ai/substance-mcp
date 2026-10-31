# Source projects and authoring

## Source projects

A job can open an existing `.ssa` by copying it into the new job directory before
opening it. The source file is never opened by Sampler and stays byte-identical.

```json
{
  "schema": 1,
  "directory": "jobs/rock-variants",
  "project": {
    "source": "projects/Rock.ssa",
    "name": "RockCopy",
    "assets": ["Rock"]
  },
  "steps": [
    {"action": "set_metadata", "asset": "Rock", "author": "Cavi",
     "category": "rock", "tags": ["game", "pbr"]},
    {"action": "export", "asset": "Rock", "target": "unity", "directory": "unity"},
    {"action": "export", "asset": "Rock", "target": "blender", "directory": "blender"}
  ]
}
```

- `name` defaults to the source stem; the copy is written into the job directory.
- `assets` is optional. When present, step asset references are validated before
  launch; when absent, runtime remains authoritative.
- `validate --check-project` verifies the source copy's assets, layers, parameters,
  presets, filters, materials, channels and blend parameters without mutating the
  source. Use it for source jobs; `--check-sampler` alone defers project-local
  filter/material checks to this pass.
- Source and project copy hashes are recorded in the manifest.
- `project.asset` cannot be combined with `source`; use `create_asset` steps.

## Authoring depth

```json
{"action": "set_metadata", "asset": "Rock", "description": "weathered granite",
 "physical_size": [1.0, 2.0, 3.0], "custom_metadata": {"engine": "unity"}}
{"action": "set_channels", "asset": "Rock", "layer": "Tiling",
 "applies_to": ["baseColor"], "blend_applies_to": ["baseColor"]}
{"action": "set_blend_parameters", "asset": "Rock", "layer": "Concrete Cast",
 "parameters": {"blend_top_material_scale": 2}}
```

- `sampler_inspect asset` reports `enum_values` and `value_labels`; integer and enum
  parameters can be set by label as well as by number.
- `applies_to` and `blend_applies_to` accept canonical channel IDs and unambiguous
  labels from the inspected layer (`applied_channels`/`output_usages`/`usage_labels`).
  Unknown or ambiguous names are rejected.
- Blend mode is read-only after insertion. Choose it with `insert_material`; the
  authoring steps cover applies-to channels and blend parameters. Inspect a layer's
  `blend_parameters` for the available identifiers; they depend on the blend mode.
