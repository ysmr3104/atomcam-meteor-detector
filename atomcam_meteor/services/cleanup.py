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
        """特定の夜のダウンロード MP4 をすべて削除する。

        DB に登録されたファイルに加え、パイプラインがダウンロードしたが
        観測範囲外で DB 未登録のファイルもファイルシステムスキャンで削除する。
        """
        clips = self._db.clips.get_clips_with_local_path(date_str)
        total_freed = 0
        deleted_paths: set[Path] = set()

        for clip in clips:
            local_path = Path(clip["local_path"])
            if not self._is_safe_path(local_path):
                continue
            if local_path.exists():
                total_freed += local_path.stat().st_size
                local_path.unlink()
                deleted_paths.add(local_path.resolve())
                logger.debug("削除: %s", local_path)

        # DB の local_path をクリア
        self._db.clips.clear_local_paths(date_str)

        # ファイルシステムスキャンで DB 未登録の残存ファイルも削除
        total_freed += self._delete_orphaned_files(date_str, deleted_paths)

        # 空ディレクトリを除去
        self._remove_empty_dirs(self._download_dir)

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

    def _delete_orphaned_files(
        self, date_str: str, already_deleted: set[Path],
    ) -> int:
        """観測夜のディレクトリに残存する DB 未登録ファイルを削除する。

        パイプラインはアワーディレクトリ全体をダウンロードするが、観測範囲外の
        クリップは DB に登録されない。この関数はそれらの孤立ファイルを削除する。
        """
        from atomcam_meteor.services.schedule_resolver import resolve_schedule

        try:
            start_time, end_time = resolve_schedule(
                self._db.settings, self._config.schedule, date_str,
            )
        except Exception:
            logger.warning("スケジュール解決に失敗、ディレクトリスキャンをスキップ")
            return 0

        dirs = self._build_night_dirs(date_str, start_time, end_time)
        total_freed = 0

        for dir_path in dirs:
            if not dir_path.is_dir():
                continue
            for mp4 in dir_path.glob("*.mp4"):
                if mp4.resolve() in already_deleted:
                    continue
                if not self._is_safe_path(mp4):
                    continue
                total_freed += mp4.stat().st_size
                mp4.unlink()
                logger.debug("残存ファイル削除: %s", mp4)

        return total_freed

    def _build_night_dirs(
        self, date_str: str, start_time: str, end_time: str,
    ) -> list[Path]:
        """観測夜に対応するダウンロードディレクトリのリストを返す。

        Pipeline._build_time_slots() と同じロジックでディレクトリを特定する。
        """
        target = datetime.strptime(date_str, "%Y%m%d")
        prev_day = (target - timedelta(days=1)).strftime("%Y%m%d")

        start_h = int(start_time.split(":")[0])
        end_h = int(end_time.split(":")[0])
        end_m = int(end_time.split(":")[1])
        start_total = start_h * 60 + int(start_time.split(":")[1])
        end_total = end_h * 60 + end_m

        dirs: list[Path] = []
        if start_total >= end_total:  # 日付またぎ（例: 22:00→06:00）
            for h in range(start_h, 24):
                dirs.append(self._download_dir / prev_day / f"{h:02d}")
            end_h_inclusive = end_h + 1 if end_m > 0 else end_h
            for h in range(0, end_h_inclusive):
                dirs.append(self._download_dir / date_str / f"{h:02d}")
        else:  # 同日内
            end_h_inclusive = end_h + 1 if end_m > 0 else end_h
            for h in range(start_h, end_h_inclusive):
                dirs.append(self._download_dir / date_str / f"{h:02d}")

        return dirs

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
