"""Tests for database module."""

import sqlite3

import pytest

from atomcam_meteor.services.db import (
    ClipStatus,
    DetectionRepository,
    SettingsRepository,
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


class TestDetectionRepository:
    def _make_clip(self, memory_db, url="http://a", date_str="20250101"):
        memory_db.clips.upsert_clip(url, date_str, 22, 0, status=ClipStatus.DETECTED)
        return memory_db.clips.get_clip(url)["id"]

    def test_bulk_insert_and_get(self, memory_db):
        clip_id = self._make_clip(memory_db)
        lines = [(10, 20, 100, 200), (30, 40, 300, 400)]
        crops = ["/crop0.png", "/crop1.png"]
        memory_db.detections.bulk_insert(clip_id, lines, crops)

        detections = memory_db.detections.get_detections_by_clip(clip_id)
        assert len(detections) == 2
        assert detections[0]["x1"] == 10
        assert detections[0]["crop_image"] == "/crop0.png"
        assert detections[1]["line_index"] == 1

    def test_toggle_excluded(self, memory_db):
        clip_id = self._make_clip(memory_db)
        memory_db.detections.bulk_insert(
            clip_id, [(10, 20, 100, 200)], ["/crop.png"],
        )
        det = memory_db.detections.get_detections_by_clip(clip_id)[0]
        assert det["excluded"] == 0

        memory_db.detections.toggle_excluded(det["id"], True)
        det = memory_db.detections.get_detection_by_id(det["id"])
        assert det["excluded"] == 1

        memory_db.detections.toggle_excluded(det["id"], False)
        det = memory_db.detections.get_detection_by_id(det["id"])
        assert det["excluded"] == 0

    def test_set_all_excluded_by_clip(self, memory_db):
        clip_id = self._make_clip(memory_db)
        memory_db.detections.bulk_insert(
            clip_id,
            [(10, 20, 100, 200), (30, 40, 300, 400)],
            ["/c0.png", "/c1.png"],
        )
        memory_db.detections.set_all_excluded(clip_id, True)
        excluded = memory_db.detections.get_excluded_detections_by_clip(clip_id)
        assert len(excluded) == 2

        memory_db.detections.set_all_excluded(clip_id, False)
        included = memory_db.detections.get_included_detections_by_clip(clip_id)
        assert len(included) == 2

    def test_set_all_excluded_by_date(self, memory_db):
        c1 = self._make_clip(memory_db, "http://a")
        c2 = self._make_clip(memory_db, "http://b")
        memory_db.detections.bulk_insert(c1, [(0, 0, 10, 10)], ["/a.png"])
        memory_db.detections.bulk_insert(c2, [(0, 0, 20, 20)], ["/b.png"])

        memory_db.detections.set_all_excluded_by_date("20250101", True)
        assert len(memory_db.detections.get_excluded_detections_by_clip(c1)) == 1
        assert len(memory_db.detections.get_excluded_detections_by_clip(c2)) == 1

    def test_delete_by_clip(self, memory_db):
        clip_id = self._make_clip(memory_db)
        memory_db.detections.bulk_insert(
            clip_id, [(10, 20, 100, 200)], ["/crop.png"],
        )
        assert len(memory_db.detections.get_detections_by_clip(clip_id)) == 1
        memory_db.detections.delete_by_clip(clip_id)
        assert len(memory_db.detections.get_detections_by_clip(clip_id)) == 0

    def test_upsert_detection(self, memory_db):
        clip_id = self._make_clip(memory_db)
        memory_db.detections.upsert_detection(clip_id, 0, 10, 20, 100, 200, "/c.png")
        det = memory_db.detections.get_detections_by_clip(clip_id)
        assert len(det) == 1
        assert det[0]["crop_image"] == "/c.png"

        # Upsert same line_index should update
        memory_db.detections.upsert_detection(clip_id, 0, 50, 60, 500, 600, "/new.png")
        det = memory_db.detections.get_detections_by_clip(clip_id)
        assert len(det) == 1
        assert det[0]["x1"] == 50
        assert det[0]["crop_image"] == "/new.png"

    def test_get_nonexistent(self, memory_db):
        assert memory_db.detections.get_detection_by_id(99999) is None


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


class TestSettingsRepository:
    def test_get_nonexistent(self, memory_db):
        """存在しないキーは None を返す"""
        assert memory_db.settings.get("nonexistent") is None

    def test_set_and_get(self, memory_db):
        """set した値を get で取得できる"""
        memory_db.settings.set("schedule.start_mode", "twilight")
        assert memory_db.settings.get("schedule.start_mode") == "twilight"

    def test_set_overwrite(self, memory_db):
        """同一キーへの set で値が上書きされる"""
        memory_db.settings.set("key1", "value1")
        memory_db.settings.set("key1", "value2")
        assert memory_db.settings.get("key1") == "value2"

    def test_get_all_empty(self, memory_db):
        """空のテーブルでは空辞書を返す"""
        assert memory_db.settings.get_all() == {}

    def test_get_all(self, memory_db):
        """全設定を辞書として取得できる"""
        memory_db.settings.set("a", "1")
        memory_db.settings.set("b", "2")
        result = memory_db.settings.get_all()
        assert result == {"a": "1", "b": "2"}

    def test_set_many(self, memory_db):
        """複数設定を一括保存できる"""
        memory_db.settings.set_many({"x": "10", "y": "20", "z": "30"})
        assert memory_db.settings.get("x") == "10"
        assert memory_db.settings.get("y") == "20"
        assert memory_db.settings.get("z") == "30"

    def test_set_many_overwrite(self, memory_db):
        """set_many で既存値が上書きされる"""
        memory_db.settings.set("key", "old")
        memory_db.settings.set_many({"key": "new"})
        assert memory_db.settings.get("key") == "new"


class TestStateDB:
    def test_facade(self, memory_db):
        assert isinstance(memory_db.clips, ClipRepository)
        assert isinstance(memory_db.detections, DetectionRepository)
        assert isinstance(memory_db.nights, NightOutputRepository)
        assert isinstance(memory_db.settings, SettingsRepository)

    def test_from_path(self, tmp_path):
        db = StateDB.from_path(tmp_path / "test.db")
        db.clips.upsert_clip("http://test", "20250101", 0, 0)
        assert db.clips.get_clip("http://test") is not None
        db.close()
