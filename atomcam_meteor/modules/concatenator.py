"""Concatenate video clips using ffmpeg."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from atomcam_meteor.exceptions import ConcatenationError

logger = logging.getLogger(__name__)


class Concatenator:
    """Joins multiple MP4 clips into a single video via ffmpeg concat."""

    def concatenate(self, video_paths: list[Path], output_path: Path) -> Path:
        """Concatenate *video_paths* into *output_path*.

        For a single video the file is simply copied.  For multiple videos
        ffmpeg's concat demuxer is used with ``-c:v libx264``.

        Returns *output_path* on success.

        Raises ``ConcatenationError`` if the list is empty or ffmpeg fails.
        """
        if len(video_paths) < 1:
            raise ConcatenationError("No videos to concatenate")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if len(video_paths) == 1:
            shutil.copy2(video_paths[0], output_path)
            logger.info("Single video copied: %s", output_path)
            return output_path

        concat_list = output_path.parent / f"{output_path.stem}_concat.txt"
        try:
            concat_list.write_text(
                "\n".join(
                    f"file '{path.resolve()}'" for path in video_paths
                )
                + "\n"
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                str(output_path),
            ]
            logger.info("Running: %s", " ".join(cmd))

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ConcatenationError(
                    f"ffmpeg failed (exit {proc.returncode}): {proc.stderr}"
                )

            logger.info("Concatenated %d videos: %s", len(video_paths), output_path)
            return output_path
        finally:
            if concat_list.exists():
                concat_list.unlink()
