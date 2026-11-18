"""Install mcp-core's standard-library modules inside an application's embedded Python.

Painter and Sampler run our code in their own interpreters, which cannot import packages
from this server's environment. ``bootstrap`` returns source text that registers these
modules under their ``cavi_mcp_core`` names, built from the installed package, so code
running in the application imports the same code the host runs. Host side only; standard
library.
"""

import importlib
from pathlib import Path

MODULES = ("files", "journal", "workspace")

# A process that imported the real package keeps it; otherwise the modules are installed
# from source, replacing any an older payload installed.
INSTALL = """\
import sys as _sys
import types as _types
_package = _sys.modules.get("cavi_mcp_core")
if _package is None:
    _package = _types.ModuleType("cavi_mcp_core")
    _package.__path__ = []
    _sys.modules["cavi_mcp_core"] = _package
for _name, _source in {sources!r}.items():
    if _package.__spec__ is not None:
        __import__("cavi_mcp_core." + _name)
    else:
        _module = _types.ModuleType("cavi_mcp_core." + _name)
        exec(compile(_source, "cavi_mcp_core/" + _name + ".py", "exec"), _module.__dict__)
        _sys.modules[_module.__name__] = _module
        setattr(_package, _name, _module)
        del _module
del _sys, _types, _package, _name, _source
"""


def sources(names=MODULES) -> dict[str, str]:
    """The installed source text of each named ``cavi_mcp_core`` module."""
    return {
        name: Path(importlib.import_module(f"cavi_mcp_core.{name}").__file__).read_text(
            encoding="utf-8")
        for name in names
    }


def bootstrap(names=MODULES) -> str:
    """Source text that installs the named ``cavi_mcp_core`` modules when it runs."""
    return INSTALL.format(sources=sources(names))
