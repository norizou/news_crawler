"""RSS/Atom feed fetch adapter."""

import asyncio
from datetime import datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from news_crawler.adapters.base import BaseAdapter
from news_crawler.models import Article
from news_crawler.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime
from news_crawler.utils import get_ssl_verify


class RSSAdapter(BaseAdapter):
    """Adapter for RSS and Atom feeds.

    With ``source.fulltext_selector`` set, entry pages are fetched to replace the
    (often truncated) feed excerpt with the article body. URLs in
    ``skip_fulltext_urls`` (normalized) are not fetched again.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.skip_fulltext_urls: set[str] = set()

    async def fetch(self) -> list[Article]:
        """Fetch and parse feed entries."""
        target_url = self.source.feed_url or self.source.base_url
        headers = {
            "User-Agent": self.crawler_config.user_agent,
            **self.source.headers,
        }

        async with httpx.AsyncClient(
            timeout=self.crawler_config.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            verify=get_ssl_verify(),
        ) as client:
            response = await client.get(target_url)
            response.raise_for_status()
            feed_content = response.text
            fetched_articles = self._parse_feed(feed_content)
            if self.source.fulltext_selector:
                # Drop articles whose full text is already stored: re-emitting them with
                # the feed excerpt would overwrite the stored body on upsert.
                fetched_articles = [
                    a for a in fetched_articles if a.normalized_url not in self.skip_fulltext_urls
                ]
                await self._fill_fulltext(client, fetched_articles)
        return fetched_articles

    async def _fill_fulltext(self, client: httpx.AsyncClient, articles: list[Article]) -> None:
        """Replace feed excerpts with page body text; keep the excerpt on any failure."""
        for art in articles:
            if self.crawler_config.download_delay > 0:
                await asyncio.sleep(self.crawler_config.download_delay)
            try:
                resp = await client.get(art.url)
                if resp.status_code != 200:
                    continue
                body = self._extract_fulltext(resp.content)
            except Exception:
                continue
            # Only adopt a body that is longer than the excerpt it replaces
            if body and len(body) > len(art.content):
                art.content = body
                art.content_hash = compute_content_hash(art.title, body)

    def _extract_fulltext(self, html: bytes) -> str:
        soup = BeautifulSoup(html, "html.parser", from_encoding=self.source.encoding)
        el = soup.select_one(self.source.fulltext_selector or "")
        if el is None:
            return ""
        return clean_text(el.get_text(separator="\n", strip=True))

    def _parse_feed(self, feed_content: str) -> list[Article]:

        # Parse with feedparser
        feed: Any = feedparser.parse(feed_content)
        articles: list[Article] = []

        for entry in feed.entries:
            url = getattr(entry, "link", "")
            title = getattr(entry, "title", "")
            if not url or not title:
                continue

            # Extract summary/content
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            # Strip HTML from summary
            summary_text = ""
            if raw_summary:
                soup = BeautifulSoup(raw_summary, "html.parser")
                summary_text = soup.get_text(separator=" ", strip=True)

            # Full content if available
            content_text = summary_text
            if hasattr(entry, "content") and entry.content:
                soup = BeautifulSoup(entry.content[0].value, "html.parser")
                content_text = soup.get_text(separator="\n", strip=True)

            # Publication date
            published_at = None
            for date_field in ("published", "pubDate", "updated", "created"):
                if hasattr(entry, date_field):
                    published_at = parse_datetime(getattr(entry, date_field))
                    if published_at:
                        break

            # Author
            author = getattr(entry, "author", None)

            # Tags
            tags: list[str] = []
            if hasattr(entry, "tags") and entry.tags:
                tags = [t.term for t in entry.tags if hasattr(t, "term") and t.term]

            norm_url = normalize_url(url)
            cleaned_title = clean_text(title)
            cleaned_summary = clean_text(summary_text)
            cleaned_content = clean_text(content_text)
            hash_val = compute_content_hash(cleaned_title, cleaned_content)

            article = Article(
                source_key=self.source.key,
                url=url,
                normalized_url=norm_url,
                title=cleaned_title,
                summary=cleaned_summary,
                content=cleaned_content,
                published_at=published_at,
                fetched_at=datetime.now(),
                content_hash=hash_val,
                category=self.source.category,
                author=author,
                tags=tags,
            )
            articles.append(article)

        return articles
