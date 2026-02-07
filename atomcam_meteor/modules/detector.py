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

        while True:
            # Read one group of frames (grayscale)
            group: list[np.ndarray] = []
            for _ in range(frames_per_group):
                ret, frame = cap.read()
                if not ret:
                    break
                group.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

            if len(group) < 2:
                break

            # Pairwise differences within the group
            diff_composite: Optional[np.ndarray] = None
            for i in range(len(group) - 1):
                diff = cv2.subtract(group[i + 1], group[i])
                if diff_composite is None:
                    diff_composite = diff
                else:
                    diff_composite = cv2.max(diff_composite, diff)

            # Free group memory
            del group

            # Lighten-composite into final result
            if diff_composite is not None:
                if final_composite is None:
                    final_composite = diff_composite
                else:
                    final_composite = cv2.max(final_composite, diff_composite)

        if final_composite is None:
            logger.warning("No frames processed from %s", clip_path)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[]
            )

        # Apply mask if configured
        if self._mask is not None:
            mask_resized = self._mask
            h, w = final_composite.shape[:2]
            mh, mw = mask_resized.shape[:2]
            if (mh, mw) != (h, w):
                mask_resized = cv2.resize(mask_resized, (w, h))
            final_composite = cv2.bitwise_and(final_composite, mask_resized)

        # Line detection
        blurred = cv2.GaussianBlur(final_composite, (5, 5), 0)
        edges = cv2.Canny(
            blurred, self._config.canny_threshold1, self._config.canny_threshold2
        )
        raw_lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=50,
            minLineLength=self._config.min_line_length,
            maxLineGap=10,
        )

        if raw_lines is None:
            logger.debug("No lines detected in %s", clip_path.name)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[]
            )

        lines = [(int(l[0][0]), int(l[0][1]), int(l[0][2]), int(l[0][3])) for l in raw_lines]
        logger.info("Detected %d line(s) in %s", len(lines), clip_path.name)

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"{clip_path.stem}_detect.png"
        cv2.imwrite(str(image_path), final_composite)

        return DetectionResult(
            detected=True,
            line_count=len(lines),
            image_path=image_path,
            lines=lines,
        )
