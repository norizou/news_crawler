from datetime import date, datetime, timedelta, timezone

from news_crawler.config import ReportConfig, SourceConfig
from news_crawler.database import Database
from news_crawler.digest import (
    article_date,
    get_latest_valid_article_date,
    render_week,
    week_monday,
    write_digests,
)
from news_crawler.models import Article

JST = timezone(timedelta(hours=9))


def _art(**kw):
    base = dict(
        source_key="netkeiba",
        url="https://example.com/1",
        normalized_url="https://example.com/1",
        title="原題",
        summary="要約",
        content="本文",
        fetched_at=datetime(2026, 9, 20, 1, 0),
        published_at=datetime(2026, 9, 19, 20, 50, tzinfo=JST),
    )
    base.update(kw)
    return Article(**base)


def test_article_date_uses_published_date():
    assert article_date(_art()).isoformat() == "2026-09-19"


def test_article_date_falls_back_when_missing_or_future():
    assert article_date(_art(published_at=None)).isoformat() == "2026-09-20"
    bogus = _art(published_at=datetime(2028, 8, 9))
    assert article_date(bogus).isoformat() == "2026-09-20"


def test_week_monday():
    assert week_monday(date(2026, 9, 19)) == date(2026, 9, 14)  # Saturday -> Monday
    assert week_monday(date(2026, 9, 14)) == date(2026, 9, 14)  # Monday -> Monday
    assert week_monday(date(2026, 9, 20)) == date(2026, 9, 14)  # Sunday -> Monday


def test_render_week_shows_summary_and_daily_sections():
    done = _art(
        title_ja="日本語題", summary_ja="要約です。", ai_status="completed", ai_model="m1"
    )
    pend = _art(url="https://example.com/2", title="未処理")
    monday = date(2026, 9, 14)
    out = render_week(monday, [done, pend], {"netkeiba": "netkeiba"})

    assert "ai_summarized: 1" in out and "total_articles: 2" in out
    assert "week: 2026-W38" in out
    assert "## 📊 収集サマリー" in out
    assert "| **general** | 1 | 2 |" in out
    assert "## 2026-09-19（土）（2 件）" in out
    assert "### netkeiba（2）" in out
    assert "[日本語題](https://example.com/1)" in out
    assert "  - 要約です。" in out
    assert out.count("  - ") == 1  # pending article has no summary line


def test_write_digests_layout_and_only_summarized(tmp_path):
    done = _art(title_ja="題", summary_ja="要約", ai_status="completed", ai_model="m1")
    other = _art(
        url="https://example.com/3",
        fetched_at=datetime(2026, 9, 21, 10, 0),
        published_at=datetime(2026, 9, 21, 9, 0, tzinfo=JST),  # belongs to W39
    )
    written, _ = write_digests([done, other], tmp_path, {}, only_summarized=True)
    assert [p.relative_to(tmp_path).as_posix() for p, _ in written] == [
        "2026/digest_2026-W38.md"
    ]

    written, _ = write_digests([done, other], tmp_path, {})
    assert sorted(p.name for p, _ in written) == [
        "digest_2026-W38.md",
        "digest_2026-W39.md",
    ]


def test_write_digests_skips_weeks_outside_range_and_keeps_existing_file(tmp_path):
    """A re-dated article (bogus future date -> fetch week) must not clobber that week's file."""
    existing = tmp_path / "2026" / "digest_2026-W38.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("COMPLETE DIGEST", encoding="utf-8")

    bogus = _art(
        published_at=datetime(2028, 8, 9),
        fetched_at=datetime(2026, 9, 19, 12, 0),  # re-dates to W38
    )
    # week_range specifies year 2028 week only
    written, skipped = write_digests(
        [bogus],
        tmp_path,
        {},
        week_range=(date(2028, 8, 7), date(2028, 8, 7)),
    )
    assert written == [] and skipped == ["2026-W38"]
    assert existing.read_text(encoding="utf-8") == "COMPLETE DIGEST"


def test_write_digests_writes_weeks_inside_range(tmp_path):
    written, skipped = write_digests(
        [_art()],
        tmp_path,
        {},
        week_range=(date(2026, 9, 14), date(2026, 9, 14)),
    )
    assert [p.name for p, _ in written] == ["digest_2026-W38.md"] and skipped == []


def test_write_digests_with_visualization(tmp_path):
    keywords_file = tmp_path / "test_keywords.yaml"
    keywords_file.write_text("""
ai_keywords:
  - 競馬
  - 秋華賞
  - 展望
stopwords_general:
  - 記事
""", encoding="utf-8")

    art = _art(
        title="秋華賞の競馬展望",
        summary="秋華賞の有力馬情報",
        content="詳細な秋華賞解説記事",
    )
    cfg = ReportConfig(visualize=True, ai_keywords_path=str(keywords_file))
    written, _ = write_digests([art], tmp_path, {}, report_config=cfg)
    assert len(written) == 1
    md_path, _ = written[0]
    content = md_path.read_text(encoding="utf-8")
    assert "## 📈 単語頻度分析" in content
    assert "![ワードクラウド](svg/digest_2026-W38_wordcloud.svg)" in content

    svg_file = tmp_path / "2026" / "svg" / "digest_2026-W38_wordcloud.svg"
    assert svg_file.exists()


def test_get_latest_valid_article_date(tmp_path):
    """Test get_latest_valid_article_date handles ISO 'T' timestamps,
    future dates, and disabled sources.
    """
    db = Database(tmp_path / "test_latest.db")
    for key in ("netkeiba", "disabled_src"):
        db.upsert_source(
            SourceConfig(
                key=key,
                name=key,
                category="media",
                fetch_method="rss",
                enabled=(key != "disabled_src"),
                base_url="https://example.com",
            )
        )

    today = datetime.now().replace(hour=0, minute=5, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    future = today + timedelta(days=365)

    # Yesterday's article
    db.upsert_article(
        _art(
            url="https://example.com/yest",
            normalized_url="https://example.com/yest",
            published_at=yesterday,
        )
    )
    # Today's early morning article (00:05) — regression test for 'T' vs space comparison
    db.upsert_article(
        _art(
            url="https://example.com/today",
            normalized_url="https://example.com/today",
            published_at=today,
        )
    )
    # Bogus future article — must be ignored
    db.upsert_article(
        _art(
            url="https://example.com/future",
            normalized_url="https://example.com/future",
            published_at=future,
        )
    )
    # Disabled source article on tomorrow — must be excluded when exclude_source_keys passed
    db.upsert_article(
        _art(
            source_key="disabled_src",
            url="https://example.com/disabled",
            normalized_url="https://example.com/disabled",
            published_at=today + timedelta(days=1),
        )
    )

    latest = get_latest_valid_article_date(db, exclude_source_keys=["disabled_src"])
    assert latest == today.date()

