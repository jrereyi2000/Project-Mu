"""Coaching changes for the NFL weekly mirror (docs/in_progress/NFL_WEEKLY_MIRROR_PLAN.md §2.7).

    real_staff_changes(head_coach_by_week, week, staff_csv_rows) -> [StaffChange]      pure
    read_staff_csv(path=DEFAULT_PATH) -> [dict]
    apply_staff_changes(cur, season, changes, user_team_ids=frozenset()) -> [STAFF move dict]

A head-coach change is read from the real per-game head coach (`RealWeek.head_coach`, {team: name} per week): a team
whose coach differs from the one it had in its previous week with a game changed coach before `week`. Coordinator
changes, and anything the games can't show (an interim flag, a note), come from the hand-kept
`data/history/nfl_staff_changes_2026.csv` (`date, week, team, role, out_name, in_name, interim, note`).

`apply_staff_changes` writes them into Mu's season-granular tenure tables the way engines/coach_save_sync.py reads
them: the outgoing tenure ends at `season` (it still covers the season), the incoming person's tenure starts at
`season` with a higher id, and `_wanted` picks the latest start, then the highest id. The incoming person is matched
by name in `coaches` (HC) or `coordinators` (OC/DC); otherwise a name-only row is created with a CREATED
`coach_madden_profiles` row built by `coach_madden_profile.build_profile` through the carousel's own helper, so the
league office's `step_staff` can create him in the save. A coordinator promoted to head coach keeps his own Madden
record (the carousel's D2 rule). A team in `user_team_ids` is never touched. Re-applying a change whose incoming
person already holds the seat does nothing. The function returns the STAFF moves for `store.record_moves`.
"""
import csv
import os
import re
from dataclasses import dataclass

from engines import coach_madden_profile as cmp
from engines import coaching_carousel as carousel
from engines.nfl_history.teams import UnknownTeam, mu_abbr

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_PATH = os.path.join(ROOT, "data", "history", "nfl_staff_changes_2026.csv")
CSV_COLUMNS = ("date", "week", "team", "role", "out_name", "in_name", "interim", "note")
ROLES = ("HC", "OC", "DC")
SIDE = {"OC": "OFFENSIVE", "DC": "DEFENSIVE"}
# A created coordinator needs a scheme (coordinators.scheme is NOT NULL). He keeps the man he replaces' system; with
# nobody to replace, the side's plain default.
DEFAULT_SCHEME = {"OC": "West Coast", "DC": "4-3"}
_TRUE = {"1", "true", "t", "yes", "y", "x"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


@dataclass(frozen=True)
class StaffChange:
    team: str                     # nflverse or Mu team code
    role: str                     # HC | OC | DC
    out_name: str | None
    in_name: str | None
    week: int
    interim: bool = False
    source: str = "GAMES"         # GAMES (per-game head coach) | CSV
    date: str | None = None
    note: str | None = None


def norm_name(name):
    """Lower case, punctuation and suffixes dropped: 'Mike McDaniel Jr.' -> 'mike mcdaniel'."""
    words = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower().replace("'", "").replace(".", "")).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def _truthy(value):
    return str(value or "").strip().lower() in _TRUE


def _blank(value):
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


# --------------------------------------------------------------------------------------------- real changes ----
def _previous_coach(head_coach_by_week, team, week):
    for w in sorted((w for w in head_coach_by_week if int(w) < int(week)), reverse=True):
        name = (head_coach_by_week.get(w) or {}).get(team)
        if name:
            return name
    return None


def real_staff_changes(head_coach_by_week, week, staff_csv_rows):
    """The coaching changes that take effect in `week`.

    Head coaches: every team whose `week` head coach differs from its coach in its last earlier week with a game
    (a bye is skipped over). Coordinators, and head-coach rows the games can't show, come from the CSV rows for
    `week`; a CSV head-coach row naming the same incoming coach as a game change adds its interim flag, date and note
    to it instead of being a second change.
    """
    week = int(week)
    changes = {}
    for team, name in sorted((head_coach_by_week.get(week) or {}).items()):
        prev = _previous_coach(head_coach_by_week, team, week)
        if name and prev and norm_name(prev) != norm_name(name):
            changes[(team, "HC", norm_name(name))] = StaffChange(team=team, role="HC", out_name=prev, in_name=name,
                                                                 week=week)
    for row in staff_csv_rows or ():
        try:
            row_week = int(str(row.get("week") or "").strip())
        except ValueError:
            continue
        if row_week != week:
            continue
        role = str(row.get("role") or "").strip().upper()
        team = str(row.get("team") or "").strip().upper()
        if role not in ROLES or not team:
            continue
        in_name = _blank(row.get("in_name"))
        key = (team, role, norm_name(in_name))
        base = changes.get(key)
        changes[key] = StaffChange(team=team, role=role,
                                   out_name=_blank(row.get("out_name")) or (base.out_name if base else None),
                                   in_name=in_name, week=week, interim=_truthy(row.get("interim")),
                                   source="GAMES" if base else "CSV", date=_blank(row.get("date")),
                                   note=_blank(row.get("note")))
    return sorted(changes.values(), key=lambda c: (c.team, ROLES.index(c.role), c.in_name or ""))


def read_staff_csv(path=DEFAULT_PATH):
    """The CSV's rows as dicts with CSV_COLUMNS keys; [] when the file is missing."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return [{c: (row.get(c) or "").strip() for c in CSV_COLUMNS} for row in csv.DictReader(fh)]


# -------------------------------------------------------------------------------------------- tenure writes ----
def _team_ids(cur):
    cur.execute("SELECT id, abbreviation FROM teams WHERE abbreviation IS NOT NULL")
    return {abbr: tid for tid, abbr in cur.fetchall()}


def _current_hc(cur, team_id, season):
    cur.execute("""SELECT ct.id, c.id, c.coach_name, c.offensive_scheme, c.defensive_scheme, ct.start_season
                   FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                   WHERE ct.team_id = %s AND ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.start_season DESC, (ct.end_season IS NULL) DESC, ct.id DESC LIMIT 1""",
                (team_id, season, season))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("tenure_id", "person_id", "name", "offensive_scheme", "defensive_scheme", "start_season"),
                    row))


def _current_coord(cur, team_id, role, season):
    cur.execute("""SELECT ct.id, c.id, c.coordinator_name, c.scheme, c.playbook, ct.start_season
                   FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.team_id = %s AND ct.role = %s AND ct.start_season <= %s
                     AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.start_season DESC, (ct.end_season IS NULL) DESC, ct.id DESC LIMIT 1""",
                (team_id, role, season, season))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("tenure_id", "person_id", "name", "scheme", "playbook", "start_season"), row))


def _find_coach(cur, name):
    cur.execute("SELECT id, coach_name FROM coaches ORDER BY id")
    for cid, cname in cur.fetchall():
        if norm_name(cname) == norm_name(name):
            return cid
    return None


def _find_coordinator(cur, name, role=None):
    """The coordinators row for `name`: one on `role`'s side first, then the lowest id."""
    cur.execute("SELECT id, coordinator_name, side FROM coordinators ORDER BY id")
    hits = [(cid, side) for cid, cname, side in cur.fetchall() if norm_name(cname) == norm_name(name)]
    if not hits:
        return None
    if role in SIDE:
        same = [cid for cid, side in hits if side == SIDE[role]]
        if same:
            return same[0]
    return hits[0][0]


def _profile_source(cur, person_kind, person_id):
    if not carousel._table_exists(cur, "coach_madden_profiles"):
        return None
    cur.execute("SELECT source FROM coach_madden_profiles WHERE person_kind = %s AND person_id = %s",
                (person_kind, person_id))
    row = cur.fetchone()
    return row[0] if row else None


def _scheme(value):
    return value if value in cmp.SCHEME_MAP else None


def _store_profile_for(cur, kind, person_id, name, role, outgoing):
    """Store the CREATED Madden profile for `person_id` (audit F1), the same profile a new row would get.

    A person Mu already knows (a `coaches` / `coordinators` row) can still lack a `coach_madden_profiles` row. The
    mirror must leave him usable by the staff write, so the existing person is given the profile instead of being
    booked with `profile: None`. The `coaches` / `coordinators` row itself is never touched (no second person row).
    """
    if kind == "COACH":
        off = (outgoing or {}).get("offensive_scheme")
        de = (outgoing or {}).get("defensive_scheme")
        carousel._store_created_profile(cur, person_id, name, "HC",
                                        scheme={"offensive": _scheme(off), "defensive": _scheme(de)},
                                        promote_from=_find_coordinator(cur, name))
    else:
        scheme = (outgoing or {}).get("scheme") or DEFAULT_SCHEME[role]
        playbook = (outgoing or {}).get("playbook")
        carousel._store_created_profile(cur, person_id, name, role, scheme=_scheme(scheme),
                                        playbook=carousel._mapped_playbook(playbook))


def _incoming_coach(cur, name, outgoing):
    """(coach id, created?) for the incoming head coach; a new row, and an existing person with no profile, gets a
    CREATED Madden profile."""
    coach_id = _find_coach(cur, name)
    if coach_id is not None:
        if carousel._table_exists(cur, "coach_madden_profiles") and _profile_source(cur, "COACH", coach_id) is None:
            _store_profile_for(cur, "COACH", coach_id, name, "HC", outgoing)
        return coach_id, False
    off = (outgoing or {}).get("offensive_scheme")
    de = (outgoing or {}).get("defensive_scheme")
    cur.execute("INSERT INTO coaches (coach_name, offensive_scheme, defensive_scheme) VALUES (%s, %s, %s) "
                "RETURNING id", (name, off, de))
    coach_id = cur.fetchone()[0]
    _store_profile_for(cur, "COACH", coach_id, name, "HC", outgoing)
    return coach_id, True


def _incoming_coordinator(cur, name, role, outgoing):
    """(coordinator id, created?) for the incoming coordinator; a new row, and an existing person with no profile,
    gets a CREATED Madden profile."""
    coord_id = _find_coordinator(cur, name, role)
    if coord_id is not None:
        if carousel._table_exists(cur, "coach_madden_profiles") and _profile_source(cur, "COORD", coord_id) is None:
            _store_profile_for(cur, "COORD", coord_id, name, role, outgoing)
        return coord_id, False
    scheme = (outgoing or {}).get("scheme") or DEFAULT_SCHEME[role]
    playbook = (outgoing or {}).get("playbook")
    cur.execute("""INSERT INTO coordinators (coordinator_name, side, scheme, playbook, origin_notes)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (name, SIDE[role], scheme, playbook, "NFL weekly mirror: real 2026 staff change"))
    coord_id = cur.fetchone()[0]
    _store_profile_for(cur, "COORD", coord_id, name, role, outgoing)
    return coord_id, True


def _start_tenure(cur, table, column, person_id, team_id, season, role=None):
    """The incoming tenure starting at `season`; a tenure that already starts then is reopened instead."""
    keys = f"{column} = %s AND team_id = %s AND start_season = %s" + (" AND role = %s" if role else "")
    params = (person_id, team_id, season) + ((role,) if role else ())
    cur.execute(f"SELECT id FROM {table} WHERE {keys}", params)
    row = cur.fetchone()
    if row:
        cur.execute(f"UPDATE {table} SET end_season = NULL WHERE id = %s", (row[0],))
        return row[0]
    if role:
        cur.execute(f"INSERT INTO {table} ({column}, team_id, role, start_season) VALUES (%s, %s, %s, %s) "
                    "RETURNING id", (person_id, team_id, role, season))
    else:
        cur.execute(f"INSERT INTO {table} ({column}, team_id, start_season) VALUES (%s, %s, %s) RETURNING id",
                    (person_id, team_id, season))
    return cur.fetchone()[0]


def _skip(change, team_id, reason):
    return {"kind": "STAFF", "to_team_id": team_id, "status": "SKIPPED", "reason": reason,
            "detail": {"team": change.team, "role": change.role, "out_name": change.out_name,
                       "in_name": change.in_name, "week": change.week, "source": change.source}}


def apply_staff_changes(cur, season, changes, user_team_ids=frozenset()):
    """Write `changes` into the tenure tables for `season` and return their STAFF moves (see the module docs).

    A change for a user team, an unknown team or with no incoming name is returned as a SKIPPED move and writes
    nothing. A change whose incoming person already holds the seat returns nothing."""
    season = int(season)
    user_team_ids = frozenset(user_team_ids or ())
    team_ids = _team_ids(cur)
    moves = []
    for change in changes:
        try:
            team_id = team_ids.get(mu_abbr(change.team, season))
        except UnknownTeam:
            team_id = None
        if team_id is None:
            moves.append(_skip(change, None, f"unknown team {change.team!r}"))
            continue
        if team_id in user_team_ids:
            moves.append(_skip(change, team_id, "user team: Mu never changes JR's staff"))
            continue
        if not change.in_name or change.role not in ROLES:
            moves.append(_skip(change, team_id, "no incoming coach named"))
            continue

        if change.role == "HC":
            current = _current_hc(cur, team_id, season)
            table, column, kind = "coach_tenures", "coach_id", "COACH"
        else:
            current = _current_coord(cur, team_id, change.role, season)
            table, column, kind = "coordinator_tenures", "coordinator_id", "COORD"
        if current and norm_name(current["name"]) == norm_name(change.in_name):
            continue                                       # already applied (or already true in Mu)

        if change.role == "HC":
            person_id, created = _incoming_coach(cur, change.in_name, current)
        else:
            person_id, created = _incoming_coordinator(cur, change.in_name, change.role, current)
        if current:
            cur.execute(f"UPDATE {table} SET end_season = %s WHERE id = %s", (season, current["tenure_id"]))
        tenure_id = _start_tenure(cur, table, column, person_id, team_id, season,
                                  role=None if change.role == "HC" else change.role)

        detail = {"team": change.team, "role": change.role, "week": change.week, "source": change.source,
                  "out_name": change.out_name, "in_name": change.in_name, "interim": change.interim,
                  "person_kind": kind, "person_id": person_id, "created": created,
                  "profile": _profile_source(cur, kind, person_id), "tenure_id": tenure_id,
                  "ended_tenure_id": current["tenure_id"] if current else None,
                  "ended_person_id": current["person_id"] if current else None,
                  "date": change.date, "note": change.note}
        if current and change.out_name and norm_name(current["name"]) != norm_name(change.out_name):
            detail["mu_out_name"] = current["name"]        # Mu had someone else in the seat; real life wins
        moves.append({"kind": "STAFF", "to_team_id": team_id, "status": "PLANNED", "detail": detail,
                      "reason": change.note})
    return moves
