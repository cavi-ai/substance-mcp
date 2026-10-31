# Receipts

Every file-producing operation writes `manifest.json` into its new output directory.
The receipt records the app, operation, parameters, input files and output files with
sizes and SHA-256 hashes.

```json
{"schema": 1, "app": "designer", "operation": "cook",
 "inputs": [{"path": "source.sbs", "size": 10, "sha256": "..."}],
 "outputs": [{"path": "cooked/source.sbsar", "size": 20, "sha256": "..."}],
 "parameters": {"source": "templates/..."}}
```

- Designer: `cook`, `render`, `pack`, `bake`.
- Painter: `export_textures`, `save_copy`; exports can record `source_manifest` +
  `source_file` provenance. `sbsar_fill` results record the imported `.sbsar` as
  `source` and set `verified` when `source_manifest` or `expected_sha256` was checked.
- Sampler: job manifests use `app_name` (`app` already holds the installation);
  `sampler_export`, `sampler_project create/save_as` and `sampler_pack` output use
  the shared fields.

## Verify a receipt

Verify a manifest and every file it records:

```sh
cavi-substance-receipts verify handoff/designer/manifest.json
cavi-substance-receipts verify handoff/designer/manifest.json \
  --file handoff/designer/rock.sbsar --role outputs --json
```

## Verify a chain

A manifest can carry `provenance` links to earlier manifests. Painter export
manifests record the verified `.sbsar` manifest and file when `source_manifest` and
`source_file` are supplied. Verify the whole chain, including those links:

```sh
cavi-substance-receipts chain handoff/painter/manifest.json
```

`cavi-substance-receipts chain` verifies every manifest, every recorded file, and each
provenance link's source file. The repository's `examples/pipeline/designer-to-painter/`
is a complete handoff.
