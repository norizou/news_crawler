#!/usr/bin/env python3
"""競馬用 Sudachi ユーザー辞書ビルドスクリプト.

入力ソース（複数可・混在可）:
  - MeCab形式CSV (13カラム: 見出し,左ID,右ID,コスト,品詞6,見出し,読み,原形)
  - プレーンな名詞リスト (1行1語)
  - Sudachi形式CSV (19カラム: そのまま通す)
  - PC-KEIBA DB から馬名・騎手名・レース名・調教師名・馬主名を直接取得 (--pckeiba)

Sudachi のユーザー辞書は1ファイルあたり約3.2万エントリが上限のため、
CHUNK_SIZE ごとに分割してビルドし、config/sudachi.json を自動更新する。

使い方:
  uv run python scripts/build_keiba_dict.py dict/keiba_dict.csv dict/uma.csv
  uv run python scripts/build_keiba_dict.py dict/keiba_dict.csv --pckeiba
  uv run python scripts/build_keiba_dict.py --pckeiba --categories horses,jockeys
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path

# ユーザー辞書1ファイルあたりの安全な上限
# 実測: ~28,000 エントリで正常、30,000 でルックアップが壊れる (内部ID上限 2^15-1 由来)
CHUNK_SIZE = 20000

# Sudachi ユーザー辞書 CSV カラム数
SUDACHI_COLS = 19
# MeCab 形式 (ipadic/mecab-ipadic-neologd 系) カラム数
MECAB_COLS = 13

DEFAULT_POS = ("名詞", "固有名詞", "一般", "*", "*", "*")
DEFAULT_COST = "3000"

PCKEIBA_CATEGORIES = ("horses", "jockeys", "races", "trainers", "owners")
PCKEIBA_QUERIES = {
    "horses": "SELECT bamei FROM jvd_um",
    "jockeys": "SELECT kishumei FROM jvd_ks",
    "races": "SELECT kyosomei_hondai FROM jvd_ra UNION ALL SELECT kyosomei_fukudai FROM jvd_ra",
    "trainers": "SELECT chokyoshimei FROM jvd_ch",
    "owners": "SELECT banushimei FROM jvd_bn",
}


def system_dic_path() -> Path:
    """sudachidict-core 同梱のシステム辞書パスを返す."""
    try:
        ref = resources.files("sudachidict_core") / "resources" / "system.dic"
        p = Path(str(ref))
        if p.exists():
            return p
    except Exception:
        pass
    # fallback: よくある配置
    import sudachidict_core
    p = Path(sudachidict_core.__file__).parent / "resources" / "system.dic"
    if not p.exists():
        sys.exit(
            "system.dic が見つかりません。"
            "sudachidict-core がインストールされているか確認してください"
        )
    return p


def to_sudachi_row(surface: str) -> list[str]:
    """名詞(固有名詞)として Sudachi 19カラム行を生成."""
    s = surface.strip()
    return [s, "0", "0", DEFAULT_COST, s, s, *DEFAULT_POS, s, "*", "A", "*", "*", "*", "*", "*"]


def convert_row(row: list[str]) -> list[str] | None:
    """入力行を Sudachi 形式に正規化. 変換不能なら None."""
    if not row or not row[0].strip():
        return None
    if len(row) >= SUDACHI_COLS:
        row = [c.strip() for c in row]
        # 連接ID -1 は SudachiPy の ubuild で panic するため 0 に補正
        row[1] = "0" if row[1] == "-1" else row[1]
        row[2] = "0" if row[2] == "-1" else row[2]
        return row
    if len(row) == MECAB_COLS:
        # MeCab: 見出し,左ID,右ID,コスト,品詞x6,見出し,読み,原形
        return to_sudachi_row(row[0])
    # プレーンな単語リスト扱い
    if len(row) == 1:
        return to_sudachi_row(row[0])
    return None


def load_csv_sources(paths: list[Path]) -> list[list[str]]:
    rows: list[list[str]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            sys.exit(f"入力ファイルがありません: {path}")
        with open(path, encoding="utf-8", errors="ignore") as f:
            for row in csv.reader(f):
                out = convert_row(row)
                if out and out[0] not in seen:
                    seen.add(out[0])
                    rows.append(out)
    return rows


def normalize_db_name(value: str) -> str:
    return "".join(value.split())


def parse_categories(value: str) -> tuple[str, ...]:
    categories = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    invalid = set(categories) - set(PCKEIBA_CATEGORIES)
    if invalid:
        choices = ", ".join(PCKEIBA_CATEGORIES)
        raise argparse.ArgumentTypeError(
            f"未対応カテゴリ: {', '.join(sorted(invalid))} (選択肢: {choices})"
        )
    if not categories:
        raise argparse.ArgumentTypeError("カテゴリを1つ以上指定してください")
    return categories


def load_pckeiba(dsn: str, categories: tuple[str, ...]) -> list[list[str]]:
    """PC-KEIBA DB (PostgreSQL) から指定カテゴリの名称一覧を取得.

    psql コマンド経由で取得するため psycopg2 等の追加依存は不要。
    """
    rows: list[list[str]] = []
    seen: set[str] = set()
    for category in categories:
        cmd = ["psql", dsn, "-At", "-c", PCKEIBA_QUERIES[category]]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError:
            sys.exit(
                "psql コマンドが見つかりません。"
                "PostgreSQL クライアントをインストールしてください"
            )
        except subprocess.CalledProcessError as e:
            sys.exit(f"pckeiba からの取得に失敗しました ({category}): {e.stderr.strip()}")
        names = {
            name
            for line in result.stdout.splitlines()
            if (name := normalize_db_name(line))
        }
        new_names = sorted(names - seen)
        rows.extend(to_sudachi_row(name) for name in new_names)
        seen.update(names)
        print(f"pckeiba から {category}: {len(new_names)} 件を取得")
    return rows


def build(rows: list[list[str]], out_dir: Path, stem: str, sys_dic: Path) -> list[str]:
    """CHUNK_SIZE ごとに分割して ubuild し、生成した .dic のパス一覧を返す."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dic_paths: list[str] = []
    chunks = [rows[i : i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)]
    outputs: list[tuple[Path, Path, Path, Path, int]] = []
    with tempfile.TemporaryDirectory(prefix=f".{stem}-", dir=out_dir) as temp_dir:
        temp_path = Path(temp_dir)
        for i, chunk in enumerate(chunks):
            suffix = "" if len(chunks) == 1 else f"_{i + 1}"
            csv_path = out_dir / f"{stem}{suffix}.csv"
            dic_path = out_dir / f"{stem}{suffix}.dic"
            temp_csv_path = temp_path / csv_path.name
            temp_dic_path = temp_path / dic_path.name
            with open(temp_csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(chunk)
            # 実行中の Python 環境の sudachipy を使う (venv activate 不要にする)
            sudachipy_bin = Path(sys.executable).parent / "sudachipy"
            ubuild_cmd = (
                [str(sudachipy_bin)]
                if sudachipy_bin.exists()
                else [sys.executable, "-m", "sudachipy.command_line"]
            )
            subprocess.run(
                [
                    *ubuild_cmd,
                    "ubuild",
                    "-s",
                    str(sys_dic),
                    "-o",
                    str(temp_dic_path),
                    str(temp_csv_path),
                ],
                check=True,
            )
            outputs.append((temp_csv_path, temp_dic_path, csv_path, dic_path, len(chunk)))
        for temp_csv_path, temp_dic_path, csv_path, dic_path, size in outputs:
            temp_csv_path.replace(csv_path)
            temp_dic_path.replace(dic_path)
            print(f"built {dic_path} ({size} entries)")
            rel_dic = dic_path if dic_path.is_absolute() else Path(dic_path)
            dic_paths.append(str(rel_dic))
    return dic_paths


def update_sudachi_json(config_path: Path, dic_paths: list[str]) -> None:
    """config/sudachi.json の userDict を生成した辞書で更新."""
    cfg: dict = {}
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg["userDict"] = dic_paths
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {config_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="競馬用 Sudachi ユーザー辞書のビルド")
    ap.add_argument(
        "inputs", nargs="*", help="入力CSV/テキスト (MeCab形式・Sudachi形式・単語リスト)"
    )
    ap.add_argument(
        "--pckeiba",
        action="store_true",
        help="PC-KEIBA DB から馬名・騎手名・レース名・調教師名・馬主名を取得して追加",
    )
    ap.add_argument(
        "--categories",
        type=parse_categories,
        default=PCKEIBA_CATEGORIES,
        help="DB取得カテゴリをカンマ区切りで指定 (default: horses,jockeys,races,trainers,owners)",
    )
    ap.add_argument(
        "--dsn",
        default="postgresql://postgres@localhost:5432/pckeiba",
        help="pckeiba 接続 DSN (default: postgresql://postgres@localhost:5432/pckeiba)",
    )
    ap.add_argument("--out-dir", default="dict", help="出力ディレクトリ (default: dict)")
    ap.add_argument(
        "--stem", default="keiba_user", help="出力ファイル名の接頭辞 (default: keiba_user)"
    )
    ap.add_argument("--config", default="config/sudachi.json", help="更新する sudachi.json")
    ap.add_argument("--no-config-update", action="store_true", help="sudachi.json を更新しない")
    args = ap.parse_args()

    if not args.inputs and not args.pckeiba:
        ap.error("入力ファイルか --pckeiba のどちらかを指定してください")

    rows = load_csv_sources([Path(p) for p in args.inputs]) if args.inputs else []
    if args.pckeiba:
        existing = {r[0] for r in rows}
        rows += [r for r in load_pckeiba(args.dsn, args.categories) if r[0] not in existing]

    if not rows:
        sys.exit("有効なエントリがありません")

    dic_paths = build(rows, Path(args.out_dir), args.stem, system_dic_path())
    if not args.no_config_update:
        update_sudachi_json(Path(args.config), dic_paths)
    print(f"合計 {len(rows)} エントリ / {len(dic_paths)} 辞書")


if __name__ == "__main__":
    main()
