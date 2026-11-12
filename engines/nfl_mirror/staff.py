"""Coaching changes for the NFL weekly mirror (docs/done/NFL_WEEKLY_MIRROR_PLAN.md §2.7).

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
record (the carousel's D2 rule).

A promotion's *source* coordinator seat needs an explicit decision (plan §8 Q2). When `changes` also names a man for
that team + OC/DC, that change ends the promoted man's coordinator tenure and installs the named replacement, so
`_wanted` asks for each man once and every seat resolves to its own profile. With no named replacement the source
seat is returned as a REFUSED STAFF move carrying its reason, and its tenure rows are left untouched: Mu never
invents a successor, and the week must not read as cleanly written. A team in `user_team_ids` is never touched, and
neither is its staff: a promotion of a man who currently coordinates for a user team is skipped whole, before any
coach row, profile or tenure is written, because a promotion would move that team's one Madden record (audit-5 C1).
Re-applying a change whose incoming person already holds the seat does nothing, except that a promotion whose source
seat is still unresolved keeps returning its REFUSED move. The function returns the STAFF moves for
`store.record_moves`.
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


def _resolved_team_id(change, team_ids, season):
    """The Mu team id for `change.team`, or None when the team is unknown (same lookup as the write loop)."""
    try:
        return team_ids.get(mu_abbr(change.team, season))
    except UnknownTeam:
        return None


def _named_seat_targets(changes, team_ids, season, user_team_ids):
    """{(team_id, role): {normalised incoming name}} for the changes that will actually write a seat.

    A user-team, unknown-team or nameless change writes nothing, so it cannot resolve a seat.
    """
    targets = {}
    for change in changes:
        team_id = _resolved_team_id(change, team_ids, season)
        if team_id is None or team_id in user_team_ids or not change.in_name or change.role not in ROLES:
            continue
        targets.setdefault((team_id, change.role), set()).add(norm_name(change.in_name))
    return targets


def _current_coordinator_seats(cur, coordinator_id, season):
    """[(team_id, role)] seats `coordinator_id` is the current holder of for `season` (the man `_wanted` picks)."""
    cur.execute("""SELECT DISTINCT team_id, role FROM coordinator_tenures
                   WHERE coordinator_id = %s AND start_season <= %s
                     AND (end_season IS NULL OR end_season >= %s)
                   ORDER BY team_id, role""", (coordinator_id, season, season))
    seats = []
    for team_id, role in cur.fetchall():
        current = _current_coord(cur, team_id, role, season)
        if current and current["person_id"] == coordinator_id:
            seats.append((team_id, role))
    return seats


def _protected_promotion_source(cur, season, change, user_team_ids):
    """(team_id, role, coordinator_id) for a user-team coordinator seat `change`'s incoming head coach holds, else None.

    A promotion keeps the man's one Madden record (carousel D2): the profile moves from COORD to COACH, which would
    leave a user team's current coordinator without a profile even though his tenure stays (audit-5 U18 close-out C1).
    The coupled promotion is therefore skipped before any coach row, profile or tenure is written: a user team's staff
    is never changed by the mirror, whatever team the promotion is to.
    """
    if change.role != "HC" or not change.in_name or not user_team_ids:
        return None
    coordinator_id = _find_coordinator(cur, change.in_name)
    if coordinator_id is None:
        return None
    for team_id, role in _current_coordinator_seats(cur, coordinator_id, season):
        if team_id in user_team_ids:
            return team_id, role, coordinator_id
    return None


def _unresolved_source_seat_moves(cur, season, change, team_ids, targets):
    """REFUSED moves for each coordinator seat a promoted head coach still holds with no named replacement.

    `change` is the HC change. A change for the same (team, role) naming a different incoming person resolves the
    seat: that change ends the promoted man's tenure and installs the named person, so nothing is returned. With no
    such change the seat is refused, its reason recorded, and the tenure rows are left untouched (Q2).
    """
    coordinator_id = _find_coordinator(cur, change.in_name)
    if coordinator_id is None:
        return []
    abbr_by_id = {team_id: abbr for abbr, team_id in team_ids.items()}
    promoted = norm_name(change.in_name)
    moves = []
    for team_id, role in _current_coordinator_seats(cur, coordinator_id, season):
        if any(name != promoted for name in targets.get((team_id, role), ())):
            continue
        moves.append({"kind": "STAFF", "to_team_id": team_id, "status": "REFUSED",
                      "reason": f"promoted to {change.role} with no named {role} replacement",
                      "detail": {"team": abbr_by_id.get(team_id), "role": role, "out_name": change.in_name,
                                 "in_name": None, "week": change.week, "source": change.source,
                                 "date": change.date, "note": change.note, "person_kind": "COORD",
                                 "person_id": coordinator_id, "promoted_team": change.team,
                                 "promoted_role": change.role}})
    return moves


def _skip(change, team_id, reason):
    return {"kind": "STAFF", "to_team_id": team_id, "status": "SKIPPED", "reason": reason,
            "detail": {"team": change.team, "role": change.role, "out_name": change.out_name,
                       "in_name": change.in_name, "week": change.week, "source": change.source}}


def apply_staff_changes(cur, season, changes, user_team_ids=frozenset()):
    """Write `changes` into the tenure tables for `season` and return their STAFF moves (see the module docs).

    A change for a user team, an unknown team or with no incoming name is returned as a SKIPPED move and writes
    nothing, as is a head-coach promotion of a man who currently holds a coordinator seat on a user team (his user
    team's profile would move with him; audit-5 C1). A change whose incoming person already holds the seat returns
    nothing, except that a promotion whose source coordinator seat is still unresolved keeps returning that seat's
    REFUSED move. A promotion whose old seat is filled by a change in `changes` writes normally; one with no named
    replacement returns a REFUSED move for the source seat and leaves its tenure rows untouched."""
    season = int(season)
    user_team_ids = frozenset(user_team_ids or ())
    changes = list(changes or ())
    team_ids = _team_ids(cur)
    targets = _named_seat_targets(changes, team_ids, season, user_team_ids)
    moves = []
    for change in changes:
        team_id = _resolved_team_id(change, team_ids, season)
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
            # Already applied (or already true in Mu). A promotion keeps refusing its unresolved source seat so a
            # re-plan still records the refusal instead of letting the week look clean (Q2).
            if change.role == "HC":
                moves.extend(_unresolved_source_seat_moves(cur, season, change, team_ids, targets))
            continue

        if change.role == "HC":
            protected = _protected_promotion_source(cur, season, change, user_team_ids)
            if protected is not None:
                seat_team, seat_role, coordinator_id = protected
                abbr_by_id = {team_id: abbr for abbr, team_id in team_ids.items()}
                moves.append({"kind": "STAFF", "to_team_id": team_id, "status": "SKIPPED",
                              "reason": (f"promotion would take user team {abbr_by_id.get(seat_team)}'s current "
                                         f"{seat_role}: Mu never changes JR's staff"),
                              "detail": {"team": change.team, "role": change.role, "out_name": change.out_name,
                                         "in_name": change.in_name, "week": change.week, "source": change.source,
                                         "date": change.date, "note": change.note,
                                         "protected_seat": {"team": abbr_by_id.get(seat_team), "role": seat_role,
                                                            "person_kind": "COORD", "person_id": coordinator_id}}})
                continue
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
        if change.role == "HC":
            moves.extend(_unresolved_source_seat_moves(cur, season, change, team_ids, targets))
    return moves
