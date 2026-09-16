"""GitHub Releases fetch adapter."""

import os
from datetime import datetime
from typing import Any

import httpx

from news_crawler.adapters.base import BaseAdapter
from news_crawler.models import Article
from news_crawler.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime
from news_crawler.utils import get_ssl_verify


class GitHubAdapter(BaseAdapter):
    """Adapter for GitHub Releases."""

    async def fetch(self) -> list[Article]:
        """Fetch GitHub releases."""
        # Extract owner/repo from base_url
        # Example: https://github.com/deepseek-ai -> deepseek-ai
        base_url = self.source.base_url.rstrip("/")
        if "github.com/" in base_url:
            repo_path = base_url.split("github.com/")[-1]
        else:
            repo_path = base_url

        # GitHub API endpoint for releases
        api_url = f"https://api.github.com/repos/{repo_path}/releases"

        # Get GitHub token from environment
        github_token = os.getenv("GITHUB_TOKEN")
        headers = {
            "User-Agent": self.crawler_config.user_agent,
            "Accept": "application/vnd.github.v3+json",
            **self.source.headers,
        }
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        async with httpx.AsyncClient(
            timeout=self.crawler_config.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            verify=get_ssl_verify(),
        ) as client:
            response = await client.get(api_url)
            if response.status_code == 404:
                # Repository not found or no releases
                return []
            response.raise_for_status()
            releases: list[dict[str, Any]] = response.json()

        articles: list[Article] = []

        for release in releases:
            # Skip draft releases
            if release.get("draft", False):
                continue

            # Extract release info
            title = release.get("name") or release.get("tag_name", "")
            url = release.get("html_url", "")
            body = release.get("body", "")
            published_at_str = release.get("published_at")
            author = release.get("author", {}).get("login") if release.get("author") else None
            tag_name = release.get("tag_name", "")

            if not title or not url:
                continue

            # Parse publication date
            published_at = None
            if published_at_str:
                published_at = parse_datetime(published_at_str)

            # Clean content
            cleaned_title = clean_text(title)
            cleaned_body = clean_text(body)

            # Create summary (first paragraph or first 200 chars)
            summary = ""
            if cleaned_body:
                lines = cleaned_body.split("\n")
                first_line = lines[0] if lines else ""
                summary = first_line[:200] + "..." if len(first_line) > 200 else first_line

            # Compute hash
            hash_val = compute_content_hash(cleaned_title, cleaned_body)

            article = Article(
                source_key=self.source.key,
                url=url,
                normalized_url=normalize_url(url),
                title=cleaned_title,
                summary=summary,
                content=cleaned_body,
                published_at=published_at,
                fetched_at=datetime.now(),
                content_hash=hash_val,
                category=self.source.category,
                author=author,
                tags=[tag_name] if tag_name else [],
            )
            articles.append(article)

        return articles
