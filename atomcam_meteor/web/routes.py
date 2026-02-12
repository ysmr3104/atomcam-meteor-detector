"""Web dashboard routes (HTML pages + JSON API)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.db import ClipRepository, StateDB
from atomcam_meteor.web.dependencies import get_config, get_db

_JST = timezone(timedelta(hours=9))


def _utc_to_jst(utc_str: str | None) -> str:
    """Convert a UTC datetime string to JST display string."""
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(_JST).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_str


def _clip_actual_datetime(clip: dict) -> str:
    """Compute the actual date+time string for a clip.

    Hours 22-23 belong to the previous calendar day relative to date_str.
    """
    date_str = clip["date_str"]
    hour = clip["hour"]
    target = datetime.strptime(date_str, "%Y%m%d")
    if hour >= 22:
        actual_date = target - timedelta(days=1)
    else:
        actual_date = target
    return f"{actual_date.strftime('%Y-%m-%d')} {hour:02d}:{clip['minute']:02d}"

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory status tracking
_rebuild_status: dict[str, str] = {}
_concatenate_status: dict[str, str] = {}


# ── HTML pages ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def index_page(
    request: Request,
    db: StateDB = Depends(get_db),
) -> HTMLResponse:
    """Night list dashboard."""
    nights = db.nights.get_all_nights()
    config: AppConfig = request.app.state.config
    output_dir = config.paths.resolve_output_dir()
    for night in nights:
        if night.get("composite_image"):
            try:
                rel = Path(night["composite_image"]).relative_to(output_dir)
                night["composite_url"] = f"/media/output/{rel}"
            except ValueError:
                night["composite_url"] = None
        else:
            night["composite_url"] = None
        night["last_updated_jst"] = _utc_to_jst(night.get("last_updated_at"))
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", {"nights": nights})


@router.get("/nights/{date_str}", response_class=HTMLResponse)
def night_page(
    request: Request,
    date_str: str,
    db: StateDB = Depends(get_db),
) -> HTMLResponse:
    """Night detail page with detection grid."""
    config: AppConfig = request.app.state.config
    output_dir = config.paths.resolve_output_dir()
    download_dir = config.paths.resolve_download_dir()

    night_output = db.nights.get_output(date_str)
    clips = db.clips.get_clips_by_date(date_str)

    composite_url = None
    video_url = None

    if night_output:
        if night_output.get("composite_image"):
            try:
                rel = Path(night_output["composite_image"]).relative_to(output_dir)
                composite_url = f"/media/output/{rel}"
            except ValueError:
                pass
        if night_output.get("concat_video"):
            try:
                rel = Path(night_output["concat_video"]).relative_to(output_dir)
                video_url = f"/media/output/{rel}"
            except ValueError:
                pass

    # Add actual datetime and sort chronologically
    for clip in clips:
        clip["actual_datetime"] = _clip_actual_datetime(clip)
    clips.sort(key=lambda c: c["actual_datetime"])

    for clip in clips:
        clip["image_url"] = None
        clip["detections"] = []
        clip["video_urls"] = []
        if clip.get("detection_image"):
            try:
                rel = Path(clip["detection_image"]).relative_to(output_dir)
                clip["image_url"] = f"/media/output/{rel}"
            except ValueError:
                pass

            # Load per-line detections from DB
            db_detections = db.detections.get_detections_by_clip(clip["id"])
            if db_detections:
                for det in db_detections:
                    det["crop_url"] = None
                    if det.get("crop_image"):
                        try:
                            rel = Path(det["crop_image"]).relative_to(output_dir)
                            det["crop_url"] = f"/media/output/{rel}"
                        except ValueError:
                            pass
                clip["detections"] = db_detections
            else:
                # Fallback: discover per-line crop images from filesystem
                detect_path = Path(clip["detection_image"])
                stem = detect_path.stem.replace("_detect", "")
                parent = detect_path.parent
                for lp in sorted(parent.glob(f"{stem}_line*.png")):
                    try:
                        rel = lp.relative_to(output_dir)
                        clip["detections"].append({
                            "id": None,
                            "crop_url": f"/media/output/{rel}",
                            "excluded": 0,
                            "line_index": len(clip["detections"]),
                        })
                    except ValueError:
                        pass

        video_paths = ClipRepository.get_detected_video_paths(clip)
        for vp in video_paths:
            vpath = Path(vp)
            try:
                rel = vpath.relative_to(output_dir)
                clip["video_urls"].append(f"/media/output/{rel}")
            except ValueError:
                try:
                    rel = vpath.relative_to(download_dir)
                    clip["video_urls"].append(f"/media/downloads/{rel}")
                except ValueError:
                    pass

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "night.html",
        {
            "date_str": date_str,
            "night_output": night_output,
            "clips": clips,
            "composite_url": composite_url,
            "video_url": video_url,
        },
    )


# ── JSON API ────────────────────────────────────────────────────────────

@router.get("/api/nights")
def api_nights(db: StateDB = Depends(get_db)) -> list[dict]:
    """Return all nights as JSON."""
    return db.nights.get_all_nights()


@router.get("/api/nights/{date_str}")
def api_night_detail(date_str: str, db: StateDB = Depends(get_db)) -> dict:
    """Return night detail as JSON."""
    output = db.nights.get_output(date_str)
    clips = db.clips.get_clips_by_date(date_str)
    return {"date_str": date_str, "output": output, "clips": clips}


@router.get("/api/nights/{date_str}/clips")
def api_night_clips(date_str: str, db: StateDB = Depends(get_db)) -> list[dict]:
    """Return clips for a night as JSON."""
    return db.clips.get_clips_by_date(date_str)


@router.patch("/api/clips/{clip_id}")
def api_toggle_clip(
    clip_id: int,
    body: dict,
    db: StateDB = Depends(get_db),
) -> dict:
    """Toggle the excluded status of a clip."""
    if "excluded" not in body:
        raise HTTPException(status_code=400, detail="'excluded' field required")
    excluded = bool(body["excluded"])
    clip = db.clips.get_clip_by_id(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    db.clips.toggle_excluded(clip_id, excluded)
    return {"id": clip_id, "excluded": excluded}


@router.patch("/api/detections/{detection_id}")
def api_toggle_detection(
    detection_id: int,
    body: dict,
    db: StateDB = Depends(get_db),
) -> dict:
    """Toggle the excluded status of a single detection (line)."""
    if "excluded" not in body:
        raise HTTPException(status_code=400, detail="'excluded' field required")
    excluded = bool(body["excluded"])
    detection = db.detections.get_detection_by_id(detection_id)
    if detection is None:
        raise HTTPException(status_code=404, detail="Detection not found")
    db.detections.toggle_excluded(detection_id, excluded)
    return {"id": detection_id, "excluded": excluded}


@router.patch("/api/nights/{date_str}/detections/bulk")
def api_bulk_detections(
    date_str: str,
    body: dict,
    db: StateDB = Depends(get_db),
) -> dict:
    """Set excluded flag for all detections in a night."""
    if "excluded" not in body:
        raise HTTPException(status_code=400, detail="'excluded' field required")
    excluded = bool(body["excluded"])
    db.detections.set_all_excluded_by_date(date_str, excluded)
    return {"date_str": date_str, "excluded": excluded}


@router.post("/api/nights/{date_str}/rebuild")
def api_rebuild(
    date_str: str,
    background_tasks: BackgroundTasks,
    config: AppConfig = Depends(get_config),
) -> dict:
    """Trigger rebuild of composite image for a night."""
    _rebuild_status[date_str] = "running"
    background_tasks.add_task(_do_rebuild, date_str, config)
    return {"date_str": date_str, "status": "started"}


@router.get("/api/nights/{date_str}/rebuild/status")
def api_rebuild_status(date_str: str) -> dict:
    """Check rebuild progress."""
    status = _rebuild_status.get(date_str, "idle")
    return {"date_str": date_str, "status": status}


@router.post("/api/nights/{date_str}/concatenate")
def api_concatenate(
    date_str: str,
    background_tasks: BackgroundTasks,
    config: AppConfig = Depends(get_config),
) -> dict:
    """Trigger video concatenation for a night."""
    _concatenate_status[date_str] = "running"
    background_tasks.add_task(_do_concatenate, date_str, config)
    return {"date_str": date_str, "status": "started"}


@router.get("/api/nights/{date_str}/concatenate/status")
def api_concatenate_status(date_str: str) -> dict:
    """Check concatenation progress."""
    status = _concatenate_status.get(date_str, "idle")
    return {"date_str": date_str, "status": status}


def _do_rebuild(date_str: str, config: AppConfig) -> None:
    """Background task: rebuild composite image."""
    try:
        from atomcam_meteor.pipeline import Pipeline
        from atomcam_meteor.services.db import StateDB

        db = StateDB.from_path(config.paths.resolve_db_path())
        try:
            pipeline = Pipeline(config, db=db)
            pipeline.rebuild_composite(date_str)
        finally:
            db.close()
        _rebuild_status[date_str] = "completed"
    except Exception as exc:
        logger.error("Rebuild failed for %s: %s", date_str, exc)
        _rebuild_status[date_str] = f"error: {exc}"


def _do_concatenate(date_str: str, config: AppConfig) -> None:
    """Background task: concatenate detected clips into a single video."""
    try:
        from atomcam_meteor.pipeline import Pipeline
        from atomcam_meteor.services.db import StateDB

        db = StateDB.from_path(config.paths.resolve_db_path())
        try:
            pipeline = Pipeline(config, db=db)
            pipeline.rebuild_concatenation(date_str)
        finally:
            db.close()
        _concatenate_status[date_str] = "completed"
    except Exception as exc:
        logger.error("Concatenation failed for %s: %s", date_str, exc)
        _concatenate_status[date_str] = f"error: {exc}"
