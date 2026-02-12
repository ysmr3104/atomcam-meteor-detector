"""Tests for database module."""

import sqlite3

import pytest

from atomcam_meteor.services.db import (
    ClipStatus,
    StateDB,
    ClipRepository,
    NightOutputRepository,
)


class TestClipStatus:
    def test_values(self):
        assert ClipStatus.PENDING == "pending"
        assert ClipStatus.DETECTED == "detected"
        assert ClipStatus.ERROR == "error"

    def test_is_str(self):
        assert isinstance(ClipStatus.PENDING, str)


class TestClipRepository:
    def test_upsert_and_get(self, memory_db):
        memory_db.clips.upsert_clip(
            "http://cam/20250101/22/01.mp4", "20250101", 22, 1,
            local_path="/dl/01.mp4", status=ClipStatus.DOWNLOADED,
        )
        clip = memory_db.clips.get_clip("http://cam/20250101/22/01.mp4")
        assert clip is not None
        assert clip["date_str"] == "20250101"
        assert clip["hour"] == 22
        assert clip["status"] == "downloaded"

    def test_get_nonexistent(self, memory_db):
        assert memory_db.clips.get_clip("http://missing") is None

    def test_update_status(self, memory_db):
        url = "http://cam/20250101/22/02.mp4"
        memory_db.clips.upsert_clip(url, "20250101", 22, 2)
        memory_db.clips.update_clip_status(
            url, ClipStatus.DETECTED, line_count=3, detection_image="/img.png"
        )
        clip = memory_db.clips.get_clip(url)
        assert clip["status"] == "detected"
        assert clip["line_count"] == 3

    def test_get_clips_by_date(self, memory_db):
        memory_db.clips.upsert_clip("http://a", "20250101", 22, 0)
        memory_db.clips.upsert_clip("http://b", "20250101", 23, 0)
        memory_db.clips.upsert_clip("http://c", "20250102", 0, 0)
        clips = memory_db.clips.get_clips_by_date("20250101")
        assert len(clips) == 2

    def test_get_detected_clips(self, memory_db):
        memory_db.clips.upsert_clip("http://a", "20250101", 22, 0, status=ClipStatus.DETECTED)
        memory_db.clips.upsert_clip("http://b", "20250101", 23, 0, status=ClipStatus.NO_DETECTION)
        detected = memory_db.clips.get_detected_clips("20250101")
        assert len(detected) == 1
        assert detected[0]["clip_url"] == "http://a"

    def test_toggle_excluded(self, memory_db):
        memory_db.clips.upsert_clip("http://a", "20250101", 22, 0, status=ClipStatus.DETECTED)
        clip = memory_db.clips.get_clip("http://a")
        clip_id = clip["id"]
        assert clip["excluded"] == 0

        memory_db.clips.toggle_excluded(clip_id, True)
        clip = memory_db.clips.get_clip_by_id(clip_id)
        assert clip["excluded"] == 1

        memory_db.clips.toggle_excluded(clip_id, False)
        clip = memory_db.clips.get_clip_by_id(clip_id)
        assert clip["excluded"] == 0

    def test_get_included_detected_clips(self, memory_db):
        memory_db.clips.upsert_clip("http://a", "20250101", 22, 0, status=ClipStatus.DETECTED)
        memory_db.clips.upsert_clip("http://b", "20250101", 23, 0, status=ClipStatus.DETECTED)
        clip_b = memory_db.clips.get_clip("http://b")
        memory_db.clips.toggle_excluded(clip_b["id"], True)

        included = memory_db.clips.get_included_detected_clips("20250101")
        assert len(included) == 1
        assert included[0]["clip_url"] == "http://a"

    def test_get_clip_by_id(self, memory_db):
        memory_db.clips.upsert_clip("http://a", "20250101", 22, 0)
        clip = memory_db.clips.get_clip("http://a")
        by_id = memory_db.clips.get_clip_by_id(clip["id"])
        assert by_id["clip_url"] == "http://a"

    def test_upsert_preserves_terminal_detected(self, memory_db):
        """upsert_clip must not overwrite a DETECTED status."""
        url = "http://cam/20250101/22/00.mp4"
        memory_db.clips.upsert_clip(url, "20250101", 22, 0, status=ClipStatus.DOWNLOADED)
        memory_db.clips.update_clip_status(
            url, ClipStatus.DETECTED, detection_image="/img.png", line_count=2,
        )
        # Re-upsert with DOWNLOADED — should NOT overwrite DETECTED
        memory_db.clips.upsert_clip(
            url, "20250101", 22, 0,
            local_path="/dl/00.mp4", status=ClipStatus.DOWNLOADED,
        )
        clip = memory_db.clips.get_clip(url)
        assert clip["status"] == ClipStatus.DETECTED
        assert clip["detection_image"] == "/img.png"
        assert clip["local_path"] == "/dl/00.mp4"

    def test_upsert_preserves_terminal_no_detection(self, memory_db):
        """upsert_clip must not overwrite a NO_DETECTION status."""
        url = "http://cam/20250101/22/01.mp4"
        memory_db.clips.upsert_clip(url, "20250101", 22, 1, status=ClipStatus.DOWNLOADED)
        memory_db.clips.update_clip_status(url, ClipStatus.NO_DETECTION)
        memory_db.clips.upsert_clip(url, "20250101", 22, 1, status=ClipStatus.DOWNLOADED)
        clip = memory_db.clips.get_clip(url)
        assert clip["status"] == ClipStatus.NO_DETECTION

    def test_upsert_preserves_terminal_error(self, memory_db):
        """upsert_clip must not overwrite an ERROR status."""
        url = "http://cam/20250101/22/02.mp4"
        memory_db.clips.upsert_clip(url, "20250101", 22, 2, status=ClipStatus.DOWNLOADED)
        memory_db.clips.update_clip_status(url, ClipStatus.ERROR, error_message="fail")
        memory_db.clips.upsert_clip(url, "20250101", 22, 2, status=ClipStatus.DOWNLOADED)
        clip = memory_db.clips.get_clip(url)
        assert clip["status"] == ClipStatus.ERROR

    def test_upsert_allows_pending_to_downloaded(self, memory_db):
        """upsert_clip should allow status update from PENDING to DOWNLOADED."""
        url = "http://cam/20250101/22/03.mp4"
        memory_db.clips.upsert_clip(url, "20250101", 22, 3, status=ClipStatus.PENDING)
        memory_db.clips.upsert_clip(
            url, "20250101", 22, 3,
            local_path="/dl/03.mp4", status=ClipStatus.DOWNLOADED,
        )
        clip = memory_db.clips.get_clip(url)
        assert clip["status"] == ClipStatus.DOWNLOADED
        assert clip["local_path"] == "/dl/03.mp4"


class TestNightOutputRepository:
    def test_upsert_and_get(self, memory_db):
        memory_db.nights.upsert_output("20250101", composite_image="/comp.jpg", detection_count=5)
        output = memory_db.nights.get_output("20250101")
        assert output is not None
        assert output["detection_count"] == 5

    def test_get_nonexistent(self, memory_db):
        assert memory_db.nights.get_output("19000101") is None

    def test_get_all_nights(self, memory_db):
        memory_db.nights.upsert_output("20250101", detection_count=2)
        memory_db.nights.upsert_output("20250102", detection_count=3)
        nights = memory_db.nights.get_all_nights()
        assert len(nights) == 2
        assert nights[0]["date_str"] == "20250102"  # DESC order


class TestStateDB:
    def test_facade(self, memory_db):
        assert isinstance(memory_db.clips, ClipRepository)
        assert isinstance(memory_db.nights, NightOutputRepository)

    def test_from_path(self, tmp_path):
        db = StateDB.from_path(tmp_path / "test.db")
        db.clips.upsert_clip("http://test", "20250101", 0, 0)
        assert db.clips.get_clip("http://test") is not None
        db.close()
