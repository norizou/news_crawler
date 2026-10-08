"""AI enrichment processor for translating and summarizing articles."""

import asyncio
import json
import os
import re
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from news_crawler.config import AIConfig, AIEndpoint
from news_crawler.database import Database
from news_crawler.models import AIResponse, Article


class AIProcessor:
    """Handles AI enrichment of articles via AIA Proxy."""

    def __init__(
        self,
        config: AIConfig,
        db: Database,
        sleeper: Callable[[float], Any] | None = None,
    ):
        """Initialize AI processor with configuration and database."""
        self.config = config
        self.db = db
        self.sleeper = sleeper or asyncio.sleep
        self.active_endpoint: str | None = None
        self.only_endpoint: str | None = None
        self._auth_env: str | None = None

    def _api_key(self, endpoint: AIEndpoint) -> str | None:
        return os.getenv(endpoint.api_key_env) if endpoint.api_key_env else None

    async def _probe(self, endpoint: AIEndpoint) -> str | None:
        """Return None if usable, else a short reason. Checks reachability and model ID."""
        headers = {}
        key = self._api_key(endpoint)
        if endpoint.api_key_env and not key:
            return f"env {endpoint.api_key_env} is not set"
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{endpoint.proxy_url}/models", headers=headers)
            r.raise_for_status()
            ids = {m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])}
        except Exception as e:  # unreachable / bad response
            return f"{type(e).__name__}"
        if endpoint.model not in ids:
            return f"model {endpoint.model} not loaded"
        return None

    async def select_endpoint(self, only: str | None = None) -> str:
        """Pick the first usable endpoint (or `only`) and apply it to self.config."""
        cfg = self.config
        if not cfg.endpoints:
            self.active_endpoint = "default"
            return "default"
        by_name = {e.name: e for e in cfg.endpoints}
        order = [only] if only else (cfg.endpoint_order or [e.name for e in cfg.endpoints])
        reasons: list[str] = []
        for name in order:
            ep = by_name.get(name)
            if ep is None:
                raise ValueError(f"Unknown LLM endpoint: {name} (available: {', '.join(by_name)})")
            reason = await self._probe(ep)
            if reason is None:
                self.config = cfg.model_copy(
                    update={
                        "proxy_url": ep.proxy_url,
                        "model": ep.model,
                        "max_tokens": ep.max_tokens or cfg.max_tokens,
                    }
                )
                self._auth_env = ep.api_key_env
                self.active_endpoint = name
                return name
            reasons.append(f"{name}: {reason}")
        raise RuntimeError("No usable LLM endpoint (" + "; ".join(reasons) + ")")

    async def enrich_articles(
        self,
        days: int = 7,
        limit: int = 50,
        retry_failed: bool = False,
    ) -> dict[str, int]:
        """
        Enrich pending articles with AI-generated Japanese title and summary.
        Returns statistics about the enrichment run.
        """
        if not self.config.enabled:
            return {"skipped": 0, "success": 0, "failed": 0, "total": 0}

        articles = self.db.get_pending_articles(
            days=days, limit=limit, retry_failed=retry_failed
        )

        if not articles:
            return {"skipped": 0, "success": 0, "failed": 0, "total": 0}

        await self.select_endpoint(self.only_endpoint)

        success_count = 0
        failed_count = 0

        for idx, article in enumerate(articles):
            if article.id is None:
                continue
            try:
                await self._enrich_single_article(article)
                success_count += 1
            except Exception as e:
                self.db.save_ai_failure(article.id, str(e))
                failed_count += 1

            # Sleep between requests to respect rate limits (not after the last article)
            if self.config.request_interval > 0 and idx < len(articles) - 1:
                await self.sleeper(self.config.request_interval)

        return {
            "skipped": 0,
            "success": success_count,
            "failed": failed_count,
            "total": len(articles),
        }

    async def _enrich_single_article(self, article: Article) -> None:
        """Enrich a single article with AI-generated content."""
        # Skip if already completed with same input hash
        input_hash = self._compute_input_hash(article)
        if (
            article.ai_status == "completed"
            and article.ai_input_hash == input_hash
            and article.ai_model == self.config.model
            and article.ai_prompt_version == self.config.prompt_version
        ):
            return

        # Prepare input text
        input_text = self._prepare_input_text(article)

        # Call AI API
        response = await self._call_ai_api(input_text)

        # Parse and validate response
        ai_result = self._parse_ai_response(response)

        # Save result
        if article.id is not None:
            self.db.save_ai_result(
                article_id=article.id,
                title_ja=ai_result.title_ja,
                summary_ja=ai_result.summary_ja,
                model=self.config.model,
                prompt_version=self.config.prompt_version,
                input_hash=input_hash,
            )

    def _compute_input_hash(self, article: Article) -> str:
        """Compute hash of input data for change detection."""
        from news_crawler.normalizer import compute_content_hash

        return compute_content_hash(article.title, article.content)

    def _prepare_input_text(self, article: Article) -> str:
        """Prepare input text for AI processing with character limit."""
        # Combine title, summary, and content
        parts = []
        if article.title:
            parts.append(f"Title: {article.title}")
        if article.summary:
            parts.append(f"Summary: {article.summary}")
        if article.content:
            parts.append(f"Content: {article.content}")

        full_text = "\n\n".join(parts)

        # Truncate to max input characters
        if len(full_text) > self.config.max_input_chars:
            full_text = full_text[: self.config.max_input_chars]

        return full_text

    async def _call_ai_api(self, input_text: str) -> dict[str, Any]:
        """Call AIA Proxy API with retry logic."""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(input_text)

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.3,
        }

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.config.timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    headers = {"Content-Type": "application/json"}
                    key = os.getenv(self._auth_env) if self._auth_env else None
                    if key:
                        headers["Authorization"] = f"Bearer {key}"
                    response = await client.post(
                        f"{self.config.proxy_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data: dict[str, Any] = response.json()
                    return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Respect Retry-After header
                    retry_after = int(e.response.headers.get("Retry-After", 5))
                    await self.sleeper(retry_after)
                    continue
                elif e.response.status_code in (400, 401, 403, 404):
                    # Don't retry client errors
                    raise
                else:
                    last_error = e
                    if attempt < self.config.max_retries:
                        await self.sleeper(2 ** attempt)  # Exponential backoff
                        continue
                    raise

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = e
                if attempt < self.config.max_retries:
                    await self.sleeper(2 ** attempt)
                    continue
                raise Exception(f"Max retries exceeded: {last_error}") from e

        raise Exception(f"Max retries exceeded: {last_error}")

    def _build_system_prompt(self) -> str:
        """Build system prompt for AI processing."""
        return self.config.system_prompt

    def _build_user_prompt(self, input_text: str) -> str:
        """Build user prompt with article content."""
        return (
            "Please process the following article and provide the Japanese title "
            f"and summary in JSON format:\n\n{input_text}"
        )

    def _parse_ai_response(self, response: dict[str, Any]) -> AIResponse:
        """Parse and validate AI response."""
        try:
            # Extract content from OpenAI-compatible response
            content = response["choices"][0]["message"]["content"]

            # Try to extract JSON from response (handle markdown code fences)
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = content.strip()

            # Parse JSON
            data = json.loads(json_str)

            # Validate with Pydantic
            return AIResponse(**data)

        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as e:
            raise ValueError(f"Invalid AI response format: {e}")
