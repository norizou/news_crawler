"""Hugging Face API fetch adapter."""

from datetime import datetime
from typing import Any

import httpx

from ai_scraper.adapters.base import BaseAdapter
from ai_scraper.models import Article
from ai_scraper.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime
from ai_scraper.utils import get_ssl_verify


class HuggingFaceAdapter(BaseAdapter):
    """Adapter for Hugging Face Model Hub API."""

    async def fetch(self) -> list[Article]:
        """Fetch models from Hugging Face API."""
        # Extract author from base_url
        # Example: https://huggingface.co/deepseek-ai -> deepseek-ai
        base_url = self.source.base_url.rstrip("/")
        if "huggingface.co/" in base_url:
            author = base_url.split("huggingface.co/")[-1]
        else:
            author = base_url

        # Hugging Face API endpoint for models by author
        api_url = f"https://huggingface.co/api/models?author={author}&limit=20"

        headers = {
            "User-Agent": self.crawler_config.user_agent,
            "Accept": "application/json",
            **self.source.headers,
        }

        async with httpx.AsyncClient(
            timeout=self.crawler_config.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            verify=get_ssl_verify(),
        ) as client:
            response = await client.get(api_url)
            response.raise_for_status()
            models: list[dict[str, Any]] = response.json()

        articles: list[Article] = []

        for model in models:
            # Extract model info
            model_id = model.get("modelId", "")
            created_at_str = model.get("createdAt")
            likes = model.get("likes", 0)
            downloads = model.get("downloads", 0)
            tags = model.get("tags", [])
            pipeline_tag = model.get("pipeline_tag", "")
            library_name = model.get("library_name", "")

            if not model_id:
                continue

            # Construct URL
            url = f"https://huggingface.co/{model_id}"

            # Parse creation date
            published_at = None
            if created_at_str:
                published_at = parse_datetime(created_at_str)

            # Create title
            title = f"{model_id}"

            # Create summary
            summary_parts = []
            if pipeline_tag:
                summary_parts.append(f"Pipeline: {pipeline_tag}")
            if library_name:
                summary_parts.append(f"Library: {library_name}")
            if likes > 0:
                summary_parts.append(f"Likes: {likes}")
            if downloads > 0:
                summary_parts.append(f"Downloads: {downloads}")
            summary = " | ".join(summary_parts)

            # Create content
            content_parts = [f"Model ID: {model_id}"]
            if pipeline_tag:
                content_parts.append(f"Pipeline Tag: {pipeline_tag}")
            if library_name:
                content_parts.append(f"Library: {library_name}")
            if likes > 0:
                content_parts.append(f"Likes: {likes}")
            if downloads > 0:
                content_parts.append(f"Downloads: {downloads}")
            if tags:
                content_parts.append(f"Tags: {', '.join(tags)}")
            content = "\n".join(content_parts)

            # Clean content
            cleaned_title = clean_text(title)
            cleaned_summary = clean_text(summary)
            cleaned_content = clean_text(content)

            # Compute hash
            hash_val = compute_content_hash(cleaned_title, cleaned_content)

            article = Article(
                source_key=self.source.key,
                url=url,
                normalized_url=normalize_url(url),
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
