"""TARGET frontier JV race comment exporter.

Converts race-specific news articles and post-race comments from the database
into CSV format for bulk import into TARGET frontier JV (FAQ 612 compliant).
"""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from news_crawler.database import Database
from news_crawler.models import Article

# JRA Venue codes (2 digits)
VENUE_CODES: dict[str, str] = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}

VENUE_PATTERN = "|".join(VENUE_CODES.keys())
RACE_REGEX = re.compile(rf"({VENUE_PATTERN})\s*([0-9０-９]{{1,2}})\s*[rRＲ]")
VENUE_PAREN_REGEX = re.compile(rf"[（(]({VENUE_PATTERN})[)）]")
JRA_SCHEDULE_REGEX = re.compile(
    rf"第\s*(\d+|[０-９]+)\s*回\s*({VENUE_PATTERN})\s*第\s*(\d+|[０-９]+)\s*日"
    r"(?:\s*[（(]\s*(\d+|[０-９]+)\s*月\s*(\d+|[０-９]+)\s*日[^\)）]*[\)）])?"
)

EXCLUDE_TITLE_WORDS = [
    "予想",
    "◎",
    "切り札",
    "勝負レース",
    "出目と運勢",
    "万馬券queen",
    "コラム",
    "win5",
    "繁殖",
    "出来事一覧",
    "夜間発売",
    "優駿",
    "引退式",
    "テレビ・ラジオ中継",
]


@dataclass
class RaceCommentItem:
    """Represents a single race's comment data ready for TARGET export."""

    race_id: str  # 16-digit TARGET race ID: YYYYMMDDPPKKNNRR
    kaisai_name: str  # e.g., "4回中山5日"
    race_date: str  # YYYY-MM-DD
    venue: str  # e.g., "中山"
    kai: int  # e.g., 4
    nichi: int  # e.g., 5
    race_num: int  # e.g., 11
    race_name: str  # e.g., "ながつきステークス"
    comment_text: str  # Consolidated comment body
    source_articles: list[Article] = field(default_factory=list)


def sanitize_for_cp932(text: str) -> str:
    """Sanitize Unicode characters that commonly cause issues in CP932 (Windows-31J)."""
    # Wave dash to fullwidth tilde
    text = text.replace("\u301c", "\uff5e")
    # En/Em dash to horizontal bar
    text = text.replace("\u2014", "\u2015").replace("\u2013", "\u2015")
    # Minus sign
    text = text.replace("\u2212", "\uff0d")
    return text


def flatten_comment_to_single_line(text: str, separator: str = " ") -> str:
    """Flatten multi-line comment into a single line for TARGET CSV compatibility."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return separator.join(lines)


def build_target_race_id(
    date_str: str,
    venue: str,
    kai: int,
    nichi: int,
    race_num: int,
) -> str:
    """
    Build a 16-digit TARGET frontier JV race ID (新仕様レースID, 馬番無).
    Format: YYYYMMDDPPKKNNRR
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    yyyy = f"{dt.year:04d}"
    mm = f"{dt.month:02d}"
    dd = f"{dt.day:02d}"

    venue_code = VENUE_CODES.get(venue)
    if not venue_code:
        raise ValueError(f"Unknown venue '{venue}'. Must be one of {list(VENUE_CODES.keys())}")

    pp = venue_code
    kk = f"{kai:02d}"
    nn = f"{nichi:02d}"
    rr = f"{race_num:02d}"

    return f"{yyyy}{mm}{dd}{pp}{kk}{nn}{rr}"


def get_latest_race_date(db: Database) -> str:
    """
    Find the latest valid race date (not in the future) with published articles in the DB.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date(published_at) FROM articles "
            "WHERE published_at IS NOT NULL AND date(published_at) <= ? "
            "ORDER BY published_at DESC LIMIT 1",
            (today,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            return str(row[0])
    return today


def extract_schedule_map(db: Database, target_date: str) -> dict[str, tuple[int, int]]:
    """
    Extract (kai, nichi) schedule per venue from JRA official news articles in database.
    Returns: {venue_name: (kai, nichi)}
    """
    articles = db.get_recent_articles(days=30, source_key="jra", limit=100)
    schedule_map: dict[str, tuple[int, int]] = {}

    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    t_month = target_dt.month
    t_day = target_dt.day

    for art in articles:
        content = art.content or ""
        norm_content = unicodedata.normalize("NFKC", content)
        for m in JRA_SCHEDULE_REGEX.finditer(norm_content):
            kai = int(m.group(1))
            venue = m.group(2)
            nichi = int(m.group(3))
            m_month = int(m.group(4)) if m.group(4) else None
            m_day = int(m.group(5)) if m.group(5) else None

            if m_month is not None and m_day is not None:
                if m_month == t_month and m_day == t_day:
                    schedule_map[venue] = (kai, nichi)
            elif venue not in schedule_map:
                # If no month/day specified in parentheses, check article published date
                if art.published_at and art.published_at.strftime("%Y-%m-%d") == target_date:
                    schedule_map[venue] = (kai, nichi)

    return schedule_map


def parse_race_metadata(
    title: str,
    content: str | None,
) -> tuple[str, int, str] | None:
    """
    Parse (venue, race_num, race_name) from article title and content.
    Returns None if article is not race-specific or is a general preview/column.
    """
    norm_title = unicodedata.normalize("NFKC", title)
    norm_content = unicodedata.normalize("NFKC", content) if content else ""

    lower_title = norm_title.lower()
    for ex in EXCLUDE_TITLE_WORDS:
        if ex in lower_title:
            return None

    venue: str | None = None
    race_num: int | None = None

    # Check title for venue + race number (e.g. 中山11R, 阪神 10R)
    m = RACE_REGEX.search(norm_title)
    if m:
        venue = m.group(1)
        race_num = int(m.group(2))
    else:
        # Check title paren + content
        vp = VENUE_PAREN_REGEX.search(norm_title)
        cr = RACE_REGEX.search(norm_content[:200])
        if vp and cr and vp.group(1) == cr.group(1):
            venue = vp.group(1)
            race_num = int(cr.group(2))
        elif cr:
            venue = cr.group(1)
            race_num = int(cr.group(2))

    if not (venue and race_num):
        return None

    # Verify if article is race result, post-race comment, or race milestone
    has_comment = (
        "レース後" in norm_title
        or "レース後のコメント" in norm_content
        or "コメント" in norm_title
    )
    result_words = [
        "勝利した", "制す", "差し切る", "逃げ切る", "デビューv",
        "初勝利", "v", "勝!", "勝達成", "着",
    ]
    has_result = any(
        w in norm_title.lower() or w in norm_content[:200].lower()
        for w in result_words
    )

    if not (has_comment or has_result):
        return None

    # Extract race name
    race_name = ""
    # Look for brackets in title: 【ながつきS】 or 【大阪スポーツ杯】
    bracket_m = re.search(r"【([^】]+)】", norm_title)
    if bracket_m:
        cand = bracket_m.group(1)
        # Clean cand: remove e.g. "中山11R・", "2歳新馬・阪神6R"
        cand = re.sub(rf"({VENUE_PATTERN})\s*\d+\s*[rRＲ][・\s]*", "", cand)
        cand = re.sub(r"レース後コメント.*", "", cand)
        race_name = cand.strip()

    if not race_name:
        # Look in content (e.g. "中山11Rのながつきステークス...")
        name_m = re.search(rf"{venue}\s*{race_num}\s*[rRＲ]の([^\(（\s]+)", norm_content)
        if name_m:
            race_name = name_m.group(1).strip()

    if not race_name:
        race_name = f"{venue}{race_num}R"

    return venue, race_num, race_name


def clean_comment_body(article: Article) -> str:
    """Extract and format comment text from a single article."""
    content = article.content or ""
    if not content:
        return article.title

    norm_content = unicodedata.normalize("NFKC", content)

    # Check if Radio NIKKEI style with "レース後のコメント"
    if "レース後のコメント" in norm_content:
        parts = norm_content.split("レース後のコメント", 1)
        comments = parts[1].strip()
        lines = [line.strip() for line in comments.splitlines() if line.strip()]
        return "\n".join(lines)

    # If netkeiba comment article or general sports paper
    lines = [line.strip() for line in norm_content.splitlines() if line.strip()]
    return "\n".join(lines)


class TargetCommentExporter:
    """Exports race comments into TARGET frontier JV bulk import CSV."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def extract_race_comments(
        self,
        target_date: str,
        race_filter: str | None = None,
        venue_filter: str | None = None,
        race_num_filter: int | None = None,
        manual_kai: int | None = None,
        manual_nichi: int | None = None,
        schedule_overrides: dict[str, tuple[int, int]] | None = None,
    ) -> list[RaceCommentItem]:
        """
        Extract race comment items for the given date, filtered by race/venue/number.
        """
        # Load articles for target date
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        articles = self.db.get_articles_in_range(
            start_at=dt,
            end_at=datetime(dt.year, dt.month, dt.day, 23, 59, 59),
        )

        # Get schedule mapping (kai, nichi)
        schedule_map = extract_schedule_map(self.db, target_date)
        if schedule_overrides:
            schedule_map.update(schedule_overrides)

        # Group articles by (venue, race_num)
        race_groups: dict[tuple[str, int], list[tuple[Article, str]]] = {}

        for art in articles:
            res = parse_race_metadata(art.title, art.content)
            if not res:
                continue

            v, r_num, r_name = res

            if venue_filter and v != venue_filter:
                continue
            if race_num_filter is not None and r_num != race_num_filter:
                continue
            if race_filter and (
                race_filter.lower() not in r_name.lower()
                and race_filter.lower() not in art.title.lower()
            ):
                continue

            key = (v, r_num)
            if key not in race_groups:
                race_groups[key] = []
            race_groups[key].append((art, r_name))

        comment_items: list[RaceCommentItem] = []

        for (v, r_num), art_list in sorted(race_groups.items(), key=lambda x: (x[0][0], x[0][1])):
            # Determine kai, nichi
            kai, nichi = 1, 1  # Default fallback
            if v in schedule_map:
                kai, nichi = schedule_map[v]
            elif manual_kai is not None and manual_nichi is not None:
                kai, nichi = manual_kai, manual_nichi

            kaisai_name = f"{kai}回{v}{nichi}日"
            race_id = build_target_race_id(target_date, v, kai, nichi, r_num)

            # Determine best race name
            best_race_name = ""
            for _, r_name in art_list:
                if r_name and r_name != f"{v}{r_num}R":
                    best_race_name = r_name
                    break
            if not best_race_name:
                best_race_name = f"{v}{r_num}R"

            # Assemble consolidated comment text
            # Prioritize Radio NIKKEI as it has complete horse-by-horse comments
            radionikkei_art = next(
                (art for art, _ in art_list if art.source_key == "radionikkei"),
                None,
            )
            other_arts = [art for art, _ in art_list if art.source_key != "radionikkei"]

            comment_sections: list[str] = [f"【{best_race_name}】({v}{r_num}R)"]

            if radionikkei_art:
                rn_text = clean_comment_body(radionikkei_art)
                comment_sections.append(rn_text)
                # Check other articles for additional insights (e.g. jockeys quotes not in RN)
                extra_comments: list[str] = []
                seen_titles: set[str] = set()
                for o_art in other_arts:
                    # If title has quotes or key info
                    if "「" in o_art.title and "」" in o_art.title:
                        tit = o_art.title.strip()
                        if tit not in seen_titles:
                            seen_titles.add(tit)
                            extra_comments.append(f"・{tit}")
                if extra_comments:
                    comment_sections.append("[関連報道・談話]\n" + "\n".join(extra_comments))
            else:
                # Combine other articles
                sections: list[str] = []
                for art, _ in art_list:
                    body = clean_comment_body(art)
                    if body:
                        sections.append(f"■ {art.title}\n{body}")
                comment_sections.append("\n\n".join(sections))

            full_comment = "\n\n".join(comment_sections).strip()

            item = RaceCommentItem(
                race_id=race_id,
                kaisai_name=kaisai_name,
                race_date=target_date,
                venue=v,
                kai=kai,
                nichi=nichi,
                race_num=r_num,
                race_name=best_race_name,
                comment_text=full_comment,
                source_articles=[art for art, _ in art_list],
            )
            comment_items.append(item)

        return comment_items

    def generate_csv_content(
        self,
        items: list[RaceCommentItem],
        format_type: Literal["target", "simple"] = "target",
        single_line: bool = True,
        line_separator: str = " ",
    ) -> str:
        """
        Generate CSV string according to TARGET frontier JV FAQ 612 specs.

        Format 'target' (Format 2):
          開催名,レースID,コメント
        Format 'simple' (Format 1):
          レースID,コメント

        When single_line is True (default), multi-line comments are flattened
        into a single line per race to ensure compatibility with TARGET's CSV parser.
        """
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

        for it in items:
            comment = it.comment_text
            if single_line:
                comment = flatten_comment_to_single_line(comment, separator=line_separator)

            if format_type == "target":
                writer.writerow([it.kaisai_name, it.race_id, comment])
            else:
                writer.writerow([it.race_id, comment])

        return buf.getvalue()

    def export_to_file(
        self,
        items: list[RaceCommentItem],
        output_path: Path | str,
        format_type: Literal["target", "simple"] = "target",
        encoding: str = "cp932",
        single_line: bool = True,
        line_separator: str = " ",
    ) -> Path:
        """
        Export comment items to CSV file with specified encoding.
        Defaults to 'cp932' for Windows TARGET frontier JV compatibility.
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        raw_csv = self.generate_csv_content(
            items,
            format_type=format_type,
            single_line=single_line,
            line_separator=line_separator,
        )

        if encoding.lower() in ["cp932", "sjis", "shift_jis"]:
            safe_text = sanitize_for_cp932(raw_csv)
            encoded_bytes = safe_text.encode("cp932", errors="replace")
            out_p.write_bytes(encoded_bytes)
        else:
            out_p.write_text(raw_csv, encoding=encoding)

        return out_p
