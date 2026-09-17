"""tests for scripts/build_keiba_dict.py."""

import argparse
import csv
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_keiba_dict.py"
spec = importlib.util.spec_from_file_location("build_keiba_dict", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_to_sudachi_row_is_18_columns_with_correct_pos():
    row = mod.to_sudachi_row("テソーロ")
    assert len(row) == 18
    assert row[5:11] == list(mod.DEFAULT_POS)
    assert row[0] == "テソーロ"
    assert row[4] == "テソーロ"   # 読み
    assert row[11] == "テソーロ"  # 正規化見出し
    assert row[12] == "テソーロ"  # 辞書形


def test_convert_legacy_20col_row_to_18():
    surface = "テソーロ"
    legacy = [
        surface, "-1", "0", "3000", surface, "読ミ",
        *mod.DEFAULT_POS, surface, "*", "A", "*", "*", "*", "*", "*",
    ]
    assert len(legacy) == 20
    out = mod.convert_row(legacy)
    assert len(out) == 18
    assert out[1] == "0"  # -1 補正
    assert out[5:11] == list(mod.DEFAULT_POS)
    assert out[4] == surface   # 見出し
    assert out[11] == "読ミ"   # row[5] の読みが col11 へ
    assert out[12] == surface  # row[12] の正規化見出しが col12 へ


def test_convert_row_18col_passthrough():
    row = mod.to_sudachi_row("メイショウ")
    assert mod.convert_row(list(row)) == row


def test_convert_row_malformed_20col_passthrough_no_crash():
    row = ["x"] * 20
    out = mod.convert_row(row)
    assert out == row


def test_normalize_db_name_strips_all_whitespace():
    assert mod.normalize_db_name("武　豊　　　　　　　　　") == "武豊"
    assert mod.normalize_db_name("  ディープ インパクト  ") == "ディープインパクト"


def test_parse_categories_dedupes_preserving_order():
    assert mod.parse_categories("owners,jockeys,owners") == ("owners", "jockeys")
    assert mod.parse_categories(" horses , races ") == ("horses", "races")


def test_parse_categories_invalid():
    with pytest.raises(argparse.ArgumentTypeError):
        mod.parse_categories("horses,unknown")


def test_parse_categories_empty():
    with pytest.raises(argparse.ArgumentTypeError):
        mod.parse_categories(" , ,")


def _completed(stdout: str):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_load_pckeiba_normalizes_and_dedupes(monkeypatch):
    outputs = {
        "horses": "ディープインパクト　　　\n\n キタサンブラック\n共有名\n",
        "jockeys": "武　豊　　　　　　　　　\n共有名\n武　豊\n",
    }
    calls = []

    def _query_to_cat(cmd):
        return next(cat for cat, q in mod.PCKEIBA_QUERIES.items() if q == cmd[-1])

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        return _completed(outputs[_query_to_cat(cmd)])

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rows = mod.load_pckeiba("dsn://x", ("horses", "jockeys"))

    surfaces = [r[0] for r in rows]
    assert surfaces == ["キタサンブラック", "ディープインパクト", "共有名", "武豊"]
    assert calls == [
        ["psql", "dsn://x", "-At", "-c", mod.PCKEIBA_QUERIES["horses"]],
        ["psql", "dsn://x", "-At", "-c", mod.PCKEIBA_QUERIES["jockeys"]],
    ]


def test_load_pckeiba_db_error_exits_with_category(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        raise subprocess.CalledProcessError(1, cmd, stderr="connection refused")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        mod.load_pckeiba("dsn://x", ("races",))
    assert "races" in str(e.value)


def test_load_crowns_requires_crown_header(tmp_path):
    path = tmp_path / "crowns.csv"
    path.write_text("foo,bar\nメイショウ,1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mod.load_crowns(path)
    assert "crown" in str(e.value)


def test_load_crowns_normalizes_blanks_and_dedupes(tmp_path):
    path = tmp_path / "crowns.csv"
    path.write_text(
        "position,crown,db_horse_count\n"
        "prefix,メイショウ,10\n"
        "suffix, サン ,5\n"
        "prefix,,3\n"
        "prefix,メイショウ,2\n",
        encoding="utf-8",
    )
    rows = mod.load_crowns(path)
    assert rows == [mod.to_sudachi_row("メイショウ"), mod.to_sudachi_row("サン")]


def test_categories_default_excludes_horses():
    assert "horses" not in mod.DEFAULT_PCKEIBA_CATEGORIES
    assert mod.DEFAULT_PCKEIBA_CATEGORIES == ("jockeys", "races", "trainers", "owners")
    assert "horses" in mod.PCKEIBA_CATEGORIES


def test_parse_categories_explicit_horses_still_valid():
    assert mod.parse_categories("horses") == ("horses",)


def _three_rows():
    return [mod.to_sudachi_row(n) for n in ("馬A", "馬B", "馬C")]


def test_build_overwrites_existing_outputs_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CHUNK_SIZE", 2)
    out = tmp_path / "dict"
    out.mkdir()
    (out / "test_1.dic").write_text("old", encoding="utf-8")
    final_dir = out.resolve()

    def fake_run(cmd, check):
        out_dic = Path(cmd[cmd.index("-o") + 1])
        in_csv = Path(cmd[-1])
        assert out_dic.parent != final_dir
        assert in_csv.parent == out_dic.parent
        out_dic.write_text("new:" + in_csv.read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod.build(_three_rows(), out, "test", tmp_path / "system.dic")

    assert result == [str(out / "test_1.dic"), str(out / "test_2.dic")]
    dic1 = (out / "test_1.dic").read_text(encoding="utf-8")
    assert dic1.startswith("new:") and "old" != dic1
    for name in ("test_1.csv", "test_1.dic", "test_2.csv", "test_2.dic"):
        assert (out / name).exists()
    with open(out / "test_1.csv", encoding="utf-8") as f:
        csv1 = list(csv.reader(f))
    with open(out / "test_2.csv", encoding="utf-8") as f:
        csv2 = list(csv.reader(f))
    assert [r[0] for r in csv1] == ["馬A", "馬B"]
    assert [r[0] for r in csv2] == ["馬C"]


def test_main_merges_inputs_crowns_and_pckeiba_with_global_dedupe(tmp_path, monkeypatch):
    inputs = tmp_path / "in.csv"
    inputs.write_text("馬A\n共通冠\n", encoding="utf-8")
    crowns = tmp_path / "crowns.csv"
    crowns.write_text("crown\n共通冠\nサン\n", encoding="utf-8")

    captured = {}

    def fake_build(rows, out_dir, stem, sys_dic):
        captured["rows"] = rows
        return []

    def fake_run(cmd, capture_output, text, check):
        return _completed("共通冠\nサン\nDB馬\n")

    monkeypatch.setattr(mod, "build", fake_build)
    monkeypatch.setattr(mod, "system_dic_path", lambda: tmp_path / "system.dic")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_keiba_dict.py",
            str(inputs),
            "--crowns",
            str(crowns),
            "--pckeiba",
            "--categories",
            "owners",
            "--no-config-update",
            "--out-dir",
            str(tmp_path / "dict"),
        ],
    )
    mod.main()
    assert [r[0] for r in captured["rows"]] == ["馬A", "共通冠", "サン", "DB馬"]


def test_build_leaves_existing_outputs_when_build_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CHUNK_SIZE", 2)
    out = tmp_path / "dict"
    out.mkdir()
    csv1 = out / "test_1.csv"
    dic1 = out / "test_1.dic"
    csv1.write_bytes(b"old csv1")
    dic1.write_bytes(b"old dic1")
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        out_dic = Path(cmd[cmd.index("-o") + 1])
        if len(calls) == 2:
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")
        out_dic.write_text("temp dic", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        mod.build(_three_rows(), out, "test", tmp_path / "system.dic")

    assert csv1.read_bytes() == b"old csv1"
    assert dic1.read_bytes() == b"old dic1"
    assert not (out / "test_2.csv").exists()
    assert not (out / "test_2.dic").exists()
