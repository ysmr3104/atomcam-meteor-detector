"""Download video clips from ATOM Cam via HTTP."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import httpx

from atomcam_meteor.config import CameraConfig
from atomcam_meteor.exceptions import DownloadError

logger = logging.getLogger(__name__)


class Downloader:
    """Downloads 1-minute MP4 clips from an ATOM Cam's SD card over HTTP."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._auth: httpx.BasicAuth | None = (
            httpx.BasicAuth(config.http_user, config.http_password)
            if config.http_user and config.http_password
            else None
        )

    def list_clips(self, date_str: str, hour: int) -> list[str]:
        """Fetch directory listing and return full URLs to .mp4 clips."""
        base_url = f"http://{self.config.host}/{self.config.base_path}"
        hour_url = f"{base_url}/{date_str}/{hour:02d}/"

        last_exc: Exception | None = None
        for attempt in range(1, self.config.retry_count + 1):
            try:
                resp = httpx.get(
                    hour_url,
                    auth=self._auth,
                    timeout=self.config.timeout_sec,
                )
                resp.raise_for_status()
                filenames = re.findall(r'href="(\d{2}\.mp4)"', resp.text)
                return [f"{hour_url}{fn}" for fn in sorted(filenames)]
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "list_clips attempt %d/%d failed for %s: %s",
                    attempt, self.config.retry_count, hour_url, exc,
                )
                if attempt < self.config.retry_count:
                    time.sleep(2)

        logger.warning("Failed to list clips at %s after %d attempts: %s",
                        hour_url, self.config.retry_count, last_exc)
        return []

    def download_clip(self, url: str, dest_dir: Path) -> Path:
        """Download a single clip with streaming and retry logic.

        The URL path is expected to end with ``.../{YYYYMMDD}/{HH}/{MM}.mp4``.
        The file is saved to ``dest_dir/{YYYYMMDD}/{HH}/{MM}.mp4``.

        Raises ``DownloadError`` if all retries are exhausted.
        """
        # Extract date_str/HH/MM.mp4 from URL path
        parts = url.rstrip("/").split("/")
        date_part, hour_part, filename = parts[-3], parts[-2], parts[-1]
        local_path = dest_dir / date_part / hour_part / filename

        if local_path.exists() and local_path.stat().st_size > 0:
            logger.info("Clip already exists, skipping: %s", local_path)
            return local_path

        local_path.parent.mkdir(parents=True, exist_ok=True)

        last_exc: Exception | None = None
        retry_count = self.config.retry_count

        for attempt in range(1, retry_count + 1):
            try:
                with httpx.stream(
                    "GET",
                    url,
                    auth=self._auth,
                    timeout=self.config.timeout_sec,
                ) as resp:
                    resp.raise_for_status()
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                logger.info("Downloaded %s -> %s", url, local_path)
                return local_path
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt,
                    retry_count,
                    url,
                    exc,
                )
                if attempt < retry_count:
                    time.sleep(1)

        raise DownloadError(
            f"Failed to download {url} after {retry_count} attempts: {last_exc}",
            url=url,
        )

    def download_hour(
        self, date_str: str, hour: int, dest_dir: Path
    ) -> list[tuple[str, Path]]:
        """List and download all clips for a given hour.

        Returns a list of (clip_url, local_path) tuples for successfully
        downloaded clips.
        """
        urls = self.list_clips(date_str, hour)
        results: list[tuple[str, Path]] = []

        for url in urls:
            try:
                local_path = self.download_clip(url, dest_dir)
                results.append((url, local_path))
            except DownloadError as exc:
                logger.error("Failed to download %s after retries: %s", url, exc)

        return results
