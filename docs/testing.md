# Test Strategy

## Overview

- **フレームワーク**: pytest
- **カバレッジ**: pytest-cov
- **モック**: pytest-mock, respx (HTTP)
- **実行**: `uv run pytest`

## Mock Boundaries

| Module | Mock Target |
|--------|-------------|
| config | None (pure logic) |
| db | None (in-memory SQLite) |
| lock | fcntl.flock |
| downloader | respx (HTTP mocking) |
| detector | cv2.VideoCapture |
| compositor | None (real files with numpy arrays) |
| concatenator | subprocess.run |
| hooks | None (in-process) |
| pipeline | All modules (DI) |
| cli | CliRunner |
| web | httpx.AsyncClient (TestClient) |

## Fixtures (conftest.py)

- `sample_config`: AppConfig with temp directories
- `memory_db`: In-memory SQLite StateDB
- `tmp_dirs`: Temporary download/output directories
- `fake_frames`: Synthetic video frames for detector testing

## Test Cases

### test_config.py
- YAML loading with all fields
- Default values when fields omitted
- Path resolution (expanduser)
- Frozen model validation (immutability)
- WebConfig defaults
- ConfigError on missing file
- ConfigError on invalid YAML

### test_db.py

#### TestClipStatus
- test_values — ClipStatus StrEnum 値の検証
- test_is_str — str 型であることの検証

#### TestClipRepository
- test_upsert_and_get — クリップの挿入と取得
- test_get_nonexistent — 存在しないクリップの取得
- test_update_status — ステータス更新
- test_get_clips_by_date — 日付別クリップ取得
- test_get_detected_clips — 検出済みクリップのフィルタリング
- test_toggle_excluded — 除外フラグの切り替え
- test_get_included_detected_clips — 含有中の検出済みクリップ（excluded=0）
- test_get_clip_by_id — ID によるクリップ取得
- test_upsert_preserves_terminal_detected — 終端ステータス（detected）の保持
- test_upsert_preserves_terminal_no_detection — 終端ステータス（no_detection）の保持
- test_upsert_preserves_terminal_error — 終端ステータス（error）の保持
- test_upsert_allows_pending_to_downloaded — pending → downloaded の遷移許可

#### TestDetectionRepository
- test_bulk_insert_and_get — 一括挿入と取得
- test_toggle_excluded — 除外フラグの切り替え
- test_set_all_excluded_by_clip — クリップ単位の一括除外設定
- test_set_all_excluded_by_date — 日付単位の一括除外設定
- test_delete_by_clip — クリップ単位の削除
- test_upsert_detection — 検出の upsert
- test_get_nonexistent — 存在しない検出の取得

#### TestNightOutputRepository
- test_upsert_and_get — 夜間出力の挿入と取得
- test_get_nonexistent — 存在しない夜間出力の取得
- test_get_all_nights — 全夜間リストの取得

#### TestSettingsRepository
- test_get_nonexistent — 存在しない設定の取得
- test_set_and_get — 設定の保存と取得
- test_set_overwrite — 設定の上書き
- test_get_all_empty — 空の設定リスト
- test_get_all — 全設定の取得
- test_set_many — 複数設定の一括保存
- test_set_many_overwrite — 複数設定の一括上書き

#### TestStateDB
- test_facade — StateDB ファサードの検証
- test_from_path — ファイルパスからの生成

### test_lock.py
- Lock acquire and release
- Double lock raises LockError
- Context manager cleanup

### test_downloader.py
- HTML directory listing parse
- Stream download success
- Retry on failure
- DownloadError after exhausting retries
- Skip existing files

### test_detector.py
- Synthetic meteor frame → detected=True
- Dark/uniform frames → detected=False
- Mask application
- DetectionError on corrupt video

### test_compositor.py
- np.maximum verification with known pixel values
- Incremental compositing (existing composite)
- CompositorError on empty input
- Size mismatch handling

### test_concatenator.py
- Single video → copy
- Multiple videos → ffmpeg concat arguments
- ConcatenationError on empty list
- ConcatenationError on ffmpeg failure

### test_hooks.py
- HookRunner invokes all hooks
- Hook failure isolation (other hooks still run)
- LoggingHook logs events
- Empty HookRunner does nothing

### test_pipeline.py
- Date auto-determination
- Time slot building
- Dry-run mode (no side effects)
- Error continuation (skip failed clips)
- rebuild_outputs with excluded clips
- Full pipeline with mocked modules

### test_cli.py

#### TestCLI
- test_help — --help の出力
- test_run_help — run --help の出力
- test_config_validate — config --validate
- test_config_show — config --show（設定内容の表示）
- test_run_dry_run — run --dry-run
- test_redetect_help — redetect --help の出力
- test_redetect_command — redetect コマンドの実行
- test_serve — serve（mocked uvicorn）

### test_web.py

#### TestHTMLPages
- test_index_page — トップページの表示
- test_index_with_data — データ有りでのトップページ表示
- test_night_page — ナイト詳細ページの表示
- test_night_page_has_concatenate_button — 結合ボタンの表示

#### TestAPI
- test_api_nights — 全夜間リストの API
- test_api_night_detail — 夜間詳細の API
- test_api_night_clips — クリップリストの API
- test_patch_excluded — 除外フラグの切り替え
- test_patch_missing_field — 必須フィールド欠落
- test_patch_nonexistent_clip — 存在しないクリップ
- test_rebuild_trigger — リビルドの開始
- test_rebuild_status — リビルドの進捗
- test_concatenate_trigger — 動画結合の開始
- test_concatenate_status — 動画結合の進捗
- test_redetect_trigger — 再検出の開始
- test_redetect_status — 再検出の進捗
- test_redetect_status_with_progress — 進捗付き再検出ステータス
- test_redetect_duplicate_rejected — 再検出の重複拒否
- test_redetect_cancel — 再検出のキャンセル
- test_redetect_cancel_no_task — タスク未実行時のキャンセル

#### TestDetectionAPI
- test_toggle_detection — 検出線の除外トグル
- test_toggle_detection_missing_field — 必須フィールド欠落
- test_toggle_detection_not_found — 存在しない検出線
- test_bulk_detections — 夜間全検出の一括除外
- test_bulk_detections_missing_field — 必須フィールド欠落

#### TestNightVisibilityAPI
- test_toggle_night_visibility — 夜間データの表示/非表示切り替え
- test_toggle_night_visibility_missing_field — 必須フィールド欠落
- test_toggle_night_visibility_not_found — 存在しない夜間データ
- test_index_hides_hidden_nights — 非表示夜間のトップページ除外

#### TestAdminPage
- test_admin_page — 管理ページの表示
- test_admin_shows_hidden_nights — 非表示夜間の管理ページ表示
- test_admin_no_hidden — 非表示データなし時の表示

#### TestSettingsAPI
- test_get_schedule_defaults — スケジュール設定のデフォルト値
- test_put_and_get_schedule — スケジュール設定の保存と取得
- test_put_empty_body — 空ボディでの保存
- test_get_prefectures — 都道府県リストの取得
- test_preview_schedule — スケジュールプレビュー

#### TestSchedulerAPI
- test_scheduler_status — スケジューラ状態の取得
- test_interval_minutes_in_schedule_settings — スケジュール設定内の実行間隔
- test_put_interval_minutes — 実行間隔の保存

#### TestSystemSettingsAPI
- test_get_system_defaults — システム設定のデフォルト値
- test_put_and_get_system — システム設定の保存と取得
- test_put_empty_body — 空ボディでの保存

#### TestDetectionSettingsAPI
- test_get_detection_defaults — 検出パラメータのデフォルト値
- test_put_and_get_detection — 検出パラメータの保存と取得
- test_put_empty_body — 空ボディでの保存
- test_put_invalid_keys_only — 無効キーのみの保存

#### TestResetSettingsAPI
- test_reset_schedule — スケジュール設定のリセット
- test_reset_detection — 検出パラメータのリセット
- test_reset_system — システム設定のリセット
