"""Custom exception hierarchy for atomcam-meteor-detector."""

from __future__ import annotations


class AtomcamError(Exception):
    """Base exception for all atomcam-meteor-detector errors."""


class ConfigError(AtomcamError):
    """Configuration loading or validation error."""


class CameraError(AtomcamError):
    """Camera communication error."""


class DownloadError(CameraError):
    """Download failure.

    Attributes:
        url: The URL that failed to download.
    """

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message)
        self.url = url


class DetectionError(AtomcamError):
    """OpenCV processing error.

    Attributes:
        clip_path: Path to the clip that caused the error.
    """

    def __init__(self, message: str, *, clip_path: str = "") -> None:
        super().__init__(message)
        self.clip_path = clip_path


class CompositorError(AtomcamError):
    """Image compositing error."""


class ConcatenationError(AtomcamError):
    """ffmpeg / video concatenation error."""


class LockError(AtomcamError):
    """Exclusive lock acquisition failure."""


class HookError(AtomcamError):
    """Hook delivery failure (non-fatal)."""
