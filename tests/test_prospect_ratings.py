"""Hidden prospect ratings (pipeline/prospect_ratings.py; SCOUTING_REVAMP_PLAN.md §2.2, §4; U1).

Offline: in-memory SQLite from the real migration, plus a recording fake cursor. No PostgreSQL connection is opened.
"""
import sqlite3

from engines import prospect_ids as pids
from engines.scouting_value import prospect_key
from pipeline import load_save as ls
from pipeline import prospect_ratings as pr
from tests.test_inbox_store import FakeCursor
from tests.test_prospect_ids import scouting_db

RATINGS = {"Speed": 91, "Acceleration": 90, "ThrowPower": 60}


def player(pgid, first, last, pos, status="Draft", ratings=RATINGS, ovr=70):
    return {"pgid": pgid, "first_name": first, "last_name": last, "position": pos, "status": status,
            "ratings": dict(ratings) if ratings else ratings, "college": None, "height_in": 72, "weight_lbs": 200,
            "age": 21, "overall": ovr, "archetype": "WR_DeepThreat"}


def snapshot():
    players = [player(1, "Cam", "Ward", "QB"), player(2, "Abdul", "Carter", "LE"),
               player(3, "Travis", "Hunter", "WR", ratings={}),            # no ratings: skipped
               player(4, "Bryce", "Young", "QB", status="Signed"),         # not a prospect: skipped
               player(5, "Mason", "Graham", "DT")]
    draft_class = [{"pgid": p, "initial_draft_rank": i} for i, p in enumerate((1, 2, 3, 5), start=1)]
    return {"players": players, "draft_class": draft_class}


def fresh():
    return FakeCursor(scouting_db())


def test_pure_rows_skip_non_draft_and_unrated():
    rows = pr.prospect_ratings(snapshot(), 2027)
    assert [r["pgid"] for r in rows] == [1, 2, 5]
    assert rows[1]["prospect_key"] == prospect_key(2027, "Abdul Carter", "LEDG")
    assert rows[0]["ratings"] == RATINGS


def test_keys_match_the_draft_board():
    snap = snapshot()
    board = {prospect_key(2027, p.full_name, p.position) for p in ls.draft_board_prospects(snap)}
    assert {r["prospect_key"] for r in pr.class_rows(snap, 2027)} == board
    assert {r["prospect_key"] for r in pr.prospect_ratings(snap, 2027)} <= board


def test_class_rows_without_draft_class_use_all_draft_players():
    snap = snapshot()
    del snap["draft_class"]
    assert [r["pgid"] for r in pr.class_rows(snap, 2027)] == [1, 2, 3, 5]


def test_rows_keyed_by_uid_and_report_counts():
    cur, report = fresh(), {}
    assert pr.store_prospect_ratings(cur, snapshot(), 2027, "week1", report) == 3
    assert report["prospect_ids"] == {"allocated": 4, "renamed": 0, "collisions": 0}
    cur.execute("SELECT prospect_uid, pgid, prospect_key, ratings, capture_folder FROM scout_prospect_ratings "
                "ORDER BY pgid")
    got = cur.fetchall()
    assert [r[1] for r in got] == [1, 2, 5]
    assert got[0][0] == str(pids.uid_from_birth_key("save-pgid:2027:1"))
    assert got[0][3] == RATINGS and got[0][4] == "week1"


def test_upsert_is_idempotent():
    cur = fresh()
    pr.store_prospect_ratings(cur, snapshot(), 2027, "week1")
    snap = snapshot()
    snap["players"][0]["ratings"]["Speed"] = 80
    assert pr.store_prospect_ratings(cur, snap, 2027, "week2") == 3
    cur.execute("SELECT count(*) FROM scout_prospect_ratings")
    assert cur.fetchone()[0] == 3
    cur.execute("SELECT ratings, capture_folder FROM scout_prospect_ratings WHERE pgid = 1")
    ratings, folder = cur.fetchone()
    assert ratings["Speed"] == 80 and folder == "week2"
    cur.execute("SELECT count(*) FROM scout_prospect_ids")
    assert cur.fetchone()[0] == 4


def test_collisions_are_skipped():
    snap = snapshot()
    snap["players"].append(player(6, "Cam", "Ward", "QB"))            # same name and group as pgid 1
    snap["draft_class"].append({"pgid": 6, "initial_draft_rank": 9})
    cur, report = fresh(), {}
    assert pr.store_prospect_ratings(cur, snap, 2027, "week1", report) == 2
    assert report["prospect_ids"]["collisions"] == 2
    cur.execute("SELECT pgid FROM scout_prospect_ratings ORDER BY pgid")
    assert [r[0] for r in cur.fetchall()] == [2, 5]


def test_missing_table_returns_zero():
    cur = FakeCursor(sqlite3.connect(":memory:", isolation_level=None))
    report = {}
    assert pr.store_prospect_ratings(cur, snapshot(), 2027, "week1", report) == 0
    assert "prospect_ids" not in report


class RecordingCursor:
    """A fake cursor that records statements: the tables exist, the class has no IDs yet."""

    def __init__(self):
        self.calls, self._next = [], None

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self._next = [(True,)] if "to_regclass" in sql else []

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return self._next or []


def test_load_save_calls_it_for_the_coming_class(monkeypatch):
    seen = {}

    def fake_store(cur, snap, draft_season, folder, report=None):
        seen.update(draft_season=draft_season, folder=folder)
        report["prospect_ids"] = {"allocated": 0, "renamed": 0, "collisions": 0}
        return 7

    monkeypatch.setattr(ls, "store_prospect_ratings", fake_store)
    for name in ("team_ids", "load_team_ratings", "load_players", "load_player_attributes", "load_games",
                 "load_game_lines", "load_team_scores", "load_season_lines", "load_contracts", "load_team_caps",
                 "load_injuries", "load_highlights", "load_depth_charts", "load_draft_class", "league_settings_report",
                 "update_league_state"):
        monkeypatch.setattr(ls, name, lambda *a, **k: {})
    monkeypatch.setattr(ls, "current_week", lambda snap: 3)
    snap = {"meta": {"game_year": 27},
            "season": {"calendar_year": 2026, "franchise_year": 0, "week_type": "RegularSeason", "week_index": 2,
                       "stage": 0}}
    report = ls.load_snapshot(RecordingCursor(), snap, capture_folder="week3")
    assert report["prospect_ratings"] == 7 and seen == {"draft_season": 2027, "folder": "week3"}
    assert "prospect_ratings" in ls.REPORT_COUNTS


def test_store_writes_only_its_tables():
    cur = RecordingCursor()
    pr.store_prospect_ratings(cur, snapshot(), 2027, "week1")
    writes = [sql for sql, _ in cur.calls if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes and all("scout_prospect_ids" in s or "scout_prospect_ratings" in s for s in writes)
