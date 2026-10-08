"""Static HTML scraping adapter using httpx and BeautifulSoup."""

import asyncio
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from news_crawler.adapters.base import BaseAdapter
from news_crawler.models import Article
from news_crawler.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime
from news_crawler.utils import get_ssl_verify


class HTMLAdapter(BaseAdapter):
    """Adapter for static HTML websites."""

    async def fetch(self) -> list[Article]:
        """Fetch article list and extract metadata and contents."""
        list_url = self.source.list_url or self.source.base_url
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
            resp = await client.get(list_url)
            resp.raise_for_status()
            list_soup = self._make_soup(resp.content)

            # Discover article links
            article_urls: list[str] = []
            if self.source.article_list_selector:
                elements = list_soup.select(self.source.article_list_selector)
                for el in elements:
                    href = el.get("href")
                    if href and isinstance(href, str):
                        full_url = urljoin(list_url, href)
                        article_urls.append(full_url)
            else:
                # Fallback: scan all <a> tags inside <article> or main content
                for a_tag in list_soup.select("article a, main a, div.post a, div.article a"):
                    href = a_tag.get("href")
                    if href and isinstance(href, str) and not href.startswith(("#", "javascript:")):
                        full_url = urljoin(list_url, href)
                        article_urls.append(full_url)

            # Deduplicate URLs maintaining order
            seen = set()
            unique_urls = []
            for u in article_urls:
                norm = normalize_url(u)
                if norm not in seen and norm.startswith("http"):
                    seen.add(norm)
                    unique_urls.append(u)

            # Limit to recent top 20 to avoid over-fetching
            unique_urls = unique_urls[:20]

            articles: list[Article] = []
            for url in unique_urls:
                if self.crawler_config.download_delay > 0:
                    await asyncio.sleep(self.crawler_config.download_delay)

                try:
                    art_resp = await client.get(url)
                    if art_resp.status_code != 200:
                        continue
                    art = self._parse_article_page(url, art_resp.content)
                    if art:
                        articles.append(art)
                except Exception:
                    # Ignore single article fetch failure
                    continue

        return articles

    def _make_soup(self, content: bytes) -> BeautifulSoup:
        """Build a BeautifulSoup from raw response bytes.

        BeautifulSoup detects the encoding from BOM and <meta charset>
        declarations; ``source.encoding`` (e.g. 'shift_jis') overrides it
        for sites whose charset is unreliable.
        """
        return BeautifulSoup(
            content, "html.parser", from_encoding=self.source.encoding
        )

    def _parse_article_page(self, url: str, html: bytes) -> Article | None:
        """Extract structured fields from article HTML."""
        soup = self._make_soup(html)

        # 1. Title
        title = ""
        if self.source.title_selector:
            title_el = soup.select_one(self.source.title_selector)
            if title_el:
                title = title_el.get_text(strip=True)
        if not title:
            h1 = soup.select_one("h1")
            if h1:
                title = h1.get_text(strip=True)
            elif soup.title:
                title = soup.title.get_text(strip=True)

        if not title:
            return None

        # 2. Content & Summary
        content = ""
        if self.source.content_selector:
            content_el = soup.select_one(self.source.content_selector)
            if content_el:
                content = content_el.get_text(separator="\n", strip=True)
        if not content:
            article_body = soup.select_one("article, main, div.entry-content, div.post-content")
            if article_body:
                content = article_body.get_text(separator="\n", strip=True)

        summary = ""
        if self.source.summary_selector:
            summary_el = soup.select_one(self.source.summary_selector)
            if summary_el:
                summary = summary_el.get_text(strip=True)
        if not summary:
            # Fallback to meta description or first paragraph
            meta_desc = soup.select_one('meta[name="description"], meta[property="og:description"]')
            if meta_desc and meta_desc.get("content"):
                summary = str(meta_desc["content"]).strip()
            elif content:
                summary = content[:200] + "..." if len(content) > 200 else content

        # 3. Date
        published_at = None
        if self.source.date_selector:
            date_el = soup.select_one(self.source.date_selector)
            if date_el:
                datetime_attr = date_el.get("datetime") or date_el.get_text(strip=True)
                if isinstance(datetime_attr, str):
                    published_at = parse_datetime(datetime_attr)

        if not published_at:
            # Fallback to meta tags
            meta_date = soup.select_one(
                'meta[property="article:published_time"], meta[name="pubdate"], time'
            )
            if meta_date:
                dt_str = (
                    meta_date.get("datetime")
                    or meta_date.get("content")
                    or meta_date.get_text()
                )
                if isinstance(dt_str, str):
                    published_at = parse_datetime(dt_str)

        # 4. Author
        author = None
        if self.source.author_selector:
            auth_el = soup.select_one(self.source.author_selector)
            if auth_el:
                author = auth_el.get_text(strip=True)

        norm_url = normalize_url(url)
        cleaned_title = clean_text(title)
        cleaned_summary = clean_text(summary)
        cleaned_content = clean_text(content)
        hash_val = compute_content_hash(cleaned_title, cleaned_content)

        return Article(
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
        )
