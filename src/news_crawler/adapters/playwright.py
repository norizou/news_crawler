"""Dynamic web page scraper using Playwright."""

import asyncio
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from news_crawler.adapters.base import BaseAdapter
from news_crawler.models import Article
from news_crawler.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime


class PlaywrightAdapter(BaseAdapter):
    """Adapter for JavaScript-heavy SPA sites requiring headless browser rendering."""

    async def fetch(self) -> list[Article]:
        """Render pages using headless Chromium and extract structured article data."""
        list_url = self.source.list_url or self.source.base_url

        articles: list[Article] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent=self.crawler_config.user_agent,
                viewport={"width": 1280, "height": 800},
                extra_http_headers=self.source.headers,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                # Load list page
                await page.goto(
                    list_url,
                    timeout=self.crawler_config.timeout_seconds * 1000,
                    wait_until="domcontentloaded",
                )

                if self.source.wait_selector:
                    try:
                        await page.wait_for_selector(
                            self.source.wait_selector,
                            timeout=10000,
                        )
                    except Exception:
                        pass
                else:
                    await page.wait_for_timeout(2000)

                list_html = await page.content()
                list_soup = BeautifulSoup(list_html, "html.parser")

                # Extract links
                article_urls: list[str] = []
                if self.source.article_list_selector:
                    elements = list_soup.select(self.source.article_list_selector)
                    for el in elements:
                        href = el.get("href")
                        if href and isinstance(href, str):
                            full_url = urljoin(list_url, href)
                            article_urls.append(full_url)
                else:
                    for a_tag in list_soup.select("article a, main a, h2 a, h3 a"):
                        href = a_tag.get("href")
                        if (
                            href
                            and isinstance(href, str)
                            and not href.startswith(("#", "javascript:"))
                        ):
                            full_url = urljoin(list_url, href)
                            article_urls.append(full_url)

                seen = set()
                unique_urls = []
                for u in article_urls:
                    norm = normalize_url(u)
                    if norm not in seen and norm.startswith("http"):
                        seen.add(norm)
                        unique_urls.append(u)

                unique_urls = unique_urls[:10]  # Limit to 10 for playwright

                for url in unique_urls:
                    if self.crawler_config.download_delay > 0:
                        await asyncio.sleep(self.crawler_config.download_delay)

                    try:
                        await page.goto(
                            url,
                            timeout=self.crawler_config.timeout_seconds * 1000,
                            wait_until="domcontentloaded",
                        )
                        await page.wait_for_timeout(1000)
                        art_html = await page.content()
                        art = self._parse_article_html(url, art_html)
                        if art:
                            articles.append(art)
                    except Exception:
                        continue

            finally:
                await context.close()
                await browser.close()

        return articles

    def _parse_article_html(self, url: str, html: str) -> Article | None:
        """Parse rendered article HTML."""
        soup = BeautifulSoup(html, "html.parser")

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
            article_body = soup.select_one(
                "article, main, div[class*='content'], div[class*='post']"
            )
            if article_body:
                content = article_body.get_text(separator="\n", strip=True)

        summary = ""
        if self.source.summary_selector:
            summary_el = soup.select_one(self.source.summary_selector)
            if summary_el:
                summary = summary_el.get_text(strip=True)
        if not summary:
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
                dt_str = date_el.get("datetime") or date_el.get_text(strip=True)
                if isinstance(dt_str, str):
                    published_at = parse_datetime(dt_str)

        if not published_at:
            meta_date = soup.select_one('meta[property="article:published_time"], time')
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
