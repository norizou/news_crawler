"""Minimal REST client for the Gemini Batch API (batchGenerateContent, inline requests)."""

from dataclasses import dataclass, field
from typing import Any

import httpx

# A batch job's state when it has not reported one yet
STATE_UNKNOWN = "JOB_STATE_UNSPECIFIED"


@dataclass
class BatchResult:
    """One inline result: the request's metadata key and its text or error."""

    key: str | None
    text: str | None = None
    error: str | None = None


@dataclass
class BatchStatus:
    """State of a batch job, plus its inline results once it has succeeded."""

    name: str
    state: str
    results: list[BatchResult] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON-schema subset to Gemini's responseSchema (upper-case type names)."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: to_gemini_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = to_gemini_schema(value)
        else:
            out[key] = value
    return out


def build_request(
    system_prompt: str,
    user_prompt: str,
    *,
    max_output_tokens: int,
    response_schema: dict[str, Any] | None = None,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Build one GenerateContentRequest for an inline batch entry."""
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if response_schema is not None:
        generation_config["responseSchema"] = to_gemini_schema(response_schema)
    return {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": generation_config,
    }


def parse_status(data: dict[str, Any]) -> BatchStatus:
    """Parse a batch resource or its Operation wrapper.

    The live API wraps the job in an Operation (``.metadata.state`` =
    ``BATCH_STATE_*``). The docs also show it bare (``.state``, ``.dest.inlinedResponses``) and
    wrapped in an Operation (``.metadata.state``, ``.response.inlinedResponses``),
    and the results list both flat and nested one level deeper, so accept all.
    """
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    state = data.get("state") or meta.get("state") or STATE_UNKNOWN
    # The live API reports BATCH_STATE_*; the guide (and our DB) use JOB_STATE_*.
    if state.startswith("BATCH_STATE_"):
        state = "JOB_STATE_" + state.removeprefix("BATCH_STATE_")
    name = data.get("name") or meta.get("name") or ""

    container: Any = None
    for source in (data, meta, data.get("response") or {}):
        for key in ("dest", "output"):
            if isinstance(source.get(key), dict) and "inlinedResponses" in source[key]:
                container = source[key]["inlinedResponses"]
                break
        if container is None and "inlinedResponses" in source:
            container = source["inlinedResponses"]
        if container is not None:
            break
    if isinstance(container, dict):
        container = container.get("inlinedResponses", [])

    results = [_parse_result(item) for item in container or []]
    return BatchStatus(name=name, state=state, results=results, raw=data)


def _parse_result(item: dict[str, Any]) -> BatchResult:
    key = (item.get("metadata") or {}).get("key")
    if item.get("error"):
        err = item["error"]
        return BatchResult(key=key, error=str(err.get("message", err)))
    candidates = (item.get("response") or {}).get("candidates") or []
    if not candidates:
        return BatchResult(key=key, error="No candidates in response")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        reason = candidates[0].get("finishReason", "empty")
        return BatchResult(key=key, error=f"Empty response ({reason})")
    return BatchResult(key=key, text=text)


class GeminiBatchClient:
    """Submits inline batch jobs and polls them."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    async def submit(
        self, model: str, requests: list[tuple[str, dict[str, Any]]], display_name: str
    ) -> BatchStatus:
        """Create a batch from (key, GenerateContentRequest) pairs."""
        body = {
            "batch": {
                "display_name": display_name,
                "input_config": {
                    "requests": {
                        "requests": [
                            {"request": req, "metadata": {"key": key}} for key, req in requests
                        ]
                    }
                },
            }
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/models/{model}:batchGenerateContent",
                headers=self._headers(),
                json=body,
            )
        _raise_for_status(r)
        return parse_status(r.json())

    async def get(self, name: str) -> BatchStatus:
        """Fetch a batch job (``batches/...``) with its current state and results."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}/{name}", headers=self._headers())
        _raise_for_status(r)
        status = parse_status(r.json())
        if not status.name:
            status.name = name
        return status


def _raise_for_status(r: httpx.Response) -> None:
    """raise_for_status, with the API's error message (never the key) in the exception."""
    if r.is_success:
        return
    try:
        message = r.json().get("error", {}).get("message", r.text)
    except ValueError:
        message = r.text
    raise httpx.HTTPStatusError(
        f"Gemini Batch API {r.status_code}: {message[:500]}", request=r.request, response=r
    )
