"""Tests for file locking."""

import pytest

from atomcam_meteor.exceptions import LockError
from atomcam_meteor.services.lock import FileLock


class TestFileLock:
    def test_acquire_and_release(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with FileLock(lock_path):
            assert lock_path.exists()

    def test_double_lock_raises(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with FileLock(lock_path):
            with pytest.raises(LockError, match="Another instance"):
                with FileLock(lock_path):
                    pass

    def test_cleanup_on_exit(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)
        lock.__enter__()
        assert lock._fp is not None
        lock.__exit__(None, None, None)
        assert lock._fp is None
