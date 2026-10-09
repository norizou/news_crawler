# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-09

### Added

- **Article keyword extraction workflow** (c233bd8)
  - LLM-based keyword extraction during `enrich` process
  - New `extract-keywords` CLI command to discover trending keywords from articles
  - AI keywords are merged with existing RSS tags in `articles.tags`
  - `stopwords_extraction` section in `config/ai_keywords.yaml` for keyword extraction stopwords
  - `--append` option to add new keywords to `ai_keywords.yaml` while preserving comments
  - Case-insensitive keyword comparison and deduplication

- **Daily digest generator** (cd264bc)
  - New `digest` CLI command to generate date-specific digest files
  - `--only-summarized` option to include only AI-summarized articles
  - Custom output directory support with `-o` option

- **LLM endpoint fallback and Bearer auth** (32ce36e)
  - Automatic endpoint fallback based on `ai.endpoint_order` configuration
  - Bearer authentication support via `api_key_env` configuration
  - Per-endpoint `max_tokens` configuration to control token consumption

- **Report configuration enhancements** (87c19a3)
  - CLI options: `--exclude-source`, `--exclude-duplicates`, `--sources-file`
  - Predefined period options: `--period week/month/quarter`
  - Japanese morphological analysis priority for domestic sources
  - Improved font detection and fallback

- **Cross-source duplicate detection** (ec4ee8f)
  - New `dedupe` CLI command to detect cross-source duplicates
  - `--apply` option to save duplicate detection results to database
  - Canonical article tracking with representative article ID

### Changed

- **HTML encoding detection and date normalization** (96ea732)
  - Improved HTML encoding detection for better content extraction
  - Enhanced date normalization for consistent date handling

- **Project rename** (253a4c4)
  - Renamed project from `ai_scraper` to `news_crawler`

### Fixed

- **Test environment stabilization** (0ecd075)
  - Stabilized test environment for ported features
  - Updated documentation for ported features

## [0.1.0] - 2026-09-14

### Added

- Initial implementation of AI scraper with SQLite FTS5 and multi-source adapters
- AI enrichment with AIA Proxy integration
- Hugging Face API adapter for Chinese official sources
- SQLite + FTS5 for full-text search (English and Japanese)
- Multi-source adapters: RSS, HTML, Playwright, GitHub Releases, Hugging Face
- Markdown report generation with word cloud visualization
- CLI commands: `crawl`, `enrich`, `search`, `stats`, `report`
