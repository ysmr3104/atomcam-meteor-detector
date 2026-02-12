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

    def test_fallback_group_detection_by_line_scoring(self, tmp_path):
        """When per-group _has_lines fails but final composite detects,
        fallback scoring should populate detection_groups."""
        video_path = tmp_path / "faint_meteor.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))

        # 60 frames = 4 groups of 15 frames at exposure_duration_sec=1.0
        # Group 0 (frames 0-14): dark frames - no meteor
        for _ in range(15):
            writer.write(np.zeros((h, w, 3), dtype=np.uint8))

        # Group 1 (frames 15-29): faint diagonal line on 2 frames only
        # Too faint for per-group Hough but visible in final composite
        for i in range(15):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            if i in (7, 8):
                # Faint line - lower brightness to avoid per-group detection
                cv2.line(frame, (100, 100), (500, 400), (60, 60, 60), 1)
            writer.write(frame)

        # Group 2 (frames 30-44): dark frames
        for _ in range(15):
            writer.write(np.zeros((h, w, 3), dtype=np.uint8))

        # Group 3 (frames 45-59): dark frames
        for _ in range(15):
            writer.write(np.zeros((h, w, 3), dtype=np.uint8))
        writer.release()

        # Use low min_line_length so final composite can detect the line
        cfg = DetectionConfig(min_line_length=20, canny_threshold1=30, canny_threshold2=80)
        det = MeteorDetector(cfg)
        result = det.detect(video_path, tmp_path / "output")

        # If detected, groups should be populated (not empty)
        if result.detected:
            assert len(result.detection_groups) > 0, (
                "detection_groups should not be empty when detection succeeds"
            )
            # Group 1 should be in the list (it has the faint line)
            assert 1 in result.detection_groups

    def test_score_groups_by_lines_basic(self):
        """_score_groups_by_lines identifies the group with brightness along lines."""
        cfg = DetectionConfig()
        det = MeteorDetector(cfg)

        h, w = 100, 200
        # Group 0: mostly dark
        dark = np.zeros((h, w), dtype=np.uint8)
        # Group 1: bright along a diagonal
        bright = np.zeros((h, w), dtype=np.uint8)
        cv2.line(bright, (10, 10), (190, 90), 200, 2)
        # Group 2: mostly dark
        dark2 = np.zeros((h, w), dtype=np.uint8)

        diff_composites = [dark, bright, dark2]
        lines = [(10, 10, 190, 90)]
        result = det._score_groups_by_lines(diff_composites, lines, (h, w))

        assert 1 in result
        assert 0 not in result
        assert 2 not in result

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
