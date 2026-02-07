"""Pipeline orchestrator for meteor detection workflow."""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from atomcam_meteor.config import AppConfig
from atomcam_meteor.exceptions import AtomcamError
from atomcam_meteor.hooks import (
    DetectionEvent,
    ErrorEvent,
    HookRunner,
    NightCompleteEvent,
)
from atomcam_meteor.modules.compositor import Compositor
from atomcam_meteor.modules.concatenator import Concatenator
from atomcam_meteor.modules.detector import MeteorDetector
from atomcam_meteor.modules.downloader import Downloader
from atomcam_meteor.services.db import ClipStatus, StateDB

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PipelineResult:
    date_str: str
    clips_processed: int
    detections_found: int
    composite_path: Optional[str]
    video_path: Optional[str]
    dry_run: bool = False


class Pipeline:
    """Orchestrates the full detection pipeline with dependency injection."""

    def __init__(
        self,
        config: AppConfig,
        *,
        dry_run: bool = False,
        hooks: HookRunner | None = None,
        downloader: Downloader | None = None,
        detector: MeteorDetector | None = None,
        compositor: Compositor | None = None,
        concatenator: Concatenator | None = None,
        db: StateDB | None = None,
    ) -> None:
        self._config = config
        self._dry_run = dry_run
        self._hooks = hooks or HookRunner()
        self._downloader = downloader or Downloader(config.camera)
        self._detector = detector or MeteorDetector(config.detection)
        self._compositor = compositor or Compositor()
        self._concatenator = concatenator or Concatenator()
        self._db = db

    def execute(self, date_str: str | None = None) -> PipelineResult:
        """Run the full pipeline for a given night.

        If date_str is None, determines the current observation date automatically:
        before noon = today's date (last night), after noon = tomorrow's date (tonight).
        Date format is YYYYMMDD.
        """
        if date_str is None:
            date_str = self._determine_date()

        logger.info("Pipeline starting for date: %s (dry_run=%s)", date_str, self._dry_run)

        download_dir = self._config.paths.resolve_download_dir()
        output_dir = self._config.paths.resolve_output_dir() / date_str

        # Build list of (date_for_dir, hour) pairs
        time_slots = self._build_time_slots(date_str)

        clips_processed = 0
        detections_found = 0
        detected_images: list[Path] = []
        detected_videos: list[Path] = []

        for slot_date, hour in time_slots:
            try:
                if self._dry_run:
                    logger.info("[DRY-RUN] Would process %s hour %02d", slot_date, hour)
                    continue

                downloaded = self._downloader.download_hour(slot_date, hour, download_dir)

                for clip_url, local_path in downloaded:
                    clips_processed += 1
                    minute = int(local_path.stem)

                    if self._db:
                        self._db.clips.upsert_clip(
                            clip_url, date_str, hour, minute,
                            local_path=str(local_path),
                            status=ClipStatus.DOWNLOADED,
                        )

                    try:
                        result = self._detector.detect(local_path, output_dir)
                    except AtomcamError as exc:
                        logger.error("Detection error for %s: %s", local_path, exc)
                        if self._db:
                            self._db.clips.update_clip_status(
                                clip_url, ClipStatus.ERROR, error_message=str(exc)
                            )
                        self._hooks.fire_error(ErrorEvent(
                            stage="detection", error=str(exc),
                            context={"clip_url": clip_url},
                        ))
                        continue

                    if result.detected:
                        detections_found += 1
                        if result.image_path:
                            detected_images.append(result.image_path)
                        detected_videos.append(local_path)

                        if self._db:
                            self._db.clips.update_clip_status(
                                clip_url, ClipStatus.DETECTED,
                                detection_image=str(result.image_path) if result.image_path else None,
                                detected_video=str(local_path),
                                line_count=result.line_count,
                            )

                        self._hooks.fire_detection(DetectionEvent(
                            date_str=date_str,
                            hour=hour,
                            minute=minute,
                            line_count=result.line_count,
                            image_path=str(result.image_path) if result.image_path else "",
                            clip_path=str(local_path),
                        ))
                    else:
                        if self._db:
                            self._db.clips.update_clip_status(
                                clip_url, ClipStatus.NO_DETECTION,
                            )

            except AtomcamError as exc:
                logger.error("Error processing hour %s/%02d: %s", slot_date, hour, exc)
                self._hooks.fire_error(ErrorEvent(
                    stage="download", error=str(exc),
                    context={"date": slot_date, "hour": hour},
                ))

        if self._dry_run:
            return PipelineResult(
                date_str=date_str, clips_processed=0, detections_found=0,
                composite_path=None, video_path=None, dry_run=True,
            )

        composite_path: str | None = None
        video_path: str | None = None

        if detected_images:
            try:
                comp_out = output_dir / f"{date_str}_composite.jpg"
                self._compositor.composite(detected_images, comp_out)
                composite_path = str(comp_out)
            except AtomcamError as exc:
                logger.error("Compositing failed: %s", exc)
                self._hooks.fire_error(ErrorEvent(
                    stage="composite", error=str(exc), context={"date": date_str},
                ))

        if detected_videos:
            try:
                vid_out = output_dir / f"{date_str}_meteors.mp4"
                self._concatenator.concatenate(detected_videos, vid_out)
                video_path = str(vid_out)
            except AtomcamError as exc:
                logger.error("Concatenation failed: %s", exc)
                self._hooks.fire_error(ErrorEvent(
                    stage="concatenate", error=str(exc), context={"date": date_str},
                ))

        if self._db:
            self._db.nights.upsert_output(
                date_str,
                composite_image=composite_path,
                concat_video=video_path,
                detection_count=detections_found,
            )

        self._hooks.fire_night_complete(NightCompleteEvent(
            date_str=date_str,
            detection_count=detections_found,
            composite_path=composite_path,
            video_path=video_path,
        ))

        logger.info(
            "Pipeline complete: %d clips, %d detections",
            clips_processed, detections_found,
        )

        return PipelineResult(
            date_str=date_str,
            clips_processed=clips_processed,
            detections_found=detections_found,
            composite_path=composite_path,
            video_path=video_path,
        )

    def rebuild_outputs(self, date_str: str) -> PipelineResult:
        """Rebuild composite and video from non-excluded detected clips.

        Used by the web dashboard when clips are excluded/included.
        """
        if self._db is None:
            raise AtomcamError("Database required for rebuild_outputs")

        output_dir = self._config.paths.resolve_output_dir() / date_str
        clips = self._db.clips.get_included_detected_clips(date_str)

        detected_images = [
            Path(c["detection_image"]) for c in clips if c.get("detection_image")
        ]
        detected_videos = [
            Path(c["detected_video"]) for c in clips if c.get("detected_video")
        ]

        composite_path: str | None = None
        video_path: str | None = None

        if detected_images:
            try:
                comp_out = output_dir / f"{date_str}_composite.jpg"
                self._compositor.composite(detected_images, comp_out)
                composite_path = str(comp_out)
            except AtomcamError as exc:
                logger.error("Rebuild compositing failed: %s", exc)

        if detected_videos:
            try:
                vid_out = output_dir / f"{date_str}_meteors.mp4"
                self._concatenator.concatenate(detected_videos, vid_out)
                video_path = str(vid_out)
            except AtomcamError as exc:
                logger.error("Rebuild concatenation failed: %s", exc)

        self._db.nights.upsert_output(
            date_str,
            composite_image=composite_path,
            concat_video=video_path,
            detection_count=len(clips),
        )

        return PipelineResult(
            date_str=date_str,
            clips_processed=0,
            detections_found=len(clips),
            composite_path=composite_path,
            video_path=video_path,
        )

    def _determine_date(self) -> str:
        """Determine the observation date string (YYYYMMDD).

        Before noon: use today's date (observation was last night).
        After noon: use tomorrow's date (observation will be tonight).
        """
        now = datetime.now()
        if now.hour < 12:
            target = now
        else:
            target = now + timedelta(days=1)
        return target.strftime("%Y%m%d")

    def _build_time_slots(self, date_str: str) -> list[tuple[str, int]]:
        """Build (directory_date, hour) pairs for the observation night.

        An observation night spans the previous day's evening hours and
        the current day's early morning hours.
        """
        target = datetime.strptime(date_str, "%Y%m%d")
        prev_day = (target - timedelta(days=1)).strftime("%Y%m%d")

        slots: list[tuple[str, int]] = []
        for hour in self._config.schedule.prev_date_hours:
            slots.append((prev_day, hour))
        for hour in self._config.schedule.curr_date_hours:
            slots.append((date_str, hour))

        return slots
