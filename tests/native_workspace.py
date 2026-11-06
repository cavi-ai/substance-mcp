"""Disposable native fixtures, with opt-in retention of failed application evidence."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


@contextlib.contextmanager
def native_workspace():
    evidence = os.environ.get('CAVI_NATIVE_EVIDENCE_DIR')
    if not evidence:
        with tempfile.TemporaryDirectory() as folder:
            yield folder
        return
    root = Path(evidence).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = tempfile.mkdtemp(prefix='native-', dir=root)
    try:
        yield folder
    except BaseException:
        # Keep the original path intact: an application may still have the failed
        # fixture open. Never move or delete active native evidence on failure.
        raise
    else:
        shutil.rmtree(folder)
