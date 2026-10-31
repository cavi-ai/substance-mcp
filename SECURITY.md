# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's private vulnerability reporting:
<https://github.com/cavi-ai/substance-mcp/security/advisories/new>. Do not open a public
issue. Include affected versions, reproduction steps, impact, and any suggested mitigation.

Do not include credentials, Adobe account details, or Substance project files (`.spp`,
`.sbs`, `.sbsar`, `.ssa`) in a report. Use synthetic examples whenever possible.

## Supported versions

Until the project reaches 1.0, security fixes are applied to the latest minor release and
the `main` branch.

## Scope

- Painter's remote-scripting endpoint listens on `127.0.0.1:60041` (`CAVI_PAINTER_PORT`
  selects another port) and executes arbitrary Python for any local process that can reach
  it. The lease from `session_control` and the workspace sandbox coordinate MCP clients;
  they are not a security boundary against other local processes. Run Painter with remote
  scripting only on a trusted workstation. The server connects to IPv4 loopback only.
- File arguments to the MCP tools resolve inside `CAVI_SUBSTANCE_WORKSPACE`, including
  through symbolic links, and outputs go to new directories only. A path that escapes the
  workspace through `..`, an absolute path or a symbolic link is in scope. Designer's
  `builtin` argument is the one exception: it names a template or package in the Designer
  installation through the fixed `templates/<name>` or `packages/<name>` pattern.
- The servers execute no caller-supplied code. Painter tool arguments reach the application
  as JSON data through a fixed set of operations. Designer and Sampler tools run Adobe
  command-line tools and a driver script shipped in this package, with arguments passed as
  argument lists and no shell. Which Adobe executables run is set by the operator's
  environment variables (`CAVI_DESIGNER_BIN_DIR`, `CAVI_DESIGNER_APP`, `CAVI_SAMPLER_BIN`,
  `CAVI_SAMPLER_APP`, `CAVI_PAINTER_APP`), never by tool arguments.
- The servers speak MCP over stdio. Their only network connection is Painter's loopback
  endpoint. They send no telemetry, upload nothing, and use no accounts or logins.
- Adobe applications, their licenses, and vulnerabilities in Adobe software are out of
  scope. Report those to Adobe.
