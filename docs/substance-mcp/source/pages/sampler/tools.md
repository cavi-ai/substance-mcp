# Sampler tools

`cavi-substance-sampler-mcp` launches Sampler with `--run-script-silent` for one script
per tool call or one complete batch job. Sampler must be fully quit: a running instance
receives the arguments but does not run startup scripts.

```json
{"mcpServers": {"substance-sampler": {"command": "cavi-substance-sampler-mcp"}}}
```

- Executable: `CAVI_SAMPLER_BIN` (exact path), else `CAVI_SAMPLER_APP` (`.app` path),
  else the standard install path for macOS, Windows or Linux, else the executable on
  `PATH`. The version is read from the bundle's `Info.plist` on macOS and from
  `--version` elsewhere.
- `CAVI_SAMPLER_SCRIPT_PATH` overrides the startup-scripts folder that `sampler_status`
  reports (default: Sampler's per-user scripts folder). Each launch also sets Adobe's own
  `SAMPLER_SCRIPT_PATH` for the Sampler it starts; that variable is Adobe's, not ours.
- Paths are relative to `CAVI_SUBSTANCE_WORKSPACE`; outputs go to new directories.
  Failed calls remove the directory they created.
- Each call starts Sampler, opens the project if supplied, runs one operation, saves
  mutations, and exits. There is no persistent Sampler session, ownership or lease.

| Tool | Actions / inputs |
| --- | --- |
| `sampler_status` | executable, version, scripts folder, workspace, running state |
| `sampler_inspect` | `project` lists assets; `asset` lists layers and parameters; `catalog` lists filters, materials, generators, export presets, material models, predefined presets, import options and target availability |
| `sampler_project` | `create` `name` + new `directory`; `save_as` `project` + new `directory` |
| `sampler_asset` | `create` with `model`/`preset` or `empty`, `delete`, `import_resource` (optional declared `resources`, `source_manifest`, `expected_sha256`), `set_metadata`, `activate_channels`, `deactivate_channels`, `import_images`, `import_textures`, `insert_filter`, `insert_material`; saves the project |
| `sampler_layer` | `set_parameters`, `apply_preset`, `set_channels` (IDs or unambiguous labels), `set_blend_parameters`, `remove`; removal needs `confirm=true` |
| `sampler_export` | `project`, new `directory`, optional engine `target`, `format`, `preset`, `channels`, `resolution`, `wait_seconds`; `dry_run` defaults to true |
| `sampler_build` | workspace `.json` job file; `dry_run` validates without launching; `check_sampler` adds the installed-Sampler probe; `check_project` adds source-project checks; `check_build` executes non-export steps in a temporary project |
| `sampler_pack` | new `directory`, `sources` usage→workspace image, `pack` channel spec; host-side packing |

`import_resource` accepts `source_manifest` and `expected_sha256`; the resource is
verified before Sampler imports it, and the result includes `verified` and `source`.

## Engine targets

`sampler_export` and batch jobs share one target registry. Only the target changes:

| Target | Default preset | Texture output |
| --- | --- | --- |
| `unreal` | Unreal Engine 4 | basecolor, normal, packed ORM |
| `unity` | Unity HD Render Pipeline Standard | basecolor, normal, mask map |
| `blender` | Blender Cycles/Eevee | basecolor, normal, roughness, metallic |
| `generic` | none | caller-selected channels |

`preset` overrides a target default. When `target` is set on `sampler_export`, a
sbsar format falls back to `png`. `sampler_inspect catalog` and the probe report
whether each target preset is installed. Exports wait up to 600 s for map files by
default; raise or lower `wait_seconds` when needed.

[Batch jobs](batch-jobs.md) run several steps in one launch, and
[Source projects and authoring](source-projects.md) covers existing `.ssa` projects.

## Limitations

- Sampler exposes startup scripts and plugins, not remote control.
- No UI automation, environment assets, bakes, masks or project closing.
- Image parameters are not settable by batch jobs; scalar, vector, integer and
  string parameters are supported.
- Layer blend mode is read-only after insertion; set it when inserting a material.
- Sampler may rewrite the metadata author to the signed-in account when saving.
- Engine presets are matched by display name. Localized Sampler installs may need an
  explicit `preset` override.
- Project-aware preflight is limited to source-project jobs. Use `--check-build` to
  validate new-project layers and parameters; it skips exports and refuses
  `import_resource` jobs.
- `sampler_status` reports `running: null` where process inspection is unavailable
  (`pgrep` on macOS and Linux, `tasklist` on Windows).
- A timed-out call may have written partial files; inspect the destination before
  retrying. Mutations that were not saved are lost when the process exits.
- Native project creation, source-project copies, source-project preflight, metadata,
  channel activation, layer applies-to channels, blend parameters, preset application,
  layer removal, B2M image import, Unreal/Unity/Blender target exports and manifest
  generation were checked with Sampler 6.0.4 on macOS.
- Native use requires a licensed Sampler installation and a closed application.
