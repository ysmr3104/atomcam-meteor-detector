"""FastAPI dependency injection helpers."""

from __future__ import annotations

from typing import Generator

from fastapi import Depends, Request

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.db import StateDB


def get_config(request: Request) -> AppConfig:
    """Retrieve AppConfig from application state."""
    return request.app.state.config


def get_db(config: AppConfig = Depends(get_config)) -> Generator[StateDB, None, None]:
    """Provide a StateDB instance, closing it after the request."""
    db = StateDB.from_path(config.paths.resolve_db_path())
    try:
        yield db
    finally:
        db.close()
