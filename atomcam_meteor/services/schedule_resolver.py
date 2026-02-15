"""スケジュール解決モジュール — DB設定 → YAML フォールバック → 薄明計算の統合。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from atomcam_meteor.config import DetectionConfig, ScheduleConfig
from atomcam_meteor.services.prefectures import get_coordinates
from atomcam_meteor.services.twilight import resolve_end_time, resolve_start_time

logger = logging.getLogger(__name__)

# SettingsRepository はオプション依存なので TYPE_CHECKING で型のみ参照
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atomcam_meteor.services.db import SettingsRepository

# デフォルト値
_DEFAULT_START_MODE = "fixed"
_DEFAULT_END_MODE = "fixed"
_DEFAULT_LOCATION_MODE = "preset"
_DEFAULT_PREFECTURE = "東京都"
_DEFAULT_OFFSET = "0"
_DEFAULT_INTERVAL_MINUTES = "60"
_DEFAULT_REBOOT_ENABLED = "false"
_DEFAULT_REBOOT_TIME = "12:00"


def resolve_schedule(
    settings: SettingsRepository | None,
    yaml_schedule: ScheduleConfig,
    obs_date_str: str,
) -> tuple[str, str]:
    """観測日のスケジュール (開始時刻, 終了時刻) を "HH:MM" 形式で返す。

    1. DB の SettingsRepository から設定を読む
    2. 値がなければ YAML の ScheduleConfig デフォルトにフォールバック
    3. 位置情報を解決（preset → 都道府県座標 / custom → 直接指定）
    4. モードに応じて薄明計算を実行
    """
    # DB 設定を一括取得
    db_settings: dict[str, str] = {}
    if settings is not None:
        db_settings = settings.get_all()

    # 各設定値の解決（DB優先 → デフォルト）
    start_mode = db_settings.get("schedule.start_mode", _DEFAULT_START_MODE)
    start_time = db_settings.get("schedule.start_time", yaml_schedule.start_time)
    start_offset = int(
        db_settings.get("schedule.start_offset_minutes", _DEFAULT_OFFSET)
    )

    end_mode = db_settings.get("schedule.end_mode", _DEFAULT_END_MODE)
    end_time = db_settings.get("schedule.end_time", yaml_schedule.end_time)
    end_offset = int(
        db_settings.get("schedule.end_offset_minutes", _DEFAULT_OFFSET)
    )

    # 全て fixed モードならば薄明計算不要
    if start_mode == "fixed" and end_mode == "fixed":
        return start_time, end_time

    # 位置情報の解決
    lat, lon = _resolve_location(db_settings)

    # 観測日の date オブジェクト
    obs_date = datetime.strptime(obs_date_str, "%Y%m%d").date()

    resolved_start = resolve_start_time(
        start_mode, start_time, start_offset, obs_date, lat, lon,
    )
    resolved_end = resolve_end_time(
        end_mode, end_time, end_offset, obs_date, lat, lon,
    )

    logger.info(
        "スケジュール解決: %s → %s (start_mode=%s, end_mode=%s)",
        resolved_start, resolved_end, start_mode, end_mode,
    )
    return resolved_start, resolved_end


def get_current_settings(
    settings: SettingsRepository | None,
    yaml_schedule: ScheduleConfig,
) -> dict[str, str]:
    """現在の設定をフラットな辞書で返す（API レスポンス用）。"""
    db_settings: dict[str, str] = {}
    if settings is not None:
        db_settings = settings.get_all()

    return {
        "start_mode": db_settings.get("schedule.start_mode", _DEFAULT_START_MODE),
        "start_time": db_settings.get("schedule.start_time", yaml_schedule.start_time),
        "start_offset_minutes": db_settings.get(
            "schedule.start_offset_minutes", _DEFAULT_OFFSET
        ),
        "end_mode": db_settings.get("schedule.end_mode", _DEFAULT_END_MODE),
        "end_time": db_settings.get("schedule.end_time", yaml_schedule.end_time),
        "end_offset_minutes": db_settings.get(
            "schedule.end_offset_minutes", _DEFAULT_OFFSET
        ),
        "location_mode": db_settings.get(
            "schedule.location_mode", _DEFAULT_LOCATION_MODE
        ),
        "prefecture": db_settings.get("schedule.prefecture", _DEFAULT_PREFECTURE),
        "latitude": db_settings.get("schedule.latitude", ""),
        "longitude": db_settings.get("schedule.longitude", ""),
        "interval_minutes": db_settings.get(
            "schedule.interval_minutes",
            str(yaml_schedule.interval_minutes),
        ),
    }


def resolve_interval_minutes(
    settings: SettingsRepository | None,
    yaml_schedule: ScheduleConfig,
) -> int:
    """パイプライン実行間隔（分）を解決する。DB → YAML フォールバック。"""
    if settings is not None:
        db_val = settings.get("schedule.interval_minutes")
        if db_val is not None:
            try:
                return max(0, int(db_val))
            except ValueError:
                pass
    return yaml_schedule.interval_minutes


def resolve_reboot_settings(
    settings: SettingsRepository | None,
) -> tuple[bool, str]:
    """リブート設定を解決する。(有効/無効, 時刻) のタプルを返す。"""
    db_settings: dict[str, str] = {}
    if settings is not None:
        db_settings = settings.get_all()
    enabled_str = db_settings.get("system.reboot_enabled", _DEFAULT_REBOOT_ENABLED)
    reboot_time = db_settings.get("system.reboot_time", _DEFAULT_REBOOT_TIME)
    enabled = enabled_str.lower() in ("true", "1", "yes")
    return enabled, reboot_time


def get_current_system_settings(
    settings: SettingsRepository | None,
) -> dict[str, str]:
    """現在のシステム設定をフラットな辞書で返す（API レスポンス用）。"""
    db_settings: dict[str, str] = {}
    if settings is not None:
        db_settings = settings.get_all()
    return {
        "reboot_enabled": db_settings.get(
            "system.reboot_enabled", _DEFAULT_REBOOT_ENABLED
        ),
        "reboot_time": db_settings.get("system.reboot_time", _DEFAULT_REBOOT_TIME),
    }


def _resolve_location(db_settings: dict[str, str]) -> tuple[float, float]:
    """位置情報を解決して (緯度, 経度) を返す。"""
    location_mode = db_settings.get("schedule.location_mode", _DEFAULT_LOCATION_MODE)

    if location_mode == "custom":
        lat_str = db_settings.get("schedule.latitude", "")
        lon_str = db_settings.get("schedule.longitude", "")
        if lat_str and lon_str:
            try:
                return float(lat_str), float(lon_str)
            except ValueError:
                logger.warning("カスタム座標の解析に失敗。プリセットにフォールバック")

    # preset モードまたはフォールバック
    prefecture = db_settings.get("schedule.prefecture", _DEFAULT_PREFECTURE)
    try:
        return get_coordinates(prefecture)
    except KeyError:
        logger.warning(
            "都道府県 %r が見つかりません。東京都にフォールバック", prefecture,
        )
        return get_coordinates(_DEFAULT_PREFECTURE)


# ── 検出パラメータ解決 ──────────────────────────────────────────────

_DETECTION_KEYS: list[str] = [
    "min_line_length",
    "canny_threshold1",
    "canny_threshold2",
    "hough_threshold",
    "max_line_gap",
    "min_line_brightness",
    "exclude_bottom_pct",
]


def resolve_detection_config(
    settings: SettingsRepository | None,
    yaml_detection: DetectionConfig,
) -> DetectionConfig:
    """DB設定でDetectionConfigをオーバーライドする。DB値がなければYAML値を使用。"""
    if settings is None:
        return yaml_detection
    db_settings = settings.get_all()
    overrides: dict[str, Any] = {}
    for key in _DETECTION_KEYS:
        db_key = f"detection.{key}"
        val = db_settings.get(db_key)
        if val:
            annotation = DetectionConfig.model_fields[key].annotation
            if annotation is int:
                overrides[key] = int(val)
            else:
                overrides[key] = float(val)
    if not overrides:
        return yaml_detection
    base = yaml_detection.model_dump()
    base.update(overrides)
    return DetectionConfig.model_validate(base)


def get_current_detection_settings(
    settings: SettingsRepository | None,
    yaml_detection: DetectionConfig,
) -> dict[str, str]:
    """現在の検出設定をAPI応答用に返す。"""
    result: dict[str, str] = {}
    db_settings = settings.get_all() if settings else {}
    for key in _DETECTION_KEYS:
        db_key = f"detection.{key}"
        db_val = db_settings.get(db_key)
        result[key] = db_val if db_val else str(getattr(yaml_detection, key))
    return result
