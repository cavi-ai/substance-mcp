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
uv run --locked python -m unittest discover -s tests -p test_designer_native.py
CAVI_SAMPLER_NATIVE=1 uv run --locked python -m unittest discover -s tests -p test_sampler_native.py
```

Without `CAVI_SAMPLER_NATIVE=1` the Sampler tests that launch Sampler are skipped.

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

The evaluator is `@cavi-ai/mcp-eval`. All commands run in CI and retain JSON reports under
`.tmp/evals/`. The protocol manifests check discovery size, catalog token budgets,
pagination, surface listing, and protocol negotiation. The core manifests add schema,
structured output, repeated reads, and contention checks.

The corpus exercises all seven Painter tools through real stdio MCP calls and the real
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
