"""Annual NFLPA Team Report Cards exposé (docs/in_progress/NFLPA_REPORT_CARD_PLAN.md §2/§5 U2).

Builds the marquee ranking article from `team_report_cards` (joined to teams) for a season: the full 1–32 ranking
with every category grade, the overall grade and score, and the top/bottom highlights. The article is deterministic:
every rank, grade, score and summary line in the text comes from the database rows, never from the writer or from a
model. The neighbouring newsroom modules store their work in `news_feed`; this module does the same, using the
front-office voice of the Sando desk.

CLI:
  python3 -m newsroom.report_card_article --season 2036 [--week 26] [--dry-run] [--publish] [--force]

  --season        league season the report cards were filed for (required)
  --week          calendar week the article is filed under (default: the FREE_AGENCY offseason stage)
  --dry-run       build and print, never write to news_feed (wins over --publish)
  --publish       write the article to news_feed
  --force         replace an existing article for the same season/week/article_type

NOTES on scheduling:
  * The plan says "Week 21 (early March, post-Combine)". In Project Mu weeks 19-23 are the playoffs and week 24
    onward are the offseason stages (see contracts_engine.OFFSEASON_STAGE_WEEKS), so a Week 21 filing would be a
    playoff week and is deliberately NOT claimed here. The module is stage/season driven: --week defaults to the
    FREE_AGENCY stage (26), exactly as compensatory_picks_article.py does, and the real scheduling hook (a weekly /
    league-office step that calls this module) is a follow-up and not in U2's files.
  * If db/migrations/2027_02_08_nflpa_report_cards.sql isn't applied, or the season has no cards, the module prints
    why and publishes nothing; it never invents a report card.
"""
import argparse
import os
import sys
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

from engines.contracts_engine import OFFSEASON_STAGE_WEEKS

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

WRITER = "SANDO"                               # front-office / league-office voice
ARTICLE_TYPE = "NFLPA_REPORT_CARD"
SLOT = "Standalone"
DEFAULT_WEEK = OFFSEASON_STAGE_WEEKS["FREE_AGENCY"]   # the offseason stage before the draft (week 26)

# The eight survey categories in display order (engines/report_card_engine.CATEGORIES), labelled for the reader.
CATEGORY_LABELS = (
    ("weight_room", "Weight Room"),
    ("training_room", "Training Room"),
    ("locker_room", "Locker Room"),
    ("food_nutrition", "Food & Nutrition"),
    ("team_travel", "Team Travel"),
    ("family_treatment", "Family Treatment"),
    ("head_coach", "Head Coach"),
    ("ownership", "Ownership"),
)


# ------------------------------------------------------------------ reads ----
def table_exists(conn, name: str) -> bool:
    """True when `name` is installed in the connection's schema (the optional-table guard used across newsroom)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (name,))
        row = cur.fetchone()
        return bool(row and row["present"])


def load_cards(conn, season: int) -> List[Dict[str, Any]]:
    """The season's team_report_cards rows joined to teams. [] when the migration isn't applied or the season has
    no cards."""
    if not table_exists(conn, "team_report_cards"):
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.season, c.team_id, t.abbreviation, t.city, t.name,
                   c.weight_room, c.training_room, c.locker_room, c.food_nutrition,
                   c.team_travel, c.family_treatment, c.head_coach, c.ownership,
                   c.overall_score, c.overall_grade, c.summary_text, c.last_renovated_season
            FROM team_report_cards c
            LEFT JOIN teams t ON t.id = c.team_id
            WHERE c.season = %s
            ORDER BY c.overall_score DESC, t.abbreviation
        """, (season,))
        rows = cur.fetchall()
    cards = []
    for r in rows:
        team_name = " ".join(x for x in (r["city"], r["name"]) if x) or (r["abbreviation"] or "NFL")
        cards.append({
            "season": r["season"],
            "team_id": r["team_id"],
            "team": r["abbreviation"] or "NFL",
            "team_name": team_name,
            "weight_room": r["weight_room"],
            "training_room": r["training_room"],
            "locker_room": r["locker_room"],
            "food_nutrition": r["food_nutrition"],
            "team_travel": r["team_travel"],
            "family_treatment": r["family_treatment"],
            "head_coach": r["head_coach"],
            "ownership": r["ownership"],
            "overall_score": r["overall_score"],
            "overall_grade": r["overall_grade"],
            "summary_text": r["summary_text"] or "",
            "last_renovated_season": r["last_renovated_season"],
        })
    return cards


# ------------------------------------------------------------------ build ----
def _fmt_score(score) -> str:
    """The overall score to 3 decimals (the NUMERIC(4,3) column), from whatever numeric type psycopg returned."""
    return f"{float(score):.3f}"


def _category_line(row: Dict[str, Any]) -> str:
    return " · ".join(f"{label} {row[col]}" for col, label in CATEGORY_LABELS)


def build_article(season: int, week: int, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The deterministic ranking article from the report-card rows. None when there are no rows (nothing to say,
    nothing published). Every rank, grade, score and summary line in the text comes from `rows`."""
    if not rows:
        return None

    ranked = sorted(rows, key=lambda r: (-float(r["overall_score"] or 0), str(r["team"] or "")))
    n = len(ranked)
    top, bottom = ranked[0], ranked[-1]

    headline = f"The {season} NFLPA Team Report Cards: The Full 1-{n} Ranking"
    excerpt = (f"The players have graded all {n} franchises across eight categories. "
               f"{top['team_name']} sits on top, {bottom['team_name']} at the bottom.")

    lines = [f"# {headline}", "", f"> **EXCERPT:** {excerpt}", ""]
    lines.append(
        f"The annual NFLPA survey is in. Players across all {n} franchises graded their clubs in eight categories "
        "— weight room, training room, locker room, food and nutrition, team travel, treatment of families, head "
        "coach and ownership investment — and the results are sorted below, best to worst by overall score."
    )
    lines.append("")
    lines.append("## The Full Ranking")
    lines.append("")
    for i, r in enumerate(ranked, 1):
        lines.append(f"**{i}. {r['team_name']}** — overall **{r['overall_grade']}** "
                     f"({_fmt_score(r['overall_score'])}). {_category_line(r)}.")
        lines.append("")

    lines.append("## The Best and the Worst")
    lines.append("")
    lines.append("**The model franchises** (top of the survey):")
    lines.append("")
    for j, r in enumerate(ranked[:5]):
        note = f" — {r['summary_text']}" if (r.get("summary_text") or "").strip() else ""
        lines.append(f"{j + 1}. **{r['team_name']}** ({r['overall_grade']}, {_fmt_score(r['overall_score'])}){note}")
    lines.append("")
    lines.append("**The work to do** (bottom of the survey):")
    lines.append("")
    bottom_highlight = ranked[-5:]
    for j, r in enumerate(bottom_highlight):
        rank = n - len(bottom_highlight) + j + 1
        note = f" — {r['summary_text']}" if (r.get("summary_text") or "").strip() else ""
        lines.append(f"{rank}. **{r['team_name']}** ({r['overall_grade']}, {_fmt_score(r['overall_score'])}){note}")
    lines.append("")
    lines.append("Every grade above comes from the survey responses of the players who lived it.")
    lines.append("")

    content = "\n".join(lines)
    return {
        "season": season,
        "week": week,
        "persona": WRITER,
        "article_type": ARTICLE_TYPE,
        "slot": SLOT,
        "headline": headline,
        "excerpt": excerpt,
        "content": content,
    }


# ------------------------------------------------------------------ write ----
def publish_article(conn, article: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """Insert the built article into news_feed. Mirrors the neighbouring modules: skip an existing row for the
    same season/week/article_type unless `force`, in which case replace it. Returns a status dict."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id FROM news_feed
                       WHERE season = %s AND week = %s AND article_type = %s
                       ORDER BY id DESC LIMIT 1""",
                    (article["season"], article["week"], article["article_type"]))
        existing = cur.fetchone()
        if existing and not force:
            return {"status": "SKIPPED_EXISTING", "article_id": existing["id"],
                    "headline": article["headline"]}
        if existing:
            cur.execute("DELETE FROM news_feed WHERE id = %s", (existing["id"],))
        cur.execute("""
            INSERT INTO news_feed (season, week, team_id, content_type, persona, article_type, slot,
                                   headline, excerpt, content, created_at)
            VALUES (%s, %s, NULL, 'ARTICLE', %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (article["season"], article["week"], article["persona"], article["article_type"],
              article["slot"], article["headline"], article["excerpt"], article["content"]))
        row = cur.fetchone()
    return {"status": "PUBLISHED", "article_id": row["id"] if row else None,
            "headline": article["headline"]}


# ------------------------------------------------------------------ run ----
def run(season: int, week: int = DEFAULT_WEEK, dry_run: bool = False, publish: bool = False,
        force: bool = False, conn_info: Optional[str] = None) -> Dict[str, Any]:
    """Load the season's cards, build the article, and (unless dry_run) publish it when asked."""
    conn_str = conn_info or CONN_INFO

    with psycopg.connect(conn_str, row_factory=dict_row) as conn:
        if not table_exists(conn, "team_report_cards"):
            result = {"status": "NO_TABLE", "season": season, "week": week, "articles_written": 0}
            print("team_report_cards is not installed (migration 2027_02_08_nflpa_report_cards.sql not applied); "
                  "nothing to publish.")
            return result

        cards = load_cards(conn, season)
        if not cards:
            result = {"status": "NO_CARDS", "season": season, "week": week, "articles_written": 0}
            print(f"No team report cards for season {season}; nothing to publish.")
            return result

        article = build_article(season, week, cards)
        if article is None:
            return {"status": "NO_CARDS", "season": season, "week": week, "articles_written": 0}

        print("=" * 70)
        print(f"NFLPA TEAM REPORT CARDS · season {season} · week {week}")
        print("=" * 70)
        print(article["content"])

        if dry_run:
            return {"status": "DRY_RUN", "article": article, "articles_written": 0}
        if not publish:
            return {"status": "BUILT", "article": article, "articles_written": 0}

        written = publish_article(conn, article, force=force)
        return {"status": written["status"], "article": article,
                "article_id": written.get("article_id"),
                "articles_written": 1 if written["status"] == "PUBLISHED" else 0}


def main():
    parser = argparse.ArgumentParser(description="Build/publish the annual Project Mu NFLPA report card article")
    parser.add_argument("--season", type=int, required=True,
                        help="League season the report cards were filed for (e.g. 2036)")
    parser.add_argument("--week", type=int, default=DEFAULT_WEEK,
                        help=f"Calendar week to file under (default: FREE_AGENCY offseason stage, {DEFAULT_WEEK})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and print without writing to news_feed")
    parser.add_argument("--publish", action="store_true", help="Write the article to news_feed")
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing article for the same season/week/article_type")

    args = parser.parse_args()
    try:
        result = run(args.season, week=args.week, dry_run=args.dry_run, publish=args.publish, force=args.force)
        print(f"\nResult: {result['status']}"
              + (f" (article #{result['article_id']})" if result.get("article_id") else ""))
    except Exception as e:
        print(f"\nNFLPA report card article failed: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
