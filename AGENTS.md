# News Crawler (news_crawler) — AGENTS.md

ニュース最新動向を定期的に自動収集・翻訳・要約・レポート化するスクレイピングシステム。

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
uv run news-crawler crawl --source netkeiba

# dry-run
uv run news-crawler crawl --dry-run

# AI翻訳・要約 (config/crawler.yamlでai.enabled: true)
uv run news-crawler enrich --days 7 --limit 50

# 失敗した記事を再試行
uv run news-crawler enrich --days 7 --retry-failed

# レポート生成（sources.yaml で enabled: false のソースは既定で自動除外。
# タイトル・タグは sources.yaml または --title, --tags で柔軟に指定可。
# 別のソース定義を使う場合は --sources-file / -f で切り替え可能）
uv run news-crawler report
uv run news-crawler report --title "競馬ニュース週報" --tags keiba,weekly
uv run news-crawler report --sources-file config/sources.ai.yaml --title "AI Trend Report"

# クロスソース重複記事の検出（既定はdry-run。--applyでDB反映）
uv run news-crawler dedupe --period month --apply

# 検索 (FTS5、英文・日本語対応)
uv run news-crawler search "キーワード"

# 統計確認 (AI処理ステータス含む)
uv run news-crawler stats

# レースコメント出力 (TARGET frontier JV 向け一括インポート用 CSV、FAQ 612 準拠)
uv run news-crawler export-comments
uv run news-crawler export-comments --race ながつき
uv run news-crawler export-comments --date 2026-09-19 --venue 中山
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
