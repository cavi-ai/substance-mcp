# Contributing

## Setup

Install Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Node.js 22, then:

```bash
uv sync --locked --dev
npm ci
```

No Adobe application is required. The unit tests and evaluations run against a simulated
Painter API and fake Designer and Sampler executables.

## Checks

Run every command in [`.pr-gates`](.pr-gates) before opening a pull request. The main ones:

```bash
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
npm run eval:protocol
npm run eval:core
npm run eval:designer-protocol
npm run eval:designer-core
npm run eval:sampler-protocol
npm run eval:sampler-core
npm run verify:package
```

Tests marked native skip unless the application is installed (`CAVI_SAMPLER_NATIVE=1` also
launches Sampler). Evaluation reports are written under `.tmp/evals/`. `verify:package` builds the wheel and
sdist, installs the wheel into a clean virtual environment and checks every console script.

## Where changes go

- Painter server: `src/cavi_substance_mcp/server.py` holds the MCP tool schemas,
  `painter_operations.py` the operations that run inside Painter's Python interpreter,
  and `client.py` the loopback transport. `runtime.py` and `cli.py` hold the launcher and
  the `receipts` commands.
- Designer server: `src/cavi_substance_mcp/designer/`. `server.py` holds the tools,
  `operations.py` the workspace-confined operations and `runner.py` the tool lookup and
  report parsing; subprocesses run through `core/proc.py`.
- Sampler server and batch CLI: `src/cavi_substance_mcp/sampler/`. `server.py` holds the
  tools, `operations.py` the scripts that run inside Sampler's embedded Python, `launch.py`
  the host side that starts Sampler and reads the response, `job.py` the batch job model,
  `batch.py` and `cli.py` the batch runner, and `targets.py` the engine targets.
- Shared code: `core/` for what every server uses (`workspace.py` holds the workspace
  root and path rules, `proc.py` the bounded subprocess runner, `discovery.py` how the
  Adobe executables are found, versioned and checked for a running process), `receipts.py` for
  receipts and `packing.py` for channel packing.
- Code that runs inside an application (`painter_operations.py`, and `receipts.py`,
  `core/workspace.py`, `sampler/operations.py`, `sampler/names.py` and
  `sampler/targets.py` inside Sampler) is standard library only and never imports a
  host-side module. `tests/test_import_closure.py` enforces this.
- `evals/`: `painter_simulator.py` (simulated Painter API), `designer_fake_cli.py`,
  `sampler_fake_cli.py` (fake executables, installed by `launcher.py` as shebang scripts on
  POSIX and `.cmd` wrappers on Windows; tests point discovery at them with
  `CAVI_DESIGNER_BIN_DIR` and `CAVI_SAMPLER_BIN`), the corpora
  (`cases.json`, `designer_cases.json`, `sampler_cases.json`), the mcp-eval manifests
  (`*.manifest.json`) and the runners (`run.py`, `designer_run.py`, `sampler_run.py`).
- `tests/`: unit tests. `examples/`: synthetic job files and handoff walkthroughs.
- Documentation: `README.md`. Update it and `CHANGELOG.md` when a tool, receipt or job
  contract changes.

## Rules for changes

- Every change ships with a test that fails without it.
- A tool that changes an application or writes files requires a lease (Painter `lease_id`) or
  defaults to a dry run, and writes only to new directories inside the workspace.
- File arguments stay inside `CAVI_SUBSTANCE_WORKSPACE`. Tool arguments are data; never build
  code or a shell command from them.
- A claim of native verification needs evidence and the application version it was checked
  against. Results from the simulator or the fakes do not establish compatibility.
- Do not commit Adobe assets, templates, presets or Substance files (`.sbs`, `.sbsar`, `.ssa`,
  `.spp`, `.spt`). Use synthetic fixtures only.
- Do not commit local plans, transcripts, credentials, or machine-specific paths.
- Keep a pull request to one concern.

## Commits

Use Conventional Commit prefixes: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `build:`, `test:`, `ci:`.

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
