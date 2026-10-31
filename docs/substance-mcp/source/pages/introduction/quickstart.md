# Quickstart

This walk-through uses the Painter server. Start Painter with
`cavi-substance-mcp runtime start` and register the server as described in
[Installation](installation.md).

## Check the connection

Call `painter_status` first. It returns `connected`, `open`, `file`, `needs_saving`,
`busy`, `edition_state`, `baking`, `mesh_reload`, `workspace` and `workspace_exists`.

## Claim the session

Before editing, call `session_control` with `action="inspect"`, then claim the
returned `project_id` with `action="claim"`, an `owner` label, and that exact
`expected_project`. Claim returns a private `lease_id`; pass it on application
mutations. Read-only inspection remains available without ownership.

## Open a project and wait

After `open`/`create`/`reload_mesh`, call `project` with `action="wait_ready"` before
editing. Loading is asynchronous.

## Preview an export

`export_textures` previews by default; only `dry_run=false` writes, and it writes into a
new workspace directory. Every file-producing operation writes a `manifest.json` receipt
into its new output directory; see [Receipts](../guides/receipts.md).

[Painter tools](../painter/tools.md) lists every tool, action and parameter, and
[Shared application ownership](../painter/session-ownership.md) covers leases in full.
