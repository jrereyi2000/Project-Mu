"""Editorial Matrix slot targeting (docs/EDITORIAL_MATRIX.md, "Slot Targeting Rules").

Chooses the subject (team, and game where relevant) for every Editorial Matrix slot that the
matrix pins to a specific subject, before any writer is assigned. All targets for a week are
resolved together so no team is the subject of two targeted slots in the same week.

The selection rules are pure functions over a LeagueSnapshot so they can be tested without a
database; load_league_snapshot() is the thin DB loader.
"""

from dataclasses import dataclass, field

import psycopg

from engines.gotw_engine import display_excitement

PRIMER = "LOCAL_MATCHUP_PRIMER"
STATE_OF_THE_UNION = "FRANCHISE_STATE_OF_THE_UNION"
PLAYOFF_AUTOPSY = "PLAYOFF_AUTOPSY"
HOT_SEAT_AUDIT = "COACHING_HOT_SEAT_AUDIT"

REGULAR_SEASON_FINAL_WEEK = 18
WILD_CARD_WEEK = 19
SUPER_BOWL_WEEK = 23
PHASE_4_WEEKS = range(15, 19)
PLAYOFF_TEAMS_PER_CONFERENCE = 7
PRIMER_ROTATION_WEEKS = 2
# Week 19 hot-seat audit: wins - expected wins below this marks a coach who lost games the roster should have won.
HOT_SEAT_WINS_ABOVE_EXPECTED = -1.5

# Editorial stakes added to a game's (projected) raw Excitement Index when picking marquee games.
# These are editorial weights, not part of the Excitement Index itself (which has no standings input).
DIVISION_GAME_BONUS = 3.0
PLAYOFF_RACE_BONUS = 2.0   # per team not yet mathematically eliminated (regular season only)


@dataclass
class LeagueSnapshot:
    season: int
    week: int
    # team_id -> {"name", "conference", "division", "wins", "losses", "popularity", "expected_wins"}
    # ("expected_wins" from Opponent-Adjusted Game Credit, None until the engine has run)
    teams: dict = field(default_factory=dict)
    # Non-bye games for `week`: {"game_id", "home_team_id", "away_team_id", "excitement", "projected"}.
    # "excitement" is the raw stored E for completed games, else the raw projected E (None if not projected yet).
    week_games: list = field(default_factory=list)
    # Completed games from `week - 1`: {"game_id", "home_team_id", "away_team_id", "home_score", "away_score"}
    prior_week_results: list = field(default_factory=list)
    # team_id -> regular-season games still to play (weeks `week`..18)
    remaining_regular_season_games: dict = field(default_factory=dict)
    # Earlier LOCAL_MATCHUP_PRIMER coverage this season: [(team_id, week), ...]
    primer_history: list = field(default_factory=list)
    teams_with_beat_reporters: set = field(default_factory=set)
    # Optional team stats / analytics mappings:
    expected_wins: dict = field(default_factory=dict)
    losing_streaks: dict = field(default_factory=dict)
    # team_id -> head coach the coach hot seat fired after this season (engines/coach_hot_seat.py; Black Monday)
    coach_firings: dict = field(default_factory=dict)
    # team_id -> ["OC Name", ...]: coordinators fired on Black Monday (engines/coaching_carousel.py)
    coordinator_firings: dict = field(default_factory=dict)


def _popularity(snap: LeagueSnapshot, team_id: int) -> float:
    return snap.teams.get(team_id, {}).get("popularity") or 0.0


def get_team_expected_wins(snap: LeagueSnapshot, team_id: int) -> float | None:
    t = snap.teams.get(team_id, {})
    if "expected_wins" in t and t["expected_wins"] is not None:
        return float(t["expected_wins"])
    if hasattr(snap, "expected_wins") and team_id in snap.expected_wins:
        val = snap.expected_wins[team_id]
        return float(val) if val is not None else None
    return None


def get_team_delta_w(snap: LeagueSnapshot, team_id: int) -> float | None:
    t = snap.teams.get(team_id, {})
    if "delta_w" in t and t["delta_w"] is not None:
        return float(t["delta_w"])
    exp = get_team_expected_wins(snap, team_id)
    if exp is not None:
        wins = t.get("wins") or 0
        return float(wins) - exp
    return None


def get_team_losing_streak(snap: LeagueSnapshot, team_id: int) -> int:
    t = snap.teams.get(team_id, {})
    if "losing_streak" in t and t["losing_streak"] is not None:
        return int(t["losing_streak"])
    if "streak" in t and t["streak"] is not None:
        s = t["streak"]
        if isinstance(s, int):
            return -s if s < 0 else (s if s > 0 and (t.get("losses") or 0) > 0 and (t.get("wins") or 0) == 0 else 0)
        if isinstance(s, str):
            if s.endswith("L"):
                try:
                    return int(s[:-1])
                except ValueError:
                    pass
    if hasattr(snap, "losing_streaks") and team_id in snap.losing_streaks:
        return int(snap.losing_streaks[team_id])
    wins = t.get("wins") or 0
    losses = t.get("losses") or 0
    if wins == 0 and losses > 0:
        return losses
    return 0


def _win_pct(snap: LeagueSnapshot, team_id: int) -> float:
    t = snap.teams.get(team_id, {})
    played = (t.get("wins") or 0) + (t.get("losses") or 0)
    return (t.get("wins") or 0) / played if played else 0.0


def _team_label(snap: LeagueSnapshot, team_id: int) -> str:
    return f"{snap.teams.get(team_id, {}).get('name', 'Team')} (ID: {team_id})"


def _team_target(snap: LeagueSnapshot, kind: str, team_id: int, note: str) -> dict:
    return {
        "kind": kind,
        "team_id": team_id,
        "team_name": snap.teams.get(team_id, {}).get("name"),
        "game_id": None,
        "opponent_team_id": None,
        "opponent_name": None,
        "note": note,
    }


def _game_target(snap: LeagueSnapshot, kind: str, game: dict, team_id: int, note: str) -> dict:
    opponent_id = game["away_team_id"] if team_id == game["home_team_id"] else game["home_team_id"]
    target = _team_target(snap, kind, team_id, note)
    target.update({
        "game_id": game["game_id"],
        "opponent_team_id": opponent_id,
        "opponent_name": snap.teams.get(opponent_id, {}).get("name"),
        "is_home": team_id == game["home_team_id"],
    })
    return target


def _pick_by_popularity(snap: LeagueSnapshot, candidates, used: set, count: int) -> list:
    ranked = sorted((t for t in set(candidates) if t not in used), key=lambda t: (-_popularity(snap, t), t))
    return ranked[:count]


# ---------------------------------------------------------------------------
# Marquee game ranking (excitement + editorial stakes)
# ---------------------------------------------------------------------------

def is_division_game(snap: LeagueSnapshot, game: dict) -> bool:
    home = snap.teams.get(game["home_team_id"], {})
    away = snap.teams.get(game["away_team_id"], {})
    return bool(home.get("division")) and home.get("division") == away.get("division") and home.get("conference") == away.get("conference")


def rank_games_by_interest(snap: LeagueSnapshot) -> list:
    """Week games ordered by editorial interest: raw (projected) Excitement Index + division-game bonus +
    playoff-race bonus per team still alive (regular season). Each returned game gains "interest",
    "division_game" and "teams_in_race". Games without any excitement value rank on stakes alone."""
    alive = set()
    if snap.week <= REGULAR_SEASON_FINAL_WEEK:
        alive = set(snap.teams) - set(mathematically_eliminated_team_ids(snap))
    ranked = []
    for game in snap.week_games:
        division_game = is_division_game(snap, game)
        in_race = sum(1 for t in (game["home_team_id"], game["away_team_id"]) if t in alive)
        interest = (game.get("excitement") or 0.0) + (DIVISION_GAME_BONUS if division_game else 0.0) + PLAYOFF_RACE_BONUS * in_race
        ranked.append({**game, "interest": interest, "division_game": division_game, "teams_in_race": in_race})
    return sorted(ranked, key=lambda g: (-g["interest"], g["game_id"]))


def _excitement_phrase(game: dict) -> str:
    shown = display_excitement(game.get("excitement"))
    if shown is None:
        return "no excitement projection yet"
    return f"{'projected ' if game.get('projected') else ''}Excitement Index {shown:.0f}/100"


# ---------------------------------------------------------------------------
# Matchup primers
# ---------------------------------------------------------------------------

def _primer_targets(snap: LeagueSnapshot, count: int, used: set, used_games: set | None = None) -> list:
    """Top games by editorial interest (rank_games_by_interest); skip teams primed in the last PRIMER_ROTATION_WEEKS weeks
    (regular season only), then cover the side with fewer primers this season (ties: more popular)."""
    season_counts = {}
    recent = set()
    for team_id, wk in snap.primer_history:
        season_counts[team_id] = season_counts.get(team_id, 0) + 1
        if snap.week - PRIMER_ROTATION_WEEKS <= wk < snap.week:
            recent.add(team_id)

    apply_rotation = snap.week <= REGULAR_SEASON_FINAL_WEEK
    games = rank_games_by_interest(snap)

    def eligible_sides(game, respect_rotation):
        sides = []
        for t in (game["home_team_id"], game["away_team_id"]):
            if t in used or t not in snap.teams_with_beat_reporters:
                continue
            if respect_rotation and t in recent:
                continue
            sides.append(t)
        return sides

    targets = []
    used_games_set = set(used_games) if used_games else set()
    # First pass honours rotation; second pass relaxes it if there aren't enough eligible games.
    for respect_rotation in ((True, False) if apply_rotation else (False,)):
        for game in games:
            if len(targets) >= count:
                break
            if game["game_id"] in used_games_set:
                continue
            sides = eligible_sides(game, respect_rotation)
            if not sides:
                continue
            team_id = min(sides, key=lambda t: (season_counts.get(t, 0), -_popularity(snap, t), t != game["home_team_id"]))
            used.add(team_id)
            used_games_set.add(game["game_id"])
            note = (
                f"Preview {_team_label(snap, team_id)} vs. "
                f"{_team_label(snap, game['away_team_id'] if team_id == game['home_team_id'] else game['home_team_id'])} "
                f"(game ID {game['game_id']}, {_excitement_phrase(game)}{', division game' if game['division_game'] else ''}) "
                f"from the {snap.teams.get(team_id, {}).get('name', 'home')} locker room."
            )
            targets.append(_game_target(snap, "MATCHUP_PRIMER", game, team_id, note))
    return targets


def _super_bowl_primer_targets(snap: LeagueSnapshot, used: set) -> list:
    """Super Bowl Week: first primer slot = AFC champion, second = NFC champion."""
    if not snap.week_games:
        return []
    game = rank_games_by_interest(snap)[0]
    by_conf = {snap.teams.get(t, {}).get("conference"): t for t in (game["home_team_id"], game["away_team_id"])}
    targets = []
    for conf in ("AFC", "NFC"):
        team_id = by_conf.get(conf)
        if team_id is None or team_id in used:
            targets.append(None)
            continue
        used.add(team_id)
        targets.append(_game_target(
            snap, "SUPER_BOWL_PRIMER", game, team_id,
            f"Preview the Super Bowl from the locker room of the {conf} champion, {_team_label(snap, team_id)}."
        ))
    return targets


# ---------------------------------------------------------------------------
# Crisis Beat (Regular Season Slot 6, Weeks 2-18)
# ---------------------------------------------------------------------------

def rank_crisis_teams(snap: LeagueSnapshot, candidates: list | None = None) -> list:
    """Ranks eligible crisis teams for Slot 6 (Weeks 2-18).
    Qualifying criteria:
    - Delta W = Wins - Expected Wins <= -2.0 (teams with worst win-differential vs expectation)
    - Active losing streak (>= 1) with sub-.350 win percentage.
    Teams with delta W <= -2.0 are prioritized (ordered by worst delta W), followed by teams
    with losing streaks and sub-.350 win percentages (ordered by longest losing streak).
    """
    if candidates is None:
        candidates = list(snap.teams.keys())

    delta_w_candidates = []
    streak_candidates = []

    for t in candidates:
        delta_w = get_team_delta_w(snap, t)
        losing_streak = get_team_losing_streak(snap, t)
        played = (snap.teams.get(t, {}).get("wins") or 0) + (snap.teams.get(t, {}).get("losses") or 0)
        win_pct = _win_pct(snap, t)

        if delta_w is not None and delta_w <= -2.0:
            delta_w_candidates.append(t)
        elif losing_streak >= 1 and played > 0 and win_pct < 0.350:
            streak_candidates.append(t)

    # Sort delta_w candidates: lowest (worst) delta_w first, then lowest win_pct, then highest popularity
    sorted_delta_w = sorted(
        delta_w_candidates,
        key=lambda t: (get_team_delta_w(snap, t), _win_pct(snap, t), -_popularity(snap, t), t)
    )

    # Sort streak candidates: longest losing streak first, then lowest win_pct, then highest popularity
    sorted_streak = sorted(
        streak_candidates,
        key=lambda t: (-get_team_losing_streak(snap, t), _win_pct(snap, t), -_popularity(snap, t), t)
    )

    return sorted_delta_w + sorted_streak


def _crisis_beat_target(snap: LeagueSnapshot, used: set) -> dict | None:
    """Target for Slot 6 Crisis Beat primer in regular season weeks 2-18."""
    playing_teams = set()
    if snap.week_games:
        playing_teams = {g["home_team_id"] for g in snap.week_games} | {g["away_team_id"] for g in snap.week_games}

    candidates = [
        t for t in snap.teams
        if t not in used
        and (not snap.teams_with_beat_reporters or t in snap.teams_with_beat_reporters)
        and (not playing_teams or t in playing_teams)
    ]
    ranked = rank_crisis_teams(snap, candidates)
    if not ranked:
        return None

    team_id = ranked[0]
    used.add(team_id)

    delta_w = get_team_delta_w(snap, team_id)
    losing_streak = get_team_losing_streak(snap, team_id)
    wins = snap.teams.get(team_id, {}).get("wins") or 0
    losses = snap.teams.get(team_id, {}).get("losses") or 0
    pct = _win_pct(snap, team_id)

    if delta_w is not None and delta_w <= -2.0:
        crisis_reason = f"has underperformed expectation by {abs(delta_w):.1f} wins (delta W = {delta_w:+.1f}, {wins}-{losses})"
    else:
        crisis_reason = f"is mired in a {losing_streak}-game losing streak with a sub-.350 win percentage ({wins}-{losses}, .{int(round(pct * 1000)):03d})"

    game = None
    if snap.week_games:
        for g in snap.week_games:
            if team_id in (g["home_team_id"], g["away_team_id"]):
                game = g
                break

    if game:
        opp_id = game["away_team_id"] if team_id == game["home_team_id"] else game["home_team_id"]
        note = (
            f"Crisis beat / hot seat accountability: {_team_label(snap, team_id)} {crisis_reason}. "
            f"Preview their matchup vs. {_team_label(snap, opp_id)} (game ID {game['game_id']}, {_excitement_phrase(game)}) "
            f"from the {_team_label(snap, team_id)} locker room, interrogating coaching accountability and structural failure."
        )
        return _game_target(snap, "CRISIS_BEAT", game, team_id, note)
    else:
        note = (
            f"Crisis beat / hot seat accountability: {_team_label(snap, team_id)} {crisis_reason}. "
            f"Audit the coaching staff hot seat and structural crisis from the {_team_label(snap, team_id)} locker room."
        )
        return _team_target(snap, "CRISIS_BEAT", team_id, note)


# ---------------------------------------------------------------------------
# Exits, autopsies, and eliminated franchises
# ---------------------------------------------------------------------------

def _missed_playoff_team_ids(snap: LeagueSnapshot) -> list:
    """Week 19: teams playing Wild Card games are in; in each conference the best-record team
    without a Wild Card game is the #1-seed bye. Everyone else missed the playoffs."""
    if not snap.week_games:
        return []
    playing = {t for g in snap.week_games for t in (g["home_team_id"], g["away_team_id"])}
    missed = []
    for conf in ("AFC", "NFC"):
        idle = [t for t, info in snap.teams.items() if info.get("conference") == conf and t not in playing]
        if not idle:
            continue
        bye = max(idle, key=lambda t: (_win_pct(snap, t), _popularity(snap, t)))
        missed.extend(t for t in idle if t != bye)
    return missed


def _prior_week_loser_ids(snap: LeagueSnapshot) -> list:
    losers = []
    for g in snap.prior_week_results:
        if g["home_score"] == g["away_score"]:
            continue
        losers.append(g["away_team_id"] if g["home_score"] > g["away_score"] else g["home_team_id"])
    return losers


def mathematically_eliminated_team_ids(snap: LeagueSnapshot) -> list:
    """Conservative check (ignores tiebreakers, so it never marks a live team as eliminated):
    a team is out if at least PLAYOFF_TEAMS_PER_CONFERENCE other conference teams already have
    more wins than it can still reach."""
    eliminated = []
    for team_id, info in snap.teams.items():
        conf = info.get("conference")
        max_wins = (info.get("wins") or 0) + snap.remaining_regular_season_games.get(team_id, 0)
        ahead = sum(
            1 for other_id, other in snap.teams.items()
            if other_id != team_id and other.get("conference") == conf and (other.get("wins") or 0) > max_wins
        )
        if ahead >= PLAYOFF_TEAMS_PER_CONFERENCE:
            eliminated.append(team_id)
    return eliminated


def _eliminated_targets(snap: LeagueSnapshot, count: int, used: set) -> list:
    eliminated = mathematically_eliminated_team_ids(snap)
    picks = _pick_by_popularity(snap, eliminated, used, count)
    kind = "ELIMINATED_FRANCHISE"
    if len(picks) < count:
        # Nobody (or not enough teams) mathematically out yet: fall back to the worst records.
        remaining = sorted(
            (t for t in snap.teams if t not in used and t not in picks),
            key=lambda t: (_win_pct(snap, t), -_popularity(snap, t), t),
        )
        picks += remaining[: count - len(picks)]
    targets = []
    for team_id in picks:
        used.add(team_id)
        reason = "is mathematically eliminated" if team_id in eliminated else "has the league's worst record (no team is mathematically eliminated yet)"
        targets.append(_team_target(snap, kind, team_id, f"Black Monday prep: {_team_label(snap, team_id)} {reason}."))
    return targets


def wins_above_expected(snap: LeagueSnapshot, team_id: int):
    """Wins - expected wins (regular season), or None when the team has no expected wins yet."""
    t = snap.teams.get(team_id, {})
    if t.get("expected_wins") is None:
        return None
    return (t.get("wins") or 0) - t["expected_wins"]


def hot_seat_team_ids(snap: LeagueSnapshot) -> list:
    """Week 19: teams that missed the playoffs whose coach was fired today (snap.coach_firings) or fell short of
    roster expectation (wins - expected wins < HOT_SEAT_WINS_ABOVE_EXPECTED); firings first, then steepest
    shortfall first (ties: more popular)."""
    shortfalls = {t: wins_above_expected(snap, t) for t in _missed_playoff_team_ids(snap)}
    hot = [t for t, d in shortfalls.items()
           if t in snap.coach_firings or (d is not None and d < HOT_SEAT_WINS_ABOVE_EXPECTED)]
    return sorted(hot, key=lambda t: (t not in snap.coach_firings, shortfalls[t] if shortfalls[t] is not None else 0,
                                      -_popularity(snap, t), t))


def _hot_seat_targets(snap: LeagueSnapshot, count: int, used: set) -> list:
    hot = [t for t in hot_seat_team_ids(snap) if t not in used]

    def shortfall(t):
        t_info = snap.teams[t]
        if t_info.get("expected_wins") is None:
            return f"{t_info.get('wins') or 0} wins"
        return f"{t_info.get('wins') or 0} wins against {t_info['expected_wins']:.1f} expected ({wins_above_expected(snap, t):+.1f})"

    targets = []
    for team_id in hot[:count]:
        used.add(team_id)
        others = [t for t in hot if t != team_id]
        if team_id in snap.coach_firings:
            note = (f"Black Monday: {_team_label(snap, team_id)} missed the playoffs with {shortfall(team_id)} and "
                    f"fired head coach {snap.coach_firings[team_id]} today; that firing leads the Firing Line.")
        else:
            note = (f"Black Monday: {_team_label(snap, team_id)} missed the playoffs with {shortfall(team_id)}, "
                    f"the steepest shortfall against roster expectation among eliminated teams; its head coach leads the Firing Line.")
        if others:
            note += " Other eliminated teams fired today or below " + f"{HOT_SEAT_WINS_ABOVE_EXPECTED:+.1f}: " + "; ".join(
                f"{_team_label(snap, t)}, {shortfall(t)}"
                + (f" (fired {snap.coach_firings[t]} today)" if t in snap.coach_firings else "") for t in others) + "."
        if snap.coordinator_firings:
            note += " Coordinators fired today: " + "; ".join(
                f"{_team_label(snap, t)}: {', '.join(c)}" for t, c in snap.coordinator_firings.items()) + "."
        note += (" Report the firings named here as news; no other head coach or coordinator was fired."
                 if snap.coach_firings or snap.coordinator_firings else " No head coach was fired today.")
        targets.append(_team_target(snap, "HOT_SEAT", team_id, note))
    return targets


def _exit_targets(snap: LeagueSnapshot, kind: str, candidates: list, count: int, used: set, note_fmt: str) -> list:
    targets = []
    for team_id in _pick_by_popularity(snap, candidates, used, count):
        used.add(team_id)
        targets.append(_team_target(snap, kind, team_id, note_fmt.format(team=_team_label(snap, team_id))))
    return targets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_slot_targets(slots: list, snap: LeagueSnapshot) -> list:
    """Returns a list aligned with `slots` ((slot_name, article_type_id, ...) tuples); each entry is a
    target dict or None when the matrix leaves the subject to the writer (or no subject qualifies)."""
    week = snap.week
    used = set()
    used_games = set()
    targets = [None] * len(slots)

    def indices(article_type):
        return [i for i, s in enumerate(slots) if s[1] == article_type]

    # In regular-season weeks (Weeks 2-18), convert Slot 6 into a "Crisis Beat" slot
    slot6_idx = next(
        (i for i, s in enumerate(slots) if s[0] == "Slot 6" and s[1] in (PRIMER, "CRISIS_BEAT")),
        None,
    )

    if 2 <= week <= REGULAR_SEASON_FINAL_WEEK and slot6_idx is not None:
        crisis_target = _crisis_beat_target(snap, used)
        if crisis_target is not None:
            targets[slot6_idx] = crisis_target
            if crisis_target.get("game_id"):
                used_games.add(crisis_target["game_id"])
            other_primer_indices = [i for i, s in enumerate(slots) if s[1] == PRIMER and i != slot6_idx]
            if other_primer_indices:
                picks = _primer_targets(snap, len(other_primer_indices), used, used_games=used_games)
                for i, t in zip(other_primer_indices, picks):
                    targets[i] = t
        else:
            # Fall back to standard matchup primer if no crisis team qualifies
            if slots[slot6_idx][1] == PRIMER:
                primer_idx = indices(PRIMER)
                picks = _primer_targets(snap, len(primer_idx), used, used_games=used_games)
                for i, t in zip(primer_idx, picks):
                    targets[i] = t
            else:
                other_primer_indices = [i for i, s in enumerate(slots) if s[1] == PRIMER and i != slot6_idx]
                if other_primer_indices:
                    picks = _primer_targets(snap, len(other_primer_indices), used, used_games=used_games)
                    for i, t in zip(other_primer_indices, picks):
                        targets[i] = t
    else:
        # Primers are resolved first so the week's marquee games are never pre-empted by exit slots.
        primer_idx = indices(PRIMER)
        if primer_idx:
            picks = _super_bowl_primer_targets(snap, used) if week == SUPER_BOWL_WEEK else _primer_targets(snap, len(primer_idx), used)
            for i, t in zip(primer_idx, picks):
                targets[i] = t

    # The hot-seat audit takes the worst underperformer before the exit State of the Union picks its teams.
    hot_seat_idx = indices(HOT_SEAT_AUDIT)
    if hot_seat_idx and week == WILD_CARD_WEEK:
        for i, t in zip(hot_seat_idx, _hot_seat_targets(snap, len(hot_seat_idx), used)):
            targets[i] = t

    sotu_idx = indices(STATE_OF_THE_UNION)
    if sotu_idx and week == WILD_CARD_WEEK:
        picks = _exit_targets(
            snap, "MISSED_PLAYOFFS", _missed_playoff_team_ids(snap), len(sotu_idx), used,
            "{team} missed the playoffs; write its exit State of the Union.",
        )
        for i, t in zip(sotu_idx, picks):
            targets[i] = t
    elif sotu_idx and week in PHASE_4_WEEKS:
        for i, t in zip(sotu_idx, _eliminated_targets(snap, len(sotu_idx), used)):
            targets[i] = t

    autopsy_idx = indices(PLAYOFF_AUTOPSY)
    if autopsy_idx and week > WILD_CARD_WEEK:
        picks = _exit_targets(
            snap, "PLAYOFF_EXIT", _prior_week_loser_ids(snap), len(autopsy_idx), used,
            f"{{team}} was eliminated in the Week {week - 1} playoff round; conduct its autopsy.",
        )
        for i, t in zip(autopsy_idx, picks):
            targets[i] = t

    return targets


def load_league_snapshot(db_conn_info: str, season: int, week: int) -> LeagueSnapshot:
    snap = LeagueSnapshot(season=season, week=week)
    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.name, t.conference, t.division, COALESCE(ts.wins, 0), COALESCE(ts.losses, 0),
                       COALESCE(ts.popularity_index, 0), ts.expected_wins
                FROM teams t
                LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s;
            """, (season,))
            for tid, name, conf, division, wins, losses, pop, exp_wins in cur.fetchall():
                exp = float(exp_wins) if exp_wins is not None else None
                snap.teams[tid] = {
                    "name": name, "conference": conf, "division": division,
                    "wins": wins, "losses": losses, "popularity": float(pop),
                    "expected_wins": exp,
                    "losing_streak": 0,
                }
                if exp is not None:
                    snap.expected_wins[tid] = exp

            cur.execute("""
                SELECT week, home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE season = %s AND week < %s AND status = 'COMPLETED'
                  AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
                ORDER BY week ASC;
            """, (season, week))
            team_history = {}
            for _, h, a, hs, as_ in cur.fetchall():
                if hs is None or as_ is None or hs == as_:
                    continue
                h_res = "W" if hs > as_ else "L"
                a_res = "L" if hs > as_ else "W"
                team_history.setdefault(h, []).append(h_res)
                team_history.setdefault(a, []).append(a_res)

            for tid, hist in team_history.items():
                if tid in snap.teams:
                    l_streak = 0
                    for res in reversed(hist):
                        if res == "L":
                            l_streak += 1
                        else:
                            break
                    snap.teams[tid]["losing_streak"] = l_streak
                    snap.losing_streaks[tid] = l_streak

            cur.execute("""
                SELECT id, home_team_id, away_team_id, status, excitement_index, projected_excitement
                FROM games
                WHERE season = %s AND week = %s AND status <> 'BYE'
                  AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL;
            """, (season, week))
            for gid, h, a, status, ex, proj in cur.fetchall():
                completed = status == "COMPLETED" and ex is not None and ex > 0
                excitement = float(ex) if completed else (float(proj) if proj is not None else None)
                snap.week_games.append({
                    "game_id": gid, "home_team_id": h, "away_team_id": a,
                    "excitement": excitement, "projected": not completed and proj is not None,
                })

            cur.execute("""
                SELECT id, home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE season = %s AND week = %s AND status = 'COMPLETED'
                  AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL;
            """, (season, week - 1))
            snap.prior_week_results = [
                {"game_id": gid, "home_team_id": h, "away_team_id": a, "home_score": hs or 0, "away_score": as_ or 0}
                for gid, h, a, hs, as_ in cur.fetchall()
            ]

            cur.execute("""
                SELECT team_id, COUNT(*) FROM (
                    SELECT home_team_id AS team_id FROM games
                    WHERE season = %s AND week BETWEEN %s AND %s AND status = 'SCHEDULED'
                    UNION ALL
                    SELECT away_team_id FROM games
                    WHERE season = %s AND week BETWEEN %s AND %s AND status = 'SCHEDULED'
                ) g WHERE team_id IS NOT NULL GROUP BY team_id;
            """, (season, week, REGULAR_SEASON_FINAL_WEEK, season, week, REGULAR_SEASON_FINAL_WEEK))
            snap.remaining_regular_season_games = dict(cur.fetchall())

            cur.execute("""
                SELECT team_id, week FROM news_feed
                WHERE season = %s AND week < %s AND article_type = %s
                  AND content_type = 'ARTICLE' AND team_id IS NOT NULL;
            """, (season, week, PRIMER))
            snap.primer_history = [(tid, wk) for tid, wk in cur.fetchall()]

            cur.execute("""
                SELECT DISTINCT assigned_team_id FROM agent_memories
                WHERE archetype = 'LOCAL_BEAT_REPORTER' AND assigned_team_id IS NOT NULL;
            """)
            snap.teams_with_beat_reporters = {r[0] for r in cur.fetchall()}

            cur.execute("SELECT to_regclass('coach_hot_seat') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("""
                    SELECT h.team_id, c.coach_name FROM coach_hot_seat h JOIN coaches c ON c.id = h.coach_id
                    WHERE h.season = %s AND h.fired AND h.phase = 'BLACK_MONDAY' ORDER BY h.score DESC;
                """, (season,))
                snap.coach_firings = dict(cur.fetchall())
            cur.execute("SELECT to_regclass('coordinator_hot_seat') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("""
                    SELECT h.team_id, h.role || ' ' || c.coordinator_name FROM coordinator_hot_seat h
                    JOIN coordinators c ON c.id = h.coordinator_id
                    WHERE h.season = %s AND h.fired AND h.phase = 'BLACK_MONDAY' ORDER BY h.team_id, h.role;
                """, (season,))
                for tid, who in cur.fetchall():
                    snap.coordinator_firings.setdefault(tid, []).append(who)
    return snap
