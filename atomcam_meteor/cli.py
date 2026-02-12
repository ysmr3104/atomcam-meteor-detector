"""Command-line interface for atomcam-meteor-detector."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from atomcam_meteor._logging import setup_logging
from atomcam_meteor.config import AppConfig, load_config
from atomcam_meteor.exceptions import AtomcamError, ConfigError


@click.group()
@click.version_option(package_name="atomcam-meteor-detector")
def cli() -> None:
    """Automatic meteor detection from ATOM Cam videos."""


@cli.command()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to config YAML file.")
@click.option("--date", "date_str", default=None, help="Target date (YYYYMMDD).")
@click.option("--dry-run", is_flag=True, help="Simulate without downloading or processing.")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v INFO, -vv DEBUG).")
def run(config_path: str | None, date_str: str | None, dry_run: bool, verbose: int) -> None:
    """Run the meteor detection pipeline."""
    setup_logging(verbose)
    config = _load(config_path)

    from atomcam_meteor.hooks import HookRunner, LoggingHook
    from atomcam_meteor.pipeline import Pipeline
    from atomcam_meteor.services.db import StateDB
    from atomcam_meteor.services.lock import FileLock

    hooks = HookRunner([LoggingHook()])

    with FileLock(config.paths.resolve_lock_path()):
        db = StateDB.from_path(config.paths.resolve_db_path())
        try:
            pipeline = Pipeline(config, dry_run=dry_run, hooks=hooks, db=db)
            result = pipeline.execute(date_str)
        finally:
            db.close()

    click.echo(f"Date:       {result.date_str}")
    click.echo(f"Clips:      {result.clips_processed}")
    click.echo(f"Detections: {result.detections_found}")
    if result.composite_path:
        click.echo(f"Composite:  {result.composite_path}")
    if result.video_path:
        click.echo(f"Video:      {result.video_path}")
    if result.dry_run:
        click.echo("(dry-run mode — no files were modified)")


@cli.command()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to config YAML file.")
@click.option("--date", "date_str", default=None, help="Target date (YYYYMMDD).")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v INFO, -vv DEBUG).")
def redetect(config_path: str | None, date_str: str | None, verbose: int) -> None:
    """Re-run detection on local files without camera access."""
    setup_logging(verbose)
    config = _load(config_path)

    from atomcam_meteor.hooks import HookRunner, LoggingHook
    from atomcam_meteor.pipeline import Pipeline
    from atomcam_meteor.services.db import StateDB
    from atomcam_meteor.services.lock import FileLock

    hooks = HookRunner([LoggingHook()])

    with FileLock(config.paths.resolve_lock_path()):
        db = StateDB.from_path(config.paths.resolve_db_path())
        try:
            pipeline = Pipeline(config, hooks=hooks, db=db)
            result = pipeline.redetect_from_local(date_str)
        finally:
            db.close()

    click.echo(f"Date:       {result.date_str}")
    click.echo(f"Clips:      {result.clips_processed}")
    click.echo(f"Detections: {result.detections_found}")
    if result.composite_path:
        click.echo(f"Composite:  {result.composite_path}")


@cli.command()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to config YAML file.")
@click.option("--date", "date_str", default=None, help="Target date (YYYYMMDD).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(config_path: str | None, date_str: str | None, as_json: bool) -> None:
    """Show detection status from the database."""
    config = _load(config_path)

    from atomcam_meteor.services.db import StateDB

    db = StateDB.from_path(config.paths.resolve_db_path())
    try:
        if date_str:
            clips = db.clips.get_clips_by_date(date_str)
            output = db.nights.get_output(date_str)
            data = {"date": date_str, "clips": clips, "output": output}
        else:
            nights = db.nights.get_all_nights()
            data = {"nights": nights}

        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            if "nights" in data:
                for n in data["nights"]:
                    click.echo(
                        f"{n['date_str']}: {n['detection_count']} detection(s)"
                    )
            else:
                click.echo(f"Night: {date_str}")
                night_out = data.get("output")
                if night_out:
                    click.echo(f"  Detections: {night_out['detection_count']}")
                    click.echo(f"  Composite:  {night_out.get('composite_image', 'N/A')}")
                    click.echo(f"  Video:      {night_out.get('concat_video', 'N/A')}")
                click.echo(f"  Clips: {len(data['clips'])}")
                for c in data["clips"]:
                    click.echo(
                        f"    {c['hour']:02d}:{c['minute']:02d} "
                        f"[{c['status']}] lines={c['line_count']}"
                    )
    finally:
        db.close()


@cli.command()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to config YAML file.")
@click.option("--validate", is_flag=True, help="Validate only, do not print.")
def config(config_path: str | None, validate: bool) -> None:
    """Show or validate the resolved configuration."""
    try:
        cfg = _load(config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        raise SystemExit(1)

    if validate:
        click.echo("Configuration is valid.")
    else:
        click.echo(cfg.model_dump_json(indent=2))


@cli.command()
@click.option("-c", "--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to config YAML file.")
@click.option("--host", default=None, help="Bind host (overrides config).")
@click.option("--port", default=None, type=int, help="Bind port (overrides config).")
def serve(config_path: str | None, host: str | None, port: int | None) -> None:
    """Start the web dashboard server."""
    config = _load(config_path)
    import uvicorn
    from atomcam_meteor.web.app import create_app

    app = create_app(config)
    uvicorn.run(
        app,
        host=host or config.web.host,
        port=port or config.web.port,
    )


def _load(config_path: str | None) -> AppConfig:
    """Load config from explicit path or default locations."""
    if config_path:
        return load_config(config_path)
    for candidate in ["config/settings.yaml", "config/settings.example.yaml"]:
        p = Path(candidate)
        if p.exists():
            return load_config(p)
    return AppConfig()
