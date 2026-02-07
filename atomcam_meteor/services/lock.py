"""File-based locking to prevent concurrent execution."""

from __future__ import annotations

import fcntl
from pathlib import Path

from atomcam_meteor.exceptions import LockError


class FileLock:
    """Exclusive file lock using fcntl.

    Usage::

        with FileLock(Path("/tmp/myapp.lock")):
            # critical section
            ...
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fp = None

    def __enter__(self) -> FileLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._lock_path, "w")
        try:
            fcntl.flock(self._fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fp.close()
            self._fp = None
            raise LockError("Another instance is already running") from exc
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._fp is not None:
            fcntl.flock(self._fp, fcntl.LOCK_UN)
            self._fp.close()
            self._fp = None
        return None
