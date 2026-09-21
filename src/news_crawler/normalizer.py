"""URL and content normalization utilities."""

import hashlib
import re
import unicodedata
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

# All timestamps are stored as naive JST so string comparison in SQLite stays consistent
# regardless of whether a source emits UTC ("Z"/+00:00), +09:00 or no offset at all.
JST = ZoneInfo("Asia/Tokyo")

# Tracking parameters to strip from URLs
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "msclkid",
    "ref",
    "source",
    "sp_source",
    "_ga",
    "_gl",
}


def normalize_url(url: str) -> str:
    """Normalize URL by stripping tracking parameters, fragments, and standardizing host."""
    if not url:
        return ""

    parsed = urlparse(url.strip())

    # Scheme and netloc to lowercase
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()

    # Remove standard ports
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    # Clean query parameters
    query_tuples = parse_qsl(parsed.query, keep_blank_values=False)
    filtered_params = [
        (k, v)
        for k, v in query_tuples
        if k.lower() not in TRACKING_PARAMS and not k.startswith("utm_")
    ]
    # Sort query parameters for consistency
    filtered_params.sort(key=lambda x: x[0])
    new_query = urlencode(filtered_params)

    # Normalize path
    path = parsed.path or "/"
    # Remove duplicate slashes
    path = re.sub(r"/+", "/", path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Discard fragment
    return urlunparse((scheme, netloc, path, "", new_query, ""))


def normalize_title_for_dedupe(title: str) -> str:
    """Normalize a title for exact-match cross-source duplicate detection.

    Applies NFKC normalization (full/half-width, compatibility forms) and strips
    all whitespace, so that titles differing only in spacing or character width
    are treated as identical.
    """
    if not title:
        return ""
    normalized = unicodedata.normalize("NFKC", title)
    return re.sub(r"\s+", "", normalized)


def compute_content_hash(title: str, content: str) -> str:
    """Compute SHA-256 hash of normalized title and content."""
    normalized_title = clean_text(title)
    normalized_content = clean_text(content)
    combined = f"{normalized_title}\n{normalized_content}".encode()
    return hashlib.sha256(combined).hexdigest()


def clean_text(text: str) -> str:
    """Clean whitespace, remove redundant empty lines, and normalize text."""
    if not text:
        return ""

    # Normalize line breaks
    text = re.sub(r"\r\n|\r", "\n", text)
    # Strip whitespace on each line
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    # Join and collapse multiple empty lines to max 2 newlines
    joined = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def parse_datetime(date_str: str | None) -> datetime | None:
    """Parse various datetime string formats into a datetime object."""
    if not date_str:
        return None

    cleaned = date_str.strip()
    if not cleaned:
        return None

    # Normalize Japanese date format (e.g. "2026年9月12日" -> "2026-9-12")
    cleaned = re.sub(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        r"\1-\2-\3",
        cleaned,
    )

    try:
        return _to_naive_jst(date_parser.parse(cleaned))
    except (ValueError, TypeError, OverflowError):
        pass

    # Retry tolerating trailing/embedded non-date text (e.g. "2026/09/17 12:24更新")
    try:
        return _to_naive_jst(date_parser.parse(cleaned, fuzzy=True))
    except (ValueError, TypeError, OverflowError):
        return None


def _to_naive_jst(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to naive JST; naive values are assumed to be JST."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(JST).replace(tzinfo=None)
