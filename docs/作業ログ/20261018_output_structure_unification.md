---
title: news_crawler 出力フォルダ構成統一・週次ダイジェスト正本一本化 作業ログ
date: 2026-10-18
author: Devin
branch: mac-local
---

# news_crawler 出力フォルダ構成統一・週次ダイジェスト正本一本化 作業ログ

## 1. 背景と課題

これまで競馬ニュース収集システム `news_crawler` の出力は以下の二本立てとなっており、Vault（`10_KEIBA/10_News_crawl/`）側での管理において複数の課題が生じていた。

1. **ファイル数の肥大化**:
   - `digest` コマンドが日毎（`YYYY-MM-DD.md`）に出力するため、1ヶ月で約30ファイル以上増加。
   - `report` コマンド（`weekly_report_YYYYMMDD.md`）も実行日ごとのファイル名のため、週末に複数回実行すると同一週に複数の週報が乱立。
2. **正本（Master）の曖昧さ**:
   - 記事別要約（`digest`）と動向レポート（`weekly_report`）が同じ `10_News_crawl/YYYY/` 直下に混在し、どちらがその週の確定正本かが一目でわかりにくかった。
3. **ワードクラウドの二重化**:
   - AI要約側（`wordcloud_ja`）と原文側（`wordcloud_original`）の2枚が生成され、描画コストおよびファイル数が倍増していた。
   - 競馬ニュースは日本語ソースが中心のため、原文側のみで十分かつ AI 要約前でも完全な単語頻度が得られる。

## 2. 改修方針（D案：Digestへのサマリー統合・正本一本化）

- **出力先の統一**: `10_News_crawl/<YYYY>/` 配下に各週1本の正本ファイル（`digest_YYYY-Www.md`）を出力。
- **正本の一本化**: 旧 `weekly_report` の「収集サマリー（カテゴリ別・ソース別記事数表）」を `digest` に統合。1本のファイルの中にフロントマター・ワードクラウド・サマリー表・AI要約一覧を集約。
- **ワードクラウドの原文一本化 & ベクターSVG化**: 原文のみ1枚（`svg/digest_YYYY-Www_wordcloud.svg`）をベクター SVG（`viewBox` レスポンシブ対応）として出力し、Markdown からリンク参照。
- **週単位上書きアップデート**: 指定期間を月曜 00:00 〜 日曜 23:59 の週境界に自動拡張して DB から取得し、週の途中に何度実行しても該当週のファイルを上書きアップデート（ファイルを増やさない）。

## 3. 実装詳細

### (1) `src/news_crawler/visualization.py`
- `VisualizationGenerator.generate_wordcloud()` にて、出力先拡張子が `.svg` の場合に `wc.to_svg(embed_font=False)` を呼び出す処理を追加。
- 素の出力 `<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">` を `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" style="max-width: 100%; height: auto;">` に変換して保存。Obsidian 上でのレスポンシブ縮小表示に対応。

### (2) `src/news_crawler/config.py` & `config/crawler.yaml`
- `ReportConfig.output_assets_name` の既定値および設定ファイルの値を `"assets"` から `"svg"` に変更。

### (3) `src/news_crawler/reporting.py`
- AI要約側のワードクラウド生成を廃止し、原文（`title + summary + content`）から 1 枚のワードクラウド（`svg/{stem}_wordcloud.svg`）を生成して Markdown セクションを返す共通ヘルパー `render_wordcloud_section()` を実装。
- `generate_markdown_report()` 内でこの共通ヘルパーを呼び出す構成に変更。

### (4) `src/news_crawler/digest.py`
- **月曜始まり週ヘルパー**: `week_monday(d)`, `week_id(d)`, `group_by_week(articles)` を実装。
- **`render_week()`**:
  - Front matter: `title: 競馬ニュース YYYY-Www (YYYY-MM-DD ~ YYYY-MM-DD)`, `week`, `week_start`, `week_end`, `total_articles`, `ai_summarized`, `tags: [keiba, news, weekly]`, `llm_models`
  - `## 📈 単語頻度分析`: `render_wordcloud_section()` で生成された `svg/digest_YYYY-Www_wordcloud.svg` へのリンク
  - `## 📊 収集サマリー`: カテゴリ別・ソース別の記事数テーブル（旧 weekly_report より統合）
  - 週内詳細: 日付降順（`## YYYY-MM-DD（曜）（N 件）`）→ ソース別（`### ソース名（件数）`）→ 時刻降順（`- **HH:MM** [タイトル](URL)` + `  - 要約`）
- **`write_digests()`**: `<root>/<iso_year>/digest_<iso_year>-W<iso_week:02d>.md` に出力。範囲外週は断片上書き防止のためスキップ。

### (5) `src/news_crawler/cli.py`
- `report` コマンド: デフォルト出力先を `<report_dir>/<YYYY>/weekly_report_YYYYMMDD.md` に変更。
- `digest` コマンド:
  - `--visualize/--no-visualize` オプションを追加。
  - 指定期間 `[start_at, end_at]` を月曜 00:00:00 〜 翌月曜 00:00:00（日曜 23:59:59）の完全な週境界へ自動拡張して DB から取得。
  - 出力メッセージを「Wrote N weekly digest(s)」に更新。

## 4. データ移行・整理作業

1. **要約サルベージ**:
   - 再クロールで本文ハッシュが変わり DB 上で `ai_status='pending'` にリセットされていた `keibalab` 6記事（2026-10-16・10-17、id 1699, 1700, 1701, 1806, 1807, 1808）の AI 要約を既存日別 Markdown から抽出し、DB（`data/articles.db`）へ復元。
2. **週次ダイジェスト再生成**:
   - `uv run news-crawler digest --start-date 2026-09-12 --end-date 2026-10-18 --visualize` を実行。
   - 2026-09-07 〜 2026-10-18 の全記事を網羅する 6 週間分（`digest_2026-W37.md` 〜 `digest_2026-W42.md`）と対応するワードクラウド SVG（6枚）を生成。
3. **不要ファイルの退避（Vault 規則遵守）**:
   - 旧日別ダイジェスト37本（`2026-09-12.md` 〜 `2026-10-18.md`）→ `10_KEIBA/99_Trash/daily_digests_2026/`
   - 旧 `weekly_report_*.md`（10本）および対応 SVG（10枚）→ `10_KEIBA/99_Trash/weekly_reports_legacy/`
   - 旧日本語ワードクラウド `*_wordcloud_ja.svg`（10枚）→ `10_KEIBA/99_Trash/weekly_report_wordcloud_ja_svg/`

## 5. 移行後のフォルダ構成（`10_News_crawl/2026/`）

```text
10_News_crawl/2026/
├── digest_2026-W37.md       ← 【正本】2026-09-07 〜 2026-09-13
├── digest_2026-W38.md       ← 【正本】2026-09-14 〜 2026-09-20
├── digest_2026-W39.md       ← 【正本】2026-09-21 〜 2026-09-27
├── digest_2026-W40.md       ← 【正本】2026-09-28 〜 2026-10-04
├── digest_2026-W41.md       ← 【正本】2026-10-05 〜 2026-10-11
├── digest_2026-W42.md       ← 【正本】2026-10-12 〜 2026-10-18
└── svg/
    ├── digest_2026-W37_wordcloud.svg
    ├── digest_2026-W38_wordcloud.svg
    ├── digest_2026-W39_wordcloud.svg
    ├── digest_2026-W40_wordcloud.svg
    ├── digest_2026-W41_wordcloud.svg
    └── digest_2026-W42_wordcloud.svg
```

## 6. テスト・検証結果

- **Pytest**: `UV_PROJECT_ENVIRONMENT=~/.virtualenvs/news_crawler uv run pytest` → **全 158 テスト合格**
- **Ruff**: `uv run ruff check .` → **All checks passed!**
- **Mypy**: `uv run mypy src` → **Success: no issues found in 22 source files**
- **上書き検証**: `uv run news-crawler digest --days 1 --visualize` を実行し、ファイルが増えず `digest_2026-W42.md` が欠損なく上書き更新されることを確認。
- **Obsidian リンク整合性**: `obsidian vault="10_KEIBA" unresolved` で未解決リンクの増分ゼロを確認。
