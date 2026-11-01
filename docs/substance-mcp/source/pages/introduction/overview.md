# Overview

Cavi Substance MCP is a set of MCP servers for Adobe Substance 3D Painter, Adobe
Substance 3D Designer and Adobe Substance 3D Sampler. Each application has its own stdio
server, and all three share one asset workspace, so the output of one application is an
input to the next.

| Server | Application | How it drives the application |
| --- | --- | --- |
| `cavi-substance-mcp` | Painter | Painter's documented Python remote scripting API, over `127.0.0.1` |
| `cavi-substance-designer-mcp` | Designer | The headless CLIs `sbscooker`, `sbsrender` and `substance3d_baker` |
| `cavi-substance-sampler-mcp` | Sampler | One `--run-script-silent` launch per tool call or batch job |

The same install provides `cavi-substance-receipts`, which verifies the manifests the
servers write. See [Receipts](../guides/receipts.md).

## Application support

The Painter server supports project operations (UDIM, templates, mesh reload), layer-tree
inspection, fill, group, mask, generator, smart-material, smart-mask and `.sbsar` layers,
mesh-map baking, shelf search, and channel-packed texture export. It does not provide brush
strokes; Designer's CLIs are served separately.

Native project creation, texture-set and preset inspection, uniform fill creation, texture
export, save-copy/reopen, and guarded closing have been checked with Painter 12.2.0 on
macOS. Exported color, roughness, and metallic pixel values were checked against the fill
inputs. Automated transport and path tests use an explicitly simulated API and do not
establish compatibility with other Painter versions.

Baking, layer actions beyond `fill`/`texture_fill`, shelf search, mesh reload and custom
export maps were checked against Painter's documented Python API and a read-only attribute
probe, then tested only against the simulator; they have not been run natively.

Designer and Sampler discovery, process checks and process termination are written for
Windows as well as macOS and Linux (default install paths under `Program Files`,
`tasklist`, `taskkill`). They have not run on Windows yet; the Windows CI leg is
experimental until it has run green on a real runner.

This documentation is for version {{PRODUCT_VERSION}}, released as `{{RELEASE_TAG}}`.
