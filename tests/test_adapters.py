"""Tests for fetch adapters using local fixtures."""

from pathlib import Path

import httpx
import pytest
import respx

from ai_scraper.adapters.html import HTMLAdapter
from ai_scraper.adapters.huggingface import HuggingFaceAdapter
from ai_scraper.adapters.rss import RSSAdapter
from ai_scraper.config import CrawlerConfig
from ai_scraper.models import FetchMethod, SourceConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@respx.mock
async def test_rss_adapter():
    feed_xml = (FIXTURES_DIR / "sample_rss.xml").read_text(encoding="utf-8")
    feed_url = "https://example.com/rss.xml"

    respx.get(feed_url).mock(return_value=httpx.Response(200, text=feed_xml))

    source = SourceConfig(
        key="sample_blog",
        name="Sample Blog",
        category="official",
        fetch_method=FetchMethod.RSS,
        base_url="https://example.com",
        feed_url=feed_url,
    )
    crawler_cfg = CrawlerConfig(download_delay=0.0)

    adapter = RSSAdapter(source, crawler_cfg)
    articles = await adapter.fetch()

    assert len(articles) == 2
    assert articles[0].title == "Introducing Next-Gen AI Model"
    assert articles[0].normalized_url == "https://example.com/blog/next-gen-ai"
    assert "reasoning capabilities" in articles[0].summary
    assert articles[0].published_at is not None
    assert articles[0].author == "AI Research Team"


@pytest.mark.asyncio
@respx.mock
async def test_html_adapter():
    list_html = (FIXTURES_DIR / "sample_list.html").read_text(encoding="utf-8")
    art_html = (FIXTURES_DIR / "sample_article.html").read_text(encoding="utf-8")

    list_url = "https://example.com/articles"
    respx.get(list_url).mock(return_value=httpx.Response(200, text=list_html))
    respx.get("https://example.com/articles/quantum-ai-breakthrough").mock(
        return_value=httpx.Response(200, text=art_html)
    )
    respx.get("https://example.com/articles/robotics-vlm-navigation").mock(
        return_value=httpx.Response(404)
    )

    source = SourceConfig(
        key="sample_media",
        name="Sample Media",
        category="media",
        fetch_method=FetchMethod.HTML,
        base_url="https://example.com",
        list_url=list_url,
        article_list_selector="article.post-card h2 a",
        title_selector="h1.article-title",
        content_selector="div.article-body",
        date_selector="time.pub-date",
        author_selector="span.author-name",
    )
    crawler_cfg = CrawlerConfig(download_delay=0.0)

    adapter = HTMLAdapter(source, crawler_cfg)
    articles = await adapter.fetch()

    assert len(articles) == 1
    assert articles[0].title == "Quantum AI Breakthrough Announced"
    assert "groundbreaking experiment" in articles[0].content
    assert articles[0].author == "Alice Johnson"
    assert articles[0].published_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_huggingface_adapter():
    api_url = "https://huggingface.co/api/models?author=deepseek-ai&limit=20"
    payload = [
        {
            "modelId": "deepseek-ai/DeepSeek-Test",
            "createdAt": "2026-09-10T02:17:58.000Z",
            "likes": 2305,
            "downloads": 288414,
            "tags": ["transformers", "license:mit"],
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
        },
        {"createdAt": "2026-09-11T00:00:00.000Z"},
    ]
    route = respx.get(api_url).mock(return_value=httpx.Response(200, json=payload))
    source = SourceConfig(
        key="deepseek_hf",
        name="DeepSeek (Hugging Face)",
        category="chinese_official",
        fetch_method=FetchMethod.HUGGINGFACE,
        base_url="https://huggingface.co/deepseek-ai",
    )

    articles = await HuggingFaceAdapter(source, CrawlerConfig()).fetch()

    assert route.called
    assert len(articles) == 1
    assert articles[0].source_key == "deepseek_hf"
    assert articles[0].title == "deepseek-ai/DeepSeek-Test"
    assert articles[0].normalized_url == "https://huggingface.co/deepseek-ai/DeepSeek-Test"
    assert articles[0].summary == (
        "Pipeline: text-generation | Library: transformers | Likes: 2305 | Downloads: 288414"
    )
    assert articles[0].author == "deepseek-ai"
    assert articles[0].tags == ["transformers", "license:mit"]
    assert articles[0].published_at is not None
    assert articles[0].content_hash
