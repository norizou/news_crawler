"""SQLite + FTS5 database repository."""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ai_scraper.models import Article, CrawlRun, SearchResult, SourceConfig
from ai_scraper.normalizer import compute_content_hash, normalize_url


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
        """Initialize relational tables and FTS5 virtual table."""
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

                -- FTS5 Full-Text Search Virtual Table
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
                # New insert
                cur = conn.execute(
                    """
                    INSERT INTO articles (
                        source_key, url, normalized_url, title, summary, content,
                        published_at, fetched_at, content_hash, category, author, tags
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                article.id = cur.lastrowid
                article.normalized_url = norm_url
                article.content_hash = content_hash
                return article, True, False

            existing_id = existing["id"]
            existing_hash = existing["content_hash"]

            if existing_hash != content_hash:
                # Content changed, update
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
                        updated_at = CURRENT_TIMESTAMP
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
                        existing_id,
                    ),
                )
                article.id = existing_id
                article.normalized_url = norm_url
                article.content_hash = content_hash
                return article, False, True

            # Unchanged
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
        """Full-text search across title, summary, and content using FTS5."""
        if not query_str.strip():
            return []

        # Sanitize FTS5 query token
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

            return {
                "total_sources": total_sources,
                "total_articles": total_articles,
                "categories": {row["category"]: row["count"] for row in categories},
                "latest_run": dict(latest_run) if latest_run else None,
            }

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
        )
