"""Tests for the downloader module."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from atomcam_meteor.config import CameraConfig
from atomcam_meteor.exceptions import DownloadError
from atomcam_meteor.modules.downloader import Downloader


@pytest.fixture
def cam_config():
    return CameraConfig(host="testcam.local", retry_count=2, timeout_sec=5)


@pytest.fixture
def downloader(cam_config):
    return Downloader(cam_config)


class TestListClips:
    @respx.mock
    def test_parse_directory_listing(self, downloader):
        url = "http://testcam.local/sdcard/record/20250101/22/"
        respx.get(url).respond(
            200,
            text='<a href="00.mp4">00.mp4</a><a href="01.mp4">01.mp4</a><a href="other.txt">other</a>',
        )
        clips = downloader.list_clips("20250101", 22)
        assert len(clips) == 2
        assert clips[0].endswith("00.mp4")
        assert clips[1].endswith("01.mp4")

    @respx.mock
    def test_empty_on_error(self, downloader):
        url = "http://testcam.local/sdcard/record/20250101/22/"
        respx.get(url).respond(500)
        with patch("atomcam_meteor.modules.downloader.time.sleep"):
            clips = downloader.list_clips("20250101", 22)
        assert clips == []

    @respx.mock
    def test_list_clips_retry_success(self, downloader):
        """list_clips should retry and succeed on second attempt."""
        url = "http://testcam.local/sdcard/record/20250101/22/"
        respx.get(url).mock(side_effect=[
            httpx.ConnectError("fail"),
            httpx.Response(200, text='<a href="00.mp4">00.mp4</a>'),
        ])
        with patch("atomcam_meteor.modules.downloader.time.sleep"):
            clips = downloader.list_clips("20250101", 22)
        assert len(clips) == 1
        assert clips[0].endswith("00.mp4")


class TestDownloadClip:
    @respx.mock
    def test_successful_download(self, downloader, tmp_path):
        url = "http://testcam.local/sdcard/record/20250101/22/00.mp4"
        respx.get(url).respond(200, content=b"fake video data")
        result = downloader.download_clip(url, tmp_path)
        assert result.exists()
        assert result.read_bytes() == b"fake video data"
        assert result == tmp_path / "20250101" / "22" / "00.mp4"

    @respx.mock
    def test_skip_existing(self, downloader, tmp_path):
        url = "http://testcam.local/sdcard/record/20250101/22/00.mp4"
        local = tmp_path / "20250101" / "22" / "00.mp4"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"existing")
        result = downloader.download_clip(url, tmp_path)
        assert result == local

    @respx.mock
    def test_retry_and_fail(self, downloader, tmp_path):
        url = "http://testcam.local/sdcard/record/20250101/22/00.mp4"
        respx.get(url).mock(side_effect=httpx.ConnectError("fail"))
        with patch("atomcam_meteor.modules.downloader.time.sleep"):
            with pytest.raises(DownloadError, match="Failed to download"):
                downloader.download_clip(url, tmp_path)


class TestDownloadHour:
    @respx.mock
    def test_downloads_all_clips(self, downloader, tmp_path):
        list_url = "http://testcam.local/sdcard/record/20250101/22/"
        respx.get(list_url).respond(200, text='<a href="00.mp4">00.mp4</a>')
        clip_url = f"{list_url}00.mp4"
        respx.get(clip_url).respond(200, content=b"data")
        results = downloader.download_hour("20250101", 22, tmp_path)
        assert len(results) == 1
        assert results[0][0] == clip_url
