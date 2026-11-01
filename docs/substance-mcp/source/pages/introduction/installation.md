# Installation

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a separately installed and
licensed Adobe application. The MCP does not supply an Adobe license or authenticate
accounts.

## Install the servers

Install the three servers as commands on your `PATH`: `cavi-substance-mcp` (Painter),
`cavi-substance-designer-mcp` and `cavi-substance-sampler-mcp`. The same install provides
`cavi-substance-receipts`, which verifies the manifests they write.

```sh
uv tool install git+https://github.com/cavi-ai/substance-mcp
```

Upgrade with `uv tool upgrade cavi-substance-mcp`.

## Register the servers

Register the servers you need in your MCP client. Desktop apps that do not inherit your
shell's `PATH` need absolute paths, which `command -v cavi-substance-mcp` prints.

```json
{
  "mcpServers": {
    "substance-painter": {"command": "cavi-substance-mcp"},
    "substance-designer": {"command": "cavi-substance-designer-mcp"},
    "substance-sampler": {"command": "cavi-substance-sampler-mcp"}
  }
}
```

Tools read and write files only inside the workspace. To use a folder other than the
default, add `"env": {"CAVI_SUBSTANCE_WORKSPACE": "/absolute/path"}` to a server entry.
[Configuration](../reference/configuration.md) lists every variable.

## Start Painter

Launch the installed Painter application with remote scripting enabled:

```sh
cavi-substance-mcp runtime start
cavi-substance-mcp runtime status
```

Automatic launch currently supports macOS. It finds the standard Adobe installation,
or the app bundle selected by `CAVI_PAINTER_APP`, and supplies `--enable-remote-scripting`.
A connected session is reused. An open app without a reachable scripting connection
is left untouched; save and quit that app before launching it again. The launcher does
not install software, sign in, accept terms, or dismiss license prompts.

`start` reports that launch was requested; it does not claim Painter is ready. Require
`connected=true` from `status` or the actual `painter_status` MCP tool before project work.
Commands print JSON and exit nonzero on failure. Running `cavi-substance-mcp` without
arguments continues to serve MCP over stdio.

Adobe documents the protocol in its [remote control guide](https://experienceleague.adobe.com/en/docs/substance-3d-dev/painter-python/tutorials/remote-control).

Designer and Sampler need no launch step: their servers start the application's
command-line tools for each call. See [Designer tools](../designer/tools.md) and
[Sampler tools](../sampler/tools.md).

## Work from a checkout

To work from a source checkout, see `CONTRIBUTING.md` in the repository.
