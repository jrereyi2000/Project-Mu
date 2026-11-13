"""
Real-NFL decision-rate targets for tuning Mu's decision engines (docs/CLOSE_OUT_CHECKLIST.md P0-2, P3-06).

  python3 -m engines.tuning.decision_targets [--seasons 2015-2025] [--refresh] [--out PATH]

engines/tuning/targets.py scores the *stat* output of a simulated season. This module scores the *decisions*
that produced it: how long careers last, at what age players retire, how often coaches change, how many trades
a season has (split in-season vs offseason), how much of the league changes teams, and what a draft pick turns
into. Without these there is no yardstick for "decisions reflect the real NFL".

Sources (nflverse releases through engines.history_backfill.sources._get and its cache, data/history/.cache/;
--refresh downloads again):
  rosters/roster_<season>.csv             who was on a roster, and with what status -> career length
  players/players.csv                     birth_date, position
  draft_picks/draft_picks.csv             who was drafted, pick outcomes (games, wAV, seasons started)
  schedules/games.csv                     home_coach / away_coach -> HC changes; game dates -> season windows
  trades/trades.csv                       every trade, one row per asset, grouped by trade_id
  stats_player/stats_player_reg_<season>.csv   games played (rookie usage, team movement)

Career length is measured two ways, and the difference matters:
  seasons_active  distinct seasons on a roster with an active status (ACT/RES/DEV/PUP/SUS/RSN/NWT), excluding
                  players whose only appearance that season was CUT. This is the definition that reproduces the
                  widely quoted "~3 year average NFL career": mean 3.4 over every player who appeared.
  seasons_any     every season on a roster, CUT included.
The population split is what explains the rest: the ~3-year figure is the *all players* mean, and it is dragged
down by undrafted churn (undrafted mean ~2 active seasons). Drafted players average ~6. Mu's rosters are built
mostly from draft classes: **P3-06a measured Mu's inflow on 2026-11-12** (`madden_tune_p2c`, 2027): 247 players
entered, 244 of them rookies, at least 224 drafted and at most 20 undrafted, so the drafted cohort is 92% of
Mu's rookie inflow and the drafted target stands. The module still reports all three populations so the caller
can check the split, not assume it.

Output: data/tuning/decision_targets.json  {"meta": {...}, "career_length": {...}, "retirement_age": {...},
"coach_changes": {...}, "trades": {...}, "team_movement": {...}, "draft_by_round": {...}, "rookie_usage": {...}}
and a markdown report beside it.
"""
import argparse
import datetime as dt
import json
import os

from engines.history_backfill.sources import NFLVERSE, _get

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tuning")
DEFAULT_SEASONS = range(2015, 2026)
ROSTER_FIRST = 1999          # rosters are available from here; needed to count a career that began before 2015

# statuses that mean the player was actually on the roster that season (CUT excluded deliberately)
ACTIVE_STATUSES = frozenset({"ACT", "RES", "DEV", "PUP", "SUS", "RSN", "NWT"})

# nflverse position -> Mu's position group (Madden 27: LE/RE/DT, LOLB/MLB/ROLB, CB/FS/SS, K, P)
GROUPS = {
    "QB": {"QB"}, "RB": {"RB", "FB"}, "WR": {"WR"}, "TE": {"TE"},
    "OL": {"OT", "T", "OG", "G", "C", "OL"},
    "DL": {"DE", "DT", "NT", "DL"},
    "LB": {"LB", "OLB", "ILB", "MLB"},
    "DB": {"CB", "S", "SAF", "FS", "SS", "DB"},
    "K": {"K"}, "P": {"P"},
}
GROUP_ORDER = ("QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "K", "P")
MIN_COHORT = 15          # a group with fewer players reports nothing rather than a misleading band


def group_of(position):
    """Mu position group for an nflverse position string, or None."""
    if not position:
        return None
    p = str(position).upper().strip()
    for grp, members in GROUPS.items():
        if p in members:
            return grp
    return None


def band(values, qs=(0.25, 0.5, 0.75, 0.9)):
    """Quartile band of a numeric list: {n, mean, p25, median, p75, p90}. None when too small."""
    vals = sorted(float(v) for v in values if v is not None)
    n = len(vals)
    if n < 2:
        return {"n": n}
    out = {"n": n, "mean": round(sum(vals) / n, 2)}
    for q in qs:
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        key = "median" if q == 0.5 else f"p{int(q * 100)}"
        out[key] = round(vals[idx], 2)
    return out


def share_at_least(values, threshold):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return round(sum(1 for v in vals if v >= threshold) / len(vals), 3)


def season_band(per_season):
    """{season: count} -> {lo, median, hi, mean, by_season} over the seasons present."""
    vals = [v for v in per_season.values() if v is not None]
    if not vals:
        return {}
    vals.sort()
    return {"lo": vals[0], "median": vals[len(vals) // 2], "hi": vals[-1],
            "mean": round(sum(vals) / len(vals), 2),
            "by_season": {str(k): v for k, v in sorted(per_season.items())}}


# ---------------------------------------------------------------- coaches

def coach_changes(games_rows, seasons):
    """Head-coach changes from per-game coach names.

    games_rows: iterable of (season, week, team, coach). Returns
      between_seasons  a new HC after the previous season, counted at the season he starts
      midseason        teams that used more than one HC inside a season
    """
    by_team = {}
    for season, week, team, coach in games_rows:
        if coach is None or team is None:
            continue
        by_team.setdefault((int(season), str(team)), []).append((int(week), str(coach)))

    season_coaches = {}
    for (season, team), rows in by_team.items():
        rows.sort()
        season_coaches[(season, team)] = (rows[0][1], rows[-1][1])

    between, midseason = {}, {}
    for (season, team), (first, last) in season_coaches.items():
        if first != last:
            midseason[season] = midseason.get(season, 0) + 1
        nxt = season_coaches.get((season + 1, team))
        if nxt is not None and nxt[0] != last:
            between[season + 1] = between.get(season + 1, 0) + 1

    seasons = set(seasons)
    return (season_band({s: between.get(s, 0) for s in seasons}),
            season_band({s: midseason.get(s, 0) for s in seasons}))


# ---------------------------------------------------------------- trades

def season_windows(game_rows):
    """{season: (first date, last date)} across every game type, from (season, gameday) rows."""
    out = {}
    for season, day in game_rows:
        if season is None or day is None:
            continue
        try:
            d = dt.date.fromisoformat(str(day)[:10])
        except ValueError:
            continue
        lo, hi = out.get(int(season), (d, d))
        out[int(season)] = (min(lo, d), max(hi, d))
    return out


def trade_counts(trade_rows, seasons, windows=None):
    """Trades per season from nflverse trades.csv rows (one row per asset).

    A trade is one trade_id. 'player' when any of its rows names a player (pfr_id).

    With `windows` ({season: (first game, last game)}) the trades are split by date:
      in_season  from the season's first game to its last game (Week 1 through the Super Bowl; the NFL's
                 deadline in early November means almost all of them fall in September-November)
      offseason  everything else (March free agency, the draft, and the September cutdown week before Week 1)

    The split matters: Mu's weekly trade engine runs during the season, and the real in-season rate is roughly
    a fifth of the offseason rate. Note that moving the in-season start from "first game" to "September 1"
    adds the cutdown-week trades — about 4 a season — which is enough to move the total by ~30%.
    """
    trades = {}
    for row in trade_rows:
        season = row.get("season")
        tid = row.get("trade_id")
        if season is None or tid is None:
            continue
        key = (int(float(season)), str(tid))
        entry = trades.setdefault(key, {"player": False, "date": None})
        if row.get("pfr_id"):
            entry["player"] = True
        day = row.get("trade_date")
        if day and (entry["date"] is None or str(day) < entry["date"]):
            entry["date"] = str(day)

    per, per_player, in_season, off_season = {}, {}, {}, {}
    for (season, _tid), entry in trades.items():
        per[season] = per.get(season, 0) + 1
        if entry["player"]:
            per_player[season] = per_player.get(season, 0) + 1
        if windows and entry["date"]:
            lo, hi = windows.get(season, (None, None))
            if lo is not None:
                try:
                    day = dt.date.fromisoformat(entry["date"][:10])
                except ValueError:
                    day = None
                if day is not None:
                    if lo <= day <= hi:
                        in_season[season] = in_season.get(season, 0) + 1
                    else:
                        off_season[season] = off_season.get(season, 0) + 1
    seasons = set(seasons)
    out = {"all": season_band({s: per.get(s, 0) for s in seasons}),
           "with_player": season_band({s: per_player.get(s, 0) for s in seasons})}
    if windows:
        out["in_season"] = season_band({s: in_season.get(s, 0) for s in seasons})
        out["offseason"] = season_band({s: off_season.get(s, 0) for s in seasons})
    return out


# ---------------------------------------------------------------- careers

def player_careers(roster_rows):
    """{gsis_id: {active: set(season), any: set(season), pos: str}} from roster rows.

    roster_rows: iterable of dicts with gsis_id, season, status, position.
    """
    out = {}
    for row in roster_rows:
        gid = row.get("gsis_id")
        season = row.get("season")
        if not gid or season is None:
            continue
        try:
            s = int(float(season))
        except ValueError:
            continue
        e = out.setdefault(gid, {"active": set(), "any": set(), "pos": None})
        e["any"].add(s)
        if str(row.get("status") or "").upper() in ACTIVE_STATUSES:
            e["active"].add(s)
        if not e["pos"] and row.get("position"):
            e["pos"] = row["position"]
    return out


def career_tables(careers, drafted_ids, seasons):
    """{"all_players"|"drafted"|"undrafted": {group: band + share_10plus}} for careers ended inside the window."""
    lo_cut, hi_cut = min(seasons), max(seasons) - 1
    pools = {"all_players": {}, "drafted": {}, "undrafted": {}}
    for gid, c in careers.items():
        active = c["active"]
        if not active:
            continue
        last = max(active)
        if not (lo_cut <= last <= hi_cut):
            continue
        grp = group_of(c["pos"])
        if not grp:
            continue
        length = len(active)
        pools["all_players"].setdefault(grp, []).append(length)
        pool = "drafted" if gid in drafted_ids else "undrafted"
        pools[pool].setdefault(grp, []).append(length)
    career_length = {}
    for name, pool in pools.items():
        table = {}
        for grp in GROUP_ORDER:
            vals = pool.get(grp) or []
            if len(vals) < MIN_COHORT:
                continue
            b = band(vals)
            b["share_10plus"] = share_at_least(vals, 10)
            table[grp] = b
        career_length[name] = table
    return career_length


def retirement_ages(careers, players_by_id, seasons, drafted_ids):
    """{"all_players"|"drafted": {group: [age at last active season]}} for careers ended inside the window."""
    lo_cut, hi_cut = min(seasons), max(seasons) - 1
    out = {"all_players": {}, "drafted": {}}
    for gid, c in careers.items():
        active = c["active"]
        if not active:
            continue
        last = max(active)
        if not (lo_cut <= last <= hi_cut):
            continue
        grp = group_of(c["pos"])
        birth = (players_by_id.get(gid) or {}).get("birth")
        if not grp or not birth:
            continue
        try:
            age = (dt.date(last, 12, 31) - dt.date.fromisoformat(str(birth)[:10])).days / 365.25
        except ValueError:
            continue
        out["all_players"].setdefault(grp, []).append(age)
        if gid in drafted_ids:
            out["drafted"].setdefault(grp, []).append(age)
    return out


def cut_only_summary(careers, seasons):
    """How many players were on a roster but never with an active status, and what the mean looks like with them.

    This is the reconciliation with the widely quoted "average NFL career ~3 years": including players whose
    only roster appearance that year was CUT (never active) pulls the all-players mean down from ~4.2 to ~3.4.
    """
    lo_cut, hi_cut = min(seasons), max(seasons) - 1
    active_lengths, zeros = [], 0
    for c in careers.values():
        if c["active"]:
            if lo_cut <= max(c["active"]) <= hi_cut:
                active_lengths.append(len(c["active"]))
        elif c["any"]:
            if lo_cut <= max(c["any"]) <= hi_cut:
                zeros += 1
    n = len(active_lengths)
    if not n:
        return {}
    total = sum(active_lengths)
    return {"n_active": n, "cut_only": zeros,
            "mean_active_only": round(total / n, 2),
            "mean_including_cut_only": round(total / (n + zeros), 2),
            "share_cut_only": round(zeros / (n + zeros), 3)}


# ---------------------------------------------------------------- output

def build_targets(seasons=None, cache_refresh=False):
    """Build the decision-target document. Reads nflverse CSVs through the shared cache."""
    seasons = list(seasons or DEFAULT_SEASONS)
    import csv

    def path(release, name):
        # same cache naming as engines/tuning/targets.py, so files already downloaded are reused
        return _get(f"{NFLVERSE}/{release}/{name}", "nflverse_" + name, refresh=cache_refresh)

    def rows(p):
        with open(p, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # --- games: coaches (regular season) and the season windows used to split trades
    all_games = rows(path("schedules", "games.csv"))
    windows = season_windows((r.get("season"), r.get("gameday")) for r in all_games)
    coach_rows = []
    for r in all_games:
        if r.get("game_type") != "REG" or not r.get("season") or int(r["season"]) > max(seasons):
            continue
        s, w = int(r["season"]), int(r.get("week") or 0)
        coach_rows.append((s, w, r.get("home_team"), r.get("home_coach")))
        coach_rows.append((s, w, r.get("away_team"), r.get("away_coach")))
    coach_between, coach_mid = coach_changes(coach_rows, seasons)

    # --- players: birth dates and positions
    players_by_id = {}
    for r in rows(path("players", "players.csv")):
        gid = r.get("gsis_id")
        if gid:
            players_by_id[gid] = {"birth": r.get("birth_date"), "position": r.get("position")}

    # --- drafted pool and pick outcomes
    picks = rows(path("draft_picks", "draft_picks.csv"))
    drafted = {}
    for r in picks:
        gid = r.get("gsis_id")
        try:
            season = int(float(r["season"])) if r.get("season") else None
            rnd = int(float(r["round"])) if r.get("round") else None
            games_n = int(float(r["games"])) if r.get("games") else 0
            started = float(r["seasons_started"]) if r.get("seasons_started") else 0
            wav = float(r["w_av"]) if r.get("w_av") else 0
        except ValueError:
            continue
        if not gid or season is None:
            continue
        drafted[gid] = {"round": rnd, "season": season, "games": games_n, "started": started, "w_av": wav,
                        "position": r.get("position") or (players_by_id.get(gid) or {}).get("position")}

    # --- rosters: career length, the definition that reproduces the published "~3 years"
    def roster_rows():
        for s in range(min(ROSTER_FIRST, min(seasons)), max(seasons) + 1):
            yield from rows(path("rosters", f"roster_{s}.csv"))

    careers = player_careers(roster_rows())
    career_length = career_tables(careers, set(drafted), seasons)
    ages = retirement_ages(careers, players_by_id, seasons, set(drafted))
    retirement_age = {pop: {grp: band(ages[pop][grp]) for grp in GROUP_ORDER
                            if len(ages[pop].get(grp, [])) >= MIN_COHORT}
                      for pop in ages}
    cut_only = cut_only_summary(careers, seasons)

    # --- stats: team movement (year over year, players with >=8 games in both seasons) and rookie usage
    per_season_team = {}
    for s in seasons:
        d = {}
        for r in rows(path("stats_player", f"stats_player_reg_{s}.csv")):
            gid = r.get("player_id")
            if not gid:
                continue
            try:
                g = int(float(r["games"])) if r.get("games") else 0
            except ValueError:
                g = 0
            team, prev = r.get("recent_team"), d.get(gid)
            d[gid] = (team or (prev[0] if prev else None), max(g, prev[1] if prev else 0))
        per_season_team[s] = d

    movement_eligible, movement_moved = {}, {}
    for prev_s, cur_s in zip(seasons, seasons[1:]):
        before, after = per_season_team.get(prev_s) or {}, per_season_team.get(cur_s) or {}
        eligible = moved = 0
        for gid, (team_b, games_b) in after.items():
            pa = before.get(gid)
            if not pa:
                continue
            team_a, games_a = pa
            if games_a >= 8 and games_b >= 8 and team_a and team_b:
                eligible += 1
                if team_a != team_b:
                    moved += 1
        movement_eligible[cur_s] = eligible
        movement_moved[cur_s] = moved

    movement = season_band({s: (round(movement_moved[s] / movement_eligible[s], 3)
                                if movement_eligible.get(s) else None) for s in seasons[1:]})
    movement_players = season_band({s: movement_moved.get(s, 0) for s in seasons[1:]})

    # --- rookie usage: games in a rookie's first season, skill positions only (linemen have no stat line)
    rookie_games = []
    for gid, rec in drafted.items():
        s = rec["season"]
        if s not in seasons or group_of(rec.get("position")) in (None, "OL"):
            continue
        team_games = per_season_team.get(s) or {}
        rookie_games.append(team_games[gid][1] if gid in team_games else 0)
    rookie_usage = {
        "n": len(rookie_games),
        "median_games": band(rookie_games).get("median"),
        "share_8plus": share_at_least(rookie_games, 8),
        "share_12plus": share_at_least(rookie_games, 12),
    }

    # --- draft outcomes by round (classes with at least three seasons to establish)
    by_round = {}
    draft_lo, draft_hi = min(seasons), max(seasons) - 4
    for gid, d in drafted.items():
        r = d["round"]
        if r is None or r < 1 or r > 7 or not (draft_lo <= d["season"] <= draft_hi):
            continue
        e = by_round.setdefault(r, {"games": [], "w_av": [], "started": []})
        e["games"].append(d["games"])
        e["w_av"].append(d["w_av"])
        e["started"].append(d["started"])
    draft_by_round = {}
    for r, e in sorted(by_round.items()):
        gb, ab = band(e["games"]), band(e["w_av"])
        draft_by_round[str(r)] = {"n": len(e["games"]), "games": gb, "w_av": ab,
                                  "games_median": gb.get("median"), "w_av_median": ab.get("median"),
                                  "share_started_2plus": share_at_least(e["started"], 2)}

    # --- trades, split by whether they happen while the season is running
    trades = trade_counts(rows(path("trades", "trades.csv")), seasons, windows)

    return {
        "meta": {
            "source": "nflverse releases: rosters, players, draft_picks, schedules, trades, stats_player (reg)",
            "seasons": seasons,
            "career_window": f"careers ending {min(seasons)}-{max(seasons) - 1}",
            "career_definition": "distinct seasons on a roster with an active status (ACT/RES/DEV/PUP/SUS/RSN/NWT); "
                                 "CUT-only seasons excluded",
            "populations": "career_length reports all_players, drafted and undrafted; the ~3-year published "
                           "figure is the all_players mean, driven by undrafted churn; drafted players average ~6",
            "trade_split": "in_season = first game to last game of the season; offseason = March free agency, "
                           "the draft, and the September cutdown week before Week 1",
            "decisions": {
                "trade_split": "JR, 2026-11-08: the cutdown week counts as preseason, so in-season is Week 1 "
                               "through season end and the gate is 5-21 trades a season",
                "career_population": "JR, 2026-11-08: use the drafted cohort (median 5-6 seasons), for career "
                                     "length and retirement age alike. Confirmed by P3-06a (2026-11-12): Mu's "
                                     "2027 inflow on p2c was 247 entrants, 244 rookies, at least 224 drafted "
                                     "and at most 20 undrafted (92% drafted), roster bound 6.87-7.57 seasons, "
                                     "so the drafted cohort stands",
                "tolerances": "JR, 2026-11-08: median +/-1 season (+/-1.5 for QB/K/P); shares +/-10 points; "
                              "rates +/-20% relative",
                "averaging": "JR, 2026-11-08: per-season counts pass on a 3-season mean, and each season must "
                             "also sit inside the band",
                "scope": "JR, 2026-11-08: blockbusters, holdouts and the P3-06a inflow measurement are pre-2026 "
                         "scope; GM and coordinator changes are deferred to a post-2034 pass (no dataset; HC "
                         "changes act as the proxy for owner_hiring)",
                "status": "P0-2 settled; only the section-5 rejected-gate list awaits confirmation",
            },
            "positions": {g: sorted(v) for g, v in GROUPS.items()},
        },
        "career_length": career_length,
        "career_length_reconciliation": cut_only,
        "retirement_age": retirement_age,
        "coach_changes": {"between_seasons": coach_between, "midseason": coach_mid},
        "trades": trades,
        "team_movement": {"rate": movement, "players": movement_players},
        "draft_by_round": draft_by_round,
        "rookie_usage": rookie_usage,
    }


def report_markdown(doc):
    """Human-readable report of a decision-target document."""
    L = ["# Real-NFL decision targets", "",
         f"Source: {doc['meta']['source']}", f"Seasons: {doc['meta']['seasons'][0]}–{doc['meta']['seasons'][-1]}",
         f"Careers: {doc['meta']['career_window']}  ·  {doc['meta']['career_definition']}", ""]

    L += ["## Career length (seasons), by position", "",
          "The quoted '~3 year average career' is the **all players** figure; it is pulled down by undrafted "
          "churn. Drafted players average about twice that. Pick the population that matches who Mu puts on a "
          "roster.", "",
          "| Pos | all players med / mean | drafted med / mean | undrafted med / mean | 10+ (all) |",
          "|---|---:|---:|---:|---:|"]
    for grp in GROUP_ORDER:
        cells = []
        for pop in ("all_players", "drafted", "undrafted"):
            e = doc["career_length"][pop].get(grp) or {}
            cells.append(f"{e['median']:.0f} / {e['mean']:.1f}" if "median" in e else "—")
        allp = doc["career_length"]["all_players"].get(grp) or {}
        share = f"{allp['share_10plus']:.0%}" if "share_10plus" in allp else "—"
        L.append(f"| {grp} | " + " | ".join(cells) + f" | {share} |")

    rec = doc.get("career_length_reconciliation") or {}
    if rec.get("n_active"):
        L += ["", "### Reconciliation with the quoted ~3-year average", "",
              f"Players with at least one active season, careers ending in the window: **{rec['n_active']:,}**, "
              f"mean career **{rec['mean_active_only']}** seasons. Including the **{rec['cut_only']:,}** players "
              f"({rec['share_cut_only']:.0%}) whose only roster appearance was CUT, the mean falls to "
              f"**{rec['mean_including_cut_only']}** — which is the widely quoted figure. The difference is not "
              f"a data error: the quoted number counts everyone who ever signed, including camp bodies."]

    L += ["", "## Retirement age, by position (median; all players / drafted)", "",
          "| Pos | all players n | all median | drafted n | drafted median |",
          "|---|---:|---:|---:|---:|"]
    for grp in GROUP_ORDER:
        a = (doc["retirement_age"].get("all_players") or {}).get(grp) or {}
        d = (doc["retirement_age"].get("drafted") or {}).get(grp) or {}
        if "median" not in a:
            continue
        L.append(f"| {grp} | {a['n']} | {a['median']} | " +
                 (f"{d['n']} | {d['median']} |" if "median" in d else "— | — |"))

    for key, title in (("between_seasons", "Head-coach changes between seasons"),
                       ("midseason", "Teams with a mid-season head-coach change")):
        b = doc["coach_changes"][key]
        L += ["", f"## {title}", "",
              f"Band: **{b['lo']}–{b['hi']}** per season (median {b['median']}, mean {b['mean']})", "",
              "| Season | " + " | ".join(b["by_season"]) + " |",
              "|---|" + "---:|" * len(b["by_season"]),
              "| Count | " + " | ".join(str(v) for v in b["by_season"].values()) + " |"]

    for key, title in (("in_season", "Trades during the season (Week 1 → season end)"),
                       ("offseason", "Trades in the offseason (March, the draft, September cutdown)"),
                       ("with_player", "Trades involving a player, all year"),
                       ("all", "All trades, all year")):
        if key not in doc["trades"]:
            continue
        b = doc["trades"][key]
        L += ["", f"## {title}", "",
              f"Band: **{b['lo']}–{b['hi']}** per season (median {b['median']}, mean {b['mean']})", "",
              "| Season | " + " | ".join(b["by_season"]) + " |",
              "|---|" + "---:|" * len(b["by_season"]),
              "| Count | " + " | ".join(str(v) for v in b["by_season"].values()) + " |"]

    b = doc["team_movement"]["rate"]
    L += ["", "## Players changing teams (≥8 games both seasons)", "",
          f"Rate band: **{b['lo']:.1%}–{b['hi']:.1%}** (median {b['median']:.1%}); "
          f"about **{doc['team_movement']['players']['median']:.0f}** players a season", "",
          "| Season | " + " | ".join(b["by_season"]) + " |",
          "|---|" + "---:|" * len(b["by_season"]),
          "| Rate | " + " | ".join(f"{v:.1%}" for v in b["by_season"].values()) + " |"]

    L += ["", "## Draft by round", "",
          "| Round | n | median games | median wAV | share with 2+ started seasons |",
          "|---:|---:|---:|---:|---:|"]
    for r, e in doc["draft_by_round"].items():
        L.append(f"| {r} | {e['n']} | {e.get('games_median', '—')} | {e.get('w_av_median', '—')} | "
                 f"{e['share_started_2plus']:.0%} |")

    ru = doc.get("rookie_usage") or {}
    if ru.get("n"):
        L += ["", "## Rookie usage (first season, skill positions with recorded stats)", "",
              f"n={ru['n']}  ·  median games {ru['median_games']}  ·  "
              f"share with 8+ games {ru['share_8plus']:.0%}  ·  12+ games {ru['share_12plus']:.0%}"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m engines.tuning.decision_targets")
    ap.add_argument("--seasons", default=f"{DEFAULT_SEASONS[0]}-{DEFAULT_SEASONS[-1]}",
                    help="inclusive range, e.g. 2015-2025")
    ap.add_argument("--refresh", action="store_true", help="download the nflverse files again")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "decision_targets.json"))
    ap.add_argument("--report", default=os.path.join(OUT_DIR, "decision_targets.md"))
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seasons.split("-"))
    doc = build_targets(range(lo, hi + 1), a.refresh)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    with open(a.report, "w", encoding="utf-8") as fh:
        fh.write(report_markdown(doc))
    print(f"wrote {a.out}\nwrote {a.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
