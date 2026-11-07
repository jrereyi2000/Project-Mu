"""Real NFL blocks on the site's endpoints (docs/done/REAL_NFL_HISTORY_PLAN.md §2.11, §4): /api/player/{id}
nfl_career, /api/team/{abbr}/history nfl_seasons, /api/awards nfl_seasons. No database: pools, connections and the
engines.nfl_history.read functions are faked."""
import contextlib
import inspect

from fastapi.testclient import TestClient

from server import api, history_api


class Cur:
    """Answers by SQL keyword; rows are dicts (api's dict_row pool) unless tuples are given."""

    def __init__(self, routes):
        self.routes, self.description, self._rows = routes, [], []

    def execute(self, sql, params=None):
        self._rows, names = [], []
        for key, (rows, cols) in self.routes.items():
            if key in sql:
                self._rows, names = rows, cols
                break
        self.description = [type("D", (), {"name": n})() for n in names]

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


TEAM_ROUTES = {"FROM teams WHERE": ([{"id": 16}], ["id"]),
               "FROM team_season_results": ([{"season": 2026, "wins": 10, "losses": 7, "ties": 0, "points_for": 1,
                                             "points_against": 2, "division_place": 1, "conference_seed": 3,
                                             "playoff_result": "MISSED", "playoff_wins": 0, "playoff_losses": 0,
                                             "source": "CAPTURE", "coach_name": "Andy Reid", "gm_name": None}], []),
               "FROM awards": ([], [])}


def _team_client(monkeypatch, nfl):
    monkeypatch.setattr(api, "season_week", lambda s, w=None: (2026, 18))
    monkeypatch.setattr(api.db_pool, "connection", lambda timeout=None: contextlib.nullcontext(Conn(Cur(TEAM_ROUTES))))
    monkeypatch.setattr(api.nfl_read, "team_nfl_seasons", nfl)
    return TestClient(api.app)


def test_team_history_adds_nfl_seasons_and_keeps_league_summary(monkeypatch):
    real = [{"season": 2025, "team_abbr": "KC", "wins": 6, "losses": 11, "ties": 0, "playoff_result": "MISSED",
             "awards": []}]
    data = _team_client(monkeypatch, lambda cur, team_id: real if team_id == 16 else []).get(
        "/api/team/KC/history").json()
    assert data["nfl_seasons"] == real
    assert data["summary"]["wins"] == 10 and data["summary"]["since"] == 2026      # league only


def test_team_history_without_nfl_tables(monkeypatch):
    def broken(cur, team_id):
        raise RuntimeError("relation nfl_team_seasons does not exist")
    data = _team_client(monkeypatch, broken).get("/api/team/KC/history").json()
    assert data["nfl_seasons"] == [] and len(data["seasons"]) == 1


def _awards_client(monkeypatch, by_season, champions):
    routes = {"to_regclass": ([(True,)], ["ok"]), "FROM teams": ([(16, "KC")], ["id", "abbreviation"]),
              "FROM awards": ([], ["season"]), "FROM games": ([], ["id"])}
    monkeypatch.setattr(history_api.psycopg, "connect", lambda *a, **k: Conn(Cur(routes)))
    from engines.nfl_history import read
    monkeypatch.setattr(read, "nfl_awards_by_season", by_season)
    monkeypatch.setattr(read, "nfl_champions", champions)
    app = api.FastAPI()
    app.include_router(history_api.router)
    return TestClient(app)


def test_awards_adds_nfl_seasons(monkeypatch):
    by = lambda cur: {2024: [{"season": 2024, "award_type": "MVP", "player_name": "Josh Allen", "coach_name": None,  # noqa: E731
                              "position": "QB", "team_abbr": "BUF", "mu_team": "BUF", "player_id": 7},
                             {"season": 2024, "award_type": "First Team All-Pro", "player_name": "X",
                              "coach_name": None, "position": "QB", "team_abbr": "BAL", "mu_team": "BAL",
                              "player_id": None}]}
    champs = lambda cur: {2024: {"season": 2024, "team_abbr": "PHI", "mu_team": "PHI", "team_id": 26,  # noqa: E731
                                 "wins": 14, "losses": 3, "ties": 0, "head_coach": "Nick Sirianni"}}
    data = _awards_client(monkeypatch, by, champs).get("/api/awards").json()
    [s] = data["nfl_seasons"]
    assert s["season"] == 2024 and s["source"] == "NFL"
    assert s["champion"] == {"team": "PHI", "team_abbr": "PHI", "record": "14-3", "coach": "Nick Sirianni"}
    assert s["awards"]["MVP"] == {"name": "Josh Allen", "position": "QB", "team": "BUF", "player_id": 7}
    assert s["awards"]["DPOY"] is None and "First Team All-Pro" not in s["awards"]
    assert data["seasons"] == []          # league seasons untouched


def test_awards_without_nfl_tables(monkeypatch):
    def broken(cur):
        raise RuntimeError("missing")
    data = _awards_client(monkeypatch, broken, broken).get("/api/awards").json()
    assert data["nfl_seasons"] == []


def test_player_endpoint_returns_nfl_career_beside_unchanged_totals(monkeypatch):
    src = inspect.getsource(api.get_player_api)
    assert '"nfl_career": nfl_player_career(cur, id)' in src
    assert '"career_totals": career_totals(career)' in src
    monkeypatch.setattr(api.nfl_read, "player_nfl_career", lambda cur, pid: {"gsis_id": "g", "seasons": []})
    assert api.nfl_player_career(None, 5) == {"gsis_id": "g", "seasons": []}

    def broken(cur, pid):
        raise RuntimeError("missing")
    monkeypatch.setattr(api.nfl_read, "player_nfl_career", broken)
    assert api.nfl_player_career(None, 5) is None


def test_career_totals_ignores_real_seasons():
    career = [{"season": 2026, "gp": 17, "pass_yards": 4000, "team_abbr": "KC"},
              {"season": 2027, "gp": 17, "pass_yards": 4100, "team_abbr": "KC"}]
    totals = api.career_totals(career)
    assert totals["pass_yards"] == 8100 and totals["since"] == 2026
