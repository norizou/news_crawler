"""tests for scripts/infer_keiba_crowns.py."""

import csv
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "infer_keiba_crowns.py"
spec = importlib.util.spec_from_file_location("infer_keiba_crowns", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_normalize_name_nfkc_and_all_whitespace():
    assert mod.normalize_name("ﾒｲｼｮｳ　ﾄﾞﾄｳ") == "メイショウドトウ"
    assert mod.normalize_name("  AB C\t1\n") == "ABC1"


def test_load_uma_names_first_column_and_dedupe(tmp_path):
    path = tmp_path / "uma.csv"
    path.write_text(
        "イクイノックス,1285,1285,10000,名詞,固有名詞,一般,*,*,*,イクイノックス,イクイノックス,イクイノックス\n"
        "ミレニアムバイオ ,1289,1289,10000,名詞,固有名詞,一般,*,*,*,a,b,c\n"
        "ミレニアムバイオ,1,1,1,名詞,固有名詞,一般,*,*,*,a,b,c\n"
        "\n",
        encoding="utf-8",
    )
    assert mod.load_uma_names(path) == {"イクイノックス", "ミレニアムバイオ"}


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_run_psql_csv_argv_and_parsing(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        return _completed("ordinal_position,column_name\n1,bamei\n2,banushimei\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rows = mod.run_psql_csv("postgresql://example/db", "SELECT 1")
    assert calls == [
        [
            "psql",
            "postgresql://example/db",
            "-X",
            "--csv",
            "--tuples-only",
            "--command",
            "SELECT 1",
        ]
    ]
    # --csv なのでヘッダー行も戻り値に含まれる
    assert rows == [["ordinal_position", "column_name"], ["1", "bamei"], ["2", "banushimei"]]


def test_load_owner_horses_missing_column_skips_query(monkeypatch):
    outputs = {
        mod.SCHEMA_QUERY: "1,bamei\n",
        mod.OWNER_HORSES_QUERY: "x,y\n",
    }
    calls = []

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd[-1])
        return _completed(outputs[cmd[-1]])

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        mod.load_owner_horses("dsn://x")
    assert "banushimei" in str(e.value)
    assert calls == [mod.SCHEMA_QUERY]


def test_load_owner_horses_groups_by_owner(monkeypatch):
    outputs = {
        mod.SCHEMA_QUERY: "1,bamei\n2,banushimei\n",
        mod.OWNER_HORSES_QUERY: "メイショウA,名 和\nメイショウA,名和\nメイショウB,名和\n",
    }

    def fake_run(cmd, capture_output, text, check):
        return _completed(outputs[cmd[-1]])

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.load_owner_horses("dsn://x") == {"名和": {"メイショウA", "メイショウB"}}


def _owner_horses(owner: str, horses: list[str]) -> dict[str, set[str]]:
    return {owner: set(horses)}


def test_infer_prefix_candidates_min_count_boundary():
    owner_horses = _owner_horses(
        "owner1",
        ["メイショウA", "メイショウB", "メイショウC", "キタサンX", "キタサンY", "キタサンZ"],
    )
    # min_count=4 で 3頭一致は除外、4頭一致は採用
    owner_horses["owner1"].add("メイショウD")
    result = mod.infer_owner_candidates(owner_horses, min_count=4)
    assert [(c.position, c.crown) for c in result] == [("prefix", "メイショウ")]


def test_infer_suffix_candidates():
    owner_horses = _owner_horses(
        "owner1", ["アルファサン", "ベータサン", "ガンマサン", "デルタサン"]
    )
    result = mod.infer_owner_candidates(owner_horses, min_count=4)
    assert [(c.position, c.crown) for c in result] == [("suffix", "サン")]
    assert result[0].horses == frozenset(
        {"アルファサン", "ベータサン", "ガンマサン", "デルタサン"}
    )


def test_eligible_requires_two_distinct_nonempty_remainders():
    # 残部が1種類しかない (空残部は除外) => 不適格
    assert not mod._eligible("メイショウ", {"メイショウ", "メイショウ"}, "prefix", 1)
    assert mod._eligible("メイショウ", {"メイショウA", "メイショウB"}, "prefix", 2)
    assert not mod._eligible("サン", {"Aサン"}, "suffix", 1)


def test_infer_ignores_duplicate_and_blank_names():
    # 正規化・重複排除後に min_count を満たさない => 候補なし
    owner_horses = {"owner1": {" メイショウA ", "メイショウA", "", "  "}}
    assert mod.infer_owner_candidates(owner_horses, min_count=2) == []


def test_infer_collapses_same_horse_set_to_longest():
    owner_horses = _owner_horses(
        "owner1", ["メイショウA", "メイショウB", "メイショウC", "メイショウD"]
    )
    result = mod.infer_owner_candidates(owner_horses, min_count=4, min_length=2)
    crowns = [c.crown for c in result if c.position == "prefix"]
    # メイ, メイシ, メイショ, メイショウ はすべて同じ馬集合 => 最長のメイショウのみ
    assert crowns == ["メイショウ"]


def test_infer_validates_params():
    owner_horses = _owner_horses("o", {"ab", "ac"})
    with pytest.raises(ValueError):
        mod.infer_owner_candidates(owner_horses, min_count=1)
    with pytest.raises(ValueError):
        mod.infer_owner_candidates(owner_horses, min_length=0)
    with pytest.raises(ValueError):
        mod.infer_owner_candidates(owner_horses, min_length=4, max_length=3)


def test_gojuon_sort_key_orders_katakana():
    values = ["メイショウ", "ガンバレ", "エイシン", "カレン", "アドマイヤ", "キタサン"]
    assert sorted(values, key=mod.gojuon_sort_key) == [
        "アドマイヤ", "エイシン", "カレン", "ガンバレ", "キタサン", "メイショウ"
    ]


def test_aggregate_across_owners_and_gojuon_sorting():
    cands = [
        mod.OwnerCandidate("prefix", "メイ", "ownerB", frozenset({"メイB1", "メイB2"})),
        mod.OwnerCandidate("prefix", "メイ", "ownerA", frozenset({"メイA1"})),
        mod.OwnerCandidate("suffix", "サン", "ownerA", frozenset({"Xサン"})),
        mod.OwnerCandidate("prefix", "サン", "ownerA", frozenset({"サンX"})),
    ]
    uma = {"メイA1", "メイB1", "メイB2", "メイC1", "メイ", "Xサン", "Yサン"}
    result = mod.aggregate_candidates(cands, uma, example_limit=2)
    # 五十音順: サン(さ) が メイ(め) より先。同冠名は prefix が先
    assert [(c.position, c.crown) for c in result] == [
        ("prefix", "サン"), ("suffix", "サン"), ("prefix", "メイ")
    ]
    mei = next(c for c in result if c.crown == "メイ")
    assert mei.db_horse_count == 3
    assert mei.owner_count == 2
    assert mei.owner_names == ("ownerA", "ownerB")
    assert mei.uma_horse_count == 4
    assert mei.examples == ("メイA1", "メイB1")
    san = next(c for c in result if c.position == "suffix")
    assert san.uma_horse_count == 2


def _cand(position, crown, db, owners):
    return mod.CrownCandidate(
        position=position,
        crown=crown,
        db_horse_count=db,
        owner_count=owners,
        owner_names=tuple(f"o{i}" for i in range(owners)),
        uma_horse_count=0,
        examples=(),
    )


def test_filter_candidates_boundaries():
    cands = [
        _cand("prefix", "A", 75, 15),   # 境界: 採用
        _cand("prefix", "B", 75, 16),   # 馬主数超過: 除外
        _cand("suffix", "C", 75, 3),    # 境界: 採用
        _cand("suffix", "D", 75, 4),    # 馬主数超過: 除外
        _cand("prefix", "E", 74, 1),    # DB数不足: 除外
    ]
    result = mod.filter_candidates(cands)
    assert [c.crown for c in result] == ["A", "C"]


def test_filter_candidates_preserves_order():
    cands = [_cand("prefix", "Z", 100, 1), _cand("prefix", "A", 100, 1)]
    assert [c.crown for c in mod.filter_candidates(cands)] == ["Z", "A"]


def test_filter_candidates_invalid_thresholds():
    cands = [_cand("prefix", "A", 100, 1)]
    with pytest.raises(ValueError):
        mod.filter_candidates(cands, min_db_count=0)
    with pytest.raises(ValueError):
        mod.filter_candidates(cands, max_prefix_owners=0)
    with pytest.raises(ValueError):
        mod.filter_candidates(cands, max_suffix_owners=0)


def test_write_candidates_refuses_existing_and_forces(tmp_path):
    path = tmp_path / "out.csv"
    cand = mod.CrownCandidate(
        position="prefix",
        crown="メイ",
        db_horse_count=3,
        owner_count=2,
        owner_names=("ownerA", "ownerB"),
        uma_horse_count=4,
        examples=("メイA1", "メイB1"),
    )
    mod.write_candidates(path, [cand])
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(mod.CSV_HEADER)
    assert rows[1] == ["prefix", "メイ", "3", "2", "ownerA | ownerB", "4", "メイA1 | メイB1"]

    with pytest.raises(FileExistsError):
        mod.write_candidates(path, [cand])

    mod.write_candidates(path, [], force=True)
    with open(path, encoding="utf-8") as f:
        assert list(csv.reader(f)) == [list(mod.CSV_HEADER)]
