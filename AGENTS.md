# News Crawler (news_crawler) — AGENTS.md

ニュース最新動向を定期的に自動収集・翻訳・要約・レポート化するスクレイピングシステム。

- **リポジトリ**: `https://github.com/norizou/news_crawler`（作業ブランチ `mac-local`。競馬ニュース用の macOS 向け）
- **ローカルパス**: `~/Projects/Dev/news_crawler/`
- **出力先**: レポート・日付別ダイジェストは Obsidian Vault `10_KEIBA/news_crawl/`（`config/crawler.yaml` の `report.output_dir`。Vault 側の手順書 `Docs/SKILL/news_crawler.md` が正本）
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
| CI | GitLab CI (`.gitlab-ci.yml`)（GitHub 移行前の設定が残っている） |
| AI 要約 | OpenAI 互換 API（LM Studio の gemma-4 を既定、Gemini を任意）。`ai.endpoints` / `endpoint_order` で自動フォールバック |
| ライセンス | MIT License |

---

## 主な実行コマンド

### 開発・実行

```bash
cd ~/Projects/Dev/news_crawler

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

# LLM を1つに固定（既定は ai.endpoint_order の先頭から自動選択）
uv run news-crawler enrich --llm gemini   # 要 .env の GEMINI_API_KEY

# レポート生成（sources.yaml で enabled: false のソースは既定で自動除外。
# タイトル・タグは sources.yaml または --title, --tags で柔軟に指定可。
# 別のソース定義を使う場合は --sources-file / -f で切り替え可能）
uv run news-crawler report
uv run news-crawler report --title "競馬ニュース週報" --tags keiba,weekly
uv run news-crawler report --sources-file config/sources.ai.yaml --title "AI Trend Report"

# クロスソース重複記事の検出（既定はdry-run。--applyでDB反映）
uv run news-crawler dedupe --period month --apply

# 日付別ダイジェスト（AI要約付き）→ <report.output_dir>/<YYYY>/<YYYY-MM-DD>.md（冪等）
uv run news-crawler digest --start-date 2026-09-18 --end-date 2026-09-18
uv run news-crawler digest --days 7 --only-summarized

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

4. **AI 要約（`enrich`）の LLM 選択**:
   - `ai.endpoint_order`（既定: `lmstudio_12b` → `lmstudio_local`）を先頭から試し、`/models` に応答し対象モデルがロード済みの最初のエンドポイントを使う。`--llm NAME` はその1つだけを使う。
   - `gemini` は外部送信・従量課金のため既定の順序に入れない。キーは `.env` の `GEMINI_API_KEY`（Git に入れない）。
   - **思考モデルは `max_tokens` を大きくする**（gemma-4 は思考トークンも消費する。500 だと `content` が空になり全件 `Invalid AI response format` で失敗）。
   - 記録される `ai_model` は実際に使ったモデル。
   - LM Studio が一時的に応答しないと `400 Bad Request` で失敗することがある。`--retry-failed` で再試行する。

5. **日付別ダイジェスト（`digest`）は日単位で丸ごと書く冪等な出力**:
   - 範囲の外の日に振り分けられた記事は書き出さない（既存の完全版ファイルを断片で上書きしないため）。試すときは `-o /tmp/...` で別の場所に出す。
   - **既知の不具合**: 公開日が未来になった記事（例: id 311・競馬ラボ・2028-08-09）はどの日にも入らない。原因未調査。

---

## テスト時の注意

- `uv run pytest` は環境の `FORCE_COLOR`（一部の CI・エージェントのシェルが設定）に依存しないよう、`tests/conftest.py` の autouse フィクスチャで `cli.console` / `coordinator.console` を色なしに差し替えている。これが無いと出力に ANSI コードが混ざり、`"Total New: 2"` のような文字列比較が失敗する。**新しいモジュールに module-level の `Console()` を足したら、同フィクスチャにも追加する。**
- `enrich` / `digest` を実データで試すときは、`data/articles.db` と Vault の `news_crawl/` を書き換える。ダイジェストは `-o` で別の場所へ出すこと。
