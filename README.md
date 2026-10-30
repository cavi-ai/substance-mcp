# Cavi Substance MCP

Typed MCP tools for Adobe Substance 3D Painter project inspection and texture export.

## Application support

This version targets Painter's documented Python remote scripting API. It supports project operations (UDIM, templates, mesh reload), layer-tree inspection, fill, group, mask, generator, smart-material, smart-mask and `.sbsar` layers, mesh-map baking, shelf search, and channel-packed texture export. It does not control Substance Designer or provide brush strokes.

Native project creation, texture-set and preset inspection, uniform fill creation, texture export, save-copy/reopen, and guarded closing have been checked with Painter 12.2.0 on macOS. Exported color, roughness, and metallic pixel values were checked against the fill inputs. Automated transport and path tests use an explicitly simulated API and do not establish compatibility with other Painter versions.

Baking, layer actions beyond `fill`/`texture_fill`, shelf search, mesh reload and custom export maps were verified against the installed Painter 12.2 module source and a read-only attribute probe, then tested only against the simulator; they have not been run natively.

## Install

Requires Python 3.11+, uv, and a separately installed and licensed Substance 3D Painter with the Python API. The MCP does not supply an Adobe license or authenticate accounts.

```sh
uv sync --locked
```

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

Register the stdio server in your MCP client:

```json
{"mcpServers":{"substance":{"command":"/path/to/substance-mcp/.venv/bin/cavi-substance-mcp"}}}
```

Call `painter_status` first. After `open`/`create`/`reload_mesh`, call `project` with `action="wait_ready"` before editing.

## Tools

Seven tools. Mutations need `lease_id` from `session_control` claim.

| Tool | Actions | Parameters |
|---|---|---|
| `painter_status` | — | none; returns `connected`, `open`, `file`, `needs_saving`, `busy`, `edition_state`, `baking`, `mesh_reload`, `workspace`, `workspace_exists` |
| `session_control` | `inspect`, `claim`, `renew`, `release`, `break` | `owner`, `expected_project`, `lease_id`, `ttl_seconds` (30–3600, default 600) |
| `project` | `open`, `create`, `close`, `save`, `save_copy`, `wait_ready`, `reload_mesh` | `path` (open), `mesh_path` (create, reload_mesh), `normal_map_format`, `default_texture_resolution`, `udim`, `template` (workspace `.spt`), `tangent_space_per_fragment` (create), `preserve_strokes` (reload_mesh, default true), `expected_file`, `discard_unsaved` (close), `directory` (save_copy), `timeout_seconds` (wait_ready), `lease_id` |
| `inspect` | `texture_sets`, `layer_stack`, `export_presets`, `shelf`, `bake_settings` | `texture_set` (layer_stack; bake_settings filter), `query`, `usage`, `limit` (shelf) |
| `layer_edit` | `fill`, `texture_fill`, `group`, `fill_mask`, `smart_material`, `smart_mask`, `generator`, `sbsar_fill`, `set_properties`, `delete` | `texture_set`, `name`, `uid`, `parent_uid`, `above_uid`, `base_color`, `base_color_texture`, `roughness`, `metallic`, `values`, `mask`, `resource`, `sbsar`, `parameters`, `blend_mode`, `opacity`, `channel`, `visible`, `confirm_name`, `lease_id` |
| `export_textures` | — | `directory`, `preset` or `maps`, `resolution`, `file_format`, `bit_depth`, `texture_sets`, `udim_tiles`, `padding`, `dilation_distance`, `dry_run` (default true), `lease_id` |
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
- `sbsar_fill` imports a workspace `.sbsar` as a project resource and makes it the fill's material source; `parameters` sets substance inputs (color inputs refused). Generators accept `parameters` too.
- `set_properties` changes `name`, `visible`, `blend_mode` (Painter `BlendingMode` name) and `opacity` for one `channel` or all; failures restore previous values.
- `delete` removes nodes this lease created; other nodes need `confirm_name` equal to the current name.
- Failed layer actions remove the nodes they created and report anything left.
- `export_textures` previews by default; only `dry_run=false` writes. `omitted_files` lists planned maps Painter did not emit.
- `maps` packs channels: `{"name":"T_$textureSet_MaskMap","format":"png","bit_depth":8,"channels":{"R":"metallic","G":"ambientOcclusion","B":"0","A":"1-roughness"}}`. Keys `R`/`G`/`B`/`A`/`RGB`/`L`; sources are document maps (`baseColor`, `roughness`, …), mesh maps (`ambient_occlusion`, `curvature`, `thickness`, `id`, `position`, `world_space_normals`, `normal_base`, `bent_normals`), `normal_opengl`/`normal_directx`/`ao_mixed`, or `0`/`1`. `1-roughness` maps to Painter's Glossiness virtual map; other inversions and constants are refused.
- `bake start` configures the selected sets (default all), disables the others, starts `bake_selected_textures_async` and returns. Poll `bake status` until `busy=false`; `status` is `Success`, `Cancel` or `Fail`. High-poly and cage meshes are workspace files passed as file URLs. Other edits are refused while baking.

Timeouts: 60 s per call, 600 s for save, save_copy and export. A timeout means the outcome is unknown; check `painter_status` before retrying. A refused connection means Painter is not reachable.

`CAVI_SUBSTANCE_WORKSPACE` selects the asset workspace, defaulting to `~/Documents/CaviGameArt`. Inputs and outputs stay inside it, including through symbolic links. Export and copy operations require a new destination directory and refuse existing ones. Use the same workspace for your Blender exports to pass meshes between applications.

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
```

The evaluator is pinned to `@cavi-ai/mcp-eval` 0.8.0. Both commands run in CI and retain JSON reports under `.tmp/evals/`. The protocol manifest checks discovery size, catalog token budgets, pagination, surface listing, and protocol negotiation. The core manifest adds schema, structured output, repeated reads, and contention checks.

The corpus exercises all seven tools through real stdio MCP calls and the real loopback transport. The core adapter explicitly simulates Painter's Python API while executing the production operation source. Cases check returned fields, project replacement refusal, busy/closed state, input and output containment, symlink escapes, existing-directory refusal, copy preservation, dry-run defaults, strict export flags, invalid presets, partial failures, and empty texture output. Layer cases cover duplicate names, bounded colors, assignment rollback, nesting, masks, shelf resolution, property edits, guarded deletion, and project readiness. Bake cases cover parameter mapping, async status, launch rollback and missing parameters; export cases cover channel packing and refused inversions. No-argument read tools have no tool-specific invalid inputs.

Simulated reports identify their evidence mode and cannot establish installation, API, licensing, or application compatibility. Protocol checks use an isolated unavailable endpoint and never contact a user's Painter session. Native export checks establish file generation, not painted-asset quality or destination-game material compatibility. MCP Eval is deterministic and does not invoke a model; no model-based quality claim is made. Designer remains unsupported.
