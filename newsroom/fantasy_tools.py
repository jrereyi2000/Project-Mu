"""Fantasy research tools for writer agents (docs/in_progress/FANTASY_EDITORIAL_MATRIX.md §7 step 4, PROJECT_SPEC §7).

Same week rule as newsroom/db_tools.py (newsroom/run_context.py): a Week N article sees box scores from weeks
before N only. Projections and simulated-league rosters for week N are visible, because they are built only
from earlier games. Scoring is full PPR (player_stats.fantasy_points). RB = HB + FB.
"""
import json
import math
from collections import defaultdict
from typing import Optional

import psycopg
from langchain_core.tools import tool

from newsroom.db_tools import db, fetch_moves, resolve_season
from newsroom.run_context import context_season, cutoff_week
from newsroom.roster_moves import move_line, on_roster, week_label

GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}
GROUPS = ("QB", "RB", "WR", "TE")
LAST_FANTASY_WEEK = 17
SEASON_WEEKS = 18


# ─────────────────────────────── helpers ───────────────────────────────

def _group_filter(position: str) -> Optional[str]:
    """'ALL' → None; QB/RB/WR/TE (HB/FB → RB) → group; anything else raises."""
    p = (position or "ALL").strip().upper()
    if p in ("", "ALL", "FLEX"):
        return None
    p = GROUP.get(p, p)
    if p not in GROUPS:
        raise ValueError(f"position must be QB, RB, WR, TE or ALL (got {position!r})")
    return p


def _publication_week(cur, season: int) -> int:
    """The week the current article is published for, else league_state.current_week."""
    cut = cutoff_week(season)
    if cut is not None and context_season() == season:
        return cut
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1")
    row = cur.fetchone()
    if row and row[0] == season and row[1]:
        return int(row[1])
    cur.execute("SELECT COALESCE(MIN(week), %s) FROM games WHERE season = %s AND status = 'SCHEDULED'",
                (SEASON_WEEKS + 1, season))
    return int(cur.fetchone()[0])


def _last_visible_week(cur, season: int) -> int:
    """Latest week with completed games the current article may see (0 if none)."""
    cut = cutoff_week(season)
    cur.execute("SELECT COALESCE(MAX(week), 0) FROM games WHERE season = %s AND status = 'COMPLETED'"
                + (" AND week < %s" if cut is not None else ""), (season, cut) if cut is not None else (season,))
    return int(cur.fetchone()[0])


def _check_forward_week(cur, season: int, week: int) -> Optional[str]:
    """Error text if a projection/roster week is beyond what the article may see."""
    cut = cutoff_week(season)
    if cut is not None and week > cut:
        return f"Week {week} is after this article's publication week ({cut}); its projections aren't available yet."
    return None


def _roster(cur, season: int) -> dict:
    """player_id -> skill-player profile, with depth = OVR order within team and position group. A player released
    by the article's week (roster_moves.on_roster) keeps his old team (his past lines are with it) but has depth
    'released' and free_agent True, and is out of the depth order."""
    cur.execute("""
        SELECT p.id, COALESCE(p.full_name, p.player_name), p.player_name, ps.position, ps.team_id, t.abbreviation,
               ps.overall_rating, ps.age, ps.years_of_experience, ps.released_week
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        JOIN teams t ON t.id = ps.team_id
        WHERE ps.season = %s AND ps.position IN ('QB', 'HB', 'FB', 'WR', 'TE')
    """, (season,))
    roster, by_slot = {}, defaultdict(list)
    cut = cutoff_week(season)
    for pid, name, short, pos, team_id, abbr, ovr, age, exp, released in cur.fetchall():
        g = GROUP[pos]
        roster[pid] = {"name": name, "short": short, "position": pos, "group": g, "team_id": team_id,
                       "team": abbr, "ovr": ovr, "age": age, "exp": exp}
        if not on_roster(released, cut):
            roster[pid].update(depth="released", free_agent=True, released_week=released)
            continue
        by_slot[(team_id, g)].append((-(ovr or 0), pid))
    for players in by_slot.values():
        for depth, (_, pid) in enumerate(sorted(players), start=1):
            roster[pid]["depth"] = f"{roster[pid]['group']}{depth}"
    return roster


SKILL_MOVE_POSITIONS = ("QB", "HB", "FB", "WR", "TE")


def nfl_move_lines(moves, roster, rostered, week: int) -> list:
    """The waiver wire's `nfl_moves`: skill players signed in the last 2 visible weeks (`week` and `week` - 1) with
    their new team, depth slot and rostered %, then released players still on fantasy rosters (drop candidates, no
    game until they sign). moves: db_tools.fetch_moves rows (already cut off for the article); roster: _roster;
    rostered: {player_id: rostered %}."""
    out = []
    for m in moves:
        pid = m.get("player_id")
        if m.get("type") != "SIGNED" or m.get("position") not in SKILL_MOVE_POSITIONS or pid not in roster \
                or m.get("week") is None or m["week"] < week - 1 or m.get("practice_squad"):
            continue
        r = roster[pid]
        out.append(f"SIGNED · {r['name']} (ID {pid}, {r['position']}) signed with {m.get('team')} ({week_label(m)}): "
                   f"now {r['depth']}, rostered {rostered.get(pid, 0.0):.0f}%")
    for pid, r in sorted(roster.items(), key=lambda kv: -rostered.get(kv[0], 0.0)):
        if r.get("free_agent") and rostered.get(pid, 0.0) > 0:
            out.append(f"RELEASED · {r['name']} (ID {pid}, {r['position']}) released by {r['team']} "
                       f"({week_label({'week': r.get('released_week')})}): no team, projects 0 until he signs; "
                       f"rostered {rostered[pid]:.0f}% (drop candidate)")
    return out


def _lines(cur, season: int, through_week: int) -> list:
    """Visible skill-player box-score lines matched to player ids (name + team in the season, as the
    projection engine does)."""
    cur.execute("""
        SELECT m.pid, st.team_id, g.week, st.position, st.pass_yards, st.pass_tds, st.interceptions,
               st.rush_attempts, st.rush_yards, st.rush_tds, st.receptions, st.rec_yards, st.rec_tds,
               st.fumb, st.fantasy_points::float
        FROM player_stats st
        JOIN games g ON g.id = st.game_id
        LEFT JOIN LATERAL (
            SELECT p.id AS pid
            FROM players p
            JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = g.season AND ps.team_id = st.team_id
            WHERE p.player_name = st.player_name
            ORDER BY (ps.position = st.position) DESC, p.id
            LIMIT 1
        ) m ON TRUE
        WHERE g.season = %s AND g.status = 'COMPLETED' AND g.week <= %s
          AND st.position IN ('QB', 'HB', 'FB', 'WR', 'TE')
    """, (season, through_week))
    keys = ("pid", "team_id", "week", "position", "pass_yds", "pass_td", "int", "rush_att", "rush_yds",
            "rush_td", "rec", "rec_yds", "rec_td", "fumb", "ppr")
    out = []
    for r in cur.fetchall():
        d = dict(zip(keys, r))
        for k in keys[4:14]:
            d[k] = d[k] or 0
        d["ppr"] = d["ppr"] or 0.0
        out.append(d)
    return out


def _opponents(cur, season: int) -> dict:
    """(team_id, week) -> 'vs ABC' / '@ ABC'."""
    cur.execute("""
        SELECT g.week, g.home_team_id, g.away_team_id, h.abbreviation, a.abbreviation
        FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
        WHERE g.season = %s
    """, (season,))
    out = {}
    for week, home, away, h, a in cur.fetchall():
        out[(home, week)] = f"vs {a}"
        out[(away, week)] = f"@ {h}"
    return out


def _projections(cur, season: int, week: int) -> dict:
    cur.execute("""
        SELECT player_id, projected_points::float, avg_points::float, matchup_stars, pos_rank, ros_rank,
               ros_total_points::float, adp_rank
        FROM fantasy_projections WHERE season = %s AND week = %s AND player_id IS NOT NULL
    """, (season, week))
    return {r[0]: dict(zip(("proj", "avg", "stars", "pos_rank", "ros_rank", "ros", "adp_rank"), r[1:]))
            for r in cur.fetchall()}


def _sim_week(cur, season: int, week: int) -> dict:
    cur.execute("""
        SELECT player_id, rostered_pct::float, started_pct::float FROM fantasy_sim_player_weeks
        WHERE season = %s AND week = %s
    """, (season, week))
    return {pid: (r, s) for pid, r, s in cur.fetchall()}


def _adp(cur, season: int) -> dict:
    cur.execute("SELECT player_id, adp::float, min_pick, max_pick, draft_pct::float FROM fantasy_sim_adp WHERE season = %s",
                (season,))
    return {r[0]: {"adp": r[1], "min": r[2], "max": r[3], "draft_pct": r[4]} for r in cur.fetchall()}


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _stat_line(d: dict) -> str:
    parts = []
    if d["pass_yds"]:
        parts.append(f"{d['pass_yds']} pass yds, {d['pass_td']} TD, {d['int']} INT")
    if d["rush_att"] or d["rush_yds"]:
        parts.append(f"{d['rush_att']}-{d['rush_yds']}-{d['rush_td']} rush")
    if d["rec"] or d["rec_yds"]:
        parts.append(f"{d['rec']}-{d['rec_yds']}-{d['rec_td']} rec")
    if d["fumb"]:
        parts.append(f"{d['fumb']} fumble")
    return ", ".join(parts) or "no stats"


def _err(e: Exception) -> str:
    return f"Error: {e}"


# ──────────────────────────────── tools ────────────────────────────────

@tool
def get_fantasy_rankings(position: str = "ALL", week: int = 0, sort: str = "week", limit: int = 40,
                         season: int = 0) -> str:
    """Fantasy rankings for a week (full PPR): each player's projected points, opponent, 1–5 matchup stars
    (5 = easiest), points per game so far, rest-of-season (ROS) rank and projected points, preseason ADP rank,
    and simulated rostered % / started % (share of 100 simulated 12-team leagues). position = QB, RB, WR, TE or
    ALL; week defaults to this article's week; sort = 'week' (this week's projection) or 'ros' (rest of season).
    Also lists the teams on bye. Use for Start/Sit, Rankings & Tiers and ROS tiers."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            w = int(week) or _publication_week(cur, s)
            if w < 2 or w > SEASON_WEEKS:
                return (f"Weekly projections exist for weeks 2–{SEASON_WEEKS}. For Week 1 (preseason) use "
                        "get_fantasy_draft_board.")
            err = _check_forward_week(cur, s, w)
            if err:
                return err
            proj = _projections(cur, s, w)
            if not proj:
                return f"No fantasy projections stored for Season {s}, Week {w}."
            roster, opp, sim = _roster(cur, s), _opponents(cur, s), _sim_week(cur, s, w)
            cur.execute("SELECT abbreviation, id FROM teams")
            teams = dict(cur.fetchall())
        byes = sorted(a for a, tid in teams.items() if (tid, w) not in opp)
        ros_sort = str(sort).lower().startswith("ros")
        rows = [(pid, p) for pid, p in proj.items() if pid in roster and (group is None or roster[pid]["group"] == group)]
        if ros_sort:
            rows.sort(key=lambda t: (-(t[1]["ros"] or 0), t[0]))
        else:
            rows = [t for t in rows if t[1]["pos_rank"]]
            rows.sort(key=lambda t: (-(t[1]["proj"] or 0), t[0]))
        lines = []
        for i, (pid, p) in enumerate(rows[:max(1, min(int(limit), 150))], start=1):
            r = roster[pid]
            ros_pct, start_pct = sim.get(pid, (0.0, 0.0))
            matchup = opp.get((r["team_id"], w), "BYE")
            stars = f"{p['stars']}★" if p["stars"] else "—"
            lines.append(
                f"{i}. {r['name']} (ID {pid}) | {r['position']} {r['team']} {r['depth']} | {matchup} {stars} | "
                f"proj {p['proj']:.1f} ({p['pos_rank'] or 'bye'}) | {p['avg'] or 0:.1f} PPR/g | "
                f"ROS {p['ros_rank']} {p['ros']:.0f} pts | ADP rank {p['adp_rank'] or '—'} | "
                f"rostered {_pct(ros_pct)}, started {_pct(start_pct)}")
        return json.dumps({
            "season": s, "week": w, "sort": "rest of season" if ros_sort else "this week", "teams_on_bye": byes,
            "note": "Projections use only games before this week. Matchup stars: 1 = toughest defense for the "
                    "position, 5 = easiest. Rostered/started % come from 100 simulated 12-team PPR leagues.",
            "players": lines,
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_week_results(week: int = 0, position: str = "ALL", limit: int = 15, season: int = 0) -> str:
    """Last week's fantasy results (full PPR): top scorers, biggest beats and biggest misses against the
    pre-game projection (misses only among players projected 8+), each with the box-score line, positional
    finish that week and rostered %. week defaults to the latest completed week this article may see.
    Use for Weekly Reactions and booms/busts."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            last = _last_visible_week(cur, s)
            if not last:
                return "No completed games this article may see yet (preseason)."
            w = int(week) or last
            if w < 1 or w > last:
                return f"Week {w} has no completed games this article may see (latest: Week {last})."
            roster, lines = _roster(cur, s), [l for l in _lines(cur, s, w) if l["week"] == w]
            proj = _projections(cur, s, w)
            sim = _sim_week(cur, s, w)
            opp = _opponents(cur, s)
        rows = [l for l in lines if l["pid"] in roster and (group is None or roster[l["pid"]]["group"] == group)]
        finish = {}
        for g in GROUPS:
            ranked = sorted((l for l in lines if l["pid"] in roster and roster[l["pid"]]["group"] == g),
                            key=lambda l: -l["ppr"])
            for i, l in enumerate(ranked, start=1):
                finish[(l["pid"], l["team_id"])] = f"{g}{i}"

        def fmt(l):
            r, p = roster[l["pid"]], proj.get(l["pid"])
            vs = f" vs proj {p['proj']:.1f} ({l['ppr'] - p['proj']:+.1f})" if p else ""
            return (f"{r['name']} (ID {l['pid']}) | {r['position']} {r['team']} {opp.get((l['team_id'], w), '')} | "
                    f"{l['ppr']:.1f} PPR{vs} | finished {finish.get((l['pid'], l['team_id']), '—')} | "
                    f"{_stat_line(l)} | rostered {_pct(sim.get(l['pid'], (0.0,))[0])}")

        n = max(1, min(int(limit), 50))
        with_proj = [l for l in rows if l["pid"] in proj]
        return json.dumps({
            "season": s, "week": w,
            "note": "Projection = what the Mu model projected before kickoff. Rostered % = share of 100 simulated "
                    "leagues with the player on a roster going into the week.",
            "top_scorers": [fmt(l) for l in sorted(rows, key=lambda l: -l["ppr"])[:n]],
            "biggest_beats": [fmt(l) for l in sorted(with_proj, key=lambda l: -(l["ppr"] - proj[l["pid"]]["proj"]))[:n]],
            "biggest_misses": [fmt(l) for l in sorted((l for l in with_proj if proj[l["pid"]]["proj"] >= 8),
                                                      key=lambda l: l["ppr"] - proj[l["pid"]]["proj"])[:n]],
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_usage(team_identifier: str = "", recent_weeks: int = 3, limit: int = 20, season: int = 0) -> str:
    """Fantasy usage (volume): carries, receptions and touches per game, each player's share of his team's
    carries and receptions, comparing the last `recent_weeks` games with the rest of the season. With a team
    (name or abbreviation): that team's skill players. Without one: the league's biggest touch risers and
    fallers (3+ games). Use for Box Score Breakdown & Usage and Waiver Wire."""
    try:
        s = resolve_season(season)
        n_recent = max(1, min(int(recent_weeks), 8))
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            last = _last_visible_week(cur, s)
            if not last:
                return "No completed games this article may see yet."
            team_id = None
            if team_identifier:
                from newsroom.db_tools import _resolve_team_id
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
            roster, lines = _roster(cur, s), _lines(cur, s, last)
        team_weeks = defaultdict(set)
        team_tot = defaultdict(lambda: [0, 0])          # (team, week) -> [carries, receptions]
        per = defaultdict(list)                         # (pid, team) -> lines
        for l in lines:
            team_weeks[l["team_id"]].add(l["week"])
            team_tot[(l["team_id"], l["week"])][0] += l["rush_att"]
            team_tot[(l["team_id"], l["week"])][1] += l["rec"]
            if l["pid"] in roster:
                per[(l["pid"], l["team_id"])].append(l)

        def split(pid, tid):
            weeks = sorted(team_weeks[tid])
            recent, early = set(weeks[-n_recent:]), set(weeks[:-n_recent])
            mine = {l["week"]: l for l in per[(pid, tid)]}

            def agg(ws):
                if not ws:
                    return None
                car = sum(mine[w]["rush_att"] for w in ws if w in mine)
                rec = sum(mine[w]["rec"] for w in ws if w in mine)
                tcar = sum(team_tot[(tid, w)][0] for w in ws)
                trec = sum(team_tot[(tid, w)][1] for w in ws)
                ppr = sum(mine[w]["ppr"] for w in ws if w in mine)
                return {"car_g": car / len(ws), "rec_g": rec / len(ws), "touch_g": (car + rec) / len(ws),
                        "car_share": car / tcar if tcar else 0.0, "rec_share": rec / trec if trec else 0.0,
                        "ppr_g": ppr / len(ws)}
            return agg(recent), agg(early), len(mine)

        def fmt(pid, tid, rec, early):
            r = roster[pid]
            trend = f" (rest of season {early['touch_g']:.1f} touches/g, {early['ppr_g']:.1f} PPR/g)" if early else ""
            return (f"{r['name']} (ID {pid}) | {r['position']} {r['team']} {r['depth']} | last {n_recent}: "
                    f"{rec['car_g']:.1f} car/g ({rec['car_share']:.0%} of team), {rec['rec_g']:.1f} rec/g "
                    f"({rec['rec_share']:.0%}), {rec['touch_g']:.1f} touches/g, {rec['ppr_g']:.1f} PPR/g{trend}")

        out = {"season": s, "through_week": last,
               "note": "Shares are of the team's carries and receptions in those weeks. Targets and snaps are "
                       "not tracked; receptions stand in for passing-game volume."}
        if team_id:
            players = [(pid, tid) for pid, tid in per if tid == team_id]
            rows = []
            for pid, tid in players:
                rec, early, _ = split(pid, tid)
                if rec and rec["touch_g"] > 0 and roster[pid]["group"] != "QB":
                    rows.append((rec["touch_g"], fmt(pid, tid, rec, early)))
            out["players"] = [t for _, t in sorted(rows, reverse=True)]
        else:
            moves = []
            for pid, tid in per:
                rec, early, games = split(pid, tid)
                if rec and early and games >= 3 and roster[pid]["group"] != "QB":
                    moves.append((rec["touch_g"] - early["touch_g"], fmt(pid, tid, rec, early)))
            moves.sort(key=lambda t: t[0])
            n = max(1, min(int(limit), 40))
            out["risers"] = [f"{d:+.1f} touches/g | {t}" for d, t in reversed(moves[-n:])]
            out["fallers"] = [f"{d:+.1f} touches/g | {t}" for d, t in moves[:n]]
        return json.dumps(out, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_waiver_wire(week: int = 0, position: str = "ALL", max_rostered_pct: int = 50, limit: int = 20,
                            season: int = 0) -> str:
    """Waiver-wire candidates for a week: players rostered in at most `max_rostered_pct`% of the 100 simulated
    12-team leagues, ordered by rest-of-season projection, with rostered % and its change from last week, last
    game and recent PPR, touches per game, ROS rank and this week's matchup and projection. Also lists the most
    added and most dropped players. week defaults to this article's week."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            w = int(week) or _publication_week(cur, s)
            w = min(max(w, 2), LAST_FANTASY_WEEK)
            err = _check_forward_week(cur, s, w)
            if err:
                return err
            now, before = _sim_week(cur, s, w), _sim_week(cur, s, w - 1)
            if not now:
                return f"No simulated-league rosters stored for Season {s}, Week {w}."
            proj, roster, opp = _projections(cur, s, w), _roster(cur, s), _opponents(cur, s)
            lines = _lines(cur, s, w - 1)
            moves = fetch_moves(cur, s)
        recent = defaultdict(list)
        for l in sorted(lines, key=lambda l: l["week"]):
            if l["pid"] in roster and l["team_id"] == roster[l["pid"]]["team_id"]:
                recent[l["pid"]].append(l)

        def fmt(pid):
            r, p = roster[pid], proj.get(pid, {})
            rp, prev = now.get(pid, (0.0, 0.0))[0], before.get(pid, (0.0, 0.0))[0]
            games = recent[pid][-3:]
            last_g = games[-1] if games else None
            last_txt = f"last game {last_g['ppr']:.1f} PPR ({_stat_line(last_g)})" if last_g else "no games yet"
            avg3 = sum(g["ppr"] for g in games) / len(games) if games else 0.0
            touches = sum(g["rush_att"] + g["rec"] for g in games) / len(games) if games else 0.0
            return (f"{r['name']} (ID {pid}) | {r['position']} {r['team']} {r['depth']} | rostered {rp:.0f}% "
                    f"({rp - prev:+.0f}) | {last_txt} | last {len(games)}: {avg3:.1f} PPR/g, {touches:.1f} touches/g | "
                    f"ROS {p.get('ros_rank', '—')} {p.get('ros') or 0:.0f} pts | this week "
                    f"{opp.get((r['team_id'], w), 'BYE')} proj {p.get('proj') or 0:.1f}")

        pool = [pid for pid in roster if (group is None or roster[pid]["group"] == group)]
        cands = [pid for pid in pool if now.get(pid, (0.0,))[0] <= max_rostered_pct]
        cands.sort(key=lambda pid: (-(proj.get(pid, {}).get("ros") or 0), pid))
        change = {pid: now.get(pid, (0.0,))[0] - before.get(pid, (0.0,))[0] for pid in pool}
        n = max(1, min(int(limit), 50))
        return json.dumps({
            "season": s, "week": w, "max_rostered_pct": max_rostered_pct,
            "note": "Rostered % = share of 100 simulated 12-team PPR leagues with the player on a roster going into "
                    "this week; the change is from last week. ROS = projected points from this week through Week 18.",
            "candidates": [fmt(pid) for pid in cands[:n]],
            "most_added": [fmt(pid) for pid in sorted(pool, key=lambda p: -change[p])[:10] if change[pid] > 0],
            "most_dropped": [fmt(pid) for pid in sorted(pool, key=lambda p: change[p])[:10] if change[pid] < 0],
            "nfl_moves": nfl_move_lines(moves, roster, {pid: v[0] for pid, v in now.items()}, w),
            "nfl_moves_note": "Real NFL signings of skill players in the last two weeks and released players still on "
                              "fantasy rosters. A signing is a pickup only if his depth slot gives him a role; a "
                              "released player has no game and projects 0 until he signs.",
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_defense_vs_position(position: str = "ALL", view: str = "defense", season: int = 0) -> str:
    """Fantasy points (full PPR) each defense allows per game to QBs, RBs, WRs and TEs, season to date and over
    its last 4 games, ranked 1 = most generous. view = 'defense' (the table) or 'playoff_schedule' (each
    offense's Weeks 15–17 opponents with how generous those defenses are to the position, for fantasy-playoff
    planning). Use for Defense vs. Position, Matchup Exploits and the Playoff Schedule Planner."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            last = _last_visible_week(cur, s)
            if not last:
                return "No completed games this article may see yet."
            lines = _lines(cur, s, last)
            cur.execute("SELECT week, home_team_id, away_team_id, status FROM games WHERE season = %s", (s,))
            games = cur.fetchall()
            cur.execute("SELECT id, abbreviation FROM teams")
            abbr = dict(cur.fetchall())
        opp_of, played = {}, defaultdict(set)
        for week, home, away, status in games:
            opp_of[(home, week)], opp_of[(away, week)] = away, home
            if status == "COMPLETED" and week <= last:
                played[home].add(week)
                played[away].add(week)
        allowed = defaultdict(float)                     # (def_team, group, week)
        for l in lines:
            d = opp_of.get((l["team_id"], l["week"]))
            if d is not None:
                allowed[(d, GROUP[l["position"]], l["week"])] += l["ppr"]
        groups = [group] if group else list(GROUPS)
        table = {}
        for g in groups:
            rows = []
            for team, weeks in played.items():
                ws = sorted(weeks)
                season_pg = sum(allowed[(team, g, w)] for w in ws) / len(ws)
                l4 = ws[-4:]
                rows.append((team, season_pg, sum(allowed[(team, g, w)] for w in l4) / len(l4)))
            rows.sort(key=lambda r: -r[1])
            table[g] = {team: (i, pg, l4) for i, (team, pg, l4) in enumerate(rows, start=1)}

        out = {"season": s, "through_week": last,
               "note": "Rank 1 = allows the most fantasy points to the position (best matchup). Per game, full PPR."}
        if str(view).lower().startswith("playoff"):
            sched = {}
            for g in groups:
                lines_g = []
                for team in sorted(abbr, key=lambda t: abbr[t]):
                    opps = []
                    for w in range(15, LAST_FANTASY_WEEK + 1):
                        o = opp_of.get((team, w))
                        if o is None:
                            opps.append(f"W{w} BYE")
                        elif o in table[g]:
                            opps.append(f"W{w} {abbr[o]} (#{table[g][o][0]}, {table[g][o][1]:.1f}/g)")
                    ranks = [table[g][opp_of[(team, w)]][0] for w in range(15, LAST_FANTASY_WEEK + 1)
                             if opp_of.get((team, w)) in table[g]]
                    avg = sum(ranks) / len(ranks) if ranks else 99
                    lines_g.append((avg, f"{abbr[team]} {g}s: avg opponent rank {avg:.1f} | " + ", ".join(opps)))
                sched[g] = [t for _, t in sorted(lines_g)]
            out["playoff_schedule"] = sched
            out["note"] += " Playoff schedule: lower average opponent rank = easier Weeks 15–17."
        else:
            out["defenses"] = {g: [f"#{i} {abbr[team]}: {pg:.1f}/g season, {l4:.1f}/g last 4"
                                   for team, (i, pg, l4) in sorted(table[g].items(), key=lambda t: t[1][0])]
                               for g in groups}
        return json.dumps(out, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_player_profile(player_identifier: str, season: int = 0) -> str:
    """One player's fantasy season (full PPR): week-by-week opponent, points, pre-game projection, box-score
    line and simulated rostered/started %; scoring range (average, best, worst, standard deviation, 20+ point
    boom weeks, sub-8 bust weeks); touches per game; draft ADP with earliest/latest pick; current ROS rank.
    Accepts a player ID or name."""
    try:
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            roster = _roster(cur, s)
            ident = str(player_identifier).strip()
            if ident.isdigit() and int(ident) in roster:
                pid = int(ident)
            else:
                low = ident.lower()
                matches = [p for p, r in roster.items() if r["name"].lower() == low or r["short"].lower() == low]
                matches = matches or [p for p, r in roster.items() if low in r["name"].lower()]
                if not matches:
                    return f"No QB/RB/WR/TE named '{player_identifier}' in Season {s}."
                if len(matches) > 1:
                    return "Several players match: " + "; ".join(
                        f"{roster[p]['name']} (ID {p}, {roster[p]['position']} {roster[p]['team']})" for p in matches[:8])
                pid = matches[0]
            r = roster[pid]
            last = _last_visible_week(cur, s)
            pub = _publication_week(cur, s)
            lines = [l for l in _lines(cur, s, last) if l["pid"] == pid and l["team_id"] == r["team_id"]]
            cur.execute("""
                SELECT week, projected_points::float FROM fantasy_projections
                WHERE season = %s AND player_id = %s AND week BETWEEN 2 AND %s
            """, (s, pid, pub))
            proj = dict(cur.fetchall())
            cur.execute("""
                SELECT week, rostered_pct::float, started_pct::float FROM fantasy_sim_player_weeks
                WHERE season = %s AND player_id = %s AND week <= %s
            """, (s, pid, pub))
            sim = {w: (ro, st) for w, ro, st in cur.fetchall()}
            adp = _adp(cur, s).get(pid)
            now = _projections(cur, s, min(max(pub, 2), SEASON_WEEKS)).get(pid, {})
            opp = _opponents(cur, s)
            team_moves = [move_line(m, with_notable=False) for m in fetch_moves(cur, s) if m["player_id"] == pid]
        by_week = {l["week"]: l for l in lines}
        log = []
        for w in range(1, last + 1):
            if (r["team_id"], w) not in opp:
                log.append(f"W{w}: BYE")
                continue
            l = by_week.get(w)
            pts = f"{l['ppr']:.1f} PPR ({_stat_line(l)})" if l else "0.0 PPR (no stat line)"
            pj = f" | proj {proj[w]:.1f}" if w in proj else ""
            ro, st = sim.get(w, (0.0, 0.0))
            log.append(f"W{w} {opp[(r['team_id'], w)]}: {pts}{pj} | rostered {_pct(ro)}, started {_pct(st)}")
        pts = [l["ppr"] for l in lines]
        summary = {}
        if pts:
            mean = sum(pts) / len(pts)
            summary = {
                "games": len(pts), "ppr_total": round(sum(pts), 1), "ppr_per_game": round(mean, 1),
                "best": round(max(pts), 1), "worst": round(min(pts), 1),
                "std_dev": round(math.sqrt(sum((p - mean) ** 2 for p in pts) / len(pts)), 1),
                "boom_weeks_20plus": sum(p >= 20 for p in pts), "bust_weeks_under_8": sum(p < 8 for p in pts),
                "touches_per_game": round(sum(l["rush_att"] + l["rec"] for l in lines) / len(lines), 1),
            }
        return json.dumps({
            "player": f"{r['name']} (ID {pid})", "position": r["position"], "team": r["team"], "depth": r["depth"],
            "ovr": r["ovr"], "age": r["age"], "years_experience": r["exp"], "season": s,
            "draft": ({"adp": adp["adp"], "earliest_pick": adp["min"], "latest_pick": adp["max"],
                       "drafted_in_pct_of_leagues": adp["draft_pct"]} if adp else "not on the draft board"),
            "rest_of_season": ({"ros_rank": now.get("ros_rank"), "ros_points": now.get("ros"),
                                "this_week_projection": now.get("proj"), "this_week_rank": now.get("pos_rank")}
                               if now else None),
            "scoring": summary, "game_log": log,
            **({"team_moves": team_moves} if team_moves else {}),
            **({"status": f"released by {r['team']} ({week_label({'week': r.get('released_week')})}): no team, "
                          "projects 0 until he signs"} if r.get("free_agent") else {}),
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_draft_board(position: str = "ALL", limit: int = 60, rookies_only: bool = False,
                            strategy: str = "", season: int = 0) -> str:
    """Preseason fantasy draft board from 100 simulated 12-team PPR drafts: ADP (average pick; undrafted counts
    as pick 181), earliest and latest pick, % of leagues that drafted him, plus OVR, age, years of experience
    (0 = rookie), depth slot and his team's pass/rush offense rank. rookies_only limits to rookies. strategy
    (BPA, HERO_RB, ZERO_RB, RB_HEAVY, EARLY_QB, EARLY_TE) adds the ADP among managers using that strategy.
    Once games are played it also shows season PPR so far and positional finish (for ADP checkpoints and draft
    retros). Use for Draft Tiers, Sleepers & Busts, Rookie Rankings and Draft Retro."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        strat = str(strategy or "").strip().upper()
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            adp = _adp(cur, s)
            if not adp:
                return f"No simulated draft stored for Season {s}."
            roster = _roster(cur, s)
            cur.execute("SELECT team_id, pass_off_rank, rush_off_rank FROM team_seasons WHERE season = %s", (s,))
            off = {t: (p, r) for t, p, r in cur.fetchall()}
            strat_adp = {}
            if strat:
                cur.execute("""
                    SELECT pk.player_id, AVG(pk.pick)::float, COUNT(*) FROM fantasy_sim_picks pk
                    JOIN fantasy_sim_teams t ON t.season = pk.season AND t.league = pk.league AND t.team = pk.team
                    WHERE pk.season = %s AND t.strategy = %s GROUP BY pk.player_id
                """, (s, strat))
                strat_adp = {pid: (a, n) for pid, a, n in cur.fetchall()}
            last = _last_visible_week(cur, s)
            lines = _lines(cur, s, last) if last else []
        totals = defaultdict(float)
        for l in lines:
            if l["pid"] in roster and l["team_id"] == roster[l["pid"]]["team_id"]:
                totals[l["pid"]] += l["ppr"]
        finish = {}
        for g in GROUPS:
            ranked = sorted((p for p in roster if roster[p]["group"] == g), key=lambda p: -totals.get(p, 0.0))
            finish.update({p: f"{g}{i}" for i, p in enumerate(ranked, start=1)})
        rows = [pid for pid in adp if pid in roster and (group is None or roster[pid]["group"] == group)
                and (not rookies_only or roster[pid]["exp"] == 0)]
        rows.sort(key=lambda pid: (adp[pid]["adp"], pid))
        out = []
        for i, pid in enumerate(rows[:max(1, min(int(limit), 200))], start=1):
            r, a = roster[pid], adp[pid]
            rng = f"{a['min']}–{a['max']}" if a["min"] else "undrafted"
            p_rank, r_rank = off.get(r["team_id"], (None, None))
            line = (f"{i}. {r['name']} (ID {pid}) | {r['position']} {r['team']} {r['depth']} | ADP {a['adp']:.1f} "
                    f"(range {rng}, drafted in {a['draft_pct']:.0f}%) | OVR {r['ovr']} | age {r['age']} | exp {r['exp']} | "
                    f"team pass off #{p_rank or '—'}, rush off #{r_rank or '—'}")
            if strat:
                sa = strat_adp.get(pid)
                line += f" | {strat} ADP {sa[0]:.1f} ({sa[1]} picks)" if sa else f" | {strat}: never drafted"
            if last:
                line += f" | {totals.get(pid, 0.0):.0f} PPR through W{last} ({finish.get(pid, '—')})"
            out.append(line)
        return json.dumps({
            "season": s, "position": group or "ALL", "rookies_only": bool(rookies_only),
            "note": "ADP from 100 simulated drafts whose managers use different strategies (BPA, Hero RB, Zero RB, "
                    "RB Heavy, Early QB, Early TE). Depth slot = OVR order within the team's position group.",
            "players": out,
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_mock_draft(league: int = 1, rounds: int = 15, season: int = 0) -> str:
    """One of the 100 simulated 12-team PPR drafts, pick by pick: round, pick, team slot and its drafting
    strategy, player, position and NFL team. Use for the Mock Draft article."""
    try:
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            cur.execute("SELECT team, strategy FROM fantasy_sim_teams WHERE season = %s AND league = %s",
                        (s, int(league)))
            strategies = dict(cur.fetchall())
            if not strategies:
                return f"No simulated league {league} for Season {s} (leagues are numbered 1–100)."
            cur.execute("""
                SELECT pick, round, team, player_id FROM fantasy_sim_picks
                WHERE season = %s AND league = %s AND round <= %s ORDER BY pick
            """, (s, int(league), max(1, min(int(rounds), 15))))
            picks = cur.fetchall()
            roster = _roster(cur, s)
        lines = []
        for pick, rnd, team, pid in picks:
            r = roster.get(pid, {"name": f"player {pid}", "position": "?", "team": "?"})
            lines.append(f"{rnd}.{(pick - 1) % 12 + 1:02d} (#{pick}) Team {team} [{strategies[team]}]: "
                         f"{r['name']} (ID {pid}), {r['position']} {r['team']}")
        return json.dumps({"season": s, "league": int(league),
                           "teams": {f"Team {t}": st for t, st in sorted(strategies.items())},
                           "picks": lines}, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_season_leaders(position: str = "ALL", first_week: int = 1, last_week: int = 17, limit: int = 25,
                               season: int = 0) -> str:
    """Fantasy points leaders (full PPR) over a range of weeks: total and per-game PPR, games, positional finish,
    preseason ADP and % of simulated leagues that drafted him. Weeks 1–17 = the fantasy season; 15–17 = the fantasy
    playoffs. Use for Awards, the All-Fantasy Team, Season in Review, Draft Retro and Dynasty pieces."""
    try:
        group = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            last = _last_visible_week(cur, s)
            hi = min(int(last_week), last)
            lo = max(1, int(first_week))
            if hi < lo:
                return f"No completed weeks between Week {first_week} and Week {last_week} that this article may see."
            roster, adp = _roster(cur, s), _adp(cur, s)
            lines = [l for l in _lines(cur, s, hi) if l["week"] >= lo]
        tot, games = defaultdict(float), defaultdict(int)
        for l in lines:
            if l["pid"] in roster and l["team_id"] == roster[l["pid"]]["team_id"]:
                tot[l["pid"]] += l["ppr"]
                games[l["pid"]] += 1
        finish = {}
        for g in GROUPS:
            ranked = sorted((p for p in tot if roster[p]["group"] == g), key=lambda p: -tot[p])
            finish.update({p: f"{g}{i}" for i, p in enumerate(ranked, start=1)})
        rows = sorted((p for p in tot if group is None or roster[p]["group"] == group), key=lambda p: (-tot[p], p))
        out = []
        for i, pid in enumerate(rows[:max(1, min(int(limit), 100))], start=1):
            r, a = roster[pid], adp.get(pid)
            draft = (f"ADP {a['adp']:.1f}, drafted in {a['draft_pct']:.0f}% of leagues" if a and a["draft_pct"]
                     else "undrafted in every league")
            out.append(f"{i}. {r['name']} (ID {pid}) | {r['position']} {r['team']} | {tot[pid]:.1f} PPR in "
                       f"{games[pid]} games ({tot[pid] / games[pid]:.1f}/g) | finished {finish[pid]} | {draft}")
        return json.dumps({"season": s, "weeks": f"{lo}–{hi}", "position": group or "ALL",
                           "note": "Undrafted players were available on waivers after the draft.", "players": out},
                          indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_projection_accuracy(season: int = 0) -> str:
    """How accurate the Mu fantasy projections were this season: mean absolute error and correlation against
    actual PPR for projected starters (each week's top 12 QB/TE, 30 RB/WR), the error of simply guessing the
    average, accuracy by position, and the biggest over- and under-projections. Use for the Projection Report Card."""
    try:
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            last = _last_visible_week(cur, s)
            if last < 2:
                return "Projections start in Week 2; no projected week has been played yet."
            roster = _roster(cur, s)
            lines = _lines(cur, s, last)
            cur.execute("""
                SELECT week, player_id, projected_points::float, pos_rank FROM fantasy_projections
                WHERE season = %s AND week BETWEEN 2 AND %s AND player_id IS NOT NULL AND pos_rank IS NOT NULL
            """, (s, last))
            proj = cur.fetchall()
            opp = _opponents(cur, s)
        actual = defaultdict(float)
        for l in lines:
            if l["pid"] in roster and l["team_id"] == roster[l["pid"]]["team_id"]:
                actual[(l["pid"], l["week"])] += l["ppr"]
        cutoff = {"QB": 12, "RB": 30, "WR": 30, "TE": 12}
        pairs = []
        for week, pid, p, rank in proj:
            if pid not in roster or (roster[pid]["team_id"], week) not in opp:
                continue
            g = rank.rstrip("0123456789")
            if int(rank[len(g):]) <= cutoff.get(g, 0):
                pairs.append((week, pid, g, p, actual.get((pid, week), 0.0)))
        if not pairs:
            return "No projected starters with results yet."

        def stats(rows):
            n = len(rows)
            mae = sum(abs(p - a) for *_, p, a in rows) / n
            mp, ma = sum(r[3] for r in rows) / n, sum(r[4] for r in rows) / n
            cov = sum((r[3] - mp) * (r[4] - ma) for r in rows)
            vp, va = sum((r[3] - mp) ** 2 for r in rows), sum((r[4] - ma) ** 2 for r in rows)
            corr = cov / math.sqrt(vp * va) if vp and va else 0.0
            naive = sum(abs(ma - r[4]) for r in rows) / n
            return {"player_weeks": n, "mae": round(mae, 2), "guess_average_mae": round(naive, 2),
                    "correlation": round(corr, 3)}

        def fmt(r):
            week, pid, _, p, a = r
            x = roster[pid]
            return f"W{week} {x['name']} (ID {pid}, {x['position']} {x['team']}): projected {p:.1f}, scored {a:.1f} ({a - p:+.1f})"

        return json.dumps({
            "season": s, "weeks": f"2–{last}", "overall": stats(pairs),
            "by_position": {g: stats([r for r in pairs if r[2] == g]) for g in GROUPS if any(r[2] == g for r in pairs)},
            "biggest_underprojections": [fmt(r) for r in sorted(pairs, key=lambda r: r[3] - r[4])[:8]],
            "biggest_overprojections": [fmt(r) for r in sorted(pairs, key=lambda r: r[4] - r[3])[:8]],
            "note": "Error is in PPR points per player-week. Projections were made before each week's games.",
        }, indent=2)
    except Exception as e:
        return _err(e)


@tool
def get_fantasy_injuries(week: int = 0, position: str = "ALL", season: int = 0) -> str:
    """Skill-position injury report for fantasy football (QB, RB, WR, TE). Lists injured players with their
    depth chart role (e.g. RB1, WR2), injury type, weeks remaining, IR status, and the immediate backup /
    beneficiary who gains volume or touches. position = QB, RB, WR, TE or ALL; week defaults to this
    article's week. Use for Start/Sit, Waiver Wire, and Rankings to identify vacated volume and handcuffs."""
    try:
        group_filter = _group_filter(position)
        s = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn, conn.cursor() as cur:
            w = int(week) or _publication_week(cur, s)
            err = _check_forward_week(cur, s, w)
            if err:
                return err

            roster = _roster(cur, s)
            opponents = _opponents(cur, s)

            cur.execute("""
                SELECT DISTINCT week FROM injuries
                WHERE season = %s AND week <= %s
                ORDER BY week DESC LIMIT 1;
            """, (s, w))
            w_row = cur.fetchone()
            query_week = w_row[0] if w_row else w

            cur.execute("""
                SELECT 
                    i.player_id,
                    COALESCE(p.full_name, p.player_name, i.player_name) as player_name,
                    COALESCE(ps.position, i.position) as position,
                    COALESCE(ps.overall_rating, i.ovr) as ovr,
                    i.injury_type,
                    i.duration,
                    i.weeks_remaining,
                    i.is_ir,
                    i.team_id,
                    t.abbreviation as team_abbr
                FROM injuries i
                JOIN teams t ON t.id = i.team_id
                LEFT JOIN players p ON p.id = i.player_id
                LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
                WHERE i.season = %s AND i.week = %s
                  AND COALESCE(ps.position, i.position) IN ('QB', 'HB', 'FB', 'WR', 'TE')
                ORDER BY t.abbreviation, COALESCE(ps.overall_rating, i.ovr) DESC;
            """, (s, query_week))
            rows = cur.fetchall()

            by_slot = defaultdict(list)
            for pid, pdata in roster.items():
                by_slot[(pdata["team_id"], pdata["group"])].append((-(pdata["ovr"] or 0), pid))
            for k in by_slot:
                by_slot[k].sort()

            injured_list = []
            for pid, pname, pos, ovr, inj_type, dur, w_rem, is_ir, team_id, team_abbr in rows:
                g = GROUP.get(pos)
                if group_filter and g != group_filter:
                    continue

                r_entry = roster.get(pid, {})
                depth = r_entry.get("depth", pos)
                opp = opponents.get((team_id, w), "BYE")

                slot_players = [p for _, p in by_slot.get((team_id, g), [])]
                beneficiary = None
                if pid in slot_players:
                    idx = slot_players.index(pid)
                    for next_pid in slot_players[idx + 1:]:
                        b_entry = roster[next_pid]
                        beneficiary = {
                            "player_id": next_pid,
                            "name": b_entry["name"],
                            "position": b_entry["position"],
                            "depth": b_entry.get("depth"),
                            "ovr": b_entry.get("ovr"),
                        }
                        break

                impact_desc = "Out / on IR"
                if depth in ("RB1", "WR1", "TE1", "QB1"):
                    if beneficiary:
                        impact_desc = f"Vacates starting {depth} role; {beneficiary['name']} ({beneficiary['depth']}) elevates to primary option."
                    else:
                        impact_desc = f"Vacates starting {depth} role."
                elif depth in ("RB2", "WR2", "WR3"):
                    if beneficiary:
                        impact_desc = f"Key rotational {depth} out; elevates {beneficiary['name']} into rotation."
                    else:
                        impact_desc = f"Key rotational {depth} out."

                injured_list.append({
                    "player_id": pid,
                    "player_name": pname,
                    "team": team_abbr,
                    "position": pos,
                    "fantasy_group": g,
                    "depth_role": depth,
                    "opponent": opp,
                    "injury_type": inj_type,
                    "duration": dur,
                    "weeks_remaining": int(w_rem or 0),
                    "is_ir": bool(is_ir),
                    "handcuff_beneficiary": beneficiary,
                    "fantasy_impact": impact_desc,
                })

            return json.dumps({
                "season": s,
                "week": query_week,
                "position_filter": position,
                "count": len(injured_list),
                "injuries": injured_list,
                "editorial_guidance": (
                    "Starters ruled OUT or on IR create immediate fantasy opportunity. "
                    "Highlight backup running backs and targeted receivers stepping into high-volume roles. "
                    "Note players who are OUT/IR to explain why they drop out of rankings or must be benched."
                )
            }, indent=2)
    except Exception as e:
        return _err(e)


FANTASY_TOOLS = [
    get_fantasy_rankings,
    get_fantasy_week_results,
    get_fantasy_usage,
    get_fantasy_waiver_wire,
    get_fantasy_defense_vs_position,
    get_fantasy_player_profile,
    get_fantasy_draft_board,
    get_fantasy_mock_draft,
    get_fantasy_season_leaders,
    get_fantasy_projection_accuracy,
    get_fantasy_injuries,
]
