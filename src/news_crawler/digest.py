"""Daily digest: one Markdown file per publication date, managed per year."""

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from news_crawler.models import Article


def article_date(article: Article) -> date:
    """Date an article belongs to.

    Falls back to the fetch date when ``published_at`` is missing or lies after the
    fetch time (a scraper occasionally parses a bogus future date, e.g. year 2028).
    """
    pub = article.published_at
    if pub is None:
        return article.fetched_at.date()
    if pub.replace(tzinfo=None) > article.fetched_at.replace(tzinfo=None) + timedelta(days=1):
        return article.fetched_at.date()
    return pub.date()


def group_by_date(articles: list[Article]) -> dict[date, list[Article]]:
    grouped: dict[date, list[Article]] = defaultdict(list)
    for a in articles:
        grouped[article_date(a)].append(a)
    return grouped


def _clock(article: Article) -> str:
    ts = article.published_at
    if ts is None or article_date(article) != ts.date():
        return "--:--"
    return ts.strftime("%H:%M")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_day(
    day: date,
    articles: list[Article],
    source_names: dict[str, str],
    *,
    only_summarized: bool = False,
) -> str:
    """Render one day's articles as Markdown (grouped by source, newest first)."""
    items = [a for a in articles if not only_summarized or a.ai_status == "completed"]
    done = [a for a in items if a.ai_status == "completed"]
    models = sorted({a.ai_model for a in done if a.ai_model})
    front = {
        "title": f"競馬ニュース {day.isoformat()}",
        "date": day.isoformat(),
        "total_articles": len(items),
        "ai_summarized": len(done),
        "tags": ["keiba", "news", "daily"],
        "llm_models": models,
    }
    lines = [
        "---",
        yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip(),
        "---",
        "",
        f"{day.isoformat()} の競馬ニュース {len(items)} 件（AI要約済み {len(done)} 件）。",
        "",
    ]
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in items:
        by_source[a.source_key].append(a)
    for key in sorted(by_source, key=lambda k: (-len(by_source[k]), k)):
        group = sorted(by_source[key], key=lambda a: (_clock(a), a.id or 0), reverse=True)
        lines += [f"## {source_names.get(key, key)}（{len(group)}）", ""]
        for a in group:
            title = _one_line(a.title_ja or a.title)
            lines.append(f"- **{_clock(a)}** [{title}]({a.url})")
            if a.ai_status == "completed" and a.summary_ja:
                lines.append(f"  - {_one_line(a.summary_ja)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_digests(
    articles: list[Article],
    root: Path,
    source_names: dict[str, str],
    *,
    only_summarized: bool = False,
    day_range: tuple[date, date] | None = None,
) -> tuple[list[tuple[Path, int]], list[date]]:
    """Write ``<root>/<YYYY>/<YYYY-MM-DD>.md`` for every date present. Idempotent.

    Returns ``(written, skipped_days)``. A day outside ``day_range`` (inclusive) is skipped:
    such a day can only hold the few articles that were re-dated by ``article_date`` (e.g. a
    bogus future ``published_at`` mapped to its fetch date), so writing it would overwrite
    that day's complete digest with a fragment.
    """
    written: list[tuple[Path, int]] = []
    skipped: list[date] = []
    for day, items in sorted(group_by_date(articles).items()):
        if day_range is not None and not day_range[0] <= day <= day_range[1]:
            skipped.append(day)
            continue
        body = render_day(day, items, source_names, only_summarized=only_summarized)
        if only_summarized and not any(a.ai_status == "completed" for a in items):
            continue
        path = root / f"{day.year}" / f"{day.isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written.append((path, len(items)))
    return written, skipped
