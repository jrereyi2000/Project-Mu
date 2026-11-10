"""pipeline/league_office.py: the coaching staff write (COACH_SAVE_WRITES_PLAN §2.6/§2.8, U6).

Two layers: plan_stage wiring with the league office's fake deps (the staff block reaches the one writer document at
OFFSEASON_START and the later offseason stops, a dry run writes nothing, the writer's PresentationIds are recorded
back), and the real engines.coach_save_sync planner against an in-memory SQLite league (a later stop writes only the
seat that still differs, and the portrait pack's queued Portrait fields ride an assign). No PostgreSQL connection.
"""
import copy
import json

import pytest

from tests.test_coach_save_sync import (_contract, _coord, _db, FINISHED, _hc, _profile, _save_coach, _seat,
                                        _snapshot)
from tests.test_inbox_store import FakeCursor
from tests.test_league_office import (FakeCursor as LeagueFakeCursor, FakeDeps, SEASON as LO_SEASON, snapshot,
                                      plan as lo_plan)


# --------------------------------------------------------------------------------------------- plan_stage wiring ---
def staff_snapshot(**kw):
    """The league office snapshot with the reader's staff seats and coaches (reader_version 8)."""
    snap = snapshot("OffSeason", "OffSeason", 0, 1, **kw)
    snap["meta"]["reader_version"] = 8
    snap["staff"] = [{"team_index": 0, "hc": 100, "oc": 101, "dc": 102},
                     {"team_index": 1, "hc": 200, "oc": None, "dc": None}]
    snap["coaches"] = [
        {"pgid": 100, "first_name": "Dave", "last_name": "Canales", "team_index": 0, "contract_status": "Signed",
         "position": "HeadCoach", "portrait": 365},
        {"pgid": 101, "first_name": "Brad", "last_name": "Idzik", "team_index": 0, "contract_status": "Signed",
         "position": "OffensiveCoordinator", "portrait": 366},
        {"pgid": 102, "first_name": "Ejiro", "last_name": "Evero", "team_index": 0, "contract_status": "Signed",
         "position": "DefensiveCoordinator", "portrait": 367},
        {"pgid": 200, "first_name": "Raheem", "last_name": "Morris", "team_index": 1, "contract_status": "Signed",
         "position": "HeadCoach", "portrait": 368},
    ]
    return snap


ASSIGN = {"action": "assign", "pgid": 100, "team_index": 0, "position": "HeadCoach",
          "contract": {"ContractLength": 3, "ContractSalary": 300, "ContractYearRemaining": 2}}
CREATE = {"action": "create", "key": "coord-311", "team_index": 1, "position": "OffensiveCoordinator",
          "profile": {"first_name": "A", "last_name": "B", "position": "OffensiveCoordinator"}, "visuals_template": 749}


def fake_planner(monkeypatch, result):
    """Monkeypatch engines.coach_save_sync.plan_staff_edits and record the target seasons it was asked for."""
    from engines import coach_save_sync
    calls = []

    def plan_staff_edits(cur, snapshot, target_season):
        calls.append(target_season)
        return copy.deepcopy(result)

    monkeypatch.setattr(coach_save_sync, "plan_staff_edits", plan_staff_edits)
    return calls


def test_offseason_start_includes_the_staff_block(monkeypatch):
    snap = staff_snapshot()
    calls = fake_planner(monkeypatch, {"staff": [ASSIGN, CREATE], "skipped": [], "report": []})
    p = lo_plan("OFFSEASON_START", snap, FakeDeps(snap))
    assert p["edits"]["staff"] == [ASSIGN, CREATE]
    assert calls == [LO_SEASON + 1], "the coming league year, not the season just played"
    assert p["summary"]["staff"]["planned"] == 2
    assert p["summary"]["staff"]["assigned"] == 1 and p["summary"]["staff"]["created"] == 1


def test_a_later_offseason_stop_still_plans_what_is_missing(monkeypatch):
    snap = staff_snapshot()
    calls = fake_planner(monkeypatch, {"staff": [CREATE], "skipped": [], "report": []})
    for stage in ("RESIGN", "FREE_AGENCY", "COMBINE", "PRO_DAY", "DRAFT", "UDFA"):
        p = lo_plan(stage, snap, FakeDeps(snap))
        assert p["edits"].get("staff") == [CREATE], stage
    assert calls == [LO_SEASON + 1] * 6
    # In season (and at the preseason stops, which are the new league year) the staff block is not planned.
    for stage in ("REGULAR_WEEK", "PLAYOFF_WEEK", "CUTDOWN", "PRESEASON"):
        p = lo_plan(stage, snap, FakeDeps(snap))
        assert "staff" not in (p["edits"] or {}), stage
    assert len(calls) == 6


def test_a_stage_where_the_save_matches_mu_has_no_staff_block(monkeypatch):
    snap = staff_snapshot()
    fake_planner(monkeypatch, {"staff": [], "skipped": [], "report": []})
    p = lo_plan("RESIGN", snap, FakeDeps(snap))
    assert "staff" not in p["edits"] and "staff" not in p["summary"]


def test_skipped_seats_are_reported_as_warnings(monkeypatch):
    snap = staff_snapshot()
    fake_planner(monkeypatch, {"staff": [], "skipped": [
        {"team": "CHI", "roles": ["OC", "DC"], "reason": "hiring cycle not finished"},
        {"team": "ARI", "role": "OC", "person": "A. B", "reason": "no coach_madden_profiles row"}],
        "report": []})
    p = lo_plan("OFFSEASON_START", snap, FakeDeps(snap))
    assert p["summary"]["staff"]["skipped"] == 2
    assert p["summary"]["staff"]["skipped_detail"][0]["team"] == "CHI"
    joined = " ".join(p["warnings"])
    assert "staff write skipped CHI OC/DC: hiring cycle not finished" in joined
    assert "staff write skipped ARI OC A. B: no coach_madden_profiles row" in joined


def test_a_dry_run_plans_and_prints_the_staff_block_and_writes_nothing(tmp_path, monkeypatch):
    save = tmp_path / "CAREER-TEST"
    save.write_bytes(b"ORIGINAL SAVE")
    snap = staff_snapshot()
    fake_planner(monkeypatch, {"staff": [ASSIGN, CREATE], "skipped": [], "report": []})
    deps = FakeDeps(snap)
    out = []
    lo_run_with(deps, snap, str(save), stage="OFFSEASON_START", dry_run=True, out=out.append)
    assert save.read_bytes() == b"ORIGINAL SAVE"
    assert not any(c[0] == "write" for c in deps.calls)
    printed = json.loads(out[-1])
    assert printed["edits"]["staff"] == [ASSIGN, CREATE]
    assert printed["summary"]["staff"]["planned"] == 2


def test_written_presentation_ids_are_recorded_back(tmp_path, monkeypatch):
    save = tmp_path / "CAREER-TEST"
    save.write_bytes(b"ORIGINAL SAVE")
    snap = staff_snapshot()
    fake_planner(monkeypatch, {"staff": [ASSIGN, CREATE], "skipped": [], "report": []})
    deps = FakeDeps(snap)
    cur = ProfileCursor()
    text = ('staff: 0 released, 1 assigned, 1 created {"released": 0, "retired": 0, "assigned": 1, '
            '"created": 1, "presentation_ids": [{"key": "coord-311", "row": 12, "presentation_id": 752}]}')
    lo_run_with(deps, snap, str(save), cur=cur, stage="OFFSEASON_START", write_text=text)
    updates = [args for sql, args in cur.sql if sql.startswith("UPDATE coach_madden_profiles")]
    assert updates == [(752, "COORD", 311)], "the new PresentationId and written_at are stored on the profile"
    assert "written_at = now()" in next(s for s, _ in cur.sql if s.startswith("UPDATE coach_madden_profiles"))


def test_a_write_with_no_staff_report_records_nothing(tmp_path, monkeypatch):
    save = tmp_path / "CAREER-TEST"
    save.write_bytes(b"ORIGINAL SAVE")
    snap = staff_snapshot()
    fake_planner(monkeypatch, {"staff": [ASSIGN], "skipped": [], "report": []})
    deps = FakeDeps(snap)
    cur = ProfileCursor()
    lo_run_with(deps, snap, str(save), cur=cur, stage="OFFSEASON_START")
    assert not any(s.startswith("UPDATE coach_madden_profiles") for s, _ in cur.sql)


def lo_run_with(deps, snap, save, cur=None, stage="OFFSEASON_START", dry_run=False, out=None, write_text=None,
                **kw):
    """league_office.run with the fake deps; `write_text` replaces the writer's stderr text (the staff report)."""
    cur = cur or ProfileCursor()
    deps.snap, deps.cur = snap, cur
    if out is None:
        out = []
    out_fn = out.append if isinstance(out, list) else out
    if write_text is not None:
        original = deps.write_save

        def write_save(save_path, edits_path, out_path):
            ok, text = original(save_path, edits_path, out_path)
            return ok, f"{text}\n{write_text}"

        deps.write_save = write_save
    return lo_run(deps, snap, save, stage, cur=cur, dry_run=dry_run, out=out_fn, **kw)


def lo_run(deps, snap, save, stage, cur, **kw):
    from pipeline import league_office as lo
    return lo.run(cur, snap, save, stage, deps=deps, **kw)


class ProfileCursor(LeagueFakeCursor):
    """The league office fake cursor plus coach_madden_profiles: to_regclass and the record-back UPDATE are answered."""

    def execute(self, sql, args=None):
        norm = " ".join(sql.split())
        if norm.startswith("SELECT to_regclass('coach_madden_profiles')"):
            self.sql.append((norm, args))
            self._ret = (True,)
            return
        if norm.startswith("UPDATE coach_madden_profiles SET presentation_id"):
            self.sql.append((norm, args))
            self._ret = None
            return
        return super().execute(sql, args)


# --------------------------------------------------------------------------- the real planner (SQLite league) ---
def _league():
    teams = [(1, "CAR", 0)]
    conn = _db(teams)
    snap = _snapshot(teams)
    snap["season"] = {"calendar_year": FINISHED}
    snap["coaches"] = []
    return conn, snap


def _staff_plan(conn, snap, stage="RESIGN", season=FINISHED, week=25):
    from pipeline import league_office as lo
    plan = lo.Plan(snap, stage, season, week, None, "seed", {0: 1}, {}, None)
    lo.step_staff(plan, FakeCursor(conn), snap)
    doc, summary = plan.finalize()
    return plan, doc, summary


def test_the_real_planner_writes_only_the_seat_that_still_differs():
    conn, snap = _league()
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 302, "New Coordinator", 1, "DC", FINISHED - 1)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 302, None, "DefensiveCoordinator")     # the new DC has never been written
    _contract(conn, 1, "HC", 41, 3_000_000, 3, 2)
    _contract(conn, 1, "OC", 300, 1_000_000, 3, 2)
    _contract(conn, 1, "DC", 302, 900_000, 3, 3)
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 100, "Dave", "Canales", 0, contract=(3, 300, 2))
    _save_coach(snap, 101, "Brad", "Idzik", 0, contract=(3, 100, 2))
    _save_coach(snap, 102, "Old", "Coord", 0)

    plan, doc, summary = _staff_plan(conn, snap)
    assert [a["action"] for a in doc["staff"]] == ["release", "create"]
    assert doc["staff"][0] == {"action": "release", "pgid": 102}
    create = doc["staff"][1]
    assert create["key"] == "coord-302" and create["position"] == "DefensiveCoordinator"
    assert create["contract"] == {"ContractLength": 3, "ContractSalary": 90, "ContractYearsRemaining": 3}
    assert summary["staff"] == {"planned": 2, "released": 1, "assigned": 0, "created": 1, "portraits": 0,
                                "skipped": 0, "skipped_detail": []}


def test_an_unfinished_hiring_cycle_is_skipped_and_warned_not_written():
    conn, snap = _league()
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)          # no OC or DC tenure at all: the cycle is unfinished
    _profile(conn, "COACH", 41, 100)
    _seat(snap, 0, "hc", 100)
    _save_coach(snap, 100, "Dave", "Canales", 0)

    plan, doc, summary = _staff_plan(conn, snap)
    assert "staff" not in doc
    assert summary["staff"]["skipped"] == 1
    assert "hiring cycle not finished" in " ".join(plan.warnings)


def test_the_portrait_packs_queued_fields_ride_an_assign():
    conn, snap = _league()
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _contract(conn, 1, "HC", 41, 3_000_000, 3, 2)
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 100, "Dave", "Canales", 0, contract=(3, 300, 2))
    _save_coach(snap, 101, "Brad", "Idzik", 0, contract=(3, 100, 2))
    _save_coach(snap, 102, "Ejiro", "Evero", 0)
    for coach in snap["coaches"]:
        coach["portrait"] = 7999                            # a created coach's placeholder until the pack is in
    # INSTALLED, not PACKED: the texture is not in the game until JR installs the pack, so only an installed
    # portrait reaches the save (audit B10; `_portrait_updates` selects `portrait_status = 'INSTALLED'`).
    conn.execute("""UPDATE coach_madden_profiles SET portrait_id = 5324, portrait_status = 'INSTALLED'
                     WHERE person_kind = 'COACH' AND person_id = 41""")

    # The seats all match, so only the portrait push is planned: an assign with the set and the held seat/contract.
    plan, doc, summary = _staff_plan(conn, snap)
    assert len(doc["staff"]) == 1
    action = doc["staff"][0]
    assert action["action"] == "assign" and action["pgid"] == 100 and action["team_index"] == 0
    assert action["position"] == "HeadCoach"
    assert action["set"] == {"Portrait": 5324, "Portrait_Force_Silhouette": False}
    assert summary["staff"]["portraits"] == 1
    assert plan.staff_info["report"][-1]["action"] == "assign"

    # Once the save record carries the portrait, nothing is left to write.
    snap["coaches"][0]["portrait"] = 5324
    plan2, doc2, _ = _staff_plan(conn, snap)
    assert "staff" not in doc2


def test_the_writer_staff_report_is_parsed_from_the_node_output():
    from pipeline import league_office as lo
    text = ('some line\nstaff: 1 released, 0 assigned, 1 created {"released": 1, "assigned": 0, "created": 1, '
            '"presentation_ids": [{"key": "coach-311", "row": 4, "presentation_id": 752}]}\n')
    report = lo.writer_staff_report(text)
    assert report["presentation_ids"] == [{"key": "coach-311", "row": 4, "presentation_id": 752}]
    assert lo.writer_staff_report("players: 2 fields") is None
