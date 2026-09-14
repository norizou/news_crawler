"""Tests for normalization utilities."""

from datetime import datetime

from ai_scraper.normalizer import clean_text, compute_content_hash, normalize_url, parse_datetime


def test_normalize_url_strips_tracking_params():
    url = "https://techcrunch.com/2026/09/14/new-ai-model/?utm_source=twitter&utm_medium=social&fbclid=123"
    expected = "https://techcrunch.com/2026/09/14/new-ai-model"
    assert normalize_url(url) == expected


def test_normalize_url_handles_ports_and_trailing_slashes():
    url = "HTTP://EXAMPLE.COM:80/path/to/page/?b=2&a=1#section"
    expected = "http://example.com/path/to/page?a=1&b=2"
    assert normalize_url(url) == expected


def test_compute_content_hash_consistency():
    h1 = compute_content_hash("Title 1", "Some body text.")
    h2 = compute_content_hash("Title 1", "Some body text.")
    h3 = compute_content_hash("Title 1", "Modified text.")
    assert h1 == h2
    assert h1 != h3


def test_clean_text():
    raw = "  Hello \t\t world! \r\n\r\n\r\n This is a test.   "
    cleaned = clean_text(raw)
    assert cleaned == "Hello world!\n\nThis is a test."


def test_parse_datetime_iso():
    dt = parse_datetime("2026-09-14T10:00:00Z")
    assert isinstance(dt, datetime)
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 14


def test_parse_datetime_invalid():
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime("invalid-date-string") is None
