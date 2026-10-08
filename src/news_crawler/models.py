"""Data models for AI Scraper."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FetchMethod(StrEnum):
    """Supported fetch methods."""

    RSS = "rss"
    HTML = "html"
    PLAYWRIGHT = "playwright"
    GITHUB = "github"
    HUGGINGFACE = "huggingface"


class AIStatus(StrEnum):
    """AI processing status."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class AIResponse(BaseModel):
    """AI-generated response containing Japanese title and summary."""

    title_ja: str = Field(description="Japanese title")
    summary_ja: str = Field(description="Japanese summary")


class SourceConfig(BaseModel):
    """Configuration for a crawl target source."""

    key: str = Field(description="Unique identifier for the source")
    name: str = Field(description="Human readable name of the source")
    category: str = Field(default="general", description="Category (official, media, oss, etc.)")
    fetch_method: FetchMethod = Field(default=FetchMethod.RSS, description="Fetch method")
    enabled: bool = Field(default=True, description="Whether the source is active")
    base_url: str = Field(description="Base URL of the website")
    feed_url: str | None = Field(default=None, description="RSS/Atom feed URL")
    list_url: str | None = Field(default=None, description="Article list page URL")
    article_list_selector: str | None = Field(
        default=None, description="CSS selector for article links in list"
    )
    title_selector: str | None = Field(default=None, description="CSS selector for title")
    content_selector: str | None = Field(default=None, description="CSS selector for body")
    summary_selector: str | None = Field(default=None, description="CSS selector for summary")
    date_selector: str | None = Field(default=None, description="CSS selector for pub date")
    author_selector: str | None = Field(default=None, description="CSS selector for author")
    wait_selector: str | None = Field(
        default=None, description="Selector to wait for in Playwright"
    )
    headers: dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers")
    encoding: str | None = Field(
        default=None,
        description="Force a specific page encoding (e.g. 'shift_jis'). "
        "If omitted, encoding is detected from the response header/meta tags.",
    )


class Article(BaseModel):
    """Article item model."""

    id: int | None = Field(default=None, description="Database auto-increment ID")
    source_key: str = Field(description="Source identifier key")
    url: str = Field(description="Original URL")
    normalized_url: str = Field(description="Normalized canonical URL")
    title: str = Field(description="Article title")
    summary: str = Field(default="", description="Short summary or excerpt")
    content: str = Field(default="", description="Main body text / markdown")
    published_at: datetime | None = Field(default=None, description="Publication timestamp")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Crawl timestamp")
    content_hash: str = Field(default="", description="SHA-256 hash of title + content")
    category: str = Field(default="general", description="Source category")
    author: str | None = Field(default=None, description="Article author")
    tags: list[str] = Field(default_factory=list, description="Tags or keywords")
    title_ja: str = Field(default="", description="AI-generated Japanese title")
    summary_ja: str = Field(default="", description="AI-generated Japanese summary")
    ai_status: str = Field(default="pending", description="AI processing status")
    ai_input_hash: str = Field(default="", description="Hash of input used for AI processing")
    ai_model: str = Field(default="", description="AI model used for processing")
    ai_prompt_version: str = Field(default="1", description="Prompt version used")
    ai_processed_at: datetime | None = Field(default=None, description="AI processing timestamp")
    ai_error: str | None = Field(default=None, description="AI processing error message")
    duplicate_of_id: int | None = Field(
        default=None, description="ID of the canonical article this duplicates, if any"
    )
    duplicate_score: float | None = Field(
        default=None, description="Similarity score used to mark this as a duplicate"
    )


class CrawlResult(BaseModel):
    """Execution result for a single source crawl."""

    source_key: str
    status: str  # "success", "failed", "skipped"
    article_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    error_message: str | None = None
    duration_seconds: float = 0.0


class CrawlRun(BaseModel):
    """Overall execution summary for a crawl run."""

    id: int | None = None
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    total_sources: int = 0
    success_count: int = 0
    failed_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    status: str = "running"  # "success", "partial", "failed"
    results: list[CrawlResult] = Field(default_factory=list)


class SearchResult(BaseModel):
    """Full-text search result item."""

    article: Article
    snippet: str = ""
    rank: float = 0.0
