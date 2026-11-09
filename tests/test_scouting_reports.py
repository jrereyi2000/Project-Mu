"""Report engine, scout voice hook and writer read (SCOUTING_REVAMP_PLAN.md §2.3-§2.4, §2.9, §4; U2).

Offline: pure model tests plus the DB readers against an in-memory SQLite database built from the real migration
(translated by the inbox tests' helpers). No PostgreSQL connection is opened.
"""
import json
import pathlib
import sqlite3

import pytest

from engines import scouting_read as sr_read
from engines import scouting_reports as sr
from engines import scout_voice as sv
from tests.test_inbox_store import FakeCursor, scout_reports_v2, sqlite_ddl

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "db" / "migrations" / "2027_01_21_scouting_reports.sql"

BODY = {"summary": "template summary", "strengths": "s", "concerns": "c", "production": "prod",
        "athletic": "", "projection": "p", "bottom_line": "template bottom line"}


# ------------------------------------------------------------------------------------------- pure helpers ---
def _qb_class(n):
    cls, cons, ratings, uids, vols, events = [], {}, {}, {}, {}, {}
    for i in range(n):
        key = f"2037:QB{i:04d}:QB"
        uid = f"00000000-0000-0000-0000-{i:012d}"
        uids[key] = uid
        t = i / max(n - 1, 1)
        ratings[key] = (uid, {"ThrowPower": 60 + 30 * t, "ThrowAccuracyShort": 60 + 20 * t,
                              "ThrowAccuracyMid": 60 + 18 * t, "ThrowAccuracyDeep": 60 + 16 * t,
                              "Awareness": 60 + 10 * t, "PlayAction": 60 + 10 * t,
                              "ThrowUnderPressure": 60 + 12 * t, "BreakSack": 60 + 8 * t,
                              "Speed": 60 + 8 * t, "ThrowOnTheRun": 60 + 8 * t})
        cls.append({"key": key, "full_name": f"QB {i}", "position": "QB", "group": "QB", "status": "ACTIVE",
                    "school": {"display": "Ohio State", "level": "P4"}, "college": "OHIO STATE", "age": 22,
                    "height_in": 75, "weight": 220, "class_year": "senior"})
        cons[key] = {"err": (i - n / 2) * 0.2, "grade": 90.0 - 20 * t, "rank": i + 1, "round_label": "1st round"}
        events[key] = []
        vols[key] = 0.0
    return cls, cons, ratings, uids, vols, events


def _corr(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else 0.0


def _trait_score(obs, trait):
    return next(t["score"] for t in obs if t["trait"] == trait)


def _keys(d):
    found, stack = set(), [d]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            found |= {str(k).upper() for k in x}
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return found


# ---------------------------------------------------------------------------------------------- T-R1 ----
def test_edition_for_first_step_bowls_final_and_late_class():
    assert sr.edition_for(4, 4) == "FALL"
    assert sr.edition_for(21, 4) == "BOWLS"
    assert sr.edition_for(26, 4) == "FINAL"
    assert sr.edition_for(5, 4) is None
    assert sr.edition_for(22, 4) is None
    assert sr.edition_for(27, 4) is None
    # a class first initialized after step 21 gets no FALL/BOWLS, only a FINAL edition
    assert sr.edition_for(22, 22) is None
    assert sr.edition_for(24, 24) is None
    assert sr.edition_for(26, 24) == "FINAL"
    assert sr.edition_for(26, 26) == "FINAL"


# ---------------------------------------------------------------------------------------------- T-R2 ----
def test_noise_falls_with_step_and_final_is_closer_to_truth():
    assert sr.sigma_t(4, 4) == sr.SIGMA_T0
    assert sr.sigma_t(4, 4) > sr.sigma_t(21, 4) > sr.sigma_t(26, 4) > sr.sigma_t(27, 4)
    assert sr.sigma_t(27, 4) == sr.SIGMA_T_DRAFT          # never below
    cls, cons, ratings, uids, _, _ = _qb_class(500)
    fall = sr._observe(cls, cons, ratings, uids, 4, 4)
    final = sr._observe(cls, cons, ratings, uids, 26, 4)
    truth = [60 + 30 * (i / 499) for i in range(500)]
    keys = sorted(uids)
    assert _corr([_trait_score(fall[k], "arm") for k in keys], truth) > 0.4
    assert _corr([_trait_score(final[k], "arm") for k in keys], truth) > \
        _corr([_trait_score(fall[k], "arm") for k in keys], truth)


def test_observations_are_deterministic_across_calls():
    cls, cons, ratings, uids, _, _ = _qb_class(30)
    assert sr._observe(cls, cons, ratings, uids, 4, 4) == sr._observe(cls, cons, ratings, uids, 4, 4)


# ---------------------------------------------------------------------------------------------- T-R3 ----
def test_scores_are_class_relative_1_to_9_and_never_expose_a_rating():
    cls, cons, ratings, uids, vols, events = _qb_class(40)
    reports = sr.build_edition(cls, cons, events, ratings, uids, 4, 4, 2037, vols)
    all_scores = [t["score"] for r in reports for t in r["traits"]]
    assert all_scores and min(all_scores) >= 1.0 and max(all_scores) <= 9.0
    assert min(all_scores) == 1.0 and max(all_scores) == 9.0     # class-relative spread reaches both ends
    for r in reports:
        assert {t["word"] for t in r["traits"]} <= {w for _, w in sr.SCORE_WORDS}
        assert not (_keys(r) & {"OVR", "TRUE_VALUE", "TRUE_RANK", "VALUE_INPUTS", "ERR", "IMPACT", "RATINGS"})


# ---------------------------------------------------------------------------------------------- T-R4 ----
def test_roles_by_rank_band():
    assert sr.role_for(1) == "IMPACT_STARTER" and sr.role_for(15) == "IMPACT_STARTER"
    assert sr.role_for(16) == "DAY1_STARTER" and sr.role_for(48) == "DAY1_STARTER"
    assert sr.role_for(49) == "EARLY_CONTRIBUTOR" and sr.role_for(100) == "EARLY_CONTRIBUTOR"
    assert sr.role_for(101) == "DEVELOPMENTAL" and sr.role_for(180) == "DEVELOPMENTAL"
    assert sr.role_for(181) == "DEPTH" and sr.role_for(260) == "DEPTH"
    assert sr.role_for(261) == "PRIORITY_FA"


# ---------------------------------------------------------------------------------------------- T-R5 ----
def test_flags_only_from_stored_events_and_payloads():
    p = {"key": "2037:X:QB", "full_name": "X", "position": "QB", "school": {"display": "Alabama", "level": "P4"},
         "college": "ALABAMA", "age": 22}
    evs = [
        {"kind": "STORYLINE", "payload": {"subtype": "injury"}},
        {"kind": "STORYLINE", "payload": {"subtype": "character"}},
        {"kind": "MEDICAL", "payload": {"result": "cleared"}},
        {"kind": "COMBINE", "payload": {"interviews": "mixed"}},
    ]
    assert set(sr.flags_for(p, evs, "FINAL", volatile=False)) == \
        {"INJURY_HISTORY", "CONSISTENCY", "INTERVIEWS_MIXED"}
    assert sr.flags_for(p, evs, "FALL", volatile=False) == ["INJURY_HISTORY", "CONSISTENCY"]    # mixed only at FINAL
    assert sr.flags_for(p, [], "FINAL", volatile=False) == []                                    # P4 school, age 22

    small = dict(p, school={"display": "Delaware", "level": "FCS"}, age=24)
    assert set(sr.flags_for(small, [], "FINAL", volatile=False)) == {"SMALL_SCHOOL", "AGE"}
    # a GAME event and an unrelated payload number never become a flag
    assert sr.flags_for(p, [{"kind": "GAME", "payload": {"stats": {"pass_td": 4}}}], "FINAL", volatile=False) == []


def test_volatile_is_the_top_quartile_only():
    vols = {f"k{i}": float(i) for i in range(20)}          # 0..19
    assert sr.volatile_keys(vols) == {f"k{i}" for i in range(15, 20)}        # top quarter
    assert sr.volatile_keys({f"k{i}": 0.0 for i in range(10)}) == set()      # no move history flags nobody


# ---------------------------------------------------------------------------------------------- T-R6 ----
def test_traits_free_mode():
    cls, cons, _, uids, vols, events = _qb_class(12)
    p = cls[0]
    events[p["key"]] = [{"kind": "STORYLINE", "payload": {"subtype": "rough_outing"}}]
    report = sr.build_report(p, cons[p["key"]], events[p["key"]], [],
                             {"production": "no line", "combine": None, "volatile": False}, "FALL", 4)
    assert report["traits"] == []
    assert report["strengths"] == []
    assert report["concerns"] == [sr.FLAG_WORDS["CONSISTENCY"]]
    assert report["flags"] == ["CONSISTENCY"]


# ---------------------------------------------------------------------------------------------- T-R7 ----
def _db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(sqlite_ddl(MIGRATION.read_text()))
    scout_reports_v2(conn)
    conn.executescript("""
        CREATE TABLE scout_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, prospect_key TEXT,
                                      prospect_id INT, class_year TEXT, conference TEXT);
        CREATE TABLE draft_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, full_name TEXT,
                                      position TEXT, college TEXT, age INT, height_in INT, weight INT);
    """)
    c = conn
    for i in range(3):
        key = f"2037:QB{i}:QB"
        c.execute("INSERT INTO scout_prospects (draft_season, prospect_key, prospect_id, class_year) "
                  "VALUES (?, ?, ?, ?)", (2037, key, i + 1, "senior"))
        c.execute("INSERT INTO draft_prospects (id, draft_season, full_name, position, college, age, height_in, weight) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (i + 1, 2037, f"QB {i}", "QB", "OHIO STATE", 22, 75, 220))
    return conn


def _uid(i):
    return f"00000000-0000-0000-0000-{i:012d}"


def _insert_report(conn, uid, key, rank, edition, step, text_source="TEMPLATE"):
    conn.execute("""INSERT INTO scout_reports (draft_season, step, prospect_uid, prospect_key, edition, grade, rank,
                       round_label, role, traits, strengths, concerns, flags, body, text_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (2037, step, uid, key, edition, 80.0, rank, "1st round", "DAY1_STARTER", "[]", "[]", "[]", "[]",
                  json.dumps(BODY), text_source))


@pytest.fixture
def cur():
    conn = _db()
    for i in range(3):
        _insert_report(conn, _uid(i), f"2037:QB{i}:QB", i + 1, "FALL", 4)
    return FakeCursor(conn)


def _rows(cur):
    cur.execute("SELECT rank, text_source, body, grade, role, prospect_key FROM scout_reports ORDER BY rank")
    return cur.fetchall()


GOOD = "A poised starter who wins with his hands and processes quickly."


def test_rewrite_touches_only_prose_and_marks_llm(cur):
    before = _rows(cur)
    n = sr.rewrite(cur, 2037, "FALL", lambda prompt: GOOD)
    assert n == 3                                             # all three TEMPLATE rows (limit 64)
    rows = _rows(cur)
    for rank, src, body, grade, role, key in rows:
        assert src == "LLM"
        assert body["summary"] == GOOD and body["bottom_line"] == GOOD
        assert body["production"] == "prod" and body["strengths"] == "s" and body["projection"] == "p"
        assert grade == 80.0 and role == "DAY1_STARTER"       # non-prose fields untouched
    assert before[0][3] == 80.0


def test_rewrite_respects_limit(cur):
    assert sr.rewrite(cur, 2037, "FALL", lambda prompt: GOOD, limit=2) == 2
    rows = _rows(cur)
    assert rows[0][1] == "LLM" and rows[1][1] == "LLM" and rows[2][1] == "TEMPLATE"


def test_rewrite_rejection_error_and_no_llm_keep_template(cur):
    assert sr.rewrite(cur, 2037, "FALL", lambda prompt: "A 74 OVR starter.") == 0     # rejection
    assert all(r[1] == "TEMPLATE" for r in _rows(cur))
    def boom(prompt):
        raise RuntimeError("quota")
    assert sr.rewrite(cur, 2037, "FALL", boom) == 0                                    # error -> template
    assert all(r[1] == "TEMPLATE" for r in _rows(cur))
    assert sr.rewrite(cur, 2037, "FALL", None) == 0                                    # no llm
    assert all(r[1] == "TEMPLATE" for r in _rows(cur))


def test_rewrite_keys_override_limit(cur):
    n = sr.rewrite(cur, 2037, "FALL", lambda prompt: GOOD, limit=1, keys=["2037:QB2:QB"])
    assert n == 1
    rows = _rows(cur)
    assert rows[0][1] == "TEMPLATE" and rows[1][1] == "TEMPLATE" and rows[2][1] == "LLM"


# ---------------------------------------------------------------------------------------------- T-R8 ----
def _reader_db():
    conn = _db()
    for i in range(3):
        key = f"2037:QB{i}:QB"
        uid = _uid(i)
        conn.execute("INSERT INTO scout_prospect_ids (prospect_uid, birth_key, draft_season, prospect_key, status, "
                     "first_seen) VALUES (?, ?, ?, ?, 'OK', 'week1')",
                     (uid, f"save-pgid:2037:{i + 10}", 2037, key))
        step, edition = (4, "FALL") if i == 0 else (26, "FINAL")
        _insert_report(conn, uid, key, i + 1, edition, step)
    return conn


def test_report_for_returns_public_fields_only():
    cur = FakeCursor(_reader_db())
    r = sr.report_for(cur, _uid(0))
    assert r is not None and r["edition"] == "FALL" and r["role_words"] == sr.ROLE_WORDS["DAY1_STARTER"]
    assert set(r) >= {"prospect_uid", "prospect_key", "draft_season", "step", "edition", "grade", "rank",
                      "round_label", "role", "role_words", "traits", "strengths", "concerns", "flags", "body",
                      "text_source"}
    blob = json.dumps(r).upper()
    for hidden in ("OVR", "TRUE_VALUE", "ERR", "IMPACT", "RATINGS", "THROWPOWER"):
        assert hidden not in blob
    assert sr.report_for(cur, "ffffffff-ffff-ffff-ffff-ffffffffffff") is None


def test_scout_report_resolves_alias_and_respects_through_step():
    conn = _reader_db()
    cur = FakeCursor(conn)
    r = sr_read.scout_report(cur, "2037:QB0:QB")
    assert r is not None and r["edition"] == "FALL" and r["prospect_uid"] == _uid(0)
    assert sr_read.scout_report(cur, "2037:NOSUCH:QB") is None
    # give QB0 a FINAL edition too, then through_step selects the right one
    _insert_report(conn, _uid(0), "2037:QB0:QB", 1, "FINAL", 26)
    assert sr_read.scout_report(cur, "2037:QB0:QB")["edition"] == "FINAL"
    assert sr_read.scout_report(cur, "2037:QB0:QB", through_step=10)["edition"] == "FALL"
    assert sr_read.scout_report(cur, "2037:QB0:QB", through_step=26)["edition"] == "FINAL"


# ---------------------------------------------------------------------------------------------- T-R9 ----
def test_editions_for_returns_stored_editions_in_step_order():
    conn = _db()
    uid = _uid(0)
    _insert_report(conn, uid, "2037:QB0:QB", 1, "FINAL", 26)
    _insert_report(conn, uid, "2037:QB0:QB", 1, "FALL", 4)   # stored out of step order; chronological wins
    cur = FakeCursor(conn)
    assert sr.editions_for(cur, uid) == ["FALL", "FINAL"]
    assert sr.editions_for(cur, _uid(9)) == []


def test_scout_voice_hook_is_neutral():
    dept = sv.department_voice()
    assert dept.scout_id is None and dept.name is None and dept.region is None and dept.lens is None
    assert sv.regional_bias(dept, {}) == 0.0
    assert sv.regional_bias(sv.ScoutVoice(scout_id=1, region="SEC", lens="TAPE"), {}) == 0.0
    assert sv.REGIONS == {"AFC": "AFC", "NFC": "NFC"}
    assert sv.voice_prompt(dept) == "a neutral national scouting service"
