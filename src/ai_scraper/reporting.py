"""Markdown report generator."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ai_scraper.database import Database
from ai_scraper.models import Article


def generate_markdown_report(
    db: Database,
    days: int = 7,
    category: str | None = None,
    output_path: str | Path | None = None,
) -> str:
    """Generate a structured Markdown report of recent articles."""
    articles = db.get_recent_articles(days=days, category=category, limit=500)
    now = datetime.now()
    start_date = now - timedelta(days=days)

    # Group by category and source
    by_category: dict[str, dict[str, list[Article]]] = defaultdict(lambda: defaultdict(list))
    for art in articles:
        by_category[art.category][art.source_key].append(art)

    lines: list[str] = []

    # FrontMatter
    lines.append("---")
    lines.append(f"title: AI Trend Weekly Report ({now.strftime('%Y-%m-%d')})")
    lines.append(f"created: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"period: {start_date.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}")
    lines.append(f"total_articles: {len(articles)}")
    lines.append("tags:")
    lines.append("  - ai")
    lines.append("  - report")
    lines.append("  - trends")
    lines.append("---")
    lines.append("")
    lines.append(
        f"期間: **{start_date.strftime('%Y-%m-%d')}** 〜 **{now.strftime('%Y-%m-%d')}** "
        f"（直近 {days} 日間）に収集された AI 関連ニュース・動向レポートです。"
    )
    lines.append("")

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
                lines.append(f"#### {idx}. [{art.title}]({art.url})")
                lines.append("")
                lines.append(f"- **公開日**: `{date_str}`")
                if art.author:
                    lines.append(f"- **著者**: {art.author}")
                if art.summary:
                    summary_clean = art.summary.replace("\n", " ")[:300]
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
