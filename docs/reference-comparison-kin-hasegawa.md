# kin-hasegawa さんの meteor-detect との比較

本プロジェクト（atomcam-meteor-detector）の流星検出ロジックは、kin-hasegawa さんの [meteor-detect](https://github.com/kin-hasegawa/meteor-detect) を強く参考にしています。このドキュメントでは、両プロジェクトの設計思想の違いと、参考にした部分を整理します。

---

## 設計思想の違い

| 観点 | kin-hasegawa | 本プロジェクト |
|------|-------------|--------------|
| 主な用途 | リアルタイムストリーミング検出（RTSP/YouTube） | cron 駆動のバッチ処理 + Web ダッシュボード |
| 入力ソース | RTSP、YouTube Live、MP4 ファイル | ATOM Cam の HTTP ファイルサーバー |
| 出力管理 | ファイルシステム上に直接保存 | SQLite DB で状態管理 |
| パラメータ | コマンドライン引数 + ハードコード | YAML + DB + Web UI で変更可能 |
| 構成 | 単一スクリプト（約 726 行） | モジュール分割（Pipeline, Detector, Compositor, etc.） |
| 実行環境 | デスクトップ PC / Mac | Raspberry Pi（systemd + cron） |

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

## 謝辞

本プロジェクトの流星検出アルゴリズム（フレーム差分 → Canny エッジ検出 → HoughLinesP 直線検出）およびその検出パラメータは、kin-hasegawa さんの [meteor-detect](https://github.com/kin-hasegawa/meteor-detect) を強く参考にさせていただきました。ありがとうございます。
