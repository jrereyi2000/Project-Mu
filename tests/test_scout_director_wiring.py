"""Scouting-director wiring (OWNERS_SCOUTS_VOTERS U6 follow-up; CLOSE_OUT_CHECKLIST P1-11; PROJECT_SPEC §2.18).

Offline: the report/board paths are pure or mocked at their DB seams. A director voice shifts his team's private
report row only; the department row, the consensus and a prospect's true ratings never change.
"""
import sqlite3

from engines import scout_directors as sd
from engines import scouting_boards as sb
from engines import scouting_engine as se
from engines import scouting_reports as sr
from tests.test_inbox_store import FakeCursor


def _director(team_id=5, traits=("TAPE_GRINDER",)):
    return {"scout_id": 11, "team_id": team_id, "name": "Pat Scout", "persona_id": "scout-11", "start_season": 2026,
            "region": "AFC", "lens": sd.lens_for(traits), "traits": list(traits)}


def _class(n=2):
    cls, cons, uids = [], {}, {}
    for i in range(n):
        key = f"2037:QB{i}:QB"
        uid = f"00000000-0000-0000-0000-{i:012d}"
        cls.append({"key": key, "full_name": f"QB {i}", "position": "QB", "status": "ACTIVE",
                    "school": {"display": "Ohio State", "level": "P4"}, "college": "OHIO STATE", "age": 22,
                    "height_in": 75, "weight": 220, "class_year": "senior"})
        cons[key] = {"err": 0.0, "grade": 80.0 - i, "rank": i + 1, "round_label": "1st round"}
        uids[key] = uid
    return cls, cons, uids


def _edition(cls, cons, uids, directors=None, team_keys=None, signals=None):
    return sr.build_edition(cls, cons, {}, {}, uids, 26, 4, 2037, {}, directors=directors, team_keys=team_keys,
                            signals=signals)


def test_department_rows_unchanged_and_no_team_rows_without_directors():
    cls, cons, uids = _class()
    rows = _edition(cls, cons, uids)
    assert len(rows) == 2
    assert all(r["team_id"] is None and r["scout_id"] is None and r["voice"] is None for r in rows)


def test_team_row_carries_voice_and_bias_but_department_row_does_not_move():
    cls, cons, uids = _class()
    signals = {p["key"]: {"production": 100.0} for p in cls}
    rows = _edition(cls, cons, uids, directors=[_director(traits=("TAPE_GRINDER",))], signals=signals)
    dept = [r for r in rows if r["team_id"] is None]
    team = [r for r in rows if r["team_id"] == 5]
    assert len(dept) == 2 and len(team) == 2
    for d, t in zip(sorted(dept, key=lambda r: r["rank"]), sorted(team, key=lambda r: r["rank"])):
        assert t["grade"] == round(d["grade"] + sd.MAX_BIAS, 1)       # full-production TAPE_GRINDER lean
        assert t["scout_id"] == 11 and t["voice"]["lens"] == "TAPE"
        assert t["body"] == d["body"] and t["traits"] == d["traits"]  # prose and ratings stay consensus
    assert dept[0]["grade"] == 80.0                                    # the consensus never moves


def test_floor_seeker_lowers_and_character_hawk_punishes_mixed_interviews():
    cls, cons, uids = _class(1)
    key = cls[0]["key"]
    floor = _edition(cls, cons, uids, directors=[_director(traits=("FLOOR_SEEKER",))],
                     signals={key: {"volatility": 1.0}})
    team = next(r for r in floor if r["team_id"] is not None)
    assert team["grade"] == round(cons[key]["grade"] - sd.MAX_BIAS, 1)
    hawk = _edition(cls, cons, uids, directors=[_director(traits=("CHARACTER_HAWK",))],
                    signals={key: {"flags": ["INTERVIEWS_MIXED"]}})
    assert next(r for r in hawk if r["team_id"] is not None)["grade"] == round(
        cons[key]["grade"] - sd.MAX_BIAS, 1)


def test_team_keys_limit_a_directors_rows():
    cls, cons, uids = _class(3)
    director = _director(traits=("TAPE_GRINDER",))
    rows = _edition(cls, cons, uids, directors=[director], team_keys={5: [cls[0]["key"]]},
                    signals={p["key"]: {"production": 100.0} for p in cls})
    assert [r["prospect_key"] for r in rows if r["team_id"] == 5] == [cls[0]["key"]]


def test_report_rows_serialize_the_new_columns():
    cls, cons, uids = _class(1)
    rows = _edition(cls, cons, uids, directors=[_director()],
                    signals={p["key"]: {"production": 100.0} for p in cls})
    stored = sr.report_rows(rows)
    assert len(stored[0]) == 18
    assert stored[0][15:] == (None, None, None)          # the department row: no team, scout or voice
    assert stored[1][15] == 5 and stored[1][16] == 11 and '"TAPE"' in stored[1][17]


def test_board_extra_bias_moves_a_boards_order():
    cls = [{"key": "a", "V": 70.0, "position": "QB", "age": 22},
           {"key": "b", "V": 70.2, "position": "QB", "age": 22}]
    cons = {p["key"]: {"err": 0.0, "rank": i + 1} for i, p in enumerate(cls)}
    ctx = {"team_id": 1, "q": 1.0, "needs": {}, "need_groups": [], "direction": "RETOOL", "position_bias": {},
           "youth_bias": 0.0, "risk": 0.5, "qb_security": 1.0}
    plain = sb.board(cls, cons, ctx, 4, 2037, 4)
    leaning = sb.board(cls, cons, ctx, 4, 2037, 4, extra_bias=lambda k: 5.0 if k == "a" else 0.0)
    assert plain[0][0] == "b" and leaning[0][0] == "a"
    plain_bias = {r[0]: r[4] for r in plain}
    assert leaning[0][4] == plain_bias["a"] + 5.0        # the lean is stored in the bias, not the error


def test_director_signals_from_stored_events():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE scout_events (id INTEGER PRIMARY KEY AUTOINCREMENT, prospect_key TEXT, kind TEXT, "
                 "payload TEXT, headline TEXT, blurb TEXT, step INT, draft_season INT)")
    for i, key in enumerate(("2037:A:QB", "2037:B:QB")):
        conn.execute("INSERT INTO scout_events (prospect_key, kind, payload, headline, blurb, step, draft_season) "
                     "VALUES (?, 'GAME', ?, '', '', 4, 2037)",
                     (key, f'{{"stats": {{"pass_yds": {100 * (i + 1)}}}}}'))
    cur = FakeCursor(conn)
    cls = [{"key": "2037:A:QB", "position": "QB"}, {"key": "2037:B:QB", "position": "QB"}]
    sig = sr.director_signals(cur, cls, 4, 2037, {"2037:B:QB": 0.5}, edition="FALL")
    assert sig["2037:A:QB"]["production"] == 0.0 and sig["2037:B:QB"]["production"] == 100.0
    assert sig["2037:B:QB"]["volatility"] == 0.5 and sig["2037:A:QB"]["athleticism"] is None


def test_directors_for_is_off_without_a_feature_or_a_lifecycle():
    cur = FakeCursor(sqlite3.connect(":memory:"))
    with_dirs, mode = se.directors_for(cur, 2036, lifecycle=lambda *a, **k: None)
    assert with_dirs == [] and mode == "off"            # no switchboard row: off
