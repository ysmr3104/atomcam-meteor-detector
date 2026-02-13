"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from atomcam_meteor.config import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    PathsConfig,
    ScheduleConfig,
    WebConfig,
    load_config,
)
from atomcam_meteor.exceptions import ConfigError


class TestCameraConfig:
    def test_defaults(self):
        config = CameraConfig()
        assert config.host == "atomcam.local"
        assert config.timeout_sec == 10
        assert config.retry_count == 3

    def test_frozen(self):
        config = CameraConfig()
        with pytest.raises(ValidationError):
            config.host = "other"


class TestDetectionConfig:
    def test_defaults(self):
        config = DetectionConfig()
        assert config.min_line_length == 30
        assert config.mask_path is None

    def test_frozen(self):
        config = DetectionConfig()
        with pytest.raises(ValidationError):
            config.min_line_length = 50


class TestScheduleConfig:
    def test_defaults(self):
        config = ScheduleConfig()
        assert config.start_time == "22:00"
        assert config.end_time == "06:00"

    def test_custom_values(self):
        config = ScheduleConfig(start_time="20:30", end_time="05:15")
        assert config.start_time == "20:30"
        assert config.end_time == "05:15"

    def test_frozen(self):
        config = ScheduleConfig()
        with pytest.raises(ValidationError):
            config.start_time = "23:00"

    def test_invalid_format_no_colon(self):
        with pytest.raises(ValidationError, match="HH:MM"):
            ScheduleConfig(start_time="2200")

    def test_invalid_format_letters(self):
        with pytest.raises(ValidationError, match="HH:MM"):
            ScheduleConfig(start_time="abc")

    def test_invalid_hour(self):
        with pytest.raises(ValidationError, match="無効な時刻"):
            ScheduleConfig(start_time="25:00")

    def test_invalid_minute(self):
        with pytest.raises(ValidationError, match="無効な時刻"):
            ScheduleConfig(end_time="22:60")

    def test_boundary_values(self):
        config = ScheduleConfig(start_time="00:00", end_time="23:59")
        assert config.start_time == "00:00"
        assert config.end_time == "23:59"


class TestPathsConfig:
    def test_resolve_download_dir(self):
        config = PathsConfig(download_dir="~/test/downloads")
        resolved = config.resolve_download_dir()
        assert isinstance(resolved, Path)
        assert "~" not in str(resolved)

    def test_resolve_generic(self):
        config = PathsConfig(download_dir="/tmp/dl")
        assert config.resolve("download_dir") == Path("/tmp/dl")

    def test_resolve_unknown_field(self):
        config = PathsConfig()
        with pytest.raises(ConfigError):
            config.resolve("nonexistent")


class TestWebConfig:
    def test_defaults(self):
        config = WebConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8080


class TestAppConfig:
    def test_defaults(self):
        config = AppConfig()
        assert isinstance(config.camera, CameraConfig)
        assert isinstance(config.web, WebConfig)

    def test_frozen(self):
        config = AppConfig()
        with pytest.raises(ValidationError):
            config.camera = CameraConfig()


class TestLoadConfig:
    def test_load_yaml(self, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            "camera:\n  host: 192.168.1.100\nweb:\n  port: 9090\n"
        )
        config = load_config(config_file)
        assert config.camera.host == "192.168.1.100"
        assert config.web.port == 9090

    def test_missing_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/settings.yaml")

    def test_invalid_yaml(self, tmp_path):
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("{{invalid yaml")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config(config_file)

    def test_empty_yaml(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        config = load_config(config_file)
        assert isinstance(config, AppConfig)
