# Cavi Substance MCP

Typed MCP tools for Adobe Substance 3D Painter project inspection and texture export.

## Application support

This version targets Painter's documented Python remote scripting API. It supports project operations, root-layer inspection, uniform and bitmap fill layers, and texture export. It does not control Substance Designer or provide brush strokes, baking, masks, or smart-material authoring tools.

Native project creation, texture-set and preset inspection, uniform fill creation, texture export, save-copy/reopen, and guarded closing have been checked with Painter 12.2.0 on macOS. Exported color, roughness, and metallic pixel values were checked against the fill inputs. Automated transport and path tests use an explicitly simulated API and do not establish compatibility with other Painter versions.

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

Call `painter_status` first. After `open`/`create`, call `project` with `action="wait_ready"` before editing.

## Tools

Six tools. Mutations need `lease_id` from `session_control` claim.

| Tool | Actions | Parameters |
|---|---|---|
| `painter_status` | — | none; returns `connected`, `open`, `file`, `needs_saving`, `busy`, `edition_state`, `workspace`, `workspace_exists` |
| `session_control` | `inspect`, `claim`, `renew`, `release`, `break` | `owner`, `expected_project`, `lease_id`, `ttl_seconds` (30–3600, default 600) |
| `project` | `open`, `create`, `close`, `save`, `save_copy`, `wait_ready` | `path` (open), `mesh_path`, `normal_map_format` (`OpenGL`/`DirectX`), `default_texture_resolution` (256–8192, power of two) (create), `expected_file`, `discard_unsaved` (close), `directory` (save_copy), `timeout_seconds` (wait_ready, 1–300, default 60), `lease_id` |
| `inspect` | `texture_sets`, `layer_stack`, `export_presets` | `texture_set` (layer_stack) |
| `layer_edit` | `fill`, `texture_fill` | `texture_set`, `name`, `base_color` (fill), `base_color_texture` (texture_fill), `roughness`, `metallic`, `lease_id` |
| `export_textures` | — | `directory`, `preset`, `dry_run` (default true), `lease_id` |

- `open`/`create` run only with no project open; loading is asynchronous.
- `close` targets the inspected `expected_file` (null for unsaved) and refuses unsaved edits unless `discard_unsaved=true`.
- `save` overwrites the project's existing workspace file; never-saved or out-of-workspace projects are refused.
- `save_copy` writes `project.spp` into a new directory without moving the open project.
- `wait_ready` polls status until `open && !busy && edition_state` or the timeout; read-only.
- `texture_sets` includes each set's resolution and channel formats; `layer_stack` includes split-fill bitmap URLs, color spaces and uniform values.
- `fill` adds a uniquely named root fill (sRGB color, raw roughness/metallic); a failed assignment removes the new layer.
- `texture_fill` embeds a workspace PNG (≤16 MiB, ≤4096 px) as sRGB base color. On failure the layer and temporary `.cavi-bitmap-*` files are removed; the imported resource stays (Painter has no removal API) and anything not removed is reported.
- `export_textures` previews by default; only `dry_run=false` writes. `omitted_files` lists planned maps Painter did not emit.

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

The corpus exercises all six tools through real stdio MCP calls and the real loopback transport. The core adapter explicitly simulates Painter's Python API while executing the production operation source. Cases check returned fields, project replacement refusal, busy/closed state, input and output containment, symlink escapes, existing-directory refusal, copy preservation, dry-run defaults, strict export flags, invalid presets, partial failures, and empty texture output. Layer cases cover duplicate names, bounded colors, assignment rollback, and project readiness. No-argument read tools have no tool-specific invalid inputs.

Simulated reports identify their evidence mode and cannot establish installation, API, licensing, or application compatibility. Protocol checks use an isolated unavailable endpoint and never contact a user's Painter session. Native export checks establish file generation, not painted-asset quality or destination-game material compatibility. MCP Eval is deterministic and does not invoke a model; no model-based quality claim is made. Designer remains unsupported.
