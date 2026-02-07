"""Tests for the compositor module."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from atomcam_meteor.exceptions import CompositorError
from atomcam_meteor.modules.compositor import Compositor


@pytest.fixture
def compositor():
    return Compositor()


class TestCompositor:
    def test_lighten_composite(self, compositor, tmp_path):
        img1 = np.zeros((100, 100, 3), dtype=np.uint8)
        img1[:, :50] = 200
        img2 = np.zeros((100, 100, 3), dtype=np.uint8)
        img2[:, 50:] = 150

        p1 = tmp_path / "img1.png"
        p2 = tmp_path / "img2.png"
        cv2.imwrite(str(p1), img1)
        cv2.imwrite(str(p2), img2)

        out = tmp_path / "composite.png"
        compositor.composite([p1, p2], out)
        result = cv2.imread(str(out))

        assert (result[:, 25, 0] >= 190).all()  # left side from img1
        assert (result[:, 75, 0] >= 140).all()  # right side from img2

    def test_incremental_composite(self, compositor, tmp_path):
        existing = np.full((100, 100, 3), 100, dtype=np.uint8)
        existing_path = tmp_path / "existing.png"
        cv2.imwrite(str(existing_path), existing)

        new_img = np.full((100, 100, 3), 200, dtype=np.uint8)
        new_path = tmp_path / "new.png"
        cv2.imwrite(str(new_path), new_img)

        out = tmp_path / "result.png"
        compositor.composite([new_path], out, existing_composite=existing_path)
        result = cv2.imread(str(out))
        assert np.all(result >= 190)

    def test_empty_input_raises(self, compositor, tmp_path):
        out = tmp_path / "out.png"
        with pytest.raises(CompositorError, match="No valid images"):
            compositor.composite([], out)

    def test_output_dir_created(self, compositor, tmp_path):
        img = np.full((50, 50, 3), 128, dtype=np.uint8)
        p = tmp_path / "img.png"
        cv2.imwrite(str(p), img)

        out = tmp_path / "subdir" / "composite.png"
        compositor.composite([p], out)
        assert out.exists()

    def test_invalid_image_skipped(self, compositor, tmp_path):
        valid = np.zeros((100, 100, 3), dtype=np.uint8)
        valid_path = tmp_path / "valid.png"
        cv2.imwrite(str(valid_path), valid)

        invalid_path = tmp_path / "invalid.png"
        invalid_path.write_bytes(b"not an image")

        out = tmp_path / "out.png"
        compositor.composite([invalid_path, valid_path], out)
        assert out.exists()
