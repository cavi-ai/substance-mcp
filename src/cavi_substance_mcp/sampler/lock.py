"""Cross-process workspace lock that serialises every Sampler launch."""

import asyncio
import os
import sys
import time
from pathlib import Path

from .errors import LockTimeout

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

LOCK_NAME = ".cavi-sampler.lock"
POLL_SECONDS = 0.2


class WorkspaceLock:
    """Advisory lock on ``<workspace>/.cavi-sampler.lock``.

    Use ``async with`` from the event loop. Acquisition polls with a non-blocking
    attempt between ``await asyncio.sleep`` calls instead of parking a thread in a
    blocking acquire: a cancelled task can then never leave an acquire running in the
    background that would take the lock later and never release it. The synchronous
    context manager remains for threads and scripts.
    """

    def __init__(self, root: Path, timeout: float = 300):
        self.path = Path(root) / LOCK_NAME
        self.timeout = timeout
        self.fd = None

    def _try(self) -> bool:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == "win32":
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def _timeout(self) -> LockTimeout:
        return LockTimeout(
            "another Sampler job holds the workspace lock; retry when it has finished"
        )

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while not self._try():
            if time.monotonic() >= deadline:
                raise self._timeout()
            time.sleep(POLL_SECONDS)
        return self

    def __exit__(self, *_):
        self.release()

    async def acquire(self):
        deadline = time.monotonic() + self.timeout
        while not self._try():
            if time.monotonic() >= deadline:
                raise self._timeout()
            # The only suspension point: cancellation lands here, before any acquire.
            await asyncio.sleep(POLL_SECONDS)
        return self

    async def __aenter__(self):
        return await self.acquire()

    async def __aexit__(self, *_):
        self.release()

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if sys.platform == "win32":
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def lock_held(root: Path) -> bool:
    root = Path(root)
    if not root.is_dir() or not (root / LOCK_NAME).is_file():
        return False
    probe = WorkspaceLock(root, timeout=0)
    if probe._try():
        probe.release()
        return False
    return True
