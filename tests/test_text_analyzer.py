"""Tests for text analyzer using SudachiPy."""

from datetime import datetime, timedelta

from news_crawler.models import Article
from news_crawler.text_analyzer import TextAnalyzer


def test_analyze_japanese():
    analyzer = TextAnalyzer()
    texts = [
        "最新のAI技術について調査しました。",
        "新しいAIモデルがリリースされました。非常に強力です。"
    ]

    counter = analyzer.analyze_japanese(texts)

    # Check if some expected words are present
    assert counter["最新"] >= 1
    assert counter["技術"] >= 1
    assert counter["調査"] >= 1
    assert counter["新しい"] >= 1
    assert counter["モデル"] >= 1
    assert counter["リリース"] >= 1

    # Check if stopwords are removed
    assert "について" not in counter
    assert "の" not in counter
    assert "が" not in counter
    assert "ました" not in counter
    # Filter common filler
    assert "非常に" not in counter

def test_analyze_japanese_with_keywords_filter(tmp_path):
    # Create a test keywords file
    keywords_file = tmp_path / "test_keywords.yaml"
    keywords_file.write_text("""
ai_keywords:
  - 人工知能
  - モデル
  - 技術
stopwords_general:
  - 新しい
  - 最新
""", encoding="utf-8")

    analyzer = TextAnalyzer(keywords_path=str(keywords_file))
    texts = [
        "最新の人工知能技術について調査しました。",
        "新しい人工知能モデルがリリースされました。非常に強力です。"
    ]

    counter = analyzer.analyze_japanese(texts)

    # Only AI keywords should be present
    assert "人工知能" in counter
    assert "モデル" in counter
    assert "技術" in counter

    # Filtered words should not be present
    assert "新しい" not in counter
    assert "最新" not in counter
    assert "調査" not in counter  # Not in whitelist
    assert "リリース" not in counter  # Not in whitelist

def test_analyze_japanese_lemma():
    analyzer = TextAnalyzer()
    texts = ["走る、走った、走れば。"]
    counter = analyzer.analyze_japanese(texts)

    # All should be normalized to "走る" (dictionary form)
    assert counter["走る"] >= 3

def test_analyze_english():
    analyzer = TextAnalyzer()
    texts = [
        "New AI models are released by OpenAI.",
        "GPT-4 is a large language model."
    ]

    counter = analyzer.analyze_english(texts)

    assert counter["ai"] >= 1
    assert counter["models"] >= 1
    assert counter["released"] >= 1
    assert counter["openai"] >= 1
    assert counter["gpt-4"] >= 1
    assert counter["large"] >= 1
    assert counter["language"] >= 1
    assert counter["model"] >= 1

    # Stopwords
    assert "are" not in counter
    assert "by" not in counter
    assert "is" not in counter
    assert "a" not in counter

def test_analyze_english_with_keywords_filter(tmp_path):
    # Create a test keywords file
    keywords_file = tmp_path / "test_keywords.yaml"
    keywords_file.write_text("""
ai_keywords:
  - ai
  - gpt-4
  - model
stopwords_general:
  - new
  - released
  - large
""", encoding="utf-8")

    analyzer = TextAnalyzer(keywords_path=str(keywords_file))
    texts = [
        "New AI models are released by OpenAI.",
        "GPT-4 is a large language model."
    ]

    counter = analyzer.analyze_english(texts)

    # Only AI keywords should be present
    assert "ai" in counter
    assert "gpt-4" in counter
    assert "model" in counter

    # Filtered words should not be present
    assert "new" not in counter
    assert "released" not in counter
    assert "large" not in counter
    assert "openai" not in counter  # Not in whitelist
    assert "language" not in counter  # Not in whitelist


def test_extract_from_tags():
    analyzer = TextAnalyzer()
    tags_list = [
        ["ai", "machine learning", "deep learning"],
        ["ai", "nlp", "transformer"],
        ["deep learning", "computer vision"],
    ]

    counter = analyzer.extract_from_tags(tags_list)

    assert counter["ai"] == 2
    assert counter["machine learning"] == 1
    assert counter["deep learning"] == 2
    assert counter["nlp"] == 1
    assert counter["transformer"] == 1
    assert counter["computer vision"] == 1


def test_extract_from_tags_with_stopwords(tmp_path):
    stopwords_file = tmp_path / "test_stopwords.yaml"
    stopwords_file.write_text("""
ai_keywords:
  - ai
  - deep learning
stopwords_general:
  - new
  - latest
""", encoding="utf-8")

    analyzer = TextAnalyzer(keywords_path=str(stopwords_file))
    tags_list = [
        ["ai", "new", "machine learning"],
        ["ai", "latest", "nlp"],
    ]

    counter = analyzer.extract_from_tags(tags_list)

    assert counter["ai"] == 2
    assert counter["machine learning"] == 1
    assert counter["nlp"] == 1
    assert "new" not in counter
    assert "latest" not in counter


def test_extraction_stopwords_are_loaded_from_config(tmp_path):
    """Test extraction-specific stopwords are loaded independently."""
    keywords_file = tmp_path / "keywords.yaml"
    keywords_file.write_text(
        """ai_keywords: []
stopwords_extraction:
  - "on"
  - 生成
""",
        encoding="utf-8",
    )

    analyzer = TextAnalyzer(keywords_path=str(keywords_file))
    article = Article(
        source_key="test",
        url="https://example.com/stopwords",
        normalized_url="https://example.com/stopwords",
        title="On-device model",
        summary="",
        content="on-device model",
        published_at=datetime.now() - timedelta(days=1),
        category="test",
        title_ja="生成AIモデル",
        summary_ja="",
        ai_status="completed",
    )

    keywords = dict(analyzer.extract_trending_keywords([article], min_count=1, top_n=20))
    assert "on" not in keywords
    assert "生成" not in keywords


def test_extract_trending_keywords():
    analyzer = TextAnalyzer()
    articles = [
        Article(
            source_key="test",
            url="https://example.com/1",
            normalized_url="https://example.com/1",
            title="DeepSeek-V3 Released",
            summary="New AI model",
            content="DeepSeek-V3 is a new AI model with transformer architecture.",
            published_at=datetime.now() - timedelta(days=1),
            category="test",
            tags=["deepseek", "transformer", "ai"],
        ),
        Article(
            source_key="test",
            url="https://example.com/2",
            normalized_url="https://example.com/2",
            title="MCP Protocol",
            summary="Model Context Protocol",
            content="MCP enables AI agents to interact with tools.",
            published_at=datetime.now() - timedelta(days=1),
            category="test",
            tags=["mcp", "agent", "ai"],
        ),
        Article(
            source_key="test",
            url="https://example.com/3",
            normalized_url="https://example.com/3",
            title="FlashAttention",
            summary="Attention optimization",
            content="FlashAttention improves transformer efficiency.",
            published_at=datetime.now() - timedelta(days=1),
            category="test",
            tags=["flashattention", "transformer", "optimization"],
        ),
    ]

    trending = analyzer.extract_trending_keywords(articles, min_count=1, top_n=20)

    # Check that keywords from tags are extracted
    keyword_dict = dict(trending)
    assert keyword_dict.get("deepseek", 0) >= 1
    assert keyword_dict.get("transformer", 0) >= 2  # Appears in 2 articles
    assert keyword_dict.get("ai", 0) >= 2
    assert keyword_dict.get("mcp", 0) >= 1
    assert keyword_dict.get("flashattention", 0) >= 1
