"""Tests for Markdown report generation."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from news_crawler.config import ReportConfig
from news_crawler.database import Database
from news_crawler.models import Article, FetchMethod, SourceConfig
from news_crawler.reporting import generate_markdown_report


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
    assert "title: AI Trend Report" in md
    assert "OpenAI Test Model Release" in md
    assert "https://openai.com/news/test-post" in md
    assert "OpenAI Team" in md
    assert "| **official** | 1 | 1 |" in md


def test_resolve_report_period():
    from news_crawler.reporting import resolve_report_period
    now = datetime(2026, 9, 14, 12, 0, 0)

    # Days
    start, end, mode = resolve_report_period(days=7, base_now=now)
    assert mode == "days"
    assert start == now - timedelta(days=7)
    assert end == now

    # Date range
    start, end, mode = resolve_report_period(start_date="2026-09-01", end_date="2026-09-05", base_now=now)
    assert mode == "date_range"
    assert start == datetime(2026, 9, 1)
    assert end == datetime(2026, 9, 6) # Exclusive end

    # Period
    start, end, mode = resolve_report_period(period="month", base_now=now)
    assert mode == "month"
    assert start == now - timedelta(days=30)
    assert end == now

    # Conflicts
    with pytest.raises(ValueError):
        resolve_report_period(days=7, period="week")


def test_generate_markdown_report_with_visualization(tmp_path):
    db_file = tmp_path / "test.db"
    db = Database(db_file)

    source = SourceConfig(
        key="test",
        name="Test",
        category="general",
        fetch_method=FetchMethod.RSS,
        base_url="https://example.com",
    )
    db.upsert_source(source)

    # Create a test keywords file with relevant terms
    keywords_file = tmp_path / "test_keywords.yaml"
    keywords_file.write_text("""
ai_keywords:
  - gpt-4
  - language
  - model
  - ai
  - transformer
  - neural
  - network
  - 言語
  - モデル
  - 進化
stopwords_general:
  - new
  - large
  - full
  - about
""", encoding="utf-8")

    art = Article(
        source_key="test",
        url="https://example.com/1",
        normalized_url="https://example.com/1",
        title="GPT-4 and Large Language Models",
        summary="AI models are advancing rapidly.",
        content="Full content about transformers and neural networks.",
        published_at=datetime.now() - timedelta(hours=1),
        category="general",
    )
    saved_art, _, _ = db.upsert_article(art)

    db.save_ai_result(
        article_id=saved_art.id,
        title_ja="GPT-4と大規模言語モデル",
        summary_ja="AIモデルは急速に進化しています。",
        model="test",
        prompt_version="1",
        input_hash="hash"
    )

    report_file = tmp_path / "report.md"
    cfg = ReportConfig(visualize=True, ai_keywords_path=str(keywords_file))

    md = generate_markdown_report(
        db,
        days=1,
        output_path=report_file,
        report_config=cfg
    )

    assert "## 📈 単語頻度分析" in md
    assert "### 日本語ワードクラウド" in md
    assert "![日本語ワードクラウド](report_assets/wordcloud_ja.png)" in md
    assert "### 原文ワードクラウド" in md

    # Check assets directory
    assets_dir = tmp_path / "report_assets"
    assert assets_dir.exists()
    assert (assets_dir / "wordcloud_ja.png").exists()
    assert (assets_dir / "wordcloud_original.png").exists()

    # Check Frontmatter
    import yaml
    parts = md.split("---")
    fm = yaml.safe_load(parts[1])
    assert fm["visualization"] is True
    assert fm["period_mode"] == "days"


def test_generate_markdown_report_with_japanese_content(tmp_path: Path):
    """Test report generation with AI-generated Japanese content."""
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
    saved_art, _, _ = db.upsert_article(art)

    # Add Japanese content
    db.save_ai_result(
        article_id=saved_art.id,
        title_ja="OpenAIテストモデルリリース",
        summary_ja="新しいリリースの要約。",
        model="test-model",
        prompt_version="1",
        input_hash="hash",
    )

    report_file = tmp_path / "report.md"
    md = generate_markdown_report(db, days=7, output_path=report_file)

    assert report_file.exists()
    assert "OpenAIテストモデルリリース" in md  # Japanese title should be used
    assert "新しいリリースの要約。" in md  # Japanese summary should be used


def test_generate_markdown_report_fallback_to_original(tmp_path: Path):
    """Test report generation falls back to original when AI not processed."""
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
    db.upsert_article(art)  # No AI processing

    report_file = tmp_path / "report.md"
    md = generate_markdown_report(db, days=7, output_path=report_file)

    assert report_file.exists()
    assert "OpenAI Test Model Release" in md  # Original title should be used
    assert "A summary of the new release." in md  # Original summary should be used


def test_generate_markdown_report_failed_ai_fallback(tmp_path: Path):
    """Test report generation falls back to original when AI processing failed."""
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
    saved_art, _, _ = db.upsert_article(art)

    # Mark as failed
    db.save_ai_failure(article_id=saved_art.id, error_message="API timeout")

    report_file = tmp_path / "report.md"
    md = generate_markdown_report(db, days=7, output_path=report_file)

    assert report_file.exists()
    assert "OpenAI Test Model Release" in md  # Original title should be used
    assert "A summary of the new release." in md  # Original summary should be used
