# Security model

## Painter scripting endpoint

`CAVI_PAINTER_PORT` selects Painter's local scripting port, defaulting to `60041`.
Connections use `127.0.0.1` directly, without HTTP proxies or redirects. Painter's own
scripting endpoint permits arbitrary scripts; keep it private to a trusted local
workstation. This MCP exposes only the listed operations and never accepts
caller-provided Python or JavaScript. The operation payload is sent once and cached in
Painter by SHA-256; later calls send a small bootstrap.

## Files

Tools read and write only inside the workspace, including through symbolic links.
Outputs go to new directories, and failed calls remove the directories they created.
Every file-producing operation writes a [receipt](../guides/receipts.md) with the SHA-256
of each input and output.

## No background activity

There are no automatic retries, external uploads, telemetry or account logins.
Application launch is explicit through the CLI. The MCP does not supply an Adobe license,
authenticate accounts, install software, accept terms, or dismiss license prompts.

## Shared sessions

Painter edits need a lease from `session_control`; see
[Shared application ownership](../painter/session-ownership.md). Claims coordinate
participating MCP clients, not direct human edits or arbitrary scripts.

## Reporting a vulnerability

Report vulnerabilities privately as described in `SECURITY.md` in the repository.
