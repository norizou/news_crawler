"""Tests for TARGET frontier JV race comment exporter."""

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from news_crawler.cli import main
from news_crawler.database import Database
from news_crawler.models import Article, FetchMethod, SourceConfig
from news_crawler.target_exporter import (
    TargetCommentExporter,
    build_target_race_id,
    extract_schedule_map,
    parse_race_metadata,
    sanitize_for_cp932,
)


def _init_sources(db: Database) -> None:
    db.upsert_source(
        SourceConfig(
            key="jra",
            name="JRA",
            category="official",
            fetch_method=FetchMethod.HTML,
            base_url="https://jra.go.jp",
        )
    )
    db.upsert_source(
        SourceConfig(
            key="radionikkei",
            name="Radio NIKKEI",
            category="media",
            fetch_method=FetchMethod.HTML,
            base_url="https://radionikkei.jp",
        )
    )


def test_build_target_race_id():
    """Test 16-digit TARGET race ID construction."""
    # 2026-09-19 4回中山5日 11R -> 2026091906040511
    race_id = build_target_race_id("2026-09-19", "中山", kai=4, nichi=5, race_num=11)
    assert race_id == "2026091906040511"
    assert len(race_id) == 16

    # 2026-09-19 4回阪神5日 1R -> 2026091909040501
    race_id2 = build_target_race_id("2026-09-19", "阪神", kai=4, nichi=5, race_num=1)
    assert race_id2 == "2026091909040501"

    # Invalid venue
    with pytest.raises(ValueError):
        build_target_race_id("2026-09-19", "大井", kai=1, nichi=1, race_num=1)


def test_sanitize_for_cp932():
    """Test character sanitization for CP932."""
    raw = "テスト〜波ダッシュ―エムダッシュ−マイナス"
    sanitized = sanitize_for_cp932(raw)
    assert "\u301c" not in sanitized
    assert "\uff5e" in sanitized
    # Must be encodable to CP932 without error
    sanitized.encode("cp932")


def test_parse_race_metadata():
    """Test parsing race metadata from article title/content."""
    # Radio NIKKEI style
    rn_title = "【ながつきＳ】（中山）伏兵ハナウマビーチが後方から豪快な末脚で差し切る"
    rn_content = (
        "中山１１Ｒのながつきステークス（３歳以上オープン・ダート１８００ｍ）は"
        "１２番人気ハナウマビーチが勝利した。"
    )
    res = parse_race_metadata(rn_title, rn_content)
    assert res is not None
    venue, r_num, r_name = res
    assert venue == "中山"
    assert r_num == 11
    assert "ながつき" in r_name

    # netkeiba comment style
    nk_title = "【メイクデビュー中山4Rレース後コメント】シルバーノリダー小林美駒騎手ら"
    nk_content = "1着 シルバーノリダー(小林美駒騎手) 「...」"
    res2 = parse_race_metadata(nk_title, nk_content)
    assert res2 is not None
    v2, r2, name2 = res2
    assert v2 == "中山"
    assert r2 == 4

    # Preview or column should be ignored
    col_title = "【コラム】騎手の腕よりも馬場と風への対応がカギに！？"
    assert parse_race_metadata(col_title, "阪神11R") is None

    win5_title = "19日のWIN5は単勝2桁人気馬が2勝で的中ゼロ！"
    assert parse_race_metadata(win5_title, "中山9R") is None


def test_extract_schedule_map(tmp_path: Path):
    """Test schedule extraction from JRA official news."""
    db_path = tmp_path / "test_schedule.db"
    db = Database(db_path)
    _init_sources(db)

    jra_art = Article(
        source_key="jra",
        url="https://example.com/jra1",
        normalized_url="https://example.com/jra1",
        title="開催競馬場・今日の出来事（9月19日（土曜））",
        content="今日の出来事\n第4回中山第5日（9月19日（土曜））\n第4回阪神第5日（9月19日（土曜））\n",
        published_at=datetime(2026, 9, 19, 10, 0, 0),
        category="official",
    )
    db.upsert_article(jra_art)

    schedule = extract_schedule_map(db, "2026-09-19")
    assert schedule.get("中山") == (4, 5)
    assert schedule.get("阪神") == (4, 5)


def test_target_exporter_csv_and_file(tmp_path: Path):
    """Test CSV generation and export to CP932 file."""
    db_path = tmp_path / "test_export.db"
    db = Database(db_path)
    _init_sources(db)

    jra_art = Article(
        source_key="jra",
        url="https://example.com/jra",
        normalized_url="https://example.com/jra",
        title="今日の出来事",
        content="第4回中山第5日（9月19日（土曜））",
        published_at=datetime(2026, 9, 19, 10, 0, 0),
        category="official",
    )
    db.upsert_article(jra_art)

    race_art = Article(
        source_key="radionikkei",
        url="https://example.com/rn1",
        normalized_url="https://example.com/rn1",
        title="【ながつきＳ】（中山）ハナウマビーチ勝利",
        content=(
            "中山１１Ｒのながつきステークスはハナウマビーチが勝利した。\n"
            "レース後のコメント\n"
            "１着　ハナウマビーチ（横山琉人騎手）\n"
            "「手応え通り伸びました」\n"
        ),
        published_at=datetime(2026, 9, 19, 17, 0, 0),
        category="media",
    )
    db.upsert_article(race_art)

    exporter = TargetCommentExporter(db)
    items = exporter.extract_race_comments("2026-09-19")
    assert len(items) == 1
    it = items[0]
    assert it.race_id == "2026091906040511"
    assert it.kaisai_name == "4回中山5日"
    assert "ハナウマビーチ" in it.comment_text

    # Test Format 'target' single-line (default)
    csv_target = exporter.generate_csv_content(items, format_type="target", single_line=True)
    assert "4回中山5日,2026091906040511," in csv_target
    assert "「手応え通り伸びました」" in csv_target
    assert len(csv_target.strip().splitlines()) == 1

    # Test Format 'target' multi-line
    csv_multi = exporter.generate_csv_content(items, format_type="target", single_line=False)
    assert len(csv_multi.strip().splitlines()) > 1

    # Test Format 'simple' (Format 1)
    csv_simple = exporter.generate_csv_content(items, format_type="simple")
    assert csv_simple.startswith("2026091906040511,")

    # Test Export to file with CP932
    out_csv = tmp_path / "target_out.csv"
    exporter.export_to_file(items, out_csv, encoding="cp932")
    assert out_csv.exists()

    # Read back bytes to verify CP932 decoding
    content = out_csv.read_bytes().decode("cp932")
    assert "4回中山5日" in content
    assert "2026091906040511" in content


def test_cli_export_comments(tmp_path: Path):
    """Test 'export-comments' command via CLI runner."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    db_path = tmp_path / "cli_test.db"

    (cfg_dir / "crawler.yaml").write_text(
        f"""
crawler:
  download_delay: 0.0
  database_path: "{db_path}"
  output_dir: "{tmp_path / 'output'}"
""",
        encoding="utf-8",
    )
    (cfg_dir / "sources.yaml").write_text(
        """
sources:
  jra:
    name: "JRA"
    category: "official"
    fetch_method: "html"
    enabled: true
    base_url: "https://example.com"
""",
        encoding="utf-8",
    )

    db = Database(db_path)
    _init_sources(db)
    jra_art = Article(
        source_key="jra",
        url="https://example.com/jra",
        normalized_url="https://example.com/jra",
        title="今日の出来事",
        content="第4回中山第5日（9月19日（土曜））",
        published_at=datetime(2026, 9, 19, 10, 0, 0),
        category="official",
    )
    race_art = Article(
        source_key="radionikkei",
        url="https://example.com/rn1",
        normalized_url="https://example.com/rn1",
        title="【ながつきＳ】（中山）ハナウマビーチ勝利",
        content=(
            "中山１１Ｒのながつきステークスはハナウマビーチが勝利した。\n"
            "レース後のコメント\n"
            "１着 ハナウマビーチ\n"
            "「強かった」"
        ),
        published_at=datetime(2026, 9, 19, 17, 0, 0),
        category="media",
    )
    db.upsert_article(jra_art)
    db.upsert_article(race_art)

    runner = CliRunner()
    res = runner.invoke(
        main,
        [
            "export-comments",
            "--date",
            "2026-09-19",
            "--race",
            "ながつき",
            "--config-dir",
            str(cfg_dir),
        ],
    )
    assert res.exit_code == 0
    assert "Successfully exported 1 race comment(s)" in res.output
    assert "2026091906040511" in res.output
    assert "ながつきS" in res.output
