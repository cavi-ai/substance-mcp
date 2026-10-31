# Shared application ownership

Before editing, call `session_control` with `action="inspect"`, then claim the
returned `project_id` with `action="claim"`, an `owner` label, and that exact
`expected_project`. Claim returns a private `lease_id`; pass it on application
mutations. Read-only inspection remains available without ownership.

Claims last 30–3600 seconds, default 600. Renew with `action="renew"` and release
with `action="release"`, both supplying the same `lease_id`. `action="break"` with
`expected_project` clears a lease unused for at least 300 seconds and returns
`previous_owner`. Release and expiry never save, close, or discard a project.
Conflicting claims and missing, foreign, expired, or project-mismatched edit tokens are
refused. An owned operation holds its exclusive section until it finishes. A successful
owned save or project transition updates the claim's binding.

Claims coordinate participating MCP clients, not direct human edits or arbitrary
scripts. Upgrade every participating client. The lease object is versioned; a newer
client migrates an older client's lease state instead of reusing its code. Keep lease IDs
out of asset receipts and handoff documents; a new agent inspects and claims its own
session.

Project identity includes Painter's process, project UUID, and file path, so
separate unsaved projects are distinguished. Project creation and opening remain
asynchronous; inspect loading completion before continuing. If that changes the
binding after the call returns, release and claim the inspected new project.
The existing unsaved-close guard remains in force.
