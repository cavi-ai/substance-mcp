# Changelog

## Unreleased

### Added

- MIT `LICENSE`.
- Governance files: `SECURITY.md` (private vulnerability reporting and scope), `CONTRIBUTING.md`,
  `.pr-gates` (the commands CI runs), issue forms, a pull request template and a Dependabot
  configuration for `uv`, `npm` and GitHub Actions.
- Trademark and non-affiliation notice in the README.
- CI on Linux, macOS and Windows across Python 3.11-3.13 with pinned actions, a lowest-dependency
  leg, CodeQL, a package smoke test (`npm run verify:package`), a tag-driven release workflow
  with PyPI trusted publishing, and a nightly upgrade and vulnerability canary.

### Changed

- Pillow requires 13.0 or later; the locked 12.3.0 had known vulnerabilities fixed in 13.0.0.
- Package metadata: readme, MIT license expression and license file, authors, keywords,
  classifiers, project URLs and a description naming all three applications. The build
  requires `hatchling>=1.27`.
- The source distribution contains only `src/`, `tests/`, `evals/`, `examples/`, the README,
  `LICENSE`, `CHANGELOG.md` and `pyproject.toml`.
- Ruff checks the `E`, `F`, `I`, `B`, `UP` and `SIM` rule sets (line length is enforced after the
  pre-release format pass).
- `.gitignore` covers local plans, worktrees, editor state, logs, build output and Substance
  `.ssa` and `.spt` files.

### Fixed

- `serverInfo.version` reports the package version instead of the MCP SDK version.

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
