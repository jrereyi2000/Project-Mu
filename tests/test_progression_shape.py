"""The stored league shape season and the live projection of leavers and the coming draft (board M-0414).

Real PostgreSQL tests against three scratch copies (never a live database): a 2026-era league templated from
`madden_tune_p2g` with the pending migrations applied (the first league to reach an offseason), the frozen 2036
archive templated from `madden_2036` **with `2027_02_26_league_shape_season.sql` applied to the copy only** (the
real archive stays unmigrated), and a fresh `db/init_db.sql` build that the clean seed fills. They are skipped
unless the scratch databases exist; set the DSNs below to point them elsewhere.

1. `engines/progression.py` calibrated against the module constant 2036 in a 2026 league: the target was empty, so
   every offseason picked development 0.0 and star -1.00 (HIGH).
2. The live projection dropped the leavers and the coming rookie class, so `class_size` was always 0 (MEDIUM).
3. The exit/rookie draw is keyed on (the season being projected or replayed, seed): the live engine passes its
   target season with seed 0, the fit each replayed season with its own seed (JR, board M-0441/M-0455). A NULL or
   missing `league_state.shape_season` refuses to run; the clean seed writes the start season.
4. The league term calibrates on every staying player and every rookie, not on a 400-player stride sample (JR
   2026-11-12, P3-04b; board M-0672). This file pins the full-roster pair on both scratch leagues.
"""
import os
import random

import psycopg
import pytest

from engines import progression as P
from engines import progression_checkpoint as cp
from engines import progression_save as psave
from engines import progression_state as state
from engines.tuning import progression_fit as F
from pipeline.load_save import DEV_TRAITS, POSITIONS
from tests.test_form_engine import DOC

SCRATCH_DSN = os.environ.get(
    "MADDEN_SHAPE_SCRATCH_DSN",
    "host=localhost dbname=mu_scratch_builder_8_league_shape_test user=postgres password=postgres")
ARCHIVE_DSN = os.environ.get(
    "MADDEN_SHAPE_2036_DSN",
    "host=localhost dbname=mu_scratch_builder_8_shape2036_test user=postgres password=postgres")
INIT_DSN = os.environ.get(
    "MADDEN_SHAPE_INIT_DSN",
    "host=localhost dbname=mu_scratch_builder_8_shapefix_init_test user=postgres password=postgres")


def _is_scratch(dsn):
    return "mu_scratch_" in dsn


def _connect(dsn):
    if not _is_scratch(dsn):
        pytest.skip("the league-shape tests only run against mu_scratch_* databases")
    try:
        return psycopg.connect(dsn)
    except psycopg.OperationalError as exc:
        pytest.skip(f"scratch database not available ({exc})")


@pytest.fixture(scope="module")
def conn():
    connection = _connect(SCRATCH_DSN)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def archive():
    connection = _connect(ARCHIVE_DSN)
    try:
        yield connection
    finally:
        connection.close()


def _target(conn, season):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*), count(*) FILTER (WHERE overall_rating >= 90), avg(overall_rating)
            FROM player_seasons WHERE season = %s AND overall_rating IS NOT NULL AND position != 'DEF'
        """, (season,))
        n, n90, mean = cur.fetchone()
    return {"players": n, "count_90_plus": n90, "avg_ovr": float(mean or 0)}


def _live(conn, season):
    logs = []
    try:
        result = P.run_progression(conn, season, commit=False, dry_run=True, log=logs.append)
    finally:
        conn.rollback()
    return result, logs


def _fit_league(conn, season):
    """The fit's replay league built from the same `player_seasons` rows the live engine progresses (all players,
    including free agents), so the two see one roster."""
    from collections import defaultdict
    with conn.cursor() as cur:
        perf = P.performance_scores(cur, season)
        cur.execute("SELECT player_id FROM injuries WHERE season = %s GROUP BY 1", (season,))
        injured = {row[0] for row in cur.fetchall()}
        cur.execute("""
            SELECT ps.player_id, ps.team_id, ps.position, ps.age, ps.overall_rating, ps.dev_trait,
                   ps.years_of_experience, sg.games_played
            FROM player_seasons ps
            LEFT JOIN season_stat_general sg ON sg.player_id = ps.player_id AND sg.season = ps.season
            WHERE ps.season = %s AND ps.overall_rating IS NOT NULL AND ps.position != 'DEF'
        """, (season,))
        rows = cur.fetchall()
    players, n_injured = [], 0
    for pid, team, pos, age, ovr, dev, years_pro, games in rows:
        n_injured += pid in injured
        players.append({"pid": int(pid), "team": team or 0, "position": pos,
                        "engine_position": POSITIONS.get(pos, pos), "group": F.perf_group(pos) or "OTHER",
                        "age": int(age or 25), "ovr": int(ovr),
                        "dev": DEV_TRAITS.get(dev or "Normal", "Normal"),
                        "years_pro": int(years_pro or 0), "last_games": games,
                        "last_prod": (perf.get(pid) or {}).get("raw")})
    players.sort(key=lambda p: p["pid"])
    starters = F._starters(players)
    role = {"starter": [], "backup": []}
    for p in players:
        if p["last_games"] is not None:
            role["starter" if starters[p["pid"]] else "backup"].append(p["last_games"])
    games = {"starter": (sum(role["starter"]) / len(role["starter"])
                         if len(role["starter"]) >= F.ROLE_MIN_SAMPLE else F.STARTER_GAMES),
             "backup": (sum(role["backup"]) / len(role["backup"])
                        if len(role["backup"]) >= F.ROLE_MIN_SAMPLE else F.BACKUP_GAMES)}
    by_group = defaultdict(list)
    for p in players:
        by_group[P.shape_group(p["engine_position"])].append(p["ovr"])
    rookies = [{k: p[k] for k in ("position", "engine_position", "group", "age", "ovr", "dev")}
               for p in players if p["years_pro"] == 0]
    return {"_league": True, "players": players, "rookies": rookies, "calendar_year": season,
            "last_year": season, "injury_share": n_injured / len(players),
            "games": {k: round(v, 2) for k, v in games.items()},
            "cut_line": {g: F._quantile(v, F.CUT_QUANTILE) for g, v in by_group.items()},
            "shape": F.league_shape(players)}


@pytest.mark.integration
def test_2026_league_holds_its_start_shape(conn):
    """HIGH: a league bootstrapped into 2026 keeps 2026's shape, not the absent 2036 one."""
    with conn.cursor() as cur:
        assert P.league_shape_season(cur) == 2026
    result, logs = _live(conn, 2027)
    shape = result["league_shape"]
    target = _target(conn, 2026)
    assert shape["season"] == 2026
    assert shape["target"]["players"] == target["players"] > 0
    assert shape["target"]["count_90_plus"] == target["count_90_plus"]
    assert shape["target"]["avg_ovr"] == pytest.approx(target["avg_ovr"])
    assert (target["players"], target["count_90_plus"]) == (3236, 71)
    assert target["avg_ovr"] == pytest.approx(70.31, abs=0.01)
    # The old code picked (0.0, -1.0) because the target was empty; never that pair again.
    assert shape["terms"] != (0.0, -1.0)
    assert shape["terms"][0] > 0
    # MEDIUM: the projection now models leavers and the coming rookie class.
    assert shape["leavers"] > 0
    assert shape["class_size"] > 0
    assert any(f"leavers={shape['leavers']} class_size={shape['class_size']}" in line for line in logs)


@pytest.mark.integration
def test_live_terms_match_the_fit_first_season(conn):
    """The live engine and the offline fit calibrate the same league the same way.

    Both now calibrate on the whole league (P3-04b), so the development term matches. The star term cannot be
    identical: the fit's `_replay` has no Phase C2 inputs (wear, mileage, lasting damage), so on this copy its star
    term is 0.0, which would leave the real 2027 league short of the target's 71 players at 90+. The live term must
    instead be the one that holds the shape; that is what is asserted.
    """
    live, _ = _live(conn, 2027)
    live_terms = live["league_shape"]["terms"]
    fit = F.replay(_fit_league(conn, 2026), seasons=1, seed=0)
    fit_season = fit["seasons"][0]
    fit_terms = (fit_season["league_term"], fit_season["star_term"])
    # Reproduce the fit number (the verdict's §1 reports +0.5 / 0 for the save snapshot; this DB-built league is
    # +1.0 / 0.0 on the full roster after the draw was keyed on (season, seed)) and report it: the live development
    # term is within one calibration step (0.5).
    assert fit_terms == (1.0, 0.0)
    assert abs(live_terms[0] - fit_terms[0]) <= 0.5
    assert live_terms[1] != fit_terms[1]
    new = [row["new_ovr"] for row in live["results"]]
    # The calibrated term holds the projected shape (the whole roster the engine calibrates on) and the full
    # roster's mean.
    projected = live["league_shape"]["projected"]["count_90_plus"]
    assert abs(projected - live["league_shape"]["target"]["count_90_plus"]) <= 0.05 * live["league_shape"]["target"]["count_90_plus"]
    assert abs(sum(new) / len(new) - live["league_shape"]["target"]["avg_ovr"]) <= 0.25


@pytest.mark.integration
def test_live_calibrates_the_league_term_on_the_full_roster(conn):
    """P3-04b (JR 2026-11-12, board M-0672): the live engine fits the league term on every staying player and every
    rookie, not on the 400-player stride sample it used before.

    On this 2026 league the two pools give different pairs: the stride sample (405 players, the old path) picks
    star +1.5, the full roster (3,236) picks +1.25. The term is then applied to the whole roster either way, which
    is why the pool, not just the pair, is pinned here."""
    live, logs = _live(conn, 2027)
    shape = live["league_shape"]
    roster = len(live["results"])
    assert shape["calibrated"] == roster == 3236
    assert shape["calibrated"] == roster - shape["leavers"] + shape["class_size"]
    assert shape["terms"] == (1.0, 1.25)
    assert any(f"calibrated={roster}" in line for line in logs)


@pytest.mark.integration
def test_empty_shape_target_never_calibrates(conn):
    """An empty target (or empty rookie pool) raises instead of quietly choosing (0.0, -1.0)."""
    with pytest.raises(ValueError, match="empty target"):
        P.calibrate_league_term([], [], {"players": 0, "count_90_plus": 0, "avg_ovr": 0.0},
                                lambda item, dev, star: 70)
    with pytest.raises(ValueError, match="no rookies"):
        P.rookie_class({"rookies": [], "devs": {"Normal": 1}}, 3, random.Random(0))
    # A league whose stored shape season has no rows: the managed path must raise, never calibrate to (0, -1).
    with conn.cursor() as cur:
        cur.execute("UPDATE league_state SET shape_season = 1999")
    try:
        with pytest.raises(ValueError, match="no player_seasons rows for shape season 1999"):
            P.run_progression(conn, 2027, commit=False, dry_run=True, log=lambda *_: None)
    finally:
        conn.rollback()


def _managed_snapshot(conn):
    """A save-shaped snapshot of the players whose progression state the managed pass tracks (for
    `prepare_offseason_pass`)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.player_id, p.madden_pgid, ps.position, ps.dev_trait, s.base_ratings
            FROM progression_player_state s
            JOIN players p ON p.id = s.player_id
            JOIN player_seasons ps ON ps.player_id = s.player_id AND ps.season = 2027
            WHERE p.madden_pgid IS NOT NULL AND ps.position != 'DEF'
        """)
        tracked = cur.fetchall()
    players = []
    for _, pgid, position, dev, ratings in tracked:
        fields = psave.ovr_fields(DOC, position, ratings, [], None)
        if not fields:
            continue
        players.append({"pgid": pgid, "position": position, "ratings": ratings, "confidence": 50,
                        "archetype_grades": [fields.get(f"OverallGrade{i}", 0) for i in range(5)],
                        "dev_trait": dev or "Normal", "traits": {}, "overall": fields["OverallRating"],
                        "archetype": fields["PlayerType"]})
    return {"players": players, "league_settings": {}}


@pytest.mark.integration
def test_managed_offseason_carries_the_stored_terms(conn):
    """No monkeypatched progression: the prepared 2028 annual rows carry the calibrated terms."""
    snapshot = _managed_snapshot(conn)
    try:
        prepared = cp.prepare_offseason_pass(conn.cursor(), snapshot, 2028,
                                             source_fingerprint="builder-8-shape-test")
        rows = []
        for event_id in prepared["event_ids"]:
            event = state._load_event(conn.cursor(), event_id)
            if event["metadata"].get("annual_result"):
                rows.append(event["metadata"]["annual_result"])
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.player_id, h.new_ovr, ps.age
                FROM player_progression_history h
                JOIN player_seasons ps ON ps.player_id = h.player_id AND ps.season = 2027
                WHERE h.season = 2027
            """)
            info = {pid: (ovr, age) for pid, ovr, age in cur.fetchall()}
    finally:
        conn.rollback()
    assert rows
    stars = [row for row in rows
             if info.get(row["player_id"]) and info[row["player_id"]][0] >= 86 and info[row["player_id"]][1] <= 32]
    assert stars, "no star-eligible player was prepared"
    for row in stars:
        # league_term includes upkeep; subtracting it exposes the stored star term. It is +1.0, never the bug's -1.00.
        assert abs((row["league_term"] - row["upkeep_term"]) + 1.0) > 0.02, row["player_id"]
    young = [row for row in rows
             if info.get(row["player_id"]) and info[row["player_id"]][0] < 86
             and info[row["player_id"]][1] <= 25 and row["league_term"] - row["upkeep_term"] > 0]
    assert len(young) > 100, "young sub-star players did not carry the development term"


@pytest.mark.integration
def test_2036_archive_keeps_its_shape(archive):
    """The 2036 archive still holds 2036; its terms change because leavers and the draft are now modelled.

    Run against a scratch copy of `madden_2036` with `2027_02_26_league_shape_season.sql` applied (the real archive
    stays unmigrated, JR's ruling). The draw is keyed on the target season (2037) and seed 0.
    """
    with archive.cursor() as cur:
        assert P.league_shape_season(cur) == 2036
    logs = []
    try:
        result = P.run_progression(archive, 2037, commit=False, dry_run=True, log=logs.append)
    finally:
        archive.rollback()
    shape = result["league_shape"]
    target = _target(archive, 2036)
    assert shape["season"] == 2036
    assert (target["players"], target["count_90_plus"]) == (1706, 144)
    assert target["avg_ovr"] == pytest.approx(78.13, abs=0.01)
    assert shape["target"]["avg_ovr"] == pytest.approx(target["avg_ovr"])
    assert shape["leavers"] > 0 and shape["class_size"] > 0
    # The draw is keyed on the target season and 0, and the term calibrates on the full roster (P3-04b), so the
    # terms move from the old (+1.5, +0.75) stride sample.
    assert shape["terms"] == (4.0, 1.25)
    assert shape["calibrated"] == target["players"]


def _shape_spy(calls):
    """Replace the shared step with a recorder that keeps each call's leaver pids and rookie (ovr, position) list."""
    original = P.shape_exits_and_rookies

    def spy(*args, **kwargs):
        keeping, leaving, rookies = original(*args, **kwargs)
        calls.append({"leavers": [p["pid"] for p in leaving],
                      "rookies": [(r["ovr"], r["position"]) for r in rookies]})
        return keeping, leaving, rookies

    return original, spy


@pytest.mark.integration
def test_the_fit_exit_draw_varies_by_seed(conn):
    """F1(a): the fit's year-1 exits are keyed on its seed, so seeds 0 and 1 draw different leavers.

    Before the fix both seeds keyed the draw on the same season and drew an identical 398-player set.
    """
    league = _fit_league(conn, 2026)
    calls = []
    original, spy = _shape_spy(calls)
    try:
        P.shape_exits_and_rookies = spy
        F.replay(league, seasons=1, seed=0)
        F.replay(league, seasons=1, seed=1)
    finally:
        P.shape_exits_and_rookies = original
    assert len(calls) == 2
    first, second = (set(c["leavers"]) for c in calls)
    assert first != second
    assert len(first) > 0 and len(second) > 0
    # Two independent draws from the same roster overlap, but are not the same set.
    assert 0 < len(first & second) < len(first | second)


@pytest.mark.integration
def test_the_exit_draw_varies_by_projected_season(conn):
    """F1(b): one fixed roster through the shared step draws different leavers for 2027 and 2028 at seed 0."""
    league = _fit_league(conn, 2026)
    compact = [{"pid": p["pid"], "position": p["engine_position"], "age": p["age"], "ovr": p["ovr"],
                "years_pro": p["years_pro"]} for p in league["players"]]
    _, first, _ = P.shape_exits_and_rookies(compact, league["rookies"], 2027, seed=0)
    _, second, _ = P.shape_exits_and_rookies(compact, league["rookies"], 2028, seed=0)
    assert {p["pid"] for p in first} != {p["pid"] for p in second}
    assert first and second


@pytest.mark.integration
def test_live_and_fit_draw_the_same_first_class(conn):
    """F1(c)/F3: live 2027 at seed 0 draws the fit's first replay year exactly: same leaver pids and the same
    rookie (OVR, position) list.

    The shared step derives both groups with `shape_group` and sorts the reference class by (position, ovr, age),
    so live's (`league_turnover`, OVR DESC) and the fit's (snapshot pid order) classes pool the same way.
    """
    league = _fit_league(conn, 2026)
    calls = []
    original, spy = _shape_spy(calls)
    try:
        P.shape_exits_and_rookies = spy
        try:
            live = P.run_progression(conn, 2027, commit=False, dry_run=True, log=lambda *_: None)
        finally:
            conn.rollback()
        live_call = calls[-1]
        F.replay(league, seasons=1, seed=0)
        fit_call = calls[-1]
    finally:
        P.shape_exits_and_rookies = original
    assert len(live_call["leavers"]) == live["league_shape"]["leavers"]
    assert live_call["leavers"] == fit_call["leavers"]
    assert live_call["rookies"] == fit_call["rookies"]


@pytest.mark.integration
def test_null_shape_season_refuses_progression(conn):
    """F2: a NULL shape season is a setup error, named with its fix, for both the live pass and the managed pass."""
    snapshot = _managed_snapshot(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE league_state SET shape_season = NULL")
    try:
        with pytest.raises(ValueError, match="shape_season is NULL"):
            P.run_progression(conn, 2027, commit=False, dry_run=True, log=lambda *_: None)
        with pytest.raises(ValueError, match="shape_season is NULL"):
            cp.prepare_offseason_pass(conn.cursor(), snapshot, 2028,
                                      source_fingerprint="builder-8-null-shape-test")
    finally:
        conn.rollback()


@pytest.mark.integration
def test_missing_shape_season_column_refuses_progression(conn):
    """F2: a database without the column refuses too, and the error names 2027_02_26_league_shape_season.sql."""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE league_state DROP COLUMN shape_season")
    try:
        with pytest.raises(ValueError, match="2027_02_26_league_shape_season"):
            P.run_progression(conn, 2027, commit=False, dry_run=True, log=lambda *_: None)
    finally:
        conn.rollback()


@pytest.mark.integration
def test_a_fresh_clean_seed_stores_its_start_shape_season():
    """F2: `seed_clean_db --season 2026` writes shape_season 2026, and it stays 2026 once 2027 is rated and
    current_season moves to 2027 (the old fallback returned the current season)."""
    from db.seed_clean_db import run_clean_seed

    if not _is_scratch(INIT_DSN):
        pytest.skip("the clean-seed test only runs against a mu_scratch_* database")
    try:
        run_clean_seed(INIT_DSN, season=2026, week=1, skip_records=True, skip_coaches=True, skip_gms=True,
                       skip_writers=True, skip_napper=True, log=lambda *_: None)
        conn = psycopg.connect(INIT_DSN)
    except psycopg.OperationalError as exc:
        pytest.skip(f"scratch database not available ({exc})")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT shape_season FROM league_state ORDER BY id LIMIT 1")
            assert cur.fetchone()[0] == 2026
            cur.execute("INSERT INTO players (player_name) VALUES ('Shape Fix Rated 2027') RETURNING id")
            player_id = cur.fetchone()[0]
            cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position, overall_rating) "
                        "VALUES (%s, 2027, NULL, 'QB', 75)", (player_id,))
            cur.execute("UPDATE league_state SET current_season = 2027")
            assert P.league_shape_season(cur) == 2026
    finally:
        conn.rollback()
        conn.close()
