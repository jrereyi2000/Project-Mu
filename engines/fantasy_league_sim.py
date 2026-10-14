"""Simulated fantasy leagues → ADP, rostered % and start % (docs/FANTASY_EDITORIAL_MATRIX.md §5, PROJECT_SPEC §3.4).

There are no real fantasy leagues, so LEAGUES (100) 12-team PPR leagues are simulated each season:

- Draft (once per season, stored): 15-round snake drafts from the preseason value board (draft_score from
  engines/fantasy_adp_engine.py). Each manager sees the board with a little personal noise and ranks players by
  value over replacement at their position (QB and TE value damped), then a drafting strategy (BPA, Hero RB,
  Zero RB, RB Heavy, Early QB, Early TE) and roster needs adjust it. Strategies are dealt seat by seat from the
  season's strategy mix. The 100 drafts give each player's ADP (undrafted = the pick after the last round),
  earliest/latest pick and draft %.
- Season (re-simulated from the stored draft on every run, weeks 1..17): lineups start the highest-projected
  eligible players (fantasy_projections; week 1 by ADP), scores are actual PPR, 12-team round robin in weeks
  1–14, 6-team playoffs (seeds 1–2 bye in week 15, semifinals week 16, final week 17). After each week,
  managers add rising players and drop falling ones (waiver priority = reverse standings) and stream past byes.
  Each player's rostered % and start % per week = share of the leagues where he is on a roster / in a lineup.
- Strategy mix: the first season uses DEFAULT_MIX. Each later season moves partway toward the strategies that
  won titles the season before (adapt_mix). Results feed the mix only; they are not published.

Randomness is seeded per season (draft) and per (season, league, week) (season), so re-running gives the same
result for the same data.

CLI:
    python3 -m engines.fantasy_league_sim                      # season from league_state, through the last completed week
    python3 -m engines.fantasy_league_sim --season 2035
    python3 -m engines.fantasy_league_sim --season 2035 --summary   # print results, no writes
The draft itself runs from engines/fantasy_adp_engine.py (it writes the week 1 ADP board).
"""

import argparse
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import psycopg

from db.db_handler import DatabaseHandler

LEAGUES = 100
TEAMS = 12
ROUNDS = 15
UNDRAFTED_PICK = TEAMS * ROUNDS + 1           # ADP counts an undrafted player as the pick after the last round
REGULAR_WEEKS = 14
LAST_FANTASY_WEEK = 17
SEASON_WEEKS = 18

GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}
LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}  # plus one FLEX (RB/WR/TE)
FLEX = ("RB", "WR", "TE")

STRATEGIES = ("BPA", "HERO_RB", "ZERO_RB", "RB_HEAVY", "EARLY_QB", "EARLY_TE")
DEFAULT_MIX = {"BPA": 0.35, "HERO_RB": 0.15, "ZERO_RB": 0.15, "RB_HEAVY": 0.15, "EARLY_QB": 0.10, "EARLY_TE": 0.10}
MIX_EXPONENT = 0.5    # damping on lift (title share ÷ manager share)
MIX_BLEND = 0.5       # weight kept on last season's mix
MIX_FLOOR = 0.05      # no strategy drops below 5%

# Draft valuation: value = POS_WEIGHT × (noisy draft_score − replacement level at the position).
REPLACEMENT_RANK = {"QB": 12, "RB": 30, "WR": 30, "TE": 12}   # per 12 teams
POS_WEIGHT = {"QB": 0.4, "RB": 1.0, "WR": 1.0, "TE": 0.8}
BOARD_NOISE = 0.012   # sd of each manager's personal read of a player's draft_score (relative)
DRAFT_CAP = {"QB": 2, "RB": 7, "WR": 7, "TE": 2}
SEASON_CAP = {"QB": 3, "RB": 8, "WR": 8, "TE": 3}
BACKUP_PENALTY = {"QB": 6.0, "TE": 10.0}  # value points off per QB/TE already on the roster
SWAP_MARGIN = (1.15, 1.0)  # add a free agent when his value > drop value × 1.15 + 1.0


# ─────────────────────────────── strategy mix ───────────────────────────────

def adapt_mix(prev_share: dict, titles: dict, exponent: float = MIX_EXPONENT, blend: float = MIX_BLEND,
              floor: float = MIX_FLOOR) -> dict:
    """Next season's mix: move partway toward strategies that won more titles than their share of managers."""
    total = sum(titles.get(s, 0) for s in STRATEGIES)
    prev = {s: prev_share.get(s, 0.0) for s in STRATEGIES}
    if not total:
        return _apply_floor(prev, floor)
    raw = {}
    for s in STRATEGIES:
        lift = (titles.get(s, 0) / total) / prev[s] if prev[s] > 0 else 1.0
        raw[s] = prev[s] * lift ** exponent
    norm = sum(raw.values()) or 1.0
    mixed = {s: blend * prev[s] + (1 - blend) * raw[s] / norm for s in STRATEGIES}
    return _apply_floor(mixed, floor)


def _apply_floor(mix: dict, floor: float) -> dict:
    total = sum(mix.values())
    mix = {s: v / total for s, v in mix.items()}
    fixed = set()
    while True:
        low = {s for s, v in mix.items() if v < floor and s not in fixed}
        if not low:
            return mix
        fixed |= low
        free = [s for s in mix if s not in fixed]
        rest = 1.0 - floor * len(fixed)
        free_total = sum(mix[s] for s in free)
        mix = {s: (floor if s in fixed else mix[s] / free_total * rest) for s in mix}


def assign_strategies(rng: random.Random, mix: dict, n: int = TEAMS) -> list:
    """One strategy per seat, drawn independently from the mix (so leagues differ)."""
    names = list(STRATEGIES)
    weights = [mix.get(s, 0.0) for s in names]
    return rng.choices(names, weights=weights, k=n)


# ─────────────────────────────────── draft ───────────────────────────────────

def replacement_levels(board: list, teams: int = TEAMS) -> dict:
    by_group = defaultdict(list)
    for p in board:
        by_group[GROUP[p["position"]]].append(p["draft_score"])
    levels = {}
    for g, scores in by_group.items():
        scores.sort(reverse=True)
        idx = min(len(scores), round(REPLACEMENT_RANK[g] * teams / 12)) - 1
        levels[g] = scores[max(idx, 0)]
    return levels


def strategy_adjustment(strategy: str, group: str, rnd: int, counts: Counter) -> float:
    """Value points a strategy adds or removes for a position in a round, given the roster so far."""
    rb = counts["RB"]
    if strategy == "HERO_RB" and group == "RB":
        if rnd <= 2 and rb == 0:
            return 4.0
        if rb >= 1 and rnd <= 5:
            return -10.0
    elif strategy == "ZERO_RB" and group == "RB":
        if rnd <= 4:
            return -12.0
        if rnd <= 7:
            return 2.0
    elif strategy == "RB_HEAVY" and group == "RB" and rnd <= 4:
        if rb < 2:
            return 5.0
        if rb == 2:
            return 2.0
    elif strategy == "EARLY_QB" and group == "QB" and rnd <= 3 and counts["QB"] == 0:
        return 8.0
    elif strategy == "EARLY_TE" and group == "TE" and rnd <= 3 and counts["TE"] == 0:
        return 6.0
    return 0.0


def roster_adjustment(group: str, counts: Counter) -> float:
    """Backups are worth less: a second QB/TE is a bench spot, and RB/WR depth past five fades."""
    c = counts[group]
    if group in BACKUP_PENALTY:
        return -BACKUP_PENALTY[group] * c
    return -1.5 * max(0, c - 4)


def draft_league(rng: random.Random, board: list, strategies: list, levels: dict) -> list:
    """Snake draft for one league. Returns [(overall_pick, round, team, player_id)], teams numbered 1..TEAMS."""
    n_teams = len(strategies)
    # Each manager's personal board, per position group, best first.
    boards = []
    for _ in range(n_teams):
        per_group = defaultdict(list)
        for p in board:
            g = GROUP[p["position"]]
            seen = p["draft_score"] * (1.0 + rng.gauss(0.0, BOARD_NOISE))
            per_group[g].append((POS_WEIGHT[g] * (seen - levels[g]), p["player_id"]))
        for lst in per_group.values():
            lst.sort(key=lambda t: (-t[0], t[1]))
        boards.append(per_group)
    cursor = [defaultdict(int) for _ in range(n_teams)]
    counts = [Counter() for _ in range(n_teams)]
    taken = set()
    picks = []

    for i in range(n_teams * ROUNDS):
        rnd, idx = divmod(i, n_teams)
        rnd += 1
        team = idx if rnd % 2 == 1 else n_teams - 1 - idx
        c = counts[team]
        need = {g: max(0, LINEUP[g] - c[g]) for g in LINEUP}
        picks_left = ROUNDS - sum(c.values())
        forced = sum(need.values()) >= picks_left
        best = None
        for g, lst in boards[team].items():
            if c[g] >= DRAFT_CAP[g] or (forced and need[g] == 0):
                continue
            k = cursor[team][g]
            while k < len(lst) and lst[k][1] in taken:
                k += 1
            cursor[team][g] = k
            if k == len(lst):
                continue
            base, pid = lst[k]
            value = base + strategy_adjustment(strategies[team], g, rnd, c) + roster_adjustment(g, c)
            if best is None or (value, -pid) > (best[0], -best[1]):
                best = (value, pid, g)
        _, pid, g = best
        taken.add(pid)
        c[g] += 1
        picks.append((i + 1, rnd, team + 1, pid))
    return picks


def run_drafts(season: int, board: list, mix: dict, leagues: int = LEAGUES) -> tuple:
    """All leagues' drafts. Returns (picks [(league, pick, round, team, pid)], teams [dict per league seat])."""
    rng = random.Random(f"mu-fantasy-draft-{season}")
    levels = replacement_levels(board)
    all_picks, teams = [], []
    for league in range(1, leagues + 1):
        strategies = assign_strategies(rng, mix)
        for t, s in enumerate(strategies, start=1):
            teams.append({"league": league, "team": t, "strategy": s,
                          "activity": round(rng.uniform(0.2, 1.0), 3),
                          "reactivity": round(rng.uniform(0.0, 0.5), 3)})
        for pick, rnd, team, pid in draft_league(rng, board, strategies, levels):
            all_picks.append((league, pick, rnd, team, pid))
    return all_picks, teams


def compute_adp(picks: list, player_ids: list, leagues: int = LEAGUES) -> dict:
    """player_id -> {adp, min_pick, max_pick, draft_pct}; undrafted counts as UNDRAFTED_PICK."""
    by_player = defaultdict(list)
    for _, pick, _, _, pid in picks:
        by_player[pid].append(pick)
    out = {}
    for pid in player_ids:
        drafted = by_player.get(pid, [])
        total = sum(drafted) + UNDRAFTED_PICK * (leagues - len(drafted))
        out[pid] = {
            "adp": round(total / leagues, 1),
            "min_pick": min(drafted) if drafted else None,
            "max_pick": max(drafted) if drafted else None,
            "draft_pct": round(100.0 * len(drafted) / leagues, 1),
        }
    return out


# ─────────────────────────────────── season ──────────────────────────────────

def round_robin(n_teams: int, weeks: int, rng: random.Random) -> list:
    """Week-by-week pairings [(a, b), ...] (teams 1..n). Circle method; rounds repeat after n−1 weeks."""
    order = list(range(1, n_teams + 1))
    rng.shuffle(order)
    rounds = []
    for _ in range(n_teams - 1):
        rounds.append([(order[i], order[n_teams - 1 - i]) for i in range(n_teams // 2)])
        order = [order[0]] + [order[-1]] + order[1:-1]
    return [rounds[w % len(rounds)] for w in range(weeks)]


def set_lineup(roster: list, value: dict, group_of: dict) -> list:
    """Best lineup by value: QB, 2 RB, 2 WR, TE, then FLEX from the remaining RB/WR/TE."""
    ranked = sorted(roster, key=lambda pid: (-value.get(pid, 0.0), pid))
    starters, used = [], set()
    for g, n in LINEUP.items():
        for pid in [p for p in ranked if group_of.get(p) == g][:n]:
            starters.append(pid)
            used.add(pid)
    flex = [p for p in ranked if p not in used and group_of.get(p) in FLEX]
    if flex:
        starters.append(flex[0])
    return starters


def seed_teams(records: dict) -> list:
    """Teams ordered best first: wins, then points for (ties count half a win)."""
    return sorted(records, key=lambda t: (-(records[t]["w"] + 0.5 * records[t]["t"]), -records[t]["pf"], t))


def playoff_round(pairs: list, scores: dict, seed_of: dict) -> tuple:
    """Winners and losers of a playoff round; ties go to the higher seed."""
    winners, losers = [], []
    for a, b in pairs:
        sa, sb = scores.get(a, 0.0), scores.get(b, 0.0)
        if sa > sb or (sa == sb and seed_of[a] < seed_of[b]):
            winners.append(a); losers.append(b)
        else:
            winners.append(b); losers.append(a)
    return winners, losers


@dataclass
class WeekData:
    """What the simulation reads for one season."""
    group_of: dict                     # player_id -> QB/RB/WR/TE (rostered skill players)
    team_of: dict                      # player_id -> NFL team_id
    points: dict                       # player_id -> {week: PPR}
    opponent: dict                     # (team_id, week) -> opponent (None = bye)
    proj: dict                         # week -> {player_id: (projected, ros_total, avg)}
    adp: dict                          # player_id -> ADP (week 1 lineups)
    last_complete: int                 # last week whose games are all final (0 = none)
    _signals: dict = field(default_factory=dict)

    def proj_at(self, week: int) -> dict:
        """Projection rows for `week`, or the latest earlier projected week if it hasn't been projected yet."""
        for w in range(min(week, SEASON_WEEKS), 1, -1):
            if w in self.proj:
                return self.proj[w]
        return {}

    def plays(self, pid: int, week: int) -> bool:
        return (self.team_of.get(pid), week) in self.opponent


@dataclass
class LeagueState:
    rosters: dict                      # team -> list of player_ids
    managers: dict                     # team -> {strategy, activity, reactivity}
    records: dict = field(default_factory=dict)
    seeds: dict = field(default_factory=dict)
    finish: dict = field(default_factory=dict)


def lineup_values(data: WeekData, week: int) -> Optional[dict]:
    if week == 1:
        return {pid: UNDRAFTED_PICK + 1 - a for pid, a in data.adp.items()}
    if week not in data.proj:
        return None
    return {pid: row[0] for pid, row in data.proj[week].items()}


def week_signals(data: WeekData, week: int) -> dict:
    """player_id -> (base, hot) before `week`: base = projection over the fantasy weeks left (playoffs: the next
    game × weeks left), hot = last week's box score against his per-game average (−0.5..1). Cached."""
    if week not in data._signals:
        after = week - 1
        out = {}
        for pid, (projected, ros_total, avg) in data.proj_at(week).items():
            if week > REGULAR_WEEKS:
                base = projected * (LAST_FANTASY_WEEK - week + 1)
            else:
                base = ros_total * (LAST_FANTASY_WEEK - week + 1) / (SEASON_WEEKS - week + 1)
            last = data.points.get(pid, {}).get(after) if data.plays(pid, after) else None
            hot = 0.0 if last is None or avg is None else max(-0.5, min(1.0, (last - avg) / (avg + 5.0)))
            out[pid] = (base, hot)
        data._signals[week] = out
    return data._signals[week]


def player_value(signals: dict, pid: int, reactivity: float) -> float:
    """A manager's read of a player: the projection, nudged toward last week's box score by his reactivity."""
    base, hot = signals.get(pid, (0.0, 0.0))
    return base * (1.0 + reactivity * hot)


def _droppable(roster: list, group_of: dict, keep_group: Optional[str] = None) -> list:
    counts = Counter(group_of.get(p) for p in roster)
    out = []
    for p in roster:
        g = group_of.get(p)
        if g == keep_group:
            continue
        if g in LINEUP and counts[g] - 1 < LINEUP[g]:
            continue
        out.append(p)
    return out


def run_waivers(state: LeagueState, data: WeekData, week: int, rng: random.Random) -> None:
    """Adds/drops before `week`, after week−1's games. Priority: worst record first."""
    signals = week_signals(data, week)
    rostered = {p for r in state.rosters.values() for p in r}
    free = [p for p in data.group_of if p not in rostered]
    order = list(reversed(seed_teams(state.records))) if state.records else sorted(state.rosters, reverse=True)
    for team in order:
        m = state.managers[team]
        roster = state.rosters[team]
        moves = sum(rng.random() < m["activity"] for _ in range(2))
        for _ in range(moves):
            counts = Counter(data.group_of.get(p) for p in roster)
            fa = [p for p in free if counts[data.group_of[p]] < SEASON_CAP[data.group_of[p]]]
            drops = _droppable(roster, data.group_of)
            if not fa or not drops:
                break
            val = {p: player_value(signals, p, m["reactivity"]) for p in fa + drops}
            add = max(fa, key=lambda p: (val[p], -p))
            drop = min(drops, key=lambda p: (val[p], p))
            if val[add] <= val[drop] * SWAP_MARGIN[0] + SWAP_MARGIN[1]:
                break
            roster.remove(drop); roster.append(add)
            free.remove(add); free.append(drop)

        # Bye-week streaming: fill a starting spot nobody on the roster can play this week.
        if rng.random() < min(1.0, m["activity"] + 0.5):
            for g, n in LINEUP.items():
                playing = [p for p in roster if data.group_of.get(p) == g and data.plays(p, week)]
                if len(playing) >= n:
                    continue
                fa = [p for p in free if data.group_of[p] == g and data.plays(p, week)]
                drops = _droppable(roster, data.group_of, keep_group=g)
                if not fa or not drops:
                    continue
                proj = data.proj_at(week)
                add = max(fa, key=lambda p: (proj.get(p, (0.0,))[0], -p))
                drop = min(drops, key=lambda p: (player_value(signals, p, m["reactivity"]), p))
                roster.remove(drop); roster.append(add)
                free.remove(add); free.append(drop)


def simulate_league(league: int, season: int, state: LeagueState, data: WeekData, through: int,
                    usage: dict) -> None:
    """Play weeks 1..through (games scored only up to data.last_complete); record roster/start usage."""
    schedule = round_robin(TEAMS, REGULAR_WEEKS, random.Random(f"mu-fantasy-schedule-{season}-{league}"))
    state.records = {t: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0} for t in state.rosters}
    alive = []
    for week in range(1, through + 1):
        rng = random.Random(f"mu-fantasy-season-{season}-{league}-{week}")
        if week > 1:
            run_waivers(state, data, week, rng)
        for roster in state.rosters.values():
            for pid in roster:
                usage[(week, pid)][0] += 1
        values = lineup_values(data, week)
        if values is None:
            continue
        lineups = {t: set_lineup(r, values, data.group_of) for t, r in state.rosters.items()}
        for lineup in lineups.values():
            for pid in lineup:
                usage[(week, pid)][1] += 1
        if week > data.last_complete:
            continue
        scores = {t: sum(data.points.get(p, {}).get(week, 0.0) for p in lu) for t, lu in lineups.items()}

        if week <= REGULAR_WEEKS:
            for a, b in schedule[week - 1]:
                ra, rb = state.records[a], state.records[b]
                ra["pf"] += scores[a]; ra["pa"] += scores[b]
                rb["pf"] += scores[b]; rb["pa"] += scores[a]
                if scores[a] > scores[b]:
                    ra["w"] += 1; rb["l"] += 1
                elif scores[b] > scores[a]:
                    rb["w"] += 1; ra["l"] += 1
                else:
                    ra["t"] += 1; rb["t"] += 1
            if week == REGULAR_WEEKS:
                ordered = seed_teams(state.records)
                state.seeds = {t: i for i, t in enumerate(ordered, start=1)}
                for t in ordered[6:]:
                    state.finish[t] = "MISSED"
        elif week == 15:
            by_seed = {s: t for t, s in state.seeds.items()}
            winners, losers = playoff_round([(by_seed[3], by_seed[6]), (by_seed[4], by_seed[5])], scores,
                                            state.seeds)
            for t in losers:
                state.finish[t] = "QUARTERFINAL"
            alive = [by_seed[1], by_seed[2]] + winners
        elif week == 16:
            top, second, *rest = alive
            low, high = sorted(rest, key=lambda t: state.seeds[t], reverse=True)
            winners, losers = playoff_round([(top, low), (second, high)], scores, state.seeds)
            for t in losers:
                state.finish[t] = "SEMIFINAL"
            alive = winners
        elif week == 17:
            winners, losers = playoff_round([tuple(alive)], scores, state.seeds)
            state.finish[winners[0]] = "CHAMPION"
            state.finish[losers[0]] = "RUNNER_UP"


def final_ranks(state: LeagueState) -> dict:
    """1 champion, 2 runner-up, 3–4 semifinal losers, 5–6 quarterfinal losers, 7–12 by standings."""
    if not state.seeds:
        return {}
    tier = {"CHAMPION": 0, "RUNNER_UP": 1, "SEMIFINAL": 2, "QUARTERFINAL": 3, "MISSED": 4}
    if any(t not in state.finish for t in state.seeds):
        return {}
    ordered = sorted(state.seeds, key=lambda t: (tier[state.finish[t]], state.seeds[t]))
    return {t: i for i, t in enumerate(ordered, start=1)}


# ──────────────────────────────── database I/O ───────────────────────────────

def season_mix(cur, season: int) -> dict:
    """The season's strategy mix: adapted from last season's results, else DEFAULT_MIX."""
    cur.execute("""
        SELECT strategy, managers, titles FROM fantasy_sim_strategy_mix
        WHERE season = %s AND titles IS NOT NULL
    """, (season - 1,))
    rows = cur.fetchall()
    total = sum(r[1] or 0 for r in rows)
    if not rows or not total:
        return dict(DEFAULT_MIX)
    share = {s: (m or 0) / total for s, m, _ in rows}
    return adapt_mix(share, {s: t or 0 for s, _, t in rows})


def ensure_draft(conn, season: int, board: list, redraft: bool = False) -> dict:
    """Run and store the season's drafts unless they exist. Returns ADP for every board player."""
    ids = [p["player_id"] for p in board]
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fantasy_sim_picks WHERE season = %s", (season,))
        exists = cur.fetchone()[0] > 0
        if exists and not redraft:
            cur.execute("SELECT player_id, adp, min_pick, max_pick, draft_pct FROM fantasy_sim_adp WHERE season = %s",
                        (season,))
            stored = {r[0]: {"adp": float(r[1]), "min_pick": r[2], "max_pick": r[3], "draft_pct": float(r[4])}
                      for r in cur.fetchall()}
            print(f"   Using the stored {season} simulated draft (--redraft to replace it).")
            return {pid: stored.get(pid, {"adp": float(UNDRAFTED_PICK), "min_pick": None, "max_pick": None,
                                          "draft_pct": 0.0}) for pid in ids}

        mix = season_mix(cur, season)
        picks, teams = run_drafts(season, board, mix)
        adp = compute_adp(picks, ids)
        for table in ("fantasy_sim_player_weeks", "fantasy_sim_picks", "fantasy_sim_teams", "fantasy_sim_adp",
                      "fantasy_sim_strategy_mix"):
            cur.execute(f"DELETE FROM {table} WHERE season = %s", (season,))
        counts = Counter(t["strategy"] for t in teams)
        cur.executemany("""
            INSERT INTO fantasy_sim_strategy_mix (season, strategy, share, managers) VALUES (%s, %s, %s, %s)
        """, [(season, s, round(mix[s], 4), counts.get(s, 0)) for s in STRATEGIES])
        cur.executemany("""
            INSERT INTO fantasy_sim_teams (season, league, team, strategy, activity, reactivity)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [(season, t["league"], t["team"], t["strategy"], t["activity"], t["reactivity"]) for t in teams])
        cur.executemany("""
            INSERT INTO fantasy_sim_picks (season, league, pick, round, team, player_id) VALUES (%s, %s, %s, %s, %s, %s)
        """, [(season, *p) for p in picks])
        cur.executemany("""
            INSERT INTO fantasy_sim_adp (season, player_id, adp, min_pick, max_pick, draft_pct)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [(season, pid, a["adp"], a["min_pick"], a["max_pick"], a["draft_pct"]) for pid, a in adp.items()])
    conn.commit()
    mix_txt = ", ".join(f"{s} {mix[s]:.0%}" for s in STRATEGIES)
    print(f"   Simulated {LEAGUES} drafts for {season} ({mix_txt}).")
    return adp


def load_week_data(cur, season: int) -> WeekData:
    from engines.fantasy_projection_engine import load_season

    sd = load_season(cur, season)
    group_of = {pid: p["group"] for pid, p in sd.roster.items()}
    team_of = {pid: p["team_id"] for pid, p in sd.roster.items()}
    cur.execute("""
        SELECT week, player_id, projected_points::float, ros_total_points::float, avg_points::float
        FROM fantasy_projections
        WHERE season = %s AND week BETWEEN 2 AND %s AND player_id IS NOT NULL
    """, (season, SEASON_WEEKS))
    proj = defaultdict(dict)
    for week, pid, p, ros, avg in cur.fetchall():
        proj[week][pid] = (p or 0.0, ros or 0.0, avg)
    cur.execute("SELECT player_id, adp::float FROM fantasy_sim_adp WHERE season = %s", (season,))
    adp = dict(cur.fetchall())
    cur.execute("""
        SELECT week FROM games WHERE season = %s AND week <= %s
        GROUP BY week HAVING BOOL_AND(status = 'COMPLETED') ORDER BY week
    """, (season, LAST_FANTASY_WEEK))
    last = 0
    for (week,) in cur.fetchall():
        if week == last + 1:
            last = week
    return WeekData(group_of, team_of, dict(sd.points), sd.opponent, dict(proj), adp, last)


def load_leagues(cur, season: int) -> dict:
    cur.execute("SELECT league, team, strategy, activity::float, reactivity::float FROM fantasy_sim_teams WHERE season = %s",
                (season,))
    leagues = defaultdict(lambda: LeagueState(rosters={}, managers={}))
    for league, team, strategy, activity, reactivity in cur.fetchall():
        st = leagues[league]
        st.managers[team] = {"strategy": strategy, "activity": activity, "reactivity": reactivity}
        st.rosters[team] = []
    cur.execute("SELECT league, team, player_id FROM fantasy_sim_picks WHERE season = %s ORDER BY league, pick",
                (season,))
    for league, team, pid in cur.fetchall():
        leagues[league].rosters[team].append(pid)
    return dict(leagues)


def simulate_season(season: int, conn_info: Optional[str] = None, write: bool = True) -> Optional[dict]:
    """Re-simulate every league from its stored draft through the next unplayed fantasy week."""
    with psycopg.connect(conn_info or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            leagues = load_leagues(cur, season)
            if not leagues:
                print(f"   No simulated draft for {season}; run python3 -m engines.fantasy_adp_engine first.")
                return None
            data = load_week_data(cur, season)
        through = min(data.last_complete + 1, LAST_FANTASY_WEEK)
        usage = defaultdict(lambda: [0, 0])
        for league, state in sorted(leagues.items()):
            simulate_league(league, season, state, data, through, usage)
        n = len(leagues)
        weeks_with_lineups = {w for (w, _), (_, s) in usage.items() if s}

        team_rows, ranks = [], {}
        for league, state in leagues.items():
            ranks[league] = final_ranks(state)
            for team, r in state.records.items():
                team_rows.append((r["w"], r["l"], r["t"], round(r["pf"], 2), round(r["pa"], 2),
                                  state.seeds.get(team), state.finish.get(team), ranks[league].get(team),
                                  season, league, team))
        complete = data.last_complete >= LAST_FANTASY_WEEK
        results = None
        if complete:
            results = defaultdict(lambda: {"managers": 0, "titles": 0, "playoffs": 0, "pf": 0.0})
            for state in leagues.values():
                for team, m in state.managers.items():
                    r = results[m["strategy"]]
                    r["managers"] += 1
                    r["titles"] += state.finish.get(team) == "CHAMPION"
                    r["playoffs"] += state.seeds.get(team, 99) <= 6
                    r["pf"] += state.records[team]["pf"]

        if write:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM fantasy_sim_player_weeks WHERE season = %s", (season,))
                cur.executemany("""
                    INSERT INTO fantasy_sim_player_weeks (season, week, player_id, rostered_pct, started_pct)
                    VALUES (%s, %s, %s, %s, %s)
                """, [(season, w, pid, round(100.0 * r / n, 1), round(100.0 * s / n, 1) if w in weeks_with_lineups else None)
                      for (w, pid), (r, s) in usage.items()])
                cur.executemany("""
                    UPDATE fantasy_sim_teams SET wins = %s, losses = %s, ties = %s, points_for = %s,
                        points_against = %s, seed = %s, playoff_finish = %s, final_rank = %s
                    WHERE season = %s AND league = %s AND team = %s
                """, team_rows)
                if results:
                    cur.executemany("""
                        UPDATE fantasy_sim_strategy_mix SET titles = %s, playoff_teams = %s, avg_points_for = %s
                        WHERE season = %s AND strategy = %s
                    """, [(r["titles"], r["playoffs"], round(r["pf"] / r["managers"], 2) if r["managers"] else None,
                           season, s) for s, r in results.items()])
            conn.commit()

    print(f"   Simulated {n} leagues for {season} through week {through} "
          f"(games scored through week {data.last_complete}).")
    if results:
        for s in STRATEGIES:
            r = results.get(s)
            if r and r["managers"]:
                print(f"     {s:<9} managers {r['managers']:>4}  titles {r['titles']:>3}  "
                      f"playoffs {r['playoffs']:>3}  avg PF {r['pf'] / r['managers']:.1f}")
    return {"through": through, "last_complete": data.last_complete, "results": results}


def main():
    parser = argparse.ArgumentParser(description="Simulated fantasy leagues: rostered %, start %, standings")
    parser.add_argument("--season", type=int)
    parser.add_argument("--summary", action="store_true", help="Simulate and print results without writing")
    args = parser.parse_args()
    season = args.season
    if season is None:
        with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
            season = conn.execute("SELECT current_season FROM league_state LIMIT 1").fetchone()[0]
    simulate_season(season, write=not args.summary)


if __name__ == "__main__":
    main()
