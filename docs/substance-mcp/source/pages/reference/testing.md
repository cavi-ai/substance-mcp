# Testing and evaluation

## Unit tests and lint

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
```

Native acceptance requires a licensed Painter session and a disposable project: inspect
status and texture sets, list presets, preview/export into a fresh directory, and inspect
the produced textures in the destination game.

Native Designer and Sampler tests skip without the application:

```sh
CAVI_DESIGNER_NATIVE=1 uv run --locked python -m unittest discover -s tests -p test_designer_native.py
CAVI_SAMPLER_NATIVE=1 uv run --locked python -m unittest discover -s tests -p test_sampler_native.py
```

Without `CAVI_SAMPLER_NATIVE=1` the Sampler tests that launch Sampler are skipped.

## MCP evaluation

```sh
uv sync --locked --dev
npm install
npm run eval:protocol
npm run eval:core
npm run eval:designer-protocol
npm run eval:designer-core
npm run eval:sampler-protocol
npm run eval:sampler-core
```

Local installation follows `github:cavi-ai/mcp-eval#main`. Its npm wrapper downloads the
released native evaluator; it does not compile unreleased changes. CI builds the native
evaluator from the same repository's `main` branch and selects it with
`MCPEVAL_EXECUTABLE`. Set that variable to use a source-built evaluator locally.
Reports under `.tmp/evals/` include the binary version and SHA-256; CI also retains the
resolved source revision and Cargo installation metadata.

The protocol manifests check discovery size, catalog token budgets,
pagination, surface listing, and protocol negotiation. The core manifests add schema,
structured output, repeated reads, and contention checks. Sampler additionally checks
catalog inspection after rejected project inputs. Both Designer and Sampler require
explicit tool errors for missing project/material inputs; unit tests check their
structured rejection fields through the MCP boundary.

Every server must score at least 95. Protocol, catalog, error-honesty, reliability and
coverage each require 100. Parameterless tools reject unexpected arguments, making their
invalid-input behavior measurable. Latency is excluded from these floors because CI
hosts vary. Catalog byte and token ceilings apply in both evaluation modes, including
Sampler's 14,000-byte ceiling. Parameter descriptions and typed validation remain present.

### Installed-application evaluation

On a host with the licensed Designer command-line tools installed:

```sh
npm run eval:designer-native
```

This route runs only headless cooking, rendering, packing and mesh-map baking acceptance
tests, then read-only MCP probes. It does not start the Designer GUI. Skipped acceptance
tests fail the lane. Reports include installed CLI versions, per-test outcomes and raw
native test logs. The command is separate from portable protocol/simulated CI lanes.

GUI SDK batches, Painter controls and Sampler startup tests require separate native
capabilities; this command does not establish their behavior. Set
`CAVI_NATIVE_EVIDENCE_DIR` when running native tests to retain failed fixtures at their
original paths. Successful disposable fixtures are removed.

The corpus exercises the portable Painter tools through real stdio MCP calls and the real
loopback transport. The core adapter explicitly simulates Painter's Python API while
executing the production operation source. Cases check returned fields, project
replacement refusal, busy/closed state, input and output containment, symlink escapes,
existing-directory refusal, copy preservation, dry-run defaults, strict export flags,
invalid presets, partial failures, and empty texture output. Layer cases cover duplicate
names, bounded colors, assignment rollback, nesting, masks, shelf resolution, property
edits, guarded deletion, and project readiness. Bake cases cover parameter mapping, async
status, launch rollback and missing parameters; export cases cover channel packing and
refused inversions. No-argument read tools have no tool-specific invalid inputs.

The Designer and Sampler evals run against fake CLIs (`evals/designer_fake_cli.py`,
`evals/sampler_fake_cli.py`).

Simulated reports identify their evidence mode and cannot establish installation, API,
licensing, or application compatibility. Protocol checks use an isolated unavailable
endpoint and never contact a user's Painter session. Native export checks establish file
generation, not painted-asset quality or destination-game material compatibility. MCP Eval
is deterministic and does not invoke a model; no model-based quality claim is made.

Native brush pixel changes and saved-project persistence require an installed macOS Painter; simulator gesture and contract checks do not establish painted output.
