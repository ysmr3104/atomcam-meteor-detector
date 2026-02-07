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
