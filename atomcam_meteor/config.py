"""Pydantic configuration models and YAML loader."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

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
    exposure_duration_sec: float = 1.0
    clip_margin_sec: float = 0.5
    mask_path: Optional[str] = None


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    prev_date_hours: list[int] = Field(default_factory=lambda: [22, 23])
    curr_date_hours: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])


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
