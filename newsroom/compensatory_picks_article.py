"""Annual Compensatory Draft Picks announcement (docs/in_progress/COMPENSATORY_PICKS_PLAN.md §1/§5 U3;
PROJECT_SPEC §2.17g/§3.3c/§4.6/§7).

Builds the league-office announcement story from `compensatory_pick_awards` (joined to teams, and to the player or
coach behind a JC-2A award), with the CBA Article 6 and Resolution JC-2A reasons stored on each row. The article is
deterministic: every team, round, slot, overall pick, count and reason in the text comes from the database rows, never
from the writer or from a model. The neighbouring newsroom modules store their work in `news_feed`; this module does
the same, using the front-office voice of the Sando desk.

CLI:
  python3 -m newsroom.compensatory_picks_article --draft-season 2037 [--season 2036] [--week 26]
                                                 [--dry-run] [--publish] [--force]

  --draft-season  the class that receives the picks (required)
  --season        league season the picks were earned in (default: draft-season - 1)
  --week          calendar week the article is filed under (default: the FREE_AGENCY offseason stage)
  --dry-run       build and print, never write to news_feed (wins over --publish)
  --publish       write the article to news_feed
  --force         replace an existing article for the same season/week/article_type

NOTES on scheduling:
  * The plan says "Week 21 (early March)". In Project Mu weeks 19-23 are the playoffs and week 24 onward are the
    offseason stages (see contracts_engine.OFFSEASON_STAGE_WEEKS), so a Week 21 filing would be a playoff week and is
    deliberately NOT claimed here. The module is stage/season driven: --week defaults to the FREE_AGENCY stage, and
    the real scheduling hook (a weekly/league-office step that calls this module) is not in U3's owned files and
    remains a follow-up.
  * If db/migrations/2027_02_06_compensatory_draft_picks.sql isn't applied, or the class has no awards, the module
    prints that fact and publishes nothing; it never invents awards.
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
ARTICLE_TYPE = "COMPENSATORY_PICKS_ANNOUNCEMENT"
SLOT = "Standalone"
DEFAULT_WEEK = OFFSEASON_STAGE_WEEKS["FREE_AGENCY"]   # the offseason stage before the draft (week 26)


# ------------------------------------------------------------------ reads ----
def table_exists(conn, name: str) -> bool:
    """True when `name` is installed in the connection's schema (the optional-table guard used across newsroom)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (name,))
        row = cur.fetchone()
        return bool(row and row["present"])


def load_awards(conn, draft_season: int) -> List[Dict[str, Any]]:
    """The class's compensatory_pick_awards rows joined to teams (and the source player/coach). [] when the
    migration isn't applied or the class has no awards."""
    if not table_exists(conn, "compensatory_pick_awards"):
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.id, a.draft_season, a.team_id, t.abbreviation, t.city, t.name,
                   a.round, a.slot_in_round, a.overall_pick, a.award_kind, a.reason,
                   a.source_player_id, p.full_name AS source_player,
                   a.source_coach_id, c.coach_name AS source_coach
            FROM compensatory_pick_awards a
            LEFT JOIN teams t ON t.id = a.team_id
            LEFT JOIN players p ON p.id = a.source_player_id
            LEFT JOIN coaches c ON c.id = a.source_coach_id
            WHERE a.draft_season = %s
            ORDER BY a.round, a.overall_pick, a.slot_in_round, a.team_id
        """, (draft_season,))
        rows = cur.fetchall()
    awards = []
    for r in rows:
        team_name = " ".join(x for x in (r["city"], r["name"]) if x) or (r["abbreviation"] or "NFL")
        awards.append({
            "id": r["id"],
            "draft_season": r["draft_season"],
            "team_id": r["team_id"],
            "team": r["abbreviation"] or "NFL",
            "team_name": team_name,
            "round": r["round"],
            "slot_in_round": r["slot_in_round"],
            "overall_pick": r["overall_pick"],
            "award_kind": r["award_kind"],
            "reason": r["reason"] or "",
            "source_player": r["source_player"],
            "source_coach": r["source_coach"],
        })
    return awards


def load_qfa_ledger(conn, season: int) -> Optional[Dict[str, int]]:
    """Counts from the season's qualifying_free_agents ledger, or None when the table is absent or empty. Each row
    is one signing: old_team_id lost a qualifier, new_team_id gained one."""
    if not table_exists(conn, "qualifying_free_agents"):
        return None
    with conn.cursor() as cur:
        cur.execute("""SELECT old_team_id, new_team_id, qualifies
                       FROM qualifying_free_agents WHERE season = %s""", (season,))
        rows = cur.fetchall()
    if not rows:
        return None
    return {
        "signings": len(rows),
        "lost_teams": len({r["old_team_id"] for r in rows}),
        "gained_teams": len({r["new_team_id"] for r in rows}),
        "qualified": sum(1 for r in rows if r["qualifies"]),
    }


# ------------------------------------------------------------------ build ----
def _kind_label(kind: str) -> str:
    return "Resolution JC-2A" if str(kind or "").upper() == "JC2A" else "CBA Article 6 formula"


def build_article(draft_season: int, season: int, week: int, awards: List[Dict[str, Any]],
                  qfa: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
    """The deterministic announcement article from the award rows. None when there are no awards (nothing to say,
    nothing published). Every number in the text is counted from `awards` / `qfa`."""
    if not awards:
        return None

    ds = int(awards[0]["draft_season"])
    n = len(awards)
    cba = [a for a in awards if str(a["award_kind"]).upper() != "JC2A"]
    jc2a = [a for a in awards if str(a["award_kind"]).upper() == "JC2A"]
    teams = sorted({a["team_name"] for a in awards})
    rounds = sorted({int(a["round"]) for a in awards})

    headline = f"The {ds} Compensatory Picks: The Full Award List"
    excerpt = (f"The league office has awarded {n} compensatory picks in the {ds} NFL Draft, "
               f"with {len(teams)} teams adding draft capital.")

    lines = [f"# {headline}", "", f"> **EXCERPT:** {excerpt}", ""]
    lines.append(
        f"The compensatory cycle is settled. The league office has awarded **{n}** picks in the {ds} NFL Draft: "
        f"{len(cba)} under the CBA's Article 6 formula and {len(jc2a)} under Resolution JC-2A. "
        f"{len(teams)} franchises added at least one."
    )
    lines.append("")

    lines.append("## The Awards, Round by Round")
    lines.append("")
    for rnd in rounds:
        lines.append(f"### Round {rnd}")
        lines.append("")
        for a in [x for x in awards if int(x["round"]) == rnd]:
            lines.append(
                f"- **{a['team_name']}** — Round {a['round']}, Pick {a['slot_in_round']} "
                f"(No. {a['overall_pick']} overall), {_kind_label(a['award_kind'])}. {a['reason']}"
            )
        lines.append("")

    lines.append("## By the Numbers")
    lines.append("")
    lines.append(f"- Compensatory picks awarded: {n}")
    lines.append(f"- CBA Article 6 formula: {len(cba)}")
    lines.append(f"- Resolution JC-2A: {len(jc2a)}")
    lines.append(f"- Teams receiving at least one: {len(teams)}")
    lines.append(f"- Rounds represented: {', '.join(str(r) for r in rounds)}")
    lines.append("")

    if jc2a:
        lines.append("## The JC-2A Awards")
        lines.append("")
        lines.append(
            f"Resolution JC-2A produced {len(jc2a)} of the {n} picks, rewarding a club when a minority coordinator "
            f"or assistant general manager is hired away as a head coach or general manager. The originating clubs:"
        )
        lines.append("")
        for a in jc2a:
            source = a.get("source_player") or a.get("source_coach")
            origin = f"; source: {source}" if source else ""
            lines.append(
                f"- **{a['team_name']}** — Round {a['round']}, Pick {a['slot_in_round']} "
                f"(No. {a['overall_pick']} overall){origin}. {a['reason']}"
            )
        lines.append("")

    if qfa:
        lines.append("## The Qualifying-Free-Agent Ledger")
        lines.append("")
        lines.append(
            f"The season's ledger records {qfa['signings']} qualifying-free-agent signings: "
            f"{qfa['lost_teams']} teams lost at least one qualifier, {qfa['gained_teams']} teams gained at least one, "
            f"and {qfa['qualified']} signings met the qualifying bar."
        )
        lines.append("")

    lines.append("The draft order now reflects it.")
    lines.append("")

    content = "\n".join(lines)
    return {
        "season": season,
        "week": week,
        "draft_season": ds,
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
def run(draft_season: int, season: Optional[int] = None, week: int = DEFAULT_WEEK,
        dry_run: bool = False, publish: bool = False, force: bool = False,
        conn_info: Optional[str] = None) -> Dict[str, Any]:
    """Load the class's awards, build the article, and (unless dry_run) publish it when asked."""
    season = draft_season - 1 if season is None else int(season)
    conn_str = conn_info or CONN_INFO

    with psycopg.connect(conn_str, row_factory=dict_row) as conn:
        awards = load_awards(conn, draft_season)
        if not awards:
            result = {"status": "NO_AWARDS", "draft_season": draft_season, "season": season,
                      "week": week, "articles_written": 0}
            print(f"No compensatory pick awards for the {draft_season} draft; nothing to publish.")
            return result

        article = build_article(draft_season, season, week, awards, load_qfa_ledger(conn, season))
        if article is None:
            return {"status": "NO_AWARDS", "draft_season": draft_season, "season": season,
                    "week": week, "articles_written": 0}

        print("=" * 70)
        print(f"COMPENSATORY PICKS ANNOUNCEMENT · {draft_season} draft · season {season} · week {week}")
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
    parser = argparse.ArgumentParser(description="Build/publish the annual Project Mu compensatory picks article")
    parser.add_argument("--draft-season", type=int, required=True,
                        help="Draft class receiving the picks (e.g. 2037)")
    parser.add_argument("--season", type=int,
                        help="League season the picks were earned in (default: draft-season - 1)")
    parser.add_argument("--week", type=int, default=DEFAULT_WEEK,
                        help=f"Calendar week to file under (default: FREE_AGENCY offseason stage, {DEFAULT_WEEK})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and print without writing to news_feed")
    parser.add_argument("--publish", action="store_true", help="Write the article to news_feed")
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing article for the same season/week/article_type")

    args = parser.parse_args()
    try:
        result = run(args.draft_season, season=args.season, week=args.week,
                     dry_run=args.dry_run, publish=args.publish, force=args.force)
        print(f"\nResult: {result['status']}"
              + (f" (article #{result['article_id']})" if result.get("article_id") else ""))
    except Exception as e:
        print(f"\nCompensatory picks article failed: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
