"""Source fetch adapters."""

from ai_scraper.adapters.github import GitHubAdapter
from ai_scraper.adapters.html import HTMLAdapter
from ai_scraper.adapters.huggingface import HuggingFaceAdapter
from ai_scraper.adapters.playwright import PlaywrightAdapter
from ai_scraper.adapters.rss import RSSAdapter

__all__ = ["RSSAdapter", "HTMLAdapter", "PlaywrightAdapter", "GitHubAdapter", "HuggingFaceAdapter"]
