#!/usr/bin/env python3
"""articles.published_at のタイムゾーン表記を naive JST に統一する一回限りのマイグレーション.

`+00:00` / `Z` / `+09:00` などのオフセット付き値を JST に変換してオフセットを外す。
オフセット無しの値は既に JST とみなして変更しない。冪等。

使い方:
  uv run python scripts/normalize_published_at.py            # dry-run（既定）
  uv run python scripts/normalize_published_at.py --apply    # バックアップを作って反映
"""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from news_crawler.normalizer import JST


def convert(value: str) -> str | None:
    """オフセット付きなら naive JST の ISO 文字列を返す。変更不要なら None."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return None
    return dt.astimezone(JST).replace(tzinfo=None).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="published_at を naive JST に統一")
    ap.add_argument("--db", type=Path, default=Path("data/articles.db"))
    ap.add_argument("--apply", action="store_true", help="DBへ反映（既定は dry-run）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    rows = conn.execute("SELECT id, published_at FROM articles WHERE published_at IS NOT NULL")
    updates = [
        (new, row_id)
        for row_id, value in rows.fetchall()
        if (new := convert(value)) is not None
    ]
    print(f"{len(updates)} row(s) need conversion")
    for new, row_id in updates[:5]:
        print(f"  id={row_id} -> {new}")

    if not args.apply:
        print("dry-run: 反映するには --apply を付けてください")
        return
    if updates:
        backup = args.db.with_name(f"{args.db.name}.bak_{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(args.db, backup)
        print(f"backup: {backup}")
        with conn:
            conn.executemany("UPDATE articles SET published_at = ? WHERE id = ?", updates)
    print("done")


if __name__ == "__main__":
    main()
