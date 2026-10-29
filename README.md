# Cavi Substance MCP

Typed MCP tools for Adobe Substance 3D Painter project inspection and texture export.

## Application support

This version targets Painter's documented Python remote scripting API. It does not control Substance Designer or provide painting, layer editing, baking, or smart-material authoring tools.

**Live Painter validation is pending.** The transport, request encoding and path boundaries have automated local tests. These tests do not establish compatibility with a running Painter installation.

## Install

Requires Python 3.11+, uv, and a separately installed and licensed Substance 3D Painter with the Python API. The MCP does not supply an Adobe license or authenticate accounts.

```sh
uv sync --locked
```

Launch Painter with `--enable-remote-scripting`. Adobe documents the protocol in its [remote control guide](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/tutorials/remote-control).

Register the stdio server in your MCP client:

```json
{"mcpServers":{"substance":{"command":"/path/to/substance-mcp/.venv/bin/cavi-substance-mcp"}}}
```

Call `painter_status` first. Project opening and creation are asynchronous: inspect status before further operations.

## Tools

- `painter_status`: connectivity and project state.
- `open_project`, `create_project`: open a workspace project or import a workspace mesh, only when no project is already open.
- `texture_sets`, `export_presets`: inspect the current project and predefined presets.
- `save_project_copy`: save a new copy without changing the open project's location.
- `export_textures`: preview output paths by default; export only when `dry_run=false` is explicitly supplied.

`CAVI_SUBSTANCE_WORKSPACE` selects the asset workspace, defaulting to `~/Documents/CaviGameArt`. Inputs and outputs stay inside it, including through symbolic links. Export and copy operations require a new destination directory and refuse existing ones. Use the same workspace for your Blender exports to pass meshes between applications.

`CAVI_PAINTER_PORT` selects Painter's local scripting port, defaulting to `60041`. Connections use `127.0.0.1` directly, without HTTP proxies or redirects. Painter's own scripting endpoint permits arbitrary scripts; keep it private to a trusted local workstation. This MCP exposes only the listed operations and never accepts caller-provided Python or JavaScript.

There are no automatic retries, external uploads, telemetry, application launches or account logins. A timed-out export may have written partial files; inspect its destination before proceeding. Export cancellation and warnings are reported as incomplete rather than success.

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

The corpus exercises all seven tools through real stdio MCP calls and the real loopback transport. The core adapter explicitly simulates Painter's Python API while executing the production operation source. Cases check returned fields, project replacement refusal, busy/closed state, input and output containment, symlink escapes, existing-directory refusal, copy preservation, dry-run defaults, strict export flags, invalid presets, partial failures, and empty texture output. No-argument read tools have no tool-specific invalid inputs.

**Native Painter validation remains pending.** Simulated reports identify their evidence mode and cannot establish installation, API, licensing, or application compatibility. Protocol checks use an isolated unavailable endpoint and never contact a user's Painter session. MCP Eval is deterministic and does not invoke a model; no model-based quality claim is made. Designer remains unsupported.
