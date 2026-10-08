# News Crawler (news_crawler) — AGENTS.md

AI最新トレンド・技術動向を定期的に自動収集・翻訳・要約・レポート化するスクレイピング・クロールシステム。

- **リポジトリ**: `gitlab.dell.com/asain/news_crawler` (または GitLab 公開リポジトリ)
- **ローカルパス**: `~/dev/news_crawler/`
- **言語**: Python 3.13+ (uv) + Node.js (Markdown Lint)

---

## 技術スタック

| 項目 | 値 |
| --- | --- |
| 言語 | Python 3.13+ |
| パッケージマネージャ | `uv` (`pyproject.toml`, `uv.lock`) |
| スクレイピング | Scrapy, scrapy-playwright, Playwright, feedparser, beautifulsoup4 |
| データベース | SQLite 3 (WAL モード, FTS5 全文検索) |
| 設定管理 | PyYAML, Pydantic v2 |
| ドキュメント Lint | markdownlint-cli, textlint (preset-ja-technical-writing) |
| CI | GitLab CI (`.gitlab-ci.yml`) |
| ライセンス | MIT License |

---

## 主な実行コマンド

### 開発・実行

```bash
cd ~/dev/news_crawler

# クロール実行 (全ソース)
uv run news-crawler crawl

# 特定ソースのみクロール
uv run news-crawler crawl --source openai

# dry-run
uv run news-crawler crawl --dry-run

# 重複検出・排除 (クロスソース)
uv run news-crawler dedupe --days 7 --apply

# AI翻訳・要約 (config/crawler.yamlでai.enabled: true)
uv run news-crawler enrich --days 7 --limit 50

# 特定LLMエンドポイントを指定して要約
uv run news-crawler enrich --days 7 --llm local_ollama

# 失敗した記事を再試行
uv run news-crawler enrich --days 7 --retry-failed

# 記事タグと本文からトレンドキーワード候補を抽出
uv run news-crawler extract-keywords --days 30 --min-count 3 --top-n 30

# 確認後、候補を ai_keywords.yaml に追記
uv run news-crawler extract-keywords --days 30 --min-count 3 --append

# 日付別ダイジェスト生成
uv run news-crawler digest --days 7

# レポート生成 (重複除外)
uv run news-crawler report --exclude-duplicates

# 検索 (FTS5、英文・日本語対応)
uv run news-crawler search "キーワード"

# 統計確認 (AI処理ステータス含む)
uv run news-crawler stats
```

### 静的解析・テスト

```bash
# Python テスト実行
uv run pytest

# Python 静的解析 (Ruff)
uv run ruff check .

# Python 型チェック (Mypy)
uv run mypy src

# ドキュメント Lint (Markdown & textlint)
npm run lint
npm run lint:fix
```

---

## 設計上の重要な決定事項

1. **取得方式の優先順位**:
   - `RSS / API` を最優先（低負荷・高安定性）
   - RSS がない場合は `Scrapy`（静的 HTML）
   - JS レンダリングが不可欠な場合のみ `Playwright` を使用

2. **データ永続化 (SQLite + FTS5)**:
   - 埋め込み・pgvector は初期スコープ外とし、SQLite FTS5 による高速キーワード検索を採用。
   - `data/articles.db` は gitignore 対象。
   - AI抽出キーワードと既存タグは `articles.tags` にマージして保存する。

3. **キーワード抽出と辞書更新**:
   - `enrich` はLLMから記事ごとのキーワードを抽出し、既存タグを保持したまま保存する。
   - `extract-keywords` はタグと本文を統計的に集計し、`config/ai_keywords.yaml` の `stopwords_extraction` を適用する。
   - `--append` は候補語を追加するため、内容を確認してから実行する。

4. **公開リポジトリのセキュリティ & クリーン性**:
   - 秘密情報（API キー、Webhook URL 等）や取得データ本体は Git にコミットしない。
   - `config/sources.example.yaml` をテンプレートとして公開し、実運用設定は `config/sources.yaml` で管理。
   - Windows Node.js への依存を排除し、WSL2 ネイティブ環境で完結させる。

## Git運用のユーザー設定

作業内容が独立した機能追加・修正・ドキュメント変更などに該当し、ブランチを切るメリットがある場合は、作業開始前に推奨ブランチ名と理由を必ず案内する。デフォルトブランチへの直接変更が妥当な場合も、その判断を明示する。
