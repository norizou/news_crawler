from datetime import date, datetime, timedelta, timezone

from news_crawler.digest import article_date, render_day, write_digests
from news_crawler.models import Article

JST = timezone(timedelta(hours=9))


def _art(**kw):
    base = dict(
        source_key="netkeiba",
        url="https://example.com/1",
        normalized_url="https://example.com/1",
        title="原題",
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


def test_render_day_shows_summary_only_for_completed():
    done = _art(title_ja="日本語題", summary_ja="要約です。", ai_status="completed",
                ai_model="m1")
    pend = _art(url="https://example.com/2", title="未処理")
    out = render_day(done.published_at.date(), [done, pend], {"netkeiba": "netkeiba"})
    assert "ai_summarized: 1" in out and "total_articles: 2" in out
    assert "[日本語題](https://example.com/1)" in out
    assert "  - 要約です。" in out
    assert out.count("  - ") == 1  # pending article has no summary line


def test_write_digests_layout_and_only_summarized(tmp_path):
    done = _art(title_ja="題", summary_ja="要約", ai_status="completed", ai_model="m1")
    other = _art(url="https://example.com/3", published_at=datetime(2026, 9, 18, 9, 0, tzinfo=JST))
    written, _ = write_digests([done, other], tmp_path, {}, only_summarized=True)
    assert [p.relative_to(tmp_path).as_posix() for p, _ in written] == ["2026/2026-09-19.md"]
    written, _ = write_digests([done, other], tmp_path, {})
    assert sorted(p.name for p, _ in written) == ["2026-09-18.md", "2026-09-19.md"]


def test_write_digests_skips_days_outside_range_and_keeps_existing_file(tmp_path):
    """A re-dated article (bogus future date -> fetch day) must not clobber that day's file."""
    existing = tmp_path / "2026" / "2026-09-19.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("COMPLETE DIGEST", encoding="utf-8")
    bogus = _art(published_at=datetime(2028, 8, 9), fetched_at=datetime(2026, 9, 19, 12, 0))
    written, skipped = write_digests(
        [bogus], tmp_path, {}, day_range=(date(2028, 8, 9), date(2028, 8, 9))
    )
    assert written == [] and skipped == [date(2026, 9, 19)]
    assert existing.read_text(encoding="utf-8") == "COMPLETE DIGEST"


def test_write_digests_writes_days_inside_range(tmp_path):
    written, skipped = write_digests(
        [_art()], tmp_path, {}, day_range=(date(2026, 9, 19), date(2026, 9, 19))
    )
    assert [p.name for p, _ in written] == ["2026-09-19.md"] and skipped == []
