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
