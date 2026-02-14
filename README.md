# atomcam-meteor-detector

ATOM Cam の動画から流星を自動検出し、比較明合成画像と結合動画を生成するツール。

## Features

- ATOM Cam の SD カード録画から1分クリップを自動ダウンロード
- フレーム差分 + Hough 変換による流星自動検出
- 比較明合成 (lighten composite) 画像の自動生成
- 検出クリップの結合動画を ffmpeg で生成
- Web ダッシュボードで検出結果の確認・除外・再合成
- cron による定期実行 + systemd による Web サーバー常駐

### Web ダッシュボード

| ナイト一覧 | ナイト詳細 |
|:---:|:---:|
| ![ナイト一覧](docs/images/screenshot-nights.jpeg) | ![ナイト詳細](docs/images/screenshot-night-detail.jpeg) |

## Getting Started

セットアップ手順は [docs/setup.md](docs/setup.md) を参照してください。

- [PC 環境でのセットアップ](docs/setup.md#pc-環境でのセットアップ)
- [Raspberry Pi 環境でのセットアップ](docs/setup.md#raspberry-pi-環境でのセットアップ)

## 参考

流星検出アルゴリズム（フレーム差分 → Canny エッジ検出 → HoughLinesP 直線検出）およびその検出パラメータは、kin-hasegawa さんの [meteor-detect](https://github.com/kin-hasegawa/meteor-detect) を強く参考にしています。

詳細な比較は [docs/reference-comparison-kin-hasegawa.md](docs/reference-comparison-kin-hasegawa.md) を参照してください。

## ドキュメント

| ドキュメント | 内容 |
|------------|------|
| [docs/setup.md](docs/setup.md) | セットアップ・使い方（PC / Raspberry Pi） |
| [docs/specs.md](docs/specs.md) | アーキテクチャ、DB スキーマ、API 仕様 |
| [docs/testing.md](docs/testing.md) | テスト規約 |
| [docs/reference-comparison-kin-hasegawa.md](docs/reference-comparison-kin-hasegawa.md) | kin-hasegawa/meteor-detect との比較 |

## Development

```bash
# 開発用依存パッケージのインストール
uv sync --group dev

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
