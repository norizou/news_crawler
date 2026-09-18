"""Cross-source duplicate detection for articles covering the same underlying story.

Current strategy: exact match on normalized title (see
``normalizer.normalize_title_for_dedupe``) across different sources within a
time window. This is intentionally simple because in practice, independent
outlets republishing the same press release/wire text tend to use an
identical title.

``duplicate_score`` is stored per match (currently always 1.0 for an exact
title match) so that a future embedding- or content-similarity-based matcher
can populate the same column with a fractional score without a schema change.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from news_crawler.database import Database
from news_crawler.models import Article
from news_crawler.normalizer import normalize_title_for_dedupe

DEFAULT_MIN_TITLE_LEN = 15
EXACT_TITLE_MATCH_SCORE = 1.0


@dataclass
class DuplicateMatch:
    """A single duplicate -> canonical pairing detected by run_dedupe."""

    canonical: Article
    duplicate: Article
    score: float


def _article_sort_key(article: Article) -> tuple[datetime, int]:
    """Sort key for picking the canonical article: earliest publish time wins.

    ``published_at`` may be timezone-aware or naive depending on the source
    (e.g. netkeiba's RSS dates carry a +09:00 offset while HTML-scraped dates
    are naive JST). Comparing them directly raises TypeError, so aware values
    are normalized to naive by dropping tzinfo before sorting.
    """
    pub = article.published_at or article.fetched_at
    if pub.tzinfo is not None:
        pub = pub.replace(tzinfo=None)
    return (pub, article.id or 0)


def find_duplicate_matches(
    articles: list[Article],
    min_title_len: int = DEFAULT_MIN_TITLE_LEN,
) -> list[DuplicateMatch]:
    """Group articles by normalized title and pick a canonical per cross-source cluster.

    Only clusters spanning at least two distinct ``source_key`` values are
    considered (same-source duplicates are already handled by URL/content-hash
    dedup in ``Database.upsert_article``).
    """
    groups: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        norm_title = normalize_title_for_dedupe(article.title)
        if len(norm_title) >= min_title_len:
            groups[norm_title].append(article)

    matches: list[DuplicateMatch] = []
    for group in groups.values():
        if len({a.source_key for a in group}) < 2:
            continue
        ordered = sorted(group, key=_article_sort_key)
        canonical, *duplicates = ordered
        for duplicate in duplicates:
            matches.append(
                DuplicateMatch(
                    canonical=canonical,
                    duplicate=duplicate,
                    score=EXACT_TITLE_MATCH_SCORE,
                )
            )
    return matches


def run_dedupe(
    db: Database,
    start_at: datetime,
    end_at: datetime,
    dry_run: bool = True,
    min_title_len: int = DEFAULT_MIN_TITLE_LEN,
) -> list[DuplicateMatch]:
    """Detect and (unless dry_run) persist cross-source duplicate clusters.

    Idempotent: any prior duplicate marking within the range is cleared before
    recomputation, so this can be re-run freely (e.g. after fixing a source's
    date extraction) without accumulating stale state.
    """
    articles = db.get_articles_in_range(start_at, end_at)
    matches = find_duplicate_matches(articles, min_title_len=min_title_len)

    if not dry_run:
        db.reset_duplicates_in_range(start_at, end_at)
        for match in matches:
            if match.duplicate.id is None or match.canonical.id is None:
                continue
            db.mark_duplicate(match.duplicate.id, match.canonical.id, match.score)

    return matches
