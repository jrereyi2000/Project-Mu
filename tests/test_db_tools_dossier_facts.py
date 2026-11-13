"""P8-04 dossier source regressions: source conflicts and metric labelling.

Offline only: the saved week-21 pilot packet supplies the exact values (Darby 132 tackles,
Galloway 153, DuVall 138; Mendoza's +47% interceptions per game; the stale 27 receiving-TD
record row) and every database touch is a fake cursor or a patched reader.

Three defects (docs/in_progress/V3_DOSSIER_EVALUATION.md lines 119-133):
1. A leaders list ordered by interceptions was the packet's only source of tackle numbers, so
   its first row (Darby, 132) was written as the team tackle leader; Galloway (153) led.
2. A QB scheme split printed `interceptions: 0.6` / `+47%` with no unit and no pressure
   dimension, so a per-game count against strong scoring defenses became an "interception
   rate when hurried".
3. The BOOK row for Davis's 2035 receiving TDs said 27 while the recorded season line said
   29, so a 2036 29 was reported as a broken record instead of a tie.
"""
import json

import pytest

from newsroom import db_tools
from newsroom.db_tools import get_defense_special_teams_leaders, get_player_scheme_splits, get_record_book


# --- fixtures (values from verify_output/v3_pilot/20261030_195619_499948/packet.json) ---

def leader_row(pid, name, position, tackles, sacks=0, tfl=0, ints=0, pd=0, ff=0, fr=0,
               def_td=0, games=17, yoe=4):
    values = [0] * 35
    values[0], values[1], values[2], values[3], values[4] = pid, name, position, "Carolina Panthers", games
    values[5], values[6], values[7], values[8], values[9] = tackles, sacks, tfl, ints, pd
    values[10], values[11], values[12], values[34] = ff, fr, def_td, yoe
    return tuple(values)


DARBY = leader_row(807, "Marquis Darby", "MIKE", 132, sacks=2, tfl=19, ints=5, pd=7, fr=2, def_td=1)
GALLOWAY = leader_row(581, "Antoine Galloway", "FS", 153, tfl=7, ints=3, pd=6, ff=2, def_td=2, yoe=1)
DUVALL = leader_row(150, "Layne DuVall", "SS", 138, tfl=9, ints=2, pd=10, ff=1, fr=2, def_td=1, games=16)
INTERCEPTION_ORDER = [DARBY, GALLOWAY, DUVALL]
TACKLE_ORDER = [GALLOWAY, DUVALL, DARBY]

STALE_DAVIS_EVENT = {
    "type": "top10", "result": "broke", "category": "season_rec_tds",
    "label": "Receiving TDs in a season", "holder": "Trey'Mane Davis", "team": "Carolina Panthers",
    "value": 29, "previous_record": 27,
    "previous_holders": ["Trey'Mane Davis (2035)"], "week": None,
}
RECORDED_2035 = {"season_rec_tds": [(2035, "Trey'Mane Davis", 29.0)]}
HIGH = {"season_rec_tds": "HIGH"}

MENDOZA_SPLITS = {
    "games": 21,
    "overall_per_game": {"pass_yards": 294.9, "pass_tds": 2.2, "interceptions": 0.4,
                         "comp_pct": 64.9, "yards_per_attempt": 7.7},
    "dimensions": {"scoring_defense": {"Strong": {
        "games": 7,
        "per_game": {"pass_yards": 292.0, "pass_tds": 2.1, "interceptions": 0.6,
                     "comp_pct": 62.6, "yards_per_attempt": 7.0},
        "vs_his_average": {"pass_yards": "-1%", "pass_tds": "-6%", "interceptions": "+47%",
                           "comp_pct": "-3%", "yards_per_attempt": "-8%"}}}},
}


class FakeCursor:
    def __init__(self):
        self._rows, self.queries = [], []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "FROM teams;" in sql:
            self._rows = [(1, "CAR", "Carolina", "Panthers")]
        elif "FROM record_categories" in sql:
            self._rows = [("season_rec_tds", "Receiving TDs in a season", "season", "player", "HIGH", 1)]
        elif "FROM players p" in sql:
            self._rows = [("Fernando Mendoza", "QB", "Cardinals")]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

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


# --- defect 1: a leaders list names the metric that ranks it --------------------------

def test_interceptions_list_does_not_label_its_first_row_the_tackle_leader():
    listing = db_tools.leader_listing(INTERCEPTION_ORDER, "interceptions", "interceptions", 2036, "CAR")
    assert listing["category_key"] == "interceptions"
    assert listing["ranked_by"]["label"] == "interceptions"
    assert "only" in listing["ranked_by"]["warning"].lower()
    assert "category='tackles'" in listing["ranked_by"]["warning"]
    assert listing["leaders"][0].startswith("1. Marquis Darby")
    assert "ranked by interceptions: 5 (list #1)" in listing["leaders"][0]
    assert "132 tkl" in listing["leaders"][0]
    assert "row 1 here is not automatically the team's tackle leader" in listing["note"]
    # Galloway's 153 is present in the same list but is not the interceptions rank.
    assert "ranked by interceptions: 3 (list #2)" in listing["leaders"][1]


def test_a_tackle_leaderboard_ranks_by_tackles_and_puts_galloway_first():
    listing = db_tools.leader_listing(TACKLE_ORDER, "tackles", "tackles", 2036, "CAR")
    assert listing["leaders"][0].startswith("1. Antoine Galloway")
    assert "ranked by tackles: 153 (list #1)" in listing["leaders"][0]
    assert "ranked by tackles: 132 (list #3)" in listing["leaders"][2]


def test_leader_rank_value_covers_the_computed_takeaways_category():
    row = leader_row(9, "A Backer", "MIKE", 90, ints=4, fr=3)
    assert db_tools.leader_rank_value(row, "takeaways") == 7


class LeaderCursor(FakeCursor):
    """Answers the leaders SELECT with one row, so the tool's own rendering can be read."""

    def __init__(self, row):
        super().__init__()
        self.row = row

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        self._rows = [self.row] if "FROM season_stat_lines l" in sql else []


def _top_level_columns(select_list):
    """The SELECT list split on its top-level commas (COALESCE(a, b) stays one column)."""
    columns, depth, current = [], 0, []
    for char in select_list:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            columns.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        columns.append("".join(current).strip())
    return columns


@pytest.mark.parametrize("category", sorted(db_tools.LEADER_CATEGORIES))
def test_the_leaders_statement_orders_by_the_column_leader_rank_value_reads(monkeypatch, category):
    """F4: nothing tied the SELECT's column order to LEADER_RANK_VALUE, so a reorder could print
    one category's total under another's ranking with every test still passing. The statement is
    now built from one tuple, and this drives the tool itself for all 13 categories: the row order
    the SQL asks for is the one the indexes point into, and the printed 'ranked by' figure is the
    value of the column the statement orders by."""
    row = tuple(100 + i for i in range(35))
    cur = LeaderCursor(row)
    monkeypatch.setattr(db_tools, "psycopg", type("P", (), {"connect": staticmethod(lambda *a, **k: FakeConn(cur))}))
    monkeypatch.setattr(db_tools, "resolve_season", lambda season=0: 2036)
    payload = json.loads(get_defense_special_teams_leaders(category=category, limit=5))
    sql = cur.queries[-1][0]
    columns = _top_level_columns(sql.split("SELECT", 1)[1].split("FROM season_stat_lines", 1)[0])
    assert tuple(columns) == db_tools.LEADER_SELECT_COLUMNS               # one definition of the row order
    assert len(columns) == 35
    order_column = sql.split("ORDER BY", 1)[1].split(" DESC NULLS LAST", 1)[0].strip().lstrip("-").strip()
    label = db_tools.LEADER_CATEGORIES[category][2]
    if category == "takeaways":
        assert order_column == f"COALESCE({columns[8]}, 0) + COALESCE({columns[11]}, 0)"
        expected = row[8] + row[11]
    else:
        assert db_tools.LEADER_RANK_VALUE[category] == columns.index(order_column)
        expected = row[db_tools.LEADER_RANK_VALUE[category]]
    assert f"ranked by {label}: {expected} (list #1)" in payload["leaders"][0]


# --- defect 2: split values carry their unit and scope ------------------------------

def test_split_metric_labels_say_interceptions_are_per_game_not_a_rate():
    labels = db_tools.split_metric_labels("QB")
    assert "per game" in labels["interceptions"]
    assert "not a rate" in labels["interceptions"]
    assert labels["comp_pct"].startswith("completion percentage")
    assert labels["yards_per_attempt"] == "yards per pass attempt"
    rb = db_tools.split_metric_labels("RB")
    assert "rush_attempts" in rb and "comp_pct" not in rb


def test_scheme_split_payload_carries_labels_and_denies_a_pressure_split(monkeypatch):
    cur = FakeCursor()
    monkeypatch.setattr(db_tools, "psycopg", type("P", (), {"connect": staticmethod(lambda *a, **k: FakeConn(cur))}))
    monkeypatch.setattr(db_tools, "resolve_season", lambda season=0: 2036)
    monkeypatch.setattr(db_tools, "player_lines", lambda *a, **k: ([{"season": 2036, "opponent_id": 2}], 15))
    monkeypatch.setattr(db_tools, "defense_groups", lambda *a, **k: {})
    monkeypatch.setattr(db_tools, "compute_splits", lambda *a, **k: MENDOZA_SPLITS)
    payload = json.loads(get_player_scheme_splits("590"))
    assert payload["metric_labels"]["interceptions"] == "interceptions per game (a count, not a rate or percentage)"
    assert "count per game is not a rate" in payload["metric_scope"]
    assert "not pressure on this player" in payload["dimension_scope"]["scoring_defense (points allowed per game)"]
    assert "pressure, hurry, blitz, hit and time-to-throw splits" in payload["not_recorded"]
    assert "interception rate (only interceptions per game is recorded)" in payload["not_recorded"]


def test_metric_scope_excludes_comp_pct_and_yards_per_attempt_from_per_game_figures(monkeypatch):
    """F5: `metric_scope` called every figure a per-game average or count, but `player_splits._per_game`
    divides completions by attempts for comp_pct and pass yards by attempts for yards_per_attempt, and
    the blended comp_pct mixes rates; the scope now says so. scoring_defense is also described as what
    it is — points allowed in the games visible so far, not a full-season grade."""
    cur = FakeCursor()
    monkeypatch.setattr(db_tools, "psycopg", type("P", (), {"connect": staticmethod(lambda *a, **k: FakeConn(cur))}))
    monkeypatch.setattr(db_tools, "resolve_season", lambda season=0: 2036)
    monkeypatch.setattr(db_tools, "player_lines", lambda *a, **k: ([{"season": 2036, "opponent_id": 2}], 15))
    monkeypatch.setattr(db_tools, "defense_groups", lambda *a, **k: {})
    monkeypatch.setattr(db_tools, "compute_splits", lambda *a, **k: MENDOZA_SPLITS)
    payload = json.loads(get_player_scheme_splits("590"))
    scope = payload["metric_scope"]
    assert "comp_pct and yards_per_attempt are the exceptions" in scope
    assert "ratios over his attempts" in scope
    assert "never a percentage-point change" in scope
    assert "season to date mid-season" in payload["dimension_scope"]["scoring_defense (points allowed per game)"]
    # the labels themselves already carried the unit; they are unchanged
    assert payload["metric_labels"]["comp_pct"].startswith("completion percentage")
    assert payload["metric_labels"]["yards_per_attempt"] == "yards per pass attempt"


# --- defect 3: a stale BOOK row cannot make a tie a broken record --------------------

def test_previous_holder_season_parses_only_a_named_holder_with_a_season():
    assert db_tools.previous_holder_season("Trey'Mane Davis (2035)") == ("Trey'Mane Davis", 2035)
    assert db_tools.previous_holder_season("2036 Carolina Panthers") is None
    assert db_tools.previous_holder_season("Trey'Mane Davis") is None


def test_record_result_handles_both_directions():
    assert db_tools.record_result(29, 29) == "tied"
    assert db_tools.record_result(30, 29) == "broke"
    assert db_tools.record_result(28, 29) == "ranks"
    assert db_tools.record_result(250, 267, "LOW") == "broke"
    assert db_tools.record_result(260, 250, "LOW") == "ranks"


def test_stale_book_row_is_re_resolved_from_the_recorded_season_line():
    [event] = db_tools.reconcile_record_events([STALE_DAVIS_EVENT], HIGH, RECORDED_2035)
    assert event["result"] == "tied"
    assert event["previous_record"] == 29
    assert event["previous_record_source"] == "recorded season stats"
    conflict = event["source_conflict"]
    assert conflict["book_previous_record"] == 27
    assert conflict["recorded_previous_record"] == 29
    assert conflict["result_if_book_used"] == "broke"
    assert conflict["resolved_result"] == "tied"
    assert "never upgrade a tie to a broken record" in conflict["note"]


def test_agreeing_book_row_is_left_alone():
    agreeing = dict(STALE_DAVIS_EVENT, previous_record=29, result="tied")
    [event] = db_tools.reconcile_record_events([agreeing], HIGH, RECORDED_2035)
    assert event == agreeing and "source_conflict" not in event


# --- F2: a recorded line may make a mark worse, never better --------------------------

def _tied_event(**overrides):
    event = {"type": "top10", "result": "tied", "category": "season_rec_tds",
             "label": "Receiving TDs in a season", "holder": "Trey'Mane Davis",
             "team": "Carolina Panthers", "value": 29, "previous_record": 29,
             "previous_holders": ["Trey'Mane Davis (2035)"], "week": None}
    event.update(overrides)
    return event


def test_a_lower_recorded_line_cannot_upgrade_a_tie_to_a_broken_record():
    """F2 (audit repro 1): a partial or same-name row below the book made the override run in
    the better direction, so a tie came out 'broke, previous 20'."""
    recorded = {"season_rec_tds": [(2035, "Trey'Mane Davis", 20.0), (2035, "Trey'Mane Davis", 9.0)]}
    [event] = db_tools.reconcile_record_events([_tied_event()], HIGH, recorded)
    assert event["result"] == "tied"                    # was "broke"
    assert event["previous_record"] == 29               # the book's value stands
    assert "previous_record_source" not in event
    conflict = event["source_conflict"]
    assert conflict["recorded_previous_record"] == 20   # the best over the holder's rows
    assert conflict["resolved_result"] == "tied"
    assert conflict["kept_book_result"] is True
    assert "flagged only" in conflict["note"]


def test_a_second_co_holder_cannot_upgrade_a_confirmed_tie():
    """F2 (audit repro 2): the first holder confirmed 29 and the loop carried on to a second
    holder recorded at 25, which overrode the tie to 'broke, previous 25'."""
    recorded = {"season_rec_tds": [(2030, "A A", 29.0), (2035, "B B", 25.0)]}
    event = _tied_event(previous_holders=["A A (2030)", "B B (2035)"])
    [got] = db_tools.reconcile_record_events([event], HIGH, recorded)
    assert got == event and "source_conflict" not in got


def test_a_recorded_line_may_still_downgrade_a_broken_record():
    """The override is one-directional, not switched off: a recorded line above the book still
    replaces it and the result worsens (the stale BOOK row is what this fix is for)."""
    event = dict(STALE_DAVIS_EVENT, previous_record=27, result="broke")
    recorded = {"season_rec_tds": [(2035, "Trey'Mane Davis", 30.0)]}
    [got] = db_tools.reconcile_record_events([event], HIGH, recorded)
    assert got["result"] == "ranks"
    assert got["previous_record"] == 30
    assert got["previous_record_source"] == "recorded season stats"
    assert got["source_conflict"]["kept_book_result"] is False


def test_a_worse_recorded_line_cannot_upgrade_a_low_record():
    """LOW categories count a lower mark as better: a recorded 300 cannot make a tied 267 'broke'."""
    low = {"season_team_fewest_points_allowed": "LOW"}
    event = {"type": "franchise_record", "result": "tied",
             "category": "season_team_fewest_points_allowed", "value": 267,
             "previous_record": 267, "previous_holders": ["A A (2030)"]}
    recorded = {"season_team_fewest_points_allowed": [(2030, "A A", 300.0)]}
    [got] = db_tools.reconcile_record_events([event], low, recorded)
    assert got["result"] == "tied" and got["previous_record"] == 267
    assert got["source_conflict"]["kept_book_result"] is True


def test_a_same_name_pair_is_reported_when_the_recorded_rows_carry_player_ids():
    """F2: matching is by name and season; where the rows carry player ids the conflict says how
    many players share that name, and the value used is the best of them (never an upgrade)."""
    recorded = {"season_rec_tds": [(2035, 11, "Trey'Mane Davis", 20.0),
                                   (2035, 12, "Trey'Mane Davis", 9.0)]}
    [got] = db_tools.reconcile_record_events([_tied_event()], HIGH, recorded)
    assert got["source_conflict"]["same_name_players"] == 2
    assert got["result"] == "tied" and got["previous_record"] == 29


def test_record_book_tool_flags_the_stale_row(monkeypatch):
    """The tool (not just the helper) recomputes the result from the recorded line."""
    import engines.records_engine as records_engine
    cur = FakeCursor()
    event = {"type": "top10", "result": "broke", "category": "season_rec_tds",
             "label": "Receiving TDs in a season", "holder": "Trey'Mane Davis", "team_id": 1,
             "team": "Carolina Panthers", "player_id": 5, "value": 29, "nfl_record": 27,
             "nfl_record_holders": ["Trey'Mane Davis (2035)"], "rank": 1}
    monkeypatch.setattr(db_tools, "psycopg", type("P", (), {"connect": staticmethod(lambda *a, **k: FakeConn(cur))}))
    monkeypatch.setattr(db_tools, "resolve_season", lambda season=0: 2036)
    monkeypatch.setattr(db_tools, "_resolve_team_id", lambda cur, identifier: 1)
    monkeypatch.setattr(records_engine, "evaluate", lambda cur, season: {"events": [event]})
    monkeypatch.setattr(records_engine, "season_player_values",
                        lambda cur, season: [("season_rec_tds", 1, 5, "Trey'Mane Davis", 29.0)]
                        if season == 2035 else [])
    monkeypatch.setattr(records_engine, "season_defense_values", lambda cur, season: [])
    output = json.loads(get_record_book(team_identifier="CAR", season=2036))
    [record] = output["records_set_this_season"]
    assert record["result"] == "tied"
    assert record["previous_record"] == 29
    assert record["source_conflict"]["book_previous_record"] == 27
    assert "source_conflict" in output["record_scope_note"]
    assert "never describe a tied mark" in output["record_scope_note"]
