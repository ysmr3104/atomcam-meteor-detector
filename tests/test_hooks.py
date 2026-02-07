"""Tests for the hook system."""

import logging

import pytest

from atomcam_meteor.hooks import (
    DetectionEvent,
    ErrorEvent,
    HookRunner,
    LoggingHook,
    NightCompleteEvent,
)


def _make_detection_event():
    return DetectionEvent(
        date_str="20250101", hour=22, minute=30,
        line_count=3, image_path="/img.png", clip_path="/clip.mp4",
    )


def _make_night_event():
    return NightCompleteEvent(
        date_str="20250101", detection_count=5,
        composite_path="/comp.jpg", video_path="/vid.mp4",
    )


def _make_error_event():
    return ErrorEvent(stage="test", error="something broke", context={})


class TestHookRunner:
    def test_fires_all_hooks(self):
        calls = []

        class TrackingHook:
            def on_detection(self, event):
                calls.append(("detection", event))
            def on_night_complete(self, event):
                calls.append(("night", event))
            def on_error(self, event):
                calls.append(("error", event))

        runner = HookRunner([TrackingHook()])
        runner.fire_detection(_make_detection_event())
        runner.fire_night_complete(_make_night_event())
        runner.fire_error(_make_error_event())
        assert len(calls) == 3

    def test_failure_isolation(self):
        calls = []

        class FailHook:
            def on_detection(self, event):
                raise RuntimeError("boom")
            def on_night_complete(self, event):
                pass
            def on_error(self, event):
                pass

        class GoodHook:
            def on_detection(self, event):
                calls.append("good")
            def on_night_complete(self, event):
                pass
            def on_error(self, event):
                pass

        runner = HookRunner([FailHook(), GoodHook()])
        runner.fire_detection(_make_detection_event())
        assert calls == ["good"]

    def test_empty_runner(self):
        runner = HookRunner()
        runner.fire_detection(_make_detection_event())  # no error


class TestLoggingHook:
    def test_logs_detection(self, caplog):
        hook = LoggingHook()
        with caplog.at_level(logging.INFO, logger="atomcam_meteor.hooks"):
            hook.on_detection(_make_detection_event())
        assert "Detection" in caplog.text

    def test_logs_night_complete(self, caplog):
        hook = LoggingHook()
        with caplog.at_level(logging.INFO, logger="atomcam_meteor.hooks"):
            hook.on_night_complete(_make_night_event())
        assert "Night complete" in caplog.text

    def test_logs_error(self, caplog):
        hook = LoggingHook()
        with caplog.at_level(logging.ERROR, logger="atomcam_meteor.hooks"):
            hook.on_error(_make_error_event())
        assert "Error" in caplog.text


class TestEvents:
    def test_detection_event_frozen(self):
        event = _make_detection_event()
        with pytest.raises(AttributeError):
            event.line_count = 10

    def test_night_event_frozen(self):
        event = _make_night_event()
        with pytest.raises(AttributeError):
            event.detection_count = 10
