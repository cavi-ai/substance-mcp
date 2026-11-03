# Channel packing

Painter `export_textures` maps, Designer `material pack` and Sampler `sampler_pack`
share one channel packing spec:

```json
{"maps": [
  {"name": "T_Rock_MaskMap", "format": "png", "bit_depth": 16,
   "channels": {"R": "metallic", "G": "ambientOcclusion", "B": "0", "A": "1-roughness"}},
  {"name": "T_Rock_BaseColor", "channels": {"RGB": "baseColor", "A": "opacity"}},
  {"name": "T_Rock_Normal", "channels": {"RGB": "normal"}}],
 "defaults": {"opacity": 1}}
```

- Sources: usage, `1-<usage>`, or a constant `0..1`. Missing usages need `defaults`.
- No `A` key writes RGB. All sources share one resolution. Gray from RGB uses R.
- Packing defaults to 16-bit PNG. Formats are `png`, `tga`, `tif`; 16-bit packing currently uses PNG. Choose 8-bit output explicitly for a runtime texture that requires it.
- Choose output bit depth to preserve the contributing sources. A 16-bit PNG pack
  retains 16-bit RGB/RGBA source samples, including interlaced sources; writing
  16-bit output from an 8-bit source does not restore missing precision.

Painter maps its own sources onto this spec; see
[Painter tools](../painter/tools.md#export) for the document and mesh map names it accepts.

## Sampler packing

`sampler_pack` packs workspace maps into a new directory. The CLI form reads a JSON file
with `directory`, `sources` and `pack`:

```sh
cavi-substance-sampler-mcp pack pack.json --json
```

Packing runs on the host, not inside Sampler. The Designer `material pack` action uses
the same spec.
