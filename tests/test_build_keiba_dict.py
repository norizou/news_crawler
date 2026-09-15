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
