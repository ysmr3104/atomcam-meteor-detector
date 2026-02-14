"""都道府県プリセットのテスト。"""

import pytest

from atomcam_meteor.services.prefectures import PREFECTURES, get_coordinates


class TestPrefectures:
    def test_all_47_prefectures(self):
        """47都道府県が登録されていること"""
        assert len(PREFECTURES) == 47

    def test_all_values_are_tuples(self):
        """全エントリが (float, float) のタプルであること"""
        for name, coords in PREFECTURES.items():
            assert isinstance(coords, tuple), f"{name} の座標がタプルではない"
            assert len(coords) == 2, f"{name} の座標が2要素ではない"
            lat, lon = coords
            assert isinstance(lat, float), f"{name} の緯度が float ではない"
            assert isinstance(lon, float), f"{name} の経度が float ではない"

    def test_latitude_range(self):
        """日本の緯度範囲（20〜46度）に収まること"""
        for name, (lat, _) in PREFECTURES.items():
            assert 20.0 <= lat <= 46.0, f"{name} の緯度 {lat} が範囲外"

    def test_longitude_range(self):
        """日本の経度範囲（127〜146度）に収まること"""
        for name, (_, lon) in PREFECTURES.items():
            assert 127.0 <= lon <= 146.0, f"{name} の経度 {lon} が範囲外"

    def test_get_coordinates_tokyo(self):
        """東京都の座標が取得できること"""
        lat, lon = get_coordinates("東京都")
        assert 35.0 < lat < 36.0
        assert 139.0 < lon < 140.0

    def test_get_coordinates_hokkaido(self):
        """北海道の座標が取得できること"""
        lat, lon = get_coordinates("北海道")
        assert 42.0 < lat < 44.0

    def test_get_coordinates_okinawa(self):
        """沖縄県の座標が取得できること"""
        lat, lon = get_coordinates("沖縄県")
        assert 25.0 < lat < 27.0

    def test_get_coordinates_unknown_raises(self):
        """未知の都道府県名で KeyError が発生すること"""
        with pytest.raises(KeyError, match="未知の都道府県"):
            get_coordinates("アトランティス県")

    def test_known_prefectures_present(self):
        """主要な都道府県が含まれていること"""
        expected = ["北海道", "東京都", "大阪府", "京都府", "沖縄県", "福岡県", "愛知県"]
        for name in expected:
            assert name in PREFECTURES, f"{name} が登録されていない"
