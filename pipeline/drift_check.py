"""
Drift check (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.5): what changed in the Madden save since Project Mu last
wrote its state, i.e. what the user (or Madden) did in the game that Project Mu didn't.

  python3 -m pipeline.drift_check --snapshot captures/<run>/save.json [--season S --week W] [--dsn ...]

`diff(snapshot, cur, season, week)` returns
  {"ratings": [...], "contracts": [...], "rosters": [...], "depth_charts": [...], "settings": [...]}
where every entry names the player or team, the field, what Project Mu expects and what the save has:
  ratings       Project Mu's ratings: the newest player_attributes row (season <= S) plus the newest player_form
                overlay of season S up to week W ({Rating: delta}, result clamped to 0..99), vs players[].ratings
  contracts     player_contracts ACTIVE rows of season S (whole dollars; this season's cap hit from
                player_contract_years) vs the snapshot contracts, converted from Madden's $10,000 units with
                load_save.contract_values: length, years_remaining, total_value, bonus, cap_hit; a contract on only
                one side is one entry with field `contract`
  rosters       player_seasons.team_id of season S (NULL = free agent) vs the save's team per player; a save player
                Project Mu doesn't know, or a stored rostered player missing from the save, is an entry too
  depth_charts  per team, the newest depth_charts week <= W of season S with source MU_COACH or JR (JR wins a tie)
                vs the save's chart (reader_version >= 4), slot by slot, empty slots dropped
  settings      every progression / regression slider value that isn't 0 (Project Mu expects all 0, LS included)
The comparison (`compare`) is pure; the small fetch_* functions read the database, so tests feed fakes.
Nothing is written. The league office stores the report in league_office_runs.drift.
"""
import argparse
import json
import sys

import psycopg

from pipeline import load_save as ls

KINDS = ("ratings", "contracts", "rosters", "depth_charts", "settings")
CHART_SOURCES = ("MU_COACH", "JR")
CONTRACT_FIELDS = ("length", "years_remaining", "total_value", "bonus", "cap_hit")


# ------------------------------------------------------------------ fetch (DB) ----
def fetch_player_ids(cur):
    """{pgid: player id}."""
    cur.execute("SELECT madden_pgid, id FROM players WHERE madden_pgid IS NOT NULL")
    return dict(cur.fetchall())


def fetch_base_ratings(cur, season):
    """{player id: ratings} from each player's newest player_attributes row up to the season."""
    cur.execute("SELECT DISTINCT ON (player_id) player_id, ratings FROM player_attributes WHERE season <= %s "
                "ORDER BY player_id, season DESC, id DESC", (season,))
    return {pid: r for pid, r in cur.fetchall() if r}


def fetch_form_overlays(cur, season, week):
    """{player id: {Rating: delta}} from each player's newest player_form row of the season up to the week."""
    if week is None:
        return {}
    cur.execute("SELECT DISTINCT ON (player_id) player_id, overlay FROM player_form WHERE season = %s AND week <= %s "
                "ORDER BY player_id, week DESC", (season, week))
    return {pid: o for pid, o in cur.fetchall() if o}


def fetch_contracts(cur, season):
    """{player id: {team_id, length, years_remaining, total_value, bonus, cap_hit}} for the season's ACTIVE rows
    (whole dollars)."""
    cur.execute("SELECT c.player_id, c.team_id, c.length, c.years_remaining, c.total_value, c.bonus, y.cap_hit "
                "FROM player_contracts c LEFT JOIN player_contract_years y ON y.contract_id = c.id AND y.year = c.season "
                "WHERE c.season = %s AND c.status = 'ACTIVE'", (season,))
    return {r[0]: dict(zip(("team_id",) + CONTRACT_FIELDS, r[1:])) for r in cur.fetchall()}


def fetch_rosters(cur, season):
    """{player id: team id or None (free agent)} from player_seasons."""
    cur.execute("SELECT player_id, team_id FROM player_seasons WHERE season = %s", (season,))
    return dict(cur.fetchall())


def fetch_depth_charts(cur, season, week):
    """{team id: {"source", "week", "chart": {slot: [player id, ...]}}}: per team the newest stored week (<= week)
    with source MU_COACH or JR; JR's chart wins when both have that week."""
    sql = ("SELECT team_id, week, source, position_slot, depth, player_id FROM depth_charts "
           "WHERE season = %s AND source IN ('MU_COACH', 'JR')")
    args = [season]
    if week is not None:
        sql += " AND week <= %s"
        args.append(week)
    cur.execute(sql, args)
    rows = cur.fetchall()
    best = {}
    for team, wk, src, *_ in rows:
        key = (wk, src == "JR")
        if team not in best or key > best[team]:
            best[team] = key
    out = {}
    for team, wk, src, slot, depth, pid in sorted(rows, key=lambda r: (r[0], r[3], r[4])):
        if best[team] != (wk, src == "JR") or pid is None:
            continue
        out.setdefault(team, {"source": src, "week": wk, "chart": {}})["chart"].setdefault(slot, []).append(pid)
    return out


def fetch_state(cur, snapshot, season, week):
    """Everything compare() needs from the database."""
    return {
        "teams": ls.team_ids(cur, snapshot),
        "player_ids": fetch_player_ids(cur),
        "base_ratings": fetch_base_ratings(cur, season),
        "form_overlays": fetch_form_overlays(cur, season, week),
        "contracts": fetch_contracts(cur, season),
        "rosters": fetch_rosters(cur, season),
        "depth_charts": fetch_depth_charts(cur, season, week),
    }


# ------------------------------------------------------------- compare (pure) ----
def expected_ratings(base, overlay):
    """Base ratings plus a form overlay, clamped to Madden's 0..99."""
    out = dict(base or {})
    for k, d in (overlay or {}).items():
        if k in out and isinstance(out[k], (int, float)):
            out[k] = max(0, min(99, out[k] + d))
    return out


def _name(p):
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def _save_team(p, teams):
    if p.get("status") not in ls.LOADED_STATUSES or p.get("status") == "FreeAgent":
        return None
    return teams.get(p.get("team_index"))


def compare_ratings(snapshot, state):
    out = []
    ids = state["player_ids"]
    for p in snapshot["players"]:
        pid = ids.get(p["pgid"])
        base = state["base_ratings"].get(pid) if pid is not None else None
        if not base or not p.get("ratings"):
            continue
        want = expected_ratings(base, state["form_overlays"].get(pid))
        for field, exp in want.items():
            found = p["ratings"].get(field)
            if found != exp:
                out.append({"player_id": pid, "pgid": p["pgid"], "name": _name(p), "field": field,
                            "expected": exp, "found": found})
    return out


def compare_contracts(snapshot, state, season):
    out, seen = [], set()
    ids, stored = state["player_ids"], state["contracts"]
    for p in snapshot["players"]:
        pid = ids.get(p["pgid"])
        if pid is None:
            continue
        c = p.get("contract")
        found = None
        if _save_team(p, state["teams"]) is not None and c and c.get("length"):
            v = ls.contract_values(c, season)
            found = {f: v[f] for f in CONTRACT_FIELDS if f != "cap_hit"}
            found["cap_hit"] = v["years"].get(season, 0)
        exp = stored.get(pid)
        seen.add(pid)
        who = {"player_id": pid, "pgid": p["pgid"], "name": _name(p)}
        if exp is None and found is None:
            continue
        if exp is None or found is None:
            out.append({**who, "field": "contract", "expected": exp and {f: exp[f] for f in CONTRACT_FIELDS},
                        "found": found})
            continue
        for f in CONTRACT_FIELDS:
            if exp.get(f) != found[f]:
                out.append({**who, "field": f, "expected": exp.get(f), "found": found[f]})
    for pid, exp in stored.items():
        if pid not in seen:
            out.append({"player_id": pid, "pgid": None, "name": None, "field": "contract",
                        "expected": {f: exp[f] for f in CONTRACT_FIELDS}, "found": None})
    return out


def compare_rosters(snapshot, state):
    out, seen = [], set()
    ids, rosters = state["player_ids"], state["rosters"]
    for p in snapshot["players"]:
        found = _save_team(p, state["teams"])
        pid = ids.get(p["pgid"])
        who = {"player_id": pid, "pgid": p["pgid"], "name": _name(p), "field": "team_id"}
        if pid is None:
            if found is not None:
                out.append({**who, "field": "player", "expected": None, "found": found})
            continue
        seen.add(pid)
        if pid not in rosters:
            if found is not None:
                out.append({**who, "expected": None, "found": found})
            continue
        if rosters[pid] != found:
            out.append({**who, "expected": rosters[pid], "found": found})
    for pid, team in rosters.items():
        if team is not None and pid not in seen:
            out.append({"player_id": pid, "pgid": None, "name": None, "field": "team_id", "expected": team,
                        "found": None})
    return out


def compare_depth_charts(snapshot, state):
    charts = snapshot.get("depth_charts")
    if charts is None:          # reader_version < 4: nothing to compare against
        return []
    teams, ids = state["teams"], state["player_ids"]
    save = {}
    for entry in charts:
        team = teams.get(entry.get("team_index"))
        if team is None:
            continue
        save[team] = {slot: [ids.get(g, f"pgid:{g}") for g in pgids if g is not None]
                      for slot, pgids in (entry.get("slots") or {}).items()}
    out = []
    for team, stored in sorted(state["depth_charts"].items()):
        found_chart = save.get(team, {})
        for slot in sorted(set(stored["chart"]) | set(found_chart)):
            exp = [p for p in stored["chart"].get(slot, []) if p is not None]
            found = found_chart.get(slot, [])
            if exp != found:
                out.append({"team_id": team, "field": slot, "expected": exp, "found": found,
                            "source": stored["source"], "week": stored["week"]})
    return out


def compare_settings(snapshot):
    return [{"slider": key, "field": field, "expected": 0, "found": value}
            for key, field, value in ls.nonzero_sliders(snapshot)]


def compare(snapshot, state, season):
    """The drift report from a snapshot and fetched state (pure)."""
    return {
        "ratings": compare_ratings(snapshot, state),
        "contracts": compare_contracts(snapshot, state, season),
        "rosters": compare_rosters(snapshot, state),
        "depth_charts": compare_depth_charts(snapshot, state),
        "settings": compare_settings(snapshot),
    }


def diff(snapshot, cur, season, week):
    """Compares the save against Project Mu's last written state for (season, week)."""
    return compare(snapshot, fetch_state(cur, snapshot, season, week), season)


# ------------------------------------------------------------------------ CLI ----
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, help="JSON written by pipeline/save_reader/pc_save_reader.mjs")
    ap.add_argument("--season", type=int, help="default: the save's calendar year")
    ap.add_argument("--week", type=int, help="default: the save's current week, else league_state's")
    ap.add_argument("--dsn", default=ls.DSN, help="database (default: DATABASE_URL or the local madden database)")
    a = ap.parse_args(argv)
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    season = a.season or snapshot["season"]["calendar_year"]
    week = a.week if a.week is not None else ls.current_week(snapshot)
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        if week is None:
            cur.execute("SELECT current_week FROM league_state WHERE id = 1")
            row = cur.fetchone()
            week = row[0] if row else None
        try:
            report = diff(snapshot, cur, season, week)
        except ls.LoadError as e:
            print(f"DRIFT CHECK STOPPED: {e}")
            sys.exit(1)
        finally:
            conn.rollback()
    print(json.dumps({"season": season, "week": week, **report}, indent=1, default=str))


if __name__ == "__main__":
    main()
