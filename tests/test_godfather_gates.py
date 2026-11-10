"""Godfather trigger gates, part 1 (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.3; CLOSE_OUT_CHECKLIST P1-27).

Offline: the carousel's candidate sliders come from legacy `persona_sliders` or the person lifecycle, and the GM
hot seat runs before the carousel so a same-cycle HC+GM sweep is a genuine dual vacancy.
"""
import json
import random
import sqlite3

from engines import coaching_carousel as cc
from engines import gm_rollover as gr, gm_voices
from tests.test_inbox_store import FakeCursor


def _personas_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript("""
        CREATE TABLE persona_sliders (persona_type TEXT, persona_id TEXT, temperament INT, sociability INT,
                                      confidence INT, openness INT, warmth INT, drive INT);
        CREATE TABLE persona_core_aliases (universe_id TEXT, persona_type TEXT, namespace TEXT, external_id TEXT,
                                           persona_id TEXT);
        CREATE TABLE persona_core_profiles (universe_id TEXT, persona_type TEXT, persona_id TEXT, birth_key TEXT);
        CREATE TABLE persona_lifecycle_universes (universe_id TEXT, league_id TEXT, registration TEXT);
        CREATE TABLE league_state (id INT, madden_league_id BIGINT);
    """)
    return conn


def test_candidate_sliders_from_legacy_personas():
    conn = _personas_db()
    conn.execute("INSERT INTO persona_sliders VALUES ('coach', '41', 5, 5, 8, 5, 5, 9)")
    cur = FakeCursor(conn)
    sliders = cc.candidate_sliders(cur)
    assert sliders[("db-coach", "41")] == {"drive": 9, "ego": 8}          # ego is the confidence slider
    assert cc._sliders_for(sliders, "db-coach", 41) == {"drive": 9, "ego": 8}


def test_candidate_sliders_from_the_lifecycle_for_a_coordinator():
    conn = _personas_db()
    conn.execute("INSERT INTO league_state VALUES (1, 1287775425)")
    conn.execute("INSERT INTO persona_lifecycle_universes VALUES ('mu:abc', '1287775425', '{\"universe_id\": \"mu:abc\"}')")
    conn.execute("INSERT INTO persona_core_profiles VALUES ('mu:abc', 'coach', 'p-coord', 'source:1')")
    conn.execute("INSERT INTO persona_core_aliases VALUES ('mu:abc', 'coach', 'db-coordinator', '77', 'p-coord')")
    conn.execute("INSERT INTO persona_sliders VALUES ('coach', 'p-coord', 5, 5, 9, 5, 5, 8)")
    sliders = cc.candidate_sliders(FakeCursor(conn))
    assert sliders[("db-coordinator", "77")] == {"drive": 8, "ego": 9}
    assert cc._sliders_for(sliders, "db-coordinator", 77) == {"drive": 8, "ego": 9}


def test_candidate_sliders_never_fails_without_the_tables():
    cur = FakeCursor(sqlite3.connect(":memory:"))
    assert cc.candidate_sliders(cur) == {}


OWNER = {"traits": ["DEEP_POCKETS", "HEADLINE_HUNTER"]}


def test_the_godfather_trigger_fires_beyond_its_rarity_gates():
    """JR, 2026-11-10: the candidate needs a Super Bowl win AND a drive or ego above 8, and the rule applies to any
    head-coach hire (poached coordinator, former head coach, outside hire)."""
    gate = {"eligible": True}
    high_drive = {"key": "coach:9", "drive": 9, "ego": 4, "super_bowl_wins": 1}
    high_ego = {"key": "coach:10", "drive": 4, "ego": 9, "super_bowl_wins": 1}
    champion = {"key": "coach:11", "super_bowl_wins": 1, "champion": True, "drive": 9}
    for candidate in (high_drive, high_ego, champion):
        decision = cc.godfather_offer(gate=gate, owner_view=OWNER, cash_pool=cc.GODFATHER_CASH,
                                      hc_vacant=True, gm_vacant=True, candidate=candidate)
        assert decision["offer"] is True and decision["blockers"] == []
        assert decision["salary"] == cc.GODFATHER_SALARY and decision["years"] == cc.GODFATHER_YEARS


def test_each_gate_still_blocks_on_its_own():
    thin = {"key": "coach:12", "drive": 5, "ego": 5}
    assert "candidate stature" in cc.godfather_offer(
        gate={"eligible": True}, owner_view=OWNER, cash_pool=cc.GODFATHER_CASH, hc_vacant=True, gm_vacant=True,
        candidate=thin)["blockers"]
    assert "dual vacancy" in cc.godfather_offer(
        gate={"eligible": True}, owner_view=OWNER, cash_pool=cc.GODFATHER_CASH, hc_vacant=True, gm_vacant=False,
        candidate={"drive": 9, "super_bowl_wins": 1})["blockers"]
    assert "owner" in cc.godfather_offer(
        gate={"eligible": True}, owner_view={"traits": ["DEEP_POCKETS"]}, cash_pool=cc.GODFATHER_CASH,
        hc_vacant=True, gm_vacant=True, candidate={"drive": 9})["blockers"]


def _vacancy_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript("""
        CREATE TABLE gm_profiles (team_id INT, end_season INT, coach_id INT, start_season INT);
        CREATE TABLE games (season INT, game_type TEXT, status TEXT);
        CREATE TABLE teams (id INT, abbreviation TEXT);
        CREATE TABLE gm_team_seasons (team_id INT, season INT, direction TEXT, fo_quality_at TEXT,
                                      hot_seat_score REAL, hot_seat_inputs TEXT);
        CREATE TABLE team_seasons (team_id INT, season INT, wins INT);
        CREATE TABLE gm_team_settings (team_id INT, user_controlled INT);
        CREATE TABLE draft_picks (draft_season INT, round INT, original_team_id INT, used INT);
    """)
    return conn


def test_the_gm_seat_is_vacant_right_after_the_hot_seat_fires_it():
    conn = _vacancy_db()
    conn.execute("INSERT INTO gm_profiles VALUES (5, NULL, NULL, 2030)")
    cur = FakeCursor(conn)
    assert cc._gm_vacant(cur, 5) is False
    conn.execute("UPDATE gm_profiles SET end_season = 2036 WHERE team_id = 5")
    assert cc._gm_vacant(cur, 5) is True                       # the carousel then sees the dual vacancy


def test_the_godfather_coach_gm_insert_writes_a_template_card():
    """A dual HC+GM vacancy inserts a coach-GM with a COACH_GM template card instead of a placeholder voice."""
    acc = cc.godfather_acceptance(2036, 5, 41, "Charles Grant", salary=cc.GODFATHER_SALARY,
                                  years=cc.GODFATHER_YEARS, slant="OFFENSIVE",
                                  trigger_reason="dual HC+GM vacancy")

    class Cur:
        def __init__(self):
            self.statements = []
            self.rowcount = 0

        def execute(self, sql, params=()):
            self.statements.append((sql, tuple(params)))
            self.rowcount = 0 if "UPDATE gm_profiles" in sql else 1

        def fetchone(self):
            return None

    cur = Cur()
    cc._extend_full_control(cur, acc)
    inserts = [(sql, params) for sql, params in cur.statements if "INSERT INTO gm_profiles" in sql]
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "voice_card" in sql and "voice_card_source" in sql
    assert sql.count("'TEMPLATE'") == 2                              # bio_source and voice_card_source
    card = gm_voices.parse_card(json.loads(params[4]))
    assert card is not None and card["register"].startswith("Talks like a coach")
    assert params[3] == gm_voices.one_line(card)                     # the voice one-liner, not a placeholder

    # The RNG draw order is part of the seed: risk_appetite takes the first draw (0.35-0.75), aggressiveness the
    # second (0.30-0.70), as every save seeded before the unilateral-trade plan has them.
    rng = random.Random("gm:5:2037:godfather")
    risk = round(rng.uniform(0.35, 0.75), 2)
    agg = round(rng.uniform(0.30, 0.70), 2)
    picks = round(rng.uniform(0.85, 1.20), 2)
    youth = round(rng.uniform(-0.5, 0.5), 2)
    assert (params[8], params[9], params[10], params[11]) == (risk, agg, picks, youth)


def test_hot_seat_only_waits_for_the_super_bowl():
    cur = FakeCursor(_vacancy_db())
    rep = gr.hot_seat_only(cur, 2037, log=lambda *a: None)
    assert rep["skipped"] == "the Super Bowl isn't complete" and rep["fired"] == []


def test_hot_seat_only_does_not_decide_twice():
    conn = _vacancy_db()
    conn.execute("INSERT INTO games VALUES (2036, 'SUPER_BOWL', 'COMPLETED')")
    conn.execute("INSERT INTO teams VALUES (5, 'CAR')")
    conn.execute("INSERT INTO gm_profiles VALUES (5, NULL, NULL, 2030)")
    conn.execute("INSERT INTO gm_team_seasons VALUES (5, 2036, 'RETOOL', NULL, 0.2, '{}')")
    cur = FakeCursor(conn)
    rep = gr.hot_seat_only(cur, 2037, log=lambda *a: None)
    assert rep["skipped"] == "already decided"
    # and the preseason rollover sees the same decided state (it does not re-run the hot seat)
    state = gr.read_state(cur, 2037)
    assert state["hot_seat_decided"] is True and gr.rollover_actions(state)["hot_seat"] is None
