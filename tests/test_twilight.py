"""天文薄明計算のテスト。"""

from datetime import date

import pytest

from atomcam_meteor.services.twilight import (
    get_evening_twilight_end,
    get_morning_twilight_start,
    resolve_end_time,
    resolve_start_time,
)

# 東京の座標
TOKYO_LAT = 35.6762
TOKYO_LON = 139.6503


class TestEveningTwilightEnd:
    def test_tokyo_summer_solstice(self):
        """東京の夏至（6/21）: 天文薄明終了は 19:30〜21:00 JST の範囲"""
        obs_date = date(2025, 6, 22)  # 前日 6/21 の夕方を計算
        dt = get_evening_twilight_end(obs_date, TOKYO_LAT, TOKYO_LON)
        assert 19 <= dt.hour <= 21, f"夏至の天文薄明終了: {dt}"

    def test_tokyo_winter_solstice(self):
        """東京の冬至（12/21）: 天文薄明終了は 17:30〜18:30 JST の範囲"""
        obs_date = date(2025, 12, 22)  # 前日 12/21 の夕方を計算
        dt = get_evening_twilight_end(obs_date, TOKYO_LAT, TOKYO_LON)
        assert 17 <= dt.hour <= 19, f"冬至の天文薄明終了: {dt}"


class TestMorningTwilightStart:
    def test_tokyo_summer_solstice(self):
        """東京の夏至（6/22）: 天文薄明開始は 2:00〜3:30 JST の範囲"""
        obs_date = date(2025, 6, 22)
        dt = get_morning_twilight_start(obs_date, TOKYO_LAT, TOKYO_LON)
        assert 2 <= dt.hour <= 4, f"夏至の天文薄明開始: {dt}"

    def test_tokyo_winter_solstice(self):
        """東京の冬至（12/22）: 天文薄明開始は 4:30〜5:30 JST の範囲"""
        obs_date = date(2025, 12, 22)
        dt = get_morning_twilight_start(obs_date, TOKYO_LAT, TOKYO_LON)
        assert 4 <= dt.hour <= 6, f"冬至の天文薄明開始: {dt}"


class TestResolveStartTime:
    def test_fixed_mode(self):
        """fixed モードでは fixed_time がそのまま返る"""
        result = resolve_start_time(
            "fixed", "22:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        assert result == "22:00"

    def test_twilight_mode(self):
        """twilight モードでは HH:MM 形式の時刻が返る"""
        result = resolve_start_time(
            "twilight", "22:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        # HH:MM 形式であること
        assert len(result) == 5
        assert result[2] == ":"
        h, m = int(result[:2]), int(result[3:])
        assert 0 <= h <= 23
        assert 0 <= m <= 59

    def test_twilight_offset_mode(self):
        """twilight_offset モードではオフセットが適用される"""
        base = resolve_start_time(
            "twilight", "22:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        offset = resolve_start_time(
            "twilight_offset", "22:00", -30, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        # -30 分オフセットなので base より早い時刻になるはず
        base_min = int(base[:2]) * 60 + int(base[3:])
        offset_min = int(offset[:2]) * 60 + int(offset[3:])
        assert offset_min == base_min - 30


class TestResolveEndTime:
    def test_fixed_mode(self):
        """fixed モードでは fixed_time がそのまま返る"""
        result = resolve_end_time(
            "fixed", "06:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        assert result == "06:00"

    def test_twilight_mode(self):
        """twilight モードでは HH:MM 形式の時刻が返る"""
        result = resolve_end_time(
            "twilight", "06:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        assert len(result) == 5
        assert result[2] == ":"

    def test_twilight_offset_positive(self):
        """twilight_offset モードで正のオフセット"""
        base = resolve_end_time(
            "twilight", "06:00", 0, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        offset = resolve_end_time(
            "twilight_offset", "06:00", 30, date(2025, 1, 1), TOKYO_LAT, TOKYO_LON,
        )
        base_min = int(base[:2]) * 60 + int(base[3:])
        offset_min = int(offset[:2]) * 60 + int(offset[3:])
        assert offset_min == base_min + 30
