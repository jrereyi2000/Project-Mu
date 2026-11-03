"""Verified progression writer privacy, temporal boundaries and legacy compatibility."""
import json
import pytest
from unittest.mock import MagicMock

from newsroom import db_tools as dt
from newsroom.run_context import article_run_context


def public_report(season=2037, week=4, kind="CHECKPOINT", **extra):
    return {"schema_version": 1, "status": "APPLIED",
            "identity": {"player_id": 17, "event_id": f"event-{season}-{week}", "kind": kind},
            "calendar": {"earned_season": season, "application_season": season, "week": week},
            "permanent": {"before_ovr": 80, "after_ovr": 81, "planned_ovr": 81,
                          "rating_changes": {"Awareness": 1}},
            "form": {"before_requested": 0, "after_requested": 2,
                     "before_effective_ovr": 80, "after_effective_ovr": 83},
            "reasons": ["EXPERIENTIAL_LEARNING"],
            "evidence": {"ids": ["g1", "g2"], "count": 2, "reliability": .5},
            "development_label": {"desired": "Star", "actual_before": "Normal", "actual_after": "Normal"},
            "failures": [], **extra}


def event(season=2037, week=4, kind="CHECKPOINT", status="APPLIED", report=None):
    return {"season": season, "week": week, "kind": kind, "status": status,
            "report": public_report(season, week, kind) if report is None else report,
            "event_id": (public_report(season, week, kind) if report is None else report).get("identity", {}).get("event_id")}


class Cursor:
    def __init__(self, events=(), history=(), has_events=True, has_history=True):
        self.events, self.history = events, history
        self.has_events, self.has_history = has_events, has_history
        self.calls, self.result = [], []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "to_regclass" in sql:
            self.result = [("progression_events" if self.has_events else None,
                            "player_progression_history" if self.has_history else None)]
        elif "SELECT application_season, kind, event_id, metadata" in sql:
            pid, season, _, cut, _ = params
            self.result = [(e["season"], e["kind"], e["event_id"], e["report"]) for e in self.events
                           if e["status"] == "APPLIED" and e["season"] <= season
                           and (e["season"] < season or cut is None
                                or isinstance(e.get("application_week", 0 if e["kind"] == "OFFSEASON"
                                    and e["season"] > e.get("earned_season", e["season"]) else e["week"]), int)
                                and e.get("application_week", 0 if e["kind"] == "OFFSEASON"
                                    and e["season"] > e.get("earned_season", e["season"]) else e["week"]) < cut)]
        elif "FROM player_progression_history" in sql:
            annual = {e["season"] for e in self.events if e["kind"] == "OFFSEASON" and e["status"] == "APPLIED"}
            self.result = [row for row in self.history if row[0] <= params[1]
                           and ("NOT EXISTS" not in sql or row[0] not in annual)]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


def setup_tool(monkeypatch, **kwargs):
    cur = Cursor(**kwargs)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cur
    monkeypatch.setattr(dt.psycopg, "connect", lambda *a, **kw: conn)
    monkeypatch.setattr(dt, "resolve_season", lambda season: season or 2037)
    monkeypatch.setattr(dt, "_resolve_player_id", lambda cur, identifier: 17)
    return cur


def invoke(season=0):
    return dt.get_player_progression.invoke({"player_identifier": "17", "season": season})


def test_progression_tool_selects_only_applied_public_projection_and_legacy_whitelist(monkeypatch):
    cur = setup_tool(monkeypatch, events=[event()], history=[(2036, 80, 82, 2, "PLAYING_TIME", "Star", "Superstar")])
    with article_run_context(2037, 5):
        result = json.loads(invoke(2040))
    assert len(result["events"]) == 1
    assert result["changes"][0] == dict(season=2036, old_ovr=80, new_ovr=82, delta_ovr=2,
                                        cause="PLAYING_TIME", dev_before="Star", dev_after="Superstar")
    sql, params = next(call for call in cur.calls if "FROM progression_events" in call[0])
    assert params == (17, 2037, 2037, 5, 5)
    assert "status = 'APPLIED'" in sql and "metadata->'report_public'" in sql
    assert "metadata->>'week'" in sql and "END < %s" in sql
    for hidden in ("candidate_base", "candidate_band", "source_fingerprint", "annual_deltas", "SELECT *"):
        assert hidden not in sql
    legacy_sql = next(sql for sql, _ in cur.calls if "FROM player_progression_history" in sql)
    for hidden in ("ceiling", "durability", "ratings_diff", "persona_term"):
        assert hidden not in legacy_sql


def test_current_week_future_and_prepared_events_are_invisible(monkeypatch):
    setup_tool(monkeypatch, events=[event(2036, 23), event(2037, 3), event(2037, 4),
                                  event(2038, 1), event(2037, 2, status="PREPARED")], has_history=False)
    with article_run_context(2037, 4):
        result = json.loads(invoke(2045))
    assert [(r["calendar"]["application_season"], r["calendar"]["week"]) for r in result["events"]] == [(2036, 23), (2037, 3)]


def test_explicit_prior_season_has_no_current_week_cutoff(monkeypatch):
    cur = setup_tool(monkeypatch, events=[event(2036, 23), event(2037, 1)], has_history=False)
    with article_run_context(2037, 2):
        result = json.loads(invoke(2036))
    assert len(result["events"]) == 1 and result["events"][0]["calendar"]["week"] == 23
    assert next(params for sql, params in cur.calls if "FROM progression_events" in sql) == (17, 2036, 2036, None, None)


def test_backdated_context_cannot_see_later_season_or_week(monkeypatch):
    setup_tool(monkeypatch, events=[event(2035, 23), event(2036, 2), event(2036, 3), event(2037, 1)], has_history=False)
    with article_run_context(2036, 3):
        result = json.loads(invoke(2040))
    assert [r["identity"]["event_id"] for r in result["events"]] == ["event-2035-23", "event-2036-2"]


def test_reports_work_without_legacy_history_table(monkeypatch):
    cur = setup_tool(monkeypatch, events=[event()], has_history=False)
    with article_run_context(2037, 5):
        result = json.loads(invoke())
    assert result["events"] and result["changes"] == []
    assert not any("FROM player_progression_history" in sql for sql, _ in cur.calls)


def test_legacy_history_still_works_without_event_table(monkeypatch):
    setup_tool(monkeypatch, history=[(2037, 80, 82, 2, "PLAYING_TIME", "Star", "Superstar")], has_events=False)
    with article_run_context(2037, 5):
        result = json.loads(invoke())
    assert len(result["changes"]) == 1 and result["events"] == []


def test_matching_offseason_report_suppresses_duplicate_annual_summary(monkeypatch):
    setup_tool(monkeypatch, events=[event(kind="OFFSEASON")], history=[
        (2037, 80, 82, 2, "PLAYING_TIME", "Star", "Superstar"),
        (2036, 78, 80, 2, "PLAYING_TIME", "Normal", "Star")])
    with article_run_context(2037, 5):
        result = json.loads(invoke())
    assert len(result["events"]) == 1
    assert [row["season"] for row in result["changes"]] == [2036]


def test_contaminated_payload_is_sanitized_again(monkeypatch):
    report = public_report(audit={"ceiling": 99}, candidate_band={"mental": 99})
    report["permanent"]["ceiling"] = 99
    report["evidence"]["source_fingerprint"] = "private-hash"
    setup_tool(monkeypatch, events=[event(report=report)], has_history=False)
    with article_run_context(2037, 5):
        text = invoke()
    result = json.loads(text)
    assert len(result["events"]) == 1
    assert "private-hash" not in text and "candidate_band" not in text and '"ceiling"' not in text


def test_missing_malformed_or_mismatched_reports_are_unknown(monkeypatch):
    wrong = public_report()
    wrong["identity"]["player_id"] = 18
    bad = public_report()
    bad["permanent"]["after_ovr"] = "invented"
    future = public_report(week=8)
    setup_tool(monkeypatch, events=[event(report={}), event(report=wrong), event(report=bad), event(report=future)], has_history=False)
    with article_run_context(2037, 5):
        result = json.loads(invoke())
    assert not result["events"] and "do not infer" in result["note"]


def test_no_tables_and_database_failure_do_not_invent_causes(monkeypatch):
    cur = setup_tool(monkeypatch, has_events=False, has_history=False)
    assert "Do not infer" in invoke() and len(cur.calls) == 1
    def fail(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(dt.psycopg, "connect", fail)
    assert "Do not infer" in invoke()


def test_offseason_application_week_zero_visible_before_first_new_year_game(monkeypatch):
    report = public_report(2037, 0, "OFFSEASON")
    report["calendar"]["earned_season"] = 2036
    row = {**event(2037, 24, "OFFSEASON", report=report), "earned_season": 2036}
    setup_tool(monkeypatch, events=[row], has_history=False)
    with article_run_context(2037, 1):
        result = json.loads(invoke())
    assert len(result["events"]) == 1 and result["events"][0]["calendar"]["week"] == 0


def test_invisible_or_malformed_applied_annual_event_cannot_leak_via_legacy_row(monkeypatch):
    setup_tool(monkeypatch, events=[event(2037, 4, "OFFSEASON", report={})],
               history=[(2037, 80, 82, 2, "PLAYING_TIME", "Star", "Superstar")])
    with article_run_context(2037, 3):
        result = json.loads(invoke())
    assert result["events"] == [] and result["changes"] == []


from tests.test_progression_state import scratch


@pytest.mark.integration
def test_real_sql_search_path_application_time_and_annual_dedup(scratch, monkeypatch):
    from engines import progression_state as state
    conn, cur, _, _ = scratch
    cur.execute("""CREATE TABLE player_progression_history (player_id INT, season INT,
        old_ovr INT,new_ovr INT,delta_ovr INT,cause TEXT,dev_before TEXT,dev_after TEXT,checkpoint TEXT)""")
    cur.execute("INSERT INTO player_progression_history VALUES (1,2037,80,81,1,'AGE','Star','Star','OFFSEASON'),"
                "(1,2035,78,80,2,'PLAYING_TIME','Normal','Star','OFFSEASON')")
    state.bootstrap_state(cur, 1, 2037, {"Awareness": 80}, {"mental": 90}, "fixture",
                          confidence=50, form_neutral=True)
    for kind, week, earned, status in (("OFFSEASON", 0, 2036, "APPLIED"),
                                      ("CHECKPOINT", 1, 2037, "APPLIED"),
                                      ("CHECKPOINT", 0, 2037, "PREPARED")):
        report = public_report(2037, week, kind)
        report["identity"]["player_id"] = 1
        report["calendar"]["earned_season"] = earned
        event = state.prepare_event(cur, player_id=1, earned_season=earned, application_season=2037,
            kind=kind, ordinal=str(week), rules_version="writer-test", source_fingerprint="fixture",
            base_revision=0, candidate_base={"Awareness": 80}, candidate_band={"mental": 90},
            metadata={"week": 24 if kind == "OFFSEASON" else week, "application_week": week})
        report["identity"]["event_id"] = event["event_id"]
        cur.execute("UPDATE progression_events SET status=%s, metadata=metadata || %s::jsonb WHERE event_id=%s",
                    (status, json.dumps({"report_public": report}), event["event_id"]))
    class Borrowed:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def cursor(self):
            return conn.cursor()
    monkeypatch.setattr(dt.psycopg, "connect", lambda *args: Borrowed())
    monkeypatch.setattr(dt, "resolve_season", lambda season: season or 2037)
    monkeypatch.setattr(dt, "_resolve_player_id", lambda cursor, identifier: 1)
    with article_run_context(2037, 1):
        result = json.loads(invoke(2040))
    assert [report["identity"]["kind"] for report in result["events"]] == ["OFFSEASON"]
    assert [row["season"] for row in result["changes"]] == [2035]
    # Explicit previous-season lookup exercises typed SQL NULL cutoff parameters.
    with article_run_context(2038, 1):
        historical = json.loads(invoke(2037))
    assert len(historical["events"]) == 2


def test_private_markers_and_invalid_numbers_in_legacy_rows_never_reach_writer(monkeypatch):
    setup_tool(monkeypatch, has_events=False, history=[
        (2037, float("nan"), 100, float("inf"), "ceiling=95", "PRIVATE_95", "SECRET_BAND"),
        (2036, 78, 80, 2, "CEILING", "Normal", "Star"),
        (2035, 80, 81, 1, "DEV", "Star", "Superstar")])
    with article_run_context(2037, 5):
        text = invoke()
    rows = json.loads(text)["changes"]
    assert rows[0] == dict(season=2037, old_ovr=None, new_ovr=None, delta_ovr=None,
                           cause=None, dev_before=None, dev_after=None)
    assert rows[1]["cause"] == "POTENTIAL_HEADROOM"
    assert rows[2]["cause"] == "DEVELOPED"
    for marker in ("ceiling=95", "PRIVATE_95", "SECRET_BAND", "NaN", "Infinity"):
        assert marker not in text
