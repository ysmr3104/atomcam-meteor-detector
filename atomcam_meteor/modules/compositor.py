"""Create lighten composite images (比較明合成)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from atomcam_meteor.exceptions import CompositorError

logger = logging.getLogger(__name__)


class Compositor:
    """Combines multiple images using lighten blending (pixel-wise maximum)."""

    @staticmethod
    def mask_lines(
        image: np.ndarray,
        lines: list[tuple[int, int, int, int]],
        padding: int = 80,
        min_size: int = 120,
    ) -> np.ndarray:
        """Black out rectangular regions around specified line coordinates.

        Uses the same region computation as MeteorDetector._save_line_crops.
        """
        result = image.copy()
        h, w = result.shape[:2]
        for x1, y1, x2, y2 in lines:
            cy1 = max(0, min(y1, y2) - padding)
            cy2 = min(h, max(y1, y2) + padding)
            cx1 = max(0, min(x1, x2) - padding)
            cx2 = min(w, max(x1, x2) + padding)
            if cy2 - cy1 < min_size:
                mid = (cy1 + cy2) // 2
                cy1 = max(0, mid - min_size // 2)
                cy2 = min(h, cy1 + min_size)
            if cx2 - cx1 < min_size:
                mid = (cx1 + cx2) // 2
                cx1 = max(0, mid - min_size // 2)
                cx2 = min(w, cx1 + min_size)
            result[cy1:cy2, cx1:cx2] = 0
        return result

    def composite(
        self,
        image_paths: list[Path],
        output_path: Path,
        existing_composite: Optional[Path] = None,
    ) -> Path:
        """Create a lighten composite from the given images.

        If *existing_composite* is provided and exists on disk, it is used as
        the starting image so that composites can be built incrementally.

        Returns *output_path* after writing the result.

        Raises ``CompositorError`` when no valid images could be loaded.
        """
        result: np.ndarray | None = None

        if existing_composite is not None and existing_composite.exists():
            result = cv2.imread(str(existing_composite))
            if result is None:
                logger.warning(
                    "Failed to load existing composite: %s", existing_composite
                )

        for path in image_paths:
            image = cv2.imread(str(path))
            if image is None:
                logger.warning("Failed to load image, skipping: %s", path)
                continue

            if result is None:
                result = image
            elif image.shape != result.shape:
                logger.warning(
                    "Size mismatch (%s vs %s), skipping: %s",
                    result.shape,
                    image.shape,
                    path,
                )
            else:
                result = np.maximum(result, image)

        if result is None:
            raise CompositorError("No valid images to composite")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), result)
        logger.info("Composite saved: %s", output_path)
        return output_path
