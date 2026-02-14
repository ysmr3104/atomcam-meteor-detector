"""天文薄明の計算モジュール（astral ライブラリ使用）。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from astral import LocationInfo
from astral.sun import dawn, dusk

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))


def get_evening_twilight_end(obs_date: date, lat: float, lon: float) -> datetime:
    """夕方の天文薄明終了時刻（太陽高度 -18°）を JST で返す。

    obs_date は観測対象日（翌朝扱い）。前日の夕方を計算する。

    Raises:
        ValueError: 白夜等で天文薄明が終わらない場合
    """
    prev_day = obs_date - timedelta(days=1)
    loc = LocationInfo(latitude=lat, longitude=lon)
    # dusk(depression=18) = 天文薄明終了（太陽が地平線下18°に沈む時刻）
    dt: datetime = dusk(loc.observer, date=prev_day, depression=18, tzinfo=_JST)
    return dt


def get_morning_twilight_start(obs_date: date, lat: float, lon: float) -> datetime:
    """朝方の天文薄明開始時刻（太陽高度 -18°）を JST で返す。

    Raises:
        ValueError: 白夜等で天文薄明が終わらない場合
    """
    loc = LocationInfo(latitude=lat, longitude=lon)
    # dawn(depression=18) = 天文薄明開始（太陽が地平線下18°から昇り始める時刻）
    dt: datetime = dawn(loc.observer, date=obs_date, depression=18, tzinfo=_JST)
    return dt


def resolve_start_time(
    mode: str,
    fixed_time: str,
    offset_minutes: int,
    obs_date: date,
    lat: float,
    lon: float,
) -> str:
    """開始時刻を解決して "HH:MM" 形式で返す。

    mode:
        "fixed" — fixed_time をそのまま返す
        "twilight" — 夕方の天文薄明終了時刻を返す
        "twilight_offset" — 天文薄明終了時刻 + offset_minutes を返す
    """
    if mode == "fixed":
        return fixed_time
    try:
        twilight = get_evening_twilight_end(obs_date, lat, lon)
        if mode == "twilight_offset":
            twilight += timedelta(minutes=offset_minutes)
        return twilight.strftime("%H:%M")
    except ValueError:
        logger.warning(
            "天文薄明の計算に失敗（白夜など）。固定時刻 %s にフォールバック", fixed_time,
        )
        return fixed_time


def resolve_end_time(
    mode: str,
    fixed_time: str,
    offset_minutes: int,
    obs_date: date,
    lat: float,
    lon: float,
) -> str:
    """終了時刻を解決して "HH:MM" 形式で返す。

    mode:
        "fixed" — fixed_time をそのまま返す
        "twilight" — 朝方の天文薄明開始時刻を返す
        "twilight_offset" — 天文薄明開始時刻 + offset_minutes を返す
    """
    if mode == "fixed":
        return fixed_time
    try:
        twilight = get_morning_twilight_start(obs_date, lat, lon)
        if mode == "twilight_offset":
            twilight += timedelta(minutes=offset_minutes)
        return twilight.strftime("%H:%M")
    except ValueError:
        logger.warning(
            "天文薄明の計算に失敗（白夜など）。固定時刻 %s にフォールバック", fixed_time,
        )
        return fixed_time
