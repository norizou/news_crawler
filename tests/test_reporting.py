"""Tests for Markdown report generation."""

from datetime import datetime, timedelta
from pathlib import Path

from ai_scraper.database import Database
from ai_scraper.models import Article, FetchMethod, SourceConfig
from ai_scraper.reporting import generate_markdown_report


def test_generate_markdown_report(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = Database(db_file)

    source = SourceConfig(
        key="openai",
        name="OpenAI News",
        category="official",
        fetch_method=FetchMethod.RSS,
        base_url="https://openai.com",
    )
    db.upsert_source(source)

    art = Article(
        source_key="openai",
        url="https://openai.com/news/test-post",
        normalized_url="https://openai.com/news/test-post",
        title="OpenAI Test Model Release",
        summary="A summary of the new release.",
        content="Full article content goes here.",
        published_at=datetime.now() - timedelta(days=1),
        category="official",
        author="OpenAI Team",
    )
    db.upsert_article(art)

    report_file = tmp_path / "report.md"
    md = generate_markdown_report(db, days=7, output_path=report_file)

    assert report_file.exists()
    assert "---" in md
    assert "title: AI Trend Weekly Report" in md
    assert "OpenAI Test Model Release" in md
    assert "https://openai.com/news/test-post" in md
    assert "OpenAI Team" in md
    assert "| **official** | 1 | 1 |" in md
