"""Tests for text analyzer using SudachiPy."""

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
    assert "非常に" not in counter  # This might be an adverb/形状詞 depending on dict, but usually we want to filter common filler

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
