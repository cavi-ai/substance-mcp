# Configuration

All configuration is through environment variables on the server entries in your MCP
client, for example `"env": {"CAVI_SUBSTANCE_WORKSPACE": "/absolute/path"}`.

| Variable | Server | Default | Meaning |
| --- | --- | --- | --- |
| `CAVI_SUBSTANCE_WORKSPACE` | all | `~/Documents/SubstanceMCP` | Asset workspace every tool reads from and writes to |
| `CAVI_PAINTER_PORT` | Painter | `60041` | Painter's local scripting port |
| `CAVI_PAINTER_APP` | Painter | standard Adobe installation | Painter app bundle that `runtime start` launches |
| `CAVI_DESIGNER_BIN_DIR` | Designer | — | Folder holding `sbscooker`, `sbsrender` and `substance3d_baker` |
| `CAVI_DESIGNER_APP` | Designer | standard install dir | Designer `.app` path, used when `CAVI_DESIGNER_BIN_DIR` is unset |
| `CAVI_SAMPLER_BIN` | Sampler | — | Exact Sampler executable |
| `CAVI_SAMPLER_APP` | Sampler | standard install path | Sampler `.app` path, used when `CAVI_SAMPLER_BIN` is unset |
| `CAVI_SAMPLER_SCRIPT_PATH` | Sampler | Sampler's per-user scripts folder | Startup-scripts folder that `sampler_status` reports |

## Workspace

The default workspace is created the first time a tool writes to it. A workspace you set
yourself must already exist; a missing one is refused instead of created. Read-only tools
never create it; the one exception is that a Sampler call holds its lock file in the
workspace, so it creates the default first. Inputs and outputs stay inside it, including
through symbolic links. Export and copy operations require a new destination directory and
refuse existing ones. All three servers share the one workspace, so the output of one
application is an input to the next.

## Painter connection

Connections use `127.0.0.1` directly, without HTTP proxies or redirects. See the
[Security model](../security/model.md).

## Application discovery

- Designer binaries: `CAVI_DESIGNER_BIN_DIR`, else `CAVI_DESIGNER_APP`, else the standard
  install dir for macOS, Windows or Linux, else `sbsrender` on `PATH`. An override is
  final: a wrong one is reported as not found, not replaced.
- Sampler executable: `CAVI_SAMPLER_BIN`, else `CAVI_SAMPLER_APP`, else the standard
  install path for macOS, Windows or Linux, else the executable on `PATH`. Each Sampler
  launch also sets Adobe's own `SAMPLER_SCRIPT_PATH` for the Sampler it starts; that
  variable is Adobe's, not ours.
