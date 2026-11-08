"""Unit tests for the press conference fact sheet and line checker (newsroom/press_facts.py).

No database: the cursor is a fake and the two readers that own connections (the box-score tool and the record-book
week query) are patched. Run: pytest -m "not integration" tests/test_press_facts.py
"""
import re
from unittest import mock

import pytest

from newsroom import press_facts

SEASON = 2036
WEEK = 8
GAME_ID = 800

# id, name, city, abbreviation, conference, division
TEAMS = [
    (1, "Bills", "Buffalo", "BUF", "AFC", "AFC East"),
    (2, "Dolphins", "Miami", "MIA", "AFC", "AFC East"),
    (3, "Chiefs", "Kansas City", "KC", "AFC", "AFC West"),
    (4, "Jets", "New York", "NYJ", "AFC", "AFC East"),
]


def _game(gid, week, home, away, home_score, away_score):
    return (gid, week, "REGULAR_SEASON", home, away, home_score, away_score)


def _season_games():
    """Weeks 1-7 of results plus the Week 8 game (BUF 27, KC 24)."""
    games = []
    gid = 100
    # week: (home, away, home_score, away_score); BUF's and KC's results match the sheet's records.
    plan = {
        1: [(1, 2, 24, 17), (3, 4, 24, 17)],       # BUF W, KC W
        2: [(4, 1, 17, 24), (2, 3, 24, 17)],       # BUF W, KC L
        3: [(1, 2, 17, 24), (3, 4, 24, 17)],       # BUF L, KC W
        4: [(1, 4, 24, 17), (3, 2, 17, 24)],       # BUF W, KC L (home KC loses)
        5: [(1, 2, 24, 17), (3, 4, 24, 17)],       # BUF W, KC W
        6: [(1, 4, 24, 17), (3, 2, 24, 17)],       # BUF W, KC W
        7: [(1, 2, 17, 24), (4, 3, 24, 17)],       # BUF L, KC L
    }
    for week, rows in plan.items():
        for home, away, hs, as_ in rows:
            games.append(_game(gid, week, home, away, hs, as_))
            gid += 1
    games.append(_game(GAME_ID, WEEK, 1, 3, 27, 24))
    return games


COACHES = [
    # away (KC) first, then home (BUF): id, name, slant, off, def, pass_lean, 4th, blitz, man, start_season
    (11, "Andy Reid", "OFFENSIVE", "West Coast", "4-3", "High", "Medium", "Low", "High", 2026),
    (12, "Sean McDermott", "DEFENSIVE", "Spread", "4-3", "Medium", "High", "Medium", "Low", 2026),
]

PLAYER_STATS = [
    # pid, name, team, pos, pass_yds, pass_td, ints, rush_att, rush_yds, rush_td, rec, rec_yds, rec_td, fumb
    (101, "Patrick Mahomes", 3, "QB", 312, 2, 2, 5, 20, 0, 0, 0, 0, 0),
    (102, "Isiah Pacheco", 3, "HB", 0, 0, 0, 15, 65, 0, 0, 0, 0, 0),
    (103, "Travis Kelce", 3, "TE", 0, 0, 0, 0, 0, 0, 7, 88, 1, 0),
    (201, "Josh Allen", 1, "QB", 280, 3, 1, 8, 40, 1, 0, 0, 0, 0),
    (202, "Stefon Diggs", 1, "WR", 0, 0, 0, 0, 0, 0, 9, 120, 2, 0),
]

DEFENSE_LINES = [
    # team, pid, name, pos, tackles, sacks, tfl, ints, ff, fr, def_tds, fgm, fga, xpm, xpa
    (1, 203, "Terrel Bernard", "LB", 6, 1.0, 1, 0, 0, 0, 0, None, None, None, None),
    (1, 204, "Trent McDuffie", "CB", 5, 0.0, 0, 0, 0, 0, 0, None, None, None, None),
]

OFFENSE_AVERAGES = [
    # pid, games, avg pass yds, pass td, ints, rush att, rush yds, rush td, rec, rec yds, rec td, fumb
    (101, 7, 290.0, 2.0, 1.0, 4.0, 18.0, 0.1, 0.0, 0.0, 0.0, 0.2),
    (202, 7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 8.5, 101.0, 1.0, 0.0),
]

DEFENSE_AVERAGES = [
    # pid, games, avg tackles, sacks, tfl, ints, ff, fr, def tds, fgm, fga, xpm, xpa
    (203, 7, 5.0, 0.5, 0.3, 0.1, 0.1, 0.1, 0.0, None, None, None, None),
    (204, 7, 4.0, 0.0, 0.1, 0.2, 0.0, 0.0, 0.0, None, None, None, None),
]

TEAM_STATS = [
    # team_name, total_offense, total_yards_gained, passing, rushing, turnovers, q1, q2, q3, q4, ot
    ("Chiefs", None, None, 290, None, 2, 7, 10, 0, 7, None),
    ("Bills", None, None, 280, 50, 1, 3, 7, 10, 7, None),
]

SCRIPT = {
    "shape": "back_and_forth",
    "storyline": "The Bills edged the Chiefs in a back-and-forth fourth quarter.",
    "quarter_scores": {"BUF": [3, 7, 10, 7], "KC": [7, 10, 0, 7]},
    "halftime": "KC 17-10",
    "lead_changes": 3,
    "largest_lead": {"BUF": 10, "KC": 7},
    "winner_largest_deficit": 7,
    "overtime": False,
    "scoring": [
        {"period": "Q4", "clock": "2:11", "team": "BUF", "play": "Allen pass to Diggs for 22 yards",
         "score": "BUF 27-24", "tags": ["lead for good"]},
    ],
    "key_moments": [
        {"period": "Q4", "clock": "2:11", "team": "BUF", "type": "takeaway", "text": "Bernard intercepted Mahomes"},
    ],
    "adjustments": [],
    "defensive_standouts": {},
    "highlights": [],
}

TRANSACTIONS = {
    3: [
        (10, SEASON, WEEK, WEEK, "SIGNED", 999, "MARQUEZ VALDES-SCANTLING",
         "Marquez Valdes-Scantling", "WR", None, 3, None, "Chiefs", 1, 1_500_000, None,
         "Chiefs sign WR Marquez Valdes-Scantling"),
    ],
    1: [],
}

INJURIES = {
    3: [
        (WEEK - 1, "Travis Kelce", "TE", "HAMSTRING", "2 WEEKS", 2, False),
        (WEEK, "Isiah Pacheco", "HB", "ANKLE", "1 WEEK", 1, False),
    ],
    1: [
        (WEEK - 1, "Stefon Diggs", "WR", "NONE", "0 WEEKS", 0, False),
        (WEEK, "Stefon Diggs", "WR", "NONE", "0 WEEKS", 0, False),
        (WEEK, "Terrel Bernard", "LB", "KNEE", "2 WEEKS", 2, False),
    ],
}

RECORDS = [
    {"type": "nfl_record", "team_id": 3, "holder": "Patrick Mahomes", "label": "single-game pass yards"},
    {"type": "franchise_record", "team_id": 1, "holder": "Stefon Diggs", "label": "franchise receptions"},
    {"type": "nfl_record", "team_id": 2, "holder": "Someone Else", "label": "not this game"},
]


class FakeCursor:
    """Routes each execute() to a result set by SQL text and parameters."""

    def __init__(self):
        self._rows = []
        self._coaches = list(COACHES)

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        params = params or ()
        if "FROM coach_hot_seat" in s:
            self._rows = [(1.23, False, "BLACK_MONDAY", {})] if params[1] == 3 else []
        elif "FROM coach_tenures ct" in s:
            self._rows = [self._coaches.pop(0)] if self._coaches else []
        elif "FROM injuries" in s:
            self._rows = list(INJURIES.get(params[1], []))
        elif "FROM transactions t" in s:
            self._rows = list(TRANSACTIONS.get(params[1], []))
        elif "FROM game_scripts" in s:
            self._rows = [(SCRIPT,)] if params[0] == GAME_ID else []
        elif "FROM team_stats" in s:
            self._rows = list(TEAM_STATS) if params[0] == GAME_ID else []
        elif "GROUP BY ps.player_id" in s:
            self._rows = list(OFFENSE_AVERAGES)
        elif "GROUP BY gl.player_id" in s:
            self._rows = list(DEFENSE_AVERAGES)
        elif "FROM player_stats ps" in s and "WHERE ps.game_id" in s:
            self._rows = list(PLAYER_STATS) if params[0] == GAME_ID else []
        elif "FROM game_stat_lines gl" in s and "WHERE gl.game_id" in s:
            self._rows = list(DEFENSE_LINES) if params[1] == GAME_ID else []
        elif "FROM games g" in s and "WHERE g.id = %s" in s:
            self._rows = [(
                GAME_ID, SEASON, WEEK, "REGULAR_SEASON", "Sun 1:00 PM", "COMPLETED",
                1, "Bills", "Buffalo", "BUF", "AFC", "AFC East", 27,
                3, "Chiefs", "Kansas City", "KC", "AFC", "AFC West", 24,
            )] if params[0] == GAME_ID else []
        elif "FROM games" in s and "status = 'COMPLETED'" in s:
            self._rows = list(_season_games())
        elif "SELECT id, name, city, abbreviation, conference, division FROM teams" in s:
            self._rows = list(TEAMS)
        else:
            self._rows = []

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def build_sheet():
    with mock.patch.object(press_facts, "get_game_box_score", return_value="BOX SCORE TEXT"), \
            mock.patch.object(press_facts, "find_records_broken_in_week", return_value=RECORDS):
        return press_facts.fact_sheet(FakeCursor(), GAME_ID)


def team(sheet, abbr):
    return next(t for t in sheet["teams"] if t["abbr"] == abbr)


def player(sheet, name):
    for t in sheet["teams"]:
        for p in t["players"]:
            if p["name"] == name:
                return p
    raise AssertionError(f"{name} not in sheet")


# ------------------------------------------------------------------------------------------------ fact sheet ---
def test_fact_sheet_shape_and_game_block():
    sheet = build_sheet()
    assert sheet["game"]["final"] == {"home": 27, "away": 24}
    assert sheet["game"]["quarter_scores"] == {"BUF": [3, 7, 10, 7], "KC": [7, 10, 0, 7]}
    assert sheet["game"]["game_type"] == "REGULAR_SEASON"
    assert sheet["game"]["week"] == WEEK
    assert sheet["game"]["broadcast_window"] == "CBS"
    assert sheet["box"] == "BOX SCORE TEXT"
    assert sheet["script"]["shape"] == "back_and_forth"
    assert sheet["script"]["lead_changes"] == 3
    assert sheet["script"]["largest_lead"] == {"BUF": 10, "KC": 7}
    assert sheet["script"]["winner_deficit"] == 7
    assert sheet["script"]["key_moments"]


def test_fact_sheet_records_streak_and_standings():
    sheet = build_sheet()
    kc, buf = team(sheet, "KC"), team(sheet, "BUF")
    assert (kc["record_before"], kc["record_after"]) == ("4-3", "4-4")
    assert (buf["record_before"], buf["record_after"]) == ("5-2", "6-2")
    assert buf["streak"] == {"type": "W", "count": 1}
    assert kc["streak"] == {"type": "L", "count": 2}
    assert buf["division_place"] == 1 and kc["division_place"] == 1
    assert buf["conference_seed"] == 1 and kc["conference_seed"] == 3


def test_fact_sheet_unknown_team_stats_stay_null():
    sheet = build_sheet()
    kc, buf = team(sheet, "KC"), team(sheet, "BUF")
    # KC's rushing yards were not recorded, so there is no team yardage and no defensive line for sacks.
    assert kc["line"]["yards"] is None
    assert kc["line"]["sacks"] is None
    assert kc["line"]["turnovers"] == 2
    assert kc["line"]["takeaways"] == 1
    # Buffalo: yards from pass + rush, a recorded sack, and the opponent's turnovers as takeaways.
    assert buf["line"]["yards"] == 330
    assert buf["line"]["sacks"] == 1.0
    assert buf["line"]["turnovers"] == 1
    assert buf["line"]["takeaways"] == 2


def test_fact_sheet_player_lines_and_averages():
    sheet = build_sheet()
    mahomes = player(sheet, "Patrick Mahomes")
    assert mahomes["line"]["pass_yards"] == 312
    assert mahomes["line"]["interceptions"] == 2
    assert mahomes["line"]["touchdowns"] == 2
    assert mahomes["season_averages"]["pass_yards"] == 290.0
    assert mahomes["season_averages"]["games"] == 7
    diggs = player(sheet, "Stefon Diggs")
    assert diggs["line"]["rec_yards"] == 120
    assert diggs["line"]["touchdowns"] == 2
    mcd = player(sheet, "Trent McDuffie")
    assert mcd["line"]["touchdowns"] == 0
    assert mcd["line"]["sacks"] == 0.0


def test_fact_sheet_coach_and_hot_seat():
    sheet = build_sheet()
    kc, buf = team(sheet, "KC"), team(sheet, "BUF")
    assert kc["coach"]["name"] == "Andy Reid"
    assert kc["coach"]["tenure"] == SEASON - 2026 + 1
    assert kc["coach"]["schemes"]["offense"] == "West Coast"
    assert kc["coach"]["tendencies"]["pass_lean"] == "High"
    assert "1.23" in kc["coach"]["hot_seat"]["line"]
    assert buf["coach"]["name"] == "Sean McDermott"
    assert buf["coach"]["hot_seat"] is None


def test_fact_sheet_injuries_transactions_records():
    sheet = build_sheet()
    kc, buf = team(sheet, "KC"), team(sheet, "BUF")
    assert [i["player_name"] for i in kc["injuries"]] == ["Isiah Pacheco"]
    assert [i["player_name"] for i in buf["injuries"]] == ["Terrel Bernard"]
    assert len(kc["transactions"]) == 1
    assert "Valdes-Scantling" in kc["transactions"][0]
    assert buf["transactions"] == []
    assert [r["holder"] for r in kc["records"]] == ["Patrick Mahomes"]
    assert [r["holder"] for r in buf["records"]] == ["Stefon Diggs"]


def test_fact_sheet_names_and_numbers():
    sheet = build_sheet()
    assert "Patrick Mahomes" in sheet["names"]
    assert "Andy Reid" in sheet["names"]
    assert "Adam Schefter" in sheet["names"]          # reporters in the room are names too
    assert "Marquez Valdes-Scantling" in sheet["names"]
    assert ["pass_yards", "Patrick Mahomes", 312] in sheet["numbers"]
    assert ["yards", "BUF", 330] in sheet["numbers"]
    assert ["rush_yards", "BUF", 50] in sheet["numbers"]
    assert ["score", "BUF", 27] in sheet["numbers"]
    assert ["season_pass_yards_pg", "Patrick Mahomes", 290.0] in sheet["numbers"]


def test_fact_sheet_missing_game_is_empty():
    with mock.patch.object(press_facts, "get_game_box_score", return_value="BOX"), \
            mock.patch.object(press_facts, "find_records_broken_in_week", return_value=[]):
        assert press_facts.fact_sheet(FakeCursor(), 9999) == {}


# ------------------------------------------------------------------------------------------------ checker ---
@pytest.fixture(scope="module")
def sheet():
    return build_sheet()


def kinds(issues):
    return [i["kind"] for i in issues]


def test_flags_wrong_interception_count(sheet):
    issues = press_facts.check_line("Mahomes threw 4 interceptions.", sheet, "Adam Schefter")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["claim"] == "4 interceptions"
    assert issues[0]["recorded"] == 2
    assert issues[0]["span"] == [14, 29]


def test_flags_wrong_player_yardage_by_last_name(sheet):
    issues = press_facts.check_line("Mahomes threw for 400 yards.", sheet, "Adam Schefter")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["recorded"] == 312


def test_flags_wrong_score(sheet):
    issues = press_facts.check_line("They won 30-24.", sheet, "Adam Schefter")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["claim"] == "30-24"


def test_flags_wrong_record(sheet):
    issues = press_facts.check_line("Our record is 8-1.", sheet, "Sean McDermott")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["claim"] == "8-1"
    assert issues[0]["recorded"] == "6-2"


def test_flags_unknown_name(sheet):
    issues = press_facts.check_line("Bob Johnson asked about the defense.", sheet, "Adam Schefter")
    assert kinds(issues) == ["NAME"]
    assert issues[0]["claim"] == "Bob Johnson"


def test_flags_touchdown_credited_to_player_without_one(sheet):
    issues = press_facts.check_line("McDuffie's touchdown changed the game.", sheet, "Adam Schefter")
    assert kinds(issues) == ["EVENT"]
    assert issues[0]["recorded"] == 0


def test_flags_wrong_straight_wins(sheet):
    issues = press_facts.check_line("We have won 4 straight.", sheet, "Sean McDermott")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["recorded"] == 1


def test_passes_correct_lines(sheet):
    text = ("Mahomes threw for 312 yards and 2 touchdowns. Stefon Diggs caught 9 passes for 120 yards. "
            "We scored 27 points and won 27-24.")
    assert press_facts.check_line(text, sheet, "Sean McDermott") == []


def test_passes_correct_event(sheet):
    assert press_facts.check_line("Mahomes's touchdown gave them the lead.", sheet, "Adam Schefter") == []


def test_passes_opinions_and_unknown_stats(sheet):
    assert press_facts.check_line("That was the worst half of football you've coached here.",
                                  sheet, "Adam Schefter") == []
    assert press_facts.check_line("He had 12 pressures and 4 hurries.", sheet, "Adam Schefter") == []


def test_reporter_names_are_allowed(sheet):
    text = "Adam Schefter and Mina Kimes both raised their hands."
    assert press_facts.check_line(text, sheet, "Adam Schefter") == []


def test_quoted_company_and_place_names_are_not_people(sheet):
    text = 'He said "Kansas City" and "Pro Football Focus" would not matter.'
    assert press_facts.check_line(text, sheet, "Adam Schefter") == []


def test_passes_first_place_and_playoffs(sheet):
    assert press_facts.check_line("We are in first place and in the playoffs.",
                                  sheet, "Sean McDermott") == []


def test_team_rushing_yards(sheet):
    assert press_facts.check_line("We ran for 50 yards.", sheet, "Sean McDermott") == []
    issues = press_facts.check_line("We ran for 87 yards.", sheet, "Sean McDermott")
    assert kinds(issues) == ["NUMBER"]
    assert issues[0]["recorded"] == 50


def test_unquoted_place_and_team_names_are_not_people(sheet):
    assert press_facts.check_line("Kansas City and the Bills brought pressure.",
                                  sheet, "Adam Schefter") == []


def test_points_allowed_and_margin_are_not_team_points(sheet):
    assert press_facts.check_line("They held us to 24 points.", sheet, "Sean McDermott") == []
    issues = press_facts.check_line("They held us to 17 points.", sheet, "Sean McDermott")
    assert kinds(issues) == ["NUMBER"] and issues[0]["recorded"] == 24
    assert press_facts.check_line("We lost by 3 points.", sheet, "Sean McDermott") == []
    issues = press_facts.check_line("We lost by 10 points.", sheet, "Sean McDermott")
    assert kinds(issues) == ["NUMBER"] and issues[0]["recorded"] == 3


def test_season_averages_are_used_for_averaging_claims(sheet):
    assert press_facts.check_line("Mahomes is averaging 290 yards a game.",
                                  sheet, "Adam Schefter") == []
    issues = press_facts.check_line("Mahomes is averaging 400 yards a game.", sheet, "Adam Schefter")
    assert kinds(issues) == ["NUMBER"] and issues[0]["recorded"] == 290.0


def test_issue_objects_have_the_four_fields(sheet):
    issues = press_facts.check_line("Bob Johnson said Mahomes threw 4 interceptions.", sheet, "Adam Schefter")
    assert issues
    for issue in issues:
        assert set(issue) == {"kind", "span", "claim", "recorded"}
        assert isinstance(issue["span"], list) and len(issue["span"]) == 2
        assert issue["kind"] in ("NUMBER", "NAME", "EVENT")
