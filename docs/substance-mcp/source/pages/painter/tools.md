# Painter tools

`cavi-substance-mcp` publishes seven tools. Mutations need `lease_id` from a
`session_control` claim; see [Shared application ownership](session-ownership.md).

| Tool | Actions | Parameters |
|---|---|---|
| `painter_status` | — | none; returns `connected`, `open`, `file`, `needs_saving`, `busy`, `edition_state`, `baking`, `mesh_reload`, `workspace`, `workspace_exists` |
| `session_control` | `inspect`, `claim`, `renew`, `release`, `break` | `owner`, `expected_project`, `lease_id`, `ttl_seconds` (30–3600, default 600) |
| `project` | `open`, `create`, `close`, `save`, `save_copy`, `wait_ready`, `reload_mesh` | `path` (open), `mesh_path` (create, reload_mesh), `normal_map_format`, `default_texture_resolution`, `udim`, `template` (workspace `.spt`), `tangent_space_per_fragment` (create), `preserve_strokes` (reload_mesh, default true), `expected_file`, `discard_unsaved` (close), `directory` (save_copy), `timeout_seconds` (wait_ready), `lease_id` |
| `inspect` | `texture_sets`, `layer_stack`, `export_presets`, `shelf`, `bake_settings` | `texture_set` (layer_stack; bake_settings filter), `query`, `usage`, `limit` (shelf) |
| `layer_edit` | `fill`, `texture_fill`, `group`, `fill_mask`, `smart_material`, `smart_mask`, `generator`, `sbsar_fill`, `set_properties`, `delete` | `texture_set`, `name`, `uid`, `parent_uid`, `above_uid`, `base_color`, `base_color_texture`, `roughness`, `metallic`, `values`, `mask`, `resource`, `sbsar`, `source_manifest`, `expected_sha256`, `parameters`, `blend_mode`, `opacity`, `channel`, `visible`, `confirm_name`, `lease_id` |
| `export_textures` | — | `directory`, `preset` or `maps`, `source_manifest` + `source_file` (optional provenance), `resolution`, `file_format`, `bit_depth`, `texture_sets`, `udim_tiles`, `padding`, `dilation_distance`, `dry_run` (default true), `lease_id` |
| `bake` | `start`, `status` | `texture_sets`, `maps`, `high_poly_meshes`, `resolution`, `antialiasing`, `max_frontal`, `max_rear`, `use_cage`, `cage_mesh`, `match`, `low_poly_suffix`, `high_poly_suffix`, `lease_id` |

## Projects

- `open`/`create` run only with no project open; loading is asynchronous. `udim` selects `ProjectWorkflow.UVTile`.
- `reload_mesh` is asynchronous: `wait_ready`, then read `painter_status.mesh_reload.status` (`SUCCESS`/`ERROR`).
- `close` targets the inspected `expected_file` (null for unsaved) and refuses unsaved edits unless `discard_unsaved=true`.
- `save` overwrites the project's existing workspace file; never-saved or out-of-workspace projects are refused.
- `save_copy` writes `project.spp` into a new directory without moving the open project.
- `wait_ready` polls status until `open && !busy && edition_state` or the timeout; read-only.

## Inspection

- `texture_sets` includes resolution, UV tiles and channel formats. `layer_stack` walks groups (depth ≤ 8, ≤ 512 nodes, `truncated` flags the cut) with masks, effects, visibility, per-channel blend mode and opacity, and every channel source.
- `shelf` searches resources (≤ 100) optionally filtered by `smart_material`, `smart_mask`, `material`, `generator`, `texture`, `environment`. `bake_settings` lists per-set baking parameters and baked mesh maps.

## Layers

- `fill` adds a uniquely named fill (sRGB color, raw roughness/metallic); `values` sets `normal`, `height`, `emissive`, `opacity`, `ao`, `user0`–`user7` when the Texture Set has them and refuses otherwise. `texture_fill` embeds a workspace PNG (≤16 MiB, ≤4096 px) as sRGB base color; the imported resource stays on failure (Painter has no removal API).
- New layers go on top, inside `parent_uid` (a group) or above `above_uid`. `group`, `smart_material` and `sbsar_fill` need a unique `name`.
- `fill_mask` adds a `black`/`white` mask. `smart_mask` and `generator` insert a shelf resource (name or `resource://` URL; ambiguous names are refused) into a layer's mask, adding a black mask when missing.
- `sbsar_fill` imports a workspace `.sbsar` as a project resource and makes it the fill's material source; `parameters` sets substance inputs (color inputs refused). `source_manifest` verifies the `.sbsar` against a receipt before importing; `expected_sha256` verifies the file directly. Both refuse before any resource is imported. Generators accept `parameters` too.
- `set_properties` changes `name`, `visible`, `blend_mode` (Painter `BlendingMode` name) and `opacity` for one `channel` or all; failures restore previous values.
- `delete` removes nodes this lease created; other nodes need `confirm_name` equal to the current name.
- Failed layer actions remove the nodes they created and report anything left.

## Export

- `export_textures` previews by default; only `dry_run=false` writes. `omitted_files` lists planned maps Painter did not emit. `source_manifest` plus `source_file` records verified provenance in the export manifest.
- `maps` packs channels: `{"name":"T_$textureSet_MaskMap","format":"png","bit_depth":8,"channels":{"R":"metallic","G":"ambientOcclusion","B":"0","A":"1-roughness"}}`. Keys `R`/`G`/`B`/`A`/`RGB`/`L`; sources are document maps (`baseColor`, `roughness`, …), mesh maps (`ambient_occlusion`, `curvature`, `thickness`, `id`, `position`, `world_space_normals`, `normal_base`, `bent_normals`), `normal_opengl`/`normal_directx`/`ao_mixed`, or `0`/`1`. `1-roughness` maps to Painter's Glossiness virtual map; other inversions and constants are refused.

## Baking

- `bake start` configures the selected sets (default all), disables the others, starts `bake_selected_textures_async` and returns. Poll `bake status` until `busy=false`; `status` is `Success`, `Cancel` or `Fail`. High-poly and cage meshes are workspace files passed as file URLs. Other edits are refused while baking.

## Timeouts

Timeouts: 60 s per call, 600 s for save, save_copy and export. A timeout means the outcome
is unknown; check `painter_status` before retrying. A refused connection means Painter is
not reachable. A timed-out export may have written partial files; inspect its destination
before proceeding. Export cancellation and warnings are reported as incomplete rather than
success.
