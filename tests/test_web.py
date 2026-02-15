"""Tests for the web dashboard."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.db import StateDB, ClipStatus
from atomcam_meteor.web.app import create_app


@pytest.fixture
def web_app(tmp_path):
    download_dir = tmp_path / "downloads"
    output_dir = tmp_path / "output"
    download_dir.mkdir()
    output_dir.mkdir()

    config = AppConfig.model_validate({
        "paths": {
            "download_dir": str(download_dir),
            "output_dir": str(output_dir),
            "db_path": str(tmp_path / "test.db"),
            "lock_path": str(tmp_path / "test.lock"),
        }
    })
    app = create_app(config)
    return app


@pytest.fixture
def client(web_app):
    return TestClient(web_app)


@pytest.fixture
def seeded_db(web_app, tmp_path):
    """Seed the DB with test data."""
    config = web_app.state.config
    db = StateDB.from_path(config.paths.resolve_db_path())
    db.clips.upsert_clip("http://cam/20250101/22/00.mp4", "20250101", 22, 0,
                         status=ClipStatus.DETECTED)
    db.clips.update_clip_status("http://cam/20250101/22/00.mp4", ClipStatus.DETECTED,
                                line_count=3, detection_image=str(tmp_path / "output" / "img.png"),
                                detected_video=json.dumps([str(tmp_path / "output" / "clip_meteor.mp4")]))
    db.nights.upsert_output("20250101", detection_count=1)
    db.close()
    return db


@pytest.fixture
def seeded_db_with_detections(web_app, tmp_path):
    """Seed DB with clips and per-line detections."""
    config = web_app.state.config
    db = StateDB.from_path(config.paths.resolve_db_path())
    db.clips.upsert_clip("http://cam/20250101/22/00.mp4", "20250101", 22, 0,
                         status=ClipStatus.DETECTED)
    db.clips.update_clip_status("http://cam/20250101/22/00.mp4", ClipStatus.DETECTED,
                                line_count=2, detection_image=str(tmp_path / "output" / "img.png"))
    clip = db.clips.get_clip("http://cam/20250101/22/00.mp4")
    db.detections.bulk_insert(
        clip["id"],
        [(10, 20, 100, 200), (30, 40, 300, 400)],
        [str(tmp_path / "output" / "line0.png"), str(tmp_path / "output" / "line1.png")],
    )
    db.nights.upsert_output("20250101", detection_count=1)
    db.close()
    return db


class TestHTMLPages:
    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "atomcam-meteor-detector" in resp.text

    def test_index_with_data(self, client, seeded_db):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "20250101" in resp.text

    def test_night_page(self, client, seeded_db):
        resp = client.get("/nights/20250101")
        assert resp.status_code == 200
        assert "20250101" in resp.text

    def test_night_page_has_concatenate_button(self, client, seeded_db):
        resp = client.get("/nights/20250101")
        assert resp.status_code == 200
        assert "Concatenate Video" in resp.text
        assert "Rebuild Composite" in resp.text


class TestAPI:
    def test_api_nights(self, client, seeded_db):
        resp = client.get("/api/nights")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_api_night_detail(self, client, seeded_db):
        resp = client.get("/api/nights/20250101")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date_str"] == "20250101"

    def test_api_night_clips(self, client, seeded_db):
        resp = client.get("/api/nights/20250101/clips")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_patch_excluded(self, client, seeded_db):
        clips = client.get("/api/nights/20250101/clips").json()
        clip_id = clips[0]["id"]
        resp = client.patch(f"/api/clips/{clip_id}", json={"excluded": True})
        assert resp.status_code == 200
        assert resp.json()["excluded"] is True

    def test_patch_missing_field(self, client, seeded_db):
        clips = client.get("/api/nights/20250101/clips").json()
        clip_id = clips[0]["id"]
        resp = client.patch(f"/api/clips/{clip_id}", json={"other": True})
        assert resp.status_code == 400

    def test_patch_nonexistent_clip(self, client):
        resp = client.patch("/api/clips/99999", json={"excluded": True})
        assert resp.status_code == 404

    def test_rebuild_trigger(self, client, seeded_db):
        resp = client.post("/api/nights/20250101/rebuild")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_rebuild_status(self, client):
        resp = client.get("/api/nights/20250101/rebuild/status")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_concatenate_trigger(self, client, seeded_db):
        resp = client.post("/api/nights/20250101/concatenate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_concatenate_status(self, client):
        resp = client.get("/api/nights/20250101/concatenate/status")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_redetect_trigger(self, client, seeded_db):
        resp = client.post("/api/nights/20250101/redetect")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_redetect_status(self, client):
        resp = client.get("/api/nights/20250101/redetect/status")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_redetect_status_with_progress(self, client):
        """進捗情報がステータスレスポンスに含まれること"""
        from atomcam_meteor.web.routes import _redetect_status
        _redetect_status["20250101"] = {
            "status": "running", "processed": 5, "total": 237,
        }
        try:
            resp = client.get("/api/nights/20250101/redetect/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "running"
            assert data["processed"] == 5
            assert data["total"] == 237
        finally:
            _redetect_status.pop("20250101", None)

    def test_redetect_duplicate_rejected(self, client, seeded_db):
        """running 中の POST が 409 を返すこと"""
        from atomcam_meteor.web.routes import _redetect_status
        _redetect_status["20250101"] = {
            "status": "running", "processed": 10, "total": 100,
        }
        try:
            resp = client.post("/api/nights/20250101/redetect")
            assert resp.status_code == 409
            assert "already running" in resp.json()["detail"]
        finally:
            _redetect_status.pop("20250101", None)

    def test_redetect_cancel(self, client):
        """キャンセルエンドポイントが動作すること"""
        import threading
        from atomcam_meteor.web.routes import _redetect_cancel_events, _redetect_status
        event = threading.Event()
        _redetect_cancel_events["20250101"] = event
        _redetect_status["20250101"] = {
            "status": "running", "processed": 5, "total": 100,
        }
        try:
            resp = client.post("/api/nights/20250101/redetect/cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"
            assert event.is_set()
        finally:
            _redetect_cancel_events.pop("20250101", None)
            _redetect_status.pop("20250101", None)

    def test_redetect_cancel_no_task(self, client):
        """実行中タスクがない場合のキャンセルが 404 を返すこと"""
        resp = client.post("/api/nights/20250101/redetect/cancel")
        assert resp.status_code == 404


class TestDetectionAPI:
    def test_toggle_detection(self, client, seeded_db_with_detections):
        # Get clip to find detection IDs
        clips = client.get("/api/nights/20250101/clips").json()
        clip_id = clips[0]["id"]

        # Get detections via night page (they are embedded in clips)
        resp = client.get("/nights/20250101")
        assert resp.status_code == 200

        # Toggle detection via API - need to find a detection ID
        # Use the DB directly through another seeded_db setup
        from atomcam_meteor.services.db import StateDB
        config = client.app.state.config
        db = StateDB.from_path(config.paths.resolve_db_path())
        detections = db.detections.get_detections_by_clip(clip_id)
        db.close()
        assert len(detections) == 2

        det_id = detections[0]["id"]
        resp = client.patch(f"/api/detections/{det_id}", json={"excluded": True})
        assert resp.status_code == 200
        assert resp.json()["excluded"] is True

        # Toggle back
        resp = client.patch(f"/api/detections/{det_id}", json={"excluded": False})
        assert resp.status_code == 200
        assert resp.json()["excluded"] is False

    def test_toggle_detection_missing_field(self, client, seeded_db_with_detections):
        resp = client.patch("/api/detections/1", json={"other": True})
        assert resp.status_code == 400

    def test_toggle_detection_not_found(self, client):
        resp = client.patch("/api/detections/99999", json={"excluded": True})
        assert resp.status_code == 404

    def test_bulk_detections(self, client, seeded_db_with_detections):
        resp = client.patch(
            "/api/nights/20250101/detections/bulk",
            json={"excluded": True},
        )
        assert resp.status_code == 200
        assert resp.json()["excluded"] is True

        # Verify all detections are now excluded
        config = client.app.state.config
        db = StateDB.from_path(config.paths.resolve_db_path())
        clips = client.get("/api/nights/20250101/clips").json()
        detections = db.detections.get_detections_by_clip(clips[0]["id"])
        db.close()
        assert all(d["excluded"] == 1 for d in detections)

    def test_bulk_detections_missing_field(self, client):
        resp = client.patch(
            "/api/nights/20250101/detections/bulk",
            json={"other": True},
        )
        assert resp.status_code == 400


class TestNightVisibilityAPI:
    def test_toggle_night_visibility(self, client, seeded_db):
        """hidden=true/false の往復"""
        # 非表示にする
        resp = client.patch(
            "/api/nights/20250101/visibility", json={"hidden": True},
        )
        assert resp.status_code == 200
        assert resp.json()["hidden"] is True

        # 表示に戻す
        resp = client.patch(
            "/api/nights/20250101/visibility", json={"hidden": False},
        )
        assert resp.status_code == 200
        assert resp.json()["hidden"] is False

    def test_toggle_night_visibility_missing_field(self, client, seeded_db):
        """hidden フィールドがない場合に 400 を返す"""
        resp = client.patch(
            "/api/nights/20250101/visibility", json={"other": True},
        )
        assert resp.status_code == 400

    def test_toggle_night_visibility_not_found(self, client):
        """存在しない夜で 404 を返す"""
        resp = client.patch(
            "/api/nights/99991231/visibility", json={"hidden": True},
        )
        assert resp.status_code == 404

    def test_index_hides_hidden_nights(self, client, seeded_db):
        """非表示にした夜がインデックスに表示されないこと"""
        client.patch(
            "/api/nights/20250101/visibility", json={"hidden": True},
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "20250101" not in resp.text


class TestAdminPage:
    def test_admin_page(self, client):
        """管理ページが200を返すこと"""
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "管理ページ" in resp.text

    def test_admin_shows_hidden_nights(self, client, seeded_db):
        """非表示の夜がリストに表示されること"""
        client.patch(
            "/api/nights/20250101/visibility", json={"hidden": True},
        )
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "20250101" in resp.text
        assert "表示に戻す" in resp.text

    def test_admin_no_hidden(self, client, seeded_db):
        """非表示の夜がない場合のメッセージ"""
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "非表示の夜はありません" in resp.text


class TestSettingsAPI:
    def test_get_schedule_defaults(self, client):
        """デフォルト設定が返ること"""
        resp = client.get("/api/settings/schedule")
        assert resp.status_code == 200
        data = resp.json()
        assert data["start_mode"] == "fixed"
        assert data["start_time"] == "22:00"
        assert data["end_mode"] == "fixed"
        assert data["end_time"] == "06:00"

    def test_put_and_get_schedule(self, client):
        """設定の保存と取得"""
        resp = client.put("/api/settings/schedule", json={
            "start_mode": "twilight",
            "start_time": "21:00",
            "end_mode": "fixed",
            "end_time": "05:30",
            "location_mode": "preset",
            "prefecture": "大阪府",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

        resp = client.get("/api/settings/schedule")
        data = resp.json()
        assert data["start_mode"] == "twilight"
        assert data["start_time"] == "21:00"
        assert data["end_time"] == "05:30"
        assert data["prefecture"] == "大阪府"

    def test_put_empty_body(self, client):
        """空のボディで 400 が返ること"""
        resp = client.put("/api/settings/schedule", json={})
        assert resp.status_code == 400

    def test_get_prefectures(self, client):
        """都道府県リストが 47 件返ること"""
        resp = client.get("/api/settings/prefectures")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 47
        assert data[0]["name"] == "北海道"
        assert "latitude" in data[0]
        assert "longitude" in data[0]

    def test_preview_schedule(self, client):
        """プレビューが有効な時刻を返すこと"""
        resp = client.get("/api/settings/schedule/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "date_str" in data
        assert "start_time" in data
        assert "end_time" in data
        assert len(data["start_time"]) == 5
        assert len(data["end_time"]) == 5


class TestSchedulerAPI:
    def test_scheduler_status(self, client):
        """スケジューラのステータスが取得できること"""
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "running" in data
        assert "interval_minutes" in data
        assert "pipeline_running" in data
        assert "reboot_enabled" in data

    def test_interval_minutes_in_schedule_settings(self, client):
        """interval_minutes がスケジュール設定に含まれること"""
        resp = client.get("/api/settings/schedule")
        assert resp.status_code == 200
        data = resp.json()
        assert "interval_minutes" in data

    def test_put_interval_minutes(self, client):
        """interval_minutes の保存と取得"""
        resp = client.put("/api/settings/schedule", json={
            "interval_minutes": "30",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

        resp = client.get("/api/settings/schedule")
        data = resp.json()
        assert data["interval_minutes"] == "30"


class TestSystemSettingsAPI:
    def test_get_system_defaults(self, client):
        """デフォルトのシステム設定が返ること"""
        resp = client.get("/api/settings/system")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reboot_enabled"] == "false"
        assert data["reboot_time"] == "12:00"

    def test_put_and_get_system(self, client):
        """システム設定の保存と取得"""
        resp = client.put("/api/settings/system", json={
            "reboot_enabled": "true",
            "reboot_time": "14:00",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

        resp = client.get("/api/settings/system")
        data = resp.json()
        assert data["reboot_enabled"] == "true"
        assert data["reboot_time"] == "14:00"

    def test_put_empty_body(self, client):
        """空のボディで 400 が返ること"""
        resp = client.put("/api/settings/system", json={})
        assert resp.status_code == 400


class TestDetectionSettingsAPI:
    def test_get_detection_defaults(self, client):
        """デフォルトの検出設定が返ること"""
        resp = client.get("/api/settings/detection")
        assert resp.status_code == 200
        data = resp.json()
        assert data["min_line_length"] == "30"
        assert data["canny_threshold1"] == "100"
        assert data["canny_threshold2"] == "200"
        assert data["hough_threshold"] == "25"
        assert data["max_line_gap"] == "5"
        assert data["min_line_brightness"] == "20.0"
        assert data["exclude_bottom_pct"] == "0"

    def test_put_and_get_detection(self, client):
        """検出設定の保存と取得"""
        resp = client.put("/api/settings/detection", json={
            "min_line_length": "50",
            "canny_threshold1": "80",
            "hough_threshold": "30",
            "min_line_brightness": "25.5",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

        resp = client.get("/api/settings/detection")
        data = resp.json()
        assert data["min_line_length"] == "50"
        assert data["canny_threshold1"] == "80"
        assert data["hough_threshold"] == "30"
        assert data["min_line_brightness"] == "25.5"
        # 未設定の値はデフォルト
        assert data["canny_threshold2"] == "200"
        assert data["max_line_gap"] == "5"

    def test_put_empty_body(self, client):
        """空のボディで 400 が返ること"""
        resp = client.put("/api/settings/detection", json={})
        assert resp.status_code == 400

    def test_put_invalid_keys_only(self, client):
        """無効なキーのみのボディで 400 が返ること"""
        resp = client.put("/api/settings/detection", json={"invalid_key": "100"})
        assert resp.status_code == 400


class TestGeolocateAPI:
    def test_geolocate_success(self, client, monkeypatch):
        """IP ジオロケーションで都道府県が返ること"""
        import httpx

        class FakeResponse:
            status_code = 200
            def json(self):
                return {
                    "status": "success",
                    "regionName": "福岡県",
                    "lat": 33.6064,
                    "lon": 130.4183,
                }
            def raise_for_status(self):
                pass

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResponse())

        resp = client.get("/api/settings/geolocate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["prefecture"] == "福岡県"
        assert data["latitude"] == 33.6064
        assert data["longitude"] == 130.4183

    def test_geolocate_no_prefecture_match(self, client, monkeypatch):
        """都道府県に一致しない地域ではカスタム座標が返ること"""
        import httpx

        class FakeResponse:
            status_code = 200
            def json(self):
                return {
                    "status": "success",
                    "regionName": "Guam",
                    "lat": 13.4443,
                    "lon": 144.7937,
                }
            def raise_for_status(self):
                pass

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResponse())

        resp = client.get("/api/settings/geolocate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["prefecture"] is None
        assert data["latitude"] == 13.4443

    def test_geolocate_api_failure(self, client, monkeypatch):
        """外部 API 失敗時に 502 が返ること"""
        import httpx

        def raise_error(*a, **kw):
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(httpx, "get", raise_error)

        resp = client.get("/api/settings/geolocate")
        assert resp.status_code == 502


class TestResetSettingsAPI:
    def test_reset_schedule(self, client):
        """スケジュール設定のリセット"""
        # 値を保存
        client.put("/api/settings/schedule", json={
            "start_time": "21:00",
            "prefecture": "大阪府",
            "interval_minutes": "30",
        })
        # リセット
        resp = client.delete("/api/settings/schedule")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"
        # デフォルトに戻っていること
        resp = client.get("/api/settings/schedule")
        data = resp.json()
        assert data["start_time"] == "22:00"
        assert data["prefecture"] == "東京都"
        assert data["interval_minutes"] == "60"

    def test_reset_detection(self, client):
        """検出設定のリセット"""
        client.put("/api/settings/detection", json={
            "min_line_length": "99",
            "hough_threshold": "50",
        })
        resp = client.delete("/api/settings/detection")
        assert resp.status_code == 200
        # デフォルトに戻っていること
        resp = client.get("/api/settings/detection")
        data = resp.json()
        assert data["min_line_length"] == "30"
        assert data["hough_threshold"] == "25"

    def test_reset_system(self, client):
        """システム設定のリセット"""
        client.put("/api/settings/system", json={
            "reboot_enabled": "true",
            "reboot_time": "15:00",
        })
        resp = client.delete("/api/settings/system")
        assert resp.status_code == 200
        # デフォルトに戻っていること
        resp = client.get("/api/settings/system")
        data = resp.json()
        assert data["reboot_enabled"] == "false"
        assert data["reboot_time"] == "12:00"
