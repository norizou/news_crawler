"""Text analysis module for Japanese and English content."""

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from sudachipy import Dictionary, SplitMode

if TYPE_CHECKING:
    from news_crawler.models import Article

# Basic English stopwords
ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these",
    "they", "this", "to", "was", "will", "with", "would", "from", "which", "has", "have", "had",
    "can", "could", "all", "any", "been", "being", "both", "each", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "about", "above", "after", "again", "against", "am",
    "because", "before", "below", "between", "did", "do", "does", "doing", "down", "during",
    "each", "further", "here", "how", "just", "now", "once", "only", "other", "our", "out",
    "over", "own", "same", "she", "should", "so", "some", "than", "too", "under", "until", "up",
    "very", "were", "what", "when", "where", "while", "who", "whom", "why", "with", "you", "your",
    "yours", "yourself", "yourselves",
    # Additional common stopwords for keyword extraction
    "one", "more", "data", "print", "first", "be", "agents", "gpt", "but", "an", "as", "at", "from",
    "are", "not",
}

# Basic Japanese stopwords (representative)
JAPANESE_STOPWORDS = {
    "これ", "それ", "あれ", "これら", "それら", "あれら", "私", "私たち",
    "僕", "僕ら", "君", "君たち", "彼", "彼女", "彼ら", "ここ", "そこ",
    "あそこ", "どこ", "こちら", "そちら", "あちら", "どちら",
    "もの", "こと", "とき", "よう", "ほう", "わけ", "ため", "はず",
    "まま", "うち", "ところ", "つもり",
    "いつ", "どこ", "だれ", "なに", "なぜ", "どう", "どこ", "どれ", "どの", "どのよう",
    "する", "なる", "ある", "いる", "くる", "いく", "いう", "できる", "くる", "おもう",
    "また", "しかし", "そして", "さらに", "または", "それとも", "および", "ならびに", "あるいは",
    "にて", "まで", "から", "より", "ほど", "くらい", "ぐらい", "だけ", "のみ", "ばかり",
    "さえ", "まで", "くらい", "など", "なり", "だに", "すら", "きり", "ふし", "よし",
    "です", "ます", "だ", "だっ", "なっ", "てる", "いる", "あり", "なし", "あり", "ない"
}


class TextAnalyzer:
    """Analyzer for Japanese and English text using morphological analysis and tokenization."""

    def __init__(
        self,
        keywords_path: str | None = None,
        sudachi_config_path: str | None = None,
    ) -> None:
        try:
            # SudachiPy initialization (with optional user dictionaries)
            if sudachi_config_path and Path(sudachi_config_path).exists():
                self.dict = Dictionary(config_path=sudachi_config_path)
            else:
                self.dict = Dictionary()
            self.tokenizer = self.dict.create()
        except Exception as e:
            print(f"Error initializing SudachiPy: {e}")
            self.tokenizer = None

        # Load AI keywords filter
        self.ai_keywords: set[str] = set()
        self.stopwords_general: set[str] = set()
        self.stopwords_extraction: set[str] = set()

        if keywords_path and Path(keywords_path).exists():
            self._load_keywords(keywords_path)

    def _load_keywords(self, keywords_path: str) -> None:
        """Load AI keywords and general stopwords from YAML file."""
        try:
            with open(keywords_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

                # Load AI keywords (whitelist)
                if "ai_keywords" in data:
                    self.ai_keywords = set(
                        k.lower() if isinstance(k, str) else str(k)
                        for k in data["ai_keywords"]
                    )

                # Load general stopwords (blacklist)
                if "stopwords_general" in data:
                    self.stopwords_general = set(
                        k.lower() if isinstance(k, str) else str(k)
                        for k in data["stopwords_general"]
                    )

                # Load extraction stopwords (for keyword extraction)
                if "stopwords_extraction" in data:
                    self.stopwords_extraction = set(
                        k.lower() if isinstance(k, str) else str(k)
                        for k in data["stopwords_extraction"]
                    )
        except Exception as e:
            print(f"Error loading keywords from {keywords_path}: {e}")

    def analyze_japanese(self, texts: list[str], use_ai_filter: bool = True) -> Counter[str]:
        """Analyze Japanese texts and return word frequencies of nouns, verbs, and adjectives."""
        if not self.tokenizer:
            return Counter()

        counter: Counter[str] = Counter()
        for text in texts:
            if not text:
                continue

            # Use SplitMode.C for capturing combined words, or B/A for more granular
            # For trends, C or B is often better
            tokens = self.tokenizer.tokenize(text, SplitMode.C)
            for m in tokens:
                pos = m.part_of_speech()
                pos_major = pos[0]

                # We want Nouns, Verbs, Adjectives (名詞, 動詞, 形容詞)
                if pos_major in ["名詞", "動詞", "形容詞"]:
                    # Use dictionary form (normalized base form)
                    lemma = m.dictionary_form()

                    # Filter out stopwords, short words, and numbers
                    if (
                        len(lemma) > 1 and
                        lemma not in JAPANESE_STOPWORDS and
                        not lemma.isdigit() and
                        not re.match(r"^[0-9.]+$", lemma)
                    ):
                        # Apply general stopwords filter if loaded (excludes overly generic terms)
                        if self.stopwords_general and lemma.lower() in self.stopwords_general:
                            continue

                        # Apply AI keywords filter if loaded and use_ai_filter is True
                        if use_ai_filter and self.ai_keywords:
                            if lemma in self.ai_keywords:
                                counter[lemma] += 1
                        else:
                            # When use_ai_filter is False, count all words (after stopwords filter)
                            counter[lemma] += 1

        return counter

    def analyze_english(self, texts: list[str], use_ai_filter: bool = True) -> Counter[str]:
        """Analyze English texts and return word frequencies of alphanumeric words."""
        counter: Counter[str] = Counter()
        for text in texts:
            if not text:
                continue

            # Extract words (alphanumeric)
            words = re.findall(r"\b[a-zA-Z0-9-]{2,}\b", text.lower())
            for word in words:
                word_lower = word.lower()
                if (
                    word_lower not in ENGLISH_STOPWORDS
                    and not word_lower.isdigit()
                    and not re.match(r"^[0-9.-]+$", word_lower)
                ):
                    # Apply general stopwords filter if loaded
                    if self.stopwords_general and word_lower in self.stopwords_general:
                        continue

                    # Apply AI keywords filter if loaded and use_ai_filter is True
                    if use_ai_filter and self.ai_keywords:
                        if word_lower in self.ai_keywords:
                            counter[word_lower] += 1
                    else:
                        counter[word_lower] += 1

        return counter

    def extract_from_tags(self, tags_list: list[list[str]]) -> Counter[str]:
        """Extract and count keywords from article tags."""
        counter: Counter[str] = Counter()
        for tags in tags_list:
            if not tags:
                continue
            for tag in tags:
                tag_lower = tag.lower().strip()
                if len(tag_lower) > 1 and not tag_lower.isdigit():
                    # Apply general stopwords filter if loaded
                    if self.stopwords_general and tag_lower in self.stopwords_general:
                        continue
                    # Also check built-in stopwords
                    if tag_lower in ENGLISH_STOPWORDS or tag_lower in JAPANESE_STOPWORDS:
                        continue
                    counter[tag_lower] += 1
        return counter

    def extract_trending_keywords(
        self,
        articles: list["Article"],
        min_count: int = 2,
        top_n: int = 50,
    ) -> list[tuple[str, int]]:
        """
        Extract trending keywords from articles using tags and text analysis.

        Returns list of (keyword, count) tuples sorted by frequency.
        """
        # Extract from tags
        tags_list = [a.tags for a in articles if a.tags]
        counter = self.extract_from_tags(tags_list)

        # Also analyze text for additional keywords (disable AI filter to discover new words)
        ja_texts = [
            f"{a.title_ja} {a.summary_ja}"
            for a in articles
            if a.ai_status == "completed" and (a.title_ja or a.summary_ja)
        ]
        if ja_texts:
            ja_freq = self.analyze_japanese(ja_texts, use_ai_filter=False)
            # Filter out extraction stopwords from config (case-insensitive)
            if self.stopwords_extraction:
                ja_freq = Counter(
                    {k: v for k, v in ja_freq.items() if k.lower() not in self.stopwords_extraction}
                )
            counter.update(ja_freq)

        orig_texts = [f"{a.title} {a.summary} {a.content}" for a in articles]
        en_freq = self.analyze_japanese(orig_texts, use_ai_filter=False) or self.analyze_english(
            orig_texts, use_ai_filter=False
        )
        # Filter out extraction stopwords from config (case-insensitive)
        if self.stopwords_extraction:
            en_freq = Counter(
                {k: v for k, v in en_freq.items() if k.lower() not in self.stopwords_extraction}
            )
        counter.update(en_freq)

        # Filter by minimum count and sort
        filtered = [(k, v) for k, v in counter.items() if v >= min_count]
        sorted_keywords = sorted(filtered, key=lambda x: (-x[1], x[0]))

        return sorted_keywords[:top_n]
