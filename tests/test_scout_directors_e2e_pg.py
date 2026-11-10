"""Scouting directors end to end on PostgreSQL (CLOSE_OUT_CHECKLIST P1-11; PROJECT_SPEC §2.18).

The audit asked for this twice (M-0036, M-0043): the loader and the forwarding seams are pinned offline in
tests/test_scout_directors.py and tests/test_scout_director_wiring.py, but nothing had ever driven a *reviewed*
`staff:scout` receipt through `step_scouting` into real directors, team report rows and board leans on a database.

The test builds a throwaway schema from `db/init_db.sql` inside INBOX_SAVEPOINT_TEST_DSN's database (a `mu_scratch_*`
database or madden_test, per tests/conftest.py) and drops it afterwards. It never writes madden_mu2026, and the
schema isolates every object it creates, so nothing in the shared database is touched.
"""
import pathlib
import uuid

import pytest

from tests.conftest import SAVEPOINT_TEST_DSN_ENV, savepoint_dsn

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT_DB = ROOT / "db" / "init_db.sql"
SEASON, DRAFT_SEASON = 2026, 2027
LEAGUE = 12345                      # league_state.madden_league_id: the universe's league_id, stringified
TEAMS = [(1, "ATL", "Atlanta", "Falcons", "NFC", "South"), (2, "BUF", "Buffalo", "Bills", "AFC", "East")]
PROSPECTS = [(i, name, pos) for i, (name, pos) in enumerate(
    [("Abe Arm", "QB"), ("Bo Back", "HB"), ("Cy Catch", "WR"), ("Dee Edge", "LEDG"),
     ("Eli End", "TE"), ("Fay Front", "DT"), ("Gus Guard", "C"), ("Hal Hook", "CB")], start=1)]

STAFF_SCOUT = [{
    "role": "scout",
    "source_person_id": "00000000-0000-0000-0000-00000000d1ec",
    "creation_receipt": {"reviewer": "JR", "reason": "director fixture", "reviewed_at": "2026-11-04"},
}]


@pytest.fixture
def league(monkeypatch):
    """A psycopg connection inside a throwaway schema built from db/init_db.sql, holding one small franchise."""
    dsn = savepoint_dsn()
    if not dsn:
        pytest.skip(f"set {SAVEPOINT_TEST_DSN_ENV} to madden_test or a mu_scratch_* database")
    import psycopg

    schema = "scout_director_e2e_" + uuid.uuid4().hex[:10]
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        conn.execute(INIT_DB.read_text())
        _seed_franchise(conn)
        # step_scouting opens its own connection from pipeline.load_capture.DSN: give it the same schema.
        from engines import gm_llm
        from pipeline import load_capture
        monkeypatch.setattr(load_capture, "DSN", dsn + f" options='-c search_path={schema}'")
        monkeypatch.setattr(gm_llm, "make_llm", lambda label=None, **kw: None)   # never call a model here
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def _seed_franchise(conn):
    conn.execute("""INSERT INTO teams (id, name, city, abbreviation, conference, division) VALUES (%s, %s, %s, %s,
                    %s, %s)""", TEAMS[0])
    conn.execute("""INSERT INTO teams (id, name, city, abbreviation, conference, division) VALUES (%s, %s, %s, %s,
                    %s, %s)""", TEAMS[1])
    conn.execute("INSERT INTO league_state (id, current_season, current_week, madden_league_id) VALUES (1, %s, 5, %s)",
                 (SEASON, LEAGUE))
    for tid, abbr in ((1, "ATL"), (2, "BUF")):
        conn.execute("""INSERT INTO gm_profiles (team_id, gm_name, bio, voice, start_season, risk_appetite,
                        aggressiveness, pick_affinity, youth_bias, position_bias, seed)
                        VALUES (%s, %s, 'test GM', 'plain', %s, 0.5, 0.5, 1.0, 0.5, '{}', %s)""",
                     (tid, f"{abbr} GM", SEASON, f"gm:{abbr}"))
    for pid, tid in ((11, 1), (12, 2)):
        conn.execute("INSERT INTO players (id, player_name, full_name) VALUES (%s, %s, %s)",
                     (pid, f"QB {pid}", f"Quarterback {pid}"))
        conn.execute("""INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating, source)
                        VALUES (%s, %s, %s, 'QB', 27, %s, 'SAVE')""", (pid, tid, SEASON, 80 - 5 * (pid - 11)))
    for pick, name, pos in PROSPECTS:
        conn.execute("""INSERT INTO draft_prospects (draft_season, projected_pick, full_name, position, college,
                        height_in, weight, age, ovr)
                        VALUES (%s, %s, %s, %s, 'Ohio State', 74, 220, 22, %s)""",
                     (DRAFT_SEASON, pick, name, pos, 80 - pick))
    # load_save allocates the class's permanent prospect IDs on every load; a report edition needs them.
    from engines import prospect_ids, scouting_value as sv
    prospect_ids.ensure_prospect_ids(
        conn.cursor(), DRAFT_SEASON,
        [{"prospect_key": sv.prospect_key(DRAFT_SEASON, name, pos), "pgid": None} for _, name, pos in PROSPECTS],
        "fixture")


def _reviewed_manifest():
    """A reviewed lifecycle manifest whose classifications carry a generated `staff:scout` receipt."""
    from personality import lifecycle as L
    from tests.test_personality_lifecycle import START, classes, manifest_for, receipt, snapshot
    from tests.test_personality_lifecycle_store import review

    snap = snapshot(league=str(LEAGUE))
    uni = L.new_universe(LEAGUE, L.snapshot_sha256(snap), SEASON)
    manifest = manifest_for(snap, cls=classes(receipt("staff:scout")), staff=STAFF_SCOUT, uni=uni,
                            cutoff=START)
    return manifest, review(manifest)


def _apply(conn):
    from personality import lifecycle_store as S
    manifest, review_receipt = _reviewed_manifest()
    with conn.transaction():                       # apply_lifecycle takes savepoints: it needs a real transaction
        S.apply_lifecycle(conn.cursor(), manifest, expected_hash=manifest["manifest_sha256"],
                          review_receipt=review_receipt)
    conn.execute("""INSERT INTO personality_feature_modes (league_id, feature, mode, version, set_by)
                    VALUES (%s, 'scout_directors', 'active', 'fixture', 'test')""", (LEAGUE,))
    return manifest


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def test_no_reviewed_receipt_stays_inert(league):
    """The same league without an applied batch runs exactly as before: no directors, no team rows."""
    from engines import scouting_engine as se
    from pipeline import weekly_capture as wc

    cur = league.cursor()
    assert se.reviewed_scout_context(cur, log=lambda m: None) is None
    assert wc.step_scouting(SEASON)
    assert _count(league, "SELECT count(*) FROM scout_directors") == 0
    assert _count(league, "SELECT count(*) FROM scout_reports WHERE team_id IS NOT NULL") == 0


def test_a_rewrite_failure_does_not_take_the_stored_steps_with_it(league, monkeypatch):
    """Chunk-2 B8 (M-0023): step_scouting commits the steps before the LLM rewrite. Only a PostgreSQL server error
    aborts the transaction — a Python exception does not — which is why this test needs a real database."""
    from engines import scouting_reports
    from pipeline import weekly_capture as wc

    def boom(cur, *a, **k):
        cur.execute("SELECT no_such_column FROM scout_reports")

    monkeypatch.setattr(scouting_reports, "rewrite", boom)
    stored = wc.step_scouting(SEASON)

    assert stored
    assert _count(league, "SELECT count(*) FROM scout_runs") == len(stored)
    assert _count(league, "SELECT count(*) FROM scout_events") > 0


def test_a_reviewed_receipt_drives_directors_team_rows_and_board_leans(league):
    """P1-11 end to end: the applied batch's staff:scout receipt reaches catch_up -> step_scouting, and with the
    feature active each team gets a durable director, its own voiced report rows, and a leaned board."""
    from pipeline import weekly_capture as wc

    manifest = _apply(league)
    stored = wc.step_scouting(SEASON)

    assert stored                                              # the step was stored, not skipped
    assert _count(league, "SELECT count(*) FROM scout_directors") == len(TEAMS)
    persona_ids = {r[0] for r in league.execute("SELECT persona_id FROM scout_directors").fetchall()}
    assert all(persona_ids) and all(p.startswith("persona:") or p for p in persona_ids)

    # person rows: the fixture's reviewed scout + one director per team, all under the same receipt
    assert _count(league, "SELECT count(*) FROM persona_lifecycle_people WHERE role = 'scout'") == 1 + len(TEAMS)

    team_rows = league.execute("""SELECT team_id, scout_id, voice FROM scout_reports
                                  WHERE team_id IS NOT NULL ORDER BY team_id""").fetchall()
    assert {r[0] for r in team_rows} == {t[0] for t in TEAMS}         # every team has its own private rows
    assert all(r[1] and r[2] for r in team_rows)                      # each carries his scout_id and a lens
    assert _count(league, "SELECT count(*) FROM scout_reports WHERE team_id IS NULL") > 0   # the department row

    # the board leans: a team's stored grade differs from the consensus for at least one prospect
    leaned = league.execute("""SELECT count(*) FROM scout_team_boards b JOIN scout_consensus c
                                 ON c.draft_season = b.draft_season AND c.step = b.step
                                AND c.prospect_key = b.prospect_key
                                WHERE b.draft_season = %s AND b.team_grade <> c.grade""", (DRAFT_SEASON,)).fetchone()[0]
    assert leaned > 0
    assert manifest["classifications"]["receipts"][-1]["scope"] == "staff:scout"
