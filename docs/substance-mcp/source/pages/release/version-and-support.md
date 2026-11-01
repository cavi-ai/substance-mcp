# Version and support

This documentation is for `cavi-substance-mcp` {{PRODUCT_VERSION}}, released as
`{{RELEASE_TAG}}` from commit `{{RELEASE_COMMIT}}`.

The project uses Semantic Versioning. Before 1.0, the tool surface, action parameters and
result fields can still change. Every change is recorded in the repository
[CHANGELOG.md](https://github.com/cavi-ai/substance-mcp/blob/main/CHANGELOG.md).

## Checked applications

- Painter 12.2.0 on macOS: native project creation, texture-set and preset inspection,
  uniform fill creation, texture export, save-copy/reopen, and guarded closing.
- Sampler 6.0.4 on macOS: project creation, source-project copies and preflight, metadata,
  channel activation, layer applies-to channels, blend parameters, preset application,
  layer removal, B2M image import, Unreal/Unity/Blender target exports and manifest
  generation.
- Windows support for Designer and Sampler is written but has not run on Windows yet.

See the [Overview](../introduction/overview.md) for what was checked natively and what was
tested only against a simulator.

## License and trademarks

MIT licensed.

Adobe, Substance 3D, Substance 3D Painter, Substance 3D Designer and Substance 3D Sampler
are either registered trademarks or trademarks of Adobe in the United States and/or other
countries. This project is not affiliated with, endorsed by, or sponsored by Adobe. It
does not include or redistribute Adobe software, templates, presets or Substance files;
you supply your own licensed applications. Other product names (for example Unreal Engine,
Unity and Blender) belong to their respective owners.
