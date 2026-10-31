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
 "sbsar": "handoff/designer/01_02_pbr_metallic_roughness.sbsar"}
```

The result contains `source` with the same path and SHA-256 as the Designer output.

3. Export textures from Painter:

```json
{"directory": "handoff/painter", "preset": "resource://...", "dry_run": false}
```

The export directory contains `manifest.json` with output hashes. Compare the Painter
`source.sha256` with the Designer `outputs[].sha256` to verify the handoff.

A Sampler job that exports `.sbsar` can feed the same Painter step; compare the
Sampler manifest output hash with the Painter `source.sha256`.
