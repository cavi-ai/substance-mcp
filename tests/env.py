"""Process environments for tests.

Most tests that drive a fake Designer or Sampler need the real environment minus every
variable that steers discovery, plus a few of their own. ``patched_environment`` is that
as an unstarted ``patch.dict``; ``scrubbed_environment`` is the plain dict.
"""

import os
from unittest.mock import patch

DESIGNER = ("CAVI_DESIGNER",)
SAMPLER = ("CAVI_SAMPLER", "SAMPLER_")
FAKE_SAMPLER = (*SAMPLER, "FAKE_SAMPLER")


def scrubbed_environment(drop_prefixes=(), add=None, drop=()) -> dict:
    """``os.environ`` without the variables named in ``drop`` or starting with one of
    ``drop_prefixes``, then updated with ``add``."""
    prefixes = tuple(drop_prefixes)
    environment = {key: value for key, value in os.environ.items()
                   if key not in drop and not key.startswith(prefixes)}
    environment.update(add or {})
    return environment


def patched_environment(drop_prefixes=(), add=None, drop=()):
    """``patch.dict`` that replaces ``os.environ`` with ``scrubbed_environment(...)``.

    It is not started: use it in a ``with`` or call ``start()`` and ``stop()``.
    """
    return patch.dict(os.environ, scrubbed_environment(drop_prefixes, add, drop), clear=True)
