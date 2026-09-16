"""Base adapter abstract class."""

from abc import ABC, abstractmethod

from news_crawler.config import CrawlerConfig
from news_crawler.models import Article, SourceConfig


class BaseAdapter(ABC):
    """Abstract base adapter for source fetching."""

    def __init__(self, source: SourceConfig, crawler_config: CrawlerConfig):
        self.source = source
        self.crawler_config = crawler_config

    @abstractmethod
    async def fetch(self) -> list[Article]:
        """Fetch articles from the source and return standardized Article objects."""
        raise NotImplementedError
