"""The NFL weekly mirror's league-office wiring (NFL_WEEKLY_MIRROR_PLAN §2.9, U6).

Two layers, both offline: `pipeline/league_office_mirror.py`'s adapter with every collaborator injected (no network,
no database), and `PRIORITY_MIRROR` / `step_nfl_mirror` / the plan_stage wiring against the league office's fake
deps. A `FakeCursor` only records SQL, so `record_written` is exercised without a PostgreSQL connection.
"""
import json
import os
import shutil
import types

import pytest

from engines.nfl_mirror import contracts as mirror_contracts
from engines.nfl_mirror import diff as mirror_diff
from engines.nfl_mirror import injuries as mirror_injuries
from engines.nfl_mirror import real_state
from engines.nfl_mirror import store as mirror_store
from pipeline import league_office as lo
from pipeline import league_office_mirror as lom
from tests.test_league_office import (AZ, CHI, SEASON, FakeCursor, FakeDeps, contract, pid, player,
                                      plan as lo_plan, snapshot)

# Spike A's Injuries / Practice Injuries field, the S18 settings shape (both switches share one record row).
INJURIES_FIELD = {
    "table": "LeagueSetting", "tableId": 4290, "row": 0,
    "fields": {"Injuries": {"field": "Field_100", "off": 0, "on": 1},
               "Practice Injuries": {"field": "Field_110", "off": 0, "on": 1}},
}
ON_EDIT = {"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_100": 1, "Field_110": 1}}
OFF_EDIT = {"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_100": 0, "Field_110": 0}}

CREATE_GSIS, RELEASE_GSIS, TRADE_GSIS, IR_GSIS, ACTIVATE_GSIS = (
    "00-9001", "00-9002", "00-9003", "00-9004", "00-9005")


# ---------------------------------------------------------------------------------------------- helpers ----
def make_mirror(mode=lom.CATCH_UP, **kw):
    """A MirrorWeek with this week's real week empty; tests pass only the parts they exercise."""
    args = dict(season=SEASON, week=3, real_week=3, stage="REGULAR_WEEK", mode=mode,
                real=types.SimpleNamespace(season=2026, week=3, depth={}))
    args.update(kw)
    return lom.MirrorWeek(**args)


class MirrorDeps(FakeDeps):
    """The league office fakes with one mirrored week injected: no fetch, no store, no database."""

    def __init__(self, snap=None, mirror=None, restore=None, charts=None, **kw):
        super().__init__(snap, **kw)
        self.mirror = mirror
        self.restore = restore
        self.charts = charts or {}
        self.mirror_calls, self.restore_calls, self.chart_calls, self.bound = [], [], [], []

    def mirror_week(self, cur, snapshot, season, stage, week):
        self.mirror_calls.append((season, stage, week))
        return self.mirror

    def mirror_restore(self, cur, season, stage, week):
        self.restore_calls.append((season, stage, week))
        return self.restore

    def chart_override(self, team_index):
        self.chart_calls.append(team_index)
        return self.charts.get(team_index)

    def bind_mirror(self, mirror, plan):
        self.bound.append((mirror, plan))

    def engine_trades(self, cur, season):
        self.calls.append(("engine_trades", season))
        return self.engine_props


class AdapterDeps(MirrorDeps):
    """MirrorDeps whose chart override comes from a real MirrorAdapter bound to this run's plan (S20)."""

    def __init__(self, snap=None, adapter=None, **kw):
        super().__init__(snap, **kw)
        self.adapter = adapter or lom.MirrorAdapter()

    def chart_override(self, team_index):
        self.chart_calls.append(team_index)
        return self.adapter.chart_override(team_index)

    def bind_mirror(self, mirror, plan):
        self.bound.append((mirror, plan))
        self.adapter.bind(mirror, plan)


class RecordingPlan(lo.Plan):
    """A Plan that logs the order step_nfl_mirror applies its parts in."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.log = []

    def set_fields(self, pgid, fields, tag=None):
        self.log.append(("fields", pgid))
        return super().set_fields(pgid, fields, tag=tag)

    def sign_player(self, *a, **kw):
        self.log.append(("sign", a[0]))
        return super().sign_player(*a, **kw)

    def release_player(self, *a, **kw):
        self.log.append(("release", a[0]))
        return super().release_player(*a, **kw)

    def trade_player(self, *a, **kw):
        self.log.append(("trade", a[0]))
        return super().trade_player(*a, **kw)

    def add_records(self, records):
        self.log.append(("records", len(records or [])))
        return super().add_records(records)


def new_plan(snap=None, cls=lo.Plan):
    snap = snap or snapshot()
    return cls(snap, "REGULAR_WEEK", SEASON, 3, None, "seed", {0: CHI, 1: AZ},
               {pid(p["pgid"]): p["pgid"] for p in snap["players"]})


def create_entry(pgid_=5, team=0):
    return {"gsis_id": CREATE_GSIS, "real": types.SimpleNamespace(name="Nate New"),
            "edit": {"pgid": pgid_, "set": {"FirstName": "Nate", "LastName": "New"}},
            "player_id": pid(pgid_), "team_index": team, "list": "Roster", "contract": contract(),
            "position": "WR", "source": "TEMPLATE", "donor_pgid": pgid_}


def injured_snapshot():
    snap = snapshot()
    for p in snap["players"]:
        if p["pgid"] in (2, 3):
            p["injury"] = {"status": "Injured", "type": "Invalid_", "min_weeks": 0, "max_weeks": 0,
                           "injured_reserve": False}
    return snap


def player_sets(p):
    return {e["pgid"]: e["set"] for e in p["edits"].get("players") or []}


# ------------------------------------------------------------------------------ step_nfl_mirror (§2.9) ----
def test_mirrored_week_plans_the_2_9_steps_in_order_with_the_mirror_priority_and_tags():
    mirror = make_mirror(
        creates=[create_entry()],
        ops=[{"kind": "release", "gsis_id": RELEASE_GSIS, "pgid": 1, "why": "cut by the real team"},
             {"kind": "trade", "gsis_id": TRADE_GSIS, "pgid": 3, "to_index": 0, "why": "traded"},
             {"kind": "ir", "gsis_id": IR_GSIS, "pgid": 2},
             {"kind": "activate", "gsis_id": ACTIVATE_GSIS, "pgid": 6}],
        injuries=[{"kind": "HEAL", "gsis_id": TRADE_GSIS, "pgid": 3, "fields": dict(mirror_injuries.HEAL_FIELDS),
                   "weeks": 0, "why": "healthy on the real roster"}],
        ps_signs=[{"pgid": 4, "team_index": 0, "why": "real practice squad has a WR"}],
        setting={"state": "OFF", "edit": dict(OFF_EDIT)},
        review=[{"gsis_id": TRADE_GSIS, "reason": "same name"}],
        counts={"create": 1}, report="1 created")
    p = new_plan(cls=RecordingPlan)

    lo.step_nfl_mirror(p, mirror)

    # creates -> roster ops (in the diff's order) -> injury/IR fields -> practice squad -> the switches record
    # (F2: the signing is claimed before the donor's fields are edited)
    assert p.log == [("sign", 5), ("fields", 5), ("release", 1), ("trade", 3), ("fields", 2), ("fields", 6),
                     ("fields", 3), ("sign", 4), ("records", 1)]
    # every move claims at the mirror priority (the engine-trade level) with `mirror:<kind>:<gsis_id>`
    assert p.claimed[1] == ("release", 0, lo.PRIORITY_MIRROR)
    assert p.claimed[3] == ("trade", 0, lo.PRIORITY_MIRROR)
    assert p.claimed[5] == ("sign", 0, lo.PRIORITY_MIRROR)
    assert p.claimed[4] == ("sign", 0, lo.PRIORITY_MIRROR)
    assert [s["pgid"] for s in p.signs] == [5, 4]
    assert [s["priority"] for s in p.signs] == [lo.PRIORITY_MIRROR, lo.PRIORITY_MIRROR]
    assert [s["tag"] for s in p.signs] == [f"mirror:create:{CREATE_GSIS}", "mirror:ps:4"]
    for tag in (f"mirror:release:{RELEASE_GSIS}", f"mirror:trade:{TRADE_GSIS}", f"mirror:ir:{IR_GSIS}",
                f"mirror:activate:{ACTIVATE_GSIS}"):
        assert p.results[tag][0] is True, tag
    assert p.trades == [(1, 0, 3)] and p.release == [1]
    assert p.edits[2]["set"] == {"IsInjuredReserve": True}
    assert p.edits[6]["set"] == {"IsInjuredReserve": False}
    assert p.edits[3]["set"] == mirror_injuries.HEAL_FIELDS
    assert p.records == [OFF_EDIT]
    assert p.db["mirror_setting"] == {"state": "OFF", "season": SEASON, "week": 3}
    assert p.counts["mirror_moves"] == 3

    # one transactions row per applied SIGN / RELEASE / TRADE, with the real gsis id and Madden team ids
    txns = p.db["mirror"]["transactions"]
    assert [t["kind"] for t in txns] == ["SIGN", "RELEASE", "TRADE"]
    assert (txns[0]["gsis_id"], txns[0]["to_team_id"], txns[0]["player_name"]) == (CREATE_GSIS, CHI, "Nate New")
    assert (txns[1]["gsis_id"], txns[1]["from_team_id"], txns[1]["player_id"]) == (RELEASE_GSIS, CHI, pid(1))
    assert (txns[2]["from_team_id"], txns[2]["to_team_id"]) == (AZ, CHI)

    doc, _ = p.finalize()
    # signing tags are noted when finalize resolves the queued signings against the contract rows
    assert p.results[f"mirror:create:{CREATE_GSIS}"][0] is True
    assert p.results["mirror:ps:4"][0] is True
    assert doc["release"] == [1]
    assert [s["pgid"] for s in doc["sign"]] == [5, 4]
    assert doc["trade"] == [{"from_team_index": 1, "to_team_index": 0, "pgids": [3], "picks": []}]
    assert OFF_EDIT in doc["records"]
    assert {e["pgid"] for e in doc["players"]} == {2, 3, 5, 6}


def test_plan_stage_uses_the_mirror_week_instead_of_engine_trades():
    snap = snapshot()
    mirror = make_mirror(ops=[{"kind": "trade", "gsis_id": TRADE_GSIS, "pgid": 3, "to_index": 0,
                               "why": "traded"}],
                         review=[{"gsis_id": TRADE_GSIS, "reason": "same name"}],
                         counts={"trade": 1}, report="1 traded")
    deps = MirrorDeps(snap, mirror=mirror)
    deps.engine_props = [{"id": 41, "status": "APPROVED", "flags": [], "refusals": 0,
                          "assets": [{"type": "PLAYER", "player_id": pid(1), "from": CHI, "to": AZ}]}]

    p = lo_plan("REGULAR_WEEK", snap=snap, deps=deps)

    # league_week: the snapshot's week 3 is played, so this write plans week 4
    assert deps.mirror_calls == [(SEASON, "REGULAR_WEEK", 4)]
    assert ("engine_trades", SEASON) not in deps.calls             # the mirror replaced the step
    assert p["db"]["engine_trades"] == []
    assert {(x["from_team_index"], x["to_team_index"], tuple(x["pgids"]))
            for x in p["edits"]["trade"]} == {(1, 0, (3,))}
    assert deps.bound and deps.bound[0][0] is mirror
    assert p["summary"]["mirror"]["mode"] == lom.CATCH_UP
    assert p["summary"]["mirror"]["review"] == [{"gsis_id": TRADE_GSIS, "reason": "same name"}]
    assert [t["kind"] for t in p["db"]["mirror"]["transactions"]] == ["TRADE"]
    json.dumps(p["db"]["mirror"])                                  # JSON-safe: plan_record / --recover


def test_a_week_without_a_mirror_plans_exactly_as_before():
    plain = lo_plan("REGULAR_WEEK", snap=snapshot(), deps=FakeDeps())
    deps = MirrorDeps(snapshot(), mirror=None)
    typed = lo_plan("REGULAR_WEEK", snap=snapshot(), deps=deps)

    assert deps.mirror_calls == [(SEASON, "REGULAR_WEEK", 4)]      # asked, answered None: not a mirrored week
    assert typed["edits"] == plain["edits"]
    assert "mirror" not in typed["summary"] and "mirror_setting" not in typed["db"]
    assert sorted(typed["summary"]) == sorted(plain["summary"])


def test_a_week_without_a_mirror_restores_the_injury_switches_the_mirror_left_off():
    deps = MirrorDeps(mirror=None, restore={"state": "ON", "edit": dict(ON_EDIT)})
    p = lo_plan("REGULAR_WEEK", deps=deps)

    assert deps.restore_calls == [(SEASON, "REGULAR_WEEK", 4)]
    assert ON_EDIT in p["edits"]["records"]
    assert p["db"]["mirror_setting"] == {"state": "ON", "season": SEASON, "week": 4}


def test_a_catch_up_week_skips_step_injuries_and_the_waivers():
    mirror = make_mirror(injuries=[{"kind": "INJURY", "gsis_id": IR_GSIS, "pgid": 2,
                                    "fields": {"InjuryStatus": "Injured"}, "weeks": 2, "why": "real IR"}],
                         injury_players={2})
    deps = MirrorDeps(injured_snapshot(), mirror=mirror)
    deps.released = [5]
    plain_deps = MirrorDeps(injured_snapshot(), mirror=None)
    plain_deps.released = [5]

    plain = lo_plan("REGULAR_WEEK", snap=injured_snapshot(), deps=plain_deps)
    p = lo_plan("REGULAR_WEEK", snap=injured_snapshot(), deps=deps)

    assert ("waivers", [pid(5)]) in plain_deps.calls
    assert not any(c[0] == "waivers" for c in deps.calls)          # never in a mirrored week
    assert "MinInjuryDuration" in player_sets(plain)[2]            # Madden's own clock, without the mirror
    sets = player_sets(p)
    assert sets[2] == {"InjuryStatus": "Injured"}                  # the mirror's fields, not a second duration
    assert 3 not in sets                                           # the whole step is skipped in a catch-up week


def test_a_live_week_spares_the_mirrors_players_and_turns_both_switches_on():
    mirror = make_mirror(mode=lom.LIVE, setting={"state": "ON", "edit": dict(ON_EDIT)}, injury_players={2},
                         injuries=[{"kind": "INJURY", "gsis_id": IR_GSIS, "pgid": 2,
                                    "fields": {"InjuryStatus": "Injured"}, "weeks": 4, "why": "IR placement"}])
    deps = MirrorDeps(injured_snapshot(), mirror=mirror)

    p = lo_plan("REGULAR_WEEK", snap=injured_snapshot(), deps=deps)

    sets = player_sets(p)
    assert sets[2] == {"InjuryStatus": "Injured"}                  # D12: the mirror's player is left alone
    assert "MinInjuryDuration" in sets[3]                          # Madden's own injury clock still runs
    assert ON_EDIT in p["edits"]["records"]                        # D10: both switches back on
    assert p["db"]["mirror_setting"] == {"state": "ON", "season": SEASON, "week": 3}

    cur = FakeCursor()
    lo.record_written(cur, p, 77)
    states = [a for s, a in cur.sql if s.startswith("UPDATE nfl_mirror_settings SET injuries_state")]
    assert states == [("ON", 1)]                                   # recorded once, for this write

    # the first live week writes the record once: once ON, `_setting` has nothing left to write
    assert lom._setting({"injuries_state": "OFF", "madden_injuries_field": INJURIES_FIELD},
                        lom.LIVE) == {"state": "ON", "edit": ON_EDIT}
    assert lom._setting({"injuries_state": "ON", "madden_injuries_field": INJURIES_FIELD}, lom.LIVE) is None


def test_the_first_catch_up_week_turns_both_switches_off_once():
    settings = {"injuries_state": "ON", "madden_injuries_field": INJURIES_FIELD}
    assert lom._setting(settings, lom.CATCH_UP) == {"state": "OFF", "edit": OFF_EDIT}
    settings["injuries_state"] = "OFF"
    assert lom._setting(settings, lom.CATCH_UP) is None


def test_the_restore_turns_the_switches_on_only_while_the_mirror_is_off_and_they_are_off():

    class Store:
        def __init__(self):
            self.settings = {"injuries_state": "OFF", "madden_injuries_field": INJURIES_FIELD}
            self.active = False

        def read_settings(self, cur):
            return dict(self.settings)

        def mirror_active(self, cur, season, stage, week):
            return self.active

    store = Store()
    adapter = lom.MirrorAdapter(store=store)
    assert adapter.mirror_restore(FakeCursor(), SEASON, "REGULAR_WEEK", 3) == {"state": "ON", "edit": ON_EDIT}
    store.settings["injuries_state"] = "ON"
    assert adapter.mirror_restore(FakeCursor(), SEASON, "REGULAR_WEEK", 3) is None
    store.settings["injuries_state"] = "OFF"
    store.active = True                                            # a LIVE week restores through its own week
    assert adapter.mirror_restore(FakeCursor(), SEASON, "REGULAR_WEEK", 3) is None


# ------------------------------------------------------------------------------------ the declared switch ----
def test_a_release_then_a_sign_to_another_team_is_a_declared_switch():
    p = new_plan()
    assert p.release_player(1, lo.PRIORITY_MIRROR, "cut by the real team")
    assert p.sign_player(1, 1, "Roster", contract(), lo.PRIORITY_MIRROR, "claimed by the real ARI")

    assert p.switches == {1}
    assert p.signs[-1]["switch"] is True
    assert p.claimed[1] == ("sign", 1, lo.PRIORITY_MIRROR)
    p.set_fields(1, {"InjuryStatus": "Injured"})                    # a switch is not a departure: fields may ride
    doc, _ = p.finalize()
    assert doc["release"] == [1]
    assert doc["sign"] == [{"pgid": 1, "team_index": 1, "list": "Roster",
                            "contract": p.signs[0]["block"], "switch": True}]


def test_a_same_team_double_move_is_still_refused_and_never_a_switch():
    p = new_plan()
    assert p.release_player(2, lo.PRIORITY_STAGE, "cut")
    assert p.sign_player(2, 0, "Roster", contract(), lo.PRIORITY_MIRROR, "re-sign", tag="promotion") is False
    assert p.results["promotion"] == (False, "P L2 already has a release this write")
    assert p.switches == set() and p.signs == []


def test_step_nfl_mirror_emits_the_switch_only_for_the_pair():
    mirror = make_mirror(ops=[{"kind": "release", "gsis_id": RELEASE_GSIS, "pgid": 1,
                               "why": "off the real roster"},
                              {"kind": "sign", "gsis_id": RELEASE_GSIS, "pgid": 1, "to_index": 1,
                               "list": "Roster", "contract": contract(), "why": "claimed by the real ARI"}])
    p = new_plan()

    lo.step_nfl_mirror(p, mirror)

    doc, _ = p.finalize()
    assert doc["sign"][0]["switch"] is True
    assert [t["kind"] for t in p.db["mirror"]["transactions"]] == ["RELEASE", "SIGN"]
    assert p.results[f"mirror:sign:{RELEASE_GSIS}"][0] is True


# ---------------------------------------------------------------------------------------------- S20 / charts ----
def s20_snapshot():
    return snapshot(players=[player(2, 0, pos="WR", injury={"status": "Injured", "type": "Invalid_", "min_weeks": 3,
                                                             "max_weeks": 5, "injured_reserve": True})])


def s20_mirror():
    return make_mirror(mode=lom.LIVE, codes={0: "CHI"}, links={IR_GSIS: 2},
                       real=types.SimpleNamespace(season=2026, week=3, depth={"CHI": {"WR": [IR_GSIS]}}),
                       ops=[{"kind": "activate", "gsis_id": IR_GSIS, "pgid": 2}],
                       injuries=[{"kind": "HEAL", "gsis_id": IR_GSIS, "pgid": 2,
                                  "fields": dict(mirror_injuries.HEAL_FIELDS), "weeks": 0,
                                  "why": "healthy on the real roster"}],
                       injury_players={2})


def test_s20_heal_and_activation_ride_the_same_write_and_reach_the_chart():
    snap = s20_snapshot()
    mirror = s20_mirror()
    p = new_plan(snap)
    adapter = lom.MirrorAdapter()
    adapter.bind(mirror, p)

    assert adapter.chart_override(0) == {}                          # still hurt in the snapshot: dropped

    lo.step_nfl_mirror(p, mirror)

    assert p.edits[2]["set"] == dict(mirror_injuries.HEAL_FIELDS, IsInjuredReserve=False)
    assert adapter.chart_override(0) == {"WR": [2]}                 # the real chart sees the post-write roster


def test_s20_the_real_chart_goes_in_front_of_the_engines():
    snap = s20_snapshot()
    mirror = s20_mirror()
    deps = AdapterDeps(snap, mirror=mirror, adapter=lom.MirrorAdapter())

    p = lo_plan("REGULAR_WEEK", snap=snap, deps=deps)

    assert deps.chart_calls and deps.bound
    charts = {c["team_index"]: c["slots"] for c in p["edits"]["depthCharts"]}
    assert charts[0]["WR"][0] == 2                                  # the healed, activated real starter
    assert p["db"]["charts"][0] == {"team_index": 0, "team_id": CHI, "source": "MU_COACH"}


# ------------------------------------------------------------------------------------------ created players ----
class FakeMirrorStore:
    """The handful of store calls plan_week makes, recorded instead of run."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {"injuries_state": "ON", "madden_injuries_field": INJURIES_FIELD})
        self.active = True
        self.weeks, self.moves = [], []

    def read_settings(self, cur):
        return dict(self.settings)

    def links(self, cur):
        return {}

    def mirror_active(self, cur, season, stage, week):
        return self.active

    def record_week(self, cur, season, week, **fields):
        self.weeks.append((season, week, fields))

    def record_moves(self, cur, season, week, moves, replace=False):
        self.moves.append((season, week, list(moves), replace))


def creation_adapter(snap, real_card=None, later=None, roster_plan=None, match_links=None, contract_book=None):
    """A real MirrorAdapter with fetch/build/diff fakes: the whole plan_week path runs offline."""
    store = FakeMirrorStore()
    real_card = real_card or real_state.RealPlayer(
        gsis_id=CREATE_GSIS, name="Nate New", position="WR", team="CHI", status="ACTIVE", designation="",
        college="State U", birth_date="2000-04-02", height_in=72, weight_lbs=200, entry_year=2022, jersey=12)
    real = real_state.RealWeek(season=2026, week=3, mode=real_state.CATCH_UP, rosters={"CHI": [real_card]},
                               depth={"CHI": {"WR": [real_card.gsis_id]}})
    roster_plan = roster_plan or types.SimpleNamespace(ops=[], creates=[real_card], ps_fill=[],
                                                       review=[{"gsis_id": real_card.gsis_id, "reason": "reviewed"}],
                                                       unlinked_new=[])
    files = {name: types.SimpleNamespace(path=f"/fake/{name}.csv")
             for name in ("rosters", "injuries", "depth", "games", "contracts")}
    adapter = lom.MirrorAdapter(
        store=store, fetch=lambda season, refresh=True: dict(files), read_rows=lambda f: [],
        sources=lambda f: {"rosters": "sha256"}, build=lambda *a, **k: real,
        later=lambda *a, **k: later or {},
        head_coaches=lambda rows, season, week: {}, plan_rosters=lambda *a: roster_plan,
        read_trades=lambda: [],
        contract_book=contract_book or (lambda path: types.SimpleNamespace(
            contract_for=lambda *a, **k: contract())),
        read_staff=lambda: {}, real_staff=lambda *a, **k: [], apply_staff=lambda *a, **k: [],
        ea_iteration=None, ea_items=None, ea_index=None, ea_find=None,
        match_links=match_links,
        donor_links=lambda cur, links, real_gsis: set(),
        team_codes=lambda snapshot, season=None: {"CHI": 0, "ARI": 1},
        team_ids=lambda cur, snapshot: {0: CHI, 1: AZ},
        user_team_ids=lambda cur: set(),
        pid_by_pgid=lambda cur: {p["pgid"]: pid(p["pgid"]) for p in snap["players"]},
        free_pool=lambda cur, season, target: [{"id": pid(4), "pos": "WR", "ovr": 80}],
        cap=lambda snapshot: 255_400_000)
    return adapter, store


def test_a_created_player_gets_a_template_edit_and_a_created_link_with_his_evidence():
    snap = snapshot()
    adapter, store = creation_adapter(snap)

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    assert len(mirror.creates) == 1
    c = mirror.creates[0]
    assert (c["gsis_id"], c["donor_pgid"], c["source"], c["player_id"]) == (CREATE_GSIS, 5, "TEMPLATE", pid(5))
    assert c["evidence"] == {"name": "Nate New", "position": "WR", "college": "State U",
                             "birth_date": "2000-04-02", "source": "TEMPLATE", "donor_pgid": 5,
                             "evidence": lom.CREATION_EVIDENCE}
    assert c["edit"]["set"]["FirstName"] == "Nate" and c["edit"]["set"]["LastName"] == "New"

    rec = mirror.record()
    # F2: a created link is not preloaded; `step_nfl_mirror` appends it only after the signing is accepted
    assert rec["links"] == []
    assert mirror.links[CREATE_GSIS] == 5
    assert rec["review"] == [{"gsis_id": CREATE_GSIS, "reason": "reviewed"}]      # U12 shape, surfaced
    json.dumps(rec)                                                               # plan_record / --recover safe

    season, week, fields = store.weeks[0]
    assert (season, week, fields["status"], fields["mode"]) == (SEASON, 3, "PLANNED", real_state.CATCH_UP)
    moves = store.moves[0]
    assert moves[3] is True                                                       # replace=True
    kinds = [m["kind"] for m in moves[2]]
    assert "CREATE" in kinds and "SETTING" in kinds and "CHART" in kinds


def test_record_written_stores_the_transactions_and_the_created_link(monkeypatch):
    snap = snapshot()
    adapter, _ = creation_adapter(snap)
    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)
    deps = AdapterDeps(snap, mirror=mirror, adapter=adapter)

    p = lo_plan("REGULAR_WEEK", snap=snap, deps=deps)

    assert p["db"]["mirror"]["links"][0]["evidence"]["name"] == "Nate New"
    cur = FakeCursor()
    lo.record_written(cur, p, 77)

    links = [a for s, a in cur.sql if s.startswith("INSERT INTO nfl_player_links")]
    assert links and links[0][0] == pid(5) and links[0][1] == CREATE_GSIS
    evidence = getattr(links[0][2], "obj", links[0][2])             # psycopg3 Jsonb wraps the evidence dict
    assert evidence["name"] == "Nate New"
    txns = [a for s, a in cur.sql if s.startswith("INSERT INTO transactions")]
    assert txns and txns[0][3] == "SIGNED" and txns[0][9] == f"mirror:{SEASON}:3:{CREATE_GSIS}:SIGN"
    # F3: the move rows take their own outcomes (`detail.tag`), not a blanket WRITTEN
    updates = [a for s, a in cur.sql if s.startswith("UPDATE nfl_mirror_moves SET status = %s")]
    assert updates and ("WRITTEN", None, SEASON, 3, f"mirror:create:{CREATE_GSIS}") in updates
    states = [a for s, a in cur.sql if s.startswith("UPDATE nfl_mirror_settings SET injuries_state")]
    assert states == [("OFF", 1)]                                                 # the catch-up turned them off


# ------------------------------------------------------------------- audit-4 U6 F1: creation state ----
def real_card(status="ACTIVE", gsis=CREATE_GSIS, team="CHI", position="WR"):
    return real_state.RealPlayer(gsis_id=gsis, name="Nate New", position=position, team=team, status=status,
                                 designation="", college="State U", birth_date="2000-04-02",
                                 height_in=72, weight_lbs=200, entry_year=2022, jersey=12)


@pytest.mark.parametrize("status", ["IR", "PUP", "NFI"])
def test_f1_a_created_reserve_player_is_reserve_and_injured_in_the_same_edit(status):
    snap = snapshot()
    adapter, _ = creation_adapter(
        snap, real_card=real_card(status=status),
        later={4: {CREATE_GSIS: {"status": "ACTIVE", "report_status": None}}})

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    c = mirror.creates[0]
    assert c["list"] == "Roster"
    assert c["edit"]["set"]["IsInjuredReserve"] is True
    assert c["edit"]["set"]["InjuryStatus"] == "Injured"
    assert c["edit"]["set"]["MinInjuryDuration"] == 1          # week 4 is his next healthy week
    assert mirror.links[CREATE_GSIS] == c["donor_pgid"]        # registered before chart/injury reconciliation
    assert mirror.injuries == []                               # F1: `_injuries` does not add a duplicate row
    assert c["donor_pgid"] in mirror.injury_players


def test_f1_a_created_active_starter_is_healthy_normalized_and_first_in_the_real_slot():
    snap = snapshot()
    for p in snap["players"]:                                  # an injured/IR spare record is fully normalized
        if p["pgid"] == 5:
            p["injury"] = {"status": "Injured", "type": "Invalid_", "min_weeks": 3, "max_weeks": 5,
                           "injured_reserve": True}
    adapter, _ = creation_adapter(snap, real_card=real_card(status="ACTIVE"))

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    c = mirror.creates[0]
    assert c["list"] == "Roster"
    assert c["edit"]["set"]["IsInjuredReserve"] is False
    assert c["edit"]["set"]["InjuryStatus"] == "Uninjured"
    assert c["edit"]["set"]["MinInjuryDuration"] == 0 and c["edit"]["set"]["MaxInjuryDuration"] == 0

    p = new_plan(snap)
    adapter.bind(mirror, p)
    assert adapter.chart_override(0) == {}                     # not signed yet: no real slot
    lo.step_nfl_mirror(p, mirror)
    assert adapter.chart_override(0) == {"WR": [c["donor_pgid"]]}


def test_f1_a_created_practice_squad_player_signs_to_the_practice_squad():
    snap = snapshot()
    adapter, _ = creation_adapter(snap, real_card=real_card(status="PS"))

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    c = mirror.creates[0]
    assert c["list"] == "PracticeSquad"
    p = new_plan(snap)
    lo.step_nfl_mirror(p, mirror)
    assert p.signs[0]["list"] == "PracticeSquad" and p.signs[0]["block"] is None


# ------------------------------------------------------------------- audit-4 U6 F2: refused creation ----
def test_f2_a_refused_creation_leaves_no_edit_link_or_transaction():
    snap = snapshot()
    mirror = make_mirror(creates=[create_entry()])
    p = new_plan(snap)
    assert p.sign_player(5, 1, "Roster", contract(), lo.PRIORITY_JR, "JR signs him first")
    p.db["mirror"] = mirror.record()

    lo.step_nfl_mirror(p, mirror)

    assert p.edits.get(5) is None                              # no FirstName/LastName edit on JR's player
    assert p.db["mirror"]["links"] == []
    assert p.db["mirror"]["transactions"] == []
    assert p.db["mirror"]["outcomes"][f"mirror:create:{CREATE_GSIS}"][0] == "REFUSED"


def test_f2_a_creation_without_a_contract_is_refused_before_the_edit():
    snap = snapshot()
    entry = create_entry()
    entry["contract"] = None
    mirror = make_mirror(creates=[entry])
    p = new_plan(snap)
    p.db["mirror"] = mirror.record()

    lo.step_nfl_mirror(p, mirror)

    assert p.edits.get(5) is None
    assert p.db["mirror"]["links"] == []
    outcome = p.db["mirror"]["outcomes"][f"mirror:create:{CREATE_GSIS}"]
    assert outcome[0] == "REFUSED" and "needs a contract" in outcome[1]


def test_f2_a_mirror_sign_cannot_ride_a_release_jr_already_planned():
    snap = snapshot()
    p = new_plan(snap)
    assert p.release_player(1, lo.PRIORITY_JR, "JR cuts him")
    mirror = make_mirror(ops=[
        {"kind": "release", "gsis_id": RELEASE_GSIS, "pgid": 1, "why": "off the real roster"},
        {"kind": "sign", "gsis_id": RELEASE_GSIS, "pgid": 1, "to_index": 1, "list": "Roster",
         "contract": contract(), "why": "claimed by the real ARI"}])
    p.db["mirror"] = mirror.record()

    lo.step_nfl_mirror(p, mirror)

    assert p.switches == set()                                 # JR's free-agent outcome stands
    assert p.roster_status(1) == ("FreeAgent", lo.FREE_AGENT_TEAM)
    assert p.db["mirror"]["transactions"] == []
    for kind in ("release", "sign"):
        assert p.db["mirror"]["outcomes"][f"mirror:{kind}:{RELEASE_GSIS}"][0] == "REFUSED"


def test_f2_a_same_owner_release_then_sign_is_still_a_declared_switch():
    snap = snapshot()
    p = new_plan(snap)
    mirror = make_mirror(ops=[
        {"kind": "release", "gsis_id": RELEASE_GSIS, "pgid": 1, "why": "off the real roster"},
        {"kind": "sign", "gsis_id": RELEASE_GSIS, "pgid": 1, "to_index": 1, "list": "Roster",
         "contract": contract(), "why": "claimed by the real ARI"}])

    lo.step_nfl_mirror(p, mirror)

    assert p.switches == {1}
    assert [t["kind"] for t in p.db["mirror"]["transactions"]] == ["RELEASE", "SIGN"]


# ------------------------------------------------------------------- audit-4 U6 F3: move outcomes ----
def test_f3_a_refused_move_is_recorded_refused_and_only_applied_moves_are_written():
    snap = snapshot()
    p = new_plan(snap)
    assert p.release_player(3, lo.PRIORITY_JR, "JR cuts him first")
    trade_tag = f"mirror:trade:{TRADE_GSIS}"
    release_tag = f"mirror:release:{RELEASE_GSIS}"
    mirror = make_mirror(
        ops=[{"kind": "trade", "gsis_id": TRADE_GSIS, "pgid": 3, "to_index": 0, "why": "traded"},
             {"kind": "release", "gsis_id": RELEASE_GSIS, "pgid": 1, "why": "cut by the real team"}],
        moves=[{"kind": "TRADE", "gsis_id": TRADE_GSIS, "pgid": 3, "detail": {"tag": trade_tag}},
               {"kind": "RELEASE", "gsis_id": RELEASE_GSIS, "pgid": 1, "detail": {"tag": release_tag}}])
    p.db["mirror"] = mirror.record()

    lo.step_nfl_mirror(p, mirror)

    assert p.db["mirror"]["outcomes"][trade_tag][0] == "REFUSED"
    assert p.db["mirror"]["outcomes"][release_tag][0] == "WRITTEN"
    assert [t["kind"] for t in p.db["mirror"]["transactions"]] == ["RELEASE"]

    rec = {"db": p.db, "season": p.season, "week": p.week, "calendar_year": p.calendar, "edits": {},
           "team_ids": p.teams, "pid_by_pgid": p.pid_by_pgid, "cap": p.cap,
           "trade_cap_db": p.trade_cap_db}
    cur = FakeCursor()
    lo.record_written(cur, rec, 77)
    updates = {args[4]: args[0] for s, args in cur.sql
               if s.startswith("UPDATE nfl_mirror_moves SET status = %s") and args and args[4]}
    assert updates == {trade_tag: "REFUSED", release_tag: "WRITTEN"}
    assert not any(s.startswith("UPDATE nfl_mirror_moves SET status = 'WRITTEN'") for s, _ in cur.sql)
    counts, report = lo._mirror_applied(p.db["mirror"], p.db["mirror"]["outcomes"])
    assert counts == {"release": 1} and "1 refused" in report


def test_f3_every_move_kind_counts_its_own_outcome():
    kinds = ["CREATE", "SIGN", "RELEASE", "TRADE", "IR", "HEAL", "PS", "CHART", "STAFF", "SETTING"]
    moves = [{"kind": kind, "detail": {"tag": f"mirror:test:{kind.lower()}"}} for kind in kinds]
    outcomes = {move["detail"]["tag"]: ["WRITTEN", None] for move in moves}
    for kind in ("TRADE", "CHART", "SETTING"):
        outcomes[f"mirror:test:{kind.lower()}"] = ["REFUSED", "refused for the test"]
    mirror = {"real_week": 3, "mode": real_state.CATCH_UP, "review": [], "moves": moves}

    counts, report = lo._mirror_applied(mirror, outcomes)

    assert counts == {kind.lower(): 1 for kind in kinds if kind not in ("TRADE", "CHART", "SETTING")}
    assert "3 refused" in report and "5 roster moves written" in report


# ------------------------------------------------------------------- audit-4 U6 F4: durable links ----
def test_f4_an_ordinary_new_link_is_recorded_with_the_linkers_method_and_evidence():
    snap = snapshot()
    gsis = "00-U13-MATCH"
    details = {gsis: {"player_id": pid(4), "gsis_id": gsis, "method": "OVERRIDE", "score": None,
                      "evidence": {"note": "JR accepted this pair"}}}
    roster_plan = types.SimpleNamespace(ops=[], creates=[], ps_fill=[], review=[], unlinked_new=[(gsis, 4)])
    adapter, _ = creation_adapter(snap, roster_plan=roster_plan, match_links=lambda *a: details)

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    assert mirror.record()["links"] == [{"player_id": pid(4), "gsis_id": gsis, "method": "OVERRIDE",
                                         "score": None, "evidence": {"note": "JR accepted this pair"}}]
    p = {"db": {"mirror": mirror.record()}, "team_ids": {0: CHI}, "pid_by_pgid": {4: pid(4)}}
    cur = FakeCursor()
    lo._record_mirror(cur, p, 77)
    inserts = [a for s, a in cur.sql if s.startswith("INSERT INTO nfl_player_links")]
    assert inserts and inserts[0][0] == pid(4) and inserts[0][1] == gsis and inserts[0][2] == "OVERRIDE"


def test_f4_every_linked_player_is_excluded_as_a_creation_donor():
    links = {"00-OLD": 5, "00-NEW": 4}
    assert lom._donor_links(FakeCursor(), links, {"00-NEW"}) == {5: "00-OLD", 4: "00-NEW"}


def test_f4_the_method_comes_from_the_same_linker_call_as_the_diffs_pair():
    wp = player(7, 0, pos="WR", first="Nate", last="New")
    wp.update({"college": "State U", "height_in": 72, "weight_lbs": 200, "years_pro": 3})
    snap = snapshot(players=[wp])
    card = real_card(status="ACTIVE", gsis="00-9099")
    real = real_state.RealWeek(season=2026, week=3, mode=real_state.CATCH_UP, rosters={"CHI": [card]})

    details = lom._match_links(snap, real, {}, {"CHI": 0})

    assert (details["00-9099"]["player_id"], details["00-9099"]["method"]) == (7, "EXACT")
    assert mirror_diff._new_links({7: wp}, {"00-9099": card}, {}, {"CHI": 0}, 2026) == [("00-9099", 7)]


# ------------------------------------------------------------------- audit-4 U6 F5: as-of boundary ----
@pytest.mark.parametrize("week,expected", [(1, "2026-09-15"), (3, "2026-09-29"), (19, "2027-01-19")])
def test_f5_the_creation_as_of_is_the_diffs_canonical_boundary(week, expected):
    real = types.SimpleNamespace(season=2026, week=week)
    assert lom._week_as_of(real) == mirror_diff._week_as_of(real) == expected


@pytest.mark.parametrize("deal_date,contract_type", [("2026-09-29", "STANDARD"),
                                                     ("2026-09-30", "MINIMUM")])
def test_f5_a_deal_on_the_boundary_is_used_and_the_following_day_is_not(deal_date, contract_type):
    deal = mirror_contracts.OtcDeal(gsis_id=CREATE_GSIS, team="CHI", year_signed=2026, signed=deal_date,
                                    years=3, apy=10.0, total=30.0, guaranteed=0.0, draft_year=2022, active=True)
    book = mirror_contracts.ContractBook({CREATE_GSIS: [deal]})
    snap = snapshot()
    adapter, _ = creation_adapter(snap, real_card=real_card(status="ACTIVE"),
                                  contract_book=lambda path: book)

    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)

    assert mirror.creates[0]["contract"]["contract_type"] == contract_type


# ----------------------------------------------------------- audit-4 U6: real-save acceptance ----
MUBASE = os.path.join(os.environ.get("MU_SAVES_DIR", "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves"),
                      "CAREER-MUBASE")


@pytest.fixture(scope="module")
def mubase_snapshot():
    if not shutil.which("node") or not os.path.exists(MUBASE):
        pytest.skip("node or the CAREER-MUBASE save is not available")
    return lo.Deps().read_snapshot(MUBASE)


def test_f1_a_created_real_reserve_player_is_reserve_and_injured_on_mubase(mubase_snapshot):
    snap = mubase_snapshot
    adapter, _ = creation_adapter(snap, real_card=real_card(status="IR"),
                                  later={4: {CREATE_GSIS: {"status": "ACTIVE", "report_status": None}}})
    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)
    if not mirror.creates:
        pytest.skip("the MUBASE snapshot has no spare free-agent record at WR")
    c = mirror.creates[0]
    donor = next(p for p in snap["players"] if p["pgid"] == c["donor_pgid"])
    assert donor["status"] == "FreeAgent"
    assert c["edit"]["set"]["IsInjuredReserve"] is True
    assert c["edit"]["set"]["InjuryStatus"] == "Injured"
    assert c["edit"]["set"]["MinInjuryDuration"] >= 1
    assert mirror.links[CREATE_GSIS] == c["donor_pgid"]


def test_f1_a_created_real_active_starter_reaches_the_real_slot_on_mubase(mubase_snapshot):
    snap = mubase_snapshot
    adapter, _ = creation_adapter(snap, real_card=real_card(status="ACTIVE"))
    mirror = adapter.plan_week(FakeCursor(), snap, SEASON, "REGULAR_WEEK", 3)
    if not mirror.creates:
        pytest.skip("the MUBASE snapshot has no spare free-agent record at WR")
    c = mirror.creates[0]
    p = lo.Plan(snap, "REGULAR_WEEK", SEASON, 3, None, "seed", {0: CHI, 1: AZ},
                {pid(x["pgid"]): x["pgid"] for x in snap["players"]})
    adapter.bind(mirror, p)

    lo.step_nfl_mirror(p, mirror)

    assert p.roster_status(c["donor_pgid"]) == ("Signed", 0)
    assert adapter.chart_override(0) == {"WR": [c["donor_pgid"]]}
    assert p.edits[c["donor_pgid"]]["set"]["IsInjuredReserve"] is False


# ----------------------------------------------------------- audit-4 U6: PostgreSQL acceptance ----
def _scratch_conn():
    dsn = os.environ.get("MU_U13_TEST_DSN") or os.environ.get("TEST_DATABASE_URL") \
        or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("no scratch database DSN")
    import psycopg
    conn = psycopg.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.nfl_mirror_moves') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.close()
            pytest.skip("the scratch database lacks the nfl_mirror schema")
    return conn


def _run_row(cur, season, week):
    """A `league_office_runs` row for the week (`nfl_mirror_weeks.run_id` is a foreign key)."""
    cur.execute("""INSERT INTO league_office_runs (season, week, stage, save_name, status, edits)
                   VALUES (%s, %s, 'REGULAR_WEEK', 'u13 scratch', 'PLANNED', '{}'::jsonb) RETURNING id""",
                (season, week))
    return cur.fetchone()[0]


@pytest.mark.integration
def test_f3_postgres_mark_written_keeps_refused_moves_refused():
    conn = _scratch_conn()
    season, week, tags = 2099, 3, {"trade": "mirror:trade:u13-f3a", "release": "mirror:release:u13-f3b",
                                   "sign": "mirror:sign:u13-f3c"}
    try:
        with conn.cursor() as cur:
            run_id = _run_row(cur, season, week)
            cur.execute("DELETE FROM nfl_mirror_moves WHERE season = %s AND week = %s", (season, week))
            mirror_store.record_week(cur, season, week, status="PLANNED", real_week=3, mode="CATCH_UP")
            mirror_store.record_moves(cur, season, week, [
                {"kind": "TRADE", "detail": {"tag": tags["trade"]}},
                {"kind": "RELEASE", "detail": {"tag": tags["release"]}},
                {"kind": "SIGN", "detail": {"tag": tags["sign"]}}], replace=True)
            outcomes = {tags["trade"]: ["REFUSED", "P L3 is FreeAgent, not on a roster"],
                        tags["release"]: ["WRITTEN", None]}
            mirror_store.mark_written(cur, season, week, run_id, outcomes=outcomes,
                                      counts={"release": 1}, report="1 roster move written; 1 refused")
            cur.execute("""SELECT detail ->> 'tag', status, reason FROM nfl_mirror_moves
                           WHERE season = %s AND week = %s""", (season, week))
            rows = {tag: (status, reason) for tag, status, reason in cur.fetchall()}
            assert rows[tags["trade"]][0] == "REFUSED"
            assert rows[tags["release"]][0] == "WRITTEN"
            assert rows[tags["sign"]][0] == "SKIPPED" and "no outcome recorded" in rows[tags["sign"]][1]
            cur.execute("SELECT status, counts, report FROM nfl_mirror_weeks WHERE season = %s AND week = %s",
                        (season, week))
            status, counts, report = cur.fetchone()
            assert (status, counts, report) == ("WRITTEN", {"release": 1}, "1 roster move written; 1 refused")
            mirror_store.mark_written(cur, season, week, run_id, outcomes=outcomes,
                                      counts={"release": 1}, report="1 roster move written; 1 refused")
            cur.execute("""SELECT detail ->> 'tag', status FROM nfl_mirror_moves
                           WHERE season = %s AND week = %s""", (season, week))
            assert {tag: status for tag, status in cur.fetchall()} == {
                tags["trade"]: "REFUSED", tags["release"]: "WRITTEN", tags["sign"]: "SKIPPED"}
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.integration
def test_f4_postgres_a_link_persists_once_with_its_method():
    conn = _scratch_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM nfl_player_links WHERE gsis_id LIKE '00-U13-%'")
            cur.execute("DELETE FROM nfl_players WHERE gsis_id LIKE '00-U13-%'")
            cur.execute("DELETE FROM players WHERE player_name = 'U13 Link'")
            cur.execute("""INSERT INTO players (player_name, full_name, origin, madden_pgid)
                           VALUES ('U13 Link', 'U13 Link', 'SAVE', 990013) RETURNING id""")
            player_id = cur.fetchone()[0]
            cur.execute("INSERT INTO nfl_players (gsis_id, display_name) VALUES ('00-U13-OVR', 'Override Man')")
            assert lo._record_link(cur, player_id, "00-U13-OVR", "OVERRIDE", None, {"note": "JR"}) is True
            assert lo._record_link(cur, player_id, "00-U13-OVR", "OVERRIDE", None, {"note": "JR"}) is False
            cur.execute("SELECT method, evidence ->> 'note' FROM nfl_player_links WHERE player_id = %s",
                        (player_id,))
            assert cur.fetchall() == [("OVERRIDE", "JR")]
            cur.execute("""INSERT INTO players (player_name, full_name, origin, madden_pgid)
                           VALUES ('U13 Link', 'U13 Link 2', 'SAVE', 990014) RETURNING id""")
            other = cur.fetchone()[0]
            with pytest.raises(ValueError):
                lo._record_link(cur, other, "00-U13-OVR", "ALIAS", 3, {})
            with pytest.raises(ValueError):
                lo._record_link(cur, other, "00-U13-CRE", "CREATED", None, {})
            assert mirror_store.add_created_link(cur, other, "00-U13-CRE",
                                                 {"name": "Created Man", "position": "WR"}) is True
            assert mirror_store.add_created_link(cur, other, "00-U13-CRE",
                                                 {"name": "Created Man"}) is False
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.integration
def test_f2_postgres_a_refused_creation_persists_no_link_or_transaction():
    conn = _scratch_conn()
    season, week, gsis = 2099, 4, "00-U13-REFUSED"
    snap = snapshot()
    entry = create_entry()
    entry["gsis_id"] = gsis
    mirror = make_mirror(creates=[entry])
    try:
        with conn.cursor() as cur:
            run_id = _run_row(cur, season, week)
            cur.execute("DELETE FROM nfl_player_links WHERE gsis_id = %s", (gsis,))
            p = new_plan(snap)
            assert p.sign_player(5, 1, "Roster", contract(), lo.PRIORITY_JR, "JR signs him first")
            p.db["mirror"] = mirror.record()
            lo.step_nfl_mirror(p, mirror)
            assert p.db["mirror"]["links"] == []
            plan = {"db": p.db, "team_ids": p.teams, "pid_by_pgid": p.pid_by_pgid}
            lo._record_mirror(cur, plan, run_id)
            lo._record_mirror(cur, plan, run_id)                   # recover is idempotent
            cur.execute("SELECT count(*) FROM nfl_player_links WHERE gsis_id = %s", (gsis,))
            assert cur.fetchone()[0] == 0
            cur.execute("""SELECT count(*) FROM transactions
                           WHERE season = %s AND week = %s AND raw_text = 'NFL mirror'""", (season, week))
            assert cur.fetchone()[0] == 0
            assert p.db["mirror"]["outcomes"][f"mirror:create:{gsis}"][0] == "REFUSED"
            assert p.edits.get(5) is None
    finally:
        conn.rollback()
        conn.close()
