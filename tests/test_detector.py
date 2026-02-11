"""Tests for the meteor detector module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from atomcam_meteor.config import DetectionConfig
from atomcam_meteor.modules.detector import MeteorDetector, DetectionResult


@pytest.fixture
def detector():
    return MeteorDetector(DetectionConfig())


class TestMeteorDetector:
    def test_dark_frames_no_detection(self, detector, tmp_path):
        """Uniform dark frames should yield no detection."""
        video_path = tmp_path / "dark.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))
        for _ in range(30):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        result = detector.detect(video_path, tmp_path / "output")
        assert result.detected is False
        assert result.line_count == 0
        assert result.detection_groups == []
        assert result.fps > 0

    def test_video_not_found(self, detector, tmp_path):
        result = detector.detect(tmp_path / "nonexistent.mp4", tmp_path / "output")
        assert result.detected is False

    def test_detection_result_fields(self):
        r = DetectionResult(
            detected=True, line_count=3, image_path=Path("/img.png"),
            lines=[(0, 0, 100, 100)],
            detection_groups=[0, 2], fps=15.0,
        )
        assert r.detected is True
        assert r.line_count == 3
        assert len(r.lines) == 1
        assert r.detection_groups == [0, 2]
        assert r.fps == 15.0

    def test_detection_result_defaults(self):
        r = DetectionResult(detected=False, line_count=0, image_path=None, lines=[])
        assert r.detection_groups == []
        assert r.fps == 0.0
