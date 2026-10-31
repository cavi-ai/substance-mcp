"""The MCP server type shared by the Painter, Designer and Sampler servers."""

import functools
from importlib.metadata import version

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# Failures a caller can act on: invalid arguments, refused paths, an application that is
# missing, busy or timed out. The SDK hides the text of any other exception, so these are
# re-raised as ToolError. Anything else (TypeError, AttributeError, ...) is a defect and
# keeps its details on the server.
ANTICIPATED = (ValueError, RuntimeError, OSError)


def _report_anticipated(fn):
    @functools.wraps(fn)
    async def tool(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except ANTICIPATED as exc:
            raise ToolError(str(exc)) from exc

    return tool


class Server(MCPServer):
    """MCPServer that reports the package version and anticipated tool failures."""

    def __init__(self, name: str, **kwargs):
        super().__init__(name, version=version("cavi-substance-mcp"), **kwargs)

    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)

        def decorator(fn):
            register(_report_anticipated(fn))
            # The module-level function stays the undecorated coroutine, so direct
            # callers and tests see the original exception types.
            return fn

        return decorator
