# Designer to Painter handoff

1. Cook a Designer material into a new workspace directory:

```json
{"action": "cook", "builtin": "templates/01_02_pbr_metallic_roughness",
 "directory": "handoff/designer"}
```

The result contains `sbsar` and `manifest`. The manifest records the `.sbsar`
SHA-256.

2. Open a Painter project and apply the `.sbsar`:

```json
{"action": "sbsar_fill", "texture_set": "Body", "name": "Designer material",
 "sbsar": "handoff/designer/01_02_pbr_metallic_roughness.sbsar",
 "source_manifest": "handoff/designer/manifest.json"}
```

The result contains `source` with the same path and SHA-256 as the Designer output,
plus `verified: true`. A mismatch fails before Painter imports the resource.

3. Export textures from Painter:

```json
{"directory": "handoff/painter", "preset": "resource://...",
 "source_manifest": "handoff/designer/manifest.json",
 "source_file": "handoff/designer/01_02_pbr_metallic_roughness.sbsar",
 "dry_run": false}
```

The export directory contains `manifest.json` with output hashes and a `provenance`
link to the Designer manifest and `.sbsar`. Verify the complete chain:

```sh
cavi-substance-receipts chain handoff/painter/manifest.json
```

A Sampler job that exports `.sbsar` can feed the same Painter step; compare the
Sampler manifest output hash with the Painter `source.sha256`.
