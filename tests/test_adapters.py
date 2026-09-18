"""Tests for fetch adapters using local fixtures."""

from pathlib import Path

import httpx
import pytest
import respx

from news_crawler.adapters.html import HTMLAdapter
from news_crawler.adapters.huggingface import HuggingFaceAdapter
from news_crawler.adapters.rss import RSSAdapter
from news_crawler.config import CrawlerConfig
from news_crawler.models import FetchMethod, SourceConfig

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
async def test_html_adapter_shift_jis():
    """Shift_JIS page without charset in HTTP header should decode via meta tag."""
    list_html = """<!DOCTYPE html><html><head><meta charset="Shift_JIS"></head>
    <body><div class="news_line"><a href="/news/001">ニュース一覧</a></div></body></html>"""
    art_html = """<!DOCTYPE html><html><head><meta charset="Shift_JIS"></head>
    <body>
    <div class="news_title"><h1>JRA レース結果</h1><p class="date">2026-09-18</p></div>
    <div class="news_body">競馬ニュースの本文です。</div>
    </body></html>"""

    list_url = "https://example.com/news/"
    # No charset in Content-Type header, body is Shift_JIS bytes
    respx.get(list_url).mock(
        return_value=httpx.Response(
            200,
            content=list_html.encode("shift_jis"),
            headers={"Content-Type": "text/html"},
        )
    )
    respx.get("https://example.com/news/001").mock(
        return_value=httpx.Response(
            200,
            content=art_html.encode("shift_jis"),
            headers={"Content-Type": "text/html"},
        )
    )

    source = SourceConfig(
        key="jra_like",
        name="Shift_JIS Site",
        category="official",
        fetch_method=FetchMethod.HTML,
        base_url="https://example.com",
        list_url=list_url,
        article_list_selector="div.news_line a",
        title_selector=".news_title h1",
        content_selector=".news_body",
        date_selector=".news_title p.date",
    )
    crawler_cfg = CrawlerConfig(download_delay=0.0)

    adapter = HTMLAdapter(source, crawler_cfg)
    articles = await adapter.fetch()

    assert len(articles) == 1
    assert articles[0].title == "JRA レース結果"
    assert "競馬ニュースの本文です" in articles[0].content


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
