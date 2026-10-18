"""Markdown report generator with word frequency visualization."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from news_crawler.config import ReportConfig
from news_crawler.database import Database
from news_crawler.models import Article
from news_crawler.text_analyzer import TextAnalyzer
from news_crawler.visualization import VisualizationGenerator


def resolve_report_period(
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str | None = None,
    base_now: datetime | None = None
) -> tuple[datetime, datetime, str]:
    """
    Resolve start and end datetimes based on provided criteria.
    Returns (start_at, end_at, mode).
    """
    now = base_now or datetime.now()

    # Check for conflicts
    specified = [x for x in [days is not None, start_date is not None, period is not None] if x]
    if len(specified) > 1:
        raise ValueError(
            "Only one of 'days', 'date range' (start/end), or 'period' can be specified."
        )

    if start_date or end_date:
        if not start_date:
            raise ValueError("start-date is required when using date range.")
        try:
            start_at = datetime.fromisoformat(start_date)
            # Up to the end of the specified day
            end_at = datetime.fromisoformat(end_date) + timedelta(days=1) if end_date else now
        except ValueError as e:
            raise ValueError(f"Invalid date format (use YYYY-MM-DD): {e}") from e

        if start_at > end_at:
            raise ValueError("start-date must be before end-date.")
        return start_at, end_at, "date_range"

    if period:
        if period == "week":
            d = 7
        elif period == "month":
            d = 30
        elif period == "quarter":
            d = 90
        else:
            raise ValueError(f"Invalid period: {period}. Use 'week', 'month', or 'quarter'.")
        start_at = now - timedelta(days=d)
        return start_at, now, period

    # Default to days
    d = days if days is not None else 7
    if d <= 0:
        raise ValueError("days must be a positive integer.")
    start_at = now - timedelta(days=d)
    return start_at, now, "days"


def render_wordcloud_section(
    articles: list[Article],
    output_md_path: str | Path,
    report_config: ReportConfig | None = None,
) -> list[str]:
    """Generate original-text word cloud SVG and return Markdown section lines."""
    cfg = report_config or ReportConfig()
    if not cfg.visualize or not articles:
        return []

    md_path = Path(output_md_path)
    assets_dir = md_path.parent / cfg.output_assets_name
    assets_dir.mkdir(parents=True, exist_ok=True)

    analyzer = TextAnalyzer(
        keywords_path=cfg.ai_keywords_path,
        sudachi_config_path=cfg.sudachi_config_path,
        crowns_path=cfg.crowns_path,
    )
    viz = VisualizationGenerator(font_path=cfg.japanese_font_path)

    orig_texts = [f"{a.title} {a.summary} {a.content}" for a in articles]
    freq = analyzer.analyze_japanese(orig_texts) or analyzer.analyze_english(orig_texts)

    lines: list[str] = ["## 📈 単語頻度分析", ""]
    if freq:
        wc_filename = f"{md_path.stem}_wordcloud.svg"
        wc_path = assets_dir / wc_filename
        if viz.generate_wordcloud(
            freq, wc_path, width=cfg.wordcloud_width, height=cfg.wordcloud_height
        ):
            rel_link = f"{cfg.output_assets_name}/{wc_filename}"
            lines.append(f"![ワードクラウド]({rel_link})")
            lines.append("")
        else:
            lines.append("ワードクラウドの生成に失敗しました。")
            lines.append("")
    else:
        lines.append("分析対象となるコンテンツがありませんでした。")
        lines.append("")

    return lines


def generate_markdown_report(
    db: Database,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str | None = None,
    category: str | None = None,
    exclude_source_keys: list[str] | None = None,
    exclude_duplicates: bool = False,
    output_path: str | Path | None = None,
    report_config: ReportConfig | None = None,
) -> str:
    """Generate a structured Markdown report of articles with visualizations."""
    cfg = report_config or ReportConfig()

    start_at, end_at, mode = resolve_report_period(days, start_date, end_date, period)

    articles = db.get_articles_in_range(
        start_at,
        end_at,
        category=category,
        exclude_source_keys=exclude_source_keys,
        exclude_duplicates=exclude_duplicates,
    )

    # Group by category and source
    by_category: dict[str, dict[str, list[Article]]] = defaultdict(lambda: defaultdict(list))
    for art in articles:
        by_category[art.category][art.source_key].append(art)

    lines: list[str] = []

    # FrontMatter
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    title_vars = {
        "title": cfg.title,
        "date": date_str,
        "mode": mode,
        "period_start": start_at.strftime("%Y-%m-%d"),
        "period_end": (end_at - timedelta(seconds=1)).strftime("%Y-%m-%d"),
    }
    try:
        report_title = cfg.title_template.format(**title_vars)
    except (KeyError, ValueError):
        report_title = f"{cfg.title} ({date_str})"

    frontmatter = {
        "title": report_title,
        "created": now.strftime("%Y-%m-%d %H:%M:%S"),
        "period": (
            f"{start_at.strftime('%Y-%m-%d')} ~ "
            f"{(end_at - timedelta(seconds=1)).strftime('%Y-%m-%d')}"
        ),
        "total_articles": len(articles),
        "tags": cfg.tags,
        "visualization": cfg.visualize,
        "analysis_category": category,
        "period_mode": mode,
        "period_start": start_at.strftime("%Y-%m-%d"),
        "period_end": (end_at - timedelta(seconds=1)).strftime("%Y-%m-%d"),
    }

    lines.append("---")
    lines.append(yaml.dump(frontmatter, allow_unicode=True, sort_keys=False).strip())
    lines.append("---")
    lines.append("")
    lines.append(
        f"期間: **{start_at.strftime('%Y-%m-%d')}** 〜 "
        f"**{(end_at - timedelta(seconds=1)).strftime('%Y-%m-%d')}** "
        f"に収集されたニュース動向レポートです。"
    )
    lines.append("")

    # Visualizations
    if cfg.visualize and output_path and articles:
        lines.extend(render_wordcloud_section(articles, output_path, cfg))

    # Summary Table
    lines.append("## 📊 収集サマリー")
    lines.append("")
    lines.append("| カテゴリ | ソース数 | 記事数 |")
    lines.append("| --- | --- | --- |")

    category_counts = {}
    for cat, sources in sorted(by_category.items()):
        art_count = sum(len(arts) for arts in sources.values())
        category_counts[cat] = art_count
        lines.append(f"| **{cat}** | {len(sources)} | {art_count} |")

    lines.append(
        f"| **合計** | **{sum(len(s) for s in by_category.values())}** | **{len(articles)}** |"
    )
    lines.append("")

    # Category Breakdown
    for cat, sources in sorted(by_category.items()):
        cat_title = cat.upper() if len(cat) <= 4 else cat.capitalize()
        lines.append(f"## 📁 カテゴリ: {cat_title}")
        lines.append("")

        for source_key, arts in sorted(sources.items()):
            lines.append(f"### 🌐 {source_key.upper()} ({len(arts)} 件)")
            lines.append("")

            for idx, art in enumerate(arts, 1):
                date_str = art.published_at.strftime("%Y-%m-%d") if art.published_at else "日付不明"

                display_title = (
                    art.title_ja
                    if art.title_ja and art.ai_status == "completed"
                    else art.title
                )
                display_summary = (
                    art.summary_ja
                    if art.summary_ja and art.ai_status == "completed"
                    else art.summary
                )

                lines.append(f"#### {idx}. [{display_title}]({art.url})")
                lines.append("")
                lines.append(f"- **公開日**: `{date_str}`")
                if art.author:
                    lines.append(f"- **著者**: {art.author}")
                if display_summary:
                    summary_clean = display_summary.replace("\n", " ")[:300]
                    lines.append(f"- **要約**: {summary_clean}")
                lines.append("")

    if not articles:
        lines.append("指定された期間内に収集された新着記事はありませんでした。")
        lines.append("")

    content = "\n".join(lines)

    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

    return content
