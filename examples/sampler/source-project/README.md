# Source project variants

Copy `job.json` to `jobs/rock-variants.json` in a workspace that already contains
`projects/Rock.ssa` with an asset named `Rock`.

```sh
cavi-substance-sampler-mcp validate jobs/rock-variants.json --check-sampler
cavi-substance-sampler-mcp run jobs/rock-variants.json --json
```

The job copies `projects/Rock.ssa` to `jobs/rock-variants/RockCopy.ssa` before
opening it. The source stays byte-identical. Exports go to
`jobs/rock-variants/unity/Rock/{sbsar,textures}` and
`jobs/rock-variants/blender/Rock/{sbsar,textures}`, with `manifest.json` at the job
root.

`assets` is optional. Provide it to validate step asset references before launch.
For channel changes, use the IDs reported by `sampler_inspect asset`, not the
display labels. Blend mode is read-only after insertion; choose it with
`insert_material`.
