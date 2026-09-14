"""Text analysis module for Japanese and English content."""

import re
from collections import Counter
from pathlib import Path
from typing import List, Optional, Set

import yaml
from sudachipy import Dictionary, SplitMode

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
    "yours", "yourself", "yourselves"
}

# Basic Japanese stopwords (representative)
JAPANESE_STOPWORDS = {
    "これ", "それ", "あれ", "これら", "それら", "あれら", "私", "私たち", "僕", "僕ら", "君", "君たち",
    "彼", "彼女", "彼ら", "ここ", "そこ", "あそこ", "どこ", "こちら", "そちら", "あちら", "どちら",
    "もの", "こと", "とき", "よう", "ほう", "わけ", "ため", "はず", "まま", "うち", "ところ", "つもり",
    "いつ", "どこ", "だれ", "なに", "なぜ", "どう", "どこ", "どれ", "どの", "どのよう",
    "する", "なる", "ある", "いる", "くる", "いく", "いう", "できる", "くる", "おもう",
    "また", "しかし", "そして", "さらに", "または", "それとも", "および", "ならびに", "あるいは",
    "にて", "まで", "から", "より", "ほど", "くらい", "ぐらい", "だけ", "のみ", "ばかり",
    "さえ", "まで", "くらい", "など", "なり", "だに", "すら", "きり", "ふし", "よし",
    "です", "ます", "だ", "だっ", "なっ", "てる", "いる", "あり", "なし", "あり", "ない"
}


class TextAnalyzer:
    """Analyzer for Japanese and English text using morphological analysis and tokenization."""

    def __init__(self, keywords_path: Optional[str] = None) -> None:
        try:
            # SudachiPy initialization
            self.dict = Dictionary()
            self.tokenizer = self.dict.create()
        except Exception as e:
            print(f"Error initializing SudachiPy: {e}")
            self.tokenizer = None
        
        # Load AI keywords filter
        self.ai_keywords: Set[str] = set()
        self.stopwords_general: Set[str] = set()
        
        if keywords_path and Path(keywords_path).exists():
            self._load_keywords(keywords_path)

    def _load_keywords(self, keywords_path: str) -> None:
        """Load AI keywords and general stopwords from YAML file."""
        try:
            with open(keywords_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                
                # Load AI keywords (whitelist)
                if "ai_keywords" in data:
                    self.ai_keywords = set(k.lower() for k in data["ai_keywords"])
                
                # Load general stopwords (blacklist)
                if "stopwords_general" in data:
                    self.stopwords_general = set(k.lower() for k in data["stopwords_general"])
        except Exception as e:
            print(f"Error loading keywords from {keywords_path}: {e}")

    def analyze_japanese(self, texts: List[str]) -> Counter[str]:
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
                        # Apply AI keywords filter if loaded
                        if self.ai_keywords:
                            if lemma in self.ai_keywords:
                                counter[lemma] += 1
                        else:
                            counter[lemma] += 1
        
        return counter

    def analyze_english(self, texts: List[str]) -> Counter[str]:
        """Analyze English texts and return word frequencies of alphanumeric words."""
        counter: Counter[str] = Counter()
        for text in texts:
            if not text:
                continue
            
            # Extract words (alphanumeric)
            words = re.findall(r"\b[a-zA-Z0-9-]{2,}\b", text.lower())
            for word in words:
                if word not in ENGLISH_STOPWORDS and not word.isdigit() and not re.match(r"^[0-9.-]+$", word):
                    # Apply general stopwords filter if loaded
                    if self.stopwords_general and word in self.stopwords_general:
                        continue
                    
                    # Apply AI keywords filter if loaded
                    if self.ai_keywords:
                        if word in self.ai_keywords:
                            counter[word] += 1
                    else:
                        counter[word] += 1
        
        return counter
