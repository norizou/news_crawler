"""Configuration loader and schema validator."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_scraper.models import SourceConfig


class CrawlerConfig(BaseModel):
    """Crawler engine configuration."""

    download_delay: float = Field(default=2.0, description="Delay between requests in seconds")
    concurrent_requests: int = Field(default=4, description="Max concurrent requests")
    user_agent: str = Field(
        default="AI-Scraper/1.0 (+https://gitlab.dell.com/asain/ai_scraper)",
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


class AppConfig(BaseModel):
    """Full application configuration."""

    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)


def load_config(config_dir: str | Path = "config") -> AppConfig:
    """Load and parse crawler and sources configurations from YAML files."""
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

    # 2. Load sources config (fallback to sources.example.yaml if sources.yaml not found)
    sources_dict: dict[str, SourceConfig] = {}
    sources_file = base_path / "sources.yaml"
    if not sources_file.exists():
        sources_file = base_path / "sources.example.yaml"

    if sources_file.exists():
        with open(sources_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            raw_sources = data.get("sources", data)
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

    return AppConfig(crawler=crawler_cfg, sources=sources_dict)
