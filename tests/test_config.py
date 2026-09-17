"""Tests for configuration loading."""

from pathlib import Path

import pytest

from news_crawler.config import load_config
from news_crawler.models import FetchMethod


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
  deepseek_hf:
    name: "DeepSeek (Hugging Face)"
    category: "chinese_official"
    fetch_method: "huggingface"
    base_url: "https://huggingface.co/deepseek-ai"
""",
        encoding="utf-8",
    )

    app_config = load_config(cfg_dir)
    assert app_config.crawler.download_delay == 3.0
    assert app_config.crawler.concurrent_requests == 2
    assert "openai" in app_config.sources
    assert app_config.sources["openai"].fetch_method == FetchMethod.RSS
    assert app_config.sources["openai"].name == "OpenAI News"
    assert app_config.sources["deepseek_hf"].fetch_method == FetchMethod.HUGGINGFACE
    assert app_config.sources["deepseek_hf"].category == "chinese_official"


def test_load_config_with_ai_section(tmp_path: Path):
    """Test loading configuration with AI section."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
crawler:
  download_delay: 2.0
ai:
  enabled: true
  proxy_url: "http://localhost:11434/v1"
  model: "test-model"
  request_interval: 1.5
  max_input_chars: 5000
""",
        encoding="utf-8",
    )

    app_config = load_config(cfg_dir)
    assert app_config.ai.enabled is True
    assert app_config.ai.proxy_url == "http://localhost:11434/v1"
    assert app_config.ai.model == "test-model"
    assert app_config.ai.request_interval == 1.5
    assert app_config.ai.max_input_chars == 5000


def test_ai_config_defaults(tmp_path: Path):
    """Test AI configuration defaults when section is missing."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
crawler:
  download_delay: 2.0
""",
        encoding="utf-8",
    )

    app_config = load_config(cfg_dir)
    assert app_config.ai.enabled is False
    assert app_config.ai.proxy_url == "http://localhost:11434/v1"
    assert app_config.ai.model == "llama-3-3-70b-instruct"
    assert app_config.ai.request_interval == 2.0
    assert app_config.ai.max_input_chars == 8000
    assert "You are a professional translator and summarizer." in app_config.ai.system_prompt


def test_load_config_with_custom_system_prompt(tmp_path: Path):
    """Test loading configuration with custom system prompt."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  system_prompt: |
    Custom system prompt for testing.
""",
        encoding="utf-8",
    )

    app_config = load_config(cfg_dir)
    assert app_config.ai.system_prompt == "Custom system prompt for testing.\n"


def test_ai_config_validation_empty_system_prompt(tmp_path: Path):
    """Test that empty or whitespace-only system prompt is rejected."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  system_prompt: "   "
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        load_config(cfg_dir)


def test_ai_config_validation_interval_negative(tmp_path: Path):
    """Test that negative request interval is rejected."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  request_interval: -1.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="request_interval must be non-negative"):
        load_config(cfg_dir)


def test_ai_config_validation_max_input_zero(tmp_path: Path):
    """Test that zero max input chars is rejected."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  max_input_chars: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_input_chars must be positive"):
        load_config(cfg_dir)


def test_ai_config_validation_max_articles_zero(tmp_path: Path):
    """Test that zero max articles per run is rejected."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  max_articles_per_run: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_articles_per_run must be positive"):
        load_config(cfg_dir)


def test_ai_config_environment_override(tmp_path: Path, monkeypatch):
    """Test that environment variables override AI configuration."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        """
ai:
  proxy_url: "http://default:11434/v1"
  model: "default-model"
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("AIA_PROXY_URL", "http://override:11434/v1")
    monkeypatch.setenv("AIA_MODEL", "override-model")

    app_config = load_config(cfg_dir)
    assert app_config.ai.proxy_url == "http://override:11434/v1"
    assert app_config.ai.model == "override-model"


def test_report_config_crowns_path_default_and_custom(tmp_path: Path):
    """Test crowns_path default and custom override in report config."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "crawler.yaml").write_text("", encoding="utf-8")

    default_cfg = load_config(cfg_dir)
    assert default_cfg.report.crowns_path == "config/keiba_crowns.csv"

    (cfg_dir / "crawler.yaml").write_text(
        """
report:
  crowns_path: "config/custom_crowns.csv"
""",
        encoding="utf-8",
    )
    custom_cfg = load_config(cfg_dir)
    assert custom_cfg.report.crowns_path == "config/custom_crowns.csv"
