"""Pydantic configuration models and YAML loader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atomcam_meteor.exceptions import ConfigError


def _resolve(path_str: str) -> Path:
    """Expand user home and resolve a path string."""
    return Path(path_str).expanduser()


class CameraConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = "atomcam.local"
    http_user: Optional[str] = None
    http_password: Optional[str] = None
    base_path: str = "sdcard/record"
    timeout_sec: int = 10
    retry_count: int = 3


class DetectionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_line_length: int = 30
    canny_threshold1: int = 100
    canny_threshold2: int = 200
    hough_threshold: int = 25
    max_line_gap: int = 5
    exposure_duration_sec: float = 1.0
    clip_margin_sec: float = 0.5
    mask_path: Optional[str] = None
    exclude_bottom_pct: float = 0

    @field_validator("exclude_bottom_pct")
    @classmethod
    def _validate_exclude_bottom_pct(cls, v: float) -> float:
        if not 0 <= v <= 50:
            raise ValueError(f"exclude_bottom_pct は 0〜50 の範囲で指定してください: {v}")
        return v


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_time: str = "22:00"
    end_time: str = "06:00"

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_time_format(cls, v: str) -> str:
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"時刻は HH:MM 形式で指定してください: {v!r}")
        h, m = int(v[:2]), int(v[3:])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"無効な時刻です: {v!r}")
        return v


class PathsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    download_dir: str = "~/atomcam/downloads"
    output_dir: str = "~/atomcam/output"
    db_path: str = "~/atomcam/state.db"
    lock_path: str = "~/atomcam/.lock"

    def resolve(self, field: str) -> Path:
        """Resolve a path field by name."""
        value = getattr(self, field, None)
        if value is None:
            raise ConfigError(f"Unknown path field: {field}")
        return _resolve(value)

    def resolve_download_dir(self) -> Path:
        return _resolve(self.download_dir)

    def resolve_output_dir(self) -> Path:
        return _resolve(self.output_dir)

    def resolve_db_path(self) -> Path:
        return _resolve(self.db_path)

    def resolve_lock_path(self) -> Path:
        return _resolve(self.lock_path)


class WebConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = "0.0.0.0"
    port: int = 8080


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    camera: CameraConfig = Field(default_factory=CameraConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    web: WebConfig = Field(default_factory=WebConfig)


def load_config(path: str | Path) -> AppConfig:
    """Load configuration from a YAML file."""
    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"Configuration validation failed: {exc}") from exc
