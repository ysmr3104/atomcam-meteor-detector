"""Tests for the concatenator module."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from atomcam_meteor.exceptions import ConcatenationError
from atomcam_meteor.modules.concatenator import Concatenator


@pytest.fixture
def concatenator():
    return Concatenator()


class TestConcatenator:
    def test_empty_raises(self, concatenator, tmp_path):
        with pytest.raises(ConcatenationError, match="No videos"):
            concatenator.concatenate([], tmp_path / "out.mp4")

    def test_single_video_copies(self, concatenator, tmp_path):
        src = tmp_path / "input.mp4"
        src.write_bytes(b"video data")
        out = tmp_path / "output" / "result.mp4"
        result = concatenator.concatenate([src], out)
        assert result.exists()
        assert result.read_bytes() == b"video data"

    @patch("atomcam_meteor.modules.concatenator.subprocess.run")
    def test_multiple_videos_ffmpeg(self, mock_run, concatenator, tmp_path):
        v1 = tmp_path / "v1.mp4"
        v2 = tmp_path / "v2.mp4"
        v1.write_bytes(b"v1")
        v2.write_bytes(b"v2")
        out = tmp_path / "output" / "concat.mp4"

        mock_run.return_value = MagicMock(returncode=0)
        concatenator.concatenate([v1, v2], out)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd
        assert "-f" in cmd
        assert "concat" in cmd

    @patch("atomcam_meteor.modules.concatenator.subprocess.run")
    def test_ffmpeg_failure(self, mock_run, concatenator, tmp_path):
        v1 = tmp_path / "v1.mp4"
        v2 = tmp_path / "v2.mp4"
        v1.write_bytes(b"v1")
        v2.write_bytes(b"v2")
        out = tmp_path / "out.mp4"

        mock_run.return_value = MagicMock(returncode=1, stderr="error msg")
        with pytest.raises(ConcatenationError, match="ffmpeg failed"):
            concatenator.concatenate([v1, v2], out)
