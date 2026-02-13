# atomcam-meteor-detector

ATOM Cam の動画から流星を自動検出し、比較明合成画像と結合動画を生成するツール。

## Features

- ATOM Cam の SD カード録画から1分クリップを自動ダウンロード
- フレーム差分 + Hough 変換による流星自動検出
- 比較明合成 (lighten composite) 画像の自動生成
- 検出クリップの結合動画を ffmpeg で生成
- Web ダッシュボードで検出結果の確認・除外・再合成
- cron による定期実行 + systemd による Web サーバー常駐

## Requirements

- Python 3.10+
- ffmpeg (動画結合用)
- ATOM Cam (HTTP アクセス可能な状態)

## Installation

```bash
# uv を使用
uv sync

# pip を使用
pip install -e .

# 開発用
uv sync --group dev
```

## Configuration

設定ファイルをコピーして編集:

```bash
cp config/settings.example.yaml config/settings.yaml
```

主要な設定項目:

| セクション | キー | 説明 |
|-----------|------|------|
| `camera.host` | カメラのホスト名/IP | `atomcam.local` |
| `camera.http_user` | HTTP 認証ユーザー | `user` |
| `camera.http_password` | HTTP 認証パスワード | `passwd` |
| `detection.min_line_length` | 最小検出線分長 (px) | `30` |
| `paths.download_dir` | DL 先ディレクトリ | `~/atomcam/downloads` |
| `paths.output_dir` | 出力ディレクトリ | `~/atomcam/output` |
| `web.host` | Web サーバーバインドアドレス | `0.0.0.0` |
| `web.port` | Web サーバーポート | `8080` |

## Usage

### CLI

```bash
# パイプライン実行
atomcam run -c config/settings.yaml -v

# 特定日付を指定
atomcam run -c config/settings.yaml --date 20250101

# ドライラン (実際のDL/処理なし)
atomcam run -c config/settings.yaml --dry-run -vv

# 検出ステータス確認
atomcam status -c config/settings.yaml
atomcam status -c config/settings.yaml --date 20250101 --json

# 設定検証
atomcam config -c config/settings.yaml --validate

# Web ダッシュボード起動
atomcam serve -c config/settings.yaml
```

### Web Dashboard

`atomcam serve` で起動後、ブラウザで `http://localhost:8080` にアクセス。

- **ナイト一覧**: 日付ごとの検出数と合成画像サムネイル
- **ナイト詳細**: 合成画像、結合動画、検出クリップのグリッド表示
- **除外/復帰**: クリップごとに included/excluded を切り替え
- **再合成**: excluded を除外したクリップのみで合成画像・結合動画を再作成

### cron 設定例

```cron
*/5 0-6,22-23 * * * cd /path/to/atomcam-meteor-detector && /path/to/uv run atomcam run -c config/settings.yaml -v >> /var/log/atomcam.log 2>&1
```

### systemd 設定例

```ini
# /etc/systemd/system/atomcam-web.service
[Unit]
Description=atomcam-meteor-detector Web Dashboard
After=network.target

[Service]
Type=simple
User=atomcam
WorkingDirectory=/path/to/atomcam-meteor-detector
ExecStart=/path/to/uv run atomcam serve -c config/settings.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable atomcam-web
sudo systemctl start atomcam-web
```

### 外部からのアクセス (Tailscale)

Web ダッシュボードは LAN 内からのみアクセス可能ですが、[Tailscale](https://tailscale.com/) を使うことで外出先のスマホや PC からも安全にアクセスできます。

**Raspberry Pi 側の設定:**

```bash
# Tailscale をインストール
curl -fsSL https://tailscale.com/install.sh | sh

# 起動・認証（表示される URL をブラウザで開いてログイン）
sudo tailscale up

# Tailscale IP を確認
tailscale ip -4
# 例: 100.89.209.44
```

**スマホ / PC 側の設定:**

1. Tailscale アプリをインストール（[iOS](https://apps.apple.com/app/tailscale/id1470499037) / [Android](https://play.google.com/store/apps/details?id=com.tailscale.ipn) / [macOS・Windows](https://tailscale.com/download)）
2. Raspberry Pi と同じアカウントでログイン
3. ブラウザで `http://<Tailscale IP>:8080/` にアクセス

Tailscale は systemd サービスとして自動起動するため、Raspberry Pi の再起動後も設定は維持されます。

## Architecture

```
cron (5分毎)                  systemd (常駐)
└─ atomcam run                └─ atomcam serve
     ├─ FileLock                   ├─ FastAPI + Uvicorn
     ├─ AppConfig                  ├─ Jinja2 Templates
     ├─ StateDB ◄──── SQLite ────► StateDB
     │                (WAL)        │
     ├─ Downloader                 ├─ GET  /
     ├─ Detector                   ├─ GET  /nights/{d}
     ├─ Compositor ◄────────────── POST /rebuild
     ├─ Concatenator               └─ Static files
     └─ HookRunner
```

## Development

```bash
# テスト実行
uv run pytest

# カバレッジ付き
uv run pytest --cov=atomcam_meteor --cov-report=html

# コード品質
uv run ruff check atomcam_meteor/
uv run mypy atomcam_meteor/
```

## License

MIT
