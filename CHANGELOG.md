# Changelog

## Unreleased

### Added

- MIT `LICENSE`.
- Governance files: `SECURITY.md` (private vulnerability reporting and scope), `CONTRIBUTING.md`,
  `.pr-gates` (the commands CI runs), issue forms, a pull request template and a Dependabot
  configuration for `uv`, `npm` and GitHub Actions.
- Trademark and non-affiliation notice in the README.
- Versioned documentation for cavi-ai.xyz in `docs/substance-mcp/source`; the release workflow
  builds, verifies and attaches it to each GitHub release, and the README links to it instead of
  repeating it.
- CI on Linux, macOS and Windows across Python 3.11-3.13 with pinned actions, a lowest-dependency
  leg, CodeQL, a package smoke test (`npm run verify:package`), a tag-driven release workflow
  with PyPI trusted publishing, and a nightly upgrade and vulnerability canary.
- Windows support for Designer and Sampler: default install paths under `Program Files`
  (`%ProgramFiles%` is honoured), `tasklist` to see whether an application is running,
  `taskkill /T /F` to end a timed-out tool and everything it started, and read-only files are
  cleared when a failed output is removed. The fake Designer and Sampler programs install as
  `.cmd` launchers on Windows, so the tests can run there. The Windows CI leg stays
  experimental until it has run green on a real runner.
- Designer and Sampler executables are also found on `PATH`, after an environment override
  and the platform's default install location.
- `CAVI_SAMPLER_SCRIPT_PATH` overrides the startup-scripts folder `sampler_status` reports.
- An import-closure test proves the code that runs inside Painter and Sampler imports only the
  standard library and, for Sampler, no host-side module.

### Changed

- Requires the MCP Python SDK 2.3 or later. The servers use its `MCPServer` and re-raise
  anticipated failures (invalid arguments, refused paths, an unavailable or busy
  application) as tool errors, so their messages still reach the client; other exceptions
  are reported without their details.
- Pillow requires 13.0 or later; the locked 12.3.0 had known vulnerabilities fixed in 13.0.0.
- Package metadata: readme, MIT license expression and license file, authors, keywords,
  classifiers, project URLs and a description naming all three applications. The build
  requires `hatchling>=1.27`.
- The source distribution contains only `src/`, `tests/`, `evals/`, `examples/`, the README,
  `LICENSE`, `CHANGELOG.md` and `pyproject.toml`.
- Ruff checks the `E`, `F`, `I`, `B`, `UP` and `SIM` rule sets, including the 100-character line
  limit (`E501`).
- `.gitignore` covers local plans, worktrees, editor state, logs, build output and Substance
  `.ssa` and `.spt` files.
- Receipt verification is its own command, `cavi-substance-receipts verify` and `chain`
  (module `cavi_substance_mcp.receipts_cli`), shared by all three servers. It replaces
  `cavi-substance-mcp receipts ...`, which is removed; the arguments, output and exit codes
  are unchanged. `npm run verify:package` runs it from the installed wheel.
- The Painter server lives in the `cavi_substance_mcp.painter` subpackage, like Designer and
  Sampler: `server`, `client`, `runtime` and `cli` moved there under the same names, and
  `painter_operations` is now `painter.operations`. `cavi-substance-mcp` runs
  `cavi_substance_mcp.painter.server:main`, and the module to run with `python -m` is
  `cavi_substance_mcp.painter.server`. The old import paths are gone; there are no aliases.

### Changed (breaking)

- The default workspace is `~/Documents/SubstanceMCP`; set `CAVI_SUBSTANCE_WORKSPACE` to keep
  using a previous folder. Only the default is created on
  demand. A workspace named by `CAVI_SUBSTANCE_WORKSPACE` must already exist, and a missing one
  is refused with a message instead of being created. Read-only calls never create it; a Sampler
  call does, because it holds its lock file there.
- `SAMPLER_SCRIPT_PATH` is read as `CAVI_SAMPLER_SCRIPT_PATH` for the scripts folder that
  `sampler_status` reports, with no alias. The variable each launch sets for the Sampler it
  starts is still Adobe's own `SAMPLER_SCRIPT_PATH`.
- The deprecated Designer `sampler` tool is removed, and `designer_status` no longer has a
  `sampler` entry. Use `cavi-substance-sampler-mcp`.
- `designer_status` no longer lists `sbsupdater`, which no operation uses.
- Painter no longer adopts edit-lease state left by lease versions that were never released
  (`_cavi_mcp_lease_v2` and the unversioned attribute).
- Painter: `bake` gains `action="cancel"`. Deleting a node whose subtree holds layers this
  lease did not create requires `confirm_name`. `layer_edit` times out after 120 seconds.
- Sampler: `sampler_build` defaults to `dry_run=true`. `sampler_asset action="delete"`
  requires `confirm=true`, and `sampler_asset` and `sampler_layer` are annotated
  destructive. `sampler_inspect action="project"` requires `project`. `sampler_export`
  rejects `target` with a non-image `format` instead of switching it to PNG. Creating an
  asset whose name already exists is refused.
- Receipts: inputs supplied by an application, such as a Designer built-in template, are
  recorded as schema 2 `app-resource` rows (name, SHA-256 and tool version) instead of
  absolute paths. Receipts without such inputs stay schema 1, and both schemas are read.
  Recording a missing file or a path outside the workspace is an error.
- Sampler job manifests are standard receipts: workspace-relative paths only, per-export
  receipts listed as outputs, and atomic writes. The top-level `app_name`, `workspace` and
  `app.executable` fields are gone.

### Fixed

- Sampler refuses a resource that changed while it was imported instead of reporting it
  verified.
- Batch preflight reports a step that names a duplicated asset as ambiguous.
- Channel packing quantizes each plane straight into the output image and frees sources
  after their last use, and refuses packs that would decode more than 2 GiB.
- Painter accepts schema 2 receipts as `source_manifest` and refuses to record a missing
  output; it now uses the shared receipts module instead of its own drifted copy.
- A Designer or Sampler program whose grandchild kept its output pipe open held the call until
  the timeout and then reported a timeout for a run that had already finished. A call now ends
  when the process exits.
- Designer output that exceeds the limit is no longer silently shortened: a cut `sbsrender`
  report is an error and error messages say when output was dropped.
- A timed-out Designer tool is asked to stop (SIGTERM) before it is killed, like Sampler.
- `runtime start` refuses to launch when it cannot tell whether Painter is already running.
- `serverInfo.version` reports the package version instead of the MCP SDK version.
- Painter tool calls run off the event loop and are serialized, so a long call no longer
  blocks other requests, and a request cancelled while queued is never sent.
- A lost bake end event no longer blocks every later Painter call.
- `.sbsar` imports use the bytes that were hashed.
- `wait_ready` requires that Painter is not baking and no mesh reload is pending.
- Failed exports and `save_copy` calls remove the directory they created.
- Non-ASCII lease ids, enum non-members as `blend_mode`, non-boolean `dry_run` values and
  duplicate names on rename are rejected.
- Painter transport errors and HTTP 5xx responses are reported as unavailable, and each
  request has a wall-clock deadline.
- Sampler batch exports work for asset names that are not already directory-safe.
- The `--version` probe of a Designer or Sampler program waits at most 5 s instead of 20 s, and a
  Sampler launch counts it against the call's timeout instead of adding to it.
- Receipts, batch job files and Painter's `source_manifest` larger than 8 MiB are refused with
  a message instead of being read whole.
- When a failed Designer or Sampler operation cannot remove the output directory it created,
  the error names what is left.
- A cancelled batch job releases the workspace lock and removes its directory.
- Every Sampler launch takes the workspace lock.
- Single Sampler operations edit a copy of the project and replace it only on success.
- A non-zero Sampler exit fails the call.
- Target exports wait for every expected map to be complete, with no file changing size for one
  second, before hashing; a pause in Sampler's flush no longer records a partial file.
- Job validation applies the runtime's naming rules before Sampler launches.
- A job whose input changes while it runs fails.
- A batch job saves its project before an export when it has unsaved changes, so each export
  receipt records the project it was exported from and a new project's first export no longer
  fails on a missing `.ssa`.

## 0.6.0 - 2026-10-30

### Added

- Painter server (`cavi-substance-mcp`) with seven stdio tools: `painter_status`,
  `session_control`, `project`, `inspect`, `layer_edit`, `export_textures` and `bake`. It uses
  Painter's documented remote-scripting API over IPv4 loopback. Tool arguments reach Painter as
  data through an allowlisted set of operations; the server never accepts caller-provided
  Python or JavaScript.
- Painter project operations: `open`, `create` (UDIM, templates, per-fragment tangents), `save`,
  `save_copy`, guarded `close`, `wait_ready` and `reload_mesh`.
- Painter inspection of texture sets, the recursive layer stack with masks, effects and channel
  sources, export presets, shelf resources and bake settings.
- Painter layer authoring: uniform fills, embedded base-color texture fills, groups, masks, smart
  materials, smart masks, generators, `.sbsar` fills, property edits and guarded deletion.
- Painter mesh-map baking with asynchronous status, and texture export with a dry-run default,
  channel packing, resolution, format and bit-depth control, texture-set and UDIM tile
  subsets, and a `manifest.json` receipt.
- Shared application ownership for Painter: `session_control` claims, renews, releases and
  breaks leases, and every mutation requires the `lease_id`. Lease state is versioned and
  migrates from older clients.
- `cavi-substance-mcp runtime start` and `runtime status` launch the installed Painter with
  remote scripting enabled on macOS, without closing open projects.
- Designer server (`cavi-substance-designer-mcp`) with `designer_status`, `material` (`info`,
  `cook`, `render`, `pack`) and `bake`, built on `sbscooker`, `sbsrender` and
  `substance3d_baker`. Each output file is verified and a timeout kills the process group.
- Sampler server (`cavi-substance-sampler-mcp`) with eight tools: `sampler_status`,
  `sampler_inspect`, `sampler_project`, `sampler_asset`, `sampler_layer`, `sampler_export`,
  `sampler_build` and `sampler_pack`. Each call launches Sampler with `--run-script-silent`.
- Engine-agnostic Sampler batch jobs: a JSON job schema, a target registry (`unreal`, `unity`,
  `blender`, `generic`), and the `run`, `validate`, `schema`, `pack` and `doctor` commands of
  the batch CLI. A job writes one atomic directory with a manifest, step timings and
  `progress.jsonl`; `run` takes several jobs with `--continue-on-error`, and concurrent jobs
  serialize on a workspace lock.
- Sampler preflight: `validate --check-sampler`, `--check-project` and `--check-build` (also
  `sampler_build` with `dry_run`) and `doctor --launch` check targets, presets, import options,
  filters, materials, channels and blend parameters before a job runs.
- Sampler source-project jobs that copy an existing `.ssa` before opening it, and authoring
  steps for metadata, channel activation, layer applies-to channels and blend parameters.
- Channel packing for Painter, Designer and Sampler from one shared spec, including the
  standalone `sampler_pack` tool and CLI command.
- Cross-application receipts: file-producing operations in all three servers write a
  `manifest.json` with SHA-256 hashes. `cavi-substance-mcp receipts verify` and
  `receipts chain` check a manifest, every file it records and its provenance links, and
  Painter `sbsar_fill` and Sampler `import_resource` verify a source receipt or hash before
  importing. `examples/pipeline/designer-to-painter/` shows a complete handoff.
- MCP evaluation of all three servers with pinned `@cavi-ai/mcp-eval` 0.8.0: protocol and core
  manifests, an explicitly simulated Painter API, and fake Designer and Sampler executables.
  CI runs the unit tests, Ruff and the six `npm run eval:*` scripts.

### Changed

- The Designer `sampler` tool is deprecated in favor of `cavi-substance-sampler-mcp` and is kept
  for compatibility.
- Painter's transport caches its operation payload in the application by SHA-256, applies
  per-operation timeouts, and separates a timed-out call (outcome unknown) from an unreachable
  application.
- `painter_status` no longer creates the workspace directory.

### Fixed

- Painter texture export supplies the parameters Painter requires.
- The Painter payload is self-contained, so it runs inside the application without importing
  the package.
- Failed `texture_fill` calls clean up their temporary files and report any file that could
  not be removed.
