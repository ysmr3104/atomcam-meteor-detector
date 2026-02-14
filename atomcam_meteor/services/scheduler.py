"""パイプラインスケジューラ — 観測時間帯にパイプラインを定期実行し、定期再起動を管理する。"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import subprocess
from datetime import date, datetime, timedelta

from atomcam_meteor.config import AppConfig
from atomcam_meteor.exceptions import LockError
from atomcam_meteor.services.schedule_resolver import (
    resolve_interval_minutes,
    resolve_reboot_settings,
    resolve_schedule,
)

logger = logging.getLogger(__name__)

# スケジューラ無効時の再チェック間隔（秒）
_DISABLED_CHECK_SEC = 60
# 観測時間帯外の再チェック間隔（秒）
_OUTSIDE_WINDOW_CHECK_SEC = 300


@dataclasses.dataclass
class SchedulerStatus:
    """スケジューラの現在状態（API レスポンス用）。"""

    enabled: bool = False
    running: bool = False
    in_observation_window: bool = False
    pipeline_running: bool = False
    last_run_at: str | None = None
    last_run_result: str | None = None
    last_run_detections: int = 0
    next_run_at: str | None = None
    interval_minutes: int = 15
    observation_start: str = "22:00"
    observation_end: str = "06:00"
    reboot_enabled: bool = False
    reboot_time: str = "12:00"

    def to_dict(self) -> dict[str, object]:
        """辞書化して返す。"""
        return dataclasses.asdict(self)


class PipelineScheduler:
    """パイプラインを定期実行するスケジューラ。

    Web サーバーの lifespan 内で start() / stop() される。
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_reboot_date: date | None = None
        self.status = SchedulerStatus()

    async def start(self) -> None:
        """スケジューラを開始する。"""
        if self._task is not None:
            logger.warning("スケジューラは既に起動しています")
            return
        self._stop_event.clear()
        self.status.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("スケジューラを開始しました")

    async def stop(self) -> None:
        """スケジューラを停止する。"""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self.status.running = False
        logger.info("スケジューラを停止しました")

    async def _loop(self) -> None:
        """メインスケジューラループ。"""
        from atomcam_meteor.services.db import StateDB

        while not self._stop_event.is_set():
            try:
                # DB から設定を毎サイクル再解決
                db = StateDB.from_path(self._config.paths.resolve_db_path())
                try:
                    interval = resolve_interval_minutes(
                        db.settings, self._config.schedule,
                    )
                    reboot_enabled, reboot_time = resolve_reboot_settings(db.settings)

                    now = datetime.now()

                    # 観測スケジュールの解決
                    date_str = self._determine_date(now)
                    start_time, end_time = resolve_schedule(
                        db.settings, self._config.schedule, date_str,
                    )
                finally:
                    db.close()

                # ステータス更新
                self.status.interval_minutes = interval
                self.status.observation_start = start_time
                self.status.observation_end = end_time
                self.status.reboot_enabled = reboot_enabled
                self.status.reboot_time = reboot_time
                self.status.enabled = interval > 0

                # リブートチェック
                self._check_reboot(
                    now, reboot_enabled, reboot_time, start_time, end_time,
                )

                if interval == 0:
                    # スケジューラ無効 — 60 秒ごとに再チェック
                    self.status.in_observation_window = False
                    self.status.next_run_at = None
                    await self._wait(_DISABLED_CHECK_SEC)
                    continue

                in_window = self._is_in_active_window(
                    now, start_time, end_time, interval,
                )
                self.status.in_observation_window = in_window

                if not in_window:
                    # 観測時間帯外 — 5 分ごとに再チェック
                    self.status.next_run_at = None
                    await self._wait(_OUTSIDE_WINDOW_CHECK_SEC)
                    continue

                # パイプライン実行
                await self._run_pipeline(date_str)

                # 次回実行予定を計算
                next_run = datetime.now() + timedelta(minutes=interval)
                self.status.next_run_at = next_run.strftime("%Y-%m-%d %H:%M:%S")

                # interval 分待機
                await self._wait(interval * 60)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("スケジューラループでエラーが発生しました")
                await self._wait(60)

    async def _run_pipeline(self, date_str: str) -> None:
        """パイプラインを別スレッドで実行する。"""
        self.status.pipeline_running = True
        try:
            result = await asyncio.to_thread(
                self._execute_pipeline_sync, date_str,
            )
            self.status.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.status.last_run_result = result
            if result == "completed":
                # detections_found は _execute_pipeline_sync 内で設定済み
                pass
        except Exception:
            logger.exception("パイプライン実行中にエラーが発生しました")
            self.status.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.status.last_run_result = "error"
            self.status.last_run_detections = 0
        finally:
            self.status.pipeline_running = False

    def _execute_pipeline_sync(self, date_str: str) -> str:
        """FileLock を取得してパイプラインを同期実行する。"""
        from atomcam_meteor.pipeline import Pipeline
        from atomcam_meteor.services.db import StateDB
        from atomcam_meteor.services.lock import FileLock

        lock_path = self._config.paths.resolve_lock_path()
        try:
            with FileLock(lock_path):
                logger.info("スケジューラ: パイプライン実行開始 (date=%s)", date_str)
                db = StateDB.from_path(self._config.paths.resolve_db_path())
                try:
                    pipeline = Pipeline(self._config, db=db)
                    result = pipeline.execute(date_str)
                    self.status.last_run_detections = result.detections_found
                finally:
                    db.close()
                logger.info(
                    "スケジューラ: パイプライン完了 (detections=%d)",
                    result.detections_found,
                )
                return "completed"
        except LockError:
            logger.info("スケジューラ: ロック取得失敗（CLI 実行中）、スキップします")
            return "skipped_lock"

    def _check_reboot(
        self,
        now: datetime,
        reboot_enabled: bool,
        reboot_time: str,
        start_time: str,
        end_time: str,
    ) -> None:
        """リブート時刻の判定と実行。"""
        if not reboot_enabled:
            return
        if self._last_reboot_date == now.date():
            return
        if self._is_in_observation_window(now, start_time, end_time):
            return

        reboot_h, reboot_m = _parse_time(reboot_time)
        if now.hour == reboot_h and abs(now.minute - reboot_m) <= 5:
            logger.warning("定期再起動を実行します")
            self._last_reboot_date = now.date()
            subprocess.run(["sudo", "reboot"])

    async def _wait(self, seconds: float) -> None:
        """キャンセル可能な待機。"""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    @staticmethod
    def _determine_date(now: datetime) -> str:
        """観測日文字列を決定する。正午前 = 今日、正午以降 = 明日。"""
        target = now if now.hour < 12 else now + timedelta(days=1)
        return target.strftime("%Y%m%d")

    @staticmethod
    def _is_in_observation_window(
        now: datetime, start_time: str, end_time: str,
    ) -> bool:
        """現在時刻が観測時間帯内かどうかを判定する。"""
        start_h, start_m = _parse_time(start_time)
        end_h, end_m = _parse_time(end_time)

        current = now.hour * 60 + now.minute
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m

        if start >= end:  # 日付またぎ（例: 22:00 → 06:00）
            return current >= start or current < end
        else:
            return start <= current < end

    @staticmethod
    def _is_in_active_window(
        now: datetime, start_time: str, end_time: str, interval: int,
    ) -> bool:
        """観測時間帯 + バッファ（interval 分）内かどうかを判定する。"""
        start_h, start_m = _parse_time(start_time)
        end_h, end_m = _parse_time(end_time)

        current = now.hour * 60 + now.minute
        start = start_h * 60 + start_m
        # 終了時刻にバッファを追加
        end = (end_h * 60 + end_m + interval) % (24 * 60)

        if start >= end:  # 日付またぎ
            return current >= start or current < end
        else:
            return start <= current < end


def _parse_time(time_str: str) -> tuple[int, int]:
    """HH:MM 形式の文字列を (時, 分) タプルに変換する。"""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])
