"""Visualization module for generating word clouds."""

from collections import Counter
from pathlib import Path

import matplotlib
from matplotlib import font_manager
from wordcloud import WordCloud

# Use Agg backend to avoid GUI issues in headless environment
matplotlib.use("Agg")

# Font name fragments (lowercase) that indicate CJK/Japanese glyph coverage,
# used to search fonts matplotlib already knows about when none of the
# hardcoded candidate paths below exist on this machine.
_JAPANESE_FONT_NAME_KEYWORDS = (
    "hiragino",
    "noto sans jp",
    "noto sans cjk",
    "yu gothic",
    "yu mincho",
    "meiryo",
    "ms gothic",
    "ms mincho",
    "ud gothic",
    "biz ud",
)


class VisualizationGenerator:
    """Generator for word clouds from word frequencies."""

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path or self._detect_japanese_font()
        # Seed for deterministic layouts
        self.random_state = 42

    def _detect_japanese_font(self) -> str | None:
        """Attempt to detect a Japanese font on the system.

        Checks common install paths first (fast, no font-cache scan needed),
        then falls back to searching matplotlib's already-loaded font list by
        name, since Japanese fonts are frequently installed at user- or
        distro-specific paths (e.g. macOS's Hiragino/Noto Sans JP) that can't
        all be enumerated as fixed paths.
        """
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
            "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
            "/usr/share/fonts/truetype/ipafont-gothic/ipag.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",  # macOS
            "/System/Library/Fonts/PingFang.ttc",  # macOS (older versions)
            "C:\\Windows\\Fonts\\msjh.ttc",      # Windows
            "C:\\Windows\\Fonts\\msgothic.ttc",  # Windows
        ]
        for path in candidates:
            if Path(path).exists():
                return path

        for font in font_manager.fontManager.ttflist:
            name = font.name.lower()
            if any(kw in name for kw in _JAPANESE_FONT_NAME_KEYWORDS) and Path(
                font.fname
            ).exists():
                return font.fname

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
            if output_path.suffix.lower() == ".svg":
                svg_content = wc.to_svg(embed_font=False)
                # WordCloud.to_svg outputs <svg ... width="W" height="H">. Replace width/height
                # with responsive viewBox + style so Obsidian and browsers scale it smoothly.
                old_tag = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
                )
                new_tag = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
                    'style="max-width: 100%; height: auto;">'
                )
                if old_tag in svg_content:
                    svg_content = svg_content.replace(old_tag, new_tag, 1)
                output_path.write_text(svg_content, encoding="utf-8")
            else:
                wc.to_file(str(output_path))
            return True
        except Exception as e:
            print(f"Error generating wordcloud: {e}")
            return False
