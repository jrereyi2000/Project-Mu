"""Step, pipeline and API wiring (SCOUTING_REVAMP_PLAN.md §2.5, §2.7, §4; U4).

Offline: the DB paths run against an in-memory SQLite database built from the real scouting-reports migration
(translated by the inbox tests' helpers); the engine/pipeline paths are mocked at their DB seams. No PostgreSQL
connection is opened.
"""
import inspect
import json
import pathlib
import sqlite3
from contextlib import ExitStack
from unittest import mock

from engines import scouting_engine as se
from engines import scouting_events as ev
from engines import scouting_interviews as si
from tests.test_inbox_store import FakeCursor, sqlite_ddl

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "db" / "migrations" / "2027_01_21_scouting_reports.sql"

BODY = {"summary": "template summary", "strengths": "s", "concerns": "c", "production": "prod",
        "athletic": "", "projection": "p", "bottom_line": "template bottom line"}


def _uid(i):
    return f"00000000-0000-0000-0000-{i:012d}"


def _report(uid, key, step=4, edition="FALL", text_source="TEMPLATE", body=None):
    return {"draft_season": 2037, "step": step, "prospect_uid": uid, "prospect_key": key, "edition": edition,
            "grade": 80.0, "rank": 1, "round_label": "1st round", "role": "DAY1_STARTER",
            "traits": [], "strengths": [], "concerns": [], "flags": [],
            "body": body or dict(BODY), "text_source": text_source}


def _qb_class(n):
    cls, cons = [], {}
    for i in range(n):
        key = f"2037:QB{i:04d}:QB"
        cls.append({"key": key, "full_name": f"QB {i}", "position": "QB", "group": "QB", "status": "ACTIVE",
                    "school": {"display": "Ohio State", "level": "P4"}, "college": "OHIO STATE", "age": 22,
                    "height_in": 75, "weight": 220, "class_year": "senior", "first_step": 4, "V": 80.0 - i})
        cons[key] = {"err": (i - n / 2) * 0.1, "grade": 80.0 - i, "rank": i + 1, "round_label": "1st round"}
    return cls, cons


class WriteCursor(FakeCursor):
    """FakeCursor plus executemany (scouting_engine._write_step uses it for consensus and report rows)."""

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)


def _write_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(sqlite_ddl(MIGRATION.read_text()))
    conn.executescript("""
        CREATE TABLE scout_runs (draft_season INT, step INT, season INT, week INT, stage INT, params TEXT,
                                 n_events INT);
        CREATE TABLE scout_events (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, step INT,
                                   prospect_key TEXT, kind TEXT, team_id INT, payload TEXT, impact REAL,
                                   headline TEXT, blurb TEXT, text_source TEXT, seq INT);
        CREATE TABLE scout_consensus (draft_season INT, step INT, prospect_key TEXT, season INT, week INT,
                                      stage INT, grade REAL, rank INT, round_label TEXT, delta_grade REAL,
                                      delta_rank INT, err REAL, event_ids TEXT);
        CREATE TABLE scout_team_boards (draft_season INT, step INT, team_id INT, prospect_key TEXT,
                                        team_grade REAL, team_rank INT, fit REAL, bias REAL, err REAL);
    """)
    return conn


# ----------------------------------------------------------------------------------------- T-W1 ----
def test_default_extras_stores_reports_only_on_edition_steps():
    uid = _uid(0)
    cls, cons = _qb_class(1)
    for step, edition in ((4, "FALL"), (21, "BOWLS"), (26, "FINAL"), (22, None)):
        out = {"cls": cls, "cons": cons, "events": {}, "first_step": 4}
        cur = mock.MagicMock()
        if step in (25, 26):
            cur.fetchall.return_value = [(5, "AAA", "Alphas")]
        with mock.patch.object(se, "team_contexts", return_value={5: {"need_groups": ["QB"]}}), \
             mock.patch.object(se, "stored_visits", return_value={}), \
             mock.patch.object(se, "volatility", return_value={}), \
             mock.patch.object(se, "_user_teams", return_value=[]), \
             mock.patch("engines.scouting_boards.board", return_value=[]), \
             mock.patch("engines.prospect_ids.class_ids",
                        return_value={"k": (uid, "2037:QB0000:QB", "OK")}), \
             mock.patch("engines.scouting_reports.load_ratings", return_value={}):
            se.default_extras(cur, out, step, 2036)
        if edition is None:
            assert "reports" not in out
        else:
            assert [r["edition"] for r in out["reports"]] == [edition]
            assert out["reports"][0]["prospect_uid"] == uid
            assert out["reports"][0]["prospect_key"] == "2037:QB0000:QB"


def test_write_step_inserts_report_rows_with_explicit_columns():
    conn = _write_db()
    uid, key = _uid(0), "2037:QB0:QB"
    out = {"events": {}, "cons": {}, "boards": [], "reports": [_report(uid, key)]}
    cur = WriteCursor(conn)
    assert se._write_step(cur, 2037, 4, 2036, out) == 0
    cur.execute("SELECT edition, grade, rank, role, text_source, prospect_uid FROM scout_reports")
    assert cur.fetchall() == [("FALL", 80.0, 1, "DAY1_STARTER", "TEMPLATE", uid)]


def test_write_step_keeps_llm_prose_when_grade_and_rank_unchanged():
    conn = _write_db()
    uid, key = _uid(0), "2037:QB0:QB"
    llm_body = {"summary": "kept", "bottom_line": "kept bottom", "strengths": "s", "concerns": "c",
                "production": "p", "athletic": "", "projection": "p"}
    out = {"events": {}, "cons": {}, "boards": [], "reports": [_report(uid, key)]}
    cur = WriteCursor(conn)
    se._write_step(cur, 2037, 4, 2036, out, None, {(uid, "FALL"): (80.0, 1, llm_body)})
    cur.execute("SELECT body, text_source FROM scout_reports WHERE prospect_uid = %s", (uid,))
    body, src = cur.fetchone()
    assert src == "LLM" and body == llm_body


def test_write_step_drops_llm_prose_when_grade_changes():
    conn = _write_db()
    uid, key = _uid(0), "2037:QB0:QB"
    out = {"events": {}, "cons": {}, "boards": [], "reports": [_report(uid, key)]}   # grade 80.0, rank 1
    cur = WriteCursor(conn)
    se._write_step(cur, 2037, 4, 2036, out, None, {(uid, "FALL"): (79.0, 1, {"summary": "stale"})})
    cur.execute("SELECT text_source FROM scout_reports")
    assert cur.fetchone()[0] == "TEMPLATE"


def test_run_replace_captures_llm_reports():
    conn = _write_db()
    uid, key = _uid(0), "2037:QB0:QB"
    llm_body = {"summary": "kept", "bottom_line": "kept bottom"}
    conn.execute("""INSERT INTO scout_reports (draft_season, step, prospect_uid, prospect_key, edition, grade, rank,
                       round_label, role, traits, strengths, concerns, flags, body, text_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (2037, 4, uid, key, "FALL", 80.0, 1, "1st round", "DAY1_STARTER", "[]", "[]", "[]", "[]",
                  json.dumps(llm_body), "LLM"))
    cur = WriteCursor(conn)
    with mock.patch.object(se, "current_step", return_value=(2036, 4)), \
         mock.patch.object(se, "_observe", return_value=None), \
         mock.patch.object(se, "stored_steps", return_value=[4]), \
         mock.patch.object(se, "compute", return_value={"cls": [], "first_step": 4, "cons": {}, "events": {},
                                                        "prev_step": None}), \
         mock.patch.object(se, "_write_step") as write:
        se.run(cur, 2037, 4, replace=True, log=lambda *a: None)
    args, kwargs = write.call_args
    assert args[5] == {}
    assert args[6] == {(uid, "FALL"): (80.0, 1, llm_body)}


# ----------------------------------------------------------------------------------------- T-W2 ----
def test_visits_per_step_matches_events_default():
    assert si.VISITS_PER_STEP == 6
    assert inspect.signature(ev.visits).parameters["per_team"].default == si.VISITS_PER_STEP


def test_user_team_visits_swap_and_cpu_unchanged():
    from engines import scouting_boards as sb
    n = 20
    cls, cons = _qb_class(n)
    by_key = {p["key"]: p for p in cls}
    cands = [{"key": k, "group": sb.needs_group(by_key[k].get("position")), "full_name": by_key[k]["full_name"]}
             for k, r in sorted(cons.items(), key=lambda x: x[1]["rank"])[:100]]
    ctxs = {5: {"need_groups": ["QB"]}, 6: {"need_groups": ["QB"]}}
    seeded = ev.visits(25, {t: {"need_groups": c["need_groups"]} for t, c in ctxs.items()}, cands, 2037)
    seeded5 = sorted(k for tid, k in seeded if tid == 5)
    seeded6 = sorted(k for tid, k in seeded if tid == 6)
    jr = seeded5[:2]                                 # JR already interviewed two of his team's prospects
    out = {"cls": cls, "cons": cons, "events": {}, "first_step": 4}
    cur = mock.MagicMock()
    cur.fetchall.return_value = [(5, "AAA", "Alphas"), (6, "BBB", "Betas")]
    with mock.patch.object(se, "team_contexts", return_value=ctxs), \
         mock.patch.object(se, "stored_visits", return_value={}), \
         mock.patch.object(se, "volatility", return_value={}), \
         mock.patch.object(se, "_user_teams", return_value=["AAA"]), \
         mock.patch("engines.scouting_boards.board", return_value=[]), \
         mock.patch("engines.scouting_interviews.jr_visit_keys", return_value=jr):
        se.default_extras(cur, out, 25, 2036)
    visits = {}
    for key, evs in out["events"].items():
        for e in evs:
            if e["kind"] == "VISIT":
                visits.setdefault(e["team_id"], []).append(key)
    expected_user = jr + [k for k in seeded5 if k not in jr][:si.VISITS_PER_STEP - len(jr)]
    assert visits[5] == expected_user and len(visits[5]) == 6
    assert set(visits[6]) == set(seeded6) and len(visits[6]) == 6


# ----------------------------------------------------------------------------------------- T-W3 ----
class _ScriptedCur:
    def __init__(self, editions_by_step, counts=(1, 1)):
        self.editions_by_step = editions_by_step
        self.counts = counts
        self.last_sql, self.last_params = None, None

    def execute(self, sql, params=()):
        self.last_sql, self.last_params = sql, params

    def fetchone(self):
        return self.counts

    def fetchall(self):
        if self.last_sql and "DISTINCT edition" in self.last_sql:
            step = self.last_params[1]
            return [(ed,) for ed in self.editions_by_step.get(step, [])]
        return []


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True


def _patch_step_scouting(done, editions_by_step, rewrite):
    import pipeline.weekly_capture as wc
    cur = _ScriptedCur(editions_by_step)
    conn = _FakeConn(cur)
    patches = [
        mock.patch("psycopg.connect", return_value=conn),
        mock.patch("engines.gm_llm.make_llm", return_value=lambda p: "x"),
        mock.patch("engines.scouting_engine.installed", return_value=True),
        mock.patch("engines.scouting_engine.current_step", return_value=(2036, max(done or [26]))),
        mock.patch("engines.scouting_engine.catch_up", return_value=done),
        mock.patch("engines.scouting_reports.rewrite", side_effect=rewrite),
        mock.patch.object(wc, "log", lambda *a: None),
    ]
    return conn, patches


def test_step_scouting_rewrites_only_edition_steps():
    import pipeline.weekly_capture as wc
    done = [4, 21, 26]
    calls = []

    def rewrite(c, ds, ed, llm):
        calls.append((ds, ed))
        return 1

    conn, patches = _patch_step_scouting(done, {4: ["FALL"], 21: ["BOWLS"], 26: ["FINAL"]}, rewrite)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = wc.step_scouting(2036)
    assert result == done
    assert calls == [(2037, "FALL"), (2037, "BOWLS"), (2037, "FINAL")]


def test_step_scouting_survives_a_rewrite_error():
    import pipeline.weekly_capture as wc
    done = [21]

    def boom(c, ds, ed, llm):
        raise RuntimeError("quota")

    conn, patches = _patch_step_scouting(done, {21: ["BOWLS"]}, boom)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = wc.step_scouting(2036)
    assert result == done
    assert conn.committed


# ----------------------------------------------------------------------------------------- T-W4 ----
def test_load_prospects_allocates_scanned_class_ids():
    import pipeline.load_capture as lc
    from pipeline.ingest_prospects import Prospect
    from engines.scouting_value import prospect_key
    prospects = [Prospect(projected_pick=1, full_name="QB One", position="QB", college="ALA", height="6'2\"",
                          weight=220, age=22, overall_rating=88)]
    cur = mock.MagicMock()
    report = {}
    ensure = mock.Mock(return_value={"allocated": 1, "renamed": 0, "collisions": 0})
    with mock.patch("pipeline.ingest_prospects.load_prospects_from_csv", return_value=prospects), \
         mock.patch("pipeline.draft_board.write_board", return_value=1), \
         mock.patch.object(lc, "class_personality", return_value={"mode": "observe"}), \
         mock.patch("engines.scouting_engine.relink", return_value={"linked": 0, "withdrawn": 0, "new": 0}), \
         mock.patch("engines.prospect_ids.ensure_prospect_ids", ensure), \
         mock.patch("os.path.exists", return_value=True), \
         mock.patch("os.path.abspath", side_effect=lambda p: p), \
         mock.patch("os.makedirs"), mock.patch("shutil.copyfile"):
        assert lc.load_prospects(cur, "/tmp/scan", 2036, report) == 1
    ensure.assert_called_once_with(cur, 2037, [{"prospect_key": prospect_key(2037, "QB One", "QB"), "pgid": None}],
                                   "scan")
    assert report["prospect_ids"] == {"allocated": 1, "renamed": 0, "collisions": 0}


def test_collision_prospect_gets_no_report():
    uid_ok = _uid(0)
    cls, cons = _qb_class(2)
    out = {"cls": cls, "cons": cons, "events": {}, "first_step": 4}
    cur = mock.MagicMock()
    with mock.patch.object(se, "team_contexts", return_value={5: {"need_groups": ["QB"]}}), \
         mock.patch.object(se, "stored_visits", return_value={}), \
         mock.patch.object(se, "volatility", return_value={}), \
         mock.patch.object(se, "_user_teams", return_value=[]), \
         mock.patch("engines.scouting_boards.board", return_value=[]), \
         mock.patch("engines.prospect_ids.class_ids", return_value={
             "k0": (uid_ok, "2037:QB0000:QB", "OK"),
             "k1": (_uid(1), "2037:QB0001:QB", "KEY_COLLISION")}), \
         mock.patch("engines.scouting_reports.load_ratings", return_value={}):
        se.default_extras(cur, out, 4, 2036)
    assert [r["prospect_key"] for r in out["reports"]] == ["2037:QB0000:QB"]
