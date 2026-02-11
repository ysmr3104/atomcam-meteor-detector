"""Tests for the clip extractor module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atomcam_meteor.config import DetectionConfig
from atomcam_meteor.exceptions import ExtractionError
from atomcam_meteor.modules.extractor import ClipExtractor, TimeRange


@pytest.fixture
def extractor():
    return ClipExtractor(DetectionConfig())


class TestTimeRange:
    def test_duration(self):
        tr = TimeRange(start_sec=1.0, end_sec=3.5)
        assert tr.duration == pytest.approx(2.5)


class TestComputeTimeRanges:
    def test_empty_groups(self, extractor):
        assert extractor.compute_time_ranges([], fps=15.0) == []

    def test_single_group(self, extractor):
        # group 0: exposure_duration_sec=1.0, margin=0.5
        # raw range: max(0, 0*1.0 - 0.5) .. min(60, 1*1.0 + 0.5) = [0.0, 1.5]
        ranges = extractor.compute_time_ranges([0], fps=15.0)
        assert len(ranges) == 1
        assert ranges[0].start_sec == pytest.approx(0.0)
        assert ranges[0].end_sec == pytest.approx(1.5)

    def test_adjacent_groups_merged(self, extractor):
        # group 0: [0.0, 1.5], group 1: [0.5, 2.5] → merged to [0.0, 2.5]
        ranges = extractor.compute_time_ranges([0, 1], fps=15.0)
        assert len(ranges) == 1
        assert ranges[0].start_sec == pytest.approx(0.0)
        assert ranges[0].end_sec == pytest.approx(2.5)

    def test_separated_groups(self, extractor):
        # group 0: [0.0, 1.5], group 10: [9.5, 11.5] → two ranges
        ranges = extractor.compute_time_ranges([0, 10], fps=15.0)
        assert len(ranges) == 2

    def test_clamped_to_duration(self, extractor):
        # group 59: [58.5, 60.0] (clamped to 60)
        ranges = extractor.compute_time_ranges([59], fps=15.0, video_duration_sec=60.0)
        assert len(ranges) == 1
        assert ranges[0].end_sec == pytest.approx(60.0)

    def test_custom_config(self):
        config = DetectionConfig(exposure_duration_sec=2.0, clip_margin_sec=1.0)
        ext = ClipExtractor(config)
        # group 5: [5*2 - 1, 6*2 + 1] = [9, 13]
        ranges = ext.compute_time_ranges([5], fps=15.0)
        assert len(ranges) == 1
        assert ranges[0].start_sec == pytest.approx(9.0)
        assert ranges[0].end_sec == pytest.approx(13.0)


class TestExtract:
    @patch("atomcam_meteor.modules.extractor.subprocess.run")
    def test_single_range(self, mock_run, extractor, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"video")

        result = extractor.extract(
            source, [TimeRange(0.0, 1.5)], tmp_path / "out",
        )
        assert len(result) == 1
        assert result[0].name == "clip_meteor.mp4"
        mock_run.assert_called_once()

    @patch("atomcam_meteor.modules.extractor.subprocess.run")
    def test_multiple_ranges(self, mock_run, extractor, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"video")

        result = extractor.extract(
            source,
            [TimeRange(0.0, 1.5), TimeRange(10.0, 12.0)],
            tmp_path / "out",
        )
        assert len(result) == 2
        assert result[0].name == "clip_meteor_0.mp4"
        assert result[1].name == "clip_meteor_1.mp4"

    @patch("atomcam_meteor.modules.extractor.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run, extractor, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"video")

        with pytest.raises(ExtractionError):
            extractor.extract(source, [TimeRange(0.0, 1.0)], tmp_path / "out")

    def test_empty_ranges(self, extractor, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"video")
        result = extractor.extract(source, [], tmp_path / "out")
        assert result == []
