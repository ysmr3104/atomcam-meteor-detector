# Raspberry Pi セットアップガイド

ATOM Cam 流星検出ツールを Raspberry Pi 上で動作させるための手順書。

## 推奨ハードウェア

| 項目 | 推奨 |
|---|---|
| モデル | Raspberry Pi 4 Model B |
| RAM | 4GB 以上 |
| ストレージ | microSD 32GB 以上（高耐久タイプ推奨） |
| 電源 | USB-C 5V/3A 公式電源アダプタ |
| ネットワーク | 有線 LAN 推奨（ATOM Cam からの動画ダウンロードが安定する） |

## 推奨 OS

**Raspberry Pi OS Lite (64-bit, Bookworm)** を推奨します。

### 選定理由

- Debian 12 ベースで Python 3.11 が標準搭載（本ツールは Python 3.10+ が必要）
- 公式 OS のため Raspberry Pi 4B とのハードウェア互換性が最も高い
- Lite 版（デスクトップなし）により RAM・CPU リソースを動画処理に集中できる
- 64-bit 版で ARM64 アーキテクチャをフル活用し、メモリ空間と演算性能が向上
- OpenCV、ffmpeg などの依存パッケージが `apt` で容易にインストール可能
- ヘッドレス・cron 駆動の運用に最適

### ダウンロード

公式サイトからダウンロードしてください：

- **Raspberry Pi Imager（推奨）**: https://www.raspberrypi.com/software/
  - OS 選択で「Raspberry Pi OS (other)」→「Raspberry Pi OS Lite (64-bit)」を選択
  - Imager の設定画面で SSH 有効化、Wi-Fi、ユーザー名/パスワードを事前設定可能
- **OS イメージ直接ダウンロード**: https://www.raspberrypi.com/software/operating-systems/
  - 「Raspberry Pi OS (64-bit) Lite」をダウンロード

## OS イメージの書き込み（別 PC で実施）

microSD カードへの OS イメージ書き込みは、作業用 PC（Windows / macOS / Linux）で行います。

### 準備するもの

- 作業用 PC
- microSD カード（32GB 以上）
- microSD カードリーダー（PC に内蔵されていない場合）

### 手順（Raspberry Pi Imager を使用）

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
     - 「ロケールを設定する」: タイムゾーンを `Asia/Tokyo`、キーボードレイアウトを `jp` に設定
   - **サービスタブ**:
     - 「SSH を有効にする」: 有効にする（ヘッドレス運用のため必須）
     - 「パスワード認証を使う」を選択（後から公開鍵認証に切り替え可能）
   - 設定を保存する

6. **書き込み**
   - 「はい」を押して書き込みを開始
   - 完了したら microSD カードを PC から取り出す

### 手順（手動で書き込む場合）

Raspberry Pi Imager を使わない場合は、以下の手順で書き込みます。

1. https://www.raspberrypi.com/software/operating-systems/ から「Raspberry Pi OS Lite (64-bit)」の `.img.xz` ファイルをダウンロード
2. 書き込みツールで microSD カードに書き込む:
   - **Windows**: [Rufus](https://rufus.ie/) または [balenaEtcher](https://etcher.balena.io/)
   - **macOS / Linux**: [balenaEtcher](https://etcher.balena.io/) または `dd` コマンド
3. 書き込み後、microSD カードの `boot` パーティションに以下のファイルを作成して SSH を有効化:
   ```bash
   # boot パーティションに空の ssh ファイルを作成
   touch /Volumes/bootfs/ssh   # macOS の場合
   ```

## Raspberry Pi の起動と初期接続

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

---

> **次のステップ**: SSH 接続できたら、OS の初期設定と本ツールの依存パッケージインストールに進みます（別途追記予定）。
