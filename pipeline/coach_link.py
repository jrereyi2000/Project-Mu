"""Linking Mu's coaches to the save's records at load (COACH_SAVE_WRITES_PLAN §2.3, U5).

    link_staff(cur, snapshot, season) -> {"linked": n, "unmatched": [...]}

For each team's HC, OC and DC in the save-reader snapshot (`snapshot["staff"]`, reader_version 8), this finds Mu's
person for that team and role in `season` (`coach_tenures` / `coordinator_tenures`), matches on the normalised full
name with team and role as the tie-breakers, and upserts a `coach_madden_profiles` row with `source = 'SAVE'`, the
save record's `PresentationId` and the fields the snapshot carries.

A seat whose names do not match is reported under `"unmatched"`, never guessed: the starting staffs match by design
(REAL_COACH_PROFILES_PLAN), so a miss is a data problem for JR. A free agent in `snapshot["coaches"]` is never linked
to a seat (only the three team seats are iterated). The function is idempotent: a second run finds the same rows and
writes nothing.

An existing `CREATED` row (an invented coach the carousel built, §2.4) is never overwritten or relabelled `SAVE`:
only its `presentation_id` link is recorded, because the headshot pipeline and the portrait batch select `CREATED`
coaches (§2.7/§2.8). Rows absent, or already `SAVE`, are written from the save.

The snapshot's playbook and scheme values are the save's raw 32-bit references into the game's static tables (U2's
`pc_save_reader.mjs`), not Madden enum names, so they are left NULL rather than stored as meaningless numbers in the
VARCHAR name columns; text fields the snapshot does carry (name, age, position, archetype, level, portrait) are
stored as read. It writes nothing outside `coach_madden_profiles`.

`pipeline/load_save.py` calls it after the roster load; it is safe against an older snapshot with no `staff` key.
"""
import unicodedata

FREE_AGENT_TEAM_INDEX = 32
# Madden's short name -> teams.abbreviation where they differ (kept in step with pipeline/load_save.py).
TEAM_ABBR_ALIASES = {"ARI": "AZ"}

# (snapshot staff key, Mu role, Madden StaffPosition, coach_madden_profiles.person_kind)
SEATS = (
    ("hc", "HC", "HeadCoach", "COACH"),
    ("oc", "OC", "OffensiveCoordinator", "COORD"),
    ("dc", "DC", "DefensiveCoordinator", "COORD"),
)


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name):
    """The name comparison key: accent-folded, punctuation-free, suffix-free, lower-cased, single-spaced.

    `engines/staff_names.py` has no normaliser of its own (it seeds names, already plain ASCII and single-spaced),
    so this is the equivalent used for matching a Mu name against the save's FirstName/LastName. Madden and Mu write
    the same man differently often enough to matter: "Pete Carmichael Jr" against "Pete Carmichael Jr." (measured on
    CAREER-MUBASE, 2026-11-10).
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("'", "").replace(".", "").replace("-", " ")
    parts = [p for p in " ".join(text.split()).lower().split(" ") if p]
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


# First names the save and Mu spell differently for the same person. Measured pairs only; a name that is not here
# stays a mismatch rather than being guessed at.
_NICKNAMES = {
    "brad": "bradley", "bradley": "bradley",
    "mike": "michael", "michael": "michael",
    "dave": "david", "david": "david",
    "bob": "robert", "rob": "robert", "robert": "robert",
    "bill": "william", "will": "william", "william": "william",
    "jim": "james", "james": "james",
    "tom": "thomas", "thomas": "thomas",
    "joe": "joseph", "joseph": "joseph",
    "dan": "daniel", "daniel": "daniel",
    "matt": "matthew", "matthew": "matthew",
    "nick": "nicholas", "nicholas": "nicholas",
    "chris": "christopher", "christopher": "christopher",
    "pat": "patrick", "patrick": "patrick",
    "ben": "benjamin", "benjamin": "benjamin",
    "josh": "joshua", "joshua": "joshua",
    "jeff": "jeffrey", "jeffrey": "jeffrey",
    "andrew": "andrew", "andy": "andrew",
    "ken": "kenneth", "kenneth": "kenneth",
    "ron": "ronald", "ronald": "ronald",
    "doug": "douglas", "douglas": "douglas",
    "zach": "zachary", "zack": "zachary", "zachary": "zachary",
    "alex": "alexander", "alexander": "alexander",
    "ed": "edward", "edward": "edward",
    "sean": "shawn", "shawn": "shawn",
    "nate": "nathan", "nathan": "nathan",
    "steve": "steven", "steven": "steven",
    "tony": "anthony", "anthony": "anthony",
    "rick": "richard", "rich": "richard", "richard": "richard",
}


def _one_edit_apart(a, b):
    """True when two short strings differ by at most one insertion, deletion or substitution."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    seen = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1; continue
        if seen:
            return False
        seen = True
        if len(a) == len(b):
            i += 1; j += 1
        else:
            j += 1
    return True


def names_match(save_name, mu_name):
    """Whether two names are the same man: equal after normalisation, the same first name with one of them misspelt
    ("Jonthan Gannon" in the save, "Jonathan Gannon" in Mu), or the same surname with a known nickname form."""
    a, b = normalize_name(save_name), normalize_name(mu_name)
    if not a or not b:
        return False
    if a == b:
        return True
    a_first, _, a_last = a.rpartition(" ")
    b_first, _, b_last = b.rpartition(" ")
    if not a_last or not b_last:
        return False
    alias = _NICKNAMES.get(a_first) is not None and _NICKNAMES.get(a_first) == _NICKNAMES.get(b_first)
    # At most one edit across the two names, and the other half must be exact: never "one typo in each".
    if a_last == b_last:
        return a_first == b_first or alias or _one_edit_apart(a_first, b_first)
    if a_first == b_first:
        return _one_edit_apart(a_last, b_last)
    return False


def _seed(person_kind, person_id):
    """The `coach_key` draw seed (§2.1 `coach-<id>` / `coord-<id>`), so a SAVE row and a CREATED row for the same
    person share one seed."""
    return f"madden-coach:{'coach' if person_kind == 'COACH' else 'coord'}-{person_id}"


def _team_map(cur, snapshot):
    """{team_index: teams.id} for every real team in the snapshot (free-agent index excluded)."""
    cur.execute("SELECT abbreviation, id FROM teams")
    by_abbr = {a: i for a, i in cur.fetchall() if a}
    out = {}
    for t in snapshot.get("teams") or []:
        idx = t.get("team_index")
        if idx is None or idx >= FREE_AGENT_TEAM_INDEX:
            continue
        abbr = TEAM_ABBR_ALIASES.get(t.get("short_name"), t.get("short_name"))
        if abbr in by_abbr:
            out[idx] = by_abbr[abbr]
    return out


def _mu_seats(cur, season):
    """({team_id: (coach_id, name)}, {(team_id, role): (coordinator_id, name)}) for the tenures covering `season`.

    A later-starting tenure wins when several cover the season (the tenure that is live now).
    """
    head = {}
    cur.execute("""SELECT ct.team_id, c.id, c.coach_name
                   FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.start_season DESC, ct.id DESC""", (season, season))
    for team_id, coach_id, name in cur.fetchall():
        head.setdefault(team_id, (coach_id, name))
    coords = {}
    cur.execute("""SELECT ct.team_id, ct.role, c.id, c.coordinator_name
                   FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.role, ct.start_season DESC, ct.id DESC""", (season, season))
    for team_id, role, coordinator_id, name in cur.fetchall():
        coords.setdefault((team_id, role), (coordinator_id, name))
    return head, coords


def _mu_person(head, coords, team_id, role):
    if team_id is None:
        return None
    if role == "HC":
        return head.get(team_id)
    return coords.get((team_id, role))


# Madden's Coach.Archetype enum holds 0 twice: "First" and "OffensiveGuru" (the field's own enum member list, and 68
# of CAREER-MUBASE's 127 coaches sit on it). The snapshot reader reports the first name, so the link stores the
# football meaning rather than the placeholder.
_ARCHETYPE_ALIASES = {"First": "OffensiveGuru"}


def _desired_row(person_kind, person_id, save, seat_position):
    """The `coach_madden_profiles` values read from one save coach record."""
    archetype = str(save.get("archetype") or "")
    return {
        "presentation_id": save.get("pgid"),
        "source": "SAVE",
        "first_name": str(save.get("first_name") or "").strip(),
        "last_name": str(save.get("last_name") or "").strip(),
        "age": int(save.get("age") or 0),
        "position": save.get("position") or seat_position,
        "archetype": _ARCHETYPE_ALIASES.get(archetype, archetype),
        "level": int(save.get("level") or 1),
        "portrait_id": save.get("portrait"),
        "seed": _seed(person_kind, person_id),
    }


_COMPARE = ("presentation_id", "source", "first_name", "last_name", "age", "position", "archetype", "level",
            "portrait_id", "seed")

# Every column `_ensure_created_profile` may take from a built profile (the table's own list, minus the identity and
# the write-back columns, which it sets itself).
_PROFILE_COLUMNS = ("person_kind", "person_id", "source", "presentation_id", "first_name", "last_name", "age",
                    "position", "archetype", "specialty", "level", "years_coaching", "offensive_playbook",
                    "defensive_playbook", "offensive_scheme", "defensive_scheme", "team_philosophy", "personality",
                    "demeanor", "body_type", "height", "weight", "face_shape", "generic_head", "talents", "ratings",
                    "tendencies", "visuals_template", "portrait_id", "portrait_status", "headshot_path", "seed")


def _upsert_profile(cur, person_kind, person_id, desired):
    """Insert or update the linked row; returns True only when it wrote (a rerun with identical values writes none).

    An existing `CREATED` row is an invented coach Mu built (§2.4). Linking must not overwrite his generated fields
    or flip his source to `SAVE`: the headshot pipeline and the portrait batch select `CREATED` coaches (§2.7/§2.8),
    so only the `presentation_id` link is recorded. A `SAVE` row (or a missing one) is written from the save.
    """
    cur.execute(f"""SELECT {', '.join(_COMPARE)}
                    FROM coach_madden_profiles WHERE person_kind = %s AND person_id = %s""",
                (person_kind, person_id))
    row = cur.fetchone()
    if row is not None:
        current = dict(zip(_COMPARE, row))
        if current["source"] == "CREATED":
            if current["presentation_id"] != desired["presentation_id"]:
                cur.execute("UPDATE coach_madden_profiles SET presentation_id = %s "
                            "WHERE person_kind = %s AND person_id = %s",
                            (desired["presentation_id"], person_kind, person_id))
                return True
            return False
        if tuple(row) == tuple(desired[c] for c in _COMPARE):
            return False
    if row is None:
        cols = ("person_kind", "person_id") + _COMPARE
        values = (person_kind, person_id) + tuple(desired[c] for c in _COMPARE)
        cur.execute(f"INSERT INTO coach_madden_profiles ({', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(cols))})", values)
    else:
        sets = ", ".join(f"{c} = %s" for c in _COMPARE)
        cur.execute(f"UPDATE coach_madden_profiles SET {sets} WHERE person_kind = %s AND person_id = %s",
                    tuple(desired[c] for c in _COMPARE) + (person_kind, person_id))
    return True


def link_staff(cur, snapshot, season):
    """Link every save staff seat to Mu's person for `season` (§2.3). See the module docstring."""
    staff = snapshot.get("staff") or []
    if not staff:
        return {"linked": 0, "unmatched": []}
    # A database without the coach-profile migration can still load: nothing to link, nothing reported.
    cur.execute("SELECT to_regclass('coach_madden_profiles') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {"linked": 0, "unmatched": []}

    team_map = _team_map(cur, snapshot)
    save_coaches = {c.get("pgid"): c for c in snapshot.get("coaches") or [] if c.get("pgid") is not None}
    head, coords = _mu_seats(cur, season)
    linked, created, unmatched = 0, 0, []
    for seat in staff:
        idx = seat.get("team_index")
        team_id = team_map.get(idx)
        for key, role, seat_position, person_kind in SEATS:
            pgid = seat.get(key)
            if team_id is None:
                if pgid is not None:
                    unmatched.append({"team_index": idx, "role": role, "save_pgid": pgid,
                                      "reason": "team not in teams table"})
                continue
            person = _mu_person(head, coords, team_id, role)
            if person is None:
                if pgid is not None:
                    unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "save_pgid": pgid,
                                      "reason": "no Mu tenure for the season"})
                continue
            person_id, mu_name = person
            if pgid is None:
                # Mu's man holds the seat and the save's is empty: give Mu's man a profile so the staff write creates
                # him (JR, 2026-11-10). He is still reported as unmatched — that is the link's report, not a skip.
                if _ensure_created_profile(cur, person_kind, person_id, mu_name, seat_position):
                    created += 1
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "reason": "no save coach in the seat"})
                continue
            save = save_coaches.get(pgid)
            if save is None:
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "save_pgid": pgid, "reason": "seat coach missing from coaches[]"})
                continue
            save_name = f"{save.get('first_name') or ''} {save.get('last_name') or ''}"
            if not names_match(save_name, mu_name):
                # A different man in the seat. Mu's real coach wins it: the profile lets the staff write create him,
                # and `plan_staff_edits` releases the man already there (JR, 2026-11-10).
                if _ensure_created_profile(cur, person_kind, person_id, mu_name, seat_position):
                    created += 1
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "save_name": save_name, "save_pgid": pgid, "reason": "name mismatch"})
                continue
            _upsert_profile(cur, person_kind, person_id,
                            _desired_row(person_kind, person_id, save, seat_position))
            linked += 1
    # A coach Madden itself fired is a free agent in the save with his own record, and Mu can hire him later. Link
    # those too, so a poached coordinator is a move rather than a second person (audit follow-up, 2026-11-10). Only an
    # exactly normalised name matches here: a free agent is not pinned to a team or role, so the seat's tie-breakers
    # are gone and a near match would be a guess.
    linked_free = 0
    seated = {seat.get(k) for seat in staff for k in ("hc", "oc", "dc") if seat.get(k) is not None}
    mu_by_name = {}
    for person_kind, people in (("COACH", head.values()), ("COORD", coords.values())):
        for person_id, mu_name in people:
            bucket = mu_by_name.setdefault(normalize_name(mu_name), set())
            bucket.add((person_kind, person_id))
    for pgid, save in save_coaches.items():
        if pgid in seated or save.get("team_index") != 32 or save.get("contract_status") != "FreeAgent":
            continue
        key = normalize_name(f"{save.get('first_name') or ''} {save.get('last_name') or ''}")
        people = sorted(mu_by_name.get(key) or ())
        if len(people) != 1:
            continue                                   # unknown, or two Mu people share the name: leave it alone
        person_kind, person_id = people[0]
        # A man already linked to a seat keeps it: an old duplicate free agent with the same name must not take the
        # link over, or every write swaps the two records (audit, 2026-11-10).
        cur.execute("SELECT presentation_id FROM coach_madden_profiles WHERE person_kind = %s AND person_id = %s",
                    (person_kind, person_id))
        existing = cur.fetchone()
        if existing and existing[0] in seated:
            continue
        if _upsert_profile(cur, person_kind, person_id,
                           _desired_row(person_kind, person_id, save, save.get("position"))):
            linked_free += 1
    # The coaches Mu creates over Madden's made-up ones are starting staff, so their deals must exist from league
    # start: without one they cross Madden's league-year rollover at 0/0/0, which the hiring cycle's own early filing
    # avoids for its hires (audit M-0203). The producer prices them; this only decides when.
    if created:
        from engines import staff_contracts_run                                # local: the link is a loader module
        fired = staff_contracts_run.file_hires(cur, season)
    else:
        fired = []
    return {"linked": linked, "created": created, "linked_free_agents": linked_free,
            "contracts_filed": len(fired), "unmatched": unmatched}


def _ensure_created_profile(cur, person_kind, person_id, mu_name, seat_position):
    """Give a Mu coach who holds a starting seat a `CREATED` profile when he has none.

    JR's decision on the starting staff (2026-11-10): Mu's real coach wins a seat, so Madden's own fictional coach is
    released and Mu's man is created in the save. `plan_staff_edits` can only create a man it has a profile for, so the
    link builds the profile here rather than leaving the seat `skipped` and never written. Returns True when it
    inserted one.
    """
    from engines.coach_madden_profile import build_profile                      # local: the link is a loader module
    cur.execute("SELECT id FROM coach_madden_profiles WHERE person_kind = %s AND person_id = %s",
                (person_kind, person_id))
    if cur.fetchone():
        return False
    coach_key = f"{'coach' if person_kind == 'COACH' else 'coord'}-{person_id}"
    row = build_profile({"name": mu_name, "id": person_id}, "HC" if person_kind == "COACH" else seat_position,
                        slant=None, scheme=None, playbook=None, tendencies={}, sliders={}, background=None,
                        contract=None, seed=_seed(person_kind, person_id))
    row = dict(row, person_kind=person_kind, person_id=person_id, position=seat_position)
    from psycopg.types.json import Jsonb                                   # local: loaders import without psycopg
    columns = [c for c in row if c in _PROFILE_COLUMNS]
    values = tuple(Jsonb(row[c]) if c in ("talents", "ratings", "tendencies") else row[c] for c in columns)
    cur.execute(f"INSERT INTO coach_madden_profiles ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})", values)
    return True
