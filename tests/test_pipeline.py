"""Tests for the pipeline orchestrator."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atomcam_meteor.config import AppConfig
from atomcam_meteor.exceptions import AtomcamError
from atomcam_meteor.modules.detector import DetectionResult
from atomcam_meteor.pipeline import Pipeline, PipelineResult
from atomcam_meteor.services.db import ClipStatus


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
        # prev day hours (22, 23) + curr day hours (0-5) = 8 slots
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
        db.clips.get_included_detected_clips.return_value = [
            {"detection_image": str(tmp_path / "img1.png"), "detected_video": '["v1.mp4"]'},
        ]
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
        db.clips.get_included_detected_clips.return_value = [
            {"detection_image": str(tmp_path / "img1.png"), "detected_video": '["v1.mp4"]'},
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
