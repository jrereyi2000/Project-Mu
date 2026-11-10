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


def normalize_name(name):
    """The name comparison key: accent-folded, lower-cased and whitespace-collapsed.

    `engines/staff_names.py` has no normaliser of its own (it seeds names, already plain ASCII and single-spaced),
    so this is the equivalent used for matching a Mu name against the save's FirstName/LastName.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split()).lower()


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


def _desired_row(person_kind, person_id, save, seat_position):
    """The `coach_madden_profiles` values read from one save coach record."""
    return {
        "presentation_id": save.get("pgid"),
        "source": "SAVE",
        "first_name": str(save.get("first_name") or "").strip(),
        "last_name": str(save.get("last_name") or "").strip(),
        "age": int(save.get("age") or 0),
        "position": save.get("position") or seat_position,
        "archetype": str(save.get("archetype") or ""),
        "level": int(save.get("level") or 1),
        "portrait_id": save.get("portrait"),
        "seed": _seed(person_kind, person_id),
    }


_COMPARE = ("presentation_id", "source", "first_name", "last_name", "age", "position", "archetype", "level",
            "portrait_id", "seed")


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
    linked, unmatched = 0, []
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
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "reason": "no save coach in the seat"})
                continue
            save = save_coaches.get(pgid)
            if save is None:
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "save_pgid": pgid, "reason": "seat coach missing from coaches[]"})
                continue
            save_name = f"{save.get('first_name') or ''} {save.get('last_name') or ''}"
            if normalize_name(save_name) != normalize_name(mu_name):
                unmatched.append({"team_index": idx, "team_id": team_id, "role": role, "mu_name": mu_name,
                                  "save_name": save_name, "save_pgid": pgid, "reason": "name mismatch"})
                continue
            _upsert_profile(cur, person_kind, person_id,
                            _desired_row(person_kind, person_id, save, seat_position))
            linked += 1
    return {"linked": linked, "unmatched": unmatched}
