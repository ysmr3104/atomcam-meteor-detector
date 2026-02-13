"""Pipeline orchestrator for meteor detection workflow."""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from collections.abc import Callable
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
from atomcam_meteor.modules.extractor import ClipExtractor
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
        extractor: ClipExtractor | None = None,
        db: StateDB | None = None,
    ) -> None:
        self._config = config
        self._dry_run = dry_run
        self._hooks = hooks or HookRunner()
        self._downloader = downloader or Downloader(config.camera)
        self._detector = detector or MeteorDetector(config.detection)
        self._compositor = compositor or Compositor()
        self._concatenator = concatenator or Concatenator()
        self._extractor = extractor or ClipExtractor(config.detection)
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
        time_slots = self._filter_available_slots(time_slots)

        clips_processed = 0
        detections_found = 0
        detected_images: list[Path] = []
        new_detected_images: list[Path] = []

        for slot_date, hour in time_slots:
            try:
                if self._dry_run:
                    logger.info("[DRY-RUN] Would process %s hour %02d", slot_date, hour)
                    continue

                downloaded = self._downloader.download_hour(slot_date, hour, download_dir)

                for clip_url, local_path in downloaded:
                    minute = int(local_path.stem)
                    if not self._clip_in_range(hour, minute):
                        logger.debug("Clip %02d:%02d outside range, skipping", hour, minute)
                        continue
                    clips_processed += 1

                    if self._db:
                        self._db.clips.upsert_clip(
                            clip_url, date_str, hour, minute,
                            local_path=str(local_path),
                            status=ClipStatus.DOWNLOADED,
                        )
                        existing = self._db.clips.get_clip(clip_url)
                        if existing and existing["status"] in (
                            ClipStatus.DETECTED,
                            ClipStatus.NO_DETECTION,
                            ClipStatus.ERROR,
                        ):
                            logger.debug("Skipping already-processed clip: %s", clip_url)
                            if (
                                existing["status"] == ClipStatus.DETECTED
                                and existing.get("detection_image")
                            ):
                                detected_images.append(Path(existing["detection_image"]))
                                detections_found += 1
                            continue

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
                            new_detected_images.append(result.image_path)

                        # Extract short clips around detected groups
                        detected_video_json = self._extract_short_clips(
                            local_path, result, output_dir,
                        )

                        if self._db:
                            self._db.clips.update_clip_status(
                                clip_url, ClipStatus.DETECTED,
                                detection_image=str(result.image_path) if result.image_path else None,
                                detected_video=detected_video_json,
                                line_count=result.line_count,
                            )
                            self._save_detections(clip_url, result)

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
        comp_out = output_dir / f"{date_str}_composite.jpg"

        if new_detected_images:
            try:
                existing_comp = comp_out if comp_out.exists() else None
                self._compositor.composite(
                    new_detected_images, comp_out, existing_composite=existing_comp,
                )
                composite_path = str(comp_out)
            except AtomcamError as exc:
                logger.error("Compositing failed: %s", exc)
                self._hooks.fire_error(ErrorEvent(
                    stage="composite", error=str(exc), context={"date": date_str},
                ))
        elif comp_out.exists():
            composite_path = str(comp_out)

        if self._db:
            all_detected = self._db.clips.get_detected_clips(date_str)
            cumulative_count = len(all_detected)
            existing_output = self._db.nights.get_output(date_str)
            existing_video = (
                existing_output.get("concat_video") if existing_output else None
            )

            self._db.nights.upsert_output(
                date_str,
                composite_image=composite_path,
                concat_video=existing_video,
                detection_count=cumulative_count,
            )

        self._hooks.fire_night_complete(NightCompleteEvent(
            date_str=date_str,
            detection_count=detections_found,
            composite_path=composite_path,
            video_path=None,
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
            video_path=None,
        )

    def redetect_from_local(
        self,
        date_str: str | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> PipelineResult:
        """Re-run detection on already-downloaded local files (no camera access).

        Scans the download directory for existing MP4 files matching the
        observation night's time slots and runs detection on each.
        """
        if date_str is None:
            date_str = self._determine_date()

        logger.info("Redetect starting for date: %s (local files only)", date_str)

        download_dir = self._config.paths.resolve_download_dir()
        output_dir = self._config.paths.resolve_output_dir() / date_str

        time_slots = self._build_time_slots(date_str)

        # 事前にクリップ総数をカウント
        all_mp4_files: list[tuple[str, int, Path]] = []
        for slot_date, hour in time_slots:
            hour_dir = download_dir / slot_date / f"{hour:02d}"
            if not hour_dir.is_dir():
                continue
            for mp4_file in sorted(hour_dir.glob("*.mp4")):
                minute = int(mp4_file.stem)
                if not self._clip_in_range(hour, minute):
                    continue
                all_mp4_files.append((slot_date, hour, mp4_file))

        total = len(all_mp4_files)
        clips_processed = 0
        detections_found = 0
        detected_images: list[Path] = []
        for slot_date, hour, mp4_file in all_mp4_files:
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Redetect cancelled at %d/%d clips", clips_processed, total)
                break

            minute = int(mp4_file.stem)
            clips_processed += 1
            clip_url = (
                f"http://{self._config.camera.host}"
                f"/{self._config.camera.base_path}"
                f"/{slot_date}/{hour:02d}/{mp4_file.name}"
            )

            if self._db:
                self._db.clips.upsert_clip(
                    clip_url, date_str, hour, minute,
                    local_path=str(mp4_file),
                    status=ClipStatus.DOWNLOADED,
                )

            try:
                result = self._detector.detect(mp4_file, output_dir)
            except AtomcamError as exc:
                logger.error("Detection error for %s: %s", mp4_file, exc)
                if self._db:
                    self._db.clips.update_clip_status(
                        clip_url, ClipStatus.ERROR, error_message=str(exc),
                    )
                self._hooks.fire_error(ErrorEvent(
                    stage="detection", error=str(exc),
                    context={"clip_url": clip_url},
                ))
                if progress_callback is not None:
                    progress_callback(clips_processed, total)
                continue

            if result.detected:
                detections_found += 1
                if result.image_path:
                    detected_images.append(result.image_path)

                detected_video_json = self._extract_short_clips(
                    mp4_file, result, output_dir,
                )

                if self._db:
                    self._db.clips.update_clip_status(
                        clip_url, ClipStatus.DETECTED,
                        detection_image=str(result.image_path) if result.image_path else None,
                        detected_video=detected_video_json,
                        line_count=result.line_count,
                    )
                    self._save_detections(clip_url, result)

                self._hooks.fire_detection(DetectionEvent(
                    date_str=date_str,
                    hour=hour,
                    minute=minute,
                    line_count=result.line_count,
                    image_path=str(result.image_path) if result.image_path else "",
                    clip_path=str(mp4_file),
                ))
            else:
                if self._db:
                    self._db.clips.update_clip_status(
                        clip_url, ClipStatus.NO_DETECTION,
                    )

            if progress_callback is not None:
                progress_callback(clips_processed, total)

        composite_path: str | None = None
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

        if self._db:
            all_detected = self._db.clips.get_detected_clips(date_str)
            cumulative_count = len(all_detected)
            self._db.nights.upsert_output(
                date_str,
                composite_image=composite_path,
                concat_video=None,
                detection_count=cumulative_count,
            )

        self._hooks.fire_night_complete(NightCompleteEvent(
            date_str=date_str,
            detection_count=detections_found,
            composite_path=composite_path,
            video_path=None,
        ))

        logger.info(
            "Redetect complete: %d clips, %d detections",
            clips_processed, detections_found,
        )

        return PipelineResult(
            date_str=date_str,
            clips_processed=clips_processed,
            detections_found=detections_found,
            composite_path=composite_path,
            video_path=None,
        )

    def rebuild_outputs(self, date_str: str) -> PipelineResult:
        """Rebuild both composite and concatenated video (backward-compat wrapper)."""
        result = self.rebuild_composite(date_str)
        result2 = self.rebuild_concatenation(date_str)
        return PipelineResult(
            date_str=date_str,
            clips_processed=0,
            detections_found=result.detections_found,
            composite_path=result.composite_path,
            video_path=result2.video_path,
        )

    def rebuild_composite(self, date_str: str) -> PipelineResult:
        """Rebuild composite image with per-line exclusion support.

        For each detected clip, excluded lines are masked out (blacked out)
        from the detection image before compositing.  Clips where all lines
        are excluded are skipped entirely.
        """
        if self._db is None:
            raise AtomcamError("Database required for rebuild_composite")

        import cv2

        output_dir = self._config.paths.resolve_output_dir() / date_str
        clips = self._db.clips.get_detected_clips(date_str)

        masked_images: list[Path] = []
        included_clip_count = 0

        for clip in clips:
            if not clip.get("detection_image"):
                continue

            detection_image_path = Path(clip["detection_image"])
            detections = self._db.detections.get_detections_by_clip(clip["id"])

            if not detections:
                # No per-line data: fall back to clip-level exclusion
                if not clip.get("excluded"):
                    masked_images.append(detection_image_path)
                    included_clip_count += 1
                continue

            included = [d for d in detections if not d["excluded"]]
            if not included:
                # All lines excluded — skip this clip
                continue

            excluded = [d for d in detections if d["excluded"]]
            included_clip_count += 1

            if not excluded:
                # No lines excluded — use original image
                masked_images.append(detection_image_path)
            else:
                # Mask out excluded line regions
                image = cv2.imread(str(detection_image_path))
                if image is None:
                    logger.warning("Failed to load image: %s", detection_image_path)
                    continue
                excluded_lines = [
                    (d["x1"], d["y1"], d["x2"], d["y2"]) for d in excluded
                ]
                masked = self._compositor.mask_lines(image, excluded_lines)
                masked_path = detection_image_path.with_suffix(".masked.png")
                cv2.imwrite(str(masked_path), masked)
                masked_images.append(masked_path)

        composite_path: str | None = None

        if masked_images:
            try:
                comp_out = output_dir / f"{date_str}_composite.jpg"
                self._compositor.composite(masked_images, comp_out)
                composite_path = str(comp_out)
            except AtomcamError as exc:
                logger.error("Rebuild compositing failed: %s", exc)

        # Preserve existing concat_video
        existing = self._db.nights.get_output(date_str)
        existing_video = existing.get("concat_video") if existing else None

        self._db.nights.upsert_output(
            date_str,
            composite_image=composite_path,
            concat_video=existing_video,
            detection_count=included_clip_count,
        )

        return PipelineResult(
            date_str=date_str,
            clips_processed=0,
            detections_found=included_clip_count,
            composite_path=composite_path,
            video_path=existing_video,
        )

    def rebuild_concatenation(self, date_str: str) -> PipelineResult:
        """Rebuild concatenated video from non-excluded detected clips."""
        if self._db is None:
            raise AtomcamError("Database required for rebuild_concatenation")

        output_dir = self._config.paths.resolve_output_dir() / date_str
        clips = self._db.clips.get_included_detected_clips(date_str)

        # Collect all short clip paths from detected_video JSON
        all_clip_paths: list[Path] = []
        for c in clips:
            paths = self._db.clips.get_detected_video_paths(c)
            all_clip_paths.extend(Path(p) for p in paths)

        video_path: str | None = None

        if all_clip_paths:
            try:
                vid_out = output_dir / f"{date_str}_meteors.mp4"
                self._concatenator.concatenate(all_clip_paths, vid_out)
                video_path = str(vid_out)
            except AtomcamError as exc:
                logger.error("Rebuild concatenation failed: %s", exc)

        # Preserve existing composite_image
        existing = self._db.nights.get_output(date_str)
        existing_composite = existing.get("composite_image") if existing else None
        existing_count = existing.get("detection_count", len(clips)) if existing else len(clips)

        self._db.nights.upsert_output(
            date_str,
            composite_image=existing_composite,
            concat_video=video_path,
            detection_count=existing_count,
        )

        return PipelineResult(
            date_str=date_str,
            clips_processed=0,
            detections_found=len(clips),
            composite_path=existing_composite,
            video_path=video_path,
        )

    def _save_detections(self, clip_url: str, result: object) -> None:
        """検出グループ単位で detection レコードを DB に保存する。"""
        from atomcam_meteor.modules.detector import DetectionResult

        assert isinstance(result, DetectionResult)
        if self._db is None or not result.detection_groups:
            return

        clip = self._db.clips.get_clip(clip_url)
        if clip is None:
            return

        clip_id = clip["id"]
        # 1グループ = 1レコード（フルフレーム合成画像付き）
        lines = [(0, 0, 0, 0)] * len(result.detection_groups)
        crop_paths = [str(p) for p in result.crop_paths]
        while len(crop_paths) < len(lines):
            crop_paths.append("")

        self._db.detections.bulk_insert(clip_id, lines, crop_paths)

    def _extract_short_clips(
        self, local_path: Path, result: object, output_dir: Path
    ) -> str:
        """Extract short clips and return a JSON array of paths.

        Falls back to the original video path on failure.
        """
        from atomcam_meteor.modules.detector import DetectionResult

        assert isinstance(result, DetectionResult)
        try:
            time_ranges = self._extractor.compute_time_ranges(
                result.detection_groups, result.fps,
            )
            if time_ranges:
                extracted = self._extractor.extract(local_path, time_ranges, output_dir)
                return json.dumps([str(p) for p in extracted])
        except AtomcamError as exc:
            logger.warning("Clip extraction failed, using original: %s", exc)

        # Fallback: store the original video path
        return json.dumps([str(local_path)])

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

    def _filter_available_slots(
        self, slots: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        """Remove time slots that are still in the future.

        When the pipeline runs during the observation night (e.g. at 23:00),
        hours that haven't started yet (0-5) would produce unnecessary HTTP
        requests to the camera.  This filter keeps only past-or-current slots.
        """
        now = datetime.now()
        available: list[tuple[str, int]] = []
        for slot_date, hour in slots:
            slot_dt = datetime.strptime(slot_date, "%Y%m%d").replace(hour=hour)
            if slot_dt <= now:
                available.append((slot_date, hour))
        return available

    def _clip_in_range(self, hour: int, minute: int) -> bool:
        """指定の (hour, minute) が観測時間範囲内かを判定する。"""
        start_h, start_m = (int(x) for x in self._config.schedule.start_time.split(":"))
        end_h, end_m = (int(x) for x in self._config.schedule.end_time.split(":"))

        clip = hour * 60 + minute
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m

        if start >= end:  # 日付またぎ
            return clip >= start or clip < end
        else:
            return start <= clip < end

    def _build_time_slots(self, date_str: str) -> list[tuple[str, int]]:
        """Build (directory_date, hour) pairs for the observation night.

        An observation night spans from start_time to end_time.
        When crossing midnight, the evening hours use the previous day's
        directory and the morning hours use the current day's directory.
        """
        target = datetime.strptime(date_str, "%Y%m%d")
        prev_day = (target - timedelta(days=1)).strftime("%Y%m%d")

        start_h, start_m = (int(x) for x in self._config.schedule.start_time.split(":"))
        end_h, end_m = (int(x) for x in self._config.schedule.end_time.split(":"))
        start_total = start_h * 60 + start_m
        end_total = end_h * 60 + end_m

        slots: list[tuple[str, int]] = []
        if start_total >= end_total:  # 日付をまたぐ (例: 22:00→06:00)
            for h in range(start_h, 24):
                slots.append((prev_day, h))
            end_h_inclusive = end_h + 1 if end_m > 0 else end_h
            for h in range(0, end_h_inclusive):
                slots.append((date_str, h))
        else:  # 同日内 (例: 01:00→05:00)
            end_h_inclusive = end_h + 1 if end_m > 0 else end_h
            for h in range(start_h, end_h_inclusive):
                slots.append((date_str, h))

        return slots
