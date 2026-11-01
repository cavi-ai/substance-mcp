# Batch jobs

A job runs every step in one Sampler launch and writes one atomic directory:

```sh
cavi-substance-sampler-mcp validate jobs/photo-to-pbr.json
cavi-substance-sampler-mcp validate jobs/photo-to-pbr.json --check-sampler
cavi-substance-sampler-mcp validate jobs/source.json --check-project
cavi-substance-sampler-mcp validate jobs/photo-to-pbr.json --check-build
cavi-substance-sampler-mcp run jobs/photo-to-pbr.json --json
cavi-substance-sampler-mcp run jobs/a.json jobs/b.json --continue-on-error
cavi-substance-sampler-mcp run jobs/photo-to-pbr.json --verbose
cavi-substance-sampler-mcp schema
cavi-substance-sampler-mcp doctor
cavi-substance-sampler-mcp doctor --launch
```

```json
{
  "schema": 1,
  "directory": "jobs/photo-to-pbr",
  "project": {"name": "Photo PBR"},
  "steps": [
    {"action": "create_asset", "name": "PhotoPBR", "empty": true},
    {"action": "import_images", "asset": "PhotoPBR",
     "images": ["photos/albedo.png"], "import_option": "image_to_material_B2M"},
    {"action": "insert_filter", "asset": "PhotoPBR", "filter": "Tiling", "position": 1},
    {"action": "set_parameters", "asset": "PhotoPBR", "layer": "Tiling",
     "parameters": {"Threshold": 0.1}},
    {"action": "export", "asset": "PhotoPBR", "target": "unreal", "resolution": 2048}
  ]
}
```

- Steps: `create_asset` (optionally `empty` for B2M), `import_resource`,
  `import_images`, `import_textures`, `insert_filter`, `insert_material`,
  `set_parameters`, `apply_preset`, `set_metadata`, `activate_channels`,
  `deactivate_channels`, `set_channels`, `set_blend_parameters`, `remove_layer`,
  `export`.
- The job directory must be new. It contains `job.json`, the project, exports and
  `manifest.json`; on failure it is removed unless `--keep-on-failure`.
- The manifest records the app version, job and input hashes, step results, output
  sizes and SHA-256 hashes.
- Each export writes its own receipt, which records the project as exported: unsaved
  changes are saved before an export step runs.
- `validate --check-sampler` launches one read-only probe and refuses a job whose
  target preset, import option, filter, material or material model is not installed.
  For source-project jobs it adds a warning that filters and materials need
  `--check-project`. `doctor --launch` reports the same probe with target availability.
- `validate --check-project` also copies a source project to a temporary project,
  opens it read-only, and verifies assets, layers, parameters, presets, filters,
  materials, channels and blend parameters before launch. The source stays untouched
  and the temporary directory is removed.
- `validate --check-build` executes every non-export step in a temporary project and
  removes it afterwards. This validates new-project layers, parameters, presets,
  channels and blend parameters before the real job directory exists. It can be as
  expensive as the real non-export job, and jobs containing `import_resource` are
  refused because importing custom resources can affect Sampler's resource library.
- `sampler_build(dry_run=true, check_sampler=true, check_project=true, check_build=true)`
  runs the same preflight from MCP. `check_build` supersedes the other checks.
- `ImportResource` steps may declare `resources` so preflight can exempt those names
  from filter/material checks. Undeclared imports skip filter/material checks and
  report a preflight warning.
- `run` accepts one or more job files. `--continue-on-error` keeps going after a
  failed job and returns the first failure code. `--verbose` streams step start/end
  progress to stderr; the job directory also keeps `progress.jsonl`.
- Each manifest step includes `seconds`. Concurrent jobs serialize on a workspace
  lock. `--wait-for-app` waits for a user Sampler window to close; otherwise the job
  fails fast.
- Exit codes: `0` success, `2` invalid job, `3` app unavailable, `4` app running or
  lock conflict, `5` operation failure, `6` timeout.

The repository's `examples/sampler/photo-to-pbr/`, `examples/sampler/ai-to-material/`
and `examples/sampler/multiangle-to-material/` are complete recipes.
