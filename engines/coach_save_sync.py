"""What to write into the save's staff block (COACH_SAVE_WRITES_PLAN §2.6, U5).

    plan_staff_edits(cur, snapshot, target_season) -> {"staff": [...], "skipped": [...], "report": [...]}

Diffs Mu's staff for `target_season` against the save-reader snapshot's staff (`snapshot["staff"]`, reader_version 8)
and turns the difference into the `"staff"` block unit U3's `staff.mjs` consumes:

    {"action": "release", "pgid": 9001}                                    # or ..., "retire": true (D8)
    {"action": "assign",  "pgid": 9002, "team_index": 7, "position": "DefensiveCoordinator",
                          "contract": {"ContractLength": 3, "ContractSalary": 450, "ContractYearsRemaining": 3}}
    {"action": "create",  "key": "coach-311", "team_index": 7, "position": "HeadCoach",
                          "profile": {...coach_madden_profiles fields...}, "visuals_template": 9010, "contract": {...}}

Rules (§2.6): wanted seats are the HC/OC/DC whose tenure covers `target_season`; actual seats are the save's. A man
in the save who leaves is a `release` unless another team wants him, in which case it is an `assign` (his old team's
ref is cleared by his own move); a wanted man with a `presentation_id` is an `assign`, without one a `create`. The
order is releases, then assigns, then creates, so a released row can be reused by a `create` and a head-coach seat
change (release + assign) lands in one write. A seat whose wanted coach has no `coach_madden_profiles` row, or a team
whose hiring cycle is not finished, is `skipped` and reported; nothing is guessed.

**Hiring-cycle-finished test:** a team counts as finished when all three of its seats (HC, OC, DC) have a tenure
covering `target_season` in Mu. A missed weekly run leaves a fired seat with no successor tenure, which is exactly the
R7 case, so the whole team is skipped rather than half-written. **D8:** a save coach who is a free agent
(`TeamIndex = 32`, `ContractStatus = FreeAgent`) and whose Mu tenure ended at `last_end` is retired once **three
complete unhired seasons** have passed (the write for `target_season` sees seasons `last_end + 1 ... target_season -
1`), i.e. `target_season - last_end - 1 >= 3`, and not before.

Rerun: once the save matches Mu (and new PresentationIds are recorded back by the league office), it returns an empty
`staff` list. It only reads; it never writes.
"""
import json

FREE_AGENT_TEAM_INDEX = 32
TEAM_ABBR_ALIASES = {"ARI": "AZ"}          # Madden's short name -> teams.abbreviation where they differ
RETIRE_AFTER_UNHIRED_SEASONS = 3           # D8: three complete seasons in the free-agent pool, then Retired

# (snapshot staff key, Mu role, Madden StaffPosition, coach_madden_profiles person_kind, staff_contracts person_kind)
SEATS = (
    ("hc", "HC", "HeadCoach", "COACH", "HC"),
    ("oc", "OC", "OffensiveCoordinator", "COORD", "OC"),
    ("dc", "DC", "DefensiveCoordinator", "COORD", "DC"),
)

# The coach_madden_profiles fields the writer sets on a created record. The contract stays in staff_contracts and
# `visuals_template` is carried separately in the action (the plan's §2.5 create example).
PROFILE_COLUMNS = ("first_name", "last_name", "age", "position", "archetype", "specialty", "level", "years_coaching",
                   "offensive_playbook", "defensive_playbook", "offensive_scheme", "defensive_scheme",
                   "team_philosophy", "personality", "demeanor", "body_type", "height", "weight", "face_shape",
                   "generic_head", "talents", "ratings", "tendencies", "portrait_id")
_SELECT_COLUMNS = ("person_kind", "person_id", "presentation_id", "source", "visuals_template") + PROFILE_COLUMNS
_JSON_COLUMNS = ("talents", "ratings", "tendencies")
# Madden stores Coach.ContractSalary in $10,000 units (Spike A: Todd Bowles 300 = $3.0M); Mu stores whole dollars.
MADDEN_MONEY_UNIT = 10_000


# ------------------------------------------------------------------------------------------------ small helpers ----
def _table_exists(cur, name):
    cur.execute(f"SELECT to_regclass('{name}') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def _team_map(cur, snapshot):
    """({team_index: teams.id}, {teams.id: abbreviation}) from the snapshot's real teams and Mu's teams table."""
    cur.execute("SELECT abbreviation, id FROM teams")
    rows = [(a, i) for a, i in cur.fetchall() if a]
    by_abbr = {a: i for a, i in rows}
    abbr_of = {i: a for a, i in rows}
    index_of = {}
    for t in snapshot.get("teams") or []:
        idx = t.get("team_index")
        if idx is None or idx >= FREE_AGENT_TEAM_INDEX:
            continue
        abbr = TEAM_ABBR_ALIASES.get(t.get("short_name"), t.get("short_name"))
        if abbr in by_abbr:
            index_of[idx] = by_abbr[abbr]
    return index_of, abbr_of


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default


def _coach_key(person_kind, person_id):
    return f"{'coach' if person_kind == 'COACH' else 'coord'}-{person_id}"


def _save_name(coach):
    if not coach:
        return None
    return f"{coach.get('first_name') or ''} {coach.get('last_name') or ''}".strip() or None


# ---------------------------------------------------------------------------------------------------- reads ----
def _wanted(cur, target_season):
    """{(team_id, role): {person_kind, person_id, name}} for the tenures covering `target_season` (latest-start wins)."""
    wanted = {}

    def add(team_id, role, person_kind, person_id, name):
        wanted.setdefault((team_id, role), {"person_kind": person_kind, "person_id": person_id, "name": name})

    cur.execute("""SELECT ct.team_id, c.id, c.coach_name
                   FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.start_season DESC, ct.id DESC""", (target_season, target_season))
    for team_id, coach_id, name in cur.fetchall():
        add(team_id, "HC", "COACH", coach_id, name)
    cur.execute("""SELECT ct.team_id, ct.role, c.id, c.coordinator_name
                   FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.role, ct.start_season DESC, ct.id DESC""", (target_season, target_season))
    for team_id, role, coordinator_id, name in cur.fetchall():
        add(team_id, role, "COORD", coordinator_id, name)
    return wanted


def _profiles(cur):
    """{(person_kind, person_id): profile dict}, JSON fields decoded to dict/list."""
    cur.execute("SELECT " + ", ".join(_SELECT_COLUMNS) + " FROM coach_madden_profiles")
    out = {}
    for row in cur.fetchall():
        profile = dict(zip(_SELECT_COLUMNS, row))
        for column in _JSON_COLUMNS:
            profile[column] = _json_value(profile.get(column), [] if column == "talents" else {})
        out[(profile["person_kind"], profile["person_id"])] = profile
    return out


def _profile_payload(profile):
    return {column: profile.get(column) for column in PROFILE_COLUMNS}


def _contract(cur, team_id, staff_kind, person_id, available):
    """The active staff_contracts row in Madden's field names, or None when there is no live deal to copy."""
    if not available:
        return None
    cur.execute("""SELECT total_years, annual_salary, remaining_years FROM staff_contracts
                   WHERE team_id = %s AND person_kind = %s AND person_id = %s AND remaining_years > 0
                   ORDER BY signed_season DESC, id DESC""", (team_id, staff_kind, person_id))
    row = cur.fetchone()
    if not row:
        return None
    total, salary, remaining = row
    return {"ContractLength": int(total or 0),
            "ContractSalary": max(0, int(round((salary or 0) / MADDEN_MONEY_UNIT))),
            "ContractYearsRemaining": int(remaining or 0)}


def _tenure_table(person_kind):
    return ("coach_tenures", "coach_id") if person_kind == "COACH" else ("coordinator_tenures", "coordinator_id")


def _covers(cur, person_kind, person_id, season):
    table, column = _tenure_table(person_kind)
    cur.execute(f"""SELECT count(*) FROM {table}
                    WHERE {column} = %s AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)""",
                (person_id, season, season))
    return int(cur.fetchone()[0] or 0) > 0


def _last_end(cur, person_kind, person_id):
    table, column = _tenure_table(person_kind)
    cur.execute(f"SELECT max(end_season) FROM {table} WHERE {column} = %s", (person_id,))
    return cur.fetchone()[0]


def _retire_actions(cur, snapshot, target_season, profiles, wanted_pids, freed):
    """D8: a Mu free agent unhired for three complete seasons becomes `release` with `retire: true`."""
    by_pid = {p["presentation_id"]: p for p in profiles.values() if p.get("presentation_id") is not None}
    out = []
    for coach in snapshot.get("coaches") or []:
        pgid = coach.get("pgid")
        if pgid is None or pgid in wanted_pids or pgid in freed:
            continue
        if coach.get("team_index") != FREE_AGENT_TEAM_INDEX or coach.get("contract_status") != "FreeAgent":
            continue
        profile = by_pid.get(pgid)
        if profile is None:                       # not one of Mu's people: never retire a stranger
            continue
        if _covers(cur, profile["person_kind"], profile["person_id"], target_season):
            continue                              # he is wanted somewhere
        last_end = _last_end(cur, profile["person_kind"], profile["person_id"])
        if last_end is None:
            continue
        if target_season - int(last_end) - 1 >= RETIRE_AFTER_UNHIRED_SEASONS:
            out.append({"action": "release", "pgid": pgid, "retire": True})
            freed.add(pgid)
    return out


# ----------------------------------------------------------------------------------------------- the plan ----
def plan_staff_edits(cur, snapshot, target_season):
    """The `"staff"` block that would make the save match Mu's staff for `target_season` (§2.6). See the module docs."""
    empty = {"staff": [], "skipped": [], "report": []}
    staff = snapshot.get("staff") or []
    if not staff:
        return empty
    if not _table_exists(cur, "coach_madden_profiles"):
        return empty

    index_of, abbr_of = _team_map(cur, snapshot)
    wanted = _wanted(cur, target_season)
    profiles = _profiles(cur)
    save_coaches = {c.get("pgid"): c for c in snapshot.get("coaches") or [] if c.get("pgid") is not None}
    has_contracts = _table_exists(cur, "staff_contracts")

    skipped = []
    decided = []                                  # (team_index, team_id, seat dict) with a finished hiring cycle
    for seat in staff:
        idx = seat.get("team_index")
        team_id = index_of.get(idx)
        if team_id is None:
            skipped.append({"team_index": idx, "reason": "team not in teams table"})
            continue
        missing = [role for _key, role, _pos, _kind, _skind in SEATS if (team_id, role) not in wanted]
        if missing:
            skipped.append({"team": abbr_of.get(team_id), "team_index": idx, "roles": missing,
                            "reason": "hiring cycle not finished"})
            continue
        decided.append((idx, team_id, seat))

    # The presentation ids of every wanted man on a decided team: a man leaving a seat who is one of them is moved
    # (an assign by his new team) rather than released.
    wanted_pids = set()
    for _idx, team_id, _seat in decided:
        for _key, role, _pos, _kind, _skind in SEATS:
            person = wanted[(team_id, role)]
            profile = profiles.get((person["person_kind"], person["person_id"]))
            if profile and profile.get("presentation_id") is not None:
                wanted_pids.add(profile["presentation_id"])

    releases, assigns, creates, report = [], [], [], []
    freed = set()
    for idx, team_id, seat in decided:
        abbr = abbr_of.get(team_id)
        for key, role, position, person_kind, staff_kind in SEATS:
            person = wanted[(team_id, role)]
            profile = profiles.get((person["person_kind"], person["person_id"]))
            actual = seat.get(key)
            if profile is None:
                skipped.append({"team": abbr, "role": role, "person": person["name"],
                                "reason": "no coach_madden_profiles row"})
                continue
            wanted_pid = profile.get("presentation_id")
            if wanted_pid is not None and actual == wanted_pid:
                continue                          # the seat already holds Mu's man: nothing to write
            if actual is not None and actual not in wanted_pids:
                releases.append({"action": "release", "pgid": actual})
                freed.add(actual)
                report.append({"team": abbr, "role": role, "action": "release", "pgid": actual,
                               "person": _save_name(save_coaches.get(actual))})
            contract = _contract(cur, team_id, staff_kind, person["person_id"], has_contracts)
            if wanted_pid is not None:
                action = {"action": "assign", "pgid": wanted_pid, "team_index": idx, "position": position}
                if contract:
                    action["contract"] = contract
                assigns.append(action)
                report.append({"team": abbr, "role": role, "action": "assign", "person": person["name"],
                               "pgid": wanted_pid})
            else:
                action = {"action": "create", "key": _coach_key(person_kind, person["person_id"]),
                          "team_index": idx, "position": position,
                          "profile": _profile_payload(profile),
                          "visuals_template": profile.get("visuals_template")}
                if contract:
                    action["contract"] = contract
                creates.append(action)
                report.append({"team": abbr, "role": role, "action": "create", "person": person["name"],
                               "key": action["key"]})

    releases += _retire_actions(cur, snapshot, target_season, profiles, wanted_pids, freed)
    # Releases first, then assigns, then creates: a freed row/seat is reusable and an HC change lands in one write.
    return {"staff": releases + assigns + creates, "skipped": skipped, "report": report}
