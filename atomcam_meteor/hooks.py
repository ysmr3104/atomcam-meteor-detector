"""Hook system for event-driven notifications."""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional, Protocol, runtime_checkable

from atomcam_meteor.exceptions import HookError

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DetectionEvent:
    date_str: str
    hour: int
    minute: int
    line_count: int
    image_path: str
    clip_path: str


@dataclasses.dataclass(frozen=True)
class NightCompleteEvent:
    date_str: str
    detection_count: int
    composite_path: Optional[str]
    video_path: Optional[str]


@dataclasses.dataclass(frozen=True)
class ErrorEvent:
    stage: str
    error: str
    context: dict


@runtime_checkable
class Hook(Protocol):
    def on_detection(self, event: DetectionEvent) -> None: ...
    def on_night_complete(self, event: NightCompleteEvent) -> None: ...
    def on_error(self, event: ErrorEvent) -> None: ...


class LoggingHook:
    """Default hook that logs all events."""

    def on_detection(self, event: DetectionEvent) -> None:
        logger.info(
            "Detection: %s %02d:%02d - %d line(s)",
            event.date_str, event.hour, event.minute, event.line_count,
        )

    def on_night_complete(self, event: NightCompleteEvent) -> None:
        logger.info(
            "Night complete: %s - %d detection(s)", event.date_str, event.detection_count,
        )

    def on_error(self, event: ErrorEvent) -> None:
        logger.error("Error in %s: %s", event.stage, event.error)


class HookRunner:
    """Runs all registered hooks, isolating failures."""

    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self._hooks: list[Hook] = list(hooks) if hooks else []

    def add(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def fire_detection(self, event: DetectionEvent) -> None:
        for hook in self._hooks:
            try:
                hook.on_detection(event)
            except Exception as exc:
                logger.warning("Hook %s.on_detection failed: %s", type(hook).__name__, exc)

    def fire_night_complete(self, event: NightCompleteEvent) -> None:
        for hook in self._hooks:
            try:
                hook.on_night_complete(event)
            except Exception as exc:
                logger.warning("Hook %s.on_night_complete failed: %s", type(hook).__name__, exc)

    def fire_error(self, event: ErrorEvent) -> None:
        for hook in self._hooks:
            try:
                hook.on_error(event)
            except Exception as exc:
                logger.warning("Hook %s.on_error failed: %s", type(hook).__name__, exc)
