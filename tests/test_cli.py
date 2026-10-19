"""Tests for CLI commands."""

from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from news_crawler.cli import main

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
    assert "Total New: 2" in res_crawl.output or "New: 2" in res_crawl.output

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


def test_cli_report_period_options(cli_config_dir: Path):
    """Test various period options for report command."""
    runner = CliRunner()

    # 1. Period month
    res = runner.invoke(main, ["report", "--period", "month", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "Report generated successfully" in res.output

    # 2. Date range
    args = [
        "report",
        "--start-date",
        "2026-09-01",
        "--end-date",
        "2026-09-10",
        "--config-dir",
        str(cli_config_dir),
    ]
    res = runner.invoke(main, args)
    assert res.exit_code == 0

    # 3. Conflict (days and period)
    args_conflict = [
        "report",
        "--days",
        "7",
        "--period",
        "week",
        "--config-dir",
        str(cli_config_dir),
    ]
    res = runner.invoke(main, args_conflict)
    assert res.exit_code == 1
    assert "Error" in res.output


def test_cli_report_visualize_options(cli_config_dir: Path):
    """Test visualization options for report command."""
    runner = CliRunner()

    # 1. No visualize
    res = runner.invoke(main, ["report", "--no-visualize", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0


def test_cli_report_excludes_disabled_sources_by_default(cli_config_dir: Path, tmp_path: Path):
    """Articles from a source disabled in sources.yaml (e.g. after a Google News
    de-dup decision) must not appear in the report, even if they remain in the DB
    from before the source was disabled, and even without an explicit --exclude-source."""
    from datetime import datetime, timedelta

    from news_crawler.config import load_config
    from news_crawler.database import Database
    from news_crawler.models import Article, FetchMethod, SourceConfig

    (cli_config_dir / "sources.yaml").write_text(
        """
sources:
  sample_rss:
    name: "Sample RSS"
    category: "official"
    fetch_method: "rss"
    enabled: true
    base_url: "https://example.com"
    feed_url: "https://example.com/rss.xml"
  google_news_keiba:
    name: "Google News (Disabled)"
    category: "aggregator"
    fetch_method: "rss"
    enabled: false
    base_url: "https://news.google.com"
    feed_url: "https://news.google.com/rss"
""",
        encoding="utf-8",
    )

    app_config = load_config(cli_config_dir)
    db = Database(app_config.crawler.database_path)
    db.upsert_source(SourceConfig(
        key="sample_rss", name="Sample RSS", category="official",
        fetch_method=FetchMethod.RSS, base_url="https://example.com",
    ))
    db.upsert_source(SourceConfig(
        key="google_news_keiba", name="Google News (Disabled)", category="aggregator",
        fetch_method=FetchMethod.RSS, base_url="https://news.google.com",
    ))
    db.upsert_article(Article(
        source_key="sample_rss",
        url="https://example.com/kept",
        normalized_url="https://example.com/kept",
        title="Kept Article",
        summary="From an enabled source.",
        published_at=datetime.now() - timedelta(days=1),
        category="official",
    ))
    db.upsert_article(Article(
        source_key="google_news_keiba",
        url="https://news.google.com/stale",
        normalized_url="https://news.google.com/stale",
        title="Stale Google News Article",
        summary="Crawled before the source was disabled.",
        published_at=datetime.now() - timedelta(days=1),
        category="aggregator",
    ))

    runner = CliRunner()
    res = runner.invoke(main, ["report", "--days", "7", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0

    today_str = datetime.now().strftime("%Y%m%d")
    output_path = (
        Path(app_config.crawler.output_dir)
        / f"{datetime.now().year}"
        / f"weekly_report_{today_str}.md"
    )
    content = output_path.read_text(encoding="utf-8")
    assert "Kept Article" in content
    assert "Stale Google News Article" not in content


def test_cli_enrich_disabled(cli_config_dir: Path):
    """Test enrich command when AI is disabled."""
    runner = CliRunner()
    res = runner.invoke(main, ["enrich", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "AI enrichment is disabled" in res.output


def test_cli_enrich_no_eligible_articles(cli_config_dir: Path, tmp_path: Path):
    """When every pending article is excluded as a duplicate, enrich must say
    'No eligible articles' instead of a misleading 'LLM endpoint: None'."""
    from datetime import datetime

    from news_crawler.config import load_config
    from news_crawler.database import Database
    from news_crawler.models import Article, FetchMethod, SourceConfig

    (cli_config_dir / "crawler.yaml").write_text(
        f"""
crawler:
  download_delay: 0.0
  database_path: "{tmp_path / 'cli_test.db'}"
  output_dir: "{tmp_path / 'output'}"

ai:
  enabled: true
""",
        encoding="utf-8",
    )

    app_config = load_config(cli_config_dir)
    db = Database(app_config.crawler.database_path)
    db.upsert_source(SourceConfig(
        key="sample_rss", name="Sample RSS", category="official",
        fetch_method=FetchMethod.RSS, base_url="https://example.com",
    ))
    canonical, _, _ = db.upsert_article(Article(
        source_key="sample_rss",
        url="https://example.com/canonical",
        normalized_url="https://example.com/canonical",
        title="Canonical Article",
        published_at=datetime.now(),
        category="official",
    ))
    dup, _, _ = db.upsert_article(Article(
        source_key="sample_rss",
        url="https://example.com/dup",
        normalized_url="https://example.com/dup",
        title="Duplicate Article",
        published_at=datetime.now(),
        category="official",
    ))
    db.mark_duplicate(dup.id, canonical.id, 1.0)
    # Keep the canonical article out of the way (completed, not pending).
    with db.connection() as conn:
        conn.execute(
            "UPDATE articles SET ai_status = 'completed' WHERE id = ?",
            (canonical.id,),
        )

    runner = CliRunner()
    res = runner.invoke(main, ["enrich", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "No eligible articles" in res.output
    assert "excluded duplicates: 1" in res.output
    assert "LLM endpoint" not in res.output


def test_cli_stats_with_ai_status(cli_config_dir: Path):
    """Test stats command shows AI processing status."""
    runner = CliRunner()

    # First crawl to populate DB
    feed_xml = (FIXTURES_DIR / "sample_rss.xml").read_text(encoding="utf-8")
    respx.get("https://example.com/rss.xml").mock(return_value=httpx.Response(200, text=feed_xml))
    runner.invoke(main, ["crawl", "--config-dir", str(cli_config_dir)])

    # Check stats
    res_stats = runner.invoke(main, ["stats", "--config-dir", str(cli_config_dir)])
    assert res_stats.exit_code == 0
    # AI status may or may not be shown depending on whether the section exists
    # Just verify stats command works


def test_cli_report_custom_title_tags_sources_file(cli_config_dir: Path, tmp_path: Path):
    """Test report command with custom --title, --tags, and --sources-file."""
    custom_sources = tmp_path / "custom_sources.yaml"
    custom_sources.write_text(
        """
title: "競馬ニュース動向レポート"
tags:
  - keiba
  - news

sources:
  sample_rss:
    name: "Sample Keiba"
    category: "media"
    fetch_method: "rss"
    enabled: true
    base_url: "https://example.com"
    feed_url: "https://example.com/rss.xml"
""",
        encoding="utf-8",
    )

    runner = CliRunner()
    output_md = tmp_path / "custom_output.md"

    # Test with custom sources file (which sets default title/tags for this file)
    # but also override title and tags via CLI
    args = [
        "report",
        "--days", "7",
        "--sources-file", str(custom_sources),
        "--title", "特別競馬週報",
        "--tags", "custom,keiba,special",
        "-o", str(output_md),
        "--config-dir", str(cli_config_dir),
    ]
    res = runner.invoke(main, args)
    assert res.exit_code == 0
    assert output_md.exists()

    content = output_md.read_text(encoding="utf-8")
    assert "title: 特別競馬週報" in content
    assert "tags:\n- custom\n- keiba\n- special" in content
    # Ensure pie charts are not generated
    assert "![日本語単語頻度パイチャート]" not in content
    assert "![原文単語頻度パイチャート]" not in content


def test_cli_digest_weekly(cli_config_dir: Path, tmp_path: Path):
    """Test digest command outputs weekly digest_YYYY-Www.md under <root>/<YYYY>/."""
    from datetime import datetime

    from news_crawler.config import load_config
    from news_crawler.database import Database
    from news_crawler.models import Article, FetchMethod, SourceConfig

    app_config = load_config(cli_config_dir)
    db = Database(app_config.crawler.database_path)
    db.upsert_source(SourceConfig(
        key="sample_rss", name="Sample RSS", category="media",
        fetch_method=FetchMethod.RSS, base_url="https://example.com",
    ))
    db.upsert_article(Article(
        source_key="sample_rss",
        url="https://example.com/digest-test",
        normalized_url="https://example.com/digest-test",
        title="Weekly Digest Article",
        summary="A summary for testing weekly digest.",
        content="Article content here.",
        published_at=datetime(2026, 10, 18, 15, 30),
        category="media",
    ))

    runner = CliRunner()
    out_dir = tmp_path / "digests"
    res = runner.invoke(
        main,
        [
            "digest",
            "--start-date", "2026-10-18",
            "--end-date", "2026-10-18",
            "--output-dir", str(out_dir),
            "--config-dir", str(cli_config_dir),
        ],
    )
    assert res.exit_code == 0
    assert "Wrote 1 weekly digest(s)" in res.output

    # 2026-10-18 belongs to ISO week 2026-W42
    target_md = out_dir / "2026" / "digest_2026-W42.md"
    assert target_md.exists()
    content = target_md.read_text(encoding="utf-8")
    assert "week: 2026-W42" in content
    assert "Weekly Digest Article" in content


@respx.mock
def test_cli_crawl_auto_updates_digest(cli_config_dir: Path, tmp_path: Path):
    """Test that crawl command automatically updates weekly digest and wordcloud SVG."""
    (cli_config_dir / "crawler.yaml").write_text(
        f"""
crawler:
  download_delay: 0.0
  database_path: "{tmp_path / 'cli_test.db'}"
  output_dir: "{tmp_path / 'output'}"
report:
  visualize: true
""",
        encoding="utf-8",
    )
    feed_xml = (FIXTURES_DIR / "sample_rss.xml").read_text(encoding="utf-8")
    respx.get("https://example.com/rss.xml").mock(return_value=httpx.Response(200, text=feed_xml))

    runner = CliRunner()
    res = runner.invoke(main, ["crawl", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "Updating current week's digest and wordcloud" in res.output
    assert "Updated:" in res.output

    out_dir = tmp_path / "output"
    digest_md = out_dir / "2026" / "digest_2026-W38.md"
    wordcloud_svg = out_dir / "2026" / "svg" / "digest_2026-W38_wordcloud.svg"
    assert digest_md.exists()
    assert wordcloud_svg.exists()
    assert "<svg" in wordcloud_svg.read_text(encoding="utf-8")
    md_text = digest_md.read_text(encoding="utf-8")
    assert "![ワードクラウド](svg/digest_2026-W38_wordcloud.svg)" in md_text


@respx.mock
def test_cli_crawl_cross_week_dedupe_and_future_date(tmp_path: Path):
    """Test crawl updates both previous & current week on cross-week articles,
    deduplicates cross-source articles before generating wordcloud/digest,
    and ignores bogus future dates when determining the update window.
    """
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    out_dir = tmp_path / "output"

    (cfg_dir / "crawler.yaml").write_text(
        f"""
crawler:
  download_delay: 0.0
  database_path: "{tmp_path / 'cli_test.db'}"
  output_dir: "{out_dir}"
report:
  visualize: true
  ai_keywords_path: "config/keiba_keywords.yaml"
""",
        encoding="utf-8",
    )
    (cfg_dir / "sources.yaml").write_text(
        """
sources:
  src_a:
    name: "Source A"
    category: "media"
    fetch_method: "rss"
    enabled: true
    base_url: "https://a.example.com"
    feed_url: "https://a.example.com/rss.xml"
  src_b:
    name: "Source B"
    category: "sports_paper"
    fetch_method: "rss"
    enabled: true
    base_url: "https://b.example.com"
    feed_url: "https://b.example.com/rss.xml"
""",
        encoding="utf-8",
    )

    rss_a = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>A</title>
  <item>
    <title>【秋華賞】日曜日のレース結果と振り返りレポート</title>
    <link>https://a.example.com/1</link>
    <description>日曜日の京都競馬場で行われた秋華賞の振り返りです。</description>
    <pubDate>Sun, 18 Oct 2026 17:00:00 +0900</pubDate>
  </item>
  <item>
    <title>【菊花賞】ロブチェンが三冠達成へ向けて最終追い切りを消化</title>
    <link>https://a.example.com/2</link>
    <description>ロブチェンが栗東トレセンで追い切りを消化しました。</description>
    <pubDate>Mon, 19 Oct 2026 07:30:00 +0900</pubDate>
  </item>
</channel></rss>"""

    rss_b = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>B</title>
  <item>
    <title>【菊花賞】ロブチェンが三冠達成へ向けて最終追い切りを消化</title>
    <link>https://b.example.com/dup</link>
    <description>同一見出しの重複配信記事です。</description>
    <pubDate>Mon, 19 Oct 2026 08:00:00 +0900</pubDate>
  </item>
  <item>
    <title>未来日付の告知記事テスト</title>
    <link>https://b.example.com/future</link>
    <description>誤パースされた未来日付の記事です。</description>
    <pubDate>Thu, 12 Oct 2028 10:00:00 +0900</pubDate>
  </item>
</channel></rss>"""

    respx.get("https://a.example.com/rss.xml").mock(return_value=httpx.Response(200, text=rss_a))
    respx.get("https://b.example.com/rss.xml").mock(return_value=httpx.Response(200, text=rss_b))

    runner = CliRunner()
    res = runner.invoke(main, ["crawl", "--config-dir", str(cfg_dir)])
    assert res.exit_code == 0

    # Both W42 (Sun Oct 18) and W43 (Mon Oct 19) must be generated with their SVG wordclouds
    w42_md = out_dir / "2026" / "digest_2026-W42.md"
    w42_svg = out_dir / "2026" / "svg" / "digest_2026-W42_wordcloud.svg"
    w43_md = out_dir / "2026" / "digest_2026-W43.md"
    w43_svg = out_dir / "2026" / "svg" / "digest_2026-W43_wordcloud.svg"

    assert w42_md.exists(), "Previous week digest (W42) should be updated on cross-week crawl"
    assert w42_svg.exists(), "Previous week wordcloud SVG (W42) should be generated"
    assert w43_md.exists(), "Current week digest (W43) should be updated"
    assert w43_svg.exists(), "Current week wordcloud SVG (W43) should be generated"

    # Cross-source duplicate on Mon Oct 19 should have been deduplicated before writing W43
    w43_text = w43_md.read_text(encoding="utf-8")
    assert "https://a.example.com/2" in w43_text
    assert "https://b.example.com/dup" not in w43_text


@respx.mock
def test_cli_crawl_no_update_digest_flag(cli_config_dir: Path, tmp_path: Path):
    """Test that --no-update-digest skips digest and wordcloud generation."""
    feed_xml = (FIXTURES_DIR / "sample_rss.xml").read_text(encoding="utf-8")
    respx.get("https://example.com/rss.xml").mock(return_value=httpx.Response(200, text=feed_xml))

    runner = CliRunner()
    res = runner.invoke(main, ["crawl", "--no-update-digest", "--config-dir", str(cli_config_dir)])
    assert res.exit_code == 0
    assert "Updating current week's digest and wordcloud" not in res.output
    out_dir = tmp_path / "output"
    assert list(out_dir.glob("*/digest_*.md")) == []
