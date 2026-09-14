"""Tests for configuration loading."""

from pathlib import Path

from ai_scraper.config import load_config
from ai_scraper.models import FetchMethod


def test_load_config_default(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    # Write crawler.yaml
    (cfg_dir / "crawler.yaml").write_text(
        """
crawler:
  download_delay: 3.0
  concurrent_requests: 2
  database_path: "data/custom.db"
""",
        encoding="utf-8",
    )

    # Write sources.yaml
    (cfg_dir / "sources.yaml").write_text(
        """
sources:
  openai:
    name: "OpenAI News"
    category: "official"
    fetch_method: "rss"
    base_url: "https://openai.com"
    feed_url: "https://openai.com/news/rss.xml"
""",
        encoding="utf-8",
    )

    app_config = load_config(cfg_dir)
    assert app_config.crawler.download_delay == 3.0
    assert app_config.crawler.concurrent_requests == 2
    assert "openai" in app_config.sources
    assert app_config.sources["openai"].fetch_method == FetchMethod.RSS
    assert app_config.sources["openai"].name == "OpenAI News"
