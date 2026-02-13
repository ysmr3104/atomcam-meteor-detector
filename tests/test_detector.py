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

    def test_per_group_detection_determines_result(self, tmp_path):
        """グループ単位でHough線が検出された場合のみ detected=True になる。"""
        video_path = tmp_path / "meteor.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))

        # Group 0 (frames 0-14): 暗いフレーム
        for _ in range(15):
            writer.write(np.zeros((h, w, 3), dtype=np.uint8))

        # Group 1 (frames 15-29): 明るい直線（流星模擬）
        for i in range(15):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            if i % 2 == 1:
                cv2.line(frame, (100, 100), (500, 400), (0, 200, 255), 2)
            writer.write(frame)

        # Group 2 (frames 30-44): 暗いフレーム
        for _ in range(15):
            writer.write(np.zeros((h, w, 3), dtype=np.uint8))
        writer.release()

        cfg = DetectionConfig(min_line_length=30)
        det = MeteorDetector(cfg)
        result = det.detect(video_path, tmp_path / "output")

        assert result.detected is True
        assert 1 in result.detection_groups
        # グループ0,2は暗いフレームのみなので含まれない
        assert 0 not in result.detection_groups
        assert 2 not in result.detection_groups

    def test_no_false_positive_from_noise(self, tmp_path):
        """微弱なノイズが全グループに分散しても誤検出しない。"""
        video_path = tmp_path / "noise.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))

        rng = np.random.RandomState(42)
        for _ in range(60):
            # 各フレームにランダムノイズを加えた暗いフレーム
            frame = rng.randint(0, 15, (h, w, 3), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        cfg = DetectionConfig()
        det = MeteorDetector(cfg)
        result = det.detect(video_path, tmp_path / "output")

        assert result.detected is False

    def test_hough_parameters_from_config(self, tmp_path):
        """設定値の hough_threshold と max_line_gap が使用されることを確認。"""
        video_path = tmp_path / "line.mp4"
        h, w = 480, 640
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, 15, (w, h))

        # 明るい直線を描画（検出可能な強度）
        for i in range(30):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            if i % 2 == 1:
                cv2.line(frame, (100, 100), (500, 400), (0, 200, 255), 2)
            writer.write(frame)
        writer.release()

        # threshold=25 (デフォルト) では検出される
        cfg_low = DetectionConfig(hough_threshold=25, max_line_gap=5)
        det_low = MeteorDetector(cfg_low)
        result_low = det_low.detect(video_path, tmp_path / "out_low")
        assert result_low.detected is True

        # threshold=999 では閾値が高すぎて検出されない
        cfg_high = DetectionConfig(hough_threshold=999, max_line_gap=1)
        det_high = MeteorDetector(cfg_high)
        result_high = det_high.detect(video_path, tmp_path / "out_high")
        assert result_high.detected is False

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
