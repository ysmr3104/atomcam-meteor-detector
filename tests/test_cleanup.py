"""Tests for the cleanup service."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.cleanup import CleanupResult, CleanupService
from atomcam_meteor.services.db import ClipStatus, StateDB


@pytest.fixture
def cleanup_env(tmp_path):
    """テスト用のクリーンアップ環境をセットアップする。"""
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
    db = StateDB.from_path(config.paths.resolve_db_path())
    return config, db, download_dir


def _seed_night(
    db: StateDB,
    download_dir: Path,
    date_str: str,
    file_count: int = 3,
    file_size: int = 1024,
) -> list[Path]:
    """テスト用に夜データをシードする。"""
    files = []
    for i in range(file_count):
        hour_dir = download_dir / date_str / f"{22 + i % 2:02d}"
        hour_dir.mkdir(parents=True, exist_ok=True)
        mp4 = hour_dir / f"{i:02d}.mp4"
        mp4.write_bytes(b"\x00" * file_size)
        files.append(mp4)

        clip_url = f"http://cam/{date_str}/{22 + i % 2:02d}/{i:02d}.mp4"
        db.clips.upsert_clip(
            clip_url, date_str, 22 + i % 2, i,
            local_path=str(mp4),
            status=ClipStatus.DETECTED,
        )

    db.nights.upsert_output(date_str, detection_count=file_count)
    return files


class TestCleanupDisabled:
    def test_cleanup_disabled(self, cleanup_env):
        """mode=disabled では何も削除しない。"""
        config, db, download_dir = cleanup_env
        _seed_night(db, download_dir, "20250101")

        # デフォルトは disabled
        service = CleanupService(config, db)
        result = service.run()

        assert result.nights_cleaned == 0
        assert result.bytes_freed == 0
        db.close()


class TestCleanupRetentionDays:
    def test_cleanup_retention_days(self, cleanup_env):
        """N日より古い夜が削除される。"""
        config, db, download_dir = cleanup_env

        # 40日前と10日前のデータ
        old_date = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")
        recent_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

        old_files = _seed_night(db, download_dir, old_date)
        recent_files = _seed_night(db, download_dir, recent_date)

        # retention_days = 30 に設定
        db.settings.set_many({
            "cleanup.mode": "retention_days",
            "cleanup.retention_days": "30",
        })

        service = CleanupService(config, db)
        result = service.run()

        assert result.nights_cleaned == 1
        assert result.bytes_freed > 0
        # 古いファイルが削除されている
        for f in old_files:
            assert not f.exists()
        # 新しいファイルは残っている
        for f in recent_files:
            assert f.exists()
        db.close()

    def test_cleanup_preserves_recent(self, cleanup_env):
        """新しい夜は削除しない。"""
        config, db, download_dir = cleanup_env

        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        recent_files = _seed_night(db, download_dir, recent_date)

        db.settings.set_many({
            "cleanup.mode": "retention_days",
            "cleanup.retention_days": "30",
        })

        service = CleanupService(config, db)
        result = service.run()

        assert result.nights_cleaned == 0
        for f in recent_files:
            assert f.exists()
        db.close()


class TestCleanupMinFreeGb:
    def test_cleanup_min_free_gb(self, cleanup_env):
        """空き容量不足時に古い夜から削除する。"""
        config, db, download_dir = cleanup_env

        _seed_night(db, download_dir, "20250101", file_size=1024)
        _seed_night(db, download_dir, "20250102", file_size=1024)

        db.settings.set_many({
            "cleanup.mode": "min_free_gb",
            "cleanup.min_free_gb": "10",
        })

        # disk_usage をモックして空き容量不足→十分な状態に変化させる
        call_count = 0
        original_disk_usage = __import__("shutil").disk_usage

        def mock_disk_usage(path):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # 空き容量不足
                return type(original_disk_usage("/"))( # noqa: E501
                    total=100 * 1024**3, used=95 * 1024**3, free=5 * 1024**3,
                )
            else:
                # 十分な空き容量
                return type(original_disk_usage("/"))(
                    total=100 * 1024**3, used=80 * 1024**3, free=20 * 1024**3,
                )

        with patch("shutil.disk_usage", side_effect=mock_disk_usage):
            service = CleanupService(config, db)
            result = service.run()

        assert result.nights_cleaned >= 1
        db.close()


class TestCleanupMinFreePct:
    def test_cleanup_min_free_pct(self, cleanup_env):
        """空き容量%不足時に古い夜から削除する。"""
        config, db, download_dir = cleanup_env

        _seed_night(db, download_dir, "20250101", file_size=1024)
        _seed_night(db, download_dir, "20250102", file_size=1024)

        db.settings.set_many({
            "cleanup.mode": "min_free_pct",
            "cleanup.min_free_pct": "20",
        })

        call_count = 0
        original_disk_usage = __import__("shutil").disk_usage

        def mock_disk_usage(path):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # 空き容量10%
                return type(original_disk_usage("/"))(
                    total=100 * 1024**3, used=90 * 1024**3, free=10 * 1024**3,
                )
            else:
                # 空き容量25%
                return type(original_disk_usage("/"))(
                    total=100 * 1024**3, used=75 * 1024**3, free=25 * 1024**3,
                )

        with patch("shutil.disk_usage", side_effect=mock_disk_usage):
            service = CleanupService(config, db)
            result = service.run()

        assert result.nights_cleaned >= 1
        db.close()


class TestDeleteNightDownloads:
    def test_delete_night_downloads(self, cleanup_env):
        """特定夜のダウンロード削除。"""
        config, db, download_dir = cleanup_env

        files = _seed_night(db, download_dir, "20250101", file_size=2048)

        service = CleanupService(config, db)
        bytes_freed = service.delete_night_downloads("20250101")

        assert bytes_freed > 0
        for f in files:
            assert not f.exists()
        db.close()

    def test_delete_clears_local_path(self, cleanup_env):
        """削除後に local_path が NULL になる。"""
        config, db, download_dir = cleanup_env

        _seed_night(db, download_dir, "20250101")

        service = CleanupService(config, db)
        service.delete_night_downloads("20250101")

        clips = db.clips.get_clips_with_local_path("20250101")
        assert len(clips) == 0
        db.close()

    def test_delete_removes_empty_dirs(self, cleanup_env):
        """削除後に空ディレクトリが除去される。"""
        config, db, download_dir = cleanup_env

        _seed_night(db, download_dir, "20250101", file_count=1)

        service = CleanupService(config, db)
        service.delete_night_downloads("20250101")

        # 日付ディレクトリが削除されているはず
        assert not (download_dir / "20250101").exists()
        db.close()

    def test_safe_path_check(self, cleanup_env):
        """ダウンロードディレクトリ外のパスは削除しない。"""
        config, db, download_dir = cleanup_env

        # output_dir 内にファイルを作成して local_path に設定
        outside_file = config.paths.resolve_output_dir() / "dangerous.mp4"
        outside_file.parent.mkdir(parents=True, exist_ok=True)
        outside_file.write_bytes(b"\x00" * 100)

        db.clips.upsert_clip(
            "http://cam/20250101/22/00.mp4", "20250101", 22, 0,
            local_path=str(outside_file),
            status=ClipStatus.DETECTED,
        )
        db.nights.upsert_output("20250101", detection_count=1)

        service = CleanupService(config, db)
        bytes_freed = service.delete_night_downloads("20250101")

        # ダウンロードディレクトリ外のファイルは削除されない
        assert bytes_freed == 0
        assert outside_file.exists()
        db.close()


class TestStorageInfo:
    def test_get_storage_info(self, cleanup_env):
        """ストレージ情報が返される。"""
        config, db, download_dir = cleanup_env

        _seed_night(db, download_dir, "20250101", file_size=4096)

        service = CleanupService(config, db)
        info = service.get_storage_info()

        assert "disk_total" in info
        assert "disk_free" in info
        assert "disk_free_pct" in info
        assert "downloads_size" in info
        assert info["downloads_size"] > 0
        assert "night_sizes" in info
        assert len(info["night_sizes"]) >= 1
        db.close()
