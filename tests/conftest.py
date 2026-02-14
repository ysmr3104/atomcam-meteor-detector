"""Shared test fixtures."""

import sqlite3
from pathlib import Path

import pytest

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.db import StateDB, init_db


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary download and output directories."""
    download_dir = tmp_path / "downloads"
    output_dir = tmp_path / "output"
    download_dir.mkdir()
    output_dir.mkdir()
    return download_dir, output_dir


@pytest.fixture
def sample_config(tmp_path):
    """AppConfig with temporary directories."""
    return AppConfig.model_validate({
        "paths": {
            "download_dir": str(tmp_path / "downloads"),
            "output_dir": str(tmp_path / "output"),
            "db_path": str(tmp_path / "test.db"),
            "lock_path": str(tmp_path / "test.lock"),
        }
    })


@pytest.fixture
def memory_db():
    """In-memory SQLite StateDB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    from atomcam_meteor.services.db import (
        _CLIPS_TABLE, _NIGHT_OUTPUTS_TABLE, _DETECTIONS_TABLE, _SETTINGS_TABLE,
    )
    conn.execute(_CLIPS_TABLE)
    conn.execute(_NIGHT_OUTPUTS_TABLE)
    conn.execute(_DETECTIONS_TABLE)
    conn.execute(_SETTINGS_TABLE)
    conn.commit()
    db = StateDB(conn)
    yield db
    conn.close()
