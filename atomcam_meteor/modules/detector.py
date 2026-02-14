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

    def _get_mask(self, h: int, w: int) -> Optional[np.ndarray]:
        """マスク画像と下部除外を合成して返す。"""
        mask = None

        # ファイルベースのマスク
        if self._mask is not None:
            mask = self._mask
            mh, mw = mask.shape[:2]
            if (mh, mw) != (h, w):
                mask = cv2.resize(mask, (w, h))

        # 下部除外マスク
        if self._config.exclude_bottom_pct > 0:
            bottom_mask = np.full((h, w), 255, dtype=np.uint8)
            exclude_rows = int(h * self._config.exclude_bottom_pct / 100)
            if exclude_rows > 0:
                bottom_mask[h - exclude_rows:, :] = 0
            if mask is not None:
                mask = cv2.bitwise_and(mask, bottom_mask)
            else:
                mask = bottom_mask

        return mask

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

    def _find_lines(
        self,
        composite: np.ndarray,
        diff_image: np.ndarray | None = None,
    ) -> list[tuple[int, int, int, int]]:
        """差分合成画像から Hough 直線を検出して返す。

        diff_image が指定され min_line_brightness > 0 の場合、
        差分画像上のピクセル平均輝度が閾値未満の淡い直線を除外する。
        """
        img = composite
        h, w = img.shape[:2]
        mask = self._get_mask(h, w)
        if mask is not None:
            img = cv2.bitwise_and(img, mask)
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
        if raw_lines is None:
            return []

        lines = [
            (int(ln[0][0]), int(ln[0][1]), int(ln[0][2]), int(ln[0][3]))
            for ln in raw_lines
        ]

        # 差分画像上の直線輝度フィルタ
        min_brightness = self._config.min_line_brightness
        if diff_image is not None and min_brightness > 0:
            bright_lines: list[tuple[int, int, int, int]] = []
            for x1, y1, x2, y2 in lines:
                line_mask = np.zeros((h, w), dtype=np.uint8)
                cv2.line(line_mask, (x1, y1), (x2, y2), 255, 2)
                mean_val = cv2.mean(diff_image, mask=line_mask)[0]
                if mean_val >= min_brightness:
                    bright_lines.append((x1, y1, x2, y2))
                else:
                    logger.debug(
                        "Filtered dim line (%d,%d)-(%d,%d) brightness=%.1f < %.1f",
                        x1, y1, x2, y2, mean_val, min_brightness,
                    )
            lines = bright_lines

        return lines

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

        color_composite: Optional[np.ndarray] = None
        detection_groups: list[int] = []
        group_color_comps: dict[int, np.ndarray] = {}
        group_lines: dict[int, list[tuple[int, int, int, int]]] = {}
        has_frames = False
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

            has_frames = True

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

            # グループ単位で直線検出し、検出グループのカラー合成を保持
            if diff_composite is not None:
                found = self._find_lines(diff_composite, diff_image=diff_composite)
                if found and group_color_comp is not None:
                    detection_groups.append(group_index)
                    group_color_comps[group_index] = group_color_comp
                    group_lines[group_index] = found

            # Accumulate color composite for output image
            if group_color_comp is not None:
                if color_composite is None:
                    color_composite = group_color_comp
                else:
                    color_composite = cv2.max(color_composite, group_color_comp)

            group_index += 1

        if not has_frames:
            logger.warning("No frames processed from %s", clip_path)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[],
                fps=fps,
            )

        # グループ単位判定: いずれかのグループで直線が検出されなければ未検出
        if not detection_groups:
            logger.debug("No groups with lines in %s", clip_path.name)
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[],
                detection_groups=[], fps=fps,
            )

        # 全グループの検出ラインを集約
        all_lines: list[tuple[int, int, int, int]] = []
        for gi in detection_groups:
            all_lines.extend(group_lines[gi])
        logger.info(
            "Detected %d line(s) in %s (groups: %s)",
            len(all_lines), clip_path.name, detection_groups,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"{clip_path.stem}_detect.png"
        cv2.imwrite(str(image_path), color_composite)

        # 検出グループごとにフルフレーム合成画像を保存
        crop_paths = self._save_group_composites(
            group_color_comps, detection_groups, output_dir, clip_path.stem,
        )

        return DetectionResult(
            detected=True,
            line_count=len(all_lines),
            image_path=image_path,
            lines=all_lines,
            detection_groups=detection_groups,
            fps=fps,
            crop_paths=crop_paths,
        )

    @staticmethod
    def _save_group_composites(
        group_color_comps: dict[int, np.ndarray],
        detection_groups: list[int],
        output_dir: Path,
        stem: str,
    ) -> list[Path]:
        """検出グループごとにフルフレーム合成画像を保存する。

        各グループの1秒間（exposure_duration_sec）を比較明合成した
        フルフレーム画像を保存し、パスのリストを返す。
        """
        paths: list[Path] = []
        for gi in detection_groups:
            group_path = output_dir / f"{stem}_group{gi}.png"
            cv2.imwrite(str(group_path), group_color_comps[gi])
            paths.append(group_path)
        return paths
