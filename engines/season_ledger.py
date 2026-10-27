"""
Player season ledger (docs/SEASON_CONTINUITY_PLAN.md phase 2a; PROJECT_SPEC §3.3m).

Each player's fate between two captured seasons, written to player_season_transitions: RETAINED (same team), MOVED
(another team), RETIRED (a retirement on record for the season he left after), FREE_AGENT (on last season's rosters,
on none at the start of this one, and not recorded as retiring: JR 2026-10-26, plan D1), DRAFTED (a rookie the draft
results name) or NEW (any other arrival: an undrafted rookie, a new free agent, a player back after a season away).
Ratings, development trait, age and experience are kept on both sides.

Captured seasons only (plan D10): 2035 -> 2036 on. The 2026-2034 history keeps each player on his 2035 team, and
its own players are retired by `players.origin` (plan D11), never by a row here.

"At the start of a season" is the team before that season's first in-season move: a player released or traded away
in Week 6 started with the team he left, a player signed in Week 10 started as a free agent (opening_team). Offseason
moves (League Transactions rows with no week, filed under the season just finished) happen between the seasons and
say how a move came about (`source`).

Run by the Season scan right after the roster load (pipeline/weekly_capture.step_preseason_engines), or by hand:

  python3 -m engines.season_ledger [--season TO] [--dry-run] [--free-agents] [--player ID]

classify() and opening_team() are pure; build() replaces one boundary's rows, so a rerun (after a retirement scan,
say) changes only what the new facts change.
"""
import argparse
import json
import os

import psycopg

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
FIRST_CAPTURED = 2035          # the first season captured from Madden (2026-2034 are generated history)
STAYS_TEAM = ("RE_SIGNED", "EXTENDED", "RESTRUCTURED")
LEAVES_TEAM = ("RELEASED", "TRADED")


# ----------------------------------------------------------------------------------------------- pure ----

def opening_team(current_team, moves):
    """A player's team at the start of a season. `moves`: his in-season transactions that season, oldest first
    (dicts with type, week, from_team_id, to_team_id, flags). No move -> his current team. Otherwise his first move
    tells: released or traded away -> the team he left; re-signed, extended or restructured -> his team; signed off
    his own practice squad -> that team; any other signing -> None (a free agent at the start)."""
    moves = [m for m in moves if m.get("week") is not None]
    if not moves:
        return current_team
    first = moves[0]
    if first["type"] in LEAVES_TEAM:
        return first["from_team_id"]
    if first["type"] in STAYS_TEAM:
        return first["to_team_id"]
    if first["type"] == "SIGNED":
        if "practice_squad" in (first.get("flags") or "") and not first.get("from_team_id"):
            return first["to_team_id"]
        return None
    return current_team


def first_team(current_team, moves):
    """The first team a player who arrived during the season joined (his first signing), else his current team."""
    for m in moves:
        if m.get("week") is not None and m["type"] == "SIGNED" and m.get("to_team_id"):
            return m["to_team_id"]
    return current_team


def classify(prev, cur, retired=False, drafted=False, scan_ran=False, returned=False, offseason=()):
    """One player's transition. prev / cur: his row in the season he left / the season starting, as dicts with
    team_id (end of season / start of season, None = no team), released (bool, prev only), signed_week (cur only:
    the week he joined during the season, when he wasn't on a roster at the start), ovr, dev, age, exp; None when he
    has no row. offseason: his offseason moves between the seasons (types). Returns (status, source, from_team,
    to_team, flags)."""
    flags = []
    if prev and prev.get("released"):
        flags.append("released_in_season")
    from_team = prev["team_id"] if prev else None
    if prev:
        to_team = cur["team_id"] if cur else None
        if to_team is not None:
            if to_team == from_team:
                return "RETAINED", None, from_team, to_team, flags
            source = ("TRADE" if "TRADED" in offseason else "TRANSACTION" if "SIGNED" in offseason
                      else "ROSTER_SCAN")
            return "MOVED", source, from_team, to_team, flags
        if cur and cur.get("signed_week") is not None:
            flags.append(f"signed_in_season:week {cur['signed_week']}")
        if retired:
            return "RETIRED", None, from_team, None, flags
        if not scan_ran:
            flags.append("retirement_scan_missing")
        return "FREE_AGENT", None, from_team, None, flags
    # an arrival
    if cur.get("exp") == 0:
        if drafted:
            return "DRAFTED", "DRAFT", None, cur["team_id"] or cur.get("first_team"), flags
        flags.append("undrafted_rookie")
    if returned:
        flags.append("returned")
    elif (cur.get("exp") or 0) > 0:
        flags.append("no_prior_capture")      # a veteran no earlier captured roster lists (e.g. a depth player it missed)
    if cur.get("signed_week") is not None:
        flags.append(f"signed_in_season:week {cur['signed_week']}")
        return "NEW", "TRANSACTION", None, cur.get("first_team"), flags
    return "NEW", "ROSTER_SCAN", None, cur["team_id"], flags


# ------------------------------------------------------------------------------------------- database ----

ROW_COLS = ("player_id", "team_id", "released_week", "overall_rating", "dev_trait", "age", "years_of_experience")


def installed(cur):
    cur.execute("SELECT to_regclass('player_season_transitions') IS NOT NULL")
    return bool(cur.fetchone()[0])


def _captured(cur):
    """SQL filter for captured player_seasons rows (the history backfill marks its own `source = 'SYNTH'`)."""
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'player_seasons' AND column_name = 'source'")
    return "AND COALESCE(source, 'CAPTURE') <> 'SYNTH'" if cur.fetchone() else ""


def _season_rows(cur, season, captured):
    cur.execute(f"""SELECT {', '.join(ROW_COLS)} FROM player_seasons
                    WHERE season = %s AND team_id IS NOT NULL {captured}""", (season,))
    return {r[0]: dict(zip(ROW_COLS, r)) for r in cur.fetchall()}


def _moves(cur, season, week_null):
    cur.execute(f"""SELECT player_id, type, week, from_team_id, to_team_id, flags FROM transactions
                    WHERE season = %s AND player_id IS NOT NULL AND applied
                      AND week IS {'NULL' if week_null else 'NOT NULL'}
                    ORDER BY week NULLS FIRST, id""", (season,))
    out = {}
    for pid, t, w, f, to, fl in cur.fetchall():
        out.setdefault(pid, []).append({"type": t, "week": w, "from_team_id": f, "to_team_id": to, "flags": fl})
    return out


def compute(cur, to_season):
    """The boundary's rows as dicts (reads only). Raises ValueError when either season isn't captured."""
    from_season = to_season - 1
    if from_season < FIRST_CAPTURED:
        raise ValueError(f"{from_season} isn't a captured season (the ledger starts at {FIRST_CAPTURED} -> "
                         f"{FIRST_CAPTURED + 1})")
    captured = _captured(cur)
    prev_rows, cur_rows = _season_rows(cur, from_season, captured), _season_rows(cur, to_season, captured)
    if not prev_rows or not cur_rows:
        raise ValueError(f"no captured rosters for {from_season if not prev_rows else to_season}")
    in_season = _moves(cur, to_season, week_null=False)
    offseason = _moves(cur, from_season, week_null=True)
    cur.execute("SELECT player_id FROM player_retirements WHERE season = %s AND player_id IS NOT NULL", (from_season,))
    retired = {r[0] for r in cur.fetchall()}
    scan_ran = bool(retired)
    cur.execute("""SELECT player_id FROM draft_prospects WHERE draft_season = %s AND actual_pick IS NOT NULL
                     AND player_id IS NOT NULL""", (to_season,))
    drafted = {r[0] for r in cur.fetchall()}
    cur.execute(f"""SELECT DISTINCT player_id FROM player_seasons WHERE season < %s AND team_id IS NOT NULL
                      AND season >= {FIRST_CAPTURED} {captured}""", (from_season,))
    seen_before = {r[0] for r in cur.fetchall()}

    rows = []
    for pid in sorted(set(prev_rows) | set(cur_rows)):
        p, c = prev_rows.get(pid), cur_rows.get(pid)
        prev = cur_ = None
        if p:
            prev = {"team_id": p["team_id"], "released": p["released_week"] is not None}
        if c:
            moves = in_season.get(pid, [])
            start = opening_team(c["team_id"], moves)
            signed = next((m["week"] for m in moves if m["week"] is not None), None) if start is None else None
            cur_ = {"team_id": start, "signed_week": signed, "first_team": first_team(c["team_id"], moves),
                    "exp": c["years_of_experience"]}
            if start is None and not p:
                cur_["team_id"] = None
        status, source, from_team, to_team, flags = classify(
            prev, cur_, retired=pid in retired, drafted=pid in drafted, scan_ran=scan_ran,
            returned=pid in seen_before, offseason=tuple(m["type"] for m in offseason.get(pid, [])))
        rows.append({"player_id": pid, "to_season": to_season, "from_season": from_season,
                     "from_team_id": from_team, "to_team_id": to_team, "status": status, "source": source,
                     "ovr_before": p and p["overall_rating"], "ovr_after": c and c["overall_rating"],
                     "dev_before": p and p["dev_trait"], "dev_after": c and c["dev_trait"],
                     "age_before": p and p["age"], "age_after": c and c["age"],
                     "exp_before": p and p["years_of_experience"], "exp_after": c and c["years_of_experience"],
                     "flags": ",".join(flags) or None})
    return rows


INSERT_COLS = ("player_id", "to_season", "from_season", "from_team_id", "to_team_id", "status", "source",
               "ovr_before", "ovr_after", "dev_before", "dev_after", "age_before", "age_after", "exp_before",
               "exp_after", "flags")


def build(cur, to_season, dry_run=False):
    """Rewrite the to_season boundary. Returns {to_season, status, counts, flags}. status: not_installed |
    not_ready (a season isn't captured) | dry_run | built."""
    if not installed(cur):
        return {"to_season": to_season, "status": "not_installed"}
    try:
        rows = compute(cur, to_season)
    except ValueError as e:
        return {"to_season": to_season, "status": "not_ready", "reason": str(e)}
    counts, flag_counts = {}, {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        for f in (r["flags"] or "").split(","):
            if f:
                key = f.split(":")[0]
                flag_counts[key] = flag_counts.get(key, 0) + 1
    report = {"to_season": to_season, "counts": dict(sorted(counts.items())), "flags": dict(sorted(flag_counts.items()))}
    if dry_run:
        return {**report, "status": "dry_run"}
    cur.execute("DELETE FROM player_season_transitions WHERE to_season = %s", (to_season,))
    cur.executemany(f"INSERT INTO player_season_transitions ({', '.join(INSERT_COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(INSERT_COLS))})",
                    [tuple(r[k] for k in INSERT_COLS) for r in rows])
    return {**report, "status": "built"}


def run_ready(cur, season):
    """The Season scan's step: build the boundary into `season` (league_state's new season) when both are captured."""
    return build(cur, season)


def free_agent_pool(cur, season):
    """Players without a team in `season`: last seen on a captured roster, on none now (or released this season),
    and not retired. History players (players.origin set) retired during 2026-2034 and are never here (plan D11).
    [{player_id, name, position, last_team, last_season, ovr, age, released_week}], best rated first."""
    captured = _captured(cur)
    cur.execute(f"""
        WITH last AS (
            SELECT DISTINCT ON (ps.player_id) ps.player_id, ps.season, ps.team_id, ps.position, ps.overall_rating,
                   ps.age, ps.released_week
            FROM player_seasons ps
            WHERE ps.season <= %s AND ps.season >= {FIRST_CAPTURED} AND ps.team_id IS NOT NULL {captured.replace('source', 'ps.source')}
            ORDER BY ps.player_id, ps.season DESC)
        SELECT l.player_id, COALESCE(p.full_name, p.player_name), l.position, t.abbreviation, l.season,
               l.overall_rating, l.age, l.released_week
        FROM last l JOIN players p ON p.id = l.player_id LEFT JOIN teams t ON t.id = l.team_id
        WHERE p.origin IS NULL AND p.retired_season IS NULL
          AND (l.season < %s OR l.released_week IS NOT NULL)
        ORDER BY l.overall_rating DESC NULLS LAST, l.player_id""", (season, season))
    keys = ("player_id", "name", "position", "last_team", "last_season", "ovr", "age", "released_week")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def career_path(cur, player_id):
    """Every season a player has on record, oldest first: team, position, whether it's generated history ('hist'),
    and, between captured seasons, the ledger's status. Ends with how his career stands: active, free agent,
    retired (a captured retirement) or retired after his last history season (players.origin, plan D11)."""
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'player_seasons' AND column_name = 'source'")
    has_source = bool(cur.fetchone())
    cur.execute(f"""SELECT ps.season, t.abbreviation, ps.position, ps.released_week,
                           {"ps.source" if has_source else "NULL"}
                    FROM player_seasons ps LEFT JOIN teams t ON t.id = ps.team_id
                    WHERE ps.player_id = %s ORDER BY ps.season""", (player_id,))
    seasons = [{"season": s, "team": t, "position": pos, "released_week": rw, "history": src == "SYNTH"}
               for s, t, pos, rw, src in cur.fetchall()]
    cur.execute("""SELECT to_season, status, source, flags FROM player_season_transitions WHERE player_id = %s""",
                (player_id,))
    trans = {r[0]: {"status": r[1], "source": r[2], "flags": r[3]} for r in cur.fetchall()}
    for s in seasons:
        if s["season"] in trans:
            s["arrived"] = trans[s["season"]]
    cur.execute("SELECT origin, retired_season FROM players WHERE id = %s", (player_id,))
    origin, retired_season = cur.fetchone() or (None, None)
    cur.execute("SELECT max(season) FROM player_seasons WHERE team_id IS NOT NULL")
    latest = cur.fetchone()[0]
    last = seasons[-1]["season"] if seasons else None
    gone = [v for k, v in trans.items() if v["status"] in ("RETIRED", "FREE_AGENT") and k == (last or 0) + 1]
    if origin:
        status = {"status": "RETIRED", "after_season": last, "basis": "history"}
    elif retired_season:
        status = {"status": "RETIRED", "after_season": retired_season, "basis": "retirement"}
    elif gone:
        status = {"status": gone[0]["status"], "after_season": last, "basis": "ledger"}
    elif last == latest and seasons and seasons[-1]["released_week"] is None:
        status = {"status": "ACTIVE", "team": seasons[-1]["team"]}
    else:
        status = {"status": "FREE_AGENT", "after_season": last, "basis": "no roster since"}
    return {"player_id": player_id, "seasons": seasons, "now": status}


def main():
    ap = argparse.ArgumentParser(description="Player season ledger: each player's fate between captured seasons.")
    ap.add_argument("--season", type=int, help="the season starting (default: league_state's season)")
    ap.add_argument("--dry-run", action="store_true", help="count and print; write nothing")
    ap.add_argument("--free-agents", action="store_true", help="print the free-agent pool for --season")
    ap.add_argument("--player", type=int, help="print one player's career path")
    args = ap.parse_args()
    with psycopg.connect(CONN_INFO) as conn:
        cur = conn.cursor()
        season = args.season
        if season is None:
            cur.execute("SELECT current_season FROM league_state LIMIT 1")
            season = cur.fetchone()[0]
        if args.player:
            out = career_path(cur, args.player)
        elif args.free_agents:
            out = free_agent_pool(cur, season)
        else:
            out = build(cur, season, dry_run=args.dry_run)
            if not args.dry_run:
                conn.commit()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
