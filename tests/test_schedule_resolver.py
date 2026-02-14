"""スケジュール解決モジュールのテスト。"""

import sqlite3

import pytest

from atomcam_meteor.config import DetectionConfig, ScheduleConfig
from atomcam_meteor.services.db import SettingsRepository, _SETTINGS_TABLE
from atomcam_meteor.services.schedule_resolver import (
    get_current_detection_settings,
    get_current_settings,
    resolve_detection_config,
    resolve_schedule,
)


@pytest.fixture
def settings_repo():
    """インメモリの SettingsRepository を作成する。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_SETTINGS_TABLE)
    conn.commit()
    repo = SettingsRepository(conn)
    yield repo
    conn.close()


@pytest.fixture
def yaml_schedule():
    """デフォルトの YAML ScheduleConfig。"""
    return ScheduleConfig()


class TestResolveScheduleFixed:
    def test_default_yaml_fallback(self, yaml_schedule):
        """DB 設定なしの場合、YAML デフォルトにフォールバック"""
        start, end = resolve_schedule(None, yaml_schedule, "20250101")
        assert start == "22:00"
        assert end == "06:00"

    def test_empty_db_fallback(self, settings_repo, yaml_schedule):
        """DB が空の場合も YAML デフォルトにフォールバック"""
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert start == "22:00"
        assert end == "06:00"

    def test_db_fixed_overrides_yaml(self, settings_repo, yaml_schedule):
        """DB の fixed 設定が YAML を上書きする"""
        settings_repo.set_many({
            "schedule.start_mode": "fixed",
            "schedule.start_time": "21:00",
            "schedule.end_mode": "fixed",
            "schedule.end_time": "05:00",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert start == "21:00"
        assert end == "05:00"


class TestResolveScheduleTwilight:
    def test_twilight_mode_returns_valid_time(self, settings_repo, yaml_schedule):
        """twilight モードで有効な HH:MM 形式が返る"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight",
            "schedule.end_mode": "twilight",
            "schedule.location_mode": "preset",
            "schedule.prefecture": "東京都",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        # HH:MM 形式
        assert len(start) == 5 and start[2] == ":"
        assert len(end) == 5 and end[2] == ":"

    def test_twilight_with_custom_location(self, settings_repo, yaml_schedule):
        """カスタム座標での twilight モード"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight",
            "schedule.end_mode": "fixed",
            "schedule.end_time": "06:00",
            "schedule.location_mode": "custom",
            "schedule.latitude": "35.6762",
            "schedule.longitude": "139.6503",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert len(start) == 5 and start[2] == ":"
        assert end == "06:00"


class TestResolveScheduleTwilightOffset:
    def test_start_offset(self, settings_repo, yaml_schedule):
        """twilight_offset モードでオフセットが適用される"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight_offset",
            "schedule.start_offset_minutes": "-30",
            "schedule.end_mode": "fixed",
            "schedule.end_time": "06:00",
            "schedule.location_mode": "preset",
            "schedule.prefecture": "東京都",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert len(start) == 5 and start[2] == ":"
        assert end == "06:00"

    def test_end_offset(self, settings_repo, yaml_schedule):
        """終了時刻の twilight_offset モード"""
        settings_repo.set_many({
            "schedule.start_mode": "fixed",
            "schedule.start_time": "22:00",
            "schedule.end_mode": "twilight_offset",
            "schedule.end_offset_minutes": "30",
            "schedule.location_mode": "preset",
            "schedule.prefecture": "東京都",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert start == "22:00"
        assert len(end) == 5 and end[2] == ":"


class TestGetCurrentSettings:
    def test_defaults(self, yaml_schedule):
        """DB なしの場合のデフォルト値"""
        settings = get_current_settings(None, yaml_schedule)
        assert settings["start_mode"] == "fixed"
        assert settings["start_time"] == "22:00"
        assert settings["end_mode"] == "fixed"
        assert settings["end_time"] == "06:00"
        assert settings["location_mode"] == "preset"
        assert settings["prefecture"] == "東京都"

    def test_db_values(self, settings_repo, yaml_schedule):
        """DB 値が反映される"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight",
            "schedule.prefecture": "大阪府",
        })
        settings = get_current_settings(settings_repo, yaml_schedule)
        assert settings["start_mode"] == "twilight"
        assert settings["prefecture"] == "大阪府"
        # 未設定の値はデフォルト
        assert settings["end_mode"] == "fixed"


class TestLocationResolution:
    def test_invalid_custom_coordinates_fallback(self, settings_repo, yaml_schedule):
        """無効なカスタム座標でプリセットにフォールバック"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight",
            "schedule.end_mode": "fixed",
            "schedule.end_time": "06:00",
            "schedule.location_mode": "custom",
            "schedule.latitude": "invalid",
            "schedule.longitude": "invalid",
        })
        # フォールバックして東京都の座標を使用する
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert len(start) == 5 and start[2] == ":"

    def test_unknown_prefecture_fallback(self, settings_repo, yaml_schedule):
        """未知の都道府県で東京都にフォールバック"""
        settings_repo.set_many({
            "schedule.start_mode": "twilight",
            "schedule.end_mode": "fixed",
            "schedule.end_time": "06:00",
            "schedule.location_mode": "preset",
            "schedule.prefecture": "存在しない県",
        })
        start, end = resolve_schedule(settings_repo, yaml_schedule, "20250101")
        assert len(start) == 5 and start[2] == ":"


# ── 検出パラメータ解決テスト ─────────────────────────────────────────


@pytest.fixture
def yaml_detection():
    """デフォルトの YAML DetectionConfig。"""
    return DetectionConfig()


class TestResolveDetectionConfig:
    def test_no_db_returns_yaml(self, yaml_detection):
        """DB なしの場合、YAML 値がそのまま返る"""
        result = resolve_detection_config(None, yaml_detection)
        assert result is yaml_detection

    def test_empty_db_returns_yaml(self, settings_repo, yaml_detection):
        """DB が空の場合、YAML 値がそのまま返る"""
        result = resolve_detection_config(settings_repo, yaml_detection)
        assert result is yaml_detection

    def test_db_overrides_all(self, settings_repo, yaml_detection):
        """DB 値が全てオーバーライドされる"""
        settings_repo.set_many({
            "detection.min_line_length": "50",
            "detection.canny_threshold1": "80",
            "detection.canny_threshold2": "160",
            "detection.hough_threshold": "30",
            "detection.max_line_gap": "10",
            "detection.min_line_brightness": "25.5",
            "detection.exclude_bottom_pct": "10.0",
        })
        result = resolve_detection_config(settings_repo, yaml_detection)
        assert result is not yaml_detection
        assert result.min_line_length == 50
        assert result.canny_threshold1 == 80
        assert result.canny_threshold2 == 160
        assert result.hough_threshold == 30
        assert result.max_line_gap == 10
        assert result.min_line_brightness == 25.5
        assert result.exclude_bottom_pct == 10.0

    def test_partial_db_override(self, settings_repo, yaml_detection):
        """一部のキーだけ DB に設定→混合値が返る"""
        settings_repo.set_many({
            "detection.min_line_length": "40",
            "detection.hough_threshold": "35",
        })
        result = resolve_detection_config(settings_repo, yaml_detection)
        assert result is not yaml_detection
        assert result.min_line_length == 40
        assert result.hough_threshold == 35
        # 未設定の値は YAML デフォルト
        assert result.canny_threshold1 == 100
        assert result.canny_threshold2 == 200
        assert result.max_line_gap == 5
        assert result.min_line_brightness == 20.0
        assert result.exclude_bottom_pct == 0

    def test_unrelated_keys_ignored(self, settings_repo, yaml_detection):
        """detection.* 以外のキーは無視される"""
        settings_repo.set_many({
            "schedule.start_time": "21:00",
        })
        result = resolve_detection_config(settings_repo, yaml_detection)
        assert result is yaml_detection


class TestGetCurrentDetectionSettings:
    def test_defaults_without_db(self, yaml_detection):
        """DB なしの場合のデフォルト値"""
        result = get_current_detection_settings(None, yaml_detection)
        assert result["min_line_length"] == "30"
        assert result["canny_threshold1"] == "100"
        assert result["canny_threshold2"] == "200"
        assert result["hough_threshold"] == "25"
        assert result["max_line_gap"] == "5"
        assert result["min_line_brightness"] == "20.0"
        assert result["exclude_bottom_pct"] == "0"

    def test_db_values_override(self, settings_repo, yaml_detection):
        """DB 値が優先される"""
        settings_repo.set_many({
            "detection.min_line_length": "50",
            "detection.min_line_brightness": "30.0",
        })
        result = get_current_detection_settings(settings_repo, yaml_detection)
        assert result["min_line_length"] == "50"
        assert result["min_line_brightness"] == "30.0"
        # 未設定の値は YAML デフォルト
        assert result["canny_threshold1"] == "100"
