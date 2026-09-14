"""RSS/Atom feed fetch adapter."""

from datetime import datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from ai_scraper.adapters.base import BaseAdapter
from ai_scraper.models import Article
from ai_scraper.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime
from ai_scraper.utils import get_ssl_verify


class RSSAdapter(BaseAdapter):
    """Adapter for RSS and Atom feeds."""

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
