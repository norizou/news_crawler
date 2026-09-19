"""Command-line interface for AI Scraper."""

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from news_crawler.ai_processor import AIProcessor
from news_crawler.config import load_config
from news_crawler.coordinator import CrawlCoordinator
from news_crawler.database import Database
from news_crawler.dedupe import DEFAULT_MIN_TITLE_LEN, run_dedupe
from news_crawler.reporting import generate_markdown_report, resolve_report_period
from news_crawler.target_exporter import TargetCommentExporter, get_latest_race_date

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="news-crawler")
def main() -> None:
    """News Scraping System CLI."""


@main.command()
@click.option(
    "--source",
    "-s",
    "source_key",
    type=str,
    default=None,
    help="Target source key to crawl (e.g., 'openai'). If omitted, crawls all enabled sources.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Simulate crawl without persisting articles to database.",
)
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def crawl(
    source_key: str | None,
    dry_run: bool,
    sources_file: Path | None,
    config_dir: Path,
) -> None:
    """Execute scraping across registered AI information sources."""
    app_config = load_config(config_dir, sources_file=sources_file)
    coordinator = CrawlCoordinator(app_config)

    mode_text = "[yellow][DRY RUN][/yellow] " if dry_run else ""
    target_text = f"source: '{source_key}'" if source_key else "all enabled sources"
    console.print(f"{mode_text}Starting crawl for {target_text}...")

    run = asyncio.run(coordinator.crawl_all(source_key=source_key, dry_run=dry_run))

    # Results Table
    table = Table(title=f"Crawl Summary (Status: {run.status.upper()})")
    table.add_column("Source Key", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Fetched", justify="right")
    table.add_column("New", justify="right", style="green")
    table.add_column("Updated", justify="right", style="blue")
    table.add_column("Duration", justify="right")
    table.add_column("Error / Note", style="red")

    for res in run.results:
        status_style = "green" if res.status == "success" else "red"
        table.add_row(
            res.source_key,
            f"[{status_style}]{res.status}[/{status_style}]",
            str(res.article_count),
            str(res.new_count),
            str(res.updated_count),
            f"{res.duration_seconds}s",
            res.error_message or "",
        )

    console.print(table)
    console.print(
        f"Completed: Total New: [green]{run.new_count}[/green], "
        f"Updated: [blue]{run.updated_count}[/blue], "
        f"Success: {run.success_count}/{run.total_sources}"
    )


@main.command()
@click.option(
    "--days",
    "-d",
    type=int,
    default=None,
    help="Number of days to include in report (default: 7).",
)
@click.option(
    "--start-date",
    type=str,
    default=None,
    help="Start date for report (YYYY-MM-DD).",
)
@click.option(
    "--end-date",
    type=str,
    default=None,
    help="End date for report (YYYY-MM-DD).",
)
@click.option(
    "--period",
    "-p",
    type=click.Choice(["week", "month", "quarter"]),
    default=None,
    help="Predefined period for report.",
)
@click.option(
    "--title",
    type=str,
    default=None,
    help="Custom report title (overrides config).",
)
@click.option(
    "--tags",
    "tags",
    type=str,
    multiple=True,
    help="Frontmatter tags (repeatable or comma-separated, e.g. --tags keiba,news).",
)
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--category",
    "-c",
    type=str,
    default=None,
    help="Filter report by category (e.g. 'official', 'media').",
)
@click.option(
    "--exclude-source",
    "exclude_source_keys",
    type=str,
    multiple=True,
    help="Source key to exclude from report (repeatable, e.g. -x google_news_jra).",
)
@click.option(
    "--exclude-duplicates/--include-duplicates",
    default=False,
    help="Exclude articles marked as cross-source duplicates via 'news-crawler dedupe' "
    "(default: include).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output markdown file path (default: output/report_YYYYMMDD.md).",
)
@click.option(
    "--visualize/--no-visualize",
    default=None,
    help="Enable/disable word cloud and charts (default: disabled).",
)
@click.option(
    "--top-n",
    type=click.IntRange(10, 50),
    default=None,
    help="Top N words for frequency charts (default: 20).",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def report(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    period: str | None,
    title: str | None,
    tags: tuple[str, ...],
    sources_file: Path | None,
    category: str | None,
    exclude_source_keys: tuple[str, ...],
    exclude_duplicates: bool,
    output: Path | None,
    visualize: bool | None,
    top_n: int | None,
    config_dir: Path,
) -> None:
    """Generate Markdown report from crawled articles."""
    app_config = load_config(config_dir, sources_file=sources_file)
    db = Database(app_config.crawler.database_path)

    # Override config with CLI options
    report_cfg = app_config.report.model_copy()
    if title is not None:
        report_cfg.title = title
    if tags:
        parsed_tags: list[str] = []
        for t_item in tags:
            for t in t_item.split(","):
                t_clean = t.strip()
                if t_clean and t_clean not in parsed_tags:
                    parsed_tags.append(t_clean)
        report_cfg.tags = parsed_tags
    if visualize is not None:
        report_cfg.visualize = visualize
    if top_n is not None:
        report_cfg.top_n = top_n

    if output is None:
        from datetime import datetime

        today_str = datetime.now().strftime("%Y%m%d")
        output = Path(app_config.crawler.output_dir) / f"weekly_report_{today_str}.md"

    # Sources disabled in config (e.g. Google News aggregators) are excluded from
    # reports by default too, since old crawled articles remain in the database.
    disabled_source_keys = [key for key, src in app_config.sources.items() if not src.enabled]
    combined_exclude_source_keys = sorted(set(exclude_source_keys) | set(disabled_source_keys))

    try:
        md_content = generate_markdown_report(
            db,
            days=days,
            start_date=start_date,
            end_date=end_date,
            period=period,
            category=category,
            exclude_source_keys=combined_exclude_source_keys or None,
            exclude_duplicates=exclude_duplicates,
            output_path=output,
            report_config=report_cfg,
        )
        console.print(f"[green]Report generated successfully:[/green] {output}")
        console.print(f"Total report size: {len(md_content.splitlines())} lines")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        ctx = click.get_current_context()
        click.echo(ctx.get_help())
        ctx.exit(1)


@main.command()
@click.option(
    "--days",
    "-d",
    type=int,
    default=7,
    help="Number of days to include in enrichment (default: 7).",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=50,
    help="Max articles to process (default: 50).",
)
@click.option(
    "--retry-failed",
    is_flag=True,
    default=False,
    help="Include failed articles in enrichment.",
)
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def enrich(
    days: int,
    limit: int,
    retry_failed: bool,
    sources_file: Path | None,
    config_dir: Path,
) -> None:
    """Enrich articles with AI-generated Japanese title and summary."""
    app_config = load_config(config_dir, sources_file=sources_file)

    if not app_config.ai.enabled:
        console.print("[yellow]AI enrichment is disabled in configuration.[/yellow]")
        return

    db = Database(app_config.crawler.database_path)
    processor = AIProcessor(app_config.ai, db)

    console.print(f"Starting AI enrichment for articles from last {days} days...")
    console.print(f"Max articles: {limit}, Retry failed: {retry_failed}")

    stats = asyncio.run(
        processor.enrich_articles(days=days, limit=limit, retry_failed=retry_failed)
    )

    console.print("\n[bold]Enrichment Summary:[/bold]")
    console.print(f"  Total processed: {stats['total']}")
    console.print(f"  Success: [green]{stats['success']}[/green]")
    console.print(f"  Failed: [red]{stats['failed']}[/red]")
    console.print(f"  Skipped: {stats['skipped']}")


@main.command()
@click.argument("query", type=str)
@click.option(
    "--category",
    "-c",
    type=str,
    default=None,
    help="Filter by category.",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=10,
    help="Max search results (default: 10).",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def search(query: str, category: str | None, limit: int, config_dir: Path) -> None:
    """Search articles using SQLite FTS5 full-text index."""
    app_config = load_config(config_dir)
    db = Database(app_config.crawler.database_path)

    results = db.search_articles(query, category=category, limit=limit)

    if not results:
        console.print(f"[yellow]No articles found matching query:[/yellow] '{query}'")
        return

    console.print(f"[green]Found {len(results)} matches for '[bold]{query}[/bold]':[/green]\n")
    for idx, res in enumerate(results, 1):
        art = res.article
        pub_date = art.published_at.strftime("%Y-%m-%d") if art.published_at else "No date"

        # Show Japanese title if available, otherwise original title
        display_title = art.title_ja if art.title_ja else art.title
        console.print(f"[bold cyan]{idx}. {display_title}[/bold cyan]")
        console.print(f"   URL: [blue]{art.url}[/blue]")
        meta_line = (
            f"   Category: [magenta]{art.category}[/magenta] | "
            f"Source: [yellow]{art.source_key}[/yellow] | Date: {pub_date}"
        )
        console.print(meta_line)
        if res.snippet:
            console.print(f"   Snippet: {res.snippet}")
        console.print()


@main.command()
@click.option(
    "--days",
    "-d",
    type=int,
    default=None,
    help="Number of days to include (default: 7).",
)
@click.option(
    "--start-date",
    type=str,
    default=None,
    help="Start date for the scan (YYYY-MM-DD).",
)
@click.option(
    "--end-date",
    type=str,
    default=None,
    help="End date for the scan (YYYY-MM-DD).",
)
@click.option(
    "--period",
    "-p",
    type=click.Choice(["week", "month", "quarter"]),
    default=None,
    help="Predefined period for the scan.",
)
@click.option(
    "--min-title-len",
    type=int,
    default=DEFAULT_MIN_TITLE_LEN,
    help=f"Minimum normalized title length to consider for matching "
    f"(default: {DEFAULT_MIN_TITLE_LEN}).",
)
@click.option(
    "--dry-run/--apply",
    "dry_run",
    default=True,
    help="Preview detected duplicates without writing to the database (default: dry-run).",
)
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def dedupe(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    period: str | None,
    min_title_len: int,
    dry_run: bool,
    sources_file: Path | None,
    config_dir: Path,
) -> None:
    """Detect and mark cross-source duplicate articles (same story, different outlet).

    Matches are based on exact normalized-title equality across different
    sources within the scanned window; the earliest-published article in each
    cluster is kept as canonical. Idempotent and safe to re-run.
    """
    app_config = load_config(config_dir, sources_file=sources_file)
    db = Database(app_config.crawler.database_path)

    try:
        start_at, end_at, _mode = resolve_report_period(days, start_date, end_date, period)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        ctx = click.get_current_context()
        click.echo(ctx.get_help())
        ctx.exit(1)
        return

    matches = run_dedupe(
        db, start_at, end_at, dry_run=dry_run, min_title_len=min_title_len
    )

    mode_text = "[yellow][DRY RUN][/yellow] " if dry_run else ""
    if not matches:
        console.print(f"{mode_text}No cross-source duplicates found.")
        return

    table = Table(title=f"{'[DRY RUN] ' if dry_run else ''}Detected Duplicates ({len(matches)})")
    table.add_column("Canonical Source", style="green")
    table.add_column("Canonical Title", overflow="fold")
    table.add_column("Duplicate Source", style="yellow")
    table.add_column("Duplicate Title", overflow="fold")
    table.add_column("Score", justify="right")

    for m in matches:
        table.add_row(
            m.canonical.source_key,
            m.canonical.title,
            m.duplicate.source_key,
            m.duplicate.title,
            f"{m.score:.2f}",
        )

    console.print(table)
    if dry_run:
        console.print(
            f"{len(matches)} duplicate(s) detected. Re-run with [bold]--apply[/bold] "
            "to persist."
        )
    else:
        console.print(f"[green]{len(matches)} duplicate(s) marked in the database.[/green]")


@main.command("list-sources")
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def list_sources(sources_file: Path | None, config_dir: Path) -> None:
    """List all registered sources from configuration."""
    app_config = load_config(config_dir, sources_file=sources_file)

    table = Table(title="Registered AI Sources")
    table.add_column("Key", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Category", style="magenta")
    table.add_column("Fetch Method", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Target / Feed URL")

    for key, src in sorted(app_config.sources.items()):
        status = "[green]Enabled[/green]" if src.enabled else "[dim]Disabled[/dim]"
        url = src.feed_url or src.list_url or src.base_url
        table.add_row(key, src.name, src.category, src.fetch_method.value, status, url)

    console.print(table)


@main.command()
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def stats(sources_file: Path | None, config_dir: Path) -> None:
    """Show database statistics."""
    app_config = load_config(config_dir, sources_file=sources_file)
    db = Database(app_config.crawler.database_path)
    info = db.get_stats()

    console.print("[bold]Database Statistics:[/bold]")
    console.print(f"  Total Sources: {info['total_sources']}")
    console.print(f"  Total Articles: {info['total_articles']}")
    console.print("  Articles per Category:")
    for cat, count in info["categories"].items():
        console.print(f"    - [magenta]{cat}[/magenta]: {count}")

    # AI statistics
    if "ai_status" in info and info["ai_status"]:
        console.print("\n[bold]AI Processing Status:[/bold]")
        for status, count in info["ai_status"].items():
            color = "green" if status == "completed" else "yellow" if status == "pending" else "red"
            console.print(f"  {status.capitalize()}: [{color}]{count}[/{color}]")



@main.command("export-comments")
@click.option(
    "--date",
    "-d",
    "target_date",
    type=str,
    default=None,
    help="Target race date (YYYY-MM-DD). Defaults to latest date in database.",
)
@click.option(
    "--race",
    "-r",
    "race_filter",
    type=str,
    default=None,
    help="Filter by specific race name or keyword (e.g. 'ながつき', '大阪スポーツ杯').",
)
@click.option(
    "--venue",
    "-v",
    "venue_filter",
    type=str,
    default=None,
    help="Filter by venue (e.g. '中山', '阪神').",
)
@click.option(
    "--race-num",
    "race_num_filter",
    type=int,
    default=None,
    help="Filter by race number (1-12).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output CSV file path (defaults to output/target_comments_YYYYMMDD.csv).",
)
@click.option(
    "--format",
    "format_type",
    type=click.Choice(["target", "simple"]),
    default="target",
    help="CSV format: 'target' (開催名,レースID,コメント [推奨]) or 'simple' (レースID,コメント).",
)
@click.option(
    "--encoding",
    type=str,
    default="cp932",
    help="Output CSV encoding (default: 'cp932' for Windows TARGET compatibility, or 'utf-8').",
)
@click.option(
    "--kai",
    type=int,
    default=None,
    help="Manual override for Kaiji (回次).",
)
@click.option(
    "--nichi",
    type=int,
    default=None,
    help="Manual override for Nichime (日次).",
)
@click.option(
    "--schedule",
    "schedule_str",
    type=str,
    default=None,
    help="Manual schedule specification, e.g. '中山:4:5,阪神:4:5' (場:回:日).",
)
@click.option(
    "--single-line/--multi-line",
    "single_line",
    default=True,
    help="Flatten comments into a single line per race (default: single-line for TARGET).",
)
@click.option(
    "--preview/--no-preview",
    default=True,
    help="Display table preview of exported comments in terminal (default: enabled).",
)
@click.option(
    "--sources-file",
    "-f",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom sources configuration YAML file.",
)
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def export_comments(
    target_date: str | None,
    race_filter: str | None,
    venue_filter: str | None,
    race_num_filter: int | None,
    output: Path | None,
    format_type: str,
    encoding: str,
    kai: int | None,
    nichi: int | None,
    schedule_str: str | None,
    single_line: bool,
    preview: bool,
    sources_file: Path | None,
    config_dir: Path,
) -> None:
    """Export race-specific news and comments to CSV for TARGET frontier JV bulk import."""
    app_config = load_config(config_dir, sources_file=sources_file)
    db = Database(app_config.crawler.database_path)

    # Determine target date
    if not target_date:
        target_date = get_latest_race_date(db)
        console.print(
            f"[dim]No date specified. Using latest available date: [bold]{target_date}[/bold][/dim]"
        )

    # Parse schedule overrides if given
    schedule_overrides: dict[str, tuple[int, int]] = {}
    if schedule_str:
        for part in schedule_str.split(","):
            items = part.strip().split(":")
            if len(items) == 3:
                s_venue = items[0].strip()
                s_kai = int(items[1].strip())
                s_nichi = int(items[2].strip())
                schedule_overrides[s_venue] = (s_kai, s_nichi)

    exporter = TargetCommentExporter(db)
    comments = exporter.extract_race_comments(
        target_date=target_date,
        race_filter=race_filter,
        venue_filter=venue_filter,
        race_num_filter=race_num_filter,
        manual_kai=kai,
        manual_nichi=nichi,
        schedule_overrides=schedule_overrides or None,
    )

    if not comments:
        filter_desc = []
        if race_filter:
            filter_desc.append(f"race='{race_filter}'")
        if venue_filter:
            filter_desc.append(f"venue='{venue_filter}'")
        if race_num_filter:
            filter_desc.append(f"R={race_num_filter}")
        desc_str = f" ({', '.join(filter_desc)})" if filter_desc else ""
        console.print(f"[yellow]No race comments found for {target_date}{desc_str}.[/yellow]")
        return

    # Determine output path
    if output is None:
        safe_date = target_date.replace("-", "")
        suffix = f"_{race_filter}" if race_filter else ""
        output = Path(app_config.crawler.output_dir) / f"target_comments_{safe_date}{suffix}.csv"

    out_path = exporter.export_to_file(
        items=comments,
        output_path=output,
        format_type=format_type,  # type: ignore[arg-type]
        encoding=encoding,
        single_line=single_line,
    )

    console.print(
        f"[bold green]Successfully exported {len(comments)} race comment(s) to:[/bold green] "
        f"[cyan]{out_path}[/cyan] [dim](Encoding: {encoding.upper()})[/dim]"
    )

    if preview:
        table = Table(title=f"TARGET Frontier JV Export Preview ({target_date})")
        table.add_column("Race ID (16桁)", style="cyan", no_wrap=True)
        table.add_column("開催", style="magenta", no_wrap=True)
        table.add_column("レース名", style="bold green")
        table.add_column("記事数", justify="right")
        table.add_column("コメント冒頭 (抜粋)", style="dim")

        for it in comments:
            # Extract first comment line or snippet
            snippet = it.comment_text.replace("\n", " ")
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            table.add_row(
                it.race_id,
                f"{it.kaisai_name} {it.race_num}R",
                it.race_name,
                str(len(it.source_articles)),
                snippet,
            )

        console.print(table)

    console.print(
        "\n[bold]TARGET frontier JV インポート手順:[/bold]\n"
        "  1. TARGET メインメニュー > [bold]ファイルからのコメント等一括登録[/bold] を開く\n"
        "  2. [bold]レースコメントのインポート[/bold] を選択\n"
        f"  3. [cyan]{out_path}[/cyan] を指定して取り込みを実行（上書き／後に結合 等を選択）\n"
    )


if __name__ == "__main__":
    main()

