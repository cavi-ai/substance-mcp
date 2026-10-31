# AI image to material

Copy `job.json` to `jobs/ai-material.json` in a workspace containing
`photos/albedo.png`, then run:

```sh
cavi-substance-sampler-mcp validate jobs/ai-material.json --check-sampler
cavi-substance-sampler-mcp run jobs/ai-material.json --json
```

The job creates an empty material, imports the image with the AI-powered Image to
Material option, adds Delighter and Make It Tile, then exports Unreal maps and
`.sbsar` into `jobs/ai-material/`.

Filter names are resolved against the installed Sampler. Check them with
`sampler_inspect catalog` or `validate --check-sampler`.
