"""Writer tools with real NFL history (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.11, §4): get_player_career's
labeled real block and combined total, get_team_history's real seasons. No database: psycopg.connect and the
engines.nfl_history.read functions are faked."""
import json

from engines.nfl_history import read as nfl_read
from newsroom import db_tools


class Cur:
    def __init__(self, routes):
        self.routes, self._rows, self.description = routes, [], []

    def execute(self, sql, params=None):
        self._rows = []
        for key, rows in self.routes:
            if key in sql:
                self._rows = rows
                break

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _cols_row(season, pass_yards):
    vals = {c: 0 for c in db_tools.CAREER_STATS} | {"games_played": 17, "pass_yards": pass_yards}
    return (season, "KC", "QB") + tuple(vals[c] for c in db_tools.CAREER_STATS)


PLAYER_ROUTES = [("FROM players WHERE id", [("Patrick Mahomes", None)]),
                 ("FROM season_stat_lines", [_cols_row(2026, 4500)]),
                 ("FROM awards", [(2026, "MVP")]),
                 ("FROM player_seasons", []),
                 ("to_regclass", [(False,)])]

REAL = {"gsis_id": "00-0033873", "display_name": "Patrick Mahomes", "first_season": 2017, "last_season": 2025,
        "seasons": [{"season": 2018, "team_abbr": "KC", "games_played": 16, "pass_yards": 5097, "pass_tds": 50},
                    {"season": 2017, "team_abbr": "KC", "games_played": 1, "pass_yards": 284, "pass_tds": 0}],
        "postseason": [{"season": 2018, "pass_yards": 529}],
        "totals": {"games_played": 17, "pass_yards": 5381, "pass_tds": 50, "kick_fg_long": None},
        "postseason_totals": {"games_played": 2, "pass_yards": 529},
        "awards": [{"season": 2018, "award_type": "MVP"}], "draft": {"season": 2017, "round": 1, "pick": 10,
                                                                     "team": "KC"}}


def _career(monkeypatch, real):
    monkeypatch.setattr(db_tools, "resolve_season", lambda s=0: 2026)
    monkeypatch.setattr(db_tools.psycopg, "connect", lambda *a, **k: Conn(Cur(PLAYER_ROUTES)))
    monkeypatch.setattr(nfl_read, "player_nfl_career", real)
    return json.loads(db_tools.get_player_career.func("15"))


def test_player_career_has_labeled_real_block_and_combined_total(monkeypatch):
    out = _career(monkeypatch, lambda cur, pid: REAL if pid == 15 else None)
    assert out["career_totals"]["pass_yards"] == 4500                 # league only, unchanged
    real = out["real_nfl_career"]
    assert [s["season"] for s in real["seasons"]] == [2017, 2018]
    assert real["totals"]["pass_yards"] == 5381 and real["playoff_totals"]["pass_yards"] == 529
    assert real["awards"] == ["2018 MVP"] and real["draft"]["pick"] == 10
    assert out["career_including_real_nfl"]["pass_yards"] == 9881
    assert out["career_including_real_nfl"]["games_played"] == 34
    assert "fact" in out["real_nfl_note"] and "career" in out["real_nfl_note"]


def test_player_career_without_real_history(monkeypatch):
    out = _career(monkeypatch, lambda cur, pid: None)
    assert out["real_nfl_career"] is None and out["career_including_real_nfl"] is None
    assert out["real_nfl_note"] is None and out["career_totals"]["pass_yards"] == 4500

    def broken(cur, pid):
        raise RuntimeError("relation nfl_player_links does not exist")
    out = _career(monkeypatch, broken)
    assert out["real_nfl_career"] is None


TEAM_ROUTES = [("abbreviation ILIKE", [(16,)]),
               ("FROM team_season_results", [(2026, 10, 7, 0, 400, 350, 1, 3, "WILD_CARD", "Andy Reid", "GM")]),
               ("FROM awards", []),
               ("SELECT name FROM teams", [("Chiefs",)])]


def _team(monkeypatch, real):
    monkeypatch.setattr(db_tools, "resolve_season", lambda s=0: 2027)
    monkeypatch.setattr(db_tools.psycopg, "connect", lambda *a, **k: Conn(Cur(TEAM_ROUTES)))
    monkeypatch.setattr(nfl_read, "team_nfl_seasons", real)
    return json.loads(db_tools.get_team_history.func("KC"))


def test_team_history_has_real_seasons_and_league_totals(monkeypatch):
    rows = [{"season": 2023, "team_abbr": "KC", "wins": 11, "losses": 6, "ties": 0, "points_for": 371,
             "points_against": 294, "division_place": 1, "conference_seed": 3, "playoff_result": "CHAMPION",
             "head_coach": "Andy Reid",
             "awards": [{"award_type": "SBMVP", "player_name": "Patrick Mahomes", "coach_name": None},
                        {"award_type": "First Team All-Pro", "player_name": "Chris Jones", "coach_name": None}]},
            {"season": 2022, "team_abbr": "KC", "wins": 14, "losses": 3, "ties": 0, "points_for": 496,
             "points_against": 369, "division_place": 1, "conference_seed": 1, "playoff_result": "CHAMPION",
             "head_coach": "Andy Reid", "awards": []}]
    out = _team(monkeypatch, lambda cur, tid: rows if tid == 16 else [])
    assert [s["season"] for s in out["real_nfl_seasons"]] == [2022, 2023]
    assert out["real_nfl_seasons"][1]["awards"] == ["SBMVP: Patrick Mahomes"]
    assert out["totals"]["wins"] == 10 and out["totals"]["titles"] == []        # league only
    assert "fact" in out["real_nfl_note"]


def test_team_history_without_real_seasons(monkeypatch):
    out = _team(monkeypatch, lambda cur, tid: [])
    assert out["real_nfl_seasons"] == [] and out["real_nfl_note"] is None


def test_dispatcher_tool_line_mentions_real_career():
    import inspect
    from newsroom import swarm_dispatcher
    src = inspect.getsource(swarm_dispatcher)
    assert "plus his real NFL career through 2025" in src and "real NFL seasons 1999-2025" in src
