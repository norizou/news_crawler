"""Configuration loader and schema validator."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from news_crawler.models import SourceConfig

# Load environment variables from .env file
load_dotenv()


DEFAULT_SYSTEM_PROMPT = (
    "You are a professional translator and summarizer. "
    "Your task is to process the provided article and output a JSON object "
    "with the following structure:\n\n"
    "{\n"
    '  "title_ja": "Japanese translation of the article title",\n'
    '  "summary_ja": "Japanese summary of the article content (2-3 sentences)"\n'
    "}\n\n"
    "Important guidelines:\n"
    "- Translate the title accurately to Japanese\n"
    "- Create a concise summary in Japanese (2-3 sentences)\n"
    "- If the article is already in Japanese, still provide a refined Japanese summary\n"
    "- Output ONLY the JSON object, no additional text\n"
    "- Ensure the JSON is valid and properly formatted"
)


class CrawlerConfig(BaseModel):
    """Crawler engine configuration."""

    download_delay: float = Field(default=2.0, description="Delay between requests in seconds")
    concurrent_requests: int = Field(default=4, description="Max concurrent requests")
    user_agent: str = Field(
        default="News-Crawler/1.0 (+https://gitlab.dell.com/asain/news_crawler)",
        description="User-Agent string",
    )
    timeout_seconds: int = Field(default=30, description="HTTP request timeout")
    retry_times: int = Field(default=3, description="Retry attempts for failed requests")
    retry_http_codes: list[int] = Field(
        default_factory=lambda: [500, 502, 503, 504, 408, 429],
        description="HTTP status codes to retry",
    )
    database_path: str = Field(default="data/articles.db", description="Path to SQLite database")
    output_dir: str = Field(default="output", description="Output directory for reports")


class AIEndpoint(BaseModel):
    """One OpenAI-compatible LLM endpoint that enrichment can use."""

    name: str = Field(description="Identifier used in ai.endpoint_order and --llm")
    proxy_url: str = Field(description="OpenAI-compatible base URL (…/v1)")
    model: str = Field(description="Model ID")
    api_key_env: str | None = Field(
        default=None, description="Env var holding the API key (Bearer). None = no auth"
    )
    max_tokens: int | None = Field(
        default=None, description="Override ai.max_tokens for this endpoint"
    )


class AIConfig(BaseModel):
    """AI enrichment configuration."""

    enabled: bool = Field(default=False, description="Enable AI enrichment")
    proxy_url: str = Field(
        default="http://localhost:11434/v1",
        description="AIA Proxy base URL (OpenAI-compatible)",
    )
    model: str = Field(default="llama-3-3-70b-instruct", description="AI model to use")
    request_interval: float = Field(default=2.0, description="Delay between AI requests in seconds")
    timeout_seconds: int = Field(default=60, description="AI request timeout")
    max_retries: int = Field(default=3, description="Max retry attempts for AI requests")
    max_input_chars: int = Field(default=8000, description="Max input characters per article")
    max_articles_per_run: int = Field(
        default=50, description="Max articles to process per enrich run"
    )
    max_tokens: int = Field(
        default=500,
        description="Max completion tokens (reasoning models need more, incl. thinking tokens)",
    )
    endpoints: list[AIEndpoint] = Field(
        default_factory=list,
        description="Selectable LLM endpoints. Empty = single endpoint from proxy_url/model",
    )
    endpoint_order: list[str] = Field(
        default_factory=list,
        description="Endpoint names tried in order; the first reachable one is used",
    )
    prompt_version: str = Field(default="1", description="Prompt version identifier")
    system_prompt: str = Field(
        default=DEFAULT_SYSTEM_PROMPT,
        description="System prompt for AI translation and summarization",
    )

    @field_validator("system_prompt")
    @classmethod
    def validate_system_prompt(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("system_prompt must not be empty")
        return v

    @field_validator("request_interval")
    @classmethod
    def validate_interval(cls, v: float) -> float:
        if v < 0:
            raise ValueError("request_interval must be non-negative")
        return v

    @field_validator("max_input_chars")
    @classmethod
    def validate_max_input(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_input_chars must be positive")
        return v

    @field_validator("max_articles_per_run")
    @classmethod
    def validate_max_articles(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_articles_per_run must be positive")
        return v


class ReportConfig(BaseModel):
    """Report and visualization configuration."""

    title: str = Field(default="News Trend Report", description="Report title prefix")
    title_template: str = Field(default="{title} ({date})", description="Template for report title")
    tags: list[str] = Field(
        default_factory=lambda: ["news", "report"], description="Report frontmatter tags"
    )
    output_dir: str | None = Field(
        default=None, description="Output directory for reports (overrides crawler.output_dir)"
    )
    visualize: bool = Field(default=False, description="Enable visualization")
    top_n: int = Field(default=20, description="Top N words for frequency chart")
    japanese_font_path: str = Field(default="", description="Path to Japanese font file")
    output_assets_name: str = Field(default="assets", description="Name of assets directory")
    wordcloud_width: int = Field(default=800, description="Word cloud width")
    wordcloud_height: int = Field(default=400, description="Word cloud height")
    ai_keywords_path: str = Field(
        default="config/ai_keywords.yaml", description="Path to AI keywords filter file"
    )
    sudachi_config_path: str = Field(
        default="config/sudachi.json", description="Path to Sudachi config JSON (user dictionaries)"
    )
    crowns_path: str = Field(
        default="config/keiba_crowns.csv", description="Path to approved horse-name crown CSV"
    )


class AppConfig(BaseModel):
    """Full application configuration."""

    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)


def load_config(
    config_dir: str | Path = "config",
    sources_file: str | Path | None = None,
) -> AppConfig:
    """Load and parse crawler, AI, and sources configurations from YAML files."""
    base_path = Path(config_dir)

    # 1. Load crawler config
    crawler_cfg = CrawlerConfig()
    crawler_file = base_path / "crawler.yaml"
    if crawler_file.exists():
        with open(crawler_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "crawler" in data:
                crawler_cfg = CrawlerConfig(**data["crawler"])
            elif data:
                crawler_cfg = CrawlerConfig(**data)

    # 2. Load AI config
    ai_cfg = AIConfig()
    report_cfg = ReportConfig()
    if crawler_file.exists():
        with open(crawler_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "ai" in data:
                ai_cfg = AIConfig(**data["ai"])
            if "report" in data:
                report_cfg = ReportConfig(**data["report"])

    # Environment variable overrides for AI config
    ai_cfg.proxy_url = os.getenv("AIA_PROXY_URL", ai_cfg.proxy_url)
    ai_cfg.model = os.getenv("AIA_MODEL", ai_cfg.model)

    # 3. Load sources config
    sources_dict: dict[str, SourceConfig] = {}
    target_sources_path: Path | None = None

    if sources_file is not None:
        p = Path(sources_file)
        if p.exists():
            target_sources_path = p
        elif (base_path / sources_file).exists():
            target_sources_path = base_path / sources_file
        else:
            target_sources_path = p
    else:
        default_path = base_path / "sources.yaml"
        if default_path.exists():
            target_sources_path = default_path
        else:
            target_sources_path = base_path / "sources.example.yaml"

    if target_sources_path and target_sources_path.exists():
        with open(target_sources_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

            # Override report title/tags if defined at top-level of sources file
            if isinstance(data, dict):
                if "title" in data and isinstance(data["title"], str):
                    report_cfg.title = data["title"]
                if "tags" in data and isinstance(data["tags"], list):
                    report_cfg.tags = [str(t) for t in data["tags"]]
                if "title_template" in data and isinstance(data["title_template"], str):
                    report_cfg.title_template = data["title_template"]

            raw_sources = data.get("sources", data) if isinstance(data, dict) else data
            if isinstance(raw_sources, dict):
                for key, item in raw_sources.items():
                    if isinstance(item, dict):
                        # Ensure key is injected if missing
                        if "key" not in item:
                            item["key"] = key
                        sources_dict[key] = SourceConfig(**item)
            elif isinstance(raw_sources, list):
                for item in raw_sources:
                    if isinstance(item, dict) and "key" in item:
                        sources_dict[item["key"]] = SourceConfig(**item)

    return AppConfig(crawler=crawler_cfg, ai=ai_cfg, report=report_cfg, sources=sources_dict)
