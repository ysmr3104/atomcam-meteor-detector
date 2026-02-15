# セットアップガイド

## 共通要件

- Python 3.10+
- ffmpeg（動画結合用）
- ATOM Cam（HTTP アクセス可能な状態）

---

## PC 環境でのセットアップ

macOS / Linux / Windows（WSL）での開発・実行環境の構築手順。

### インストール

```bash
git clone https://github.com/ysmr3104/atomcam-meteor-detector.git
cd atomcam-meteor-detector

# uv を使用
uv sync

# pip を使用する場合
pip install -e .

# 開発用（pytest, respx 等を追加）
uv sync --group dev
```

### 設定

設定ファイルをコピーして編集:

```bash
cp config/settings.example.yaml config/settings.yaml
```

主な変更点：

- `camera.host`: ATOM Cam のホスト名に合わせて変更（例: `atomcam2.local`）

```yaml
camera:
  host: "atomcam2.local"
```

> **補足**: `http_user` / `http_password` はデフォルトで無効（認証なし）です。ATOM Cam 2 はそのままで動作します。認証が必要な環境の場合のみ設定してください。

詳細は `config/settings.example.yaml` 内のコメントを参照してください。

### 使い方

#### CLI

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
```

#### Web ダッシュボード

```bash
atomcam serve -c config/settings.yaml
```

ブラウザで `http://localhost:8080` にアクセス。

- **ナイト一覧** (`/`): 日付ごとの検出数と合成画像サムネイルをグリッド表示
- **ナイト詳細** (`/nights/{date}`): 合成画像・結合動画・検出クリップのグリッド表示と操作
  - 検出線単位での除外/含有トグル
  - **Re-detect**: ダウンロード済みクリップから検出をやり直し
  - **Rebuild**: excluded を反映して合成画像・結合動画を再作成
  - **Hide Night**: 夜間データをトップページから非表示
- **管理ページ** (`/admin`): 以下の5タブで設定を変更
  - **観測スケジュール**: 開始/終了時刻のモード（固定 / 天文薄明 / 薄明オフセット）、観測地点（47都道府県プリセット / カスタム座標）
  - **自動実行**: パイプライン実行間隔、スケジューラ状態確認
  - **検出パラメータ**: min_line_length, canny_threshold 等の調整
  - **システム**: 定期再起動の設定
  - **データ管理**: 非表示にした夜間データの一覧表示・再表示

> **設定の優先順位**: 管理ページで変更した設定は SQLite DB に保存され、`config/settings.yaml` の値より優先されます。管理ページでリセットすると YAML の値に戻ります。

---

## Raspberry Pi 環境でのセットアップ

ATOM Cam 流星検出ツールを Raspberry Pi 上で常時稼働させるための手順。

### 推奨ハードウェア

| 項目 | 推奨 |
|---|---|
| モデル | Raspberry Pi 4 Model B |
| RAM | 4GB 以上 |
| ストレージ | microSD 32GB 以上（高耐久タイプ推奨） |
| 電源 | USB-C 5V/3A 公式電源アダプタ |
| ネットワーク | 有線 LAN 推奨（ATOM Cam からの動画ダウンロードが安定する） |

### 推奨 OS

**Raspberry Pi OS Lite (64-bit, Bookworm)** を推奨します。

#### 選定理由

- Debian 12 ベースで Python 3.11 が標準搭載（本ツールは Python 3.10+ が必要）
- 公式 OS のため Raspberry Pi 4B とのハードウェア互換性が最も高い
- Lite 版（デスクトップなし）により RAM・CPU リソースを動画処理に集中できる
- 64-bit 版で ARM64 アーキテクチャをフル活用し、メモリ空間と演算性能が向上
- OpenCV、ffmpeg などの依存パッケージが `apt` で容易にインストール可能
- ヘッドレス運用に最適

#### ダウンロード

公式サイトからダウンロードしてください：

- **Raspberry Pi Imager（推奨）**: https://www.raspberrypi.com/software/
  - OS 選択で「Raspberry Pi OS (other)」→「Raspberry Pi OS Lite (64-bit)」を選択
  - Imager の設定画面で SSH 有効化、Wi-Fi、ユーザー名/パスワードを事前設定可能
- **OS イメージ直接ダウンロード**: https://www.raspberrypi.com/software/operating-systems/
  - 「Raspberry Pi OS (64-bit) Lite」をダウンロード

### OS イメージの書き込み

#### 準備するもの

- microSD カード（32GB 以上）
- microSD カードリーダー（PC に内蔵されていない場合）

#### Raspberry Pi Imager を使用する場合

1. **Raspberry Pi Imager のインストール**
   - https://www.raspberrypi.com/software/ から作業用 PC の OS に合ったインストーラをダウンロード
   - インストールして起動する

2. **デバイスの選択**
   - 「デバイスを選択」→「Raspberry Pi 4」を選択

3. **OS の選択**
   - 「OSを選択」→「Raspberry Pi OS (other)」→「Raspberry Pi OS Lite (64-bit)」を選択

4. **ストレージの選択**
   - microSD カードを PC に挿入
   - 「ストレージを選択」→ 挿入した microSD カードを選択

5. **カスタム設定（重要）**
   - 「次へ」を押すと「OSのカスタマイズを使いますか？」と表示されるので「設定を編集する」を選択
   - **一般タブ**:
     - 「ホスト名」: 任意の名前を設定（例: `meteor-pi`）
     - 「ユーザー名とパスワードを設定する」: 有効にして任意のユーザー名・パスワードを設定
     - 「Wi-Fi を設定する」: 必要に応じて SSID とパスワードを入力（有線 LAN 推奨だが初期接続用に設定しておくと便利）
     - 「ロケールを設定する」: タイムゾーンを `Asia/Tokyo`、キーボードレイアウトは環境に合わせて設定（SSH 接続時はクライアント側のレイアウトが使われるため、ヘッドレス運用なら任意で可）
   - **サービスタブ**:
     - 「SSH を有効にする」: 有効にする（ヘッドレス運用のため必須）
     - 「パスワード認証を使う」を選択（後から公開鍵認証に切り替え可能）
   - 設定を保存する

6. **書き込み**
   - 「はい」を押して書き込みを開始
   - 完了したら microSD カードを PC から取り出す

#### 手動で書き込む場合

1. https://www.raspberrypi.com/software/operating-systems/ から「Raspberry Pi OS Lite (64-bit)」の `.img.xz` ファイルをダウンロード
2. 書き込みツールで microSD カードに書き込む:
   - **Windows**: [Rufus](https://rufus.ie/) または [balenaEtcher](https://etcher.balena.io/)
   - **macOS / Linux**: [balenaEtcher](https://etcher.balena.io/) または `dd` コマンド
3. 書き込み後、microSD カードの `boot` パーティションに以下のファイルを作成して SSH を有効化:
   ```bash
   # boot パーティションに空の ssh ファイルを作成
   touch /Volumes/bootfs/ssh   # macOS の場合
   ```

### Raspberry Pi の起動と初期接続

1. 書き込み済みの microSD カードを Raspberry Pi に挿入
2. LAN ケーブルを接続（推奨）
3. 電源を接続して起動（初回起動は数分かかる場合がある）
4. 作業用 PC から SSH で接続:
   ```bash
   ssh <ユーザー名>@<ホスト名>.local
   # 例: ssh meteor@meteor-pi.local
   ```
   - `.local` で見つからない場合は、ルーターの管理画面等で Raspberry Pi の IP アドレスを確認して直接指定
   ```bash
   ssh <ユーザー名>@<IPアドレス>
   ```

### OS の初期設定

SSH 接続後、以下のコマンドで OS を最新の状態にします。

```bash
sudo apt update && sudo apt upgrade -y
```

再起動が必要な場合：

```bash
sudo reboot
```

### 依存パッケージのインストール

#### システムパッケージ

OpenCV と ffmpeg に必要なシステムライブラリをインストールします。

```bash
sudo apt install -y \
  git \
  ffmpeg \
  libopencv-dev \
  python3-dev \
  python3-venv
```

#### uv（Python パッケージマネージャ）のインストール

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

シェルを再読み込みして `uv` コマンドを有効にします：

```bash
source ~/.local/bin/env
```

インストール確認：

```bash
uv --version
```

### 本ツールのセットアップ

#### リポジトリのクローン

```bash
cd ~
git clone https://github.com/ysmr3104/atomcam-meteor-detector.git
cd atomcam-meteor-detector
```

#### Python 依存パッケージのインストール

```bash
uv sync
```

#### 設定ファイルの作成

```bash
cp config/settings.example.yaml config/settings.yaml
```

`config/settings.yaml` を環境に合わせて編集してください。

主な変更点：

- `camera.host`: ATOM Cam のホスト名に合わせて変更（例: `atomcam2.local`）

```yaml
camera:
  host: "atomcam2.local"
```

> **補足**: `http_user` / `http_password` はデフォルトで無効（認証なし）です。ATOM Cam 2 はそのままで動作します。認証が必要な環境の場合のみ設定してください。

#### 動作確認

```bash
uv run atomcam --help
```

### Web ダッシュボードの自動起動（systemd）

Web ダッシュボードを OS 起動時に自動で立ち上げるには、systemd のサービスを作成します。

#### サービスファイルの作成

```bash
sudo nano /etc/systemd/system/atomcam-web.service
```

以下の内容を記述します（`User` と `WorkingDirectory` は環境に合わせて変更してください）：

```ini
[Unit]
Description=ATOM Cam Meteor Detector Web Dashboard
After=network.target

[Service]
Type=simple
User=ysmr3104
WorkingDirectory=/home/ysmr3104/atomcam-meteor-detector
ExecStart=/home/ysmr3104/.local/bin/uv run atomcam serve -c config/settings.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### サービスの有効化と起動

```bash
sudo systemctl daemon-reload
sudo systemctl enable atomcam-web
sudo systemctl start atomcam-web
```

#### 状態確認

```bash
sudo systemctl status atomcam-web
```

ブラウザから `http://<ホスト名>.local:8080/` にアクセスして表示を確認してください。

### パイプラインの自動実行

Web ダッシュボードに内蔵されたスケジューラが、観測時間帯中にパイプラインを自動定期実行します。cron の設定は不要です。

- **実行間隔**: 管理ページの「自動実行」タブで設定（デフォルト: 60分）
- **観測時間帯**: 管理ページの「観測スケジュール」タブで設定
- **排他制御**: FileLock により CLI (`atomcam run`) との同時実行を防止
- `interval_minutes` を `0` に設定するとスケジューラ無効（手動実行のみ）

### 外部からのアクセス（Tailscale）

Web ダッシュボードは LAN 内からのみアクセス可能ですが、[Tailscale](https://tailscale.com/) を使うことで外出先のスマホや PC からも安全にアクセスできます。

#### Raspberry Pi 側の設定

```bash
# Tailscale をインストール
curl -fsSL https://tailscale.com/install.sh | sh

# 起動・認証（表示される URL をブラウザで開いてログイン）
sudo tailscale up

# Tailscale IP を確認
tailscale ip -4
# 例: 100.89.209.44
```

#### スマホ / PC 側の設定

1. Tailscale アプリをインストール（[iOS](https://apps.apple.com/app/tailscale/id1470499037) / [Android](https://play.google.com/store/apps/details?id=com.tailscale.ipn) / [macOS・Windows](https://tailscale.com/download)）
2. Raspberry Pi と同じアカウントでログイン
3. ブラウザで `http://<Tailscale IP>:8080/` にアクセス

Tailscale は systemd サービスとして自動起動するため、Raspberry Pi の再起動後も設定は維持されます。
