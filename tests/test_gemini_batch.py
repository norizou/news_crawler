"""Tests for the Gemini Batch API client and batch enrichment."""

import json
from datetime import datetime, timedelta

import pytest

from news_crawler.ai_processor import AIProcessor
from news_crawler.config import AIConfig, AIEndpoint, GeminiBatchConfig
from news_crawler.database import Database
from news_crawler.gemini_batch import BatchResult, BatchStatus, parse_status, to_gemini_schema
from news_crawler.models import Article, FetchMethod, SourceConfig


def _response(text: str, key: str) -> dict:
    return {
        "metadata": {"key": key},
        "response": {"candidates": [{"content": {"parts": [{"text": text}]}}]},
    }


def test_parse_status_bare_batch_with_dest():
    status = parse_status(
        {
            "name": "batches/1",
            "state": "JOB_STATE_SUCCEEDED",
            "dest": {"inlinedResponses": [_response("{}", "pack-0000")]},
        }
    )
    assert status.name == "batches/1"
    assert status.state == "JOB_STATE_SUCCEEDED"
    assert status.results == [BatchResult(key="pack-0000", text="{}")]


def test_parse_status_operation_wrapper_with_nested_output():
    status = parse_status(
        {
            "name": "batches/2",
            "metadata": {
                "state": "JOB_STATE_SUCCEEDED",
                "output": {
                    "inlinedResponses": {
                        "inlinedResponses": [
                            _response("[]", "pack-0000"),
                            {"metadata": {"key": "pack-0001"}, "error": {"message": "boom"}},
                        ]
                    }
                },
            },
        }
    )
    assert status.state == "JOB_STATE_SUCCEEDED"
    assert status.results[0].text == "[]"
    assert status.results[1] == BatchResult(key="pack-0001", error="boom")


def test_parse_status_normalizes_batch_state_prefix():
    """Shape observed from the live API on 2026-10-31."""
    status = parse_status(
        {
            "name": "batches/x",
            "metadata": {
                "@type": "type.googleapis.com/...GenerateContentBatch",
                "model": "models/gemini-2.5-flash-lite",
                "batchStats": {"requestCount": "5", "pendingRequestCount": "5"},
                "state": "BATCH_STATE_RUNNING",
                "name": "batches/x",
            },
        }
    )
    assert status.state == "JOB_STATE_RUNNING"
    assert status.results == []


def test_parse_status_pending_without_results():
    status = parse_status({"name": "batches/3", "metadata": {"state": "JOB_STATE_PENDING"}})
    assert status.state == "JOB_STATE_PENDING"
    assert status.results == []


def test_to_gemini_schema_uppercases_types():
    schema = to_gemini_schema(
        {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "integer"}}}}
    )
    assert schema == {
        "type": "OBJECT",
        "properties": {"a": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
    }


class FakeBatchClient:
    """Records submissions; returns preset statuses from get()."""

    def __init__(self):
        self.submitted: list[tuple[str, list, str]] = []
        self.statuses: list[BatchStatus] = []

    async def submit(self, model, requests, display_name):
        self.submitted.append((model, requests, display_name))
        return BatchStatus(name="batches/job1", state="JOB_STATE_PENDING")

    async def get(self, name):
        return self.statuses.pop(0)


@pytest.fixture
def batch_config(ai_config: AIConfig) -> AIConfig:
    ai_config.endpoints = [
        AIEndpoint(name="local", proxy_url="http://local/v1", model="m1"),
        AIEndpoint(
            name="gemini",
            proxy_url="http://litellm/v1",
            model="gemini-test",
            pack_size=2,
            batch_api=GeminiBatchConfig(api_key_env="TEST_GEMINI_KEY", poll_interval=5),
        ),
    ]
    return ai_config


@pytest.fixture
def batch_processor(temp_db: Database, batch_config: AIConfig, fake_sleeper, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "secret")
    client = FakeBatchClient()
    processor = AIProcessor(batch_config, temp_db, sleeper=fake_sleeper)
    processor.only_endpoint = "gemini"
    processor.batch_client_factory = lambda base_url, key: client
    processor.fake_client = client
    return processor


def _save_articles(db: Database, n: int) -> list[Article]:
    db.upsert_source(
        SourceConfig(
            key="test_source",
            name="Test",
            category="test",
            fetch_method=FetchMethod.RSS,
            base_url="https://example.com",
        )
    )
    out = []
    for i in range(n):
        article, _, _ = db.upsert_article(
            Article(
                source_key="test_source",
                url=f"https://example.com/b-{i}",
                normalized_url=f"https://example.com/b-{i}",
                title=f"Batch {i}",
                content=f"Body {i}",
                published_at=datetime.now() - timedelta(hours=i),
                category="test",
            )
        )
        out.append(article)
    return out


def _status_for(processor: AIProcessor, state: str, entries_by_key: dict) -> BatchStatus:
    results = [
        BatchResult(key=key, text=json.dumps({"articles": entries}))
        for key, entries in entries_by_key.items()
    ]
    return BatchStatus(name="batches/job1", state=state, results=results)


def _ids_by_pack(db: Database) -> dict[str, list[int]]:
    packs: dict[str, list[int]] = {}
    for item in db.get_batch_items("batches/job1"):
        packs.setdefault(item["pack_key"], []).append(item["article_id"])
    return packs


@pytest.mark.asyncio
async def test_submit_batch_packs_and_records_job(batch_processor, temp_db):
    _save_articles(temp_db, 3)

    sub = await batch_processor.submit_batch(days=7, limit=10)

    assert sub == {"job_name": "batches/job1", "articles": 3, "requests": 2}
    model, requests, _ = batch_processor.fake_client.submitted[0]
    assert model == "gemini-test"
    assert [key for key, _ in requests] == ["pack-0000", "pack-0001"]
    first = requests[0][1]
    assert first["generationConfig"]["responseSchema"]["type"] == "OBJECT"
    assert "### id=" in first["contents"][0]["parts"][0]["text"]
    assert '"articles"' in first["systemInstruction"]["parts"][0]["text"]

    # Articles in the open job are not picked up again
    assert temp_db.get_pending_articles(days=7, limit=10) == []
    assert len(temp_db.get_pending_articles(days=7, limit=10, exclude_in_flight=False)) == 3


@pytest.mark.asyncio
async def test_collect_saves_results_and_closes_job(batch_processor, temp_db, fake_sleeper):
    saved = _save_articles(temp_db, 3)
    await batch_processor.submit_batch(days=7, limit=10)
    packs = _ids_by_pack(temp_db)
    entries = {
        key: [{"id": i, "title_ja": f"題{i}", "summary_ja": f"要約{i}"} for i in ids]
        for key, ids in packs.items()
    }
    batch_processor.fake_client.statuses = [
        BatchStatus(name="batches/job1", state="JOB_STATE_RUNNING"),
        _status_for(batch_processor, "JOB_STATE_SUCCEEDED", entries),
    ]

    stats = await batch_processor.collect_batches(wait=True)

    assert stats["collected"] == 1
    assert stats["success"] == 3
    assert stats["open"] == 0
    assert fake_sleeper.calls == [5]
    assert temp_db.list_open_batch_jobs() == []
    done = temp_db.get_articles_by_ids([a.id for a in saved])
    for article in saved:
        assert done[article.id].ai_status == "completed"
        assert done[article.id].title_ja == f"題{article.id}"
        assert done[article.id].ai_model == "gemini-test"


@pytest.mark.asyncio
async def test_collect_failed_job_releases_articles(batch_processor, temp_db):
    _save_articles(temp_db, 2)
    await batch_processor.submit_batch(days=7, limit=10)
    batch_processor.fake_client.statuses = [
        BatchStatus(name="batches/job1", state="JOB_STATE_FAILED", raw={"error": "quota"})
    ]

    stats = await batch_processor.collect_batches()

    assert stats["closed"] == 1
    assert len(temp_db.get_pending_articles(days=7, limit=10)) == 2


@pytest.mark.asyncio
async def test_collect_skips_articles_changed_since_submit(batch_processor, temp_db):
    saved = _save_articles(temp_db, 1)
    await batch_processor.submit_batch(days=7, limit=10)
    changed = saved[0].model_copy(update={"content": "Rewritten body", "id": None})
    temp_db.upsert_article(changed)
    entries = {"pack-0000": [{"id": saved[0].id, "title_ja": "古い", "summary_ja": "古い"}]}
    batch_processor.fake_client.statuses = [
        _status_for(batch_processor, "JOB_STATE_SUCCEEDED", entries)
    ]

    stats = await batch_processor.collect_batches()

    assert stats["skipped"] == 1
    assert stats["success"] == 0
    assert temp_db.get_articles_by_ids([saved[0].id])[saved[0].id].title_ja != "古い"


@pytest.mark.asyncio
async def test_batch_requires_batch_api_and_key(batch_processor, monkeypatch):
    batch_processor.only_endpoint = "local"
    with pytest.raises(ValueError, match="no batch_api"):
        await batch_processor.submit_batch()

    batch_processor.only_endpoint = "gemini"
    monkeypatch.delenv("TEST_GEMINI_KEY")
    with pytest.raises(ValueError, match="TEST_GEMINI_KEY is not set"):
        await batch_processor.submit_batch()
