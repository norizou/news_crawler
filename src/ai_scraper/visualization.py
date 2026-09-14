"""Visualization module for generating word clouds and pie charts."""

import os
from pathlib import Path
from collections import Counter
from typing import Dict, List, Optional, Tuple

import matplotlib
import matplotlib.pyplot as plt
from wordcloud import WordCloud

# Use Agg backend to avoid GUI issues in headless environment
matplotlib.use("Agg")


class VisualizationGenerator:
    """Generator for word clouds and pie charts from word frequencies."""

    def __init__(self, font_path: Optional[str] = None) -> None:
        self.font_path = font_path or self._detect_japanese_font()
        # Seed for deterministic layouts
        self.random_state = 42

    def _detect_japanese_font(self) -> Optional[str]:
        """Attempt to detect a Japanese font on the system."""
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
            "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
            "/usr/share/fonts/truetype/ipafont-gothic/ipag.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf",
            "/System/Library/Fonts/PingFang.ttc",  # MacOS
            "C:\\Windows\\Fonts\\msjh.ttc",      # Windows
            "C:\\Windows\\Fonts\\msgothic.ttc",  # Windows
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        return None

    def generate_wordcloud(
        self, 
        frequencies: Counter[str], 
        output_path: Path, 
        width: int = 800, 
        height: int = 400,
        background_color: str = "white"
    ) -> bool:
        """Generate a word cloud image and save it to output_path."""
        if not frequencies:
            return False

        try:
            wc = WordCloud(
                font_path=self.font_path,
                width=width,
                height=height,
                background_color=background_color,
                random_state=self.random_state,
                max_words=100
            )
            wc.generate_from_frequencies(frequencies)
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            wc.to_file(str(output_path))
            return True
        except Exception as e:
            print(f"Error generating wordcloud: {e}")
            return False

    def generate_pie_chart(
        self, 
        frequencies: Counter[str], 
        output_path: Path, 
        top_n: int = 20,
        title: str = "Word Frequency Distribution"
    ) -> bool:
        """Generate a pie chart of top N words and save it to output_path."""
        if not frequencies:
            return False

        try:
            # Get top N items
            top_items = frequencies.most_common(top_n)
            labels = [item[0] for item in top_items]
            values = [item[1] for item in top_items]

            plt.figure(figsize=(10, 7))
            
            # Setup font for Japanese labels if needed
            if self.font_path:
                from matplotlib import font_manager
                prop = font_manager.FontProperties(fname=self.font_path)
            else:
                prop = None

            patches, texts, autotexts = plt.pie(
                values, 
                labels=labels, 
                autopct='%1.1f%%', 
                startangle=140,
                textprops={'fontproperties': prop} if prop else None
            )
            
            # Matplotlib pie labels can be tricky with Japanese, let's also set legend
            if prop:
                plt.legend(patches, labels, prop=prop, loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))

            plt.title(title, fontproperties=prop)
            plt.axis('equal')
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(str(output_path), bbox_inches='tight')
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating pie chart: {e}")
            if plt.get_fignums():
                plt.close()
            return False
