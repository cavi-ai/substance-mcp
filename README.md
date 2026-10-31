# Cavi Substance MCP

MCP servers for Adobe Substance 3D Painter, Adobe Substance 3D Designer and Adobe
Substance 3D Sampler. Each application has its own stdio server.

## Application support

This version targets Painter's documented Python remote scripting API. It supports project operations (UDIM, templates, mesh reload), layer-tree inspection, fill, group, mask, generator, smart-material, smart-mask and `.sbsar` layers, mesh-map baking, shelf search, and channel-packed texture export. It does not provide brush strokes; Designer's CLIs are served separately (see Designer).

Native project creation, texture-set and preset inspection, uniform fill creation, texture export, save-copy/reopen, and guarded closing have been checked with Painter 12.2.0 on macOS. Exported color, roughness, and metallic pixel values were checked against the fill inputs. Automated transport and path tests use an explicitly simulated API and do not establish compatibility with other Painter versions.

Baking, layer actions beyond `fill`/`texture_fill`, shelf search, mesh reload and custom export maps were checked against Painter's documented Python API and a read-only attribute probe, then tested only against the simulator; they have not been run natively.

Designer and Sampler discovery, process checks and process termination are written for Windows as well as macOS and Linux (default install paths under `Program Files`, `tasklist`, `taskkill`). They have not run on Windows yet; the Windows CI leg is experimental until it has run green on a real runner.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a separately installed and
licensed Adobe application. The MCP does not supply an Adobe license or authenticate
accounts.

Install the three servers as commands on your `PATH`: `cavi-substance-mcp` (Painter),
`cavi-substance-designer-mcp` and `cavi-substance-sampler-mcp`. The same install provides
`cavi-substance-receipts`, which verifies the manifests they write (see [Receipts](#receipts)).

```sh
uv tool install git+https://github.com/cavi-ai/substance-mcp
```

Upgrade with `uv tool upgrade cavi-substance-mcp`. To work from a checkout instead, see
[CONTRIBUTING.md](CONTRIBUTING.md).

Register the servers you need in your MCP client. Desktop apps that do not inherit your
shell's `PATH` need absolute paths, which `command -v cavi-substance-mcp` prints.

```json
{
  "mcpServers": {
    "substance-painter": {"command": "cavi-substance-mcp"},
    "substance-designer": {"command": "cavi-substance-designer-mcp"},
    "substance-sampler": {"command": "cavi-substance-sampler-mcp"}
  }
}
```

Tools read and write files only inside the workspace (see below). To use a folder other
than the default, add `"env": {"CAVI_SUBSTANCE_WORKSPACE": "/absolute/path"}` to a server
entry.

### Painter

Launch the installed Painter application with remote scripting enabled:

```sh
cavi-substance-mcp runtime start
cavi-substance-mcp runtime status
```

Automatic launch currently supports macOS. It finds the standard Adobe installation,
or the app bundle selected by `CAVI_PAINTER_APP`, and supplies `--enable-remote-scripting`.
A connected session is reused. An open app without a reachable scripting connection
is left untouched; save and quit that app before launching it again. The launcher does
not install software, sign in, accept terms, or dismiss license prompts.

`start` reports that launch was requested; it does not claim Painter is ready. Require
`connected=true` from `status` or the actual `painter_status` MCP tool before project work.
Commands print JSON and exit nonzero on failure. Running `cavi-substance-mcp` without
arguments continues to serve MCP over stdio.

Adobe documents the protocol in its [remote control guide](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/tutorials/remote-control).

Call `painter_status` first. After `open`/`create`/`reload_mesh`, call `project` with `action="wait_ready"` before editing.

## Tools

Seven tools. Mutations need `lease_id` from `session_control` claim.

| Tool | Actions | Parameters |
|---|---|---|
| `painter_status` | — | none; returns `connected`, `open`, `file`, `needs_saving`, `busy`, `edition_state`, `baking`, `mesh_reload`, `workspace`, `workspace_exists` |
| `session_control` | `inspect`, `claim`, `renew`, `release`, `break` | `owner`, `expected_project`, `lease_id`, `ttl_seconds` (30–3600, default 600) |
| `project` | `open`, `create`, `close`, `save`, `save_copy`, `wait_ready`, `reload_mesh` | `path` (open), `mesh_path` (create, reload_mesh), `normal_map_format`, `default_texture_resolution`, `udim`, `template` (workspace `.spt`), `tangent_space_per_fragment` (create), `preserve_strokes` (reload_mesh, default true), `expected_file`, `discard_unsaved` (close), `directory` (save_copy), `timeout_seconds` (wait_ready), `lease_id` |
| `inspect` | `texture_sets`, `layer_stack`, `export_presets`, `shelf`, `bake_settings` | `texture_set` (layer_stack; bake_settings filter), `query`, `usage`, `limit` (shelf) |
| `layer_edit` | `fill`, `texture_fill`, `group`, `fill_mask`, `smart_material`, `smart_mask`, `generator`, `sbsar_fill`, `set_properties`, `delete` | `texture_set`, `name`, `uid`, `parent_uid`, `above_uid`, `base_color`, `base_color_texture`, `roughness`, `metallic`, `values`, `mask`, `resource`, `sbsar`, `source_manifest`, `expected_sha256`, `parameters`, `blend_mode`, `opacity`, `channel`, `visible`, `confirm_name`, `lease_id` |
| `export_textures` | — | `directory`, `preset` or `maps`, `source_manifest` + `source_file` (optional provenance), `resolution`, `file_format`, `bit_depth`, `texture_sets`, `udim_tiles`, `padding`, `dilation_distance`, `dry_run` (default true), `lease_id` |
| `bake` | `start`, `status` | `texture_sets`, `maps`, `high_poly_meshes`, `resolution`, `antialiasing`, `max_frontal`, `max_rear`, `use_cage`, `cage_mesh`, `match`, `low_poly_suffix`, `high_poly_suffix`, `lease_id` |

- `open`/`create` run only with no project open; loading is asynchronous. `udim` selects `ProjectWorkflow.UVTile`.
- `reload_mesh` is asynchronous: `wait_ready`, then read `painter_status.mesh_reload.status` (`SUCCESS`/`ERROR`).
- `close` targets the inspected `expected_file` (null for unsaved) and refuses unsaved edits unless `discard_unsaved=true`.
- `save` overwrites the project's existing workspace file; never-saved or out-of-workspace projects are refused.
- `save_copy` writes `project.spp` into a new directory without moving the open project.
- `wait_ready` polls status until `open && !busy && edition_state` or the timeout; read-only.
- `texture_sets` includes resolution, UV tiles and channel formats. `layer_stack` walks groups (depth ≤ 8, ≤ 512 nodes, `truncated` flags the cut) with masks, effects, visibility, per-channel blend mode and opacity, and every channel source.
- `shelf` searches resources (≤ 100) optionally filtered by `smart_material`, `smart_mask`, `material`, `generator`, `texture`, `environment`. `bake_settings` lists per-set baking parameters and baked mesh maps.
- `fill` adds a uniquely named fill (sRGB color, raw roughness/metallic); `values` sets `normal`, `height`, `emissive`, `opacity`, `ao`, `user0`–`user7` when the Texture Set has them and refuses otherwise. `texture_fill` embeds a workspace PNG (≤16 MiB, ≤4096 px) as sRGB base color; the imported resource stays on failure (Painter has no removal API).
- New layers go on top, inside `parent_uid` (a group) or above `above_uid`. `group`, `smart_material` and `sbsar_fill` need a unique `name`.
- `fill_mask` adds a `black`/`white` mask. `smart_mask` and `generator` insert a shelf resource (name or `resource://` URL; ambiguous names are refused) into a layer's mask, adding a black mask when missing.
- `sbsar_fill` imports a workspace `.sbsar` as a project resource and makes it the fill's material source; `parameters` sets substance inputs (color inputs refused). `source_manifest` verifies the `.sbsar` against a receipt before importing; `expected_sha256` verifies the file directly. Both refuse before any resource is imported. Generators accept `parameters` too.
- `set_properties` changes `name`, `visible`, `blend_mode` (Painter `BlendingMode` name) and `opacity` for one `channel` or all; failures restore previous values.
- `delete` removes nodes this lease created; other nodes need `confirm_name` equal to the current name.
- Failed layer actions remove the nodes they created and report anything left.
- `export_textures` previews by default; only `dry_run=false` writes. `omitted_files` lists planned maps Painter did not emit. `source_manifest` plus `source_file` records verified provenance in the export manifest.
- `maps` packs channels: `{"name":"T_$textureSet_MaskMap","format":"png","bit_depth":8,"channels":{"R":"metallic","G":"ambientOcclusion","B":"0","A":"1-roughness"}}`. Keys `R`/`G`/`B`/`A`/`RGB`/`L`; sources are document maps (`baseColor`, `roughness`, …), mesh maps (`ambient_occlusion`, `curvature`, `thickness`, `id`, `position`, `world_space_normals`, `normal_base`, `bent_normals`), `normal_opengl`/`normal_directx`/`ao_mixed`, or `0`/`1`. `1-roughness` maps to Painter's Glossiness virtual map; other inversions and constants are refused.
- `bake start` configures the selected sets (default all), disables the others, starts `bake_selected_textures_async` and returns. Poll `bake status` until `busy=false`; `status` is `Success`, `Cancel` or `Fail`. High-poly and cage meshes are workspace files passed as file URLs. Other edits are refused while baking.

Timeouts: 60 s per call, 600 s for save, save_copy and export. A timeout means the outcome is unknown; check `painter_status` before retrying. A refused connection means Painter is not reachable.

`CAVI_SUBSTANCE_WORKSPACE` selects the asset workspace, defaulting to `~/Documents/SubstanceMCP`. The default is created the first time a tool writes to it. A workspace you set yourself must already exist; a missing one is refused instead of created. Read-only tools never create it; the one exception is that a Sampler call holds its lock file in the workspace, so it creates the default first. Inputs and outputs stay inside it, including through symbolic links. Export and copy operations require a new destination directory and refuse existing ones. All three servers share the one workspace, so the output of one application is an input to the next.

`CAVI_PAINTER_PORT` selects Painter's local scripting port, defaulting to `60041`. Connections use `127.0.0.1` directly, without HTTP proxies or redirects. Painter's own scripting endpoint permits arbitrary scripts; keep it private to a trusted local workstation. This MCP exposes only the listed operations and never accepts caller-provided Python or JavaScript. The operation payload is sent once and cached in Painter by SHA-256; later calls send a small bootstrap.

There are no automatic retries, external uploads, telemetry or account logins. Application launch is explicit through the CLI. A timed-out export may have written partial files; inspect its destination before proceeding. Export cancellation and warnings are reported as incomplete rather than success.


## Shared application ownership

Before editing, call `session_control` with `action="inspect"`, then claim the
returned `project_id` with `action="claim"`, an `owner` label, and that exact
`expected_project`. Claim returns a private `lease_id`; pass it on application
mutations. Read-only inspection remains available without ownership.

Claims last 30–3600 seconds, default 600. Renew with `action="renew"` and release
with `action="release"`, both supplying the same `lease_id`. `action="break"` with
`expected_project` clears a lease unused for at least 300 seconds and returns `previous_owner`. Release and expiry
never save, close, or discard a project. Conflicting claims and missing, foreign,
expired, or project-mismatched edit tokens are refused. An owned operation holds
its exclusive section until it finishes. A successful owned save or project
transition updates the claim's binding.

Claims coordinate participating MCP clients, not direct human edits or arbitrary
scripts. Upgrade every participating client. The lease object is versioned; a newer
client migrates an older client's lease state instead of reusing its code. Keep lease IDs out of asset receipts
and handoff documents; a new agent inspects and claims its own session.

Project identity includes Painter's process, project UUID, and file path, so
separate unsaved projects are distinguished. Project creation and opening remain
asynchronous; inspect loading completion before continuing. If that changes the
binding after the call returns, release and claim the inspected new project.
The existing unsaved-close guard remains in force.

## Receipts

Every file-producing operation writes `manifest.json` into its new output directory.
The receipt records the app, operation, parameters, input files and output files with
sizes and SHA-256 hashes.

```json
{"schema": 1, "app": "designer", "operation": "cook",
 "inputs": [{"path": "source.sbs", "size": 10, "sha256": "..."}],
 "outputs": [{"path": "cooked/source.sbsar", "size": 20, "sha256": "..."}],
 "parameters": {"source": "templates/..."}}
```

- Designer: `cook`, `render`, `pack`, `bake`.
- Painter: `export_textures`, `save_copy`; exports can record `source_manifest` +
  `source_file` provenance. `sbsar_fill` results record the imported `.sbsar` as
  `source` and set `verified` when `source_manifest` or `expected_sha256` was checked.
- Sampler: job manifests use `app_name` (`app` already holds the installation);
  `sampler_export`, `sampler_project create/save_as` and `sampler_pack` output use
  the shared fields.

Verify a manifest and every file it records:

```sh
cavi-substance-receipts verify handoff/designer/manifest.json
cavi-substance-receipts verify handoff/designer/manifest.json \
  --file handoff/designer/rock.sbsar --role outputs --json
```

A manifest can carry `provenance` links to earlier manifests. Painter export
manifests record the verified `.sbsar` manifest and file when `source_manifest` and
`source_file` are supplied. Verify the whole chain, including those links:

```sh
cavi-substance-receipts chain handoff/painter/manifest.json
```

`cavi-substance-receipts chain` verifies every manifest, every recorded file, and each provenance
link's source file. See `examples/pipeline/designer-to-painter/` for a complete
handoff.

## Validation

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
```

Native acceptance requires a licensed Painter session and a disposable project: inspect status and texture sets, list presets, preview/export into a fresh directory, and inspect the produced textures in the destination game.

## MCP evaluation

```sh
uv sync --locked --dev
npm ci
npm run eval:protocol
npm run eval:core
npm run eval:designer-protocol
npm run eval:designer-core
npm run eval:sampler-protocol
npm run eval:sampler-core
```

The evaluator is pinned to `@cavi-ai/mcp-eval` 0.8.0. All commands run in CI and retain JSON reports under `.tmp/evals/`. The protocol manifests check discovery size, catalog token budgets, pagination, surface listing, and protocol negotiation. The core manifests add schema, structured output, repeated reads, and contention checks.

The corpus exercises all seven tools through real stdio MCP calls and the real loopback transport. The core adapter explicitly simulates Painter's Python API while executing the production operation source. Cases check returned fields, project replacement refusal, busy/closed state, input and output containment, symlink escapes, existing-directory refusal, copy preservation, dry-run defaults, strict export flags, invalid presets, partial failures, and empty texture output. Layer cases cover duplicate names, bounded colors, assignment rollback, nesting, masks, shelf resolution, property edits, guarded deletion, and project readiness. Bake cases cover parameter mapping, async status, launch rollback and missing parameters; export cases cover channel packing and refused inversions. No-argument read tools have no tool-specific invalid inputs.

Simulated reports identify their evidence mode and cannot establish installation, API, licensing, or application compatibility. Protocol checks use an isolated unavailable endpoint and never contact a user's Painter session. Native export checks establish file generation, not painted-asset quality or destination-game material compatibility. MCP Eval is deterministic and does not invoke a model; no model-based quality claim is made.

## Designer

`cavi-substance-designer-mcp` is a second stdio server for Substance 3D Designer's
headless CLIs (`sbscooker`, `sbsrender`, `substance3d_baker`). No Designer UI or lease.

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

Bake maps: `normal` Normal.Raytraced, `ambient_occlusion` AmbientOcclusion.Raytraced,
`curvature` Curvature.Raytraced, `thickness` Thickness.Raytraced, `position`
Position.Raytraced, `world_normal` Normal.TangentToWorld (low mesh only), `bent_normal`
Normal.BentNormals, `id` Color.Raytraced, `height` Height.Raytraced, `opacity`
Opacity.Raytraced. One baker run per map; each output file is verified.

Channel packing spec (shared with the Painter server):

```json
{"maps": [
  {"name": "T_Rock_MaskMap", "format": "png", "bit_depth": 8,
   "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}},
  {"name": "T_Rock_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}},
  {"name": "T_Rock_Normal", "channels": {"RGB": "normal"}}],
 "defaults": {"opacity": 1}}
```

- Sources: usage, `1-<usage>`, or a constant `0..1`. Missing usages need `defaults`.
- No `A` key writes RGB. All sources share one resolution. Gray from RGB uses R.
- Formats `png`, `tga`, `tif`; `bit_depth` 16 is PNG only.

Limitations:

- Presets are not listed (`sbsrender info` does not expose them) and an unknown preset
  name is ignored by `sbsrender`.
- 16-bit RGB/RGBA sources are read at 8-bit precision; 16-bit grayscale is exact.

```sh
npm run eval:designer-protocol
npm run eval:designer-core
uv run --locked python -m unittest tests.test_designer_native  # skips without Designer
```

The evals run against fake CLIs (`evals/designer_fake_cli.py`); native tests need
Designer and, for baking, Blender.

## Sampler

`cavi-substance-sampler-mcp` is a third stdio server. It launches Sampler with
`--run-script-silent` for one script per tool call or one complete batch job.
Sampler must be fully quit: a running instance receives the arguments but does not
run startup scripts.

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

### Engine targets

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

### Batch jobs

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

See `examples/sampler/photo-to-pbr/`, `examples/sampler/ai-to-material/` and
`examples/sampler/multiangle-to-material/` for complete recipes.

### Source projects

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

### Authoring depth

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

### Packing

`sampler_pack` packs workspace maps into a new directory using the shared channel
spec:

```json
{"maps": [
  {"name": "T_Rock_MaskMap", "format": "png", "bit_depth": 8,
   "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}},
  {"name": "T_Rock_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}}],
 "defaults": {"opacity": 1}}
```

The CLI form reads a JSON file with `directory`, `sources` and `pack`:

```sh
cavi-substance-sampler-mcp pack pack.json --json
```

Packing runs on the host, not inside Sampler. The Designer `material pack` action uses
the same spec.

Limitations:

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

```sh
npm run eval:sampler-protocol
npm run eval:sampler-core
uv run --locked python -m unittest tests.test_sampler_native  # launch test needs CAVI_SAMPLER_NATIVE=1
```

The evals run against a fake Sampler executable (`evals/sampler_fake_cli.py`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and the rules for changes.

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

## Trademarks

Adobe, Substance 3D, Substance 3D Painter, Substance 3D Designer and Substance 3D Sampler are
either registered trademarks or trademarks of Adobe in the United States and/or other
countries. This project is not affiliated with, endorsed by, or sponsored by Adobe. It does
not include or redistribute Adobe software, templates, presets or Substance files; you supply
your own licensed applications. Other product names (for example Unreal Engine, Unity and
Blender) belong to their respective owners.
