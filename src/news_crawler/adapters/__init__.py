"""Source fetch adapters."""

from news_crawler.adapters.github import GitHubAdapter
from news_crawler.adapters.html import HTMLAdapter
from news_crawler.adapters.huggingface import HuggingFaceAdapter
from news_crawler.adapters.playwright import PlaywrightAdapter
from news_crawler.adapters.rss import RSSAdapter

__all__ = ["RSSAdapter", "HTMLAdapter", "PlaywrightAdapter", "GitHubAdapter", "HuggingFaceAdapter"]
