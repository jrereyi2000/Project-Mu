"""get_award_race (newsroom/db_tools.py), the AWARDS_TRACKER candidate-race tool. Offline: a fake cursor and a patched
engines.award_candidates.candidates, so no database and no model is touched."""
from unittest import mock

import pytest

from newsroom.db_tools import AWARD_RACE_CODES, AWARD_RACE_LIMIT, _render_award_race, get_award_race

TEAMS = [(1, "KC"), (2, "BUF"), (3, "SF")]

ROWS = [
    {"id": 11, "name": "Rio Vance", "team_id": 1, "position": "QB", "games": 17,
     "metrics": {"production": 1.9, "efficiency": 1.1, "wins": 0.7, "record_pace": 0.0}, "base_score": 1.42},
    {"id": 12, "name": "Dez Hall", "team_id": 2, "position": "WR", "games": 16,
     "metrics": {"production": 1.2, "efficiency": 0.4, "wins": -0.2, "record_pace": 0.0}, "base_score": 0.68},
    {"id": 13, "name": "Cal Rowe", "team_id": 3, "position": "TE", "games": 17,
     "metrics": {"production": 0.6, "efficiency": -0.3, "wins": 0.1, "record_pace": 0.0}, "base_score": 0.28},
]
EXPERIENCE = [(11, 0), (12, 4), (13, 7)]
ROOKIE_EXPERIENCE = [(11, 0), (12, 0), (13, 0)]

COTY_ROWS = [
    {"id": 51, "name": "Ann Head", "team_id": 1, "position": "HC", "games": None,
     "metrics": {"production": 1.6, "efficiency": 0.0, "wins": 1.06, "record_pace": 0.0}, "base_score": 1.6},
    {"id": 52, "name": "Bo Bench", "team_id": 2, "position": "HC", "games": None,
     "metrics": {"production": 0.4, "efficiency": 0.0, "wins": -0.44, "record_pace": 0.0}, "base_score": 0.4},
]


class FakeCursor:
    def __init__(self, experience=(), teams=()):
        self.experience, self.teams = list(experience), list(teams)
        self.queries, self._rows = [], []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "player_seasons" in sql:
            wanted = set((params or (None, []))[1])
            self._rows = [r for r in self.experience if r[0] in wanted]
        elif "FROM teams" in sql:
            self._rows = list(self.teams)
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def call(award, season=2036, rows=None, experience=EXPERIENCE, teams=TEAMS):
    cur = FakeCursor(experience, teams)
    with mock.patch("newsroom.db_tools.psycopg.connect", return_value=FakeConn(cur)), \
         mock.patch("engines.award_candidates.candidates", return_value=ROWS if rows is None else rows) as cands:
        text = get_award_race.invoke({"award": award, "season": season})
    return text, cands, cur


@pytest.mark.parametrize("given,code", [("MVP", "MVP"), ("opoy", "OPOY"), ("Dpoy", "DPOY"), ("oroy", "OROY"),
                                        ("DROY", "DROY"), ("coty", "COTY"), (" Oroy ", "OROY")])
def test_each_award_code_resolves_case_insensitively(given, code):
    rows = COTY_ROWS if code == "COTY" else ROWS
    text, cands, _ = call(given, rows=rows)
    assert not text.startswith("Error:")
    assert f"{code} candidate race" in text
    assert cands.call_args.args[1:] == (2036, code)


def test_defaults_the_season_and_the_engine_routes_player_awards():
    cur = FakeCursor(EXPERIENCE, TEAMS)
    with mock.patch("newsroom.db_tools.psycopg.connect", return_value=FakeConn(cur)), \
         mock.patch("newsroom.db_tools.resolve_season", return_value=2039) as resolve, \
         mock.patch("engines.award_candidates.candidates", return_value=ROWS) as cands:
        text = get_award_race.invoke({"award": "MVP"})
    resolve.assert_called_once_with(0)
    assert cands.call_args.args[1] == 2039 and "2039 MVP candidate race" in text


def test_unknown_code_lists_the_six_valid_codes_and_never_queries():
    with mock.patch("newsroom.db_tools.psycopg.connect") as connect:
        text = get_award_race.invoke({"award": "rookie of the year"})
    connect.assert_not_called()
    assert "Unknown award code 'rookie of the year'" in text
    for code in AWARD_RACE_CODES:
        assert code in text
    assert len(AWARD_RACE_CODES) == 6
    assert not text.startswith("Error:")


@pytest.mark.parametrize("code", ["OROY", "DROY"])
def test_rookie_awards_header_and_rows_state_yoe_zero(code):
    rows = [dict(r, position="RB") for r in ROWS]
    text, _, cur = call(code.lower(), rows=rows, experience=ROOKIE_EXPERIENCE)
    header, *lines = text.splitlines()
    assert header.startswith(f"2036 {code} candidate race")
    assert "every listed player has years_of_experience 0" in header
    assert "second-year" in header and "contradicts this tool" in header
    assert len(lines) == len(rows)
    assert all("YOE 0 (rookie)" in line for line in lines)
    assert all("YOE 0" in line and "YOE 4" not in line for line in lines)
    experience_sql = [params for sql, params in cur.queries if "player_seasons" in sql]
    assert experience_sql == [(2036, [11, 12, 13])]


def test_a_non_rookie_award_shows_real_experience_and_no_rookie_claim():
    text, _, _ = call("MVP")
    header, *lines = text.splitlines()
    assert "years_of_experience 0" not in header and "second-year" not in header
    assert "YOE 3" not in text
    assert "YOE 0 (rookie)" in lines[0]                 # Rio Vance, year one
    assert "YOE 4" in lines[1] and "YOE 7" in lines[2]


def test_renderer_keeps_the_engine_order_and_shows_team_position_games_metrics():
    rows = [
        {"id": 11, "name": "Rio Vance", "team_id": 1, "position": "QB", "games": 17,
         "metrics": {"production": 1.9, "efficiency": 1.1, "wins": 0.7, "record_pace": 0.0}, "base_score": 0.1},
        {"id": 12, "name": "Dez Hall", "team_id": 2, "position": "WR", "games": 16,
         "metrics": {"production": 1.2, "efficiency": 0.4, "wins": -0.2, "record_pace": 0.0}, "base_score": 9.9},
    ]
    header, *lines = _render_award_race("MVP", 2036, rows, {11: 3, 12: 0}, {1: "KC", 2: "BUF"}).splitlines()
    assert header.startswith("2036 MVP candidate race")
    assert "CANDIDATE RACE, NOT THE RESULT" in header and "Mu-internal" in header
    assert [line.split(" | ")[0] for line in lines] == ["1. Rio Vance", "2. Dez Hall"]   # not re-sorted by score
    first = lines[0].split(" | ")
    assert first[1] == "KC" and first[2] == "QB" and first[3] == "17 G" and first[4] == "YOE 3"
    assert "production +1.90, efficiency +1.10, wins +0.70, record pace +0.00" in first[5]
    assert lines[1].split(" | ")[4] == "YOE 0 (rookie)"


def test_renderer_caps_at_ten_and_handles_missing_games_and_team():
    rows = [{"id": i, "name": f"Player {i}", "team_id": 1, "position": "WR", "games": 17,
             "metrics": {"production": float(i), "efficiency": 0.0, "wins": 0.0, "record_pace": 0.0},
             "base_score": 20.0 - i} for i in range(1, 13)]
    rows[0] = dict(rows[0], games=None, team_id=99)
    text = _render_award_race("OPOY", 2036, rows, {}, {1: "KC"})
    header, *lines = text.splitlines()
    assert "top 10" in header and "pool of up to 15" in header
    assert len(lines) == AWARD_RACE_LIMIT == 10
    assert lines[0].startswith("1. Player 1") and lines[-1].startswith("10. Player 10")
    assert "team 99" in lines[0] and "? G" in lines[0] and "YOE ?" in lines[0]   # never invent a team or a zero


def test_coty_rows_are_coaches_with_record_inputs_and_no_experience():
    text, _, cur = call("coty", rows=COTY_ROWS)
    header, *lines = text.splitlines()
    assert "Head coaches only" in header and "win%" in header
    assert "YOE" not in text and "years_of_experience" not in text
    assert all("| HC |" in line for line in lines)
    assert "win% 0.765" in lines[0] and "production +1.60, wins +1.06" in lines[0]
    assert "win% 0.390" in lines[1]
    assert not any("player_seasons" in sql for sql, _ in cur.queries)


def test_a_season_with_no_candidates_returns_a_readable_paragraph():
    text, _, cur = call("MVP", rows=[])
    assert text.startswith("No candidates for MVP in season 2036")
    assert "no candidate race to report" in text
    assert "\n" not in text and not text.startswith("Error:")
    assert not any("player_seasons" in sql for sql, _ in cur.queries)


def test_a_database_error_becomes_text_not_an_exception():
    with mock.patch("newsroom.db_tools.psycopg.connect", side_effect=RuntimeError("connection refused")):
        text = get_award_race.invoke({"award": "MVP", "season": 2036})
    assert text.startswith("Error: connection refused")
