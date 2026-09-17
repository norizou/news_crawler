#!/usr/bin/env python3
"""馬主別データから冠名候補を推定するスクリプト.

PC-KEIBA DB (jvd_um) の (馬名, 馬主名) を psql 経由で取得し、
同一馬主内で共通する前方・後方文字列を冠名候補として抽出する。
dict/uma.csv の全馬名を候補の裏付け・順位付けに使い、
config/keiba_crowns.csv を生成する。不要行はユーザーが削除する。

使い方 (PGPASSWORD は環境変数で事前に設定しておくこと):
  uv run python scripts/infer_keiba_crowns.py
  uv run python scripts/infer_keiba_crowns.py --output config/keiba_crowns.csv --force
"""

import argparse
import csv
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Position = Literal["prefix", "suffix"]

SCHEMA_QUERY = """SELECT ordinal_position, column_name
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'jvd_um'
ORDER BY ordinal_position"""

OWNER_HORSES_QUERY = """SELECT DISTINCT bamei, banushimei
FROM jvd_um
WHERE NULLIF(BTRIM(bamei), '') IS NOT NULL
  AND NULLIF(BTRIM(banushimei), '') IS NOT NULL
ORDER BY banushimei, bamei"""

CSV_HEADER = (
    "position",
    "crown",
    "db_horse_count",
    "owner_count",
    "owner_names",
    "uma_horse_count",
    "examples",
)


@dataclass(frozen=True)
class OwnerCandidate:
    position: Position
    crown: str
    owner: str
    horses: frozenset[str]


@dataclass(frozen=True)
class CrownCandidate:
    position: Position
    crown: str
    db_horse_count: int
    owner_count: int
    owner_names: tuple[str, ...]
    uma_horse_count: int
    examples: tuple[str, ...]


def normalize_name(value: str) -> str:
    """NFKC正規化して全ての空白を除去."""
    return "".join(unicodedata.normalize("NFKC", value).split())


def gojuon_sort_key(value: str) -> str:
    """カタカナを五十音順に並べる決定的ソートキー (カタカナ→対応するひらがな)."""
    normalized = normalize_name(value)
    return "".join(
        chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
        for char in normalized
    )


def load_uma_names(path: Path) -> set[str]:
    """uma.csv (Sudachi形式) の第1列から正規化済み馬名の集合を返す."""
    names: set[str] = set()
    with open(path, encoding="utf-8", errors="ignore") as f:
        for row in csv.reader(f):
            if not row:
                continue
            name = normalize_name(row[0])
            if name:
                names.add(name)
    return names


def run_psql_csv(dsn: str, query: str) -> list[list[str]]:
    """psql --csv --tuples-only でクエリを実行し、行を返す."""
    cmd = ["psql", dsn, "-X", "--csv", "--tuples-only", "--command", query]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("psql コマンドが見つかりません。PostgreSQL クライアントをインストールしてください")
    except subprocess.CalledProcessError as e:
        sys.exit(f"psql の実行に失敗しました: {(e.stderr or '').strip()}")
    return [row for row in csv.reader(result.stdout.splitlines()) if row]


def load_owner_horses(dsn: str) -> dict[str, set[str]]:
    """jvd_um から {馬主名: {馬名, ...}} を取得. スキーマ検証を先に行う."""
    schema_rows = run_psql_csv(dsn, SCHEMA_QUERY)
    columns = {normalize_name(row[1]).lower() for row in schema_rows if len(row) >= 2}
    required = {"bamei", "banushimei"}
    missing = sorted(required - columns)
    if missing:
        sys.exit(f"jvd_um に必要な列がありません: {', '.join(missing)}")

    owner_horses: dict[str, set[str]] = {}
    for row in run_psql_csv(dsn, OWNER_HORSES_QUERY):
        if len(row) < 2:
            continue
        horse = normalize_name(row[0])
        owner = normalize_name(row[1])
        if horse and owner:
            owner_horses.setdefault(owner, set()).add(horse)
    return owner_horses


def _eligible(
    crown: str, horses: set[str], position: Position, min_count: int
) -> bool:
    if len(horses) < min_count:
        return False
    remainders = {
        horse[len(crown) :] if position == "prefix" else horse[: -len(crown)]
        for horse in horses
    }
    remainders.discard("")
    return len(remainders) >= 2


def infer_owner_candidates(
    owner_horses: dict[str, set[str]],
    min_count: int = 4,
    min_length: int = 2,
    max_length: int = 8,
) -> list[OwnerCandidate]:
    """馬主ごとに前方・後方の共通文字列から冠名候補を抽出."""
    if min_count < 2:
        raise ValueError("min_count は 2 以上にしてください")
    if min_length < 1:
        raise ValueError("min_length は 1 以上にしてください")
    if max_length < min_length:
        raise ValueError("max_length は min_length 以上にしてください")

    candidates: list[OwnerCandidate] = []
    for owner, raw_horses in owner_horses.items():
        horses = {h for h in (normalize_name(h) for h in raw_horses) if h}
        positions: tuple[Position, ...] = ("prefix", "suffix")
        for position in positions:
            affix_horses: dict[str, set[str]] = {}
            for horse in horses:
                upper = min(max_length, len(horse) - 1)
                for length in range(min_length, upper + 1):
                    crown = horse[:length] if position == "prefix" else horse[-length:]
                    affix_horses.setdefault(crown, set()).add(horse)
            # 同じ馬集合の候補は最長だけ残す (同長は辞書順の先頭)
            by_horse_set: dict[frozenset[str], str] = {}
            for crown, matched in affix_horses.items():
                if not _eligible(crown, matched, position, min_count):
                    continue
                key = frozenset(matched)
                current = by_horse_set.get(key)
                if current is None or len(crown) > len(current) or (
                    len(crown) == len(current) and crown < current
                ):
                    by_horse_set[key] = crown
            for key, crown in by_horse_set.items():
                candidates.append(
                    OwnerCandidate(
                        position=position, crown=crown, owner=owner, horses=key
                    )
                )
    return sorted(candidates, key=lambda c: (c.position, c.crown, c.owner))


def aggregate_candidates(
    owner_candidates: list[OwnerCandidate],
    uma_names: set[str],
    example_limit: int = 5,
) -> list[CrownCandidate]:
    """同一 (位置, 冠名) を馬主横断で集約し uma.csv の裏付けを付加."""
    horses_by_key: dict[tuple[Position, str], set[str]] = {}
    owners_by_key: dict[tuple[Position, str], set[str]] = {}
    for cand in owner_candidates:
        key = (cand.position, cand.crown)
        horses_by_key.setdefault(key, set()).update(cand.horses)
        owners_by_key.setdefault(key, set()).add(cand.owner)

    results: list[CrownCandidate] = []
    for (position, crown), horses in horses_by_key.items():
        if position == "prefix":
            matches = [n for n in uma_names if n.startswith(crown) and n != crown]
        else:
            matches = [n for n in uma_names if n.endswith(crown) and n != crown]
        matches.sort()
        owners = sorted(owners_by_key[(position, crown)])
        results.append(
            CrownCandidate(
                position=position,
                crown=crown,
                db_horse_count=len(horses),
                owner_count=len(owners),
                owner_names=tuple(owners),
                uma_horse_count=len(matches),
                examples=tuple(matches[:example_limit]),
            )
        )
    return sorted(
        results,
        key=lambda c: (gojuon_sort_key(c.crown), c.crown, c.position),
    )


def filter_candidates(
    candidates: list[CrownCandidate],
    min_db_count: int = 75,
    max_prefix_owners: int = 15,
    max_suffix_owners: int = 3,
) -> list[CrownCandidate]:
    """人手レビュー用に候補を絞り込む.

    DB出現数が少ない候補と、多数馬主にまたがる一般語的な候補を除外する。
    後方候補は一般語を拾いやすいため馬主数の上限を厳しくする。
    """
    if min_db_count < 1:
        raise ValueError("min_db_count は 1 以上にしてください")
    if max_prefix_owners < 1:
        raise ValueError("max_prefix_owners は 1 以上にしてください")
    if max_suffix_owners < 1:
        raise ValueError("max_suffix_owners は 1 以上にしてください")
    return [
        candidate
        for candidate in candidates
        if candidate.db_horse_count >= min_db_count
        and candidate.owner_count
        <= (max_prefix_owners if candidate.position == "prefix" else max_suffix_owners)
    ]


def write_candidates(
    path: Path, candidates: list[CrownCandidate], force: bool = False
) -> None:
    """候補CSVを原子的に書き出す. 既存ファイルは force なしでは上書きしない."""
    if path.exists() and not force:
        raise FileExistsError(f"出力先が既に存在します: {path} (--force で上書き)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}-",
        delete=False,
    ) as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for c in candidates:
            writer.writerow(
                [
                    c.position,
                    c.crown,
                    c.db_horse_count,
                    c.owner_count,
                    " | ".join(c.owner_names),
                    c.uma_horse_count,
                    " | ".join(c.examples),
                ]
            )
        temp_name = f.name
    Path(temp_name).replace(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="馬主別データから冠名候補を推定")
    ap.add_argument(
        "--dsn",
        default="postgresql://postgres@localhost:5432/pckeiba",
        help="pckeiba 接続 DSN (PGPASSWORD は環境変数で指定)",
    )
    ap.add_argument(
        "--uma-csv",
        type=Path,
        default=Path("dict/uma.csv"),
        help="全馬名CSV (Sudachi形式、第1列を使用)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("config/keiba_crowns.csv"),
        help="候補CSV出力先",
    )
    ap.add_argument("--min-count", type=int, default=4, help="同一馬主内の最小一致頭数")
    ap.add_argument("--min-length", type=int, default=2, help="冠名の最小文字数")
    ap.add_argument("--max-length", type=int, default=8, help="冠名の最大文字数")
    ap.add_argument("--example-limit", type=int, default=5, help="例示馬名の最大件数")
    ap.add_argument(
        "--min-db-count", type=int, default=75, help="候補の最小DB出現頭数"
    )
    ap.add_argument(
        "--max-prefix-owners",
        type=int,
        default=15,
        help="前方冠名の最大馬主数 (超過は一般語的とみなす)",
    )
    ap.add_argument(
        "--max-suffix-owners",
        type=int,
        default=3,
        help="後方冠名の最大馬主数 (超過は一般語的とみなす)",
    )
    ap.add_argument("--force", action="store_true", help="既存の出力を上書きする")
    args = ap.parse_args()

    owner_horses = load_owner_horses(args.dsn)
    print(f"馬主数: {len(owner_horses)}")

    owner_candidates = infer_owner_candidates(
        owner_horses,
        min_count=args.min_count,
        min_length=args.min_length,
        max_length=args.max_length,
    )
    print(f"馬主別候補: {len(owner_candidates)}")

    uma_names = load_uma_names(args.uma_csv)
    all_candidates = aggregate_candidates(owner_candidates, uma_names, args.example_limit)
    candidates = filter_candidates(
        all_candidates,
        min_db_count=args.min_db_count,
        max_prefix_owners=args.max_prefix_owners,
        max_suffix_owners=args.max_suffix_owners,
    )
    print(f"候補: {len(all_candidates)} 件 -> フィルタ後 {len(candidates)} 件")

    write_candidates(args.output, candidates, force=args.force)
    print(f"候補 {len(candidates)} 件を {args.output} に出力しました")


if __name__ == "__main__":
    main()
