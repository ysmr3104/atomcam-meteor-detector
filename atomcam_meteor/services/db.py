"""SQLite database module for clip and night-output state management."""

from __future__ import annotations

import enum
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ClipStatus(enum.StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    DETECTED = "detected"
    NO_DETECTION = "no_detection"
    ERROR = "error"


_CLIPS_TABLE = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_url TEXT UNIQUE NOT NULL,
    date_str TEXT NOT NULL,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    detection_image TEXT,
    detected_video TEXT,
    line_count INTEGER DEFAULT 0,
    excluded INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_NIGHT_OUTPUTS_TABLE = """
CREATE TABLE IF NOT EXISTS night_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_str TEXT UNIQUE NOT NULL,
    composite_image TEXT,
    concat_video TEXT,
    detection_count INTEGER DEFAULT 0,
    last_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MIGRATE_EXCLUDED = (
    "ALTER TABLE clips ADD COLUMN excluded INTEGER DEFAULT 0;"
)


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the database, enable WAL mode, and create tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CLIPS_TABLE)
    conn.execute(_NIGHT_OUTPUTS_TABLE)
    conn.commit()
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Run idempotent schema migrations."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(clips);").fetchall()
    }
    if "excluded" not in columns:
        conn.execute(_MIGRATE_EXCLUDED)
        conn.commit()
        logger.info("Migrated: added 'excluded' column to clips table")


class ClipRepository:
    """CRUD operations for the clips table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_clip(
        self,
        clip_url: str,
        date_str: str,
        hour: int,
        minute: int,
        local_path: Optional[str] = None,
        status: str = ClipStatus.PENDING,
    ) -> None:
        self._conn.execute(
            """INSERT INTO clips
               (clip_url, date_str, hour, minute, local_path, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(clip_url) DO UPDATE SET
                   local_path = COALESCE(excluded.local_path, clips.local_path),
                   status = CASE
                       WHEN clips.status IN ('detected', 'no_detection', 'error')
                       THEN clips.status
                       ELSE excluded.status
                   END,
                   updated_at = datetime('now')""",
            (clip_url, date_str, hour, minute, local_path, status),
        )
        self._conn.commit()

    def get_clip(self, clip_url: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM clips WHERE clip_url = ?", (clip_url,)
        ).fetchone()
        return dict(row) if row else None

    def get_clip_by_id(self, clip_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_clip_status(self, clip_url: str, status: str, **kwargs: object) -> None:
        allowed = {"detection_image", "detected_video", "line_count", "error_message"}
        extras = {k: v for k, v in kwargs.items() if k in allowed}
        sets = ["status = ?", "updated_at = datetime('now')"]
        params: list[object] = [status]
        for col, val in extras.items():
            sets.append(f"{col} = ?")
            params.append(val)
        params.append(clip_url)
        self._conn.execute(
            f"UPDATE clips SET {', '.join(sets)} WHERE clip_url = ?", params
        )
        self._conn.commit()

    def get_clips_by_date(self, date_str: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM clips WHERE date_str = ? ORDER BY hour, minute",
            (date_str,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_detected_clips(self, date_str: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM clips WHERE date_str = ? AND status = ? ORDER BY hour, minute",
            (date_str, ClipStatus.DETECTED),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_included_detected_clips(self, date_str: str) -> list[dict]:
        """Return detected clips that are not excluded."""
        rows = self._conn.execute(
            "SELECT * FROM clips WHERE date_str = ? AND status = ? AND excluded = 0 "
            "ORDER BY hour, minute",
            (date_str, ClipStatus.DETECTED),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_detected_video_paths(clip: dict) -> list[str]:
        """Parse ``detected_video`` field into a list of path strings.

        Handles JSON arrays, plain strings, and *None*.
        """
        raw = clip.get("detected_video")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
        # Legacy single-path fallback
        return [raw]

    def toggle_excluded(self, clip_id: int, excluded: bool) -> None:
        """Set the excluded flag for a clip by its ID."""
        self._conn.execute(
            "UPDATE clips SET excluded = ?, updated_at = datetime('now') WHERE id = ?",
            (int(excluded), clip_id),
        )
        self._conn.commit()


class NightOutputRepository:
    """CRUD operations for the night_outputs table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_output(
        self,
        date_str: str,
        composite_image: Optional[str] = None,
        concat_video: Optional[str] = None,
        detection_count: int = 0,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO night_outputs
               (date_str, composite_image, concat_video, detection_count, last_updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (date_str, composite_image, concat_video, detection_count),
        )
        self._conn.commit()

    def get_output(self, date_str: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM night_outputs WHERE date_str = ?", (date_str,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_nights(self) -> list[dict]:
        """Return all night output records ordered by date descending."""
        rows = self._conn.execute(
            "SELECT * FROM night_outputs ORDER BY date_str DESC"
        ).fetchall()
        return [dict(r) for r in rows]


class StateDB:
    """Facade providing access to both repositories from a single connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.clips = ClipRepository(conn)
        self.nights = NightOutputRepository(conn)

    @classmethod
    def from_path(cls, db_path: Path) -> StateDB:
        conn = init_db(db_path)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()
