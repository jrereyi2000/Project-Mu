"""pipeline/coach_link.py: linking the save's starting staff to Mu at load (COACH_SAVE_WRITES_PLAN §4, U5).

Offline: an in-memory SQLite league behind the shared psycopg-like FakeCursor, plus a synthetic reader_version-8
snapshot. No PostgreSQL connection is opened.
"""
import sqlite3

from pipeline import coach_link as cl
from tests.test_inbox_store import FakeCursor

SEASON = 2026

DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT);
CREATE TABLE coaches (id INTEGER PRIMARY KEY, coach_name TEXT);
CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT, team_id INT, start_season INT,
                            end_season INT);
CREATE TABLE coordinators (id INTEGER PRIMARY KEY, coordinator_name TEXT);
CREATE TABLE coordinator_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_id INT, team_id INT, role TEXT,
                                  start_season INT, end_season INT);
CREATE TABLE coach_madden_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_kind TEXT, person_id INT, source TEXT, presentation_id INT,
    first_name TEXT, last_name TEXT, age INT, position TEXT, archetype TEXT, specialty TEXT,
    level INT DEFAULT 1, years_coaching INT,
    offensive_playbook TEXT, defensive_playbook TEXT, offensive_scheme TEXT, defensive_scheme TEXT,
    team_philosophy TEXT, personality TEXT, demeanor TEXT, body_type TEXT, height INT, weight INT,
    face_shape TEXT, generic_head TEXT,
    talents TEXT DEFAULT '[]', ratings TEXT DEFAULT '{}', tendencies TEXT DEFAULT '{}',
    visuals_template INT, portrait_id INT, portrait_status TEXT DEFAULT 'NONE', headshot_path TEXT,
    seed TEXT, written_at TEXT,
    UNIQUE (person_kind, person_id)
);
"""

# (Mu team id, abbreviation, Madden team_index) — CAR and ATL.
TEAMS = ((1, "CAR", 0), (2, "ATL", 1))

# (coach/coordinator id, full name, team id, role) — the starting staff, matching by design (S13).
PEOPLE = (
    (41, "Dave Canales", 1, "HC"),
    (300, "Brad Idzik", 1, "OC"),
    (301, "Ejiro Evero", 1, "DC"),
    (42, "Raheem Morris", 2, "HC"),
    (302, "Tommy Rees", 2, "OC"),
    (303, "Jeff Ulbrich", 2, "DC"),
)

# (Madden team_index, seat key, pgid, save first, save last, portrait)
SEATS = (
    (0, "hc", 100, "Dave", "Canales", 5400),
    (0, "oc", 101, "Brad", "Idzik", 5401),
    (0, "dc", 102, "Ejiro", "Evero", 5402),
    (1, "hc", 200, "Raheem", "Morris", 5403),
    (1, "oc", 201, "Tommy", "Rees", 5404),
    (1, "dc", 202, "Jeff", "Ulbrich", 5405),
)

SAVE_POSITION = {"hc": "HeadCoach", "oc": "OffensiveCoordinator", "dc": "DefensiveCoordinator"}


def _league():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(DDL)
    conn.executemany("INSERT INTO teams (id, abbreviation) VALUES (?, ?)", [(i, a) for i, a, _ in TEAMS])
    for person_id, name, team_id, role in PEOPLE:
        if role == "HC":
            conn.execute("INSERT INTO coaches (id, coach_name) VALUES (?, ?)", (person_id, name))
            conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (?, ?, ?, NULL)",
                         (person_id, team_id, SEASON - 2))
        else:
            conn.execute("INSERT INTO coordinators (id, coordinator_name) VALUES (?, ?)", (person_id, name))
            conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                         "VALUES (?, ?, ?, ?, NULL)", (person_id, team_id, role, SEASON - 2))
    return conn


def _snapshot(overrides=None):
    """A reader_version-8 snapshot: teams[], staff[] and coaches[] for the two-team league."""
    overrides = overrides or {}
    teams = [{"team_index": idx, "row": idx, "short_name": abbr, "long_name": abbr} for _i, abbr, idx in TEAMS]
    staff = []
    for idx, abbr in ((0, "CAR"), (1, "ATL")):
        seats = {key: pgid for ti, key, pgid, *_ in SEATS if ti == idx}
        staff.append({"team_index": idx, "hc": seats["hc"], "oc": seats["oc"], "dc": seats["dc"]})
    coaches = []
    for idx, key, pgid, first, last, portrait in SEATS:
        entry = {"pgid": pgid, "table": 4168, "row": pgid, "first_name": first, "last_name": last,
                 "age": 50, "position": SAVE_POSITION[key], "team_index": idx, "contract_status": "Signed",
                 "contract_length": 4, "contract_salary": 400, "contract_years_remaining": 2,
                 "archetype": "OffensiveGuru", "level": 5, "portrait": portrait,
                 "offensive_playbook": 16384, "defensive_playbook": 16384,
                 "offensive_scheme": 16485, "defensive_scheme": 16485, "is_user_controlled": False}
        entry.update(overrides.get(pgid, {}))
        coaches.append(entry)
    return {"meta": {"game_year": 27, "reader_version": 8}, "teams": teams, "staff": staff, "coaches": coaches}


def _profiles(conn):
    return conn.execute("SELECT person_kind, person_id, source, presentation_id, first_name, last_name, age, "
                        "position, archetype, level, portrait_id, seed FROM coach_madden_profiles "
                        "ORDER BY person_kind, person_id").fetchall()


# ------------------------------------------------------------------------------------- the starting staff ---

def test_the_starting_staff_all_link():
    conn = _league()
    result = cl.link_staff(FakeCursor(conn), _snapshot(), SEASON)
    assert result["linked"] == 6 and result["unmatched"] == []
    rows = _profiles(conn)
    assert len(rows) == 6
    # The two head coaches are COACH rows; every coordinator is a COORD row.
    assert sum(1 for r in rows if r[0] == "COACH") == 2
    assert all(r[2] == "SAVE" for r in rows)
    by_pid = {r[3]: r for r in rows}
    # A linked row carries the save's PresentationId, split name, age, position, archetype, level and portrait.
    dave = by_pid[100]
    assert dave[0] == "COACH" and dave[1] == 41
    assert (dave[4], dave[5], dave[6], dave[7]) == ("Dave", "Canales", 50, "HeadCoach")
    assert (dave[8], dave[9], dave[10]) == ("OffensiveGuru", 5, 5400)
    assert by_pid[101][0] == "COORD" and by_pid[101][1] == 300
    # Opaque playbook/scheme references are left NULL for the name columns; nothing else is written.
    assert conn.execute("SELECT offensive_playbook, offensive_scheme FROM coach_madden_profiles").fetchall() == \
        [(None, None)] * 6


def test_a_name_mismatch_is_reported_not_guessed():
    conn = _league()
    # The save's ATL OC is a different man from Mu's Tommy Rees.
    snap = _snapshot(overrides={201: {"first_name": "Someone", "last_name": "Else"}})
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["linked"] == 5
    mismatches = [u for u in result["unmatched"] if u["reason"] == "name mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["role"] == "OC" and mismatches[0]["save_pgid"] == 201
    assert mismatches[0]["mu_name"] == "Tommy Rees" and mismatches[0]["save_name"] == "Someone Else"
    # The mismatched seat got no profile row; the other five did.
    # 5 linked SAVE rows plus the CREATED row the mismatched seat needs so Mu's man can be written (JR's B4 rule).
    assert conn.execute("SELECT count(*) FROM coach_madden_profiles").fetchone()[0] == 6
    assert conn.execute("SELECT count(*) FROM coach_madden_profiles WHERE source = 'CREATED' "
                        "AND presentation_id IS NULL").fetchone()[0] == 1
    # Mu's man still gets a CREATED profile so the staff write can put him in the seat (JR's B4 rule); the
    # mismatch is reported, and the release of the man already there is the planner's job.
    assert conn.execute("SELECT source, presentation_id FROM coach_madden_profiles"
                        " WHERE person_id = 302").fetchone() == ("CREATED", None)


def test_a_rerun_changes_nothing():
    conn = _league()
    cur = FakeCursor(conn)
    first = cl.link_staff(cur, _snapshot(), SEASON)
    before = _profiles(conn)
    writes_before = sum(1 for s in cur.statements if "coach_madden_profiles" in s and
                        (s.lstrip().upper().startswith("INSERT") or s.lstrip().upper().startswith("UPDATE")))
    second = cl.link_staff(cur, _snapshot(), SEASON)
    writes_after = sum(1 for s in cur.statements if "coach_madden_profiles" in s and
                       (s.lstrip().upper().startswith("INSERT") or s.lstrip().upper().startswith("UPDATE")))
    assert first == second
    assert _profiles(conn) == before
    assert writes_before == 6 and writes_after == writes_before       # the rerun wrote no rows


def test_a_free_agent_coach_in_the_snapshot_is_not_linked_to_a_seat():
    conn = _league()
    snap = _snapshot()
    # A free agent (TeamIndex 32) in coaches[] has no seat and no Mu tenure: it must be neither linked nor guessed.
    snap["coaches"].append({"pgid": 999, "table": 4168, "row": 999, "first_name": "Free", "last_name": "Agent",
                            "age": 55, "position": "HeadCoach", "team_index": 32, "contract_status": "FreeAgent",
                            "archetype": "OffensiveGuru", "level": 3, "portrait": 5999,
                            "is_user_controlled": False})
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["linked"] == 6
    assert not any(u.get("save_pgid") == 999 for u in result["unmatched"])
    assert conn.execute("SELECT count(*) FROM coach_madden_profiles WHERE presentation_id = 999").fetchone()[0] == 0


def test_an_empty_seat_is_reported_and_an_unknown_team_is_reported():
    conn = _league()
    snap = _snapshot()
    snap["staff"][0]["hc"] = None
    snap["staff"].append({"team_index": 31, "hc": 777, "oc": None, "dc": None})   # not in the teams table
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["linked"] == 5
    reasons = {(u.get("team_index"), u.get("role"), u["reason"]) for u in result["unmatched"]}
    assert (0, "HC", "no save coach in the seat") in reasons
    assert (31, "HC", "team not in teams table") in reasons


def test_a_snapshot_without_staff_links_nothing():
    conn = _league()
    assert cl.link_staff(FakeCursor(conn), {"teams": [], "coaches": []}, SEASON) == {"linked": 0, "unmatched": []}


def test_a_created_profile_is_not_overwritten_by_the_link():
    """An invented coach's CREATED row keeps his generated fields and marker; only the PresentationId is recorded.
    The headshot pipeline and the portrait batch select CREATED coaches, so flipping it to SAVE would lose him."""
    conn = _league()
    conn.execute("""INSERT INTO coach_madden_profiles
                    (person_kind, person_id, source, presentation_id, first_name, last_name, age, position, archetype,
                     level, portrait_id, seed)
                    VALUES ('COORD', 302, 'CREATED', NULL, 'Generated', 'Name', 41, 'OffensiveCoordinator',
                            'DevelopmentWizard', 7, 0, 'madden-coach:coord-302')""")
    cur = FakeCursor(conn)
    result = cl.link_staff(cur, _snapshot(), SEASON)
    assert result["linked"] == 6 and result["unmatched"] == []
    row = conn.execute("SELECT source, presentation_id, first_name, last_name, archetype, level "
                       "FROM coach_madden_profiles WHERE person_kind = 'COORD' AND person_id = 302").fetchone()
    assert row == ("CREATED", 201, "Generated", "Name", "DevelopmentWizard", 7)
    # A rerun records nothing more.
    writes_before = sum(1 for s in cur.statements if s.lstrip().upper().startswith(("INSERT", "UPDATE"))
                        and "coach_madden_profiles" in s)
    cl.link_staff(cur, _snapshot(), SEASON)
    writes_after = sum(1 for s in cur.statements if s.lstrip().upper().startswith(("INSERT", "UPDATE"))
                       and "coach_madden_profiles" in s)
    assert writes_after == writes_before


# ---- name matching (measured pairs, CAREER-MUBASE against a fresh 2026 seed, 2026-11-10) ----
def test_normalize_drops_punctuation_and_suffixes():
    assert cl.normalize_name("Pete Carmichael Jr.") == "pete carmichael"
    assert cl.normalize_name("Pete Carmichael Jr") == "pete carmichael"
    assert cl.normalize_name("Chris O'Leary") == "chris oleary"
    assert cl.normalize_name("A.J.  Smith") == "aj smith"


def test_names_match_across_the_spellings_the_save_and_mu_use():
    pairs = [
        ("Pete Carmichael Jr", "Pete Carmichael Jr."),      # suffix period
        ("Bradley Idzik", "Brad Idzik"),                    # nickname
        ("Jonthan Gannon", "Jonathan Gannon"),              # one misspelt first name
        ("Brian Angelicho", "Brian Angelichio"),            # one misspelt surname, same first name
        ("Nate Scheelhaase", "Nathan Scheelhaase"),         # nickname
        ("mike vrabel", "Mike Vrabel"),                     # case and spacing
    ]
    for save_name, mu_name in pairs:
        assert cl.names_match(save_name, mu_name), (save_name, mu_name)


def test_names_do_not_match_different_people():
    # The genuine 2026 mismatches: Madden ships a fictional coach where Mu has the real man.
    for save_name, mu_name in [("Orson Grizzly", "Press Taylor"), ("Bill Summers", "Vic Fangio"),
                               ("Sean Mannion", "Kevin Patullo"), ("Andy Reid", "Ron Leonard")]:
        assert not cl.names_match(save_name, mu_name), (save_name, mu_name)


def test_a_free_agent_coach_mu_could_hire_is_linked_not_duplicated():
    """A coach Madden fired is a free agent in the save with his own record. If Mu hires him later, the staff write
    must move that record rather than create a second person (audit follow-up, 2026-11-10)."""
    conn = _league()
    snap = _snapshot()
    save = [c for c in snap["coaches"] if c["pgid"] == 201][0]
    save.update({"team_index": 32, "contract_status": "FreeAgent"})       # Madden fired him; the seat is empty
    for seat in snap["staff"]:
        if seat.get("oc") == 201:
            seat["oc"] = None
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["linked_free_agents"] == 1, result
    # The save's pgid 201 is Mu's coordinator 302. He keeps the CREATED marker his empty seat gave him (the headshot
    # pipeline selects CREATED coaches), but the recorded PresentationId is what matters: a later hire is an assign.
    row = conn.execute("SELECT source, presentation_id FROM coach_madden_profiles"
                       " WHERE person_kind = 'COORD' AND person_id = 302").fetchone()
    assert row == ("CREATED", 201), row


def test_a_duplicate_free_agent_does_not_take_over_a_seated_link():
    """An old duplicate free agent with a linked coach's name must not steal the link, or every write swaps two
    records (audit, 2026-11-10)."""
    conn = _league()
    snap = _snapshot()
    # Mu's ATL OC is linked to his seat; the save also carries a stale free-agent copy of the same name.
    cl.link_staff(FakeCursor(conn), snap, SEASON)
    seated = [c for c in snap["coaches"] if c["pgid"] == 201][0]
    dup = dict(seated, pgid=999, team_index=32, contract_status="FreeAgent")
    snap["coaches"].append(dup)
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["linked_free_agents"] == 0, result
    row = conn.execute("SELECT presentation_id FROM coach_madden_profiles"
                       " WHERE person_kind = 'COORD' AND person_id = 302").fetchone()
    assert row == (201,), row


def test_the_link_files_contracts_even_when_it_created_nothing(monkeypatch):
    """F2: a save whose seats all link still starts with staff who have no live contract row, so filing must not
    depend on the load having created a profile (audit, 2026-11-10)."""
    calls = []
    from engines import staff_contracts_run
    monkeypatch.setattr(staff_contracts_run, "file_hires",
                        lambda cur, season, **kw: calls.append((season, kw)) or [])
    conn = _league()
    snap = _snapshot()                                   # every seat matches: nothing is created
    result = cl.link_staff(FakeCursor(conn), snap, SEASON)
    assert result["created"] == 0
    assert len(calls) == 1 and calls[0][0] == SEASON, calls
    assert calls[0][1]["starting_only"] is False
    # the save's contract years go through for the seated coaches (JR's F1 rule)
    assert calls[0][1]["years_from"], calls


def test_the_save_sets_a_starting_coachs_contract_years(monkeypatch):
    """JR's F1: Mu prices a starting coach, the save sets the term. The link passes the save's two numbers through,
    and a coach Mu invents has no save record, so he is absent and the producer's tenure-start rule applies."""
    calls = []
    from engines import staff_contracts_run
    monkeypatch.setattr(staff_contracts_run, "file_hires",
                        lambda cur, season, **kw: calls.append(kw) or [])
    conn = _league()
    snap = _snapshot()
    snap["coaches"][0]["contract_length"], snap["coaches"][0]["contract_years_remaining"] = 5, 1
    cl.link_staff(FakeCursor(conn), snap, SEASON)
    years = calls[0]["years_from"]
    assert years[(1, "HC", 41)] == (5, 1), years          # CAR's head coach, the save's term
    assert all(v[0] == 4 and v[1] == 2 for k, v in years.items() if k != (1, "HC", 41))
