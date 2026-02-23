"""ダウンロードファイル自動削除サービス。"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from atomcam_meteor.config import AppConfig
from atomcam_meteor.services.db import StateDB
from atomcam_meteor.services.schedule_resolver import resolve_cleanup_settings

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CleanupResult:
    """クリーンアップ実行結果。"""

    nights_cleaned: int = 0
    bytes_freed: int = 0


class CleanupService:
    """ダウンロード MP4 ファイルの自動クリーンアップサービス。"""

    def __init__(self, config: AppConfig, db: StateDB) -> None:
        self._config = config
        self._db = db
        self._download_dir = config.paths.resolve_download_dir()

    def run(self) -> CleanupResult:
        """設定に基づいてクリーンアップを実行する。"""
        settings = resolve_cleanup_settings(self._db.settings)
        mode = settings["mode"]

        if mode == "disabled":
            return CleanupResult()

        dates = self._db.nights.get_all_dates_asc()
        if not dates:
            return CleanupResult()

        if mode == "retention_days":
            return self._cleanup_by_retention(dates, int(settings["retention_days"]))
        elif mode == "min_free_gb":
            return self._cleanup_by_free_space_gb(
                dates, float(settings["min_free_gb"]),
            )
        elif mode == "min_free_pct":
            return self._cleanup_by_free_space_pct(
                dates, float(settings["min_free_pct"]),
            )

        return CleanupResult()

    def delete_night_downloads(self, date_str: str) -> int:
        """特定の夜のダウンロード MP4 をすべて削除する。"""
        clips = self._db.clips.get_clips_with_local_path(date_str)
        total_freed = 0

        for clip in clips:
            local_path = Path(clip["local_path"])
            if not self._is_safe_path(local_path):
                continue
            if local_path.exists():
                total_freed += local_path.stat().st_size
                local_path.unlink()
                logger.debug("削除: %s", local_path)

        # 空ディレクトリを除去
        self._remove_empty_dirs(self._download_dir)

        # DB の local_path をクリア
        self._db.clips.clear_local_paths(date_str)

        return total_freed

    def get_storage_info(self) -> dict[str, object]:
        """ストレージの使用状況を返す。"""
        usage = shutil.disk_usage(str(self._download_dir))
        downloads_size = self._get_dir_size(self._download_dir)

        # 夜ごとのダウンロードサイズ
        night_sizes: list[dict[str, object]] = []
        dates = self._db.nights.get_all_dates_asc()
        for date_str in dates:
            clips = self._db.clips.get_clips_with_local_path(date_str)
            size = 0
            for clip in clips:
                p = Path(clip["local_path"])
                if p.exists():
                    size += p.stat().st_size
            if size > 0:
                night_sizes.append({"date_str": date_str, "size": size})

        return {
            "disk_total": usage.total,
            "disk_used": usage.used,
            "disk_free": usage.free,
            "disk_free_pct": round(usage.free / usage.total * 100, 1)
            if usage.total > 0
            else 0,
            "downloads_size": downloads_size,
            "night_sizes": night_sizes,
        }

    def _cleanup_by_retention(
        self, dates: list[str], retention_days: int,
    ) -> CleanupResult:
        """N日より古い夜のダウンロードを削除する。"""
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y%m%d")
        result = CleanupResult()

        for date_str in dates:
            if date_str >= cutoff:
                break
            freed = self.delete_night_downloads(date_str)
            if freed > 0:
                result.nights_cleaned += 1
                result.bytes_freed += freed
                logger.info("クリーンアップ: %s を削除 (%d bytes)", date_str, freed)

        return result

    def _cleanup_by_free_space_gb(
        self, dates: list[str], min_free_gb: float,
    ) -> CleanupResult:
        """空き容量が閾値未満なら古い夜から削除する。"""
        result = CleanupResult()

        for date_str in dates:
            usage = shutil.disk_usage(str(self._download_dir))
            free_gb = usage.free / (1024 ** 3)
            if free_gb >= min_free_gb:
                break
            freed = self.delete_night_downloads(date_str)
            if freed > 0:
                result.nights_cleaned += 1
                result.bytes_freed += freed
                logger.info("クリーンアップ: %s を削除 (%d bytes)", date_str, freed)

        return result

    def _cleanup_by_free_space_pct(
        self, dates: list[str], min_free_pct: float,
    ) -> CleanupResult:
        """空き容量%が閾値未満なら古い夜から削除する。"""
        result = CleanupResult()

        for date_str in dates:
            usage = shutil.disk_usage(str(self._download_dir))
            free_pct = usage.free / usage.total * 100 if usage.total > 0 else 100
            if free_pct >= min_free_pct:
                break
            freed = self.delete_night_downloads(date_str)
            if freed > 0:
                result.nights_cleaned += 1
                result.bytes_freed += freed
                logger.info("クリーンアップ: %s を削除 (%d bytes)", date_str, freed)

        return result

    def _is_safe_path(self, path: Path) -> bool:
        """削除対象がダウンロードディレクトリ配下であることを確認する。"""
        try:
            path.resolve().relative_to(self._download_dir.resolve())
            return True
        except ValueError:
            logger.warning("安全チェック: %s はダウンロードディレクトリ外です", path)
            return False

    def _remove_empty_dirs(self, base_dir: Path) -> None:
        """ベースディレクトリ配下の空ディレクトリを再帰的に除去する。"""
        if not base_dir.is_dir():
            return
        for child in sorted(base_dir.rglob("*"), reverse=True):
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()

    @staticmethod
    def _get_dir_size(path: Path) -> int:
        """ディレクトリのトータルサイズを計算する。"""
        if not path.is_dir():
            return 0
        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total
