"""Shared fixtures for AI Scraper tests."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console

from news_crawler import cli, coordinator
from news_crawler.config import AIConfig
from news_crawler.database import Database
from news_crawler.models import Article, FetchMethod, SourceConfig


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make CLI output plain text regardless of the runner's environment.

    rich builds the module-level ``console`` at import time and honours FORCE_COLOR
    (set by some CI runners and agent shells), which puts ANSI codes inside the text
    the tests assert on (e.g. ``Total New: \x1b[1;32m2``).
    """
    plain = Console(force_terminal=False, no_color=True, color_system=None)
    monkeypatch.setattr(cli, "console", plain)
    monkeypatch.setattr(coordinator, "console", plain)


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    """Create a temporary database for testing."""
    db_file = tmp_path / "test_articles.db"
    return Database(db_file)


@pytest.fixture
def sample_source() -> SourceConfig:
    """Create a sample source configuration."""
    return SourceConfig(
        key="test_source",
        name="Test Source",
        category="official",
        fetch_method=FetchMethod.RSS,
        base_url="https://example.com",
    )


@pytest.fixture
def english_article(sample_source: SourceConfig) -> Article:
    """Create a recent English article for testing."""
    return Article(
        source_key="test_source",
        url="https://example.com/english-article",
        normalized_url="https://example.com/english-article",
        title="New AI Model Breakthrough",
        summary="A summary of the new AI model breakthrough.",
        content="Full content of the AI model breakthrough article with detailed information.",
        published_at=datetime.now() - timedelta(days=1),
        category="official",
        author="AI Research Team",
    )


@pytest.fixture
def japanese_article(sample_source: SourceConfig) -> Article:
    """Create a recent Japanese article for testing."""
    return Article(
        source_key="test_source",
        url="https://example.com/japanese-article",
        normalized_url="https://example.com/japanese-article",
        title="新しいAIモデルのブレイクスルー",
        summary="新しいAIモデルのブレイクスルーの要約。",
        content="AIモデルのブレイクスルーに関する詳細な情報を含む記事の全文。",
        published_at=datetime.now() - timedelta(days=2),
        category="official",
        author="AI研究チーム",
    )


@pytest.fixture
def old_article(sample_source: SourceConfig) -> Article:
    """Create an old article outside the recent window for testing."""
    return Article(
        source_key="test_source",
        url="https://example.com/old-article",
        normalized_url="https://example.com/old-article",
        title="Old AI Article",
        summary="Summary of an old article.",
        content="Content of an old article.",
        published_at=datetime.now() - timedelta(days=30),
        category="official",
    )


@pytest.fixture
def ai_config() -> AIConfig:
    """Create AI configuration for testing with minimal delays."""
    return AIConfig(
        enabled=True,
        proxy_url="http://localhost:11434/v1",
        model="test-model",
        request_interval=0.0,  # No delay in tests
        timeout_seconds=30,
        max_retries=2,
        max_input_chars=1000,
        max_articles_per_run=10,
        prompt_version="1",
    )


@pytest.fixture
def aia_success_response() -> dict:
    """Create a mock successful AIA API response."""
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"title_ja": "新しいAIモデルのブレイクスルー", '
                        '"summary_ja": "新しいAIモデルに関する重要な発見の要約。"}'
                    )
                }
            }
        ]
    }


@pytest.fixture
def fake_sleeper():
    """Create a fake sleeper that records sleep durations instead of actually sleeping."""
    class FakeSleeper:
        def __init__(self):
            self.calls = []

        async def __call__(self, seconds: float) -> None:
            self.calls.append(seconds)

    return FakeSleeper()
