"""Tests for AI enrichment processor."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from news_crawler.ai_processor import AIProcessor
from news_crawler.config import AIConfig
from news_crawler.database import Database
from news_crawler.models import Article, FetchMethod, SourceConfig


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_call_api_success(
    ai_config: AIConfig, aia_success_response: dict
):
    """Test successful AI API call."""
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    processor = AIProcessor(ai_config, Database(":memory:"))
    result = await processor._call_ai_api("Test input")

    assert result == aia_success_response
    assert respx.calls.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_uses_configured_system_prompt(
    ai_config: AIConfig, aia_success_response: dict
):
    """Test that configured system prompt is passed to the AI API payload."""
    ai_config.system_prompt = "Custom test prompt instruction."
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    processor = AIProcessor(ai_config, Database(":memory:"))
    await processor._call_ai_api("Test input")

    payload = json.loads(route.calls.last.request.content.decode("utf-8"))
    system_messages = [m for m in payload["messages"] if m["role"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["content"] == "Custom test prompt instruction."


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_parse_response(ai_config: AIConfig):
    """Test parsing AI response."""
    response = {
        "choices": [
            {
                "message": {
                    "content": '{"title_ja": "テストタイトル", "summary_ja": "テスト要約"}'
                }
            }
        ]
    }

    processor = AIProcessor(ai_config, Database(":memory:"))
    result = processor._parse_ai_response(response)

    assert result.title_ja == "テストタイトル"
    assert result.summary_ja == "テスト要約"


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_parse_response_with_markdown(ai_config: AIConfig):
    """Test parsing AI response with markdown code fence."""
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        "```json\n"
                        '{"title_ja": "テストタイトル", "summary_ja": "テスト要約"}\n'
                        "```"
                    )
                }
            }
        ]
    }

    processor = AIProcessor(ai_config, Database(":memory:"))
    result = processor._parse_ai_response(response)

    assert result.title_ja == "テストタイトル"
    assert result.summary_ja == "テスト要約"


@pytest.mark.asyncio
async def test_ai_processor_parse_response_invalid_json(ai_config: AIConfig):
    """Test parsing invalid JSON response."""
    response = {
        "choices": [{"message": {"content": "invalid json"}}]
    }

    processor = AIProcessor(ai_config, Database(":memory:"))

    with pytest.raises(ValueError, match="Invalid AI response format"):
        processor._parse_ai_response(response)


@pytest.mark.asyncio
async def test_ai_processor_parse_response_missing_fields(ai_config: AIConfig):
    """Test parsing response with missing required fields."""
    response = {
        "choices": [{"message": {"content": '{"title_ja": "test"}'}}]
    }

    processor = AIProcessor(ai_config, Database(":memory:"))

    with pytest.raises(ValueError, match="Invalid AI response format"):
        processor._parse_ai_response(response)


@pytest.mark.asyncio
async def test_ai_processor_prepare_input_text(ai_config: AIConfig):
    """Test input text preparation with character limit."""
    article = Article(
        source_key="test",
        url="https://example.com",
        normalized_url="https://example.com",
        title="Test Title",
        summary="Test Summary",
        content="A" * 2000,  # Long content
        category="test",
    )

    processor = AIProcessor(ai_config, Database(":memory:"))
    input_text = processor._prepare_input_text(article)

    assert len(input_text) <= ai_config.max_input_chars
    assert "Title: Test Title" in input_text
    assert "Summary: Test Summary" in input_text
    assert "Content:" in input_text


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_retry_429(ai_config: AIConfig, fake_sleeper):
    """Test retry logic with 429 status and Retry-After header."""
    # First call returns 429 with Retry-After
    valid_content = '{"title_ja": "test", "summary_ja": "test"}'
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"choices": [{"message": {"content": valid_content}}]}),
        ]
    )

    processor = AIProcessor(ai_config, Database(":memory:"), sleeper=fake_sleeper)
    result = await processor._call_ai_api("test")

    assert result["choices"][0]["message"]["content"]
    assert len(fake_sleeper.calls) == 1
    assert fake_sleeper.calls[0] == 1.0  # Retry-After value
    assert respx.calls.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_retry_timeout(ai_config: AIConfig, fake_sleeper):
    """Test retry logic with timeout."""
    valid_content = '{"title_ja": "test", "summary_ja": "test"}'
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        side_effect=[
            httpx.TimeoutException("Timeout"),
            httpx.Response(200, json={"choices": [{"message": {"content": valid_content}}]}),
        ]
    )

    processor = AIProcessor(ai_config, Database(":memory:"), sleeper=fake_sleeper)
    result = await processor._call_ai_api("test")

    assert result["choices"][0]["message"]["content"]
    assert len(fake_sleeper.calls) == 1
    assert fake_sleeper.calls[0] == 1.0  # Exponential backoff
    assert respx.calls.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_max_retries_exceeded(ai_config: AIConfig, fake_sleeper):
    """Test that max retries are respected."""
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        side_effect=[httpx.TimeoutException("Timeout")] * 10
    )

    processor = AIProcessor(ai_config, Database(":memory:"), sleeper=fake_sleeper)

    with pytest.raises(Exception, match="Max retries exceeded"):
        await processor._call_ai_api("test")

    assert len(fake_sleeper.calls) == ai_config.max_retries
    assert respx.calls.call_count == ai_config.max_retries + 1


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_enrich_single_article(
    temp_db: Database,
    ai_config: AIConfig,
    english_article: Article,
    aia_success_response: dict,
    fake_sleeper,
):
    """Test enriching a single article."""
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    # Save article to DB
    temp_db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )
    saved_article, _, _ = temp_db.upsert_article(english_article)

    processor = AIProcessor(ai_config, temp_db, sleeper=fake_sleeper)
    await processor._enrich_single_article(saved_article)

    # Verify AI result was saved
    updated = temp_db.get_recent_articles(days=7, limit=1)[0]
    assert updated.ai_status == "completed"
    assert updated.title_ja == "新しいAIモデルのブレイクスルー"
    assert updated.summary_ja == "新しいAIモデルに関する重要な発見の要約。"
    assert updated.ai_model == ai_config.model
    assert updated.ai_prompt_version == ai_config.prompt_version


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_enrich_articles_service(
    temp_db: Database,
    ai_config: AIConfig,
    english_article: Article,
    aia_success_response: dict,
    fake_sleeper,
):
    """Test full enrichment service with multiple articles."""
    ai_config.request_interval = 0.1
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    # Save source and articles
    temp_db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )

    # Create multiple pending articles
    for i in range(3):
        article = Article(
            source_key="test_source",
            url=f"https://example.com/article-{i}",
            normalized_url=f"https://example.com/article-{i}",
            title=f"Article {i}",
            summary=f"Summary {i}",
            content=f"Content {i}",
            published_at=datetime.now() - timedelta(days=i),
            category="test",
        )
        temp_db.upsert_article(article)

    processor = AIProcessor(ai_config, temp_db, sleeper=fake_sleeper)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats["total"] == 3
    assert stats["success"] == 3
    assert stats["failed"] == 0
    assert len(fake_sleeper.calls) == 2  # Sleep between articles (3 articles = 2 sleeps)
    assert respx.calls.call_count == 3  # One API call per article


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_skip_completed_articles(
    temp_db: Database, ai_config: AIConfig, english_article: Article, aia_success_response: dict
):
    """Test that completed articles are skipped."""
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    # Save source and article
    temp_db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )
    saved_article, _, _ = temp_db.upsert_article(english_article)

    # Mark as completed
    temp_db.save_ai_result(
        article_id=saved_article.id,
        title_ja="既存の日本語タイトル",
        summary_ja="既存の日本語要約",
        model=ai_config.model,
        prompt_version=ai_config.prompt_version,
        input_hash="test_hash",
    )

    processor = AIProcessor(ai_config, temp_db)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats["total"] == 0
    assert stats["success"] == 0
    assert respx.calls.call_count == 0  # No API calls for completed articles


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_disabled(temp_db: Database, ai_config: AIConfig):
    """Test that enrichment is skipped when disabled."""
    ai_config.enabled = False
    processor = AIProcessor(ai_config, temp_db)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats["total"] == 0
    assert stats["success"] == 0
    assert stats["failed"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_request_interval(
    temp_db: Database,
    ai_config: AIConfig,
    english_article: Article,
    aia_success_response: dict,
    fake_sleeper,
):
    """Test that request interval is respected."""
    ai_config.request_interval = 0.5
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    # Save source and articles
    temp_db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )

    for i in range(2):
        article = Article(
            source_key="test_source",
            url=f"https://example.com/article-{i}",
            normalized_url=f"https://example.com/article-{i}",
            title=f"Article {i}",
            summary=f"Summary {i}",
            content=f"Content {i}",
            published_at=datetime.now() - timedelta(days=i),
            category="test",
        )
        temp_db.upsert_article(article)

    processor = AIProcessor(ai_config, temp_db, sleeper=fake_sleeper)
    await processor.enrich_articles(days=7, limit=10)

    assert len(fake_sleeper.calls) == 1  # One sleep between 2 articles
    assert fake_sleeper.calls[0] == 0.5  # Configured interval
