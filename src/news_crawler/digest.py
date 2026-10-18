"""Weekly digest: one Markdown file per ISO week (Monday-Sunday), managed per year."""

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from news_crawler.config import ReportConfig
from news_crawler.models import Article
from news_crawler.reporting import render_wordcloud_section

_WEEKDAY_NAMES_JA = ["月", "火", "水", "木", "金", "土", "日"]


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


def week_monday(d: date) -> date:
    """Return the Monday of the week that date ``d`` belongs to."""
    return d - timedelta(days=d.weekday())


def week_id(d: date) -> str:
    """Return the ISO week string (e.g. '2026-W42') for date ``d``."""
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def group_by_week(
    articles: list[Article],
) -> dict[tuple[int, int, date], list[Article]]:
    """Group articles by (iso_year, iso_week, monday_date)."""
    grouped: dict[tuple[int, int, date], list[Article]] = defaultdict(list)
    for a in articles:
        d = article_date(a)
        monday = week_monday(d)
        iso_year, iso_week, _ = monday.isocalendar()
        grouped[(iso_year, iso_week, monday)].append(a)
    return grouped


def _clock(article: Article) -> str:
    ts = article.published_at
    if ts is None or article_date(article) != ts.date():
        return "--:--"
    return ts.strftime("%H:%M")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_week(
    monday: date,
    articles: list[Article],
    source_names: dict[str, str],
    *,
    output_path: Path | None = None,
    report_config: ReportConfig | None = None,
    only_summarized: bool = False,
) -> str:
    """Render one week's articles as Markdown with original word cloud and daily breakdown."""
    sunday = monday + timedelta(days=6)
    iso_year, iso_week, _ = monday.isocalendar()
    wk_str = f"{iso_year}-W{iso_week:02d}"

    items = [a for a in articles if not only_summarized or a.ai_status == "completed"]
    done = [a for a in items if a.ai_status == "completed"]
    models = sorted({a.ai_model for a in done if a.ai_model})

    front = {
        "title": f"競馬ニュース {wk_str} ({monday.isoformat()} ~ {sunday.isoformat()})",
        "week": wk_str,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "total_articles": len(items),
        "ai_summarized": len(done),
        "tags": ["keiba", "news", "weekly"],
        "llm_models": models,
    }

    lines = [
        "---",
        yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip(),
        "---",
        "",
        f"{wk_str}（{monday.isoformat()} 〜 {sunday.isoformat()}）の競馬ニュース "
        f"{len(items)} 件（AI要約済み {len(done)} 件）。",
        "",
    ]

    # Word cloud section (original text only)
    if output_path is not None and report_config is not None and report_config.visualize:
        wc_lines = render_wordcloud_section(items, output_path, report_config)
        if wc_lines:
            lines.extend(wc_lines)

    # Summary Table (collected article breakdown)
    if items:
        by_category: dict[str, dict[str, list[Article]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for a in items:
            by_category[a.category][a.source_key].append(a)

        lines.append("## 📊 収集サマリー")
        lines.append("")
        lines.append("| カテゴリ | ソース数 | 記事数 |")
        lines.append("| --- | --- | --- |")
        for cat, sources in sorted(by_category.items()):
            art_count = sum(len(arts) for arts in sources.values())
            lines.append(f"| **{cat}** | {len(sources)} | {art_count} |")
        lines.append(
            f"| **合計** | **{sum(len(s) for s in by_category.values())}** | **{len(items)}** |"
        )
        lines.append("")

    # Group items by publication date within the week (newest date first)
    by_day: dict[date, list[Article]] = defaultdict(list)
    for a in items:
        by_day[article_date(a)].append(a)

    for day in sorted(by_day.keys(), reverse=True):
        day_items = by_day[day]
        wd_ja = _WEEKDAY_NAMES_JA[day.weekday()]
        lines.append(f"## {day.isoformat()}（{wd_ja}）（{len(day_items)} 件）")
        lines.append("")

        # Group by source (most articles first)
        by_source: dict[str, list[Article]] = defaultdict(list)
        for a in day_items:
            by_source[a.source_key].append(a)

        for key in sorted(by_source, key=lambda k: (-len(by_source[k]), k)):
            group = sorted(
                by_source[key], key=lambda a: (_clock(a), a.id or 0), reverse=True
            )
            lines.append(f"### {source_names.get(key, key)}（{len(group)}）")
            lines.append("")
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
    report_config: ReportConfig | None = None,
    week_range: tuple[date, date] | None = None,
) -> tuple[list[tuple[Path, int]], list[str]]:
    """Write ``<root>/<YYYY>/digest_<YYYY>-W<ww>.md`` for every week present. Idempotent.

    Returns ``(written, skipped_weeks)``. A week outside ``week_range`` (inclusive Mondays)
    is skipped: such a week can only hold the few articles that were re-dated by ``article_date``
    (e.g. a bogus future ``published_at`` mapped to its fetch date), so writing it would overwrite
    that week's complete digest with a fragment.
    """
    written: list[tuple[Path, int]] = []
    skipped: list[str] = []

    grouped = group_by_week(articles)
    for (iso_year, iso_week, monday), items in sorted(grouped.items()):
        wk_str = f"{iso_year}-W{iso_week:02d}"
        if week_range is not None and not week_range[0] <= monday <= week_range[1]:
            skipped.append(wk_str)
            continue
        if only_summarized:
            items = [a for a in items if a.ai_status == "completed"]
            if not items:
                continue

        path = root / f"{iso_year}" / f"digest_{wk_str}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = render_week(
            monday,
            items,
            source_names,
            output_path=path,
            report_config=report_config,
            only_summarized=only_summarized,
        )
        path.write_text(body, encoding="utf-8")
        written.append((path, len(items)))
    return written, skipped
