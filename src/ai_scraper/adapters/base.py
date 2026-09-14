"""Base adapter abstract class."""

from abc import ABC, abstractmethod

from ai_scraper.config import CrawlerConfig
from ai_scraper.models import Article, SourceConfig


class BaseAdapter(ABC):
    """Abstract base adapter for source fetching."""

    def __init__(self, source: SourceConfig, crawler_config: CrawlerConfig):
        self.source = source
        self.crawler_config = crawler_config

    @abstractmethod
    async def fetch(self) -> list[Article]:
        """Fetch articles from the source and return standardized Article objects."""
        raise NotImplementedError
