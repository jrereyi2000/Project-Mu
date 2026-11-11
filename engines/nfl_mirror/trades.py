"""Real 2026 trades (NFL_WEEKLY_MIRROR_PLAN.md §2.3). Pure apart from reading the CSV.

nflverse's trades.csv stopped updating in May 2026, so trades come from the hand-kept data/history/nfl_trades_2026.csv
(trade_id, date, week, from_team, to_team, gsis_id, player_name, note; one row per player leg, pick legs in note).
infer_switches(prev, cur) lists every team-to-team move between two consecutive RealWeeks; unconfirmed() keeps the
switches no CSV leg explains (planned as release plus sign and reported as "unconfirmed: trade or claim?").
"""
import csv
import os
from dataclasses import dataclass

from engines.nfl_history.nflverse import _blank, _int, _text
from engines.nfl_mirror.real_state import team_code

DEFAULT_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "history",
                                             "nfl_trades_2026.csv"))
COLUMNS = ("trade_id", "date", "week", "from_team", "to_team", "gsis_id", "player_name", "note")


@dataclass(frozen=True)
class TradeLeg:
    trade_id: str
    date: str | None
    week: int | None        # real week the player first plays for to_team; None matches any week
    from_team: str | None   # save team codes
    to_team: str | None
    gsis_id: str | None     # None for a pick-only leg
    player_name: str | None
    note: str | None


@dataclass(frozen=True)
class Switch:
    gsis_id: str
    name: str
    from_team: str
    to_team: str
    week: int               # the week he is first on to_team
    status: str             # his RealPlayer.status on to_team


def read_trades(path=DEFAULT_PATH):
    """[TradeLeg] from the trades CSV; blank lines and lines starting with '#' are skipped. [] when missing."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    out = []
    for r in csv.DictReader(lines):
        if all(_blank(r.get(c)) for c in COLUMNS):
            continue
        out.append(TradeLeg(
            trade_id=_text(r.get("trade_id")) or "", date=_text(r.get("date")), week=_int(r.get("week")),
            from_team=team_code(r.get("from_team")), to_team=team_code(r.get("to_team")),
            gsis_id=_text(r.get("gsis_id")), player_name=_text(r.get("player_name")), note=_text(r.get("note"))))
    return out


def _teams_by_player(week):
    out = {}
    for team, players in week.rosters.items():
        for p in players:
            out.setdefault(p.gsis_id, {})[team] = p
    return out


def infer_switches(prev, cur):
    """[Switch] for every player on a team in `cur` that he was not on in `prev`, having been on another team there."""
    before, after = _teams_by_player(prev), _teams_by_player(cur)
    out = []
    for gid, teams in sorted(after.items()):
        old = before.get(gid)
        if not old:
            continue
        for team, p in sorted(teams.items()):
            if team in old:
                continue
            from_team = sorted(t for t in old if t not in teams)
            if not from_team:
                continue
            out.append(Switch(gsis_id=gid, name=p.name, from_team=from_team[0], to_team=team, week=cur.week,
                              status=p.status))
    return out


def unconfirmed(switches, trades):
    """Switches with no trade leg matching (gsis_id, to_team, week); a leg with no week matches any week."""
    legs = [(t.gsis_id, t.to_team, t.week) for t in trades if t.gsis_id]
    return [s for s in switches
            if not any(g == s.gsis_id and to == s.to_team and (w is None or w == s.week) for g, to, w in legs)]
