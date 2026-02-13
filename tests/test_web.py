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
