"""FastAPI application factory for the web dashboard."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.scheduler import PipelineScheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """スケジューラの起動・停止を管理する。"""
    scheduler: PipelineScheduler = app.state.scheduler
    await scheduler.start()
    yield
    await scheduler.stop()


def create_app(config: AppConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="atomcam-meteor-detector",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.config = config
    app.state.scheduler = PipelineScheduler(config)

    templates_dir = Path(__file__).parent / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    # Mount static assets (logo, CSS, etc.)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount media directories for serving images and videos
    download_dir = config.paths.resolve_download_dir()
    output_dir = config.paths.resolve_output_dir()
    download_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/media/downloads", StaticFiles(directory=str(download_dir)), name="downloads")
    app.mount("/media/output", StaticFiles(directory=str(output_dir)), name="output")

    from atomcam_meteor.web.routes import router
    app.include_router(router)

    return app
