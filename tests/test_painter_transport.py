"""Painter HTTP transport: payload loading, malformed replies, deadlines and readiness."""

import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
import unittest

from cavi_substance_mcp.client import PainterClient, PainterTimeout, PainterUnavailable


class PayloadSourceTests(unittest.TestCase):
    def test_payload_source_is_read_as_utf8(self):
        script = textwrap.dedent("""
            import json
            from pathlib import Path

            seen = []
            real = Path.read_text

            def spy(self, *args, **kwargs):
                seen.append([self.name, kwargs.get("encoding", args[0] if args else None)])
                return real(self, *args, **kwargs)

            Path.read_text = spy
            import cavi_substance_mcp.client  # noqa: F401
            print(json.dumps(seen))
        """)
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True)
        reads = dict(json.loads(done.stdout.splitlines()[-1]))
        self.assertEqual(reads["painter_operations.py"], "utf-8")


if __name__ == "__main__":
    unittest.main()
