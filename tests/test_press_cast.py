"""Offline tests for press conference casting (PRESS_CONFERENCE_PLAN §2.4, U2).

Run: pytest -m "not integration" tests/test_press_cast.py

Everything here uses synthetic cursors. No database is opened.
"""

import copy
import json
import os
import random

import pytest

from newsroom import press_cast


# ------------------------------------------------------------------------------------- fake database

class FakeCursor:
    """A DB-API-ish cursor that routes each execute() to a configured result set."""

    def __init__(self, teams=(), week_games=(), current=(), prior=(), beats=(), nationals=()):
        self.teams = list(teams)
        self.week_games = list(week_games)
        self.current = list(current)
        self.prior = list(prior)
        self.beats = list(beats)
        self.nationals = list(nationals)
        self.queries = []
        self._rows = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        text = " ".join(sql.split())
        if "FROM teams" in text:
            self._rows = list(self.teams)
        elif "projected_excitement" in text and "FROM games" in text:
            self._rows = list(self.week_games)
        elif "FROM games" in text and "week <" in text:
            self._rows = list(self.current)
        elif "FROM games" in text:
            self._rows = list(self.prior)
        elif "LOCAL_BEAT_REPORTER" in text:
            self._rows = list(self.beats)
        elif "agent_memories" in text:
            self._rows = list(self.nationals)
        else:
            self._rows = []

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


# id, name, abbreviation, conference, division — teams.name matches engines.market_constants keys.
TEAMS = [
    (1, "Cowboys", "DAL", "NFC", "NFC East"),
    (2, "Jaguars", "JAX", "AFC", "AFC South"),
    (3, "Chiefs", "KC", "AFC", "AFC West"),
    (4, "Eagles", "PHI", "NFC", "NFC East"),
    (5, "Panthers", "CAR", "NFC", "NFC South"),
    (6, "Broncos", "DEN", "AFC", "AFC West"),
    (7, "Titans", "TEN", "AFC", "AFC South"),
    (8, "Falcons", "ATL", "NFC", "NFC South"),
]

BEATS = [
    ("beat_1a", "LOCAL_BEAT_REPORTER", "person_beat_1a", "@beat1a", "Beat One A", 1),
    ("beat_1b", "LOCAL_BEAT_REPORTER", "person_beat_1b", "@beat1b", "Beat One B", 1),
    ("beat_2a", "LOCAL_BEAT_REPORTER", "person_beat_2a", "@beat2a", "Beat Two A", 2),
]


def _pool_archetypes():
    pool = press_cast.load_pool()
    return sorted(a for names in pool["tiers"].values() for a in names)


def _national_rows(archetypes=None, extra=()):
    archetypes = _pool_archetypes() if archetypes is None else list(archetypes)
    rows = [(f"agent_{a}", a, f"person_{a}", f"@{a}", a.replace("_", " ").title()) for a in archetypes]
    return rows + list(extra)


def make_cursor(week_games=(), current=(), prior=(), beats=BEATS, nationals=None, teams=TEAMS):
    return FakeCursor(
        teams=teams, week_games=week_games, current=current, prior=prior,
        beats=beats, nationals=_national_rows() if nationals is None else nationals,
    )


def game(game_id, home, away, week=5, game_type="REGULAR_SEASON", game_time="Sun 1:00 PM",
         projected=None, season=2035):
    return (game_id, season, week, game_type, game_time, home, away, projected)


def game_dict(game_id, home, away, week=5, game_type="REGULAR_SEASON", game_time="Sun 1:00 PM",
              projected=None, season=2035):
    return {
        "game_id": game_id, "season": season, "week": week, "game_type": game_type,
        "game_time": game_time, "home_team_id": home, "away_team_id": away,
        "projected_excitement": projected,
    }


# ------------------------------------------------------------------------------------- pre-game interest

def test_pregame_interest_quiet_low_market():
    cur = make_cursor()
    result = press_cast.pregame_interest(cur, game_dict(100, 2, 5))
    # 0.45 * 50 (missing projection) + 0.35 * Jaguars/Panthers B (31.9) + 0.20 * 50 (form 0.5)
    expected = 0.45 * 50 + 0.35 * 31.9 + 0.20 * 50
    assert result["interest"] == pytest.approx(expected, abs=0.02)
    assert result["interest"] < 45
    assert result["inputs"]["excitement_rescaled"] == 50.0
    assert result["inputs"]["weighted"]["excitement"] == pytest.approx(22.5)
    assert result["inputs"]["division_bonus"] == 0
    assert result["inputs"]["primetime_bonus"] == 0


def test_pregame_interest_market_form_and_bonuses():
    cur = make_cursor(
        current=[(1, 7, 30, 10)] * 4 + [(8, 4, 20, 10)] * 4,
        prior=[(1, 7, 20, 20)],
    )
    result = press_cast.pregame_interest(
        cur, game_dict(101, 1, 4, game_time="Sun 8:20 PM", projected=90)
    )
    inputs = result["inputs"]
    assert inputs["market_home"] == pytest.approx(85.85)
    assert inputs["market_away"] == pytest.approx(71.0)
    assert inputs["form_home"] == pytest.approx(1.0)
    assert inputs["form_away"] == pytest.approx(0.0)
    assert inputs["broadcast_window"] == "SNF"
    assert inputs["primetime_bonus"] == press_cast.PRIMETIME_BONUS
    assert inputs["division_bonus"] == press_cast.DIVISION_BONUS
    expected = (0.45 * ((70 + 30 * (90 - 40) / 60 - 70) / 30 * 100)
                + 0.35 * (85.85 + 71.0) / 2
                + 0.20 * 50
                + press_cast.PRIMETIME_BONUS + press_cast.DIVISION_BONUS)
    assert result["interest"] == pytest.approx(expected, abs=0.02)
    assert result["interest"] >= 80


def test_pregame_interest_ignores_postgame_values():
    pre = game_dict(102, 3, 6, projected=58)
    post = copy.deepcopy(pre)
    post.update({"home_score": 31, "away_score": 3, "excitement_index": 99.0,
                 "popularity_index": 100.0, "status": "COMPLETED"})
    cur_pre, cur_post = make_cursor(), make_cursor()
    assert press_cast.pregame_interest(cur_pre, pre) == press_cast.pregame_interest(cur_post, post)
    for sql in cur_pre.queries + cur_post.queries:
        lowered = sql.lower()
        assert "excitement_index" not in lowered
        assert "popularity_index" not in lowered


def test_tier_bands():
    assert press_cast.interest_tier(44.99) == "QUIET"
    assert press_cast.interest_tier(45.0) == "NOTABLE"
    assert press_cast.interest_tier(64.99) == "NOTABLE"
    assert press_cast.interest_tier(65.0) == "BIG"
    assert press_cast.interest_tier(79.99) == "BIG"
    assert press_cast.interest_tier(80.0) == "MARQUEE"
    assert press_cast.interest_tier(0.0, "WILD_CARD") == "MARQUEE"
    assert press_cast.interest_tier(99.0, "SUPER_BOWL") == "MARQUEE"


@pytest.mark.parametrize("game_type,floor", [
    ("WILD_CARD", 70), ("DIVISIONAL", 80), ("CONFERENCE", 90), ("SUPER_BOWL", 100),
])
def test_playoff_round_floors(game_type, floor):
    cur = make_cursor()
    result = press_cast.pregame_interest(cur, game_dict(110, 2, 5, week=19, game_type=game_type))
    assert result["interest"] >= floor
    assert result["inputs"]["playoff_floor"] == floor
    assert press_cast.interest_tier(result["interest"], game_type) == "MARQUEE"


# ------------------------------------------------------------------------------------- national counts

def _cast_one(rows, week, beats=BEATS):
    cur = make_cursor(week_games=rows, beats=beats)
    casts = press_cast.cast_week(cur, 2035, week)
    assert len(casts) == 1
    return next(iter(casts.values())), cur


def test_quiet_tier_and_national_count():
    cast, _ = _cast_one([game(120, 2, 5)], 5)
    assert cast["tier"] == "QUIET"
    expected_count = 1 if random.Random("press:2035:5:120").random() < 1 / 3 else 0
    assert len(cast["nationals"]) == expected_count
    assert len(cast["nationals"]) in (0, 1)
    assert all(a["pool_tier"] == "INSIDER" for a in cast["nationals"])


def test_notable_tier_and_national_count():
    cast, _ = _cast_one([game(121, 3, 6, projected=58)], 5)
    assert cast["tier"] == "NOTABLE"
    assert 45 <= cast["interest"] < 65
    assert 1 <= len(cast["nationals"]) <= 2


def test_big_tier_and_national_count():
    cast, _ = _cast_one([game(122, 1, 4, game_time="Sun 8:20 PM")], 5)
    assert cast["tier"] == "BIG"
    assert 65 <= cast["interest"] < 80
    assert 2 <= len(cast["nationals"]) <= 4


def test_marquee_tier_and_national_count():
    cast, _ = _cast_one([game(123, 1, 4, game_time="Sun 8:20 PM", projected=90)], 5)
    assert cast["tier"] == "MARQUEE"
    assert cast["interest"] >= 80
    assert 4 <= len(cast["nationals"]) <= 6


def test_super_bowl_takes_up_to_eight():
    cast, _ = _cast_one([game(124, 1, 4, week=23, game_type="SUPER_BOWL")], 23)
    assert cast["tier"] == "MARQUEE"
    assert len(cast["nationals"]) == press_cast.SUPER_BOWL_MAX_NATIONALS


def test_one_game_per_national_per_week():
    rows = [game(200 + i, 1, 4, game_time="Sun 8:20 PM", projected=90) for i in range(6)]
    cur = make_cursor(week_games=rows)
    casts = press_cast.cast_week(cur, 2035, 5)
    keys = [a["person_key"] for cast in casts.values() for a in cast["nationals"]]
    assert keys
    assert len(keys) == len(set(keys))


# ------------------------------------------------------------------------------------- pool membership

def test_excluded_archetypes_never_cast():
    extra = [
        ("agent_MEL_KIPER", "MEL_KIPER", "person_MEL_KIPER", "@mel", "Mel Kiper"),
        ("agent_AGGREGATOR", "AGGREGATOR", "person_AGG", "@agg", "Aggregator"),
    ]
    cur = make_cursor(
        week_games=[game(130, 1, 4, game_time="Sun 8:20 PM", projected=90)],
        nationals=_national_rows(extra=extra),
    )
    cast = press_cast.cast_week(cur, 2035, 5)[130]
    pool = press_cast.load_pool()
    excluded = {a.upper() for a in pool["excluded"]}
    placed = {a.upper() for names in pool["tiers"].values() for a in names}
    for attendee in cast["nationals"]:
        assert attendee["archetype"] in placed
        assert attendee["archetype"] not in excluded


def test_former_team_weighting():
    weights = press_cast.load_pool()["weights"]
    assert press_cast.national_weight("OCHOCINCO", "CIN", "PIT", weights) == 2.0
    assert press_cast.national_weight("SHANNON", "DAL", "DEN", weights) == 2.0
    assert press_cast.national_weight("OCHOCINCO", "DAL", "PHI", weights) == 1.0
    assert press_cast.national_weight("SCHEFTER", "CIN", "PIT", weights) == 1.0


def test_relationship_weighting_is_neutral_and_reads_nothing():
    weights = press_cast.load_pool()["weights"]
    assert weights["relationship_bonus"] == 0.0
    assert press_cast.RELATIONSHIP_CONTEXT_AVAILABLE is False
    cur = make_cursor()
    assert press_cast._relationship_multiplier(cur, "person_x", 1, 4, weights) == 1.0
    assert cur.queries == []
    assert press_cast.national_weight("SCHEFTER", "DAL", "PHI", weights, relationship_multiplier=1.0) == 1.0


def test_never_reads_persona_relationships():
    cur = make_cursor(
        week_games=[game(140 + i, 1, 4, game_time="Sun 8:20 PM", projected=90) for i in range(4)]
    )
    press_cast.cast_week(cur, 2035, 5)
    for sql in cur.queries:
        lowered = sql.lower()
        assert "persona_relationships" not in lowered
        assert "popularity_index" not in lowered
        assert "excitement_index" not in lowered


# ------------------------------------------------------------------------------------- beats

def test_beats_attend_only_their_own_teams_presser():
    cast, _ = _cast_one([game(150, 1, 2, projected=58)], 5)
    assert [a["agent_id"] for a in cast["beats"][1]] == ["beat_1a", "beat_1b"]
    assert [a["agent_id"] for a in cast["beats"][2]] == ["beat_2a"]
    for team_id, attendees in cast["beats"].items():
        for attendee in attendees:
            assert attendee["role"] == "BEAT"
            assert attendee["pool_tier"] is None
            assert attendee["archetype"] == "LOCAL_BEAT_REPORTER"
    # No beat reporter leaks into the national list.
    beat_ids = {a["agent_id"] for cast_ in [cast] for a in cast_["beats"][1] + cast_["beats"][2]}
    assert not (beat_ids & {a["agent_id"] for a in cast["nationals"]})


# ------------------------------------------------------------------------------------- determinism / R3

def test_cast_is_deterministic_for_a_seed():
    rows = [game(160 + i, 1, 4, game_time="Sun 8:20 PM", projected=90) for i in range(3)]
    first = press_cast.cast_week(make_cursor(week_games=rows), 2035, 5)
    second = press_cast.cast_week(make_cursor(week_games=rows), 2035, 5)
    assert first == second


def test_same_cast_before_and_after_the_game_is_played():
    # cast_week never selects the score, E or popularity; the projected E is kept for completed games.
    pre = [game(170, 3, 6, projected=58)]
    post = [game(170, 3, 6, projected=58)]  # same projected E; query cannot see the result
    first = press_cast.cast_week(make_cursor(week_games=pre), 2035, 5)
    second = press_cast.cast_week(make_cursor(week_games=post), 2035, 5)
    assert first == second


# ------------------------------------------------------------------------------------- press_pool.json

def _personas_path():
    return os.path.join(os.path.dirname(press_cast.__file__), "config", "agent_personas.json")


def test_press_pool_json_is_valid():
    with open(_personas_path(), encoding="utf-8") as f:
        archetypes = set(json.load(f)["archetypes"])
    pool = press_cast.load_pool()
    press_cast.validate_pool(pool, archetypes)

    placed = [a for names in pool["tiers"].values() for a in names]
    excluded = pool["excluded"]
    assert len(placed) == len(set(placed))
    assert not (set(placed) & set(excluded))
    assert set(pool["notes"]) == archetypes
    assert pool["weights"]["former_team_bonus"] == 2.0
    assert pool["weights"]["relationship_bonus"] == 0.0

    # Every non-excluded S5 national candidate is placed.
    for candidate in press_cast.NATIONAL_CANDIDATES:
        if candidate not in excluded:
            assert candidate in placed


def test_validate_pool_rejects_overlaps_and_missing_notes():
    with open(_personas_path(), encoding="utf-8") as f:
        archetypes = set(json.load(f)["archetypes"])
    good = press_cast.load_pool()

    two_tiers = copy.deepcopy(good)
    two_tiers["tiers"]["REPORTER"].append("SCHEFTER")
    with pytest.raises(ValueError):
        press_cast.validate_pool(two_tiers, archetypes)

    tier_and_excluded = copy.deepcopy(good)
    tier_and_excluded["excluded"].append("SCHEFTER")
    with pytest.raises(ValueError):
        press_cast.validate_pool(tier_and_excluded, archetypes)

    missing_candidate = copy.deepcopy(good)
    missing_candidate["tiers"]["COLUMNIST"].remove("BARNWELL")
    with pytest.raises(ValueError):
        press_cast.validate_pool(missing_candidate, archetypes)

    missing_note = copy.deepcopy(good)
    del missing_note["notes"]["BARNWELL"]
    with pytest.raises(ValueError):
        press_cast.validate_pool(missing_note, archetypes)

    relationship_on = copy.deepcopy(good)
    relationship_on["weights"]["relationship_bonus"] = 0.5
    with pytest.raises(ValueError):
        press_cast.validate_pool(relationship_on, archetypes)
