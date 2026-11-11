"""The real NFL state of one week (NFL_WEEKLY_MIRROR_PLAN.md §2.1-2.2). Pure: rows in, frozen dataclasses out.

build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week) reads nflverse's roster_weekly,
injuries, depth_charts and games.csv rows (dicts as csv.DictReader gives them) for real week `week` (nflverse
numbering: 1-18 regular season, 19 WC, 20 DIV, 21 CON, 22 SB) and returns a RealWeek keyed on the save's team codes
(engines.nfl_history.teams.mu_abbr: ARI -> AZ, LA -> LAR, WSH -> WAS).

- Status: STATUS_MAP on status_description_abbr, then STATUS_FALLBACK on status (RES with an unknown code -> IR,
  logged). CUT, RET and EXE players are left off the team.
- Bye weeks: a team with no roster rows for `week` reuses its newest earlier week (rosters and injuries) and is
  listed in carried_forward. A player who shows up on another team's current-week roster is dropped from the
  carried team.
- Depth: per team the newest `dt` snapshot dated on or before the week's first game day (LIVE: the newest snapshot);
  a team with no snapshot by then uses its oldest one. Special Teams rows are skipped except K / PK / P.
- Head coach: the team's games.csv coach in that week's game, else its nearest earlier game.
- Mode: CATCH_UP when the week has games and every one has a final score, else LIVE.

later_weeks() gives per-week status maps after `week` for injury return dates (U4).
"""
import collections
import logging
from dataclasses import dataclass, field

from engines.nfl_history.nflverse import _blank, _int, _text, height_inches
from engines.nfl_history.teams import UnknownTeam, mu_abbr

log = logging.getLogger(__name__)

CATCH_UP = "CATCH_UP"
LIVE = "LIVE"

STATUS_MAP = {   # status_description_abbr -> RealPlayer.status
    "A01": "ACTIVE",
    **{f"I0{i}": "INACTIVE" for i in range(1, 10)},
    **{f"P0{i}": "PS" for i in range(1, 10)},
    "R01": "IR", "R48": "IR",
    "R04": "PUP",
    "R05": "NFI",
}
STATUS_FALLBACK = {"ACT": "ACTIVE", "INA": "INACTIVE", "DEV": "PS", "RES": "IR", "SUS": "SUSP"}
LEFT_OFF = frozenset({"CUT", "RET", "EXE"})
SPECIAL_TEAMS_KEPT = frozenset({"K", "PK", "P"})

MU_TO_REAL_WEEK = {19: 19, 20: 20, 21: 21, 23: 22}   # Mu playoff weeks (22 = Pro Bowl) -> nflverse weeks
REGULAR_WEEKS = 18


@dataclass(frozen=True)
class RealPlayer:
    gsis_id: str
    name: str
    position: str
    team: str
    status: str          # ACTIVE | INACTIVE | PS | IR | PUP | NFI | SUSP
    designation: str     # the raw status_description_abbr (e.g. R48 = designated for return), "" when blank
    birth_date: str | None = None
    college: str | None = None
    height_in: int | None = None
    weight_lbs: int | None = None
    entry_year: int | None = None
    draft_number: int | None = None
    jersey: int | None = None


@dataclass(frozen=True)
class RealInjury:
    gsis_id: str
    team: str
    report_status: str
    body_part: str | None = None
    practice_status: str | None = None


@dataclass(frozen=True)
class RealWeek:
    season: int
    week: int
    mode: str
    rosters: dict = field(default_factory=dict)       # team -> [RealPlayer]
    injuries: dict = field(default_factory=dict)      # gsis_id -> RealInjury
    depth: dict = field(default_factory=dict)         # team -> pos_abb -> [gsis_id] (depth order)
    head_coach: dict = field(default_factory=dict)    # team -> name
    carried_forward: tuple = ()
    sources: dict = field(default_factory=dict)       # file name -> sha256


# ---------- helpers ----------

def real_week_for(mu_week):
    """nflverse week for a Mu league week: 1-18 unchanged, 19 WC, 20 DIV, 21 CON, 23 SB -> 22."""
    w = int(mu_week)
    if 1 <= w <= REGULAR_WEEKS:
        return w
    if w in MU_TO_REAL_WEEK:
        return MU_TO_REAL_WEEK[w]
    raise ValueError(f"Mu week {mu_week} has no real NFL week (22 is the Pro Bowl)")


def map_status(status, abbr):
    """RealPlayer.status for a roster row, or None when the player is off the team (CUT, RET, EXE)."""
    st = (status or "").strip().upper()
    code = (abbr or "").strip().upper()
    if st in LEFT_OFF:
        return None
    if code in STATUS_MAP:
        return STATUS_MAP[code]
    if st in STATUS_FALLBACK:
        if st == "RES":
            log.warning("unknown reserve code %r -> IR", code)
        return STATUS_FALLBACK[st]
    log.warning("unknown roster status %r/%r, left off", st, code)
    return None


def team_code(code, season=None):
    """The save's team code for an nflverse code, or None (logged) when unknown or blank."""
    if _blank(code):
        return None
    try:
        return mu_abbr(code, season)
    except UnknownTeam:
        log.warning("unknown team code %r skipped", code)
        return None


def _season_week(rows, season):
    """Rows of `season`, grouped by int week."""
    out = collections.defaultdict(list)
    for r in rows:
        if _int(r.get("season")) != int(season):
            continue
        w = _int(r.get("week"))
        if w is not None:
            out[w].append(r)
    return out


def _games_of(rows_games, season):
    return [g for g in rows_games if _int(g.get("season")) == int(season) and _int(g.get("week")) is not None]


def _final(g):
    return not _blank(g.get("home_score")) and not _blank(g.get("away_score"))


def week_mode(rows_games, season, week):
    """CATCH_UP when real week `week` has games and all are final, else LIVE."""
    games = [g for g in _games_of(rows_games, season) if _int(g.get("week")) == int(week)]
    return CATCH_UP if games and all(_final(g) for g in games) else LIVE


def first_game_day(rows_games, season, week):
    days = [str(g.get("gameday")).strip()[:10] for g in _games_of(rows_games, season)
            if _int(g.get("week")) == int(week) and not _blank(g.get("gameday"))]
    return min(days) if days else None


def _player(r, team):
    status = map_status(r.get("status"), r.get("status_description_abbr"))
    if status is None or _blank(r.get("gsis_id")):
        return None
    name = _text(r.get("full_name")) or " ".join(
        x for x in (_text(r.get("football_name")) or _text(r.get("first_name")), _text(r.get("last_name"))) if x)
    try:
        height = height_inches(r.get("height"))
    except ValueError:
        height = None
    return RealPlayer(
        gsis_id=str(r["gsis_id"]).strip(), name=name or "", position=_text(r.get("position")) or "",
        team=team, status=status, designation=_text(r.get("status_description_abbr")) or "",
        birth_date=_text(r.get("birth_date")), college=_text(r.get("college")), height_in=height,
        weight_lbs=_int(r.get("weight")), entry_year=_int(r.get("entry_year")),
        draft_number=_int(r.get("draft_number")), jersey=_int(r.get("jersey_number")))


def _by_team(rows, season):
    out = collections.defaultdict(list)
    for r in rows:
        t = team_code(r.get("team"), season)
        if t:
            out[t].append(r)
    return out


def source_weeks(rows_rosters, season, week):
    """{team: week its roster comes from}: `week` when it has rows, else its newest earlier week."""
    by_week = _season_week(rows_rosters, season)
    out = {}
    for w in sorted(k for k in by_week if k <= int(week)):
        for t in _by_team(by_week[w], season):
            out[t] = w
    return out


def _injury(r, team):
    if _blank(r.get("gsis_id")):
        return None
    return RealInjury(gsis_id=str(r["gsis_id"]).strip(), team=team, report_status=_text(r.get("report_status")) or "",
                      body_part=_text(r.get("report_primary_injury")),
                      practice_status=_text(r.get("practice_status")))


def build_depth(rows_depth, season, cutoff=None):
    """{team: {pos_abb: [gsis_id]}} from each team's chosen snapshot (newest dt <= cutoff date; None = newest)."""
    snaps = collections.defaultdict(lambda: collections.defaultdict(list))   # team -> dt -> rows
    for r in rows_depth:
        if _blank(r.get("dt")) or _blank(r.get("gsis_id")):
            continue
        grp = (_text(r.get("pos_grp")) or "")
        pos = (_text(r.get("pos_abb")) or "").upper()
        if grp.lower() == "special teams" and pos not in SPECIAL_TEAMS_KEPT:
            continue
        t = team_code(r.get("team"), season)
        if t and pos:
            snaps[t][str(r["dt"]).strip()].append(r)
    out = {}
    for t, by_dt in snaps.items():
        dts = sorted(by_dt)
        ok = [d for d in dts if cutoff is None or d[:10] <= cutoff]
        chosen = ok[-1] if ok else dts[0]
        chart = collections.defaultdict(list)
        rows = sorted(by_dt[chosen], key=lambda r: (_int(r.get("pos_rank")) or 99, _int(r.get("pos_slot")) or 99))
        for r in rows:
            pos, gid = r["pos_abb"].strip().upper(), str(r["gsis_id"]).strip()
            if gid not in chart[pos]:
                chart[pos].append(gid)
        out[t] = dict(chart)
    return out


def head_coaches(rows_games, season, week):
    """{team: head coach} from the team's game in `week`, else its nearest earlier game."""
    out = {}
    games = sorted(_games_of(rows_games, season), key=lambda g: (_int(g["week"]), str(g.get("gameday") or "")))
    for g in games:
        if _int(g["week"]) > int(week):
            break
        for side in ("home", "away"):
            t, coach = team_code(g.get(f"{side}_team"), season), _text(g.get(f"{side}_coach"))
            if t and coach:
                out[t] = coach
    return out


# ---------- the week ----------

def build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week, sources=None):
    """RealWeek for real week `week` of `season` (see the module docstring)."""
    season, week = int(season), int(week)
    rows_rosters, rows_injuries = list(rows_rosters), list(rows_injuries)
    rows_games = list(rows_games)
    by_week = _season_week(rows_rosters, season)
    src = source_weeks(rows_rosters, season, week)
    carried = tuple(sorted(t for t, w in src.items() if w != week))

    rosters = {}
    for t, w in src.items():
        players = [p for p in (_player(r, t) for r in _by_team(by_week[w], season).get(t, [])) if p]
        rosters[t] = players
    current = {p.gsis_id for t in src if t not in carried for p in rosters[t]}
    for t in carried:
        rosters[t] = [p for p in rosters[t] if p.gsis_id not in current]
    for t in rosters:
        seen, kept = set(), []
        for p in rosters[t]:
            if p.gsis_id not in seen:
                seen.add(p.gsis_id)
                kept.append(p)
        rosters[t] = kept

    inj_by_week = _season_week(rows_injuries, season)
    injuries = {}
    for t, w in src.items():
        for r in _by_team(inj_by_week.get(w, []), season).get(t, []):
            i = _injury(r, t)
            if i and (t not in carried or i.gsis_id not in current):
                injuries[i.gsis_id] = i

    mode = week_mode(rows_games, season, week)
    cutoff = None if mode == LIVE else first_game_day(rows_games, season, week)
    return RealWeek(season=season, week=week, mode=mode, rosters=rosters, injuries=injuries,
                    depth=build_depth(rows_depth, season, cutoff), head_coach=head_coaches(rows_games, season, week),
                    carried_forward=carried, sources=dict(sources or {}))


def later_weeks(rows_rosters, rows_injuries, season, week):
    """{w: {gsis_id: {"status": RealPlayer.status | None, "report_status": str | None}}} for every week after `week`
    with roster or injury rows. status None = not on any roster that week (cut, retired, or his team's bye);
    report_status is that week's injury report, None when he has none."""
    rosters, inj = _season_week(rows_rosters, season), _season_week(rows_injuries, season)
    out = {}
    for w in sorted(set(rosters) | set(inj)):
        if w <= int(week):
            continue
        m = {}
        for r in rosters.get(w, []):
            if _blank(r.get("gsis_id")):
                continue
            m[str(r["gsis_id"]).strip()] = {"status": map_status(r.get("status"), r.get("status_description_abbr")),
                                            "report_status": None}
        for r in inj.get(w, []):
            if _blank(r.get("gsis_id")):
                continue
            entry = m.setdefault(str(r["gsis_id"]).strip(), {"status": None, "report_status": None})
            entry["report_status"] = _text(r.get("report_status"))
        out[w] = m
    return out
