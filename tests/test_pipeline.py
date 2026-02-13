"""Tests for the pipeline orchestrator."""

import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atomcam_meteor.config import AppConfig
from atomcam_meteor.exceptions import AtomcamError
from atomcam_meteor.modules.detector import DetectionResult
from atomcam_meteor.pipeline import Pipeline, PipelineResult
from atomcam_meteor.services.db import ClipStatus, StateDB


@pytest.fixture
def mock_deps(tmp_path):
    config = AppConfig.model_validate({
        "paths": {
            "download_dir": str(tmp_path / "dl"),
            "output_dir": str(tmp_path / "out"),
            "db_path": str(tmp_path / "test.db"),
            "lock_path": str(tmp_path / "test.lock"),
        }
    })
    downloader = MagicMock()
    detector = MagicMock()
    compositor = MagicMock()
    concatenator = MagicMock()
    extractor = MagicMock()
    db = MagicMock()
    return config, downloader, detector, compositor, concatenator, extractor, db


class TestPipeline:
    def test_dry_run(self, mock_deps):
        config, dl, det, comp, concat, ext, db = mock_deps
        pipeline = Pipeline(config, dry_run=True, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.execute("20250101")
        assert result.dry_run is True
        assert result.clips_processed == 0
        dl.download_hour.assert_not_called()

    def test_time_slot_building(self, mock_deps):
        config, *_ = mock_deps
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        slots = pipeline._build_time_slots("20250101")
        # 22:00-06:00 → hours 22,23 (prev day) + 0,1,2,3,4,5 (curr day) = 8 slots
        assert len(slots) == 8
        assert slots[0] == ("20241231", 22)
        assert slots[-1] == ("20250101", 5)

    def test_detection_flow(self, mock_deps, tmp_path):
        config, dl, det, comp, concat, ext, db = mock_deps
        clip_path = tmp_path / "dl" / "20250101" / "22" / "00.mp4"
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(b"video")

        # Return clip only for first slot, empty for the rest (8 slots total)
        dl.download_hour.side_effect = [
            [("http://cam/20250101/22/00.mp4", clip_path)],
        ] + [[] for _ in range(7)]
        det.detect.return_value = DetectionResult(
            detected=True, line_count=2,
            image_path=tmp_path / "out" / "detect.png", lines=[(0, 0, 10, 10)],
            detection_groups=[0], fps=15.0,
        )
        comp.composite.return_value = tmp_path / "comp.jpg"
        ext.compute_time_ranges.return_value = []

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.execute("20250101")
        assert result.detections_found == 1
        assert result.clips_processed == 1
        # Concatenation should NOT be called in pipeline execute
        concat.concatenate.assert_not_called()
        # video_path should be None (no concatenation in pipeline)
        assert result.video_path is None

    def test_detection_error_continues(self, mock_deps, tmp_path):
        config, dl, det, comp, concat, ext, db = mock_deps
        clip1 = tmp_path / "dl" / "20250101" / "22" / "00.mp4"
        clip2 = tmp_path / "dl" / "20250101" / "22" / "01.mp4"
        clip1.parent.mkdir(parents=True)
        clip1.write_bytes(b"v1")
        clip2.write_bytes(b"v2")

        dl.download_hour.side_effect = [
            [("http://c/00.mp4", clip1), ("http://c/01.mp4", clip2)],
        ] + [[] for _ in range(7)]

        det.detect.side_effect = [
            AtomcamError("fail"),
            DetectionResult(detected=False, line_count=0, image_path=None, lines=[]),
        ]

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.execute("20250101")
        assert result.clips_processed == 2

    @patch("atomcam_meteor.pipeline.datetime")
    def test_date_determination_morning(self, mock_dt, mock_deps):
        config, *_ = mock_deps
        mock_dt.now.return_value = datetime(2025, 1, 15, 8, 0)
        mock_dt.strptime = datetime.strptime
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        assert pipeline._determine_date() == "20250115"

    @patch("atomcam_meteor.pipeline.datetime")
    def test_date_determination_afternoon(self, mock_dt, mock_deps):
        config, *_ = mock_deps
        mock_dt.now.return_value = datetime(2025, 1, 15, 14, 0)
        mock_dt.strptime = datetime.strptime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        assert pipeline._determine_date() == "20250116"

    def test_rebuild_composite(self, mock_deps, tmp_path):
        config, dl, det, comp, concat, ext, db = mock_deps
        db.clips.get_detected_clips.return_value = [
            {"id": 1, "detection_image": str(tmp_path / "img1.png"),
             "detected_video": '["v1.mp4"]', "excluded": 0},
        ]
        # No per-line detections -> falls back to clip-level exclusion
        db.detections.get_detections_by_clip.return_value = []
        db.nights.get_output.return_value = {"concat_video": "/old/video.mp4"}
        comp.composite.return_value = tmp_path / "comp.jpg"

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.rebuild_composite("20250101")
        assert result.detections_found == 1
        comp.composite.assert_called_once()
        # concat not called
        concat.concatenate.assert_not_called()
        # preserves existing video
        assert result.video_path == "/old/video.mp4"

    def test_rebuild_concatenation(self, mock_deps, tmp_path):
        config, dl, det, comp, concat, ext, db = mock_deps
        db.clips.get_included_detected_clips.return_value = [
            {"detected_video": f'["{tmp_path / "v1.mp4"}"]'},
        ]
        db.clips.get_detected_video_paths.return_value = [str(tmp_path / "v1.mp4")]
        db.nights.get_output.return_value = {
            "composite_image": "/old/comp.jpg",
            "detection_count": 1,
        }
        concat.concatenate.return_value = tmp_path / "vid.mp4"

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.rebuild_concatenation("20250101")
        assert result.detections_found == 1
        concat.concatenate.assert_called_once()
        comp.composite.assert_not_called()
        assert result.composite_path == "/old/comp.jpg"

    def test_rebuild_outputs_calls_both(self, mock_deps, tmp_path):
        config, dl, det, comp, concat, ext, db = mock_deps
        db.clips.get_detected_clips.return_value = [
            {"id": 1, "detection_image": str(tmp_path / "img1.png"),
             "detected_video": '["v1.mp4"]', "excluded": 0},
        ]
        db.detections.get_detections_by_clip.return_value = []
        db.clips.get_included_detected_clips.return_value = [
            {"detected_video": '["v1.mp4"]'},
        ]
        db.clips.get_detected_video_paths.return_value = ["v1.mp4"]
        db.nights.get_output.return_value = {
            "composite_image": None,
            "concat_video": None,
            "detection_count": 1,
        }
        comp.composite.return_value = tmp_path / "comp.jpg"
        concat.concatenate.return_value = tmp_path / "vid.mp4"

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=db)
        result = pipeline.rebuild_outputs("20250101")
        assert result.detections_found == 1
        comp.composite.assert_called_once()
        concat.concatenate.assert_called_once()

    def test_rebuild_without_db_raises(self, mock_deps):
        config, dl, det, comp, concat, ext, _ = mock_deps
        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext, db=None)
        with pytest.raises(AtomcamError, match="Database required"):
            pipeline.rebuild_outputs("20250101")

    def test_skip_already_processed_clip(self, mock_deps, tmp_path, memory_db):
        """Already-detected clips should be skipped; detector must not be called."""
        config, dl, det, comp, concat, ext, _ = mock_deps
        clip_path = tmp_path / "dl" / "20250101" / "22" / "00.mp4"
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(b"video")
        img_path = tmp_path / "out" / "detect.png"
        img_path.parent.mkdir(parents=True)
        img_path.write_bytes(b"img")

        # Pre-populate DB with a detected clip
        memory_db.clips.upsert_clip(
            "http://cam/20250101/22/00.mp4", "20250101", 22, 0,
            local_path=str(clip_path), status=ClipStatus.DOWNLOADED,
        )
        memory_db.clips.update_clip_status(
            "http://cam/20250101/22/00.mp4", ClipStatus.DETECTED,
            detection_image=str(img_path), line_count=2,
        )

        dl.download_hour.side_effect = [
            [("http://cam/20250101/22/00.mp4", clip_path)],
        ] + [[] for _ in range(7)]

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.execute("20250101")

        # Detector should NOT be called for the already-processed clip
        det.detect.assert_not_called()
        # But the detection should still be counted
        assert result.detections_found == 1

    def test_skip_no_detection_clip(self, mock_deps, tmp_path, memory_db):
        """Clips with NO_DETECTION status should be skipped."""
        config, dl, det, comp, concat, ext, _ = mock_deps
        clip_path = tmp_path / "dl" / "20250101" / "22" / "00.mp4"
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(b"video")

        memory_db.clips.upsert_clip(
            "http://cam/20250101/22/00.mp4", "20250101", 22, 0,
            local_path=str(clip_path), status=ClipStatus.DOWNLOADED,
        )
        memory_db.clips.update_clip_status(
            "http://cam/20250101/22/00.mp4", ClipStatus.NO_DETECTION,
        )

        dl.download_hour.side_effect = [
            [("http://cam/20250101/22/00.mp4", clip_path)],
        ] + [[] for _ in range(7)]

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.execute("20250101")

        det.detect.assert_not_called()
        assert result.detections_found == 0

    @patch("atomcam_meteor.pipeline.datetime")
    def test_filter_future_slots(self, mock_dt, mock_deps):
        """Future time slots should be filtered out."""
        config, *_ = mock_deps
        # Simulate running at 23:30 on Dec 31
        mock_dt.now.return_value = datetime(2024, 12, 31, 23, 30)
        mock_dt.strptime = datetime.strptime
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        all_slots = pipeline._build_time_slots("20250101")
        filtered = pipeline._filter_available_slots(all_slots)
        # Only 22:00 and 23:00 of Dec 31 should remain (0-5 of Jan 1 are future)
        assert len(filtered) == 2
        assert filtered[0] == ("20241231", 22)
        assert filtered[1] == ("20241231", 23)

    @patch("atomcam_meteor.pipeline.datetime")
    def test_filter_all_slots_past(self, mock_dt, mock_deps):
        """When running after all slots (e.g. morning), all slots should pass."""
        config, *_ = mock_deps
        mock_dt.now.return_value = datetime(2025, 1, 1, 8, 0)
        mock_dt.strptime = datetime.strptime
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        all_slots = pipeline._build_time_slots("20250101")
        filtered = pipeline._filter_available_slots(all_slots)
        assert len(filtered) == 8

    def test_incremental_composite(self, mock_deps, tmp_path, memory_db):
        """Incremental compositing should pass existing_composite to compositor."""
        config, dl, det, comp, concat, ext, _ = mock_deps
        output_dir = tmp_path / "out" / "20250101"
        output_dir.mkdir(parents=True)

        # Simulate existing composite from prior run
        existing_comp = output_dir / "20250101_composite.jpg"
        existing_comp.write_bytes(b"existing")

        # Pre-populate DB with a prior detection (already processed)
        old_img = tmp_path / "out" / "old_detect.png"
        old_img.parent.mkdir(parents=True, exist_ok=True)
        old_img.write_bytes(b"old")
        memory_db.clips.upsert_clip(
            "http://cam/20241231/22/00.mp4", "20250101", 22, 0,
            local_path="/dl/00.mp4", status=ClipStatus.DOWNLOADED,
        )
        memory_db.clips.update_clip_status(
            "http://cam/20241231/22/00.mp4", ClipStatus.DETECTED,
            detection_image=str(old_img), line_count=1,
        )

        # New clip to process
        clip_path = tmp_path / "dl" / "20241231" / "23" / "05.mp4"
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(b"video")
        new_img = tmp_path / "out" / "new_detect.png"

        dl.download_hour.side_effect = [
            # hour 22: already-processed clip
            [("http://cam/20241231/22/00.mp4", tmp_path / "dl" / "22" / "00.mp4")],
            # hour 23: new clip
            [("http://cam/20241231/23/05.mp4", clip_path)],
        ] + [[] for _ in range(6)]

        det.detect.return_value = DetectionResult(
            detected=True, line_count=3,
            image_path=new_img, lines=[(0, 0, 10, 10)],
            detection_groups=[0], fps=15.0,
        )
        ext.compute_time_ranges.return_value = []

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.execute("20250101")

        # Compositor should be called with only the NEW image
        comp.composite.assert_called_once()
        call_args = comp.composite.call_args
        assert call_args[0][0] == [new_img]  # only new images
        assert call_args[1]["existing_composite"] == existing_comp

    def test_no_new_detections_preserves_composite(self, mock_deps, tmp_path, memory_db):
        """When no new detections, existing composite should be preserved."""
        config, dl, det, comp, concat, ext, _ = mock_deps
        output_dir = tmp_path / "out" / "20250101"
        output_dir.mkdir(parents=True)

        existing_comp = output_dir / "20250101_composite.jpg"
        existing_comp.write_bytes(b"existing")

        # All clips already processed, no new detections
        dl.download_hour.side_effect = [[] for _ in range(8)]

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.execute("20250101")

        comp.composite.assert_not_called()
        assert result.composite_path == str(existing_comp)

    def test_cumulative_detection_count_in_db(self, mock_deps, tmp_path, memory_db):
        """Night output should store cumulative detection count from DB."""
        config, dl, det, comp, concat, ext, _ = mock_deps

        # Pre-populate DB with a prior detection
        memory_db.clips.upsert_clip(
            "http://cam/20241231/22/00.mp4", "20250101", 22, 0,
            local_path="/dl/00.mp4", status=ClipStatus.DOWNLOADED,
        )
        memory_db.clips.update_clip_status(
            "http://cam/20241231/22/00.mp4", ClipStatus.DETECTED,
            detection_image="/img1.png", line_count=1,
        )

        # New clip that also has a detection
        clip_path = tmp_path / "dl" / "20241231" / "23" / "05.mp4"
        clip_path.parent.mkdir(parents=True)
        clip_path.write_bytes(b"video")
        new_img = tmp_path / "out" / "new_detect.png"

        dl.download_hour.side_effect = [
            [("http://cam/20241231/22/00.mp4", tmp_path / "dl" / "00.mp4")],
            [("http://cam/20241231/23/05.mp4", clip_path)],
        ] + [[] for _ in range(6)]

        det.detect.return_value = DetectionResult(
            detected=True, line_count=2,
            image_path=new_img, lines=[(0, 0, 10, 10)],
            detection_groups=[0], fps=15.0,
        )
        ext.compute_time_ranges.return_value = []

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        pipeline.execute("20250101")

        # DB should have cumulative count = 2 (1 old + 1 new)
        night_output = memory_db.nights.get_output("20250101")
        assert night_output is not None
        assert night_output["detection_count"] == 2


class TestRedetectFromLocal:
    def test_redetect_processes_local_files(self, mock_deps, tmp_path, memory_db):
        """redetect_from_local should detect from local files without downloader."""
        config, dl, det, comp, concat, ext, _ = mock_deps

        # Create local files matching the time slots for 20250101
        # prev_date_hours: 22,23 on 20241231; curr_date_hours: 0-5 on 20250101
        dl_dir = tmp_path / "dl"
        hour_dir = dl_dir / "20241231" / "22"
        hour_dir.mkdir(parents=True)
        (hour_dir / "00.mp4").write_bytes(b"video0")
        (hour_dir / "01.mp4").write_bytes(b"video1")

        det.detect.side_effect = [
            DetectionResult(
                detected=True, line_count=1,
                image_path=tmp_path / "out" / "img.png",
                lines=[(0, 0, 10, 10)], detection_groups=[0], fps=15.0,
            ),
            DetectionResult(
                detected=False, line_count=0,
                image_path=None, lines=[],
            ),
        ]
        ext.compute_time_ranges.return_value = []

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.redetect_from_local("20250101")

        assert result.clips_processed == 2
        assert result.detections_found == 1
        # Downloader should NOT be called
        dl.download_hour.assert_not_called()
        # Detector should be called for each local file
        assert det.detect.call_count == 2

    def test_redetect_no_local_files(self, mock_deps, tmp_path, memory_db):
        """redetect_from_local with no local files should return zero results."""
        config, dl, det, comp, concat, ext, _ = mock_deps

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.redetect_from_local("20250101")

        assert result.clips_processed == 0
        assert result.detections_found == 0
        dl.download_hour.assert_not_called()
        det.detect.assert_not_called()

    def test_redetect_cancel_event(self, mock_deps, tmp_path, memory_db):
        """cancel_event をセットすると処理が中断されること"""
        config, dl, det, comp, concat, ext, _ = mock_deps

        dl_dir = tmp_path / "dl"
        hour_dir = dl_dir / "20241231" / "22"
        hour_dir.mkdir(parents=True)
        (hour_dir / "00.mp4").write_bytes(b"video0")
        (hour_dir / "01.mp4").write_bytes(b"video1")
        (hour_dir / "02.mp4").write_bytes(b"video2")

        cancel_event = threading.Event()

        def detect_side_effect(path, output_dir):
            # 最初のクリップ処理後にキャンセル
            cancel_event.set()
            return DetectionResult(
                detected=False, line_count=0, image_path=None, lines=[],
            )

        det.detect.side_effect = detect_side_effect

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.redetect_from_local("20250101", cancel_event=cancel_event)

        # 最初の1つだけ処理され、残りはキャンセルされる
        assert result.clips_processed == 1
        assert det.detect.call_count == 1

    def test_redetect_progress_callback(self, mock_deps, tmp_path, memory_db):
        """progress_callback が正しく呼ばれること"""
        config, dl, det, comp, concat, ext, _ = mock_deps

        dl_dir = tmp_path / "dl"
        hour_dir = dl_dir / "20241231" / "22"
        hour_dir.mkdir(parents=True)
        (hour_dir / "00.mp4").write_bytes(b"video0")
        (hour_dir / "01.mp4").write_bytes(b"video1")

        det.detect.return_value = DetectionResult(
            detected=False, line_count=0, image_path=None, lines=[],
        )

        progress_calls: list[tuple[int, int]] = []

        def on_progress(processed: int, total: int) -> None:
            progress_calls.append((processed, total))

        pipeline = Pipeline(config, downloader=dl, detector=det,
                          compositor=comp, concatenator=concat, extractor=ext,
                          db=memory_db)
        result = pipeline.redetect_from_local(
            "20250101", progress_callback=on_progress,
        )

        assert result.clips_processed == 2
        assert len(progress_calls) == 2
        assert progress_calls[0] == (1, 2)
        assert progress_calls[1] == (2, 2)


class TestTimeSlots:
    """start_time / end_time によるスロット生成テスト"""

    def test_time_slots_same_day(self, tmp_path):
        """同日内レンジ（01:00→05:00）のスロット生成"""
        config = AppConfig.model_validate({
            "schedule": {"start_time": "01:00", "end_time": "05:00"},
            "paths": {
                "download_dir": str(tmp_path / "dl"),
                "output_dir": str(tmp_path / "out"),
                "db_path": str(tmp_path / "test.db"),
                "lock_path": str(tmp_path / "test.lock"),
            },
        })
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        slots = pipeline._build_time_slots("20250101")
        # 01:00-05:00 → hours 1,2,3,4 = 4 slots (all same day)
        assert len(slots) == 4
        assert slots[0] == ("20250101", 1)
        assert slots[-1] == ("20250101", 4)

    def test_time_slots_partial_hour(self, tmp_path):
        """分指定ありのスロット生成（22:30→05:15）"""
        config = AppConfig.model_validate({
            "schedule": {"start_time": "22:30", "end_time": "05:15"},
            "paths": {
                "download_dir": str(tmp_path / "dl"),
                "output_dir": str(tmp_path / "out"),
                "db_path": str(tmp_path / "test.db"),
                "lock_path": str(tmp_path / "test.lock"),
            },
        })
        pipeline = Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=MagicMock())
        slots = pipeline._build_time_slots("20250101")
        # 22:30→05:15 → hours 22,23 (prev day) + 0,1,2,3,4,5 (curr day) = 8 slots
        # end_m=15 > 0 なので hour 5 も含む
        assert len(slots) == 8
        assert slots[0] == ("20241231", 22)
        assert slots[-1] == ("20250101", 5)


class TestClipInRange:
    """_clip_in_range の分レベルフィルタリングテスト"""

    def _make_pipeline(self, tmp_path, start_time, end_time):
        config = AppConfig.model_validate({
            "schedule": {"start_time": start_time, "end_time": end_time},
            "paths": {
                "download_dir": str(tmp_path / "dl"),
                "output_dir": str(tmp_path / "out"),
                "db_path": str(tmp_path / "test.db"),
                "lock_path": str(tmp_path / "test.lock"),
            },
        })
        return Pipeline(config, downloader=MagicMock(), detector=MagicMock(),
                       compositor=MagicMock(), concatenator=MagicMock(),
                       extractor=MagicMock())

    def test_midnight_crossing_inside(self, tmp_path):
        """日付またぎ: 範囲内のクリップ"""
        pipeline = self._make_pipeline(tmp_path, "22:00", "06:00")
        assert pipeline._clip_in_range(22, 0) is True
        assert pipeline._clip_in_range(23, 30) is True
        assert pipeline._clip_in_range(0, 0) is True
        assert pipeline._clip_in_range(3, 15) is True
        assert pipeline._clip_in_range(5, 59) is True

    def test_midnight_crossing_outside(self, tmp_path):
        """日付またぎ: 範囲外のクリップ"""
        pipeline = self._make_pipeline(tmp_path, "22:00", "06:00")
        assert pipeline._clip_in_range(6, 0) is False
        assert pipeline._clip_in_range(12, 0) is False
        assert pipeline._clip_in_range(21, 59) is False

    def test_midnight_crossing_partial_start(self, tmp_path):
        """日付またぎ + 分指定: 開始境界のフィルタリング"""
        pipeline = self._make_pipeline(tmp_path, "22:30", "05:15")
        assert pipeline._clip_in_range(22, 29) is False
        assert pipeline._clip_in_range(22, 30) is True
        assert pipeline._clip_in_range(22, 59) is True

    def test_midnight_crossing_partial_end(self, tmp_path):
        """日付またぎ + 分指定: 終了境界のフィルタリング"""
        pipeline = self._make_pipeline(tmp_path, "22:30", "05:15")
        assert pipeline._clip_in_range(5, 14) is True
        assert pipeline._clip_in_range(5, 15) is False
        assert pipeline._clip_in_range(5, 59) is False

    def test_same_day_inside(self, tmp_path):
        """同日内: 範囲内のクリップ"""
        pipeline = self._make_pipeline(tmp_path, "01:00", "05:00")
        assert pipeline._clip_in_range(1, 0) is True
        assert pipeline._clip_in_range(3, 30) is True
        assert pipeline._clip_in_range(4, 59) is True

    def test_same_day_outside(self, tmp_path):
        """同日内: 範囲外のクリップ"""
        pipeline = self._make_pipeline(tmp_path, "01:00", "05:00")
        assert pipeline._clip_in_range(0, 59) is False
        assert pipeline._clip_in_range(5, 0) is False
        assert pipeline._clip_in_range(22, 0) is False

    def test_clip_outside_range_skipped_in_redetect(self, tmp_path, memory_db):
        """範囲外クリップが redetect_from_local でスキップされることの統合テスト"""
        config = AppConfig.model_validate({
            "schedule": {"start_time": "22:30", "end_time": "05:15"},
            "paths": {
                "download_dir": str(tmp_path / "dl"),
                "output_dir": str(tmp_path / "out"),
                "db_path": str(tmp_path / "test.db"),
                "lock_path": str(tmp_path / "test.lock"),
            },
        })
        det = MagicMock()
        det.detect.return_value = DetectionResult(
            detected=False, line_count=0, image_path=None, lines=[],
        )
        ext = MagicMock()

        # 22時台に 00.mp4（範囲外: 22:00 < 22:30）と 30.mp4（範囲内: 22:30）を配置
        hour_dir = tmp_path / "dl" / "20241231" / "22"
        hour_dir.mkdir(parents=True)
        (hour_dir / "00.mp4").write_bytes(b"video0")
        (hour_dir / "30.mp4").write_bytes(b"video30")

        pipeline = Pipeline(config, downloader=MagicMock(), detector=det,
                          compositor=MagicMock(), concatenator=MagicMock(),
                          extractor=ext, db=memory_db)
        result = pipeline.redetect_from_local("20250101")

        # 00.mp4 は範囲外なのでスキップ、30.mp4 のみ処理される
        assert result.clips_processed == 1
        assert det.detect.call_count == 1
