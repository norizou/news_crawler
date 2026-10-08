"""SQLite + FTS5 database repository."""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from news_crawler.models import Article, CrawlRun, SearchResult, SourceConfig
from news_crawler.normalizer import (
    compute_content_hash,
    normalize_title_for_dedupe,
    normalize_url,
)


class Database:
    """SQLite Database wrapper with FTS5 support."""

    def __init__(self, db_path: str | Path = "data/articles.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        """Provide a configured SQLite connection with foreign keys and WAL mode."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Initialize relational tables and FTS5 virtual table with backward compatibility."""
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    fetch_method TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    last_crawled_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    normalized_url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    content TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP NOT NULL,
                    content_hash TEXT NOT NULL,
                    category TEXT NOT NULL,
                    author TEXT,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_key) REFERENCES sources (key) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_articles_source_key ON articles(source_key);
                CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
                CREATE INDEX IF NOT EXISTS idx_articles_fetched_at ON articles(fetched_at);
                CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);

                CREATE TABLE IF NOT EXISTS crawl_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TIMESTAMP NOT NULL,
                    finished_at TIMESTAMP,
                    total_sources INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    new_count INTEGER DEFAULT 0,
                    updated_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS crawl_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    source_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    article_count INTEGER DEFAULT 0,
                    new_count INTEGER DEFAULT 0,
                    updated_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    duration_seconds REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE
                );

                -- FTS5 Full-Text Search Virtual Table for original text
                CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(
                    title,
                    summary,
                    content,
                    category,
                    content='articles',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                -- Triggers to keep FTS index synchronized with articles table
                CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
                    INSERT INTO article_fts(rowid, title, summary, content, category)
                    VALUES (new.id, new.title, new.summary, new.content, new.category);
                END;

                CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
                    INSERT INTO article_fts(article_fts, rowid, title, summary, content, category)
                    VALUES ('delete', old.id, old.title, old.summary, old.content, old.category);
                END;

                CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
                    INSERT INTO article_fts(article_fts, rowid, title, summary, content, category)
                    VALUES ('delete', old.id, old.title, old.summary, old.content, old.category);
                    INSERT INTO article_fts(rowid, title, summary, content, category)
                    VALUES (new.id, new.title, new.summary, new.content, new.category);
                END;
                """
            )

            # Backward compatible schema migration for AI fields
            self._migrate_ai_columns(conn)
            self._migrate_japanese_fts(conn)
            self._migrate_dedupe_columns(conn)

    def _migrate_ai_columns(self, conn: sqlite3.Connection) -> None:
        """Add AI-related columns to articles table if they don't exist."""
        columns_to_add = [
            ("title_ja", "TEXT"),
            ("summary_ja", "TEXT"),
            ("ai_status", "TEXT DEFAULT 'pending'"),
            ("ai_input_hash", "TEXT"),
            ("ai_model", "TEXT"),
            ("ai_prompt_version", "TEXT DEFAULT '1'"),
            ("ai_processed_at", "TIMESTAMP"),
            ("ai_error", "TEXT"),
        ]

        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()
        }

        for col_name, col_type in columns_to_add:
            if col_name not in existing_columns:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}")

    def _migrate_dedupe_columns(self, conn: sqlite3.Connection) -> None:
        """Add cross-source duplicate-detection columns to articles table if missing."""
        columns_to_add = [
            ("normalized_title", "TEXT"),
            ("duplicate_of_id", "INTEGER"),
            ("duplicate_score", "REAL"),
        ]

        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()
        }

        for col_name, col_type in columns_to_add:
            if col_name not in existing_columns:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_normalized_title "
            "ON articles(normalized_title)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_duplicate_of_id "
            "ON articles(duplicate_of_id)"
        )

        # Backfill normalized_title for rows inserted before this column existed
        rows = conn.execute(
            "SELECT id, title FROM articles WHERE normalized_title IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE articles SET normalized_title = ? WHERE id = ?",
                (normalize_title_for_dedupe(row["title"]), row["id"]),
            )

    def _migrate_japanese_fts(self, conn: sqlite3.Connection) -> None:
        """Create Japanese FTS5 table with trigram tokenizer if it doesn't exist."""
        # Check if table exists
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='article_ja_fts'"
        ).fetchone()

        if not table_exists:
            conn.execute(
                """
                CREATE VIRTUAL TABLE article_ja_fts USING fts5(
                    title_ja,
                    summary_ja,
                    content='articles',
                    content_rowid='id',
                    tokenize='trigram'
                )
                """
            )
            # Rebuild index for existing articles
            conn.execute(
                """
                INSERT INTO article_ja_fts(rowid, title_ja, summary_ja)
                SELECT id, title_ja, summary_ja FROM articles
                """
            )
            # Create triggers for synchronization
            conn.execute(
                """
                CREATE TRIGGER articles_ja_ai AFTER INSERT ON articles BEGIN
                    INSERT INTO article_ja_fts(rowid, title_ja, summary_ja)
                    VALUES (new.id, new.title_ja, new.summary_ja);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER articles_ja_ad AFTER DELETE ON articles BEGIN
                    INSERT INTO article_ja_fts(article_ja_fts, rowid, title_ja, summary_ja)
                    VALUES ('delete', old.id, old.title_ja, old.summary_ja);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER articles_ja_au AFTER UPDATE ON articles BEGIN
                    INSERT INTO article_ja_fts(article_ja_fts, rowid, title_ja, summary_ja)
                    VALUES ('delete', old.id, old.title_ja, old.summary_ja);
                    INSERT INTO article_ja_fts(rowid, title_ja, summary_ja)
                    VALUES (new.id, new.title_ja, new.summary_ja);
                END
                """
            )

    def upsert_source(self, source: SourceConfig) -> None:
        """Insert or update a source record."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO sources (key, name, category, fetch_method, base_url, enabled)
                VALUES (:key, :name, :category, :fetch_method, :base_url, :enabled)
                ON CONFLICT(key) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    fetch_method = excluded.fetch_method,
                    base_url = excluded.base_url,
                    enabled = excluded.enabled
                """,
                {
                    "key": source.key,
                    "name": source.name,
                    "category": source.category,
                    "fetch_method": source.fetch_method.value,
                    "base_url": source.base_url,
                    "enabled": 1 if source.enabled else 0,
                },
            )

    def upsert_article(self, article: Article) -> tuple[Article, bool, bool]:
        """
        Insert or update an article.
        Returns: (saved_article, is_new, is_updated)
        """
        norm_url = normalize_url(article.url)
        content_hash = compute_content_hash(article.title, article.content)
        norm_title = normalize_title_for_dedupe(article.title)
        tags_json = json.dumps(article.tags, ensure_ascii=False)

        pub_iso = article.published_at.isoformat() if article.published_at else None
        fetch_iso = (article.fetched_at or datetime.now()).isoformat()

        with self.connection() as conn:
            # Check existing article
            cur = conn.execute(
                "SELECT id, content_hash FROM articles WHERE normalized_url = ?",
                (norm_url,),
            )
            existing = cur.fetchone()

            if existing is None:
                # New insert - AI fields default to pending
                cur = conn.execute(
                    """
                    INSERT INTO articles (
                        source_key, url, normalized_url, title, summary, content,
                        published_at, fetched_at, content_hash, category, author, tags,
                        ai_status, normalized_title
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        article.source_key,
                        article.url,
                        norm_url,
                        article.title,
                        article.summary,
                        article.content,
                        pub_iso,
                        fetch_iso,
                        content_hash,
                        article.category,
                        article.author,
                        tags_json,
                        norm_title,
                    ),
                )
                article.id = cur.lastrowid
                article.normalized_url = norm_url
                article.content_hash = content_hash
                article.ai_status = "pending"
                return article, True, False

            existing_id = existing["id"]
            existing_hash = existing["content_hash"]

            if existing_hash != content_hash:
                # Content changed, update and reset AI status to pending
                conn.execute(
                    """
                    UPDATE articles SET
                        title = ?,
                        summary = ?,
                        content = ?,
                        published_at = COALESCE(?, published_at),
                        fetched_at = ?,
                        content_hash = ?,
                        category = ?,
                        author = COALESCE(?, author),
                        tags = ?,
                        normalized_title = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        ai_status = 'pending',
                        title_ja = '',
                        summary_ja = '',
                        ai_input_hash = '',
                        ai_model = '',
                        ai_processed_at = NULL,
                        ai_error = NULL,
                        duplicate_of_id = NULL,
                        duplicate_score = NULL
                    WHERE id = ?
                    """,
                    (
                        article.title,
                        article.summary,
                        article.content,
                        pub_iso,
                        fetch_iso,
                        content_hash,
                        article.category,
                        article.author,
                        tags_json,
                        norm_title,
                        existing_id,
                    ),
                )
                article.id = existing_id
                article.normalized_url = norm_url
                article.content_hash = content_hash
                article.ai_status = "pending"
                return article, False, True

            # Unchanged - preserve AI fields
            article.id = existing_id
            article.normalized_url = norm_url
            article.content_hash = content_hash
            return article, False, False

    def get_recent_articles(
        self,
        days: int = 7,
        category: str | None = None,
        source_key: str | None = None,
        limit: int = 100,
    ) -> list[Article]:
        """Fetch articles published or fetched within the last N days."""
        cutoff_iso = (datetime.now() - timedelta(days=days)).isoformat()
        query = """
            SELECT * FROM articles
            WHERE (published_at >= ? OR (published_at IS NULL AND fetched_at >= ?))
        """
        params: list[Any] = [cutoff_iso, cutoff_iso]

        if category:
            query += " AND category = ?"
            params.append(category)
        if source_key:
            query += " AND source_key = ?"
            params.append(source_key)

        query += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_article(row) for row in rows]

    def search_articles(
        self, query_str: str, category: str | None = None, limit: int = 20
    ) -> list[SearchResult]:
        """Full-text search across title, summary, content, and Japanese fields using FTS5."""
        if not query_str.strip():
            return []

        # Search both original and Japanese FTS tables
        english_results = self._search_english(query_str, category, limit)
        japanese_results = self.search_articles_japanese(query_str, category, limit)

        # Merge results, avoiding duplicates by article ID
        seen_ids = set()
        merged_results = []

        for result in english_results:
            if result.article.id not in seen_ids:
                seen_ids.add(result.article.id)
                merged_results.append(result)

        for result in japanese_results:
            if result.article.id not in seen_ids:
                seen_ids.add(result.article.id)
                merged_results.append(result)

        # Sort by rank and limit
        merged_results.sort(key=lambda x: x.rank)
        return merged_results[:limit]

    def _search_english(
        self, query_str: str, category: str | None = None, limit: int = 20
    ) -> list[SearchResult]:
        """Search original text FTS index."""
        fts_query = " ".join(f'"{token}"' for token in query_str.strip().split() if token)

        sql = """
            SELECT
                a.*,
                snippet(article_fts, 1, '<b>', '</b>', '...', 20) as snippet,
                bm25(article_fts) as rank
            FROM article_fts
            JOIN articles a ON a.id = article_fts.rowid
            WHERE article_fts MATCH ?
        """
        params: list[Any] = [fts_query]

        if category:
            sql += " AND a.category = ?"
            params.append(category)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                art = self._row_to_article(row)
                snippet_text = row["snippet"] if "snippet" in row.keys() else ""
                rank_score = float(row["rank"]) if "rank" in row.keys() else 0.0
                results.append(SearchResult(article=art, snippet=snippet_text, rank=rank_score))
            return results

    def record_crawl_run(self, run: CrawlRun) -> int:
        """Create a new crawl run record."""
        started_iso = run.started_at.isoformat()
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO crawl_runs (
                    started_at, total_sources, success_count, failed_count,
                    new_count, updated_count, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_iso,
                    run.total_sources,
                    run.success_count,
                    run.failed_count,
                    run.new_count,
                    run.updated_count,
                    run.status,
                ),
            )
            last_id = cur.lastrowid
            run_id = int(last_id) if last_id is not None else 0
            run.id = run_id
            return run_id

    def update_crawl_run(self, run: CrawlRun) -> None:
        """Update finished crawl run record and insert source results."""
        if run.id is None:
            return

        finished_iso = (run.finished_at or datetime.now()).isoformat()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE crawl_runs SET
                    finished_at = ?,
                    success_count = ?,
                    failed_count = ?,
                    new_count = ?,
                    updated_count = ?,
                    status = ?
                WHERE id = ?
                """,
                (
                    finished_iso,
                    run.success_count,
                    run.failed_count,
                    run.new_count,
                    run.updated_count,
                    run.status,
                    run.id,
                ),
            )

            for result in run.results:
                conn.execute(
                    """
                    INSERT INTO crawl_results (
                        run_id, source_key, status, article_count,
                        new_count, updated_count, error_message, duration_seconds
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        result.source_key,
                        result.status,
                        result.article_count,
                        result.new_count,
                        result.updated_count,
                        result.error_message,
                        result.duration_seconds,
                    ),
                )
                if result.status == "success":
                    conn.execute(
                        "UPDATE sources SET last_crawled_at = CURRENT_TIMESTAMP WHERE key = ?",
                        (result.source_key,),
                    )

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics from the database."""
        with self.connection() as conn:
            total_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as count FROM articles GROUP BY category"
            ).fetchall()
            latest_run = conn.execute(
                "SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()

            # AI statistics
            ai_stats = conn.execute(
                """
                SELECT
                    ai_status,
                    COUNT(*) as count
                FROM articles
                GROUP BY ai_status
                """
            ).fetchall()
            ai_status_counts = {row["ai_status"]: row["count"] for row in ai_stats}

            return {
                "total_sources": total_sources,
                "total_articles": total_articles,
                "categories": {row["category"]: row["count"] for row in categories},
                "latest_run": dict(latest_run) if latest_run else None,
                "ai_status": ai_status_counts,
            }

    def get_pending_articles(
        self,
        days: int = 7,
        limit: int = 50,
        retry_failed: bool = False,
    ) -> list[Article]:
        """Get articles pending AI enrichment within the last N days."""
        cutoff_iso = (datetime.now() - timedelta(days=days)).isoformat()

        query = """
            SELECT * FROM articles
            WHERE (published_at >= ? OR (published_at IS NULL AND fetched_at >= ?))
            AND ai_status = 'pending'
            AND duplicate_of_id IS NULL
        """
        params: list[Any] = [cutoff_iso, cutoff_iso]

        if retry_failed:
            query = query.replace("ai_status = 'pending'", "ai_status IN ('pending', 'failed')")

        query += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_article(row) for row in rows]

    def save_ai_result(
        self,
        article_id: int,
        title_ja: str,
        summary_ja: str,
        model: str,
        prompt_version: str,
        input_hash: str,
    ) -> None:
        """Save AI enrichment result for an article."""
        processed_iso = datetime.now().isoformat()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE articles SET
                    title_ja = ?,
                    summary_ja = ?,
                    ai_status = 'completed',
                    ai_input_hash = ?,
                    ai_model = ?,
                    ai_prompt_version = ?,
                    ai_processed_at = ?,
                    ai_error = NULL
                WHERE id = ?
                """,
                (
                    title_ja,
                    summary_ja,
                    input_hash,
                    model,
                    prompt_version,
                    processed_iso,
                    article_id,
                ),
            )

    def save_ai_failure(self, article_id: int, error_message: str) -> None:
        """Save AI enrichment failure for an article."""
        # Sanitize error message to avoid exposing secrets
        sanitized_error = error_message[:500] if error_message else "Unknown error"
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE articles SET
                    ai_status = 'failed',
                    ai_error = ?
                WHERE id = ?
                """,
                (sanitized_error, article_id),
            )

    def search_articles_japanese(
        self, query_str: str, category: str | None = None, limit: int = 20
    ) -> list[SearchResult]:
        """Search Japanese FTS index for articles."""
        if not query_str.strip():
            return []

        # Try trigram FTS first
        fts_query = " ".join(f'"{token}"' for token in query_str.strip().split() if token)

        sql = """
            SELECT
                a.*,
                snippet(article_ja_fts, 1, '<b>', '</b>', '...', 20) as snippet,
                bm25(article_ja_fts) as rank
            FROM article_ja_fts
            JOIN articles a ON a.id = article_ja_fts.rowid
            WHERE article_ja_fts MATCH ?
        """
        params: list[Any] = [fts_query]

        if category:
            sql += " AND a.category = ?"
            params.append(category)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            try:
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
                results = []
                for row in rows:
                    art = self._row_to_article(row)
                    snippet_text = row["snippet"] if "snippet" in row.keys() else ""
                    rank_score = float(row["rank"]) if "rank" in row.keys() else 0.0
                    results.append(SearchResult(article=art, snippet=snippet_text, rank=rank_score))
                return results
            except sqlite3.OperationalError:
                # Fallback to LIKE if trigram query fails
                return self._search_japanese_fallback(query_str, category, limit)

    def _search_japanese_fallback(
        self, query_str: str, category: str | None = None, limit: int = 20
    ) -> list[SearchResult]:
        """Fallback LIKE search for very short Japanese terms."""
        query = """
            SELECT
                a.*,
                '' as snippet,
                0.0 as rank
            FROM articles a
            WHERE (title_ja LIKE ? OR summary_ja LIKE ?)
        """
        params: list[Any] = [f"%{query_str}%", f"%{query_str}%"]

        if category:
            query += " AND a.category = ?"
            params.append(category)

        query += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                art = self._row_to_article(row)
                results.append(SearchResult(article=art, snippet="", rank=0.0))
            return results

    def _row_to_article(self, row: sqlite3.Row) -> Article:
        """Convert a database row into an Article model."""
        tags_raw = row["tags"]
        tags_list = json.loads(tags_raw) if tags_raw else []

        pub_at = row["published_at"]
        if isinstance(pub_at, str):
            pub_at = datetime.fromisoformat(pub_at)

        fetch_at = row["fetched_at"]
        if isinstance(fetch_at, str):
            fetch_at = datetime.fromisoformat(fetch_at)

        keys = row.keys()
        ai_processed_at = row["ai_processed_at"] if "ai_processed_at" in keys else None
        if isinstance(ai_processed_at, str):
            ai_processed_at = datetime.fromisoformat(ai_processed_at)

        return Article(
            id=row["id"],
            source_key=row["source_key"],
            url=row["url"],
            normalized_url=row["normalized_url"],
            title=row["title"],
            summary=row["summary"] or "",
            content=row["content"] or "",
            published_at=pub_at,
            fetched_at=fetch_at,
            content_hash=row["content_hash"],
            category=row["category"],
            author=row["author"],
            tags=tags_list,
            title_ja=(
                row["title_ja"]
                if "title_ja" in keys and row["title_ja"] is not None
                else ""
            ),
            summary_ja=(
                row["summary_ja"]
                if "summary_ja" in keys and row["summary_ja"] is not None
                else ""
            ),
            ai_status=(
                row["ai_status"]
                if "ai_status" in keys and row["ai_status"] is not None
                else "pending"
            ),
            ai_input_hash=(
                row["ai_input_hash"]
                if "ai_input_hash" in keys and row["ai_input_hash"] is not None
                else ""
            ),
            ai_model=(
                row["ai_model"]
                if "ai_model" in keys and row["ai_model"] is not None
                else ""
            ),
            ai_prompt_version=(
                row["ai_prompt_version"]
                if "ai_prompt_version" in keys and row["ai_prompt_version"] is not None
                else "1"
            ),
            ai_processed_at=ai_processed_at,
            ai_error=row["ai_error"] if "ai_error" in keys else None,
            duplicate_of_id=(
                row["duplicate_of_id"] if "duplicate_of_id" in keys else None
            ),
            duplicate_score=(
                row["duplicate_score"] if "duplicate_score" in keys else None
            ),
        )

    def get_articles_in_range(
        self,
        start_at: datetime,
        end_at: datetime,
        category: str | None = None,
        exclude_source_keys: list[str] | None = None,
        exclude_duplicates: bool = False,
    ) -> list[Article]:
        """Fetch articles published or fetched within the specified date range."""
        start_iso = start_at.isoformat()
        end_iso = end_at.isoformat()

        query = """
            SELECT * FROM articles
            WHERE (
                (published_at >= ? AND published_at < ?)
                OR (published_at IS NULL AND fetched_at >= ? AND fetched_at < ?)
            )
        """
        params: list[Any] = [start_iso, end_iso, start_iso, end_iso]

        if category:
            query += " AND category = ?"
            params.append(category)

        if exclude_source_keys:
            placeholders = ", ".join("?" for _ in exclude_source_keys)
            query += f" AND source_key NOT IN ({placeholders})"
            params.extend(exclude_source_keys)

        if exclude_duplicates:
            query += " AND duplicate_of_id IS NULL"

        query += " ORDER BY COALESCE(published_at, fetched_at) DESC"

        with self.connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_article(row) for row in rows]

    def reset_duplicates_in_range(self, start_at: datetime, end_at: datetime) -> int:
        """Clear duplicate_of_id/duplicate_score for articles in range.

        Used before recomputing duplicate clusters so that the detection stays
        idempotent (a stale grouping from a previous run never lingers).
        """
        start_iso = start_at.isoformat()
        end_iso = end_at.isoformat()
        query = """
            UPDATE articles SET duplicate_of_id = NULL, duplicate_score = NULL
            WHERE (
                (published_at >= ? AND published_at < ?)
                OR (published_at IS NULL AND fetched_at >= ? AND fetched_at < ?)
            )
        """
        with self.connection() as conn:
            cur = conn.execute(query, [start_iso, end_iso, start_iso, end_iso])
            return cur.rowcount

    def mark_duplicate(self, article_id: int, canonical_id: int, score: float) -> None:
        """Mark an article as a duplicate of another (canonical) article."""
        with self.connection() as conn:
            conn.execute(
                "UPDATE articles SET duplicate_of_id = ?, duplicate_score = ? WHERE id = ?",
                (canonical_id, score, article_id),
            )
