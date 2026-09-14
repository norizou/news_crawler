"""Command-line interface for AI Scraper."""

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ai_scraper.ai_processor import AIProcessor
from ai_scraper.config import load_config
from ai_scraper.coordinator import CrawlCoordinator
from ai_scraper.database import Database
from ai_scraper.reporting import generate_markdown_report

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="ai-scraper")
def main() -> None:
    """AI Trend & Tech News Scraping System CLI."""


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
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def crawl(source_key: str | None, dry_run: bool, config_dir: Path) -> None:
    """Execute scraping across registered AI information sources."""
    app_config = load_config(config_dir)
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
    "--category",
    "-c",
    type=str,
    default=None,
    help="Filter report by category (e.g. 'official', 'media').",
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
    help="Enable/disable word cloud and charts (default: enabled in config).",
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
    category: str | None,
    output: Path | None,
    visualize: bool | None,
    top_n: int | None,
    config_dir: Path,
) -> None:
    """Generate Markdown report from crawled articles with visualizations."""
    app_config = load_config(config_dir)
    db = Database(app_config.crawler.database_path)

    # Override config with CLI options
    report_cfg = app_config.report.model_copy()
    if visualize is not None:
        report_cfg.visualize = visualize
    if top_n is not None:
        report_cfg.top_n = top_n

    if output is None:
        from datetime import datetime

        today_str = datetime.now().strftime("%Y%m%d")
        output = Path(app_config.crawler.output_dir) / f"weekly_report_{today_str}.md"

    try:
        md_content = generate_markdown_report(
            db,
            days=days,
            start_date=start_date,
            end_date=end_date,
            period=period,
            category=category,
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
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def enrich(days: int, limit: int, retry_failed: bool, config_dir: Path) -> None:
    """Enrich articles with AI-generated Japanese title and summary."""
    app_config = load_config(config_dir)

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


@main.command("list-sources")
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def list_sources(config_dir: Path) -> None:
    """List all registered sources from configuration."""
    app_config = load_config(config_dir)

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
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default="config",
    help="Path to configuration directory.",
)
def stats(config_dir: Path) -> None:
    """Show database statistics."""
    app_config = load_config(config_dir)
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


if __name__ == "__main__":
    main()
