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

Simulated readiness must score at least 95 for Painter and Sampler, and 80 for Designer.
Protocol, catalog, reliability and coverage each require 100; Painter and Sampler also
require error-honesty 100. Latency is excluded from these floors because CI hosts vary.
Designer's only read-only tool is parameterless, so its standard error-honesty area is
unmeasured and receives zero credit. Mutating tools keep their write annotations.
Catalog byte and token ceilings are enforced in both evaluation modes. Sampler's byte
budget includes the parameter descriptions and conditional project/asset requirements.

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
