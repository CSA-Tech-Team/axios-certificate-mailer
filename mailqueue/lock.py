"""OS-level exclusive lock so only one `send` runs per queue.

The lock is released by the OS when the process dies, so there are no stale
PID files to clean up after a crash.
"""
from __future__ import annotations

import os
from pathlib import Path


class LockHeld(Exception):
    pass


class FileLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._f = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise LockHeld(f"another send process holds {self.path}")
        self._f = f

    def release(self) -> None:
        if self._f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._f.close()
            self._f = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
