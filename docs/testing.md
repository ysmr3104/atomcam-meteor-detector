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
- ClipStatus StrEnum values
- Clip CRUD (upsert, get, update_status)
- get_detected_clips filtering
- get_included_detected_clips (excluded=0)
- toggle_excluded
- get_clip_by_id
- Night output CRUD
- get_all_nights ordering
- StateDB facade

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
- --help output
- config --validate
- run --dry-run
- status output
- serve (mocked uvicorn)

### test_web.py
- GET / returns night list HTML
- GET /nights/{date} returns detail HTML
- GET /api/nights returns JSON
- PATCH /api/clips/{id} toggles excluded
- POST /api/nights/{date}/rebuild starts rebuild
- GET /api/nights/{date}/rebuild/status returns progress
- 404 on unknown clip
