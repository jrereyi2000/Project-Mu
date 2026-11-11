"""engines/nfl_history store and read (docs/done/REAL_NFL_HISTORY_PLAN.md §2.2, §2.8, §4)."""
import os
import re
import subprocess

import pytest

from engines.nfl_history import read, store

ROOT = os.path.join(os.path.dirname(__file__), "..")
MIGRATION = os.path.join(ROOT, "db", "migrations", "2027_02_04_nfl_history.sql")
INIT_DB = os.path.join(ROOT, "db", "init_db.sql")
TABLES = ("nfl_players", "nfl_player_seasons", "nfl_games", "nfl_team_seasons", "nfl_coach_seasons",
          "nfl_awards", "nfl_draft_picks", "nfl_player_links", "nfl_history_loads")


class FakeCursor:
    """Records SQL; answers fetches from a queue of (rows, column names)."""

    def __init__(self, results=()):
        self.sql = []
        self.many = []
        self.results = list(results)
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        if self.results:
            rows, names = self.results.pop(0)
            self._rows = rows
            self.description = [(n,) for n in names]
        else:
            self._rows = []

    def executemany(self, sql, seq):
        self.many.append((" ".join(sql.split()), list(seq)))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


# ---------- connect ----------

@pytest.mark.parametrize("db", ["madden", "madden_2036", "host=localhost dbname=madden user=postgres",
                                "postgresql://postgres@localhost/madden_2036"])
def test_connect_refuses_live_databases(db):
    with pytest.raises(store.RefusedDatabase):
        store.connect(db)


def test_db_name_and_dsn():
    assert store.db_name("madden_test") == "madden_test"
    assert store.db_name("host=x dbname=madden_abc user=y") == "madden_abc"
    assert store.dsn_for("madden_abc") == "host=localhost dbname=madden_abc user=postgres password=postgres"
    assert store.dsn_for("dbname=x") == "dbname=x"


# ---------- writes ----------

def test_replace_kind_deletes_only_its_kind_and_range():
    cur = FakeCursor()
    rows = [{"game_id": "1999_01_A_B", "season": 1999, "week": 1, "game_type": "REG", "home_abbr": "KC",
             "away_abbr": "DEN"}]
    counts = store.replace_kind(cur, "games", 1999, 2001, rows)
    assert counts == {"nfl_games": 1}
    assert cur.sql == [("DELETE FROM nfl_games WHERE season BETWEEN %s AND %s", (1999, 2001))]
    sql, params = cur.many[0]
    assert sql.startswith("INSERT INTO nfl_games (game_id, season, week, game_type, home_abbr, away_abbr)")
    assert params == [("1999_01_A_B", 1999, 1, "REG", "KC", "DEN")]


def test_replace_kind_teams_replaces_team_and_coach_seasons():
    cur = FakeCursor()
    counts = store.replace_kind(cur, "teams", 2000, 2000, {
        "team_seasons": [{"season": 2000, "team_id": 1, "team_abbr": "KC", "wins": 7, "losses": 9, "ties": 0,
                          "points_for": 1, "points_against": 2, "playoff_result": "MISSED",
                          "standings_source": "COMPUTED"}],
        "coach_seasons": []})
    assert counts == {"nfl_team_seasons": 1, "nfl_coach_seasons": 0}
    deleted = [s for s, _ in cur.sql]
    assert deleted == ["DELETE FROM nfl_team_seasons WHERE season BETWEEN %s AND %s",
                       "DELETE FROM nfl_coach_seasons WHERE season BETWEEN %s AND %s"]


def test_replace_kind_rejects_rows_outside_range_and_unknown():
    with pytest.raises(ValueError):
        store.replace_kind(FakeCursor(), "draft", 2000, 2001, [{"season": 2005, "overall": 1, "round": 1,
                                                                 "team_abbr": "KC", "player_name": "X"}])
    with pytest.raises(ValueError):
        store.replace_kind(FakeCursor(), "players", 2000, 2001, [])
    with pytest.raises(ValueError):
        store.replace_kind(FakeCursor(), "games", 2000, 2000, [{"season": 2000, "bogus; DROP": 1}])


def test_upsert_players_never_deletes():
    cur = FakeCursor()
    store.upsert_players(cur, [{"gsis_id": "00-1", "display_name": "A B"}])
    assert not any("DELETE" in s for s, _ in cur.sql)
    sql, _ = cur.many[0]
    assert "ON CONFLICT (gsis_id) DO UPDATE" in sql and "DELETE" not in sql


def test_record_load_stores_hashes_and_counts():
    class F:
        url, cache_name, sha256, bytes = "u", "c", "abc", 3
    cur = FakeCursor([([(7,)], ["id"])])
    assert store.record_load(cur, "stats", 1999, 2025, [F()], {"rows": 5}, {"x": 1}) == 7
    sql, params = cur.sql[0]
    assert sql.startswith("INSERT INTO nfl_history_loads")
    assert '"sha256": "abc"' in params[3] and '"rows": 5' in params[4]
    with pytest.raises(ValueError):
        store.record_load(FakeCursor(), "bogus", 1, 2, [], {}, {})


def test_write_links_replaces_all_and_baseline_upserts():
    cur = FakeCursor()
    store.write_links(cur, [{"player_id": 1, "gsis_id": "g", "method": "EXACT", "score": 9.0,
                             "evidence": {"name": True}}])
    assert cur.sql[0][0] == "DELETE FROM nfl_player_links"
    assert cur.many[0][1][0][4] == '{"name": true}'
    cur = FakeCursor()
    store.write_coach_baseline(cur, [{"coach_id": 3, "career_wins": 10, "career_losses": 6, "career_ties": 0,
                                      "winning_seasons": 1, "playoff_wins": 0, "playoff_losses": 1,
                                      "super_bowl_wins": 0, "super_bowl_losses": 0}])
    sql, params = cur.sql[0]
    assert "ON CONFLICT (coach_id, season) DO UPDATE" in sql and "NULL" in sql
    assert params[:2] == (3, 2025)


# ---------- reads ----------

def test_reads_empty_when_not_installed():
    def cur():
        return FakeCursor([([(False,)], ["ok"])])
    assert read.player_nfl_career(cur(), 1) is None
    assert read.nfl_career_before(cur(), [1], ["pass_yards"]) == {}
    assert read.nfl_first_seasons(cur(), [1]) == {}
    assert read.team_nfl_seasons(cur(), 1) == []
    assert read.nfl_awards_by_season(cur()) == {}
    assert read.nfl_champions(cur()) == {}
    assert read.nfl_player_awards(cur()) == []


def test_player_nfl_career_shapes_rows():
    season_cols = ["gsis_id", "season", "season_type", "team_abbr", "games_played", "pass_yards", "def_sacks",
                   "kick_fg_long", "mu_team"]
    cur = FakeCursor([
        ([(True,)], ["ok"]),
        ([("g1", "Tom Real", 2000, 6, 199, "NE")],
         ["gsis_id", "display_name", "draft_year", "draft_round", "draft_pick", "draft_team_abbr"]),
        ([("g1", 2002, "REG", "NE", 16, 3764, None, None, "NE"),
          ("g1", 2001, "POST", "NE", 3, 572, None, None, "NE"),
          ("g1", 2001, "REG", "NE", 15, 2843, 0.5, 40, "NE")], season_cols),
        ([(2001, "SBMVP")], ["season", "award_type"]),
        ([], ["season"]),
    ])
    c = read.player_nfl_career(cur, 5)
    assert c["gsis_id"] == "g1" and c["first_season"] == 2001 and c["last_season"] == 2002
    assert [s["season"] for s in c["seasons"]] == [2002, 2001]
    assert c["totals"]["pass_yards"] == 6607 and c["totals"]["games_played"] == 31
    assert c["totals"]["def_sacks"] == 0.5 and c["totals"]["kick_fg_long"] == 40
    assert c["postseason_totals"]["pass_yards"] == 572
    assert c["awards"] == [{"season": 2001, "award_type": "SBMVP"}]
    assert c["draft"] == {"season": 2000, "round": 6, "pick": 199, "team": "NE"}


def test_player_nfl_career_unlinked_is_none():
    cur = FakeCursor([([(True,)], ["ok"]), ([], ["gsis_id"])])
    assert read.player_nfl_career(cur, 5) is None


def test_nfl_career_before_sums_and_filters_columns():
    cur = FakeCursor([([(True,)], ["ok"]), ([(5, 100, None)], ["player_id", "pass_yards", "rush_yards"])])
    out = read.nfl_career_before(cur, [5], ["pass_yards", "rush_yards", "kick_fg_long", "bogus"])
    assert out == {5: {"pass_yards": 100, "rush_yards": 0}}
    assert "kick_fg_long" not in cur.sql[1][0] and "bogus" not in cur.sql[1][0]


# ---------- schema text ----------

def _objects(text):
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (nfl_(?!mirror_)\w+)", text))
    views = set(re.findall(r"CREATE OR REPLACE VIEW (nfl_\w+)", text))
    return tables, views


def test_migration_and_init_db_define_every_object():
    mig = open(MIGRATION).read()
    init = open(INIT_DB).read()
    for text in (mig, init):
        tables, views = _objects(text)
        assert tables == set(TABLES)
        assert views == {"nfl_season_lines"}
    assert "BEGIN;" in mig and "COMMIT;" in mig


def test_store_columns_match_schema():
    mig = open(MIGRATION).read()
    for table, cols in store.COLUMNS.items():
        body = mig[mig.index(f"CREATE TABLE IF NOT EXISTS {table} ("):]
        body = body[:body.index(");\n")]
        for c in cols:
            assert re.search(rf"\b{c}\b", body), (table, c)


# ---------- scratch database (integration) ----------

SCRATCH = "mu_scratch_nfl_history_store"
ADMIN = "host=localhost dbname=postgres user=postgres password=postgres connect_timeout=3"


@pytest.mark.integration
def test_migration_applies_twice_and_reload_is_stable():
    import psycopg
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except Exception:
        pytest.skip("no Postgres")
    admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
    admin.execute(f"CREATE DATABASE {SCRATCH}")
    try:
        with psycopg.connect(f"host=localhost dbname={SCRATCH} user=postgres password=postgres") as conn:
            conn.execute("CREATE TABLE teams (id SERIAL PRIMARY KEY, abbreviation TEXT)")
            conn.execute("CREATE TABLE coaches (id SERIAL PRIMARY KEY)")
            conn.execute("CREATE TABLE players (id SERIAL PRIMARY KEY)")
            conn.execute("INSERT INTO teams (abbreviation) VALUES ('KC')")
            sql = open(MIGRATION).read().replace("BEGIN;", "").replace("COMMIT;", "")
            conn.execute(sql)
            conn.execute(sql)
            with conn.cursor() as cur:
                def load():
                    store.upsert_players(cur, [{"gsis_id": f"g{i}", "display_name": f"P {i}"} for i in range(3)])
                    store.replace_kind(cur, "stats", 2000, 2000, [
                        {"gsis_id": f"g{i}", "season": 2000, "season_type": "REG", "team_id": 1,
                         "team_abbr": "KC", "pass_yards": 100 * i, "def_sacks": 1.5} for i in range(3)])
                load()
                cur.execute("SELECT count(*), sum(pass_yards) FROM nfl_player_seasons")
                first = cur.fetchone()
                load()
                cur.execute("SELECT count(*), sum(pass_yards) FROM nfl_player_seasons")
                assert cur.fetchone() == first == (3, 300)
                assert read.installed(cur)
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
        admin.close()
