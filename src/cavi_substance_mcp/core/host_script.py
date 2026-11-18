"""One request through a script that runs inside an application's own Python.

The host writes a request file and a driver script; the application runs the script, which
imports ``run`` from the named in-app module, calls it with the request and writes a reply
envelope: ``{"ok": true, "result": ...}`` or ``{"ok": false, "error": "Type: message"}``.
Host side only.
"""

import json
from pathlib import Path

from cavi_mcp_core.files import parse_json

from . import embedded

# The largest reply read back from an application.
RESPONSE_LIMIT = 8 * 1024 * 1024
# Error types a reply may name that are raised again as themselves on the host.
ERRORS = (ValueError, FileExistsError, TypeError, RuntimeError)
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])

DRIVER = '''import json
import sys
from pathlib import Path

{bootstrap}
PACKAGE_ROOT = {package_root!r}
REQUEST = Path({request!r})
RESPONSE = Path({response!r})


def main():
    data = json.loads(REQUEST.read_text(encoding="utf-8"))
    try:
        sys.path.insert(0, PACKAGE_ROOT)
        from {module} import run

        text = json.dumps({{"ok": True, "result": run(data)}}, allow_nan=False, default=str)
    except BaseException as exc:
        text = json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}})
    RESPONSE.write_text(text, encoding="utf-8")


main()
'''


def prepare(script: Path, request: Path, response: Path, module: str, payload) -> None:
    """Write the request and a driver script that answers it with ``module.run``."""
    request.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    script.write_text(DRIVER.format(
        bootstrap=embedded.bootstrap(), package_root=PACKAGE_ROOT, request=str(request),
        response=str(response), module=module), encoding="utf-8")


def envelope(response: Path, app: str) -> dict:
    """The reply envelope the driver wrote; RuntimeError if it is too large or unreadable."""
    if response.stat().st_size > RESPONSE_LIMIT:
        raise RuntimeError(f"{app} response exceeds size limit")
    try:
        value = parse_json(response.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{app} returned an unreadable response") from exc
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise RuntimeError(f"{app} returned an unexpected response")
    return value


def raise_error(detail: str):
    """Raise the error a reply reports, as its own type when it is one of ``ERRORS``."""
    for kind in ERRORS:
        prefix = f"{kind.__name__}: "
        if detail.startswith(prefix):
            raise kind(detail[len(prefix):])
    raise RuntimeError(detail)
