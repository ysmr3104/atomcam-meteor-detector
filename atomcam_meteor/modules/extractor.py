"""Extract short clips around detected meteor frames using ffmpeg."""

from __future__ import annotations

import dataclasses
import logging
import subprocess
from pathlib import Path

from atomcam_meteor.config import DetectionConfig
from atomcam_meteor.exceptions import ExtractionError

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class TimeRange:
    start_sec: float
    end_sec: float

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


class ClipExtractor:
    """Extracts short clips around detected meteor groups."""

    def __init__(self, config: DetectionConfig) -> None:
        self._exposure_duration_sec = config.exposure_duration_sec
        self._clip_margin_sec = config.clip_margin_sec

    def compute_time_ranges(
        self,
        detection_groups: list[int],
        fps: float,
        video_duration_sec: float = 60.0,
    ) -> list[TimeRange]:
        """Convert detected group indices to time ranges with margins.

        Each group spans ``[group_index * exposure_duration_sec,
        (group_index + 1) * exposure_duration_sec]``.  A margin is added
        on both sides and overlapping/adjacent ranges are merged.
        """
        if not detection_groups:
            return []

        margin = self._clip_margin_sec
        exp = self._exposure_duration_sec

        # Build raw ranges with margin
        raw: list[tuple[float, float]] = []
        for idx in sorted(detection_groups):
            start = max(0.0, idx * exp - margin)
            end = min(video_duration_sec, (idx + 1) * exp + margin)
            raw.append((start, end))

        # Merge overlapping / adjacent ranges
        merged: list[tuple[float, float]] = [raw[0]]
        for start, end in raw[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        return [TimeRange(s, e) for s, e in merged]

    def extract(
        self,
        source_path: Path,
        time_ranges: list[TimeRange],
        output_dir: Path,
    ) -> list[Path]:
        """Extract short clips from *source_path* for each time range.

        Returns a list of output file paths.
        Raises ``ExtractionError`` on ffmpeg failure.
        """
        if not time_ranges:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = source_path.stem
        outputs: list[Path] = []

        for i, tr in enumerate(time_ranges):
            if len(time_ranges) == 1:
                out_name = f"{stem}_meteor.mp4"
            else:
                out_name = f"{stem}_meteor_{i}.mp4"
            out_path = output_dir / out_name

            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{tr.start_sec:.3f}",
                "-i", str(source_path),
                "-t", f"{tr.duration:.3f}",
                "-c", "copy",
                "-an",
                str(out_path),
            ]
            logger.info("Extracting clip: %s", " ".join(cmd))

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ExtractionError(
                    f"ffmpeg extraction failed (exit {proc.returncode}): {proc.stderr}"
                )
            outputs.append(out_path)

        logger.info(
            "Extracted %d clip(s) from %s", len(outputs), source_path.name
        )
        return outputs
