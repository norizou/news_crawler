"""Markdown report generator with word frequency visualization."""

import yaml
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from ai_scraper.database import Database
from ai_scraper.models import Article
from ai_scraper.text_analyzer import TextAnalyzer
from ai_scraper.visualization import VisualizationGenerator
from ai_scraper.config import ReportConfig


def resolve_report_period(
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: Optional[str] = None,
    base_now: Optional[datetime] = None
) -> Tuple[datetime, datetime, str]:
    """
    Resolve start and end datetimes based on provided criteria.
    Returns (start_at, end_at, mode).
    """
    now = base_now or datetime.now()
    
    # Check for conflicts
    specified = [x for x in [days is not None, start_date is not None, period is not None] if x]
    if len(specified) > 1:
        raise ValueError("Only one of 'days', 'date range' (start/end), or 'period' can be specified.")

    if start_date or end_date:
        if not start_date:
            raise ValueError("start-date is required when using date range.")
        try:
            start_at = datetime.fromisoformat(start_date)
            if end_date:
                # Up to the end of the specified day
                end_at = datetime.fromisoformat(end_date) + timedelta(days=1)
            else:
                end_at = now
            
            if start_at > end_at:
                raise ValueError("start-date must be before end-date.")
            return start_at, end_at, "date_range"
        except ValueError as e:
            raise ValueError(f"Invalid date format (use YYYY-MM-DD): {e}")

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


def generate_markdown_report(
    db: Database,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: Optional[str] = None,
    category: Optional[str] = None,
    output_path: Optional[str | Path] = None,
    report_config: Optional[ReportConfig] = None,
) -> str:
    """Generate a structured Markdown report of articles with visualizations."""
    cfg = report_config or ReportConfig()
    
    start_at, end_at, mode = resolve_report_period(days, start_date, end_date, period)
    
    articles = db.get_articles_in_range(start_at, end_at, category=category)
    
    # Group by category and source
    by_category: dict[str, dict[str, list[Article]]] = defaultdict(lambda: defaultdict(list))
    for art in articles:
        by_category[art.category][art.source_key].append(art)

    lines: list[str] = []

    # FrontMatter
    now = datetime.now()
    frontmatter = {
        "title": f"AI Trend Report ({now.strftime('%Y-%m-%d')})",
        "created": now.strftime("%Y-%m-%d %H:%M:%S"),
        "period": f"{start_at.strftime('%Y-%m-%d')} ~ {(end_at - timedelta(seconds=1)).strftime('%Y-%m-%d')}",
        "total_articles": len(articles),
        "tags": ["ai", "report", "trends"],
        "visualization": cfg.visualize,
        "analysis_top_n": cfg.top_n,
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
        f"期間: **{start_at.strftime('%Y-%m-%d')}** 〜 **{(end_at - timedelta(seconds=1)).strftime('%Y-%m-%d')}** "
        f"に収集された AI 関連ニュース・動向レポートです。"
    )
    lines.append("")

    # Visualizations
    if cfg.visualize and output_path and articles:
        lines.append("## 📈 単語頻度分析")
        lines.append("")
        
        assets_dir = Path(output_path).parent / f"{Path(output_path).stem}_{cfg.output_assets_name}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        
        analyzer = TextAnalyzer(keywords_path=cfg.ai_keywords_path)
        viz = VisualizationGenerator(font_path=cfg.japanese_font_path)
        
        # Japanese analysis
        ja_texts = [
            f"{a.title_ja} {a.summary_ja}" 
            for a in articles 
            if a.ai_status == "completed" and (a.title_ja or a.summary_ja)
        ]
        if ja_texts:
            ja_freq = analyzer.analyze_japanese(ja_texts)
            if ja_freq:
                wc_path = assets_dir / "wordcloud_ja.png"
                pie_path = assets_dir / "frequency_ja.png"
                
                if viz.generate_wordcloud(ja_freq, wc_path, width=cfg.wordcloud_width, height=cfg.wordcloud_height):
                    lines.append("### 日本語ワードクラウド")
                    lines.append(f"![日本語ワードクラウド]({assets_dir.name}/{wc_path.name})")
                    lines.append("")
                
                if viz.generate_pie_chart(ja_freq, pie_path, top_n=cfg.top_n, title="日本語頻出単語 (Top N)"):
                    lines.append(f"### 日本語 Top {cfg.top_n} 単語分布")
                    lines.append(f"![日本語単語頻度パイチャート]({assets_dir.name}/{pie_path.name})")
                    lines.append("")
        else:
            lines.append("### 日本語分析")
            lines.append("分析対象となる日本語コンテンツ（AI処理済み記事）がありませんでした。")
            lines.append("")

        # Original analysis
        en_texts = [f"{a.title} {a.summary} {a.content}" for a in articles]
        en_freq = analyzer.analyze_english(en_texts)
        if en_freq:
            wc_path = assets_dir / "wordcloud_original.png"
            pie_path = assets_dir / "frequency_original.png"
            
            if viz.generate_wordcloud(en_freq, wc_path, width=cfg.wordcloud_width, height=cfg.wordcloud_height):
                lines.append("### 原文ワードクラウド")
                lines.append(f"![原文ワードクラウド]({assets_dir.name}/{wc_path.name})")
                lines.append("")
            
            if viz.generate_pie_chart(en_freq, pie_path, top_n=cfg.top_n, title="原文頻出単語 (Top N)"):
                lines.append(f"### 原文 Top {cfg.top_n} 単語分布")
                lines.append(f"![原文単語頻度パイチャート]({assets_dir.name}/{pie_path.name})")
                lines.append("")
        else:
            lines.append("### 原文分析")
            lines.append("分析対象となる原文コンテンツがありませんでした。")
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
