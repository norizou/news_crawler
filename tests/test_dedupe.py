"""Tests for cross-source duplicate detection."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from news_crawler.database import Database
from news_crawler.dedupe import find_duplicate_matches, run_dedupe
from news_crawler.models import Article, FetchMethod, SourceConfig
from news_crawler.normalizer import normalize_title_for_dedupe


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test_articles.db")


@pytest.fixture
def sources() -> list[SourceConfig]:
    return [
        SourceConfig(
            key=key,
            name=key,
            category="media",
            fetch_method=FetchMethod.HTML,
            base_url="https://example.com",
        )
        for key in ("netkeiba", "sponichi", "sanspo")
    ]


def make_article(
    source_key: str,
    url: str,
    title: str,
    published_at: datetime | None,
    content: str = "本文",
) -> Article:
    return Article(
        source_key=source_key,
        url=url,
        normalized_url=url,
        title=title,
        content=content,
        published_at=published_at,
        category="media",
    )


class TestNormalizeTitleForDedupe:
    def test_strips_whitespace(self):
        assert normalize_title_for_dedupe("大井競馬 ゴールドジュニア") == "大井競馬ゴールドジュニア"

    def test_full_width_space(self):
        assert normalize_title_for_dedupe("大井競馬　ゴールドジュニア") == "大井競馬ゴールドジュニア"

    def test_nfkc_normalizes_width(self):
        # Full-width alphanumerics collapse to half-width under NFKC.
        assert normalize_title_for_dedupe("Ｖ３重賞初制覇") == normalize_title_for_dedupe("V3重賞初制覇")

    def test_empty_title(self):
        assert normalize_title_for_dedupe("") == ""


class TestFindDuplicateMatches:
    def test_real_duplicate_pair_detected(self):
        # Actual titles observed across netkeiba and sponichi for the same event.
        title = "【大井競馬　ゴールドジュニア】牝馬パープルフォッグ　無傷V3重賞初制覇　最後まで余力あり"
        earlier = make_article(
            "netkeiba", "https://netkeiba.example/1", title,
            datetime(2026, 9, 17, 21, 26, 39, tzinfo=timezone(timedelta(hours=9))),
        )
        later = make_article(
            "sponichi", "https://sponichi.example/1", title,
            datetime(2026, 9, 18, 1, 23, 32),
        )
        earlier.id, later.id = 1, 2

        matches = find_duplicate_matches([earlier, later])

        assert len(matches) == 1
        assert matches[0].canonical.source_key == "netkeiba"
        assert matches[0].duplicate.source_key == "sponichi"
        assert matches[0].score == 1.0

    def test_distinct_articles_not_matched(self):
        a = make_article(
            "netkeiba", "https://netkeiba.example/1",
            "【セントライト記念】鞍上が中山トライアルでまた一仕事！ジャスティンシカゴが重賞初V！",
            datetime(2026, 9, 16),
        )
        b = make_article(
            "sponichi", "https://sponichi.example/1",
            "【神戸新聞杯】ロブチェン　威風堂々の秋初戦　春から格段にパワフル",
            datetime(2026, 9, 17),
        )
        a.id, b.id = 1, 2

        assert find_duplicate_matches([a, b]) == []

    def test_same_source_not_matched(self):
        # Same-source duplicates are already handled by URL/content-hash dedup upstream;
        # find_duplicate_matches should not double-handle them.
        title = "中山馬主協会の西川賢会長が熊谷千葉県知事を訪問　豪雨被害の「千葉県を助けよう」という気持ちで"
        a = make_article("netkeiba", "https://netkeiba.example/1", title, datetime(2026, 9, 17))
        b = make_article("netkeiba", "https://netkeiba.example/2", title, datetime(2026, 9, 17))
        a.id, b.id = 1, 2

        assert find_duplicate_matches([a, b]) == []

    def test_short_title_below_threshold_ignored(self):
        # Generic short titles (e.g. recurring column names) should not match
        # across sources just by coincidence.
        a = make_article("netkeiba", "https://netkeiba.example/1", "重賞見どころ", datetime(2026, 9, 17))
        b = make_article("sponichi", "https://sponichi.example/1", "重賞見どころ", datetime(2026, 9, 18))
        a.id, b.id = 1, 2

        assert find_duplicate_matches([a, b], min_title_len=15) == []

    def test_mixed_aware_and_naive_datetimes_do_not_crash(self):
        title = "中山馬主協会の西川賢会長が熊谷千葉県知事を訪問　豪雨被害の「千葉県を助けよう」という気持ちで"
        aware = make_article(
            "netkeiba", "https://netkeiba.example/1", title,
            datetime(2026, 9, 17, 19, 43, 40, tzinfo=timezone(timedelta(hours=9))),
        )
        naive = make_article(
            "sponichi", "https://sponichi.example/1", title,
            datetime(2026, 9, 18, 1, 23, 33, 993702),
        )
        aware.id, naive.id = 1, 2

        matches = find_duplicate_matches([aware, naive])

        assert len(matches) == 1
        assert matches[0].canonical.source_key == "netkeiba"


class TestRunDedupe:
    def test_persists_and_is_idempotent(self, temp_db: Database, sources: list[SourceConfig]):
        for src in sources:
            temp_db.upsert_source(src)

        title = "【大井競馬　ゴールドジュニア】牝馬パープルフォッグ　無傷V3重賞初制覇　最後まで余力あり"
        canonical, _, _ = temp_db.upsert_article(
            make_article("netkeiba", "https://netkeiba.example/1", title, datetime(2026, 9, 17, 21, 0))
        )
        duplicate, _, _ = temp_db.upsert_article(
            make_article("sponichi", "https://sponichi.example/1", title, datetime(2026, 9, 18, 1, 0))
        )

        start = datetime(2026, 9, 1)
        end = datetime(2026, 9, 30)

        matches = run_dedupe(temp_db, start, end, dry_run=False)
        assert len(matches) == 1

        refreshed_dup = temp_db.get_articles_in_range(start, end)
        dup_row = next(a for a in refreshed_dup if a.id == duplicate.id)
        canon_row = next(a for a in refreshed_dup if a.id == canonical.id)
        assert dup_row.duplicate_of_id == canonical.id
        assert dup_row.duplicate_score == 1.0
        assert canon_row.duplicate_of_id is None

        # Re-running should not error and should keep the same result (idempotent).
        matches_again = run_dedupe(temp_db, start, end, dry_run=False)
        assert len(matches_again) == 1

    def test_dry_run_does_not_persist(self, temp_db: Database, sources: list[SourceConfig]):
        for src in sources:
            temp_db.upsert_source(src)

        title = "【大井競馬　ゴールドジュニア】牝馬パープルフォッグ　無傷V3重賞初制覇　最後まで余力あり"
        temp_db.upsert_article(
            make_article("netkeiba", "https://netkeiba.example/1", title, datetime(2026, 9, 17, 21, 0))
        )
        temp_db.upsert_article(
            make_article("sponichi", "https://sponichi.example/1", title, datetime(2026, 9, 18, 1, 0))
        )

        start = datetime(2026, 9, 1)
        end = datetime(2026, 9, 30)
        matches = run_dedupe(temp_db, start, end, dry_run=True)
        assert len(matches) == 1

        for a in temp_db.get_articles_in_range(start, end):
            assert a.duplicate_of_id is None

    def test_pending_articles_exclude_marked_duplicates(
        self, temp_db: Database, sources: list[SourceConfig]
    ):
        for src in sources:
            temp_db.upsert_source(src)

        title = "【大井競馬　ゴールドジュニア】牝馬パープルフォッグ　無傷V3重賞初制覇　最後まで余力あり"
        temp_db.upsert_article(
            make_article("netkeiba", "https://netkeiba.example/1", title, datetime(2026, 9, 17, 21, 0))
        )
        temp_db.upsert_article(
            make_article("sponichi", "https://sponichi.example/1", title, datetime(2026, 9, 18, 1, 0))
        )

        start = datetime(2026, 9, 1)
        end = datetime(2026, 9, 30)
        run_dedupe(temp_db, start, end, dry_run=False)

        pending = temp_db.get_pending_articles(days=365, limit=50)
        assert len(pending) == 1
        assert pending[0].source_key == "netkeiba"
