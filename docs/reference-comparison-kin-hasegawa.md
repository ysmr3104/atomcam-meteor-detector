# kin-hasegawa さんの meteor-detect との比較

本プロジェクト（atomcam-meteor-detector）の流星検出ロジックは、kin-hasegawa さんの [meteor-detect](https://github.com/kin-hasegawa/meteor-detect) を強く参考にしています。このドキュメントでは、参考にした部分とオリジナル部分を明確にします。

---

## 参考にした部分

### 1. 流星検出のコアアルゴリズム

処理パイプラインの設計思想と処理順序を参考にしています。

```
露出時間分のフレーム取得
  → 連続フレーム間の差分（cv2.subtract）
  → 差分画像の比較明合成（ピクセル単位 max）
  → GaussianBlur(5, 5)
  → Canny エッジ検出
  → HoughLinesP 直線検出
```

| 処理ステップ | kin-hasegawa (`atomcam.py`) | 本プロジェクト (`detector.py`) |
|------------|---------------------------|-------------------------------|
| フレームグループ化 | `num_frames = int(FPS * exposure)` | `frames_per_group = int(fps * exposure_duration_sec)` |
| フレーム差分 | `cv2.subtract(img1, img2)` | `cv2.subtract(group_gray[i+1], group_gray[i])` |
| 差分の比較明合成 | `brightest(diff_list)` → `np.where` | `cv2.max(diff_composite, diff)` |
| ガウシアンブラー | `cv2.GaussianBlur(img, (5,5), 0)` | `cv2.GaussianBlur(img, (5,5), 0)` |
| エッジ検出 | `cv2.Canny(blur, 100, 200, 3)` | `cv2.Canny(blurred, canny_threshold1, canny_threshold2)` |
| 直線検出 | `cv2.HoughLinesP(canny, 1, np.pi/180, 25, minLineLength=min_length, maxLineGap=5)` | `cv2.HoughLinesP(edges, rho=1, theta=np.pi/180, threshold=hough_threshold, minLineLength=min_line_length, maxLineGap=max_line_gap)` |

### 2. 検出パラメータの具体値

デフォルトのパラメータ値はすべて kin-hasegawa さんのプログラムと同一です。

| パラメータ | kin-hasegawa | 本プロジェクト | 備考 |
|-----------|-------------|--------------|------|
| GaussianBlur カーネル | `(5, 5)` | `(5, 5)` | 同一 |
| GaussianBlur sigma | `0` | `0` | 同一 |
| Canny 低閾値 | `100` | `100` | 同一（本プロジェクトでは設定変更可） |
| Canny 高閾値 | `200` | `200` | 同一（本プロジェクトでは設定変更可） |
| Hough rho | `1` | `1` | 同一 |
| Hough theta | `np.pi / 180` | `np.pi / 180` | 同一 |
| Hough threshold | `25` | `25` | 同一（本プロジェクトでは設定変更可） |
| minLineLength | `30` | `30` | 同一（本プロジェクトでは設定変更可） |
| maxLineGap | `5` | `5` | 同一（本プロジェクトでは設定変更可） |

### 3. 比較明合成の手法

ピクセル単位で最大輝度値を選択する比較明合成の手法を参考にしています。

| 要素 | kin-hasegawa | 本プロジェクト |
|------|-------------|--------------|
| 実装 | `np.where(output > img, output, img)` | `np.maximum(result, image)` |
| 原理 | ピクセル単位で最大値を選択 | 同じ（numpy API が異なるのみ） |

### 4. 露出時間（フレームグループ化）の概念

1秒単位でフレームをまとめて処理する設計を参考にしています。

| 要素 | kin-hasegawa | 本プロジェクト |
|------|-------------|--------------|
| パラメータ名 | `exposure` | `exposure_duration_sec` |
| デフォルト値 | `1` 秒 | `1.0` 秒 |
| フレーム数計算 | `int(FPS * exposure)` | `int(fps * exposure_duration_sec)` |

### 5. マスク処理の考え方

タイムスタンプ等の不要領域をマスクで除外する設計を参考にしています。

| 要素 | kin-hasegawa | 本プロジェクト |
|------|-------------|--------------|
| カスタムマスク | `--mask` で画像ファイル指定 | `mask_path` 設定でファイル指定 |
| タイムスタンプ除外 | `cv2.rectangle` で座標固定指定 | `exclude_bottom_pct` で下部 N% を除外 |
| マスク適用方法 | `cv2.bitwise_or` で差分前に適用 | `cv2.bitwise_and` で差分合成後に適用 |

---

## 本プロジェクトのオリジナル部分

以下は kin-hasegawa さんのプログラムには存在しない、本プロジェクト独自の設計・実装です。

### アーキテクチャ・設計

| 機能 | 説明 |
|------|------|
| Pipeline クラス | 依存性注入パターンによるモジュール統括。テスト時にモック差し替え可能 |
| 状態管理（SQLite） | WAL モードの SQLite で CLI パイプラインと Web UI が並行アクセス |
| StateDB ファサード | `ClipRepository` + `NightOutputRepository` を統合するファサードパターン |
| Pydantic 設定モデル | frozen（イミュータブル）な Pydantic モデルで YAML 設定を型安全に管理 |
| DB ベースの設定上書き | YAML 設定を DB 値で動的に上書きする仕組み |

### Web UI・ユーザー機能

| 機能 | 説明 |
|------|------|
| FastAPI ダッシュボード | クリップ一覧、比較明合成画像、結合動画の閲覧・管理 |
| クリップ除外/含める | 検出線単位での除外トグルと合成画像・結合動画のリビルド |
| 検出パラメータ変更 | Web UI から検出パラメータを動的に変更可能 |
| 再検出 | ダウンロード済みクリップからの再検出機能 |

### スケジューリング・自動化

| 機能 | 説明 |
|------|------|
| 天文薄明スケジュール | 太陽高度 -18° ベースの観測開始・終了時刻の自動計算 |
| FileLock 排他制御 | cron 実行時の重複起動防止 |
| フックシステム | `on_detection`, `on_night_complete`, `on_error` イベント通知 |

### 検出精度向上

| 機能 | 説明 |
|------|------|
| 輝度フィルタ | `min_line_brightness` による差分画像上の平均輝度チェック |
| グループ単位の合成画像保存 | 検出グループごとにフルフレーム合成画像を保存し、細粒度の分析に対応 |
| クリップ抽出 | ffmpeg で検出グループの時間範囲のみを切り出し（前後マージン付き） |

### 動画取得・結合

| 機能 | 説明 |
|------|------|
| HTTP ダウンロード | httpx ストリーミング + リトライ（kin-hasegawa は RTSP/FTP） |
| ffmpeg concat demuxer | `-c copy` による再エンコード不要の高速結合 |

### 品質・テスト

| 機能 | 説明 |
|------|------|
| テスト基盤 | pytest + respx、インメモリ SQLite、カバレッジ計測 |
| 型安全 | mypy strict モード、全ファイルで `from __future__ import annotations` |
| リンター | Ruff（E/F/I/UP/B/SIM ルール） |

---

## 両プロジェクトの設計思想の違い

| 観点 | kin-hasegawa | 本プロジェクト |
|------|-------------|--------------|
| 主な用途 | リアルタイムストリーミング検出（RTSP/YouTube） | cron 駆動のバッチ処理 + Web ダッシュボード |
| 入力ソース | RTSP、YouTube Live、MP4 ファイル | ATOM Cam の HTTP ファイルサーバー |
| 出力管理 | ファイルシステム上に直接保存 | SQLite DB で状態管理 |
| パラメータ | コマンドライン引数 + ハードコード | YAML + DB + Web UI で変更可能 |
| 構成 | 単一スクリプト（約 726 行） | モジュール分割（Pipeline, Detector, Compositor, etc.） |
| 実行環境 | デスクトップ PC / Mac | Raspberry Pi（systemd + cron） |

---

## 謝辞

本プロジェクトの流星検出アルゴリズム（フレーム差分 → Canny エッジ検出 → HoughLinesP 直線検出）およびその検出パラメータは、kin-hasegawa さんの [meteor-detect](https://github.com/kin-hasegawa/meteor-detect) を強く参考にさせていただきました。ありがとうございます。
