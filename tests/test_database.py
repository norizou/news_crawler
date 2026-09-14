"""Tests for SQLite + FTS5 database repository."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ai_scraper.database import Database
from ai_scraper.models import Article, CrawlResult, CrawlRun, FetchMethod, SourceConfig


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_articles.db"
    return Database(db_file)


@pytest.fixture
def sample_source() -> SourceConfig:
    return SourceConfig(
        key="test_source",
        name="Test Source",
        category="official",
        fetch_method=FetchMethod.RSS,
        base_url="https://example.com",
    )


def test_upsert_source(temp_db: Database, sample_source: SourceConfig):
    temp_db.upsert_source(sample_source)
    stats = temp_db.get_stats()
    assert stats["total_sources"] == 1


def test_upsert_article_new_and_update(temp_db: Database, sample_source: SourceConfig):
    temp_db.upsert_source(sample_source)

    art = Article(
        source_key="test_source",
        url="https://example.com/art1?utm_source=rss",
        normalized_url="https://example.com/art1",
        title="Initial Title",
        summary="Initial summary",
        content="Initial content",
        published_at=datetime.now(),
        category="official",
    )

    # 1. Insert new
    saved_art, is_new, is_updated = temp_db.upsert_article(art)
    assert is_new is True
    assert is_updated is False
    assert saved_art.id is not None
    assert saved_art.normalized_url == "https://example.com/art1"

    # 2. Duplicate unchanged
    _, is_new2, is_updated2 = temp_db.upsert_article(art)
    assert is_new2 is False
    assert is_updated2 is False

    # 3. Update content
    art.title = "Updated Title"
    art.content = "New modified content"
    _, is_new3, is_updated3 = temp_db.upsert_article(art)
    assert is_new3 is False
    assert is_updated3 is True


def test_get_recent_articles(temp_db: Database, sample_source: SourceConfig):
    temp_db.upsert_source(sample_source)

    now = datetime.now()
    art1 = Article(
        source_key="test_source",
        url="https://example.com/recent",
        normalized_url="https://example.com/recent",
        title="Recent Article",
        published_at=now - timedelta(days=1),
        category="official",
    )
    art2 = Article(
        source_key="test_source",
        url="https://example.com/old",
        normalized_url="https://example.com/old",
        title="Old Article",
        published_at=now - timedelta(days=30),
        category="official",
    )
    temp_db.upsert_article(art1)
    temp_db.upsert_article(art2)

    recent = temp_db.get_recent_articles(days=7)
    assert len(recent) == 1
    assert recent[0].title == "Recent Article"


def test_fts5_search_articles(temp_db: Database, sample_source: SourceConfig):
    temp_db.upsert_source(sample_source)

    art1 = Article(
        source_key="test_source",
        url="https://example.com/llm-research",
        normalized_url="https://example.com/llm-research",
        title="Breakthrough in Transformer Attention",
        summary="Novel self-attention mechanism scaling quadratic speedups.",
        content="Detailed research into multi-head attention optimizations.",
        category="official",
    )
    art2 = Article(
        source_key="test_source",
        url="https://example.com/robotics",
        normalized_url="https://example.com/robotics",
        title="Humanoid Robotics Navigation",
        summary="Vision-based path planning for bipedal robots.",
        content="Robotics navigation without GPS.",
        category="media",
    )
    temp_db.upsert_article(art1)
    temp_db.upsert_article(art2)

    # Search query
    res = temp_db.search_articles("Transformer")
    assert len(res) == 1
    assert res[0].article.title == "Breakthrough in Transformer Attention"

    res_rob = temp_db.search_articles("Robotics")
    assert len(res_rob) == 1
    assert res_rob[0].article.title == "Humanoid Robotics Navigation"


def test_crawl_run_recording(temp_db: Database, sample_source: SourceConfig):
    temp_db.upsert_source(sample_source)

    run = CrawlRun(
        started_at=datetime.now(),
        total_sources=1,
        status="running",
    )
    run_id = temp_db.record_crawl_run(run)
    assert run_id > 0

    run.finished_at = datetime.now()
    run.success_count = 1
    run.new_count = 5
    run.status = "success"
    run.results = [
        CrawlResult(
            source_key="test_source",
            status="success",
            article_count=5,
            new_count=5,
            duration_seconds=1.2,
        )
    ]
    temp_db.update_crawl_run(run)

    stats = temp_db.get_stats()
    assert stats["latest_run"] is not None
    assert stats["latest_run"]["status"] == "success"
