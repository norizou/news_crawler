# AI Scraper (ai_scraper) — AGENTS.md

AI最新トレンド・技術動向を定期的に自動収集・要約・レポート化するスクレイピングシステム。

- **リポジトリ**: `gitlab.dell.com/asain/ai_scraper` (または GitLab 公開リポジトリ)
- **ローカルパス**: `~/dev/ai_scraper/`
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
cd ~/dev/ai_scraper

# クロール実行 (全ソース)
uv run ai-scraper crawl

# 特定ソースのみクロール
uv run ai-scraper crawl --source openai

# dry-run
uv run ai-scraper crawl --dry-run

# レポート生成
uv run ai-scraper report

# 検索 (FTS5)
uv run ai-scraper search "キーワード"
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

3. **公開リポジトリのセキュリティ & クリーン性**:
   - 秘密情報（API キー、Webhook URL 等）や取得データ本体は Git にコミットしない。
   - `config/sources.example.yaml` をテンプレートとして公開し、実運用設定は `config/sources.yaml` で管理。
   - Windows Node.js への依存を排除し、WSL2 ネイティブ環境で完結させる。
