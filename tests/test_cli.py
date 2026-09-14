"""Tests for CLI commands."""

from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from ai_scraper.cli import main

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def cli_config_dir(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    (cfg_dir / "crawler.yaml").write_text(
        f"""
crawler:
  download_delay: 0.0
  database_path: "{tmp_path / 'cli_test.db'}"
  output_dir: "{tmp_path / 'output'}"
""",
        encoding="utf-8",
    )

    (cfg_dir / "sources.yaml").write_text(
        """
sources:
  sample_rss:
    name: "Sample RSS"
    category: "official"
    fetch_method: "rss"
    enabled: true
    base_url: "https://example.com"
    feed_url: "https://example.com/rss.xml"
""",
        encoding="utf-8",
    )
    return cfg_dir


def test_cli_list_sources(cli_config_dir: Path):
    runner = CliRunner()
    res = runner.invoke(main, ["list-sources", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "Sample RSS" in res.output
    assert "sample_rss" in res.output


@respx.mock
def test_cli_crawl_and_search_and_report(cli_config_dir: Path):
    feed_xml = (FIXTURES_DIR / "sample_rss.xml").read_text(encoding="utf-8")
    respx.get("https://example.com/rss.xml").mock(return_value=httpx.Response(200, text=feed_xml))

    runner = CliRunner()

    # 1. Crawl
    res_crawl = runner.invoke(main, ["crawl", "--config-dir", str(cli_config_dir)])
    assert res_crawl.exit_code == 0
    assert "Completed: Total New: 2" in res_crawl.output

    # 2. Search
    res_search = runner.invoke(main, ["search", "Next-Gen", "--config-dir", str(cli_config_dir)])
    assert res_search.exit_code == 0
    assert "Introducing Next-Gen AI Model" in res_search.output

    # 3. Stats
    res_stats = runner.invoke(main, ["stats", "--config-dir", str(cli_config_dir)])
    assert res_stats.exit_code == 0
    assert "Total Articles: 2" in res_stats.output

    # 4. Report
    res_report = runner.invoke(main, ["report", "--days", "7", "--config-dir", str(cli_config_dir)])
    assert res_report.exit_code == 0
    assert "Report generated successfully" in res_report.output
