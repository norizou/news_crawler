"""Tests for visualization module."""

from collections import Counter

from news_crawler.visualization import VisualizationGenerator


def test_visualization_generator_detection():
    # Detect font without crashing
    viz = VisualizationGenerator()
    # It might be None in some CI environments, but shouldn't crash
    assert hasattr(viz, 'font_path')

def test_generate_wordcloud(tmp_path):
    viz = VisualizationGenerator()
    freq = Counter({
        "AI": 10,
        "Python": 8,
        "Sudachi": 6,
        "Scraper": 5,
        "NLP": 4
    })

    output_path = tmp_path / "wordcloud.png"
    success = viz.generate_wordcloud(freq, output_path)

    assert success
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_generate_wordcloud_svg(tmp_path):
    viz = VisualizationGenerator()
    freq = Counter({
        "競馬": 10,
        "武豊": 8,
        "秋華賞": 6,
    })

    output_path = tmp_path / "wordcloud.svg"
    success = viz.generate_wordcloud(freq, output_path, width=800, height=400)

    assert success
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400"' in content
    assert 'style="max-width: 100%; height: auto;"' in content

def test_empty_frequencies(tmp_path):
    viz = VisualizationGenerator()
    freq = Counter()

    output_path = tmp_path / "empty.png"
    assert not viz.generate_wordcloud(freq, output_path)
    assert not output_path.exists()
