"""AI enrichment processor for translating and summarizing articles."""

import asyncio
import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError

from news_crawler.config import AIConfig, AIEndpoint
from news_crawler.database import BATCH_FINAL_STATES, Database
from news_crawler.gemini_batch import BatchStatus, GeminiBatchClient, build_request
from news_crawler.models import AIResponse, Article

PACKED_PROMPT_SUFFIX = """\
Multiple articles are provided in this request. Each one starts with a line "### id=<number>".
This overrides the output format above:
- Process each article independently, following the guidelines above
- Never mix content between articles; each title and summary must come only from its own article
- Output ONLY a JSON object of the form
  {"articles": [{"id": <number>, "title_ja": "...", "summary_ja": "..."}, ...]}
  with exactly one entry per article, using the same id as its "### id=" line"""

PACKED_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title_ja": {"type": "string"},
                    "summary_ja": {"type": "string"},
                },
                "required": ["id", "title_ja", "summary_ja"],
            },
        }
    },
    "required": ["articles"],
}


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
        self.batch_client_factory: Callable[[str, str], GeminiBatchClient] = GeminiBatchClient
        self.batch_poll_interval: float = 30.0

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
                self._apply_endpoint(ep)
                return name
            reasons.append(f"{name}: {reason}")
        raise RuntimeError("No usable LLM endpoint (" + "; ".join(reasons) + ")")

    def _apply_endpoint(self, ep: AIEndpoint, model: str | None = None) -> None:
        """Apply an endpoint's settings to self.config (no reachability check)."""
        cfg = self.config
        self.config = cfg.model_copy(
            update={
                "proxy_url": ep.proxy_url,
                "model": model or ep.model,
                "max_tokens": ep.max_tokens or cfg.max_tokens,
                "reasoning_effort": (
                    ep.reasoning_effort if ep.reasoning_effort is not None else cfg.reasoning_effort
                ),
                "pack_size": ep.pack_size or 1,
                "pack_max_tokens": ep.pack_max_tokens,
            }
        )
        self._auth_env = ep.api_key_env
        self.active_endpoint = ep.name

    def _batch_client(self, endpoint: str | None) -> GeminiBatchClient:
        """Apply a batch-capable endpoint and return a client for it.

        Batch jobs go to Google directly, so LiteLLM's /models probe is skipped;
        only the API key has to be present.
        """
        by_name = {e.name: e for e in self.config.endpoints}
        ep = by_name.get(endpoint or "")
        if ep is None:
            raise ValueError(
                f"Unknown LLM endpoint: {endpoint} (available: {', '.join(by_name)})"
            )
        if ep.batch_api is None:
            raise ValueError(f"Endpoint {ep.name} has no batch_api settings")
        key = os.getenv(ep.batch_api.api_key_env)
        if not key:
            raise ValueError(f"env {ep.batch_api.api_key_env} is not set")
        self._apply_endpoint(ep, model=ep.batch_api.model)
        self.batch_poll_interval = ep.batch_api.poll_interval
        return self.batch_client_factory(ep.batch_api.base_url, key)

    async def submit_batch(
        self,
        days: int = 7,
        limit: int = 50,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        """Submit pending articles as one Gemini batch job, packed `pack_size` per request."""
        client = self._batch_client(self.only_endpoint)
        articles = [
            a
            for a in self.db.get_pending_articles(
                days=days,
                limit=limit,
                retry_failed=retry_failed,
                prompt_version=self.config.prompt_version,
            )
            if a.id is not None and not self._is_up_to_date(a)
        ]
        if not articles:
            return {"job_name": None, "articles": 0, "requests": 0}

        size = self.config.pack_size
        packs = [articles[i : i + size] for i in range(0, len(articles), size)]
        requests = []
        items: list[tuple[int, str, str]] = []
        for idx, pack in enumerate(packs):
            key = f"pack-{idx:04d}"
            requests.append(
                (
                    key,
                    build_request(
                        self._build_packed_system_prompt(),
                        self._build_packed_user_prompt(pack),
                        max_output_tokens=(
                            self.config.pack_max_tokens or self.config.max_tokens * len(pack)
                        ),
                        response_schema=PACKED_RESPONSE_SCHEMA,
                    ),
                )
            )
            items.extend(
                (a.id, self._compute_input_hash(a), key) for a in pack if a.id is not None
            )

        display_name = f"news-crawler-{datetime.now():%Y%m%d-%H%M%S}"
        status = await client.submit(self.config.model, requests, display_name)
        self.db.create_batch_job(
            job_name=status.name,
            model=self.config.model,
            prompt_version=self.config.prompt_version,
            state=status.state,
            items=items,
        )
        return {"job_name": status.name, "articles": len(items), "requests": len(requests)}

    async def collect_batches(self, wait: bool = False) -> dict[str, int]:
        """Check open batch jobs; save results of succeeded ones.

        Failed/cancelled/expired jobs are closed without touching their articles,
        which therefore become eligible for the next run again.
        """
        stats = {"open": 0, "collected": 0, "closed": 0, "success": 0, "failed": 0, "skipped": 0}
        jobs = self.db.list_open_batch_jobs()
        if not jobs:
            return stats
        client = self._batch_client(self.only_endpoint)

        while True:
            still_open = []
            for job in jobs:
                status = await client.get(job["job_name"])
                if status.state == "JOB_STATE_SUCCEEDED":
                    self._save_batch_results(job, status, stats)
                    self.db.update_batch_job_state(job["job_name"], "COLLECTED")
                    stats["collected"] += 1
                elif status.state in BATCH_FINAL_STATES:
                    error = str(status.raw.get("error") or "") or None
                    self.db.update_batch_job_state(job["job_name"], status.state, error)
                    stats["closed"] += 1
                else:
                    self.db.update_batch_job_state(job["job_name"], status.state)
                    still_open.append(job)
            jobs = still_open
            if not wait or not jobs:
                break
            await self.sleeper(self.batch_poll_interval)

        stats["open"] = len(jobs)
        return stats

    def _save_batch_results(
        self, job: dict[str, Any], status: BatchStatus, stats: dict[str, int]
    ) -> None:
        items = self.db.get_batch_items(job["job_name"])
        by_pack: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_pack.setdefault(item["pack_key"], []).append(item)

        # Results normally echo metadata.key; fall back to submission order if not.
        pack_keys = sorted(by_pack)
        results = {
            (r.key or (pack_keys[i] if i < len(pack_keys) else None)): r
            for i, r in enumerate(status.results)
        }
        current = self.db.get_articles_by_ids([item["article_id"] for item in items])

        for key, pack_items in by_pack.items():
            result = results.get(key)
            parsed: dict[int, AIResponse] = {}
            error = None
            if result is None:
                error = "Missing from batch results"
            elif result.error:
                error = result.error
            else:
                try:
                    parsed = self._parse_packed_response(result.text or "")
                except ValueError as e:
                    error = str(e)

            for item in pack_items:
                article = current.get(item["article_id"])
                if article is None or self._compute_input_hash(article) != item["input_hash"]:
                    # Deleted or re-crawled with new content since submission
                    stats["skipped"] += 1
                    continue
                ai_result = parsed.get(item["article_id"])
                if ai_result is None:
                    self.db.save_ai_failure(
                        item["article_id"], error or "Missing from packed AI response"
                    )
                    stats["failed"] += 1
                    continue
                self.db.save_ai_result(
                    article_id=item["article_id"],
                    title_ja=ai_result.title_ja,
                    summary_ja=ai_result.summary_ja,
                    model=job["model"],
                    prompt_version=job["prompt_version"],
                    input_hash=item["input_hash"],
                )
                stats["success"] += 1

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
            days=days,
            limit=limit,
            retry_failed=retry_failed,
            prompt_version=self.config.prompt_version,
        )

        if not articles:
            return {"skipped": 0, "success": 0, "failed": 0, "total": 0}

        await self.select_endpoint(self.only_endpoint)

        if self.config.pack_size > 1:
            return await self._enrich_packed(articles)

        success_count = 0
        failed_count = 0
        skipped_count = 0

        for idx, article in enumerate(articles):
            if article.id is None:
                continue
            try:
                if await self._enrich_single_article(article):
                    success_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                self.db.save_ai_failure(article.id, str(e))
                failed_count += 1

            # Sleep between requests to respect rate limits (not after the last article)
            if self.config.request_interval > 0 and idx < len(articles) - 1:
                await self.sleeper(self.config.request_interval)

        return {
            "skipped": skipped_count,
            "success": success_count,
            "failed": failed_count,
            "total": len(articles),
        }

    async def _enrich_packed(self, articles: list[Article]) -> dict[str, int]:
        """Enrich articles `pack_size` at a time, one API request per pack."""
        skipped_count = 0
        todo: list[Article] = []
        for article in articles:
            if article.id is None:
                continue
            if self._is_up_to_date(article):
                skipped_count += 1
            else:
                todo.append(article)

        success_count = 0
        failed_count = 0
        size = self.config.pack_size
        packs = [todo[i : i + size] for i in range(0, len(todo), size)]
        for idx, pack in enumerate(packs):
            ok, ng = await self._enrich_pack(pack)
            success_count += ok
            failed_count += ng

            # Sleep between requests to respect rate limits (not after the last pack)
            if self.config.request_interval > 0 and idx < len(packs) - 1:
                await self.sleeper(self.config.request_interval)

        return {
            "skipped": skipped_count,
            "success": success_count,
            "failed": failed_count,
            "total": len(articles),
        }

    async def _enrich_pack(self, pack: list[Article]) -> tuple[int, int]:
        """Send one packed request and save each article's result. Returns (success, failed)."""
        try:
            response = await self._post_with_retry(self._build_packed_payload(pack))
            content = response["choices"][0]["message"]["content"]
            results = self._parse_packed_response(content)
        except Exception as e:
            for article in pack:
                if article.id is not None:
                    self.db.save_ai_failure(article.id, str(e))
            return 0, len(pack)

        success = 0
        for article in pack:
            if article.id is None:
                continue
            ai_result = results.get(article.id)
            if ai_result is None:
                self.db.save_ai_failure(article.id, "Missing from packed AI response")
                continue
            self.db.save_ai_result(
                article_id=article.id,
                title_ja=ai_result.title_ja,
                summary_ja=ai_result.summary_ja,
                model=self.config.model,
                prompt_version=self.config.prompt_version,
                input_hash=self._compute_input_hash(article),
            )
            success += 1
        return success, len(pack) - success

    def _is_up_to_date(self, article: Article) -> bool:
        """True if the article was already enriched from the same input, model and prompt."""
        return (
            article.ai_status == "completed"
            and article.ai_input_hash == self._compute_input_hash(article)
            and article.ai_model == self.config.model
            and article.ai_prompt_version == self.config.prompt_version
        )

    async def _enrich_single_article(self, article: Article) -> bool:
        """Enrich a single article. Returns False if skipped (already up to date)."""
        # Skip if already completed with same input hash
        input_hash = self._compute_input_hash(article)
        if self._is_up_to_date(article):
            return False

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
        return True

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
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort

        return await self._post_with_retry(payload)

    async def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a chat/completions payload with retry logic."""
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
                    last_error = e
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

    def _build_packed_system_prompt(self) -> str:
        """System prompt for several articles in one request (output format overridden)."""
        return (
            self.config.system_prompt.rstrip()
            + "\n\n"
            + PACKED_PROMPT_SUFFIX
        )

    def _build_packed_user_prompt(self, articles: list[Article]) -> str:
        """Concatenate articles, each headed by its id marker."""
        blocks = [
            f"### id={article.id}\n{self._prepare_input_text(article)}" for article in articles
        ]
        return (
            f"Please process the following {len(articles)} articles and provide "
            "the Japanese title and summary of each in JSON format:\n\n" + "\n\n".join(blocks)
        )

    def _build_packed_payload(self, articles: list[Article]) -> dict[str, Any]:
        """Build a chat/completions payload covering all articles in the pack."""
        max_tokens = self.config.pack_max_tokens or self.config.max_tokens * len(articles)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": self._build_packed_system_prompt()},
                {"role": "user", "content": self._build_packed_user_prompt(articles)},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "packed_articles", "schema": PACKED_RESPONSE_SCHEMA},
            },
        }
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        return payload

    def _parse_packed_response(self, content: str) -> dict[int, AIResponse]:
        """Parse a packed response into {article_id: AIResponse}.

        Raises ValueError if the response as a whole is not valid JSON. Individual
        malformed entries are dropped, so only those articles end up failed.
        """
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        json_str = fenced.group(1) if fenced else content.strip()
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid packed AI response format: {e}") from e

        items = data.get("articles") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("Invalid packed AI response format: no 'articles' list")

        results: dict[int, AIResponse] = {}
        for item in items:
            try:
                article_id = int(item["id"])
                ai_result = AIResponse(title_ja=item["title_ja"], summary_ja=item["summary_ja"])
            except (TypeError, KeyError, ValueError, ValidationError):
                continue
            results.setdefault(article_id, ai_result)
        return results

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
