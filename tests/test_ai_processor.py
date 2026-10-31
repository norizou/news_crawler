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


def _save_pending_articles(db: Database, n: int) -> list[Article]:
    db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )
    saved = []
    for i in range(n):
        article, _, _ = db.upsert_article(
            Article(
                source_key="test_source",
                url=f"https://example.com/article-{i}",
                normalized_url=f"https://example.com/article-{i}",
                title=f"Article {i}",
                summary=f"Summary {i}",
                content=f"Content {i}",
                published_at=datetime.now() - timedelta(hours=i),
                category="test",
            )
        )
        saved.append(article)
    return saved


def _get(db: Database, article_id: int) -> Article:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    return db._row_to_article(row)


def _packed_response(entries: list[dict]) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"articles": entries})}}]}


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_packed_one_request_per_pack(
    temp_db: Database, ai_config: AIConfig, fake_sleeper
):
    """pack_size>1 sends several articles per request and saves each result."""
    ai_config.pack_size = 2
    ai_config.request_interval = 0.1
    saved = _save_pending_articles(temp_db, 3)

    def reply(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        user = payload["messages"][1]["content"]
        ids = [a.id for a in saved if f"### id={a.id}\n" in user]
        return httpx.Response(
            200,
            json=_packed_response(
                [{"id": i, "title_ja": f"題{i}", "summary_ja": f"要約{i}"} for i in ids]
            ),
        )

    route = respx.post("http://localhost:11434/v1/chat/completions").mock(side_effect=reply)

    processor = AIProcessor(ai_config, temp_db, sleeper=fake_sleeper)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats == {"skipped": 0, "success": 3, "failed": 0, "total": 3}
    assert route.call_count == 2  # 3 articles / pack of 2
    assert len(fake_sleeper.calls) == 1  # sleep between packs only

    first = json.loads(route.calls[0].request.content)
    assert first["max_tokens"] == ai_config.max_tokens * 2
    assert first["response_format"]["type"] == "json_schema"
    assert '"articles"' in first["messages"][0]["content"]

    for article in saved:
        updated = _get(temp_db, article.id)
        assert updated.ai_status == "completed"
        assert updated.title_ja == f"題{article.id}"
        assert updated.summary_ja == f"要約{article.id}"
        assert updated.ai_model == ai_config.model


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_packed_missing_and_invalid_entries_fail_individually(
    temp_db: Database, ai_config: AIConfig
):
    """Only articles missing from (or malformed in) the packed response are failed."""
    ai_config.pack_size = 10
    a, b, c = _save_pending_articles(temp_db, 3)
    content = (
        "```json\n"
        + json.dumps(
            {
                "articles": [
                    {"id": a.id, "title_ja": "題A", "summary_ja": "要約A"},
                    {"id": b.id, "title_ja": "題B"},  # summary_ja missing
                    {"id": 99999, "title_ja": "余分", "summary_ja": "余分"},
                ]
            }
        )
        + "\n```"
    )
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )

    processor = AIProcessor(ai_config, temp_db)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats == {"skipped": 0, "success": 1, "failed": 2, "total": 3}
    assert _get(temp_db, a.id).ai_status == "completed"
    assert _get(temp_db, b.id).ai_status == "failed"
    assert _get(temp_db, c.id).ai_status == "failed"
    assert "Missing" in _get(temp_db, c.id).ai_error


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_packed_invalid_json_fails_whole_pack(
    temp_db: Database, ai_config: AIConfig
):
    """A response that is not JSON at all fails every article in the pack."""
    ai_config.pack_size = 10
    saved = _save_pending_articles(temp_db, 2)
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "oops"}}]})
    )

    processor = AIProcessor(ai_config, temp_db)
    stats = await processor.enrich_articles(days=7, limit=10)

    assert stats == {"skipped": 0, "success": 0, "failed": 2, "total": 2}
    for article in saved:
        assert _get(temp_db, article.id).ai_status == "failed"


def test_ai_processor_parse_packed_response_accepts_bare_list(ai_config: AIConfig):
    processor = AIProcessor(ai_config, Database(":memory:"))
    results = processor._parse_packed_response(
        '[{"id": "7", "title_ja": "題", "summary_ja": "要約"}]'
    )
    assert results[7].title_ja == "題"


@pytest.mark.asyncio
@respx.mock
async def test_select_endpoint_applies_pack_size(ai_config: AIConfig):
    """pack_size/pack_max_tokens come from the selected endpoint; others default to 1."""
    from news_crawler.config import AIEndpoint

    ai_config.endpoints = [
        AIEndpoint(name="local", proxy_url="http://local/v1", model="m1"),
        AIEndpoint(
            name="packed",
            proxy_url="http://packed/v1",
            model="m2",
            pack_size=10,
            pack_max_tokens=5000,
        ),
    ]
    respx.get("http://local/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}]})
    )
    respx.get("http://packed/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m2"}]})
    )

    processor = AIProcessor(ai_config, Database(":memory:"))
    await processor.select_endpoint("packed")
    assert processor.config.pack_size == 10
    assert processor.config.pack_max_tokens == 5000

    processor = AIProcessor(ai_config, Database(":memory:"))
    await processor.select_endpoint("local")
    assert processor.config.pack_size == 1


def test_split_sums_exactly():
    from news_crawler.ai_processor import _split

    assert _split(10, [1, 1, 1]) == [4, 3, 3]
    assert sum(_split(1001, [7, 3, 5, 0])) == 1001
    assert _split(5, [0, 0]) == [3, 2]
    assert _split(0, []) == []


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_packed_records_usage_and_cost(
    temp_db: Database, ai_config: AIConfig
):
    """Packed usage is recorded per request and split over the articles."""
    from news_crawler.config import ModelPricing

    ai_config.pack_size = 10
    ai_config.pricing = ModelPricing(input_per_mtok=0.10, output_per_mtok=0.40)
    saved = _save_pending_articles(temp_db, 3)
    response = _packed_response(
        [{"id": a.id, "title_ja": "題", "summary_ja": "要約"} for a in saved]
    )
    response["usage"] = {"prompt_tokens": 1000, "completion_tokens": 301}
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=response)
    )

    await AIProcessor(ai_config, temp_db).enrich_articles(days=7, limit=10)

    with temp_db.connection() as conn:
        row = conn.execute("SELECT * FROM ai_usage").fetchone()
    assert row["mode"] == "sync"
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 301)
    assert row["cost_usd"] == pytest.approx((1000 * 0.10 + 301 * 0.40) / 1e6)
    assert json.loads(row["article_ids"]) == [a.id for a in saved]

    arts = [_get(temp_db, a.id) for a in saved]
    assert sum(a.ai_input_tokens for a in arts) == 1000
    assert sum(a.ai_output_tokens for a in arts) == 301
    assert sum(a.ai_cost_usd for a in arts) == pytest.approx(row["cost_usd"])


@pytest.mark.asyncio
@respx.mock
async def test_ai_processor_single_records_tokens_without_pricing(
    temp_db: Database, ai_config: AIConfig, aia_success_response: dict
):
    """Without pricing, tokens are recorded but cost stays empty."""
    (article,) = _save_pending_articles(temp_db, 1)
    aia_success_response["usage"] = {"prompt_tokens": 120, "completion_tokens": 40}
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=aia_success_response)
    )

    await AIProcessor(ai_config, temp_db).enrich_articles(days=7, limit=10)

    updated = _get(temp_db, article.id)
    assert (updated.ai_input_tokens, updated.ai_output_tokens) == (120, 40)
    assert updated.ai_cost_usd is None
