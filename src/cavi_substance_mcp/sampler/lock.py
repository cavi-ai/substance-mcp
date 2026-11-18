"""Cross-process workspace lock that serialises every Sampler launch."""

import asyncio
import time
from pathlib import Path

from ..core.lock import FileLock, held
from .errors import LockTimeout

LOCK_NAME = ".cavi-sampler.lock"
POLL_SECONDS = 0.2


class WorkspaceLock(FileLock):
    """Advisory lock on ``<workspace>/.cavi-sampler.lock``.

    Use ``async with`` from the event loop. Acquisition polls with a non-blocking
    attempt between ``await asyncio.sleep`` calls instead of parking a thread in a
    blocking acquire: a cancelled task can then never leave an acquire running in the
    background that would take the lock later and never release it. The synchronous
    context manager remains for threads and scripts.
    """

    def __init__(self, root: Path, timeout: float = 300):
        super().__init__(Path(root) / LOCK_NAME)
        self.timeout = timeout

    def _timeout(self) -> LockTimeout:
        return LockTimeout(
            "another Sampler job holds the workspace lock; retry when it has finished"
        )

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while not self.try_lock():
            if time.monotonic() >= deadline:
                raise self._timeout()
            time.sleep(POLL_SECONDS)
        return self

    def __exit__(self, *_):
        self.release()

    async def acquire(self):
        deadline = time.monotonic() + self.timeout
        while not self.try_lock():
            if time.monotonic() >= deadline:
                raise self._timeout()
            # The only suspension point: cancellation lands here, before any acquire.
            await asyncio.sleep(POLL_SECONDS)
        return self

    async def __aenter__(self):
        return await self.acquire()

    async def __aexit__(self, *_):
        self.release()


def lock_held(root: Path) -> bool:
    root = Path(root)
    return root.is_dir() and held(root / LOCK_NAME)
