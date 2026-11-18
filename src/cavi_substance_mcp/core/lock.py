"""An advisory, non-blocking lock on one file, held by a launch that needs an app to itself.

Host side only; standard library.
"""

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class FileLock:
    """Exclusive lock on ``path`` (created if missing); taking it never waits."""

    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def try_lock(self) -> bool:
        """Take the lock; False when another holder has it."""
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


def held(path) -> bool:
    """Whether another holder has the lock on an existing ``path``; never creates it."""
    if not Path(path).is_file():
        return False
    probe = FileLock(path)
    if probe.try_lock():
        probe.release()
        return False
    return True
