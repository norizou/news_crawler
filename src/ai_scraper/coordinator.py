"""Crawl execution coordinator."""

import time
from collections.abc import Callable
from datetime import datetime

from rich.console import Console

from ai_scraper.adapters.base import BaseAdapter
from ai_scraper.adapters.github import GitHubAdapter
from ai_scraper.adapters.html import HTMLAdapter
from ai_scraper.adapters.huggingface import HuggingFaceAdapter
from ai_scraper.adapters.playwright import PlaywrightAdapter
from ai_scraper.adapters.rss import RSSAdapter
from ai_scraper.config import AppConfig
from ai_scraper.database import Database
from ai_scraper.models import CrawlResult, CrawlRun, FetchMethod, SourceConfig

console = Console()


class CrawlCoordinator:
    """Orchestrates source scraping, database persistence, and error handling."""

    def __init__(self, config: AppConfig, db: Database | None = None):
        self.config = config
        self.db = db or Database(config.crawler.database_path)

    def _get_adapter(self, source: SourceConfig) -> BaseAdapter:
        """Factory method to get corresponding adapter for source."""
        match source.fetch_method:
            case FetchMethod.RSS:
                return RSSAdapter(source, self.config.crawler)
            case FetchMethod.HTML:
                return HTMLAdapter(source, self.config.crawler)
            case FetchMethod.PLAYWRIGHT:
                return PlaywrightAdapter(source, self.config.crawler)
            case FetchMethod.GITHUB:
                return GitHubAdapter(source, self.config.crawler)
            case FetchMethod.HUGGINGFACE:
                return HuggingFaceAdapter(source, self.config.crawler)
            case _:
                return RSSAdapter(source, self.config.crawler)

    async def crawl_source(
        self,
        source: SourceConfig,
        dry_run: bool = False,
        progress_cb: Callable[[str], None] | None = None,
    ) -> CrawlResult:
        """Crawl a single source with timing and error isolation."""
        start_time = time.time()
        if progress_cb:
            progress_cb(f"Starting crawl for {source.name} ({source.fetch_method.value})...")

        try:
            adapter = self._get_adapter(source)
            articles = await adapter.fetch()

            new_count = 0
            updated_count = 0

            if not dry_run:
                # Save source info to DB
                self.db.upsert_source(source)
                for art in articles:
                    _, is_new, is_updated = self.db.upsert_article(art)
                    if is_new:
                        new_count += 1
                    elif is_updated:
                        updated_count += 1
            else:
                new_count = len(articles)

            duration = time.time() - start_time
            return CrawlResult(
                source_key=source.key,
                status="success",
                article_count=len(articles),
                new_count=new_count,
                updated_count=updated_count,
                duration_seconds=round(duration, 2),
            )

        except Exception as e:
            duration = time.time() - start_time
            return CrawlResult(
                source_key=source.key,
                status="failed",
                article_count=0,
                new_count=0,
                updated_count=0,
                error_message=str(e),
                duration_seconds=round(duration, 2),
            )

    async def crawl_all(
        self,
        source_key: str | None = None,
        dry_run: bool = False,
    ) -> CrawlRun:
        """Execute crawl across all or selected enabled sources."""
        sources = self.config.sources

        if source_key:
            if source_key not in sources:
                raise ValueError(f"Source '{source_key}' not found in configuration.")
            target_sources = [sources[source_key]]
        else:
            target_sources = [s for s in sources.values() if s.enabled]

        crawl_run = CrawlRun(
            started_at=datetime.now(),
            total_sources=len(target_sources),
            status="running",
        )

        run_id = None
        if not dry_run:
            run_id = self.db.record_crawl_run(crawl_run)

        success_count = 0
        failed_count = 0
        total_new = 0
        total_updated = 0
        results: list[CrawlResult] = []

        for source in target_sources:
            res = await self.crawl_source(source, dry_run=dry_run)
            results.append(res)

            if res.status == "success":
                success_count += 1
                total_new += res.new_count
                total_updated += res.updated_count
            else:
                failed_count += 1

        crawl_run.finished_at = datetime.now()
        crawl_run.success_count = success_count
        crawl_run.failed_count = failed_count
        crawl_run.new_count = total_new
        crawl_run.updated_count = total_updated
        crawl_run.results = results

        if failed_count == 0:
            crawl_run.status = "success"
        elif success_count > 0:
            crawl_run.status = "partial"
        else:
            crawl_run.status = "failed"

        if not dry_run and run_id is not None:
            self.db.update_crawl_run(crawl_run)

        return crawl_run
