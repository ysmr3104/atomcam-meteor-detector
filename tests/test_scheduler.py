"""スケジューラモジュールのテスト。"""

from __future__ import annotations

import asyncio
from datetime import datetime, date
from unittest.mock import MagicMock, patch

import pytest

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.scheduler import (
    PipelineScheduler,
    SchedulerStatus,
    _parse_time,
)


@pytest.fixture
def config(tmp_path):
    """テスト用の AppConfig。"""
    return AppConfig.model_validate({
        "paths": {
            "download_dir": str(tmp_path / "downloads"),
            "output_dir": str(tmp_path / "output"),
            "db_path": str(tmp_path / "test.db"),
            "lock_path": str(tmp_path / "test.lock"),
        }
    })


@pytest.fixture
def scheduler(config):
    """テスト用のスケジューラインスタンス。"""
    return PipelineScheduler(config)


class TestParseTime:
    def test_basic(self):
        assert _parse_time("22:00") == (22, 0)
        assert _parse_time("06:30") == (6, 30)
        assert _parse_time("00:00") == (0, 0)
        assert _parse_time("23:59") == (23, 59)


class TestSchedulerStatus:
    def test_default_values(self):
        status = SchedulerStatus()
        assert status.enabled is False
        assert status.running is False
        assert status.pipeline_running is False
        assert status.interval_minutes == 15

    def test_to_dict(self):
        status = SchedulerStatus(enabled=True, running=True)
        d = status.to_dict()
        assert d["enabled"] is True
        assert d["running"] is True
        assert isinstance(d, dict)


class TestIsInObservationWindow:
    """_is_in_observation_window の直接テスト。"""

    def test_within_cross_midnight(self):
        """日付またぎの観測時間帯内（23:00、22:00-06:00）"""
        now = datetime(2025, 1, 1, 23, 0)
        assert PipelineScheduler._is_in_observation_window(now, "22:00", "06:00") is True

    def test_within_cross_midnight_early_morning(self):
        """日付またぎの観測時間帯内（03:00、22:00-06:00）"""
        now = datetime(2025, 1, 2, 3, 0)
        assert PipelineScheduler._is_in_observation_window(now, "22:00", "06:00") is True

    def test_outside_cross_midnight(self):
        """日付またぎの観測時間帯外（12:00、22:00-06:00）"""
        now = datetime(2025, 1, 1, 12, 0)
        assert PipelineScheduler._is_in_observation_window(now, "22:00", "06:00") is False

    def test_boundary_start(self):
        """開始時刻ちょうど（22:00）"""
        now = datetime(2025, 1, 1, 22, 0)
        assert PipelineScheduler._is_in_observation_window(now, "22:00", "06:00") is True

    def test_boundary_end(self):
        """終了時刻ちょうど（06:00）→ 時間帯外"""
        now = datetime(2025, 1, 2, 6, 0)
        assert PipelineScheduler._is_in_observation_window(now, "22:00", "06:00") is False

    def test_same_day_window(self):
        """同日内の観測時間帯（01:00-05:00、03:00）"""
        now = datetime(2025, 1, 1, 3, 0)
        assert PipelineScheduler._is_in_observation_window(now, "01:00", "05:00") is True

    def test_same_day_outside(self):
        """同日内の観測時間帯外（01:00-05:00、06:00）"""
        now = datetime(2025, 1, 1, 6, 0)
        assert PipelineScheduler._is_in_observation_window(now, "01:00", "05:00") is False


class TestIsInActiveWindow:
    """_is_in_active_window（バッファ付き）のテスト。"""

    def test_within_window(self):
        """観測時間帯内"""
        now = datetime(2025, 1, 1, 23, 0)
        assert PipelineScheduler._is_in_active_window(now, "22:00", "06:00", 15) is True

    def test_within_buffer(self):
        """終了時刻後のバッファ内（06:10、バッファ15分）"""
        now = datetime(2025, 1, 2, 6, 10)
        assert PipelineScheduler._is_in_active_window(now, "22:00", "06:00", 15) is True

    def test_outside_buffer(self):
        """バッファ外（06:20、バッファ15分）"""
        now = datetime(2025, 1, 2, 6, 20)
        assert PipelineScheduler._is_in_active_window(now, "22:00", "06:00", 15) is False


class TestDetermineDate:
    def test_before_noon(self):
        """正午前は今日の日付"""
        now = datetime(2025, 1, 15, 8, 0)
        assert PipelineScheduler._determine_date(now) == "20250115"

    def test_after_noon(self):
        """正午以降は明日の日付"""
        now = datetime(2025, 1, 15, 14, 0)
        assert PipelineScheduler._determine_date(now) == "20250116"


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_stop(self, scheduler):
        """起動と停止が正常に動作する"""
        # ループが DB アクセスする前にすぐ停止
        await scheduler.start()
        assert scheduler.status.running is True
        assert scheduler._task is not None

        await scheduler.stop()
        assert scheduler.status.running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_double_start(self, scheduler):
        """二重起動でも問題ない"""
        await scheduler.start()
        task1 = scheduler._task
        await scheduler.start()  # 二重起動
        assert scheduler._task is task1  # 同じタスク

        await scheduler.stop()


class TestExecutePipelineSync:
    def test_successful_execution(self, scheduler, tmp_path):
        """パイプライン正常実行"""
        (tmp_path / "downloads").mkdir(exist_ok=True)
        (tmp_path / "output").mkdir(exist_ok=True)

        with patch("atomcam_meteor.pipeline.Pipeline") as mock_pipeline_cls:
            mock_pipeline = MagicMock()
            mock_result = MagicMock()
            mock_result.detections_found = 5
            mock_pipeline.execute.return_value = mock_result
            mock_pipeline_cls.return_value = mock_pipeline

            result = scheduler._execute_pipeline_sync("20250101")
            assert result == "completed"
            assert scheduler.status.last_run_detections == 5

    def test_lock_taken(self, scheduler, tmp_path):
        """ロック取得失敗時にスキップ"""
        from atomcam_meteor.services.lock import FileLock

        (tmp_path / "downloads").mkdir(exist_ok=True)
        (tmp_path / "output").mkdir(exist_ok=True)
        lock_path = scheduler._config.paths.resolve_lock_path()
        with FileLock(lock_path):
            result = scheduler._execute_pipeline_sync("20250101")
            assert result == "skipped_lock"


class TestCheckReboot:
    def test_reboot_disabled(self, scheduler):
        """リブート無効時は何もしない"""
        now = datetime(2025, 1, 1, 12, 0)
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, False, "12:00", "22:00", "06:00")
            mock_sub.run.assert_not_called()

    def test_reboot_time_match(self, scheduler):
        """リブート時刻に一致→リブート実行"""
        now = datetime(2025, 1, 1, 12, 0)
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, True, "12:00", "22:00", "06:00")
            mock_sub.run.assert_called_once_with(["sudo", "reboot"])
            assert scheduler._last_reboot_date == now.date()

    def test_reboot_time_mismatch(self, scheduler):
        """リブート時刻に不一致→リブートしない"""
        now = datetime(2025, 1, 1, 14, 0)
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, True, "12:00", "22:00", "06:00")
            mock_sub.run.assert_not_called()

    def test_reboot_already_done_today(self, scheduler):
        """今日既にリブート済み→スキップ"""
        now = datetime(2025, 1, 1, 12, 0)
        scheduler._last_reboot_date = now.date()
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, True, "12:00", "22:00", "06:00")
            mock_sub.run.assert_not_called()

    def test_reboot_during_observation(self, scheduler):
        """観測時間帯中はリブートしない"""
        now = datetime(2025, 1, 1, 23, 0)
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, True, "23:00", "22:00", "06:00")
            mock_sub.run.assert_not_called()

    def test_reboot_within_tolerance(self, scheduler):
        """リブート時刻の±5分以内→リブート実行"""
        now = datetime(2025, 1, 1, 12, 3)
        with patch("atomcam_meteor.services.scheduler.subprocess") as mock_sub:
            scheduler._check_reboot(now, True, "12:00", "22:00", "06:00")
            mock_sub.run.assert_called_once_with(["sudo", "reboot"])
