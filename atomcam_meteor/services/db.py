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

_DETECTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    line_index INTEGER NOT NULL,
    x1 INTEGER NOT NULL,
    y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL,
    y2 INTEGER NOT NULL,
    crop_image TEXT,
    excluded INTEGER DEFAULT 0,
    FOREIGN KEY (clip_id) REFERENCES clips(id),
    UNIQUE(clip_id, line_index)
);
"""

_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MIGRATE_EXCLUDED = (
    "ALTER TABLE clips ADD COLUMN excluded INTEGER DEFAULT 0;"
)

_MIGRATE_NIGHT_HIDDEN = (
    "ALTER TABLE night_outputs ADD COLUMN hidden INTEGER DEFAULT 0;"
)


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the database, enable WAL mode, and create tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CLIPS_TABLE)
    conn.execute(_NIGHT_OUTPUTS_TABLE)
    conn.execute(_DETECTIONS_TABLE)
    conn.execute(_SETTINGS_TABLE)
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

    night_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(night_outputs);").fetchall()
    }
    if "hidden" not in night_columns:
        conn.execute(_MIGRATE_NIGHT_HIDDEN)
        conn.commit()
        logger.info("Migrated: added 'hidden' column to night_outputs table")


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


class DetectionRepository:
    """CRUD operations for the detections table (per-line records)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_detection(
        self,
        clip_id: int,
        line_index: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        crop_image: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO detections
               (clip_id, line_index, x1, y1, x2, y2, crop_image)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(clip_id, line_index) DO UPDATE SET
                   x1 = excluded.x1, y1 = excluded.y1,
                   x2 = excluded.x2, y2 = excluded.y2,
                   crop_image = excluded.crop_image""",
            (clip_id, line_index, x1, y1, x2, y2, crop_image),
        )
        self._conn.commit()

    def bulk_insert(
        self,
        clip_id: int,
        lines: list[tuple[int, int, int, int]],
        crop_images: list[str],
    ) -> None:
        """Insert multiple detection records for a clip."""
        for i, ((x1, y1, x2, y2), crop_path) in enumerate(zip(lines, crop_images)):
            self._conn.execute(
                """INSERT INTO detections
                   (clip_id, line_index, x1, y1, x2, y2, crop_image)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(clip_id, line_index) DO UPDATE SET
                       x1 = excluded.x1, y1 = excluded.y1,
                       x2 = excluded.x2, y2 = excluded.y2,
                       crop_image = excluded.crop_image""",
                (clip_id, i, x1, y1, x2, y2, crop_path),
            )
        self._conn.commit()

    def get_detections_by_clip(self, clip_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM detections WHERE clip_id = ? ORDER BY line_index",
            (clip_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_detection_by_id(self, detection_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM detections WHERE id = ?", (detection_id,)
        ).fetchone()
        return dict(row) if row else None

    def toggle_excluded(self, detection_id: int, excluded: bool) -> None:
        self._conn.execute(
            "UPDATE detections SET excluded = ? WHERE id = ?",
            (int(excluded), detection_id),
        )
        self._conn.commit()

    def set_all_excluded(self, clip_id: int, excluded: bool) -> None:
        """Set excluded flag for all detections of a clip."""
        self._conn.execute(
            "UPDATE detections SET excluded = ? WHERE clip_id = ?",
            (int(excluded), clip_id),
        )
        self._conn.commit()

    def set_all_excluded_by_date(self, date_str: str, excluded: bool) -> None:
        """Set excluded flag for all detections of clips in a given night."""
        self._conn.execute(
            """UPDATE detections SET excluded = ?
               WHERE clip_id IN (
                   SELECT id FROM clips WHERE date_str = ? AND status = 'detected'
               )""",
            (int(excluded), date_str),
        )
        self._conn.commit()

    def get_excluded_detections_by_clip(self, clip_id: int) -> list[dict]:
        """Return excluded detections for a clip."""
        rows = self._conn.execute(
            "SELECT * FROM detections WHERE clip_id = ? AND excluded = 1 ORDER BY line_index",
            (clip_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_included_detections_by_clip(self, clip_id: int) -> list[dict]:
        """Return included (non-excluded) detections for a clip."""
        rows = self._conn.execute(
            "SELECT * FROM detections WHERE clip_id = ? AND excluded = 0 ORDER BY line_index",
            (clip_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_by_clip(self, clip_id: int) -> None:
        """Delete all detections for a clip (used during redetect)."""
        self._conn.execute("DELETE FROM detections WHERE clip_id = ?", (clip_id,))
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

    def clear_concat_video(self, date_str: str) -> None:
        """Clear the concat_video path for a night."""
        self._conn.execute(
            """UPDATE night_outputs
               SET concat_video = NULL, last_updated_at = datetime('now')
               WHERE date_str = ?""",
            (date_str,),
        )
        self._conn.commit()

    def get_all_nights(self) -> list[dict]:
        """Return all night output records ordered by date descending."""
        rows = self._conn.execute(
            "SELECT * FROM night_outputs ORDER BY date_str DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def toggle_hidden(self, date_str: str, hidden: bool) -> None:
        """Set the hidden flag for a night."""
        self._conn.execute(
            "UPDATE night_outputs SET hidden = ?, last_updated_at = datetime('now') "
            "WHERE date_str = ?",
            (int(hidden), date_str),
        )
        self._conn.commit()

    def get_visible_nights(self) -> list[dict]:
        """Return only visible (non-hidden) night output records."""
        rows = self._conn.execute(
            "SELECT * FROM night_outputs WHERE hidden = 0 ORDER BY date_str DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_hidden(self) -> int:
        """Return the number of hidden nights."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM night_outputs WHERE hidden = 1"
        ).fetchone()
        return row["cnt"] if row else 0


class SettingsRepository:
    """Key-value settings stored in the ``settings`` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        """Return the value for *key*, or ``None`` if not set."""
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        """Insert or update a single setting."""
        self._conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = datetime('now')""",
            (key, value),
        )
        self._conn.commit()

    def get_all(self) -> dict[str, str]:
        """Return all settings as a dictionary."""
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_many(self, items: dict[str, str]) -> None:
        """Insert or update multiple settings at once."""
        for key, value in items.items():
            self._conn.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = datetime('now')""",
                (key, value),
            )
        self._conn.commit()

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all settings whose key starts with *prefix*.

        Returns the number of deleted rows.
        """
        cur = self._conn.execute(
            "DELETE FROM settings WHERE key LIKE ?", (prefix + "%",),
        )
        self._conn.commit()
        return cur.rowcount


class StateDB:
    """Facade providing access to both repositories from a single connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.clips = ClipRepository(conn)
        self.detections = DetectionRepository(conn)
        self.nights = NightOutputRepository(conn)
        self.settings = SettingsRepository(conn)

    @classmethod
    def from_path(cls, db_path: Path) -> StateDB:
        conn = init_db(db_path)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()
