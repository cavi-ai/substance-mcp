# Photo to PBR

Copy `job.json` to `jobs/photo-to-pbr.json` in a workspace that contains
`photos/albedo.png` and an existing `jobs/` directory, then run:

```sh
cavi-substance-sampler-mcp validate jobs/photo-to-pbr.json
cavi-substance-sampler-mcp validate jobs/photo-to-pbr.json --check-sampler
cavi-substance-sampler-mcp run jobs/photo-to-pbr.json --json
```

The job writes `jobs/photo-to-pbr/` with the project, `exports/PhotoPBR/sbsar/`,
`exports/PhotoPBR/textures/` and `manifest.json`. The job directory must not exist.

Change only the export target to use a different engine profile:

| Target | Default preset | Texture output |
| --- | --- | --- |
| `unreal` | Unreal Engine 4 | basecolor, normal, packed ORM |
| `unity` | Unity HD Render Pipeline Standard | basecolor, normal, mask map |
| `blender` | Blender Cycles/Eevee | basecolor, normal, roughness, metallic |
| `generic` | none | caller-selected channels |

`preset` on the export step overrides the target default. `outputs` can be
`["sbsar"]` or `["textures"]` when only one kind is needed.

The B2M option name is resolved by Sampler and may vary by version. If it is
rejected, use `use_as_bitmap` and insert filters explicitly.
