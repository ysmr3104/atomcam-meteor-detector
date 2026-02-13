"""Meteor detection via frame-differencing and Hough line detection."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from atomcam_meteor.config import DetectionConfig
from atomcam_meteor.exceptions import DetectionError

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class DetectionResult:
    detected: bool
    line_count: int
    image_path: Optional[Path]
    lines: list[tuple[int, int, int, int]]
    detection_groups: list[int] = dataclasses.field(default_factory=list)
    fps: float = 0.0
    crop_paths: list[Path] = dataclasses.field(default_factory=list)


class MeteorDetector:
    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._mask: Optional[np.ndarray] = None
        if config.mask_path is not None:
            mask_file = Path(config.mask_path).expanduser()
            if mask_file.exists():
                self._mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
                logger.info("Loaded mask from %s", mask_file)
            else:
                logger.warning("Mask path configured but not found: %s", mask_file)

    def detect(self, clip_path: Path, output_dir: Path) -> DetectionResult:
        """Run meteor detection on a video clip.

        Frames are processed in groups to keep memory usage bounded.
        """
        try:
            cap = cv2.VideoCapture(str(clip_path))
            try:
                return self._detect_impl(cap, clip_path, output_dir)
            finally:
                cap.release()
        except DetectionError:
            raise
        except Exception as exc:
            raise DetectionError(
                f"Detection failed for {clip_path}: {exc}",
                clip_path=str(clip_path),
            ) from exc

    def _has_lines(self, composite: np.ndarray) -> bool:
        """Check whether a diff composite contains any Hough lines."""
        img = composite
        if self._mask is not None:
            mask_resized = self._mask
            h, w = img.shape[:2]
            mh, mw = mask_resized.shape[:2]
            if (mh, mw) != (h, w):
                mask_resized = cv2.resize(mask_resized, (w, h))
            img = cv2.bitwise_and(img, mask_resized)
        blurred = cv2.GaussianBlur(img, (5, 5), 0)
        edges = cv2.Canny(
            blurred, self._config.canny_threshold1, self._config.canny_threshold2
        )
        raw_lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self._config.hough_threshold,
            minLineLength=self._config.min_line_length,
            maxLineGap=self._config.max_line_gap,
        )
        return raw_lines is not None

    def _detect_impl(
        self, cap: cv2.VideoCapture, clip_path: Path, output_dir: Path
    ) -> DetectionResult:
        if not cap.isOpened():
            logger.error("Failed to open video: %s", clip_path)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[]
            )

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 15.0
            logger.warning("Could not determine FPS, defaulting to %.1f", fps)

        frames_per_group = int(fps * self._config.exposure_duration_sec)
        if frames_per_group < 2:
            frames_per_group = 2

        logger.debug(
            "Processing %s: fps=%.1f, frames_per_group=%d",
            clip_path.name,
            fps,
            frames_per_group,
        )

        final_composite: Optional[np.ndarray] = None
        color_composite: Optional[np.ndarray] = None
        diff_composites: list[np.ndarray] = []
        detection_groups: list[int] = []
        group_index = 0

        while True:
            # Read one group of frames (grayscale for detection, color for output)
            group_gray: list[np.ndarray] = []
            group_color_comp: Optional[np.ndarray] = None
            for _ in range(frames_per_group):
                ret, frame = cap.read()
                if not ret:
                    break
                group_gray.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
                if group_color_comp is None:
                    group_color_comp = frame
                else:
                    group_color_comp = cv2.max(group_color_comp, frame)

            if len(group_gray) < 2:
                break

            # Pairwise differences within the group
            diff_composite: Optional[np.ndarray] = None
            for i in range(len(group_gray) - 1):
                diff = cv2.subtract(group_gray[i + 1], group_gray[i])
                if diff_composite is None:
                    diff_composite = diff
                else:
                    diff_composite = cv2.max(diff_composite, diff)

            # Free group memory
            del group_gray

            # Check per-group detection and lighten-composite into final result
            if diff_composite is not None:
                if self._has_lines(diff_composite):
                    detection_groups.append(group_index)
                if final_composite is None:
                    final_composite = diff_composite
                else:
                    final_composite = cv2.max(final_composite, diff_composite)
                diff_composites.append(diff_composite)

            # Accumulate color composite for output image
            if group_color_comp is not None:
                if color_composite is None:
                    color_composite = group_color_comp
                else:
                    color_composite = cv2.max(color_composite, group_color_comp)

            group_index += 1

        if final_composite is None:
            logger.warning("No frames processed from %s", clip_path)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[],
                fps=fps,
            )

        del diff_composites

        # グループ単位判定: いずれかのグループで直線が検出されなければ未検出
        if not detection_groups:
            logger.debug("No groups with lines in %s", clip_path.name)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[],
                detection_groups=[], fps=fps,
            )

        # 検出あり — 最終合成からライン座標を取得（出力用）
        if self._mask is not None:
            mask_resized = self._mask
            h, w = final_composite.shape[:2]
            mh, mw = mask_resized.shape[:2]
            if (mh, mw) != (h, w):
                mask_resized = cv2.resize(mask_resized, (w, h))
            final_composite = cv2.bitwise_and(final_composite, mask_resized)

        blurred = cv2.GaussianBlur(final_composite, (5, 5), 0)
        edges = cv2.Canny(
            blurred, self._config.canny_threshold1, self._config.canny_threshold2
        )
        raw_lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self._config.hough_threshold,
            minLineLength=self._config.min_line_length,
            maxLineGap=self._config.max_line_gap,
        )

        lines: list[tuple[int, int, int, int]] = []
        if raw_lines is not None:
            lines = [(int(l[0][0]), int(l[0][1]), int(l[0][2]), int(l[0][3])) for l in raw_lines]
        logger.info("Detected %d line(s) in %s (groups: %s)", len(lines), clip_path.name, detection_groups)

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"{clip_path.stem}_detect.png"
        cv2.imwrite(str(image_path), color_composite)

        # Save per-line crop images
        crop_paths = self._save_line_crops(color_composite, lines, output_dir, clip_path.stem)

        return DetectionResult(
            detected=True,
            line_count=len(lines),
            image_path=image_path,
            lines=lines,
            detection_groups=detection_groups,
            fps=fps,
            crop_paths=crop_paths,
        )

    @staticmethod
    def _save_line_crops(
        composite: np.ndarray,
        lines: list[tuple[int, int, int, int]],
        output_dir: Path,
        stem: str,
        padding: int = 80,
        min_size: int = 120,
    ) -> list[Path]:
        """Save cropped images around each detected line.

        Returns the list of saved crop image paths.
        """
        h, w = composite.shape[:2]
        paths: list[Path] = []
        for i, (x1, y1, x2, y2) in enumerate(lines):
            cy1 = max(0, min(y1, y2) - padding)
            cy2 = min(h, max(y1, y2) + padding)
            cx1 = max(0, min(x1, x2) - padding)
            cx2 = min(w, max(x1, x2) + padding)
            # Ensure minimum crop size
            if cy2 - cy1 < min_size:
                mid = (cy1 + cy2) // 2
                cy1 = max(0, mid - min_size // 2)
                cy2 = min(h, cy1 + min_size)
            if cx2 - cx1 < min_size:
                mid = (cx1 + cx2) // 2
                cx1 = max(0, mid - min_size // 2)
                cx2 = min(w, cx1 + min_size)
            crop = composite[cy1:cy2, cx1:cx2]
            line_path = output_dir / f"{stem}_line{i}.png"
            cv2.imwrite(str(line_path), crop)
            paths.append(line_path)
        return paths
