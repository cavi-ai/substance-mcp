# Multiangle to material

Copy `job.json` to `jobs/multiangle-material.json` in a workspace containing
`photos/angle0.png` and `photos/angle1.png` (2–8 angles), then run:

```sh
cavi-substance-sampler-mcp validate jobs/multiangle-material.json --check-sampler
cavi-substance-sampler-mcp run jobs/multiangle-material.json --json
```

The job creates an empty material, imports the angles with
`multiangle_to_material`, adds Make It Tile, then exports Unreal maps and `.sbsar`
into `jobs/multiangle-material/`.

Filter names are resolved against the installed Sampler. Check them with
`sampler_inspect catalog` or `validate --check-sampler`.
