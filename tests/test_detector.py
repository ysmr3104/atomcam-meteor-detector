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

    def test_saved_image_is_color_composite(self, tmp_path):
        """Detection image should be a color (BGR) lighten composite."""
        video_path = tmp_path / "meteor.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))

        # Write alternating dark and bright-colored frames to trigger detection
        for i in range(30):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            if i % 2 == 1:
                # Draw a bright diagonal line (colored) to trigger HoughLinesP
                cv2.line(frame, (100, 100), (500, 400), (0, 200, 255), 2)
            writer.write(frame)
        writer.release()

        cfg = DetectionConfig(min_line_length=30)
        det = MeteorDetector(cfg)
        result = det.detect(video_path, tmp_path / "output")

        assert result.detected is True
        assert result.image_path is not None

        saved = cv2.imread(str(result.image_path), cv2.IMREAD_UNCHANGED)
        assert saved is not None
        # Must be 3-channel (BGR color), not single-channel grayscale
        assert saved.ndim == 3
        assert saved.shape[2] == 3
        # The bright line color should be preserved in the composite
        assert saved[:, :, 2].max() > 100  # red channel from (0, 200, 255) BGR

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
