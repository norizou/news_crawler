"""Tests for visualization module."""

import pytest
from pathlib import Path
from collections import Counter
from ai_scraper.visualization import VisualizationGenerator

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

def test_generate_pie_chart(tmp_path):
    viz = VisualizationGenerator()
    freq = Counter({
        "Apple": 50,
        "Banana": 30,
        "Cherry": 20,
        "Date": 10,
        "Elderberry": 5
    })
    
    output_path = tmp_path / "pie_chart.png"
    success = viz.generate_pie_chart(freq, output_path, title="Test Fruits")
    
    assert success
    assert output_path.exists()
    assert output_path.stat().st_size > 0

def test_empty_frequencies(tmp_path):
    viz = VisualizationGenerator()
    freq = Counter()
    
    output_path = tmp_path / "empty.png"
    assert not viz.generate_wordcloud(freq, output_path)
    assert not viz.generate_pie_chart(freq, output_path)
    assert not output_path.exists()
