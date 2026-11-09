"""pipeline/league_office.py: stage detection, the plan per stage with fake engines and a fake database, composition
with the progression engine's prepared envelope (PROGRESSION_PLAN §5.3a), and the write path (PREPARED operation
committed first, temp copy, read-back, the franchise-closed rule, acknowledgement only after a verified replacement,
--recover, dry run, setup bootstrap) with fake Node calls and fake progression modules. No database."""
import copy
import json
import os

import pytest

from engines import contracts_engine as ce
from pipeline import league_office as lo

CHI, AZ = 100, 101          # team ids; team indexes 0 and 1
SEASON = 2099


def pid(pgid):
    return pgid + 1000


def contract(length=4, cur=1, sal=(100, 200, 300, 400), bon=(50, 50, 50, 50)):
    return {"length": length, "current_year": cur, "salary_by_year": list(sal) + [0] * (7 - len(sal)),
            "bonus_by_year": list(bon) + [0] * (7 - len(bon))}


def player(pgid, team, status="Signed", pos="WR", ovr=70, age=26, years_pro=3, injury=None, contract_=None,
           confidence=50, first="P", last=None, ratings=None):
    return {"pgid": pgid, "first_name": first, "last_name": last or f"L{pgid}", "position": pos, "archetype": None,
            "status": status, "team_index": team, "age": age, "years_pro": years_pro, "overall": ovr,
            "confidence": confidence, "ratings": ratings or {"Speed": 80, "Awareness": 70},
            "contract": contract_ if contract_ is not None else (contract() if status == "Signed" else None),
            "injury": injury or {"status": "Uninjured", "type": "Invalid_", "min_weeks": 0, "max_weeks": 0,
                                 "injured_reserve": False},
            "traits": {}, "cap_salary": 250}


def snapshot(stage="NFLSeason", week_type="RegularSeason", week=3, off=None, players=None, year=SEASON):
    return {"meta": {"reader_version": 5}, "franchise": {"league_id": 7},
            "season": {"calendar_year": year, "stage": stage, "week_type": week_type, "week_index": week,
                       "offseason_stage": off},
            "teams": [{"team_index": 0, "short_name": "CHI"}, {"team_index": 1, "short_name": "ARI"}],
            "players": players if players is not None else [
                player(1, 0, pos="QB", ovr=85), player(2, 0), player(3, 1),
                player(4, 32, status="FreeAgent", ovr=78), player(5, 32, status="FreeAgent", ovr=60),
                player(6, 1, status="PracticeSquad", years_pro=1)],
            "depth_charts": [], "draft_class": [],
            "league_settings": {"sliders": {"progression_by_age": {"table_id": 9, "row": 0, "values": {"int0": 5}}},
                                "league_setting": {"table_id": 4290, "row": 0}}}


STATE = {"team_table_id": 5934, "salary_cap": 25540, "spare_contract_rows": 0,
         "teams": [{"team_index": 0, "row": 1, "TEAM_SALARY": 10000, "SalCapCapRoom": 150000,
                    "SalCapNextYearCapRoom": 150000, "SalCapSpendingMoney": 150000},
                   {"team_index": 1, "row": 2, "TEAM_SALARY": 9000, "SalCapCapRoom": 160000,
                    "SalCapNextYearCapRoom": 160000, "SalCapSpendingMoney": 160000}]}
SLIDERS = {"table": "int[]", "tableId": 9, "row": 0, "set": {"int0": 0}}


class FakeDeps(lo.Deps):
    """Every dependency faked; tests set attributes to change what an engine returns. The progression fakes keep
    the §5.3a envelope shape and record their PREPARED events in the fake cursor's (transactional) store."""

    def __init__(self, snap=None, **kw):
        self.snap = snap
        self.pending, self.engine_props, self.offers, self.drift_report, self.stored = [], [], [], {}, {}
        self.resign, self.cpu, self.decisions, self.cut, self.ps, self.claims, self.udfa = [], [], [], [], [], [], []
        self.prep_doc = {"players": [], "records": [SLIDERS]}
        self.event_ids = ["ev-1"]
        self.prev_inj, self.released, self.ps_ids, self.kept = {}, [], set(), set()
        self.draft = ([], {}, {})
        self.users, self.jr, self.schemes = set(), {}, {}
        self.running, self.write_ok, self.read_back = False, True, None
        self.calls, self.acks, self.seen = [], [], {}
        self.ack_error = None
        self.cur = None
        self.form_rows = 0
        self.state = copy.deepcopy(STATE)
        self.__dict__.update(kw)

    def team_ids(self, cur, snapshot):
        return {0: CHI, 1: AZ}

    def pgid_map(self, cur):
        return {pid(p["pgid"]): p["pgid"] for p in (self.snap or snapshot())["players"]}

    def user_team_ids(self, cur):
        return self.users

    def league_caps(self, cur):
        return getattr(self, "caps", {SEASON: 255_400_000})

    def sync_team_caps(self, cur, season, cap_dollars):
        self.calls.append(("sync_team_caps", season, cap_dollars))
        super().sync_team_caps(cur, season, cap_dollars)

    def pending_moves(self, cur):
        return self.pending

    def engine_trades(self, cur, season):
        return self.engine_props

    def accepted_offers(self, cur, season):
        return self.offers

    def drift(self, snapshot, cur, season, week):
        self.calls.append(("drift", season, week))
        return self.drift_report

    def stored_contracts(self, cur, season):
        return self.stored

    def resign_decisions(self, cur, season, seed):
        return self.resign

    def cpu_offers(self, cur, season, stage, wave, seed):
        self.calls.append(("cpu_offers", stage, wave))
        return self.cpu

    def free_agent_pool(self, cur, season, target):
        return [{"id": pid(4), "ovr": 78}]

    def run_wave(self, players, offers, wave, seed):
        return self.decisions

    def market_needs(self, cur, season, target):
        return {CHI: {"needs": {"WR": 5}}}

    def udfa_market(self, prospects, needs, seed):
        self.calls.append(("udfa", sorted(p["id"] for p in prospects)))
        return self.udfa

    def cutdown(self, cur, season, seed, limit=lo.ROSTER_LIMIT):
        self.calls.append(("cutdown", limit))
        return self.cut

    def practice_squad(self, cur, season, seed):
        return self.ps

    def waivers(self, cur, season, week, released, seed):
        self.calls.append(("waivers", sorted(released)))
        return self.claims

    # -- progression fakes
    def _prepared(self, cur, fp):
        doc = copy.deepcopy(self.prep_doc)
        if hasattr(cur, "db"):
            for e in self.event_ids:
                cur.db["events"][e] = "PREPARED"
        return {"document": doc, "event_ids": list(self.event_ids), "source_fingerprint": fp,
                "edits_hash": lo.canonical_hash(doc)}

    def progression_weekly(self, cur, season, week, snapshot, source_fingerprint):
        self.calls.append(("weekly", season, week, source_fingerprint))
        self.seen["weekly_pgids"] = sorted(p["pgid"] for p in snapshot["players"])
        return self._prepared(cur, source_fingerprint)

    def progression_offseason(self, cur, snapshot, target_season, source_fingerprint):
        self.calls.append(("offseason", target_season, source_fingerprint))
        return self._prepared(cur, source_fingerprint)

    def progression_explain(self, cur, prepared):
        self.calls.append(("explain", tuple(prepared["event_ids"])))
        return [{"status": "PROPOSED", "event_id": e} for e in prepared["event_ids"]]

    def progression_acknowledge(self, cur, prepared, output_snapshot, output_fingerprint):
        if self.ack_error:
            raise self.ack_error
        save = self.seen.get("save")
        self.acks.append({"prepared": copy.deepcopy(prepared), "output_fingerprint": output_fingerprint,
                          "save_bytes": open(save, "rb").read() if save else None,
                          "committed": copy.deepcopy(cur.committed) if hasattr(cur, "committed") else None})
        for e in prepared["event_ids"]:
            cur.db["events"][e] = "APPLIED"
        return [{"event_id": e} for e in prepared["event_ids"]]

    def progression_bootstrap(self, cur, snapshot, season, source_fingerprint):
        self.calls.append(("bootstrap", season, source_fingerprint))
        return [{"player_id": pid(p["pgid"])} for p in snapshot["players"]]

    def applied_form_rows(self, cur, season):
        return self.form_rows

    def rating_edits(self, rows, snapshot, pgid_by_player):
        out = []
        for r in rows:
            g = pgid_by_player.get(r.get("player_id"), r.get("pgid"))
            out.append({"pgid": g, "set": {f"{k}Rating": 80 + v for k, v in r["ratings_diff"].items()}})
        return out

    def slider_edits(self, snapshot):
        return [copy.deepcopy(SLIDERS)]

    def staff_depth_chart(self, roster, scheme, injuries):
        ids = [p["player_id"] for p in sorted(roster, key=lambda p: -(p["overall"] or 0)) if not injuries[p["player_id"]]["out"]]
        return {"QB": ids[:1], "WR": ids[1:3]}

    def team_schemes(self, cur, season):
        return self.schemes

    def jr_charts(self, cur, season):
        return self.jr

    def previous_injuries(self, cur, season, week):
        return self.prev_inj

    def released_last_run(self, cur):
        return self.released

    def ps_without_contract(self, cur, season):
        return self.ps_ids

    def kept_retirees(self, cur, season):
        return self.kept

    def draft_inputs(self, cur, season):
        return self.draft

    def league_state(self, save_path):
        return self.state

    def run_setup(self, save, out_path):
        with open(out_path, "wb") as fh:
            fh.write(b"NEUTRAL SAVE")
        return True, json.dumps({"verified": True, "usersCreated": ["CHI"]}), "users created 1"

    def write_save(self, save_path, edits_path, out_path):
        cur = self.cur
        self.calls.append(("write", save_path, out_path))
        self.seen["at_write"] = {"save_bytes": open(save_path, "rb").read(),
                                 "committed": copy.deepcopy(cur.committed) if cur is not None else None}
        with open(edits_path) as fh:
            self.written_edits = json.load(fh)
        if not self.write_ok:
            return False, "read-back failed: pgid 1 TeamIndex"
        with open(out_path, "wb") as fh:
            fh.write(b"WRITTEN COPY")
        return True, "ok"

    def read_snapshot(self, path):
        if self.read_back is not None:
            return self.read_back
        # what the edits make true
        snap = copy.deepcopy(self.snap)
        players = {p["pgid"]: p for p in snap["players"]}
        e = self.written_edits
        for g in e.get("release") or []:
            players[g].update(status="FreeAgent", team_index=32)
        for s in e.get("sign") or []:
            players[s["pgid"]].update(status="Signed" if s["list"] == "Roster" else "PracticeSquad",
                                      team_index=s["team_index"])
        for t in e.get("trade") or []:
            for g in t["pgids"]:
                players[g]["team_index"] = t["to_team_index"]
        for pe in e.get("players") or []:
            p = players[pe["pgid"]]
            for k, v in pe["set"].items():
                if k == "IsInjuredReserve":
                    p["injury"]["injured_reserve"] = v
                elif k == "MinInjuryDuration":
                    p["injury"]["min_weeks"] = v
                elif k == "MaxInjuryDuration":
                    p["injury"]["max_weeks"] = v
                elif k == "ConfidenceRating":
                    p["confidence"] = v
                elif k == "PLYR_CAPSALARY":
                    p["cap_salary"] = v
                elif k == "ContractStatus":
                    p["status"] = v
                elif k == "TeamIndex":
                    p["team_index"] = v
                elif k.endswith("Rating") and k[:-6] in p["ratings"]:
                    p["ratings"][k[:-6]] = v
        return snap

    def madden_running(self):
        return self.running


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = self.rollbacks = 0

    def commit(self):
        self.commits += 1
        self.cur.committed = copy.deepcopy(self.cur.db)

    def rollback(self):
        self.rollbacks += 1
        self.cur.db = copy.deepcopy(self.cur.committed)


class FakeCursor:
    """Records SQL; keeps league_office_runs rows and progression events in a store that commit / rollback act on."""

    def __init__(self):
        self.db = {"runs": {}, "events": {}}
        self.committed = copy.deepcopy(self.db)
        self.connection = FakeConn(self)
        self.sql = []
        self._next = 0
        self._ret = None

    def execute(self, sql, args=None):
        norm = " ".join(sql.split())
        self.sql.append((norm, args))
        self._ret = None
        runs = self.db["runs"]
        if norm.startswith("INSERT INTO league_office_runs"):
            self._next += 1
            season, week, stage, save_name, status, edits, summary, drift, error, _ = args
            runs[self._next] = {"status": status, "stage": stage, "save_name": save_name, "edits": json.loads(edits),
                                "summary": json.loads(summary) if summary else None, "error": error}
            self._ret = (self._next,)
        elif norm.startswith("UPDATE league_office_runs SET status"):
            status, edits, summary, drift, error, rid = args
            r = runs[rid]
            r["status"], r["error"] = status, error
            if edits is not None:
                r["edits"] = json.loads(edits)
            if summary is not None:
                r["summary"] = json.loads(summary)
        elif norm.startswith("UPDATE league_office_runs SET edits"):
            edits, summary, rid = args
            if edits is not None:
                runs[rid]["edits"] = json.loads(edits)
            runs[rid]["summary"] = json.loads(summary)
        elif norm.startswith("SELECT id, status, stage, save_name, edits, summary FROM league_office_runs"):
            r = runs.get(args[0])
            self._ret = (args[0], r["status"], r["stage"], r["save_name"], json.dumps(r["edits"]),
                         json.dumps(r["summary"])) if r else None

    def executemany(self, sql, rows):
        self.sql.append((" ".join(sql.split()), list(rows)))

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return []

    def runs(self):
        return [a for s, a in self.sql if s.startswith("INSERT INTO league_office_runs")]

    def finished(self):
        return [a for s, a in self.sql if s.startswith("UPDATE league_office_runs SET status")]

    def run_row(self, run_id, committed=True):
        return (self.committed if committed else self.db)["runs"][run_id]


FP = "f" * 64


def plan(stage, snap=None, deps=None, wave=None, state="default", fp=FP):
    snap = snap or snapshot()
    deps = deps or FakeDeps(snap)
    deps.snap = snap
    return lo.plan_stage(FakeCursor(), snap, stage, "seed", deps=deps, wave=wave,
                         state=copy.deepcopy(STATE) if state == "default" else state, source_fingerprint=fp)


# ------------------------------------------------------------------ stages ----
@pytest.mark.parametrize("season,expected", [
    ({"stage": "NFLSeason", "week_type": "RegularSeason", "week_index": 3}, ("REGULAR_WEEK", None)),
    ({"stage": "NFLSeason", "week_type": "WildcardPlayoff", "week_index": 18}, ("PLAYOFF_WEEK", None)),
    ({"stage": "NFLSeason", "week_type": "SuperBowl", "week_index": 22}, ("PLAYOFF_WEEK", None)),
    # Madden's 8 offseason stops (docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md): week index, offseason stage
    ({"stage": "OffSeason", "week_type": "OffSeason", "week_index": 0, "offseason_stage": 1}, ("OFFSEASON_START", None)),
    ({"stage": "OffSeason", "week_type": "SuperBowl", "week_index": 0, "offseason_stage": 1}, ("OFFSEASON_START", None)),
    ({"stage": "OffSeason", "week_index": 1, "offseason_stage": 1}, ("RESIGN", None)),
    ({"stage": "OffSeason", "week_index": 2, "offseason_stage": 1}, ("FREE_AGENCY", 1)),
    ({"stage": "OffSeason", "week_index": 3, "offseason_stage": 2}, ("FREE_AGENCY", 2)),
    ({"stage": "OffSeason", "week_index": 5, "offseason_stage": 4}, ("COMBINE", 3)),
    ({"stage": "OffSeason", "week_index": 7, "offseason_stage": 6}, ("PRO_DAY", None)),
    ({"stage": "OffSeason", "week_index": 11, "offseason_stage": 9}, ("DRAFT", None)),
    ({"stage": "OffSeason", "week_index": 15, "offseason_stage": 10}, ("UDFA", None)),
    ({"stage": "PreSeason", "week_type": "PreSeason", "week_index": 0}, ("PRESEASON", None)),
    ({"stage": "PreSeason", "week_type": "PreSeason", "week_index": 3}, ("CUTDOWN", None)),
    # without CurrentOffseasonStage the week index alone decides
    ({"stage": "OffSeason", "week_index": 0}, ("OFFSEASON_START", None)),
    ({"stage": "OffSeason", "week_index": 3}, ("FREE_AGENCY", 2)),
    ({"stage": "OffSeason", "week_index": 5}, ("COMBINE", 3)),
    ({"stage": "OffSeason", "week_index": 11}, ("DRAFT", None)),
    ({"stage": "OffSeason", "week_index": 16}, ("UDFA", None)),
])
def test_detect_stage(season, expected):
    snap = {"season": dict({"calendar_year": SEASON}, **season)}
    assert lo.stage_and_wave(snap) == expected
    assert lo.detect_stage(snap) == expected[0]


@pytest.mark.parametrize("season", [
    {"stage": "Mystery", "week_type": "Nope", "week_index": 1},
    {"stage": "OffSeason", "week_index": 6},                          # not one of the 8 stops
    {"stage": "OffSeason", "week_index": 4, "offseason_stage": 3},    # internal stage number, never a stop
    {"stage": "OffSeason", "week_index": 3, "offseason_stage": 1},
])
def test_detect_stage_unknown(season):
    with pytest.raises(lo.StageError):
        lo.detect_stage({"season": season})


def test_eight_offseason_stops():
    assert len(lo.OFFSEASON_STOPS) == 8
    assert [r[0] for r in lo.OFFSEASON_STOPS] == [0, 1, 2, 3, 5, 7, 11, 15]
    assert [r[1] for r in lo.OFFSEASON_STOPS] == [1, 1, 1, 2, 4, 6, 9, 10]


def test_stage_weeks_reconcile_with_contracts_engine():
    for name, week in ce.OFFSEASON_STAGE_WEEKS.items():
        assert lo.STAGE_WEEKS[name] == week
    assert lo.STAGE_WEEKS["FREE_AGENCY"] < lo.STAGE_WEEKS["COMBINE"] < lo.STAGE_WEEKS["PRO_DAY"] < lo.STAGE_WEEKS["DRAFT"]
    assert lo.league_week(snapshot(week=3), "REGULAR_WEEK") == 4
    assert lo.league_season(snapshot(year=2100), "PRESEASON") == 2099
    assert lo.save_year(2099, "RESIGN") == 2099 and lo.save_year(2099, "FREE_AGENCY") == 2100


def test_progression_years():
    assert lo.offseason_target_season(2026) == 2027
    assert lo.setup_application_season("REGULAR_WEEK", 2026) == 2026
    assert lo.setup_application_season("OFFSEASON_START", 2026) == 2026
    assert lo.setup_application_season("PRESEASON", 2026) == 2027
    assert lo.setup_application_season("DRAFT", 2026) == 2027


def test_canonical_hash_is_the_engines_fingerprint():
    from engines import progression_checkpoint as pc
    doc = {"players": [{"pgid": 2, "set": {"SpeedRating": 80}}], "records": [SLIDERS]}
    assert lo.canonical_hash(doc) == pc.fingerprint(doc)


# ------------------------------------------------------------ pure helpers ----
def test_contract_block_offsets_and_cap_hit():
    new = {"length": 2, "salary_by_year": [1_000_000, 2_000_000], "bonus_by_year": [500_000, 500_000]}
    b, dropped = lo.contract_block(new, None, 0, slots=4)
    assert b == {"Length": 2, "CurrentYear": 0, "SalaryYear0": 100, "SalaryYear1": 200, "SalaryYear2": 0,
                 "SalaryYear3": 0, "BonusYear0": 50, "BonusYear1": 50, "BonusYear2": 0, "BonusYear3": 0}
    assert dropped == 0 and lo.block_hit(b, 0) == 150
    old = contract(length=2, cur=1, sal=(90, 95), bon=(10, 20))
    b, _ = lo.contract_block(new, old, 1, slots=4)
    assert (b["Length"], b["SalaryYear0"], b["BonusYear0"], b["SalaryYear1"]) == (3, 95, 20, 100)
    b, dropped = lo.contract_block(dict(new, length=2), old, 1, slots=2)
    assert dropped == 1 and b["Length"] == 2


def test_release_cap_accelerates_bonus():
    b = lo.snapshot_block(contract(length=4, cur=1, sal=(100, 200, 300, 400), bon=(50, 50, 50, 50)))
    assert lo.release_cap(b) == (250 - 150, 350, 200)


def test_choose_picks_jr_board_fallback():
    slots = [{"overall_pick": 1, "team_id": CHI}, {"overall_pick": 2, "team_id": AZ}, {"overall_pick": 3, "team_id": CHI}]
    picks = lo.choose_picks(slots, {CHI: [10, 11], AZ: [10]}, {2: 12}, [10, 11, 12, 13])
    assert [(p["pgid"], p["source"]) for p in picks] == [(10, "BOARD"), (12, "JR"), (11, "BOARD")]
    picks = lo.choose_picks(slots, {}, {}, [13])
    assert [(p["pgid"], p["source"]) for p in picks] == [(13, "FALLBACK")]


def test_keep_retired_rule_is_seeded():
    assert lo.keep_retired({"pgid": 1, "age": 30, "overall": 80}, SEASON, "s")[0] is True
    assert lo.keep_retired({"pgid": 1, "age": 36, "overall": 90}, SEASON, "s")[0] is False
    assert lo.keep_retired({"pgid": 1, "age": 30, "overall": 70}, SEASON, "s")[0] is False
    late = {"pgid": 9, "age": 34, "overall": 88}
    assert lo.keep_retired(late, SEASON, "s") == lo.keep_retired(late, SEASON, "s")


def test_injury_weeks_carry_and_draw():
    inj = {"type": "KneeACLSprain", "min_weeks": 2, "max_weeks": 6}
    w = lo.injury_weeks(inj, None, 5, "s", 1)
    assert 2 <= w <= 6 and w == lo.injury_weeks(inj, None, 5, "s", 1)
    assert lo.injury_weeks(inj, {"week": 3, "mu_weeks_out": 7, "type": None}, 5, "s", 1) == 5


# --------------------------------------------------- progression envelope ----
def test_weekly_envelope_merged_and_no_separate_form_call():
    snap = snapshot(players=[
        player(1, 0, pos="QB", ovr=85),
        player(2, 0, injury={"status": "Injured", "type": "KneeACLSprain", "min_weeks": 6, "max_weeks": 8,
                             "injured_reserve": False}),
        player(3, 0, injury={"status": "Uninjured", "type": "Invalid_", "min_weeks": 0, "max_weeks": 0,
                             "injured_reserve": True}),
        player(7, 1, ovr=90), player(4, 32, status="FreeAgent")])
    prep = {"players": [{"pgid": 1, "set": {"SpeedRating": 81, "OverallRating": 86, "ConfidenceRating": 50}},
                        {"pgid": 2, "set": {"AwarenessRating": 72, "ExperiencePoints": 0}}],
            "records": [SLIDERS]}
    deps = FakeDeps(snap, prep_doc=prep)
    p = plan("REGULAR_WEEK", snap, deps)
    sets = {e["pgid"]: e["set"] for e in p["edits"]["players"]}
    assert sets[1] == {"SpeedRating": 81, "OverallRating": 86, "ConfidenceRating": 50}
    assert sets[2]["IsInjuredReserve"] is True and 6 <= sets[2]["MinInjuryDuration"] <= 8   # one entry, both sources
    assert sets[2]["AwarenessRating"] == 72 and sets[2]["MinInjuryDuration"] == sets[2]["MaxInjuryDuration"]
    assert sets[3]["IsInjuredReserve"] is False          # healed but left on IR: activated
    assert p["edits"]["records"] == [SLIDERS]
    assert p["db"]["injuries"] == [{"player_id": pid(2), "mu_weeks_out": sets[2]["MinInjuryDuration"]}]
    charts = {c["team_index"]: c["slots"] for c in p["edits"]["depthCharts"]}
    assert charts[0]["QB"] == [1] and 2 not in charts[0]["WR"] and 3 in charts[0]["WR"]
    # the envelope is kept unchanged; the combined document has its own hash
    assert p["prepared"]["document"] == prep and p["prepared"]["edits_hash"] == lo.canonical_hash(prep)
    assert p["prepared"]["source_fingerprint"] == FP and p["prepared"]["event_ids"] == ["ev-1"]
    assert p["edits_hash"] == lo.canonical_hash(p["edits"]) != p["prepared"]["edits_hash"]
    assert [c for c in deps.calls if c[0] == "weekly"] == [("weekly", SEASON, 4, FP)]
    assert p["summary"]["progression"]["kind"] == "WEEKLY" and p["summary"]["progression"]["events"] == 1
    # no form engine, morale or rating-row hook in the league office any more
    assert not hasattr(lo.Deps, "form_edits") and not hasattr(lo.Deps, "pass_edits")
    assert not hasattr(lo.Deps, "in_season_checkpoint")


def test_offseason_uses_prepare_offseason_pass_with_target_year():
    snap = snapshot("OffSeason", "OffSeason", 0, 1, players=[
        player(1, 0, pos="QB", ovr=85), player(2, 0, years_pro=1),
        player(8, 32, status="Retired", age=30, ovr=80), player(9, 32, status="Retired", age=37, ovr=70)])
    deps = FakeDeps(snap, ps_ids={pid(2)},
                    prep_doc={"players": [{"pgid": 1, "set": {"SpeedRating": 88, "ExperiencePoints": 0}},
                                          {"pgid": 8, "set": {"ExperiencePoints": 0}}],
                              "records": [{"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_88": 0}}]})
    p = plan("OFFSEASON_START", snap, deps)
    sets = {e["pgid"]: e for e in p["edits"]["players"]}
    assert sets[1]["set"] == {"SpeedRating": 88, "ExperiencePoints": 0}
    assert ("offseason", SEASON + 1, FP) in deps.calls and not any(c[0] == "weekly" for c in deps.calls)
    assert p["progression"] == dict(p["progression"], kind="OFFSEASON", earned_season=SEASON, target_season=SEASON + 1)
    assert {"table": "LeagueSetting", "tableId": 4290, "row": 0, "set": {"Field_88": 0}} in p["edits"]["records"]
    assert sets[8]["set"] == {"ContractStatus": "FreeAgent", "TeamIndex": 32, "ExperiencePoints": 0}
    assert 9 not in sets
    assert {r["player_id"]: r["mu_keep"] for r in p["db"]["retirements"]} == {pid(8): True, pid(9): False}
    # promoted squad player: the current year kept, then a 1-year minimum for the coming league year
    block = sets[2]["contract"]
    assert block["Length"] == 2 and block["SalaryYear0"] == 200
    assert block["SalaryYear1"] == int(ce.minimum_salary(2, p["summary"]["salary_cap"]["cap"]) / 10_000 + 0.5)
    assert sets[2]["set"]["PLYR_CAPSALARY"] == 250


def test_progression_only_at_its_stages():
    for stage, snap in (("RESIGN", snapshot("OffSeason", "OffSeason", 1, 1)),
                        ("PRESEASON", snapshot("PreSeason", "PreSeason", 0, year=SEASON + 1))):
        deps = FakeDeps(snap)
        p = plan(stage, snap, deps)
        assert p["prepared"] is None and not any(c[0] in ("weekly", "offseason") for c in deps.calls)


def test_missing_hook_raises(monkeypatch):
    from engines import progression
    deps = FakeDeps()
    deps.progression_weekly = lambda *a: lo.Deps.progression_weekly(deps, *a)
    monkeypatch.delattr(progression, "in_season_checkpoint")
    with pytest.raises(lo.ProgressionIntegrationError):
        plan("REGULAR_WEEK", deps=deps)


def test_old_hook_shape_and_missing_fingerprint_raise():
    deps = FakeDeps()
    deps.progression_weekly = lambda cur, season, week, snap, fp: [{"player_id": pid(2), "ratings_diff": {"Speed": 1}}]
    with pytest.raises(lo.ProgressionIntegrationError):
        plan("REGULAR_WEEK", deps=deps)
    deps = FakeDeps()
    deps.progression_weekly = lambda cur, season, week: {}             # the superseded three-argument hook
    with pytest.raises(lo.ProgressionIntegrationError, match="interface mismatch"):
        plan("REGULAR_WEEK", deps=deps)
    with pytest.raises(lo.ProgressionIntegrationError, match="sha256"):
        plan("REGULAR_WEEK", fp=None)
    deps = FakeDeps()
    real = deps.progression_weekly
    deps.progression_weekly = lambda cur, s, w, snap, fp: dict(real(cur, s, w, snap, fp), source_fingerprint="other")
    with pytest.raises(lo.ProgressionIntegrationError, match="different input save"):
        plan("REGULAR_WEEK", deps=deps)
    deps = FakeDeps()
    deps.progression_weekly = lambda cur, s, w, snap, fp: dict(real(cur, s, w, snap, fp), edits_hash="0" * 64)
    with pytest.raises(lo.ProgressionIntegrationError, match="edits_hash"):
        plan("REGULAR_WEEK", deps=deps)


def test_real_deps_call_the_engine_with_the_contract_signatures(monkeypatch):
    from engines import progression, progression_checkpoint as pc
    got = {}
    monkeypatch.setattr(progression, "in_season_checkpoint",
                        lambda cur, season, week, *, snapshot=None, source_fingerprint=None:
                        got.setdefault("weekly", (season, week, snapshot, source_fingerprint)))
    monkeypatch.setattr(pc, "prepare_offseason_pass", lambda cur, snapshot, season, *, source_fingerprint, config=None:
                        got.setdefault("offseason", (season, source_fingerprint)))
    monkeypatch.setattr(pc, "acknowledge_pass", lambda cur, prepared, observed, *, output_fingerprint,
                        writer_verified=False: got.setdefault("ack", (prepared, output_fingerprint, writer_verified)))
    monkeypatch.setattr(pc, "bootstrap_pass", lambda cur, snapshot, season, *, source_fingerprint, form_neutral=False,
                        pgid_by_player=None: got.setdefault("boot", (season, source_fingerprint, form_neutral)))
    d = lo.Deps()
    d.progression_weekly(None, 2026, 5, {"players": []}, "in")
    d.progression_offseason(None, {"players": []}, 2027, "in")
    d.progression_acknowledge(None, {"x": 1}, {}, "out")
    d.progression_bootstrap(None, {}, 2026, "neutral")
    assert got["weekly"] == (2026, 5, {"players": []}, "in") and got["offseason"] == (2027, "in")
    assert got["ack"] == ({"x": 1}, "out", True) and got["boot"] == (2026, "neutral", True)


def test_released_players_are_excluded_before_progression_prepares():
    deps = FakeDeps(prep_doc={"players": [{"pgid": 1, "set": {"ExperiencePoints": 0}}], "records": []})
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    p = plan("REGULAR_WEEK", deps=deps)
    assert 2 not in deps.seen["weekly_pgids"] and 1 in deps.seen["weekly_pgids"]
    assert p["edits"]["release"] == [2] and all(e["pgid"] != 2 for e in p["edits"]["players"])
    assert p["progression"]["released_excluded"] == 1
    # an engine that still proposes for a released player is refused, never silently trimmed
    deps = FakeDeps(prep_doc={"players": [{"pgid": 2, "set": {"ExperiencePoints": 0}}], "records": []})
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    with pytest.raises(lo.CompositionError, match="releases"):
        plan("REGULAR_WEEK", deps=deps)


# ------------------------------------------------------------ composition ----
def test_compose_conflicts_raise():
    league = {"players": [{"pgid": 1, "set": {"MinInjuryDuration": 4}}], "records": [SLIDERS], "release": [5]}
    same = {"players": [{"pgid": 1, "set": {"MinInjuryDuration": 4, "SpeedRating": 90}}], "records": [SLIDERS]}
    doc = lo.compose(league, same, released={5})
    assert doc["players"] == [{"pgid": 1, "set": {"MinInjuryDuration": 4, "SpeedRating": 90}}]
    assert doc["records"] == [SLIDERS] and doc["release"] == [5]
    assert league["players"][0]["set"] == {"MinInjuryDuration": 4}               # inputs unchanged
    with pytest.raises(lo.CompositionError, match="MinInjuryDuration"):
        lo.compose(league, {"players": [{"pgid": 1, "set": {"MinInjuryDuration": 5}}]}, released={5})
    with pytest.raises(lo.CompositionError, match="int0"):
        lo.compose(league, {"records": [dict(SLIDERS, set={"int0": 3})]})
    with pytest.raises(lo.CompositionError, match="releases"):
        lo.compose(league, {"players": [{"pgid": 5, "set": {"ExperiencePoints": 0}}]}, released={5})
    with pytest.raises(lo.CompositionError, match="TeamIndex"):
        lo.compose(league, {"players": [{"pgid": 7, "set": {"TeamIndex": 3}}]}, moved={7: "sign"})


def test_field_conflict_between_league_office_and_progression_raises():
    snap = snapshot(players=[player(1, 0, pos="QB"), player(2, 0, injury={
        "status": "Injured", "type": "KneeACLSprain", "min_weeks": 2, "max_weeks": 2, "injured_reserve": False})])
    deps = FakeDeps(snap, prep_doc={"players": [{"pgid": 2, "set": {"MinInjuryDuration": 9}}], "records": []})
    with pytest.raises(lo.CompositionError, match="MinInjuryDuration"):
        plan("REGULAR_WEEK", snap, deps)


def test_field_conflict_inside_the_plan_raises():
    snap = snapshot()
    pl = lo.Plan(snap, "REGULAR_WEEK", SEASON, 4, None, "s", {0: CHI, 1: AZ}, {pid(1): 1})
    pl.set_fields(1, {"IsInjuredReserve": True})
    pl.set_fields(1, {"IsInjuredReserve": True})                 # same value: fine
    with pytest.raises(lo.CompositionError):
        pl.set_fields(1, {"IsInjuredReserve": False})


def test_jr_ir_move_decides_over_the_injury_step():
    snap = snapshot(players=[player(1, 0, pos="QB"), player(2, 0, injury={
        "status": "Injured", "type": "KneeACLSprain", "min_weeks": 6, "max_weeks": 6, "injured_reserve": True})])
    deps = FakeDeps(snap)
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "IR_ACTIVATE", "payload": {"player_id": pid(2)}}]
    p = plan("REGULAR_WEEK", snap, deps)
    sets = {e["pgid"]: e["set"] for e in p["edits"]["players"]}
    assert sets[2]["IsInjuredReserve"] is False and sets[2]["MinInjuryDuration"] == 6


# ------------------------------------------------------------ the plan ----
def test_roster_legality_keeps_a_buffer_under_the_limit():
    players = [player(1, 0, pos="QB", ovr=90)] + [player(100 + i, 0, ovr=50 + (i % 30)) for i in range(lo.MAX_ACTIVE + 2)]
    snap = snapshot(players=players)
    p = plan("REGULAR_WEEK", snap)
    released = p["edits"]["release"]
    assert len(released) == 3 and 1 not in released
    assert lo.MAX_ACTIVE == lo.ROSTER_LIMIT - lo.ROSTER_BUFFER == 63


def test_waivers_claim_last_runs_releases():
    snap = snapshot()
    deps = FakeDeps(snap, released=[4], state=dict(STATE, spare_contract_rows=1))
    deps.claims = [{"kind": "WAIVER_CLAIM", "player_id": pid(4), "team_id": AZ, "reason": "waiver claim",
                    "contract": {"length": 1, "current_year": 0, "salary_by_year": [1_000_000],
                                 "bonus_by_year": [0]}}]
    p = plan("REGULAR_WEEK", snap, deps, state=deps.state)
    assert ("waivers", [pid(4)]) in deps.calls
    assert p["edits"]["sign"] == [{"pgid": 4, "team_index": 1, "list": "Roster",
                                   "contract": lo.contract_block(deps.claims[0]["contract"], None, 0, 7)[0]}]


def test_resign_moves_rewrite_contracts():
    snap = snapshot("OffSeason", "OffSeason", 1, 1)
    deps = FakeDeps(snap)
    new = {"length": 3, "current_year": 0, "salary_by_year": [5_000_000] * 3, "bonus_by_year": [1_000_000] * 3}
    deps.resign = [{"kind": "CONTRACT", "player_id": pid(1), "team_id": CHI, "reason": "re-sign", "contract": new},
                   {"kind": "LET_GO", "player_id": pid(2), "team_id": CHI, "reason": "let go"},
                   {"kind": "TAG", "player_id": pid(3), "team_id": AZ, "reason": "tag", "contract": new}]
    p = plan("RESIGN", snap, deps)
    sets = {e["pgid"]: e for e in p["edits"]["players"]}
    assert sets[1]["contract"]["Length"] == 4 and sets[1]["contract"]["SalaryYear0"] == 200   # this year first
    assert sets[1]["contract"]["SalaryYear1"] == 500
    assert 2 not in sets and 3 in sets
    assert p["summary"]["resign:LET_GO"] == 1


def test_free_agency_signs_every_accepted_offer_without_waiting_for_releases():
    """P1-08: the writer fills an empty PlayerContract row, so more signings than spare rows no longer defer."""
    snap = snapshot("OffSeason", "OffSeason", 3, 2)
    c = {"length": 2, "current_year": 0, "salary_by_year": [3_000_000, 3_000_000], "bonus_by_year": [0, 0]}
    offers = [{"team_id": CHI, "player_id": pid(4), "contract": c, "offer_id": 55},
              {"team_id": AZ, "player_id": pid(5), "contract": c, "offer_id": 56}]
    decisions = [{"player_id": pid(4), "team_id": CHI, "offer_index": 0, "status": "ACCEPT"},
                 {"player_id": pid(5), "team_id": AZ, "offer_index": 1, "status": "ACCEPT"}]
    deps = FakeDeps(snap, cpu=offers, decisions=decisions)
    deps.signings_from_decisions = lambda d, o: lo.te.signings_from_decisions(d, o)
    st = dict(STATE, spare_contract_rows=1)
    p = plan("FREE_AGENCY", snap, deps, state=st)
    assert ("cpu_offers", "FREE_AGENCY", 2) in deps.calls
    assert [s["pgid"] for s in p["edits"]["sign"]] == [4, 5]
    assert [d["pgid"] for d in p["deferred"]] == []
    statuses = {o["id"]: (o.get("status"), o.get("skip")) for o in p["db"]["offers"]}
    assert statuses == {55: ("ACCEPTED", None), 56: ("ACCEPTED", None)}


def test_combine_runs_the_third_free_agency_wave():
    deps = FakeDeps()
    p = plan("COMBINE", snapshot("OffSeason", "OffSeason", 5, 4), deps)
    assert ("cpu_offers", "FREE_AGENCY", 3) in deps.calls
    assert p["edits"] == {} and p["week"] == lo.STAGE_WEEKS["COMBINE"] and p["wave"] == 3


def test_pro_day_only_every_stage_items():
    deps = FakeDeps()
    p = plan("PRO_DAY", snapshot("OffSeason", "OffSeason", 7, 6), deps)
    assert p["edits"] == {} and not any(c[0] == "cpu_offers" for c in deps.calls)


def test_draft_signs_picks_from_boards_and_jr():
    players = [player(1, 0), player(20, 32, status="Draft", first="Al", last="Pha", pos="QB", years_pro=0),
               player(21, 32, status="Draft", first="Be", last="Ta", pos="WR", years_pro=0),
               player(22, 32, status="Draft", first="Ga", last="Mma", pos="CB", years_pro=0)]
    snap = snapshot("OffSeason", "OffSeason", 11, 9, players=players)
    snap["draft_class"] = [{"pgid": 22, "initial_draft_rank": 0}, {"pgid": 21, "initial_draft_rank": 1},
                           {"pgid": 20, "initial_draft_rank": 2}]
    deps = FakeDeps(snap, draft=([{"overall_pick": 1, "team_id": AZ}, {"overall_pick": 2, "team_id": CHI},
                                  {"overall_pick": 3, "team_id": AZ}],
                                 {AZ: [("Be Ta", "WR")]}, {2: ("Al Pha", "QB")}))
    p = plan("DRAFT", snap, deps, state=dict(STATE, spare_contract_rows=3))
    got = [(s["pgid"], s["team_index"]) for s in p["edits"]["sign"]]
    assert got == [(21, 1), (20, 0), (22, 1)]
    rookie = ce.rookie_contract(1, SEASON + 1, 25540 * 10_000)
    assert p["edits"]["sign"][0]["contract"]["SalaryYear0"] == round(rookie["salary_by_year"][0] / 10_000)
    assert p["summary"]["draft_jr"] == 1 and p["summary"]["draft_board"] == 1 and p["summary"]["draft_fallback"] == 1


def test_udfa_signs_undrafted_rookies():
    players = [player(1, 0), player(30, 32, status="FreeAgent", years_pro=0, pos="WR")]
    snap = snapshot("OffSeason", "OffSeason", 15, 10, players=players)
    deps = FakeDeps(snap, udfa=[{"player_id": pid(30), "team_id": CHI, "status": "ACCEPT",
                                 "offer": {"years": 3, "total_value": 2_500_000, "bonus": 100_000}}])
    p = plan("UDFA", snap, deps, state=dict(STATE, spare_contract_rows=1))
    assert ("udfa", [pid(30)]) in deps.calls
    assert p["edits"]["sign"][0]["pgid"] == 30 and p["edits"]["sign"][0]["contract"]["Length"] == 3


def test_cutdown_releases_and_practice_squad():
    snap = snapshot("PreSeason", "PreSeason", 3, year=SEASON + 1)
    deps = FakeDeps(snap)
    deps.cut = [{"kind": "RELEASE", "player_id": pid(2), "team_id": CHI, "reason": "cut-down", "list": "Roster"}]
    deps.ps = [{"kind": "SIGN", "player_id": pid(5), "team_id": AZ, "list": "PracticeSquad", "contract": None},
               {"kind": "PS_PROMOTE", "player_id": pid(6), "team_id": AZ, "contract": {}}]
    p = plan("CUTDOWN", snap, deps)
    assert p["season"] == SEASON and p["edits"]["release"] == [2]
    assert p["edits"]["sign"] == [{"pgid": 5, "team_index": 1, "list": "PracticeSquad"}]
    assert [d["move"] for d in p["deferred"]] == ["PS_PROMOTE"]
    assert ("cutdown", lo.ROSTER_LIMIT) in deps.calls and "depthCharts" in p["edits"]


def test_preseason_enforces_roster_limit():
    deps = FakeDeps()
    plan("PRESEASON", snapshot("PreSeason", "PreSeason", 0, year=SEASON + 1), deps)
    assert ("cutdown", lo.ROSTER_LIMIT) in deps.calls


# ------------------------------------------------- JR's moves and offers ----
def test_pending_moves_marked_written_or_refused():
    deps = FakeDeps()
    deps.pending = [
        {"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}},
        {"id": 2, "team_id": AZ, "kind": "PS_PROMOTE", "payload": {"player_id": pid(6)}},
        {"id": 3, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(3)}},   # not his team
        {"id": 4, "team_id": CHI, "kind": "IR_PLACE", "payload": {"player_id": pid(1)}},
        {"id": 5, "team_id": CHI, "kind": "DEPTH_CHART", "payload": {"slots": {"QB": [pid(1)], "WR": [pid(2), pid(3)]}}},
        {"id": 6, "team_id": CHI, "kind": "TRADE", "payload": {"other_team_id": AZ, "send": {"players": [pid(1)], "picks": []},
                                                               "receive": {"players": [pid(3)], "picks": []}}},
    ]
    p = plan("REGULAR_WEEK", deps=deps)
    status = {m["id"]: (m["status"], m["reason"]) for m in p["db"]["pending"]}
    assert status[1][0] == "WRITTEN" and status[4][0] == "WRITTEN" and status[5][0] == "WRITTEN"
    assert status[2] == ("REFUSED", "the save writer has no practice-squad promote / demote move yet")
    assert status[3][0] == "REFUSED" and "team 1" in status[3][1]
    assert status[6][0] == "WRITTEN"
    assert p["edits"]["release"] == [2]
    assert {(t["from_team_index"], t["to_team_index"], tuple(t["pgids"])) for t in p["edits"]["trade"]} == {(0, 1, (1,)), (1, 0, (3,))}
    sets = {e["pgid"]: e["set"] for e in p["edits"]["players"]}
    assert "IsInjuredReserve" not in sets.get(3, {})
    # JR's chart wins over the coach's for his team (the traded QB and the released WR drop out of it)
    chart0 = next(c for c in p["edits"]["depthCharts"] if c["team_index"] == 0)
    assert chart0["slots"] == {"QB": [], "WR": [3]}
    assert p["summary"]["pending_written"] == 4 and p["summary"]["pending_refused"] == 2


def test_record_written_updates_pending_and_stores_charts():
    deps = FakeDeps()
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}},
                    {"id": 2, "team_id": AZ, "kind": "PS_DEMOTE", "payload": {"player_id": pid(3)}}]
    p = plan("REGULAR_WEEK", deps=deps)
    cur = FakeCursor()
    lo.record_written(cur, p, 77)
    updates = [a for s, a in cur.sql if s.startswith("UPDATE franchise_pending_moves")]
    assert updates[0] == ("WRITTEN", None, 77, 1) and updates[1][0] == "REFUSED" and updates[1][2:] == (77, 2)
    assert any(s.startswith("DELETE FROM depth_charts") and a[3] == "MU_COACH" for s, a in cur.sql)
    # the stored record round-trips through JSON to the same writes (what --recover uses)
    rec = json.loads(json.dumps(lo.plan_record(p), default=str))
    cur2 = FakeCursor()
    lo.record_written(cur2, lo.plan_from_record(rec, json.loads(json.dumps(p["edits"]))), 77)
    assert [x for x in cur2.sql if not x[0].startswith("SELECT")] == [x for x in cur.sql if not x[0].startswith("SELECT")]


def test_engine_trades_plan_player_legs_and_skip_picks():
    deps = FakeDeps()
    deps.engine_props = [{"id": 41, "status": "APPROVED", "flags": ["talks:9", "auto_approved"], "refusals": 0,
                          "assets": [{"type": "PLAYER", "player_id": pid(1), "from": CHI, "to": AZ},
                                     {"type": "PLAYER", "player_id": pid(3), "from": AZ, "to": CHI},
                                     {"type": "PICK", "pick": (SEASON + 1, 2, AZ), "from": AZ, "to": CHI}]}]
    p = plan("REGULAR_WEEK", deps=deps)
    t = p["db"]["engine_trades"][0]
    assert t["id"] == 41 and t["status"] == "WRITTEN" and t["flags"] == ["talks:9", "auto_approved"]
    assert {(x["from_team_index"], x["to_team_index"], tuple(x["pgids"])) for x in p["edits"]["trade"]} == \
           {(0, 1, (1,)), (1, 0, (3,))}
    assert all(not x["picks"] for x in p["edits"]["trade"])          # picks are never written to the save
    assert p["summary"]["engine_trades"] == 1
    assert p["summary"]["engine_trades_written"] == 1 and p["summary"]["engine_trades_failed"] == 0
    assert p["summary"]["engine_trades_failed_ids"] == []


def test_engine_trade_with_a_claimed_player_is_refused_whole():
    deps = FakeDeps()
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    deps.engine_props = [{"id": 41, "status": "APPROVED", "flags": ["auto_approved"], "refusals": 0,
                          "assets": [{"type": "PLAYER", "player_id": pid(2), "from": CHI, "to": AZ},
                                     {"type": "PLAYER", "player_id": pid(3), "from": AZ, "to": CHI}]}]
    p = plan("REGULAR_WEEK", deps=deps)
    t = p["db"]["engine_trades"][0]
    assert t["status"] == "REFUSED" and "already has a release" in t["reason"]
    assert not any(x["pgids"] == [3] for x in p["edits"].get("trade") or [])   # the other leg wasn't planned either
    assert p["summary"]["engine_trades_failed"] == 1
    assert p["summary"]["engine_trades_failed_ids"] == [41]


def test_second_engine_trade_refusal_calls_could_not_make(monkeypatch):
    from engines import front_office as fo
    from engines import gm_talks
    monkeypatch.setattr(gm_talks, "publish_talk", lambda cur, nid: None)
    deps = FakeDeps()
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    deps.engine_props = [{"id": 41, "status": "APPROVED", "flags": ["auto_approved"], "refusals": 0,
                          "assets": [{"type": "PLAYER", "player_id": pid(2), "from": CHI, "to": AZ}]}]
    first = plan("REGULAR_WEEK", deps=deps)
    calls = []
    monkeypatch.setattr(fo, "could_not_make", lambda cur, proposal_id, reason=None: calls.append((proposal_id, reason)))
    lo.record_written(FakeCursor(), first, 77)
    assert calls == []                                    # one refusal: stays APPROVED, retried next write
    deps.engine_props[0]["refusals"] = 1                  # the previous WRITTEN run refused it
    second = plan("REGULAR_WEEK", deps=deps)
    lo.record_written(FakeCursor(), second, 78)
    assert calls == [(41, "the league office couldn't make it")]


def test_pending_trade_with_a_pick_is_written():
    deps = FakeDeps()
    deps.pending = [{"id": 7, "team_id": CHI, "kind": "TRADE",
                     "payload": {"proposal_id": 55, "other_team_id": AZ,
                                 "send": {"players": [pid(1)], "picks": [(SEASON + 1, 2, CHI)]},
                                 "receive": {"players": [pid(3)], "picks": []}}}]
    p = plan("REGULAR_WEEK", deps=deps)
    assert p["db"]["pending"][0]["status"] == "WRITTEN"
    assert any("trade #55: picks moved in Mu's books only" in w for w in p["warnings"])
    assert {(x["from_team_index"], x["to_team_index"], tuple(x["pgids"])) for x in p["edits"]["trade"]} == \
           {(0, 1, (1,)), (1, 0, (3,))}


def test_old_trade_payload_without_proposal_is_not_executed(monkeypatch):
    from engines import front_office as fo
    called = []
    monkeypatch.setattr(fo, "_execute", lambda *a, **kw: called.append(a))
    deps = FakeDeps()
    deps.pending = [{"id": 7, "team_id": CHI, "kind": "TRADE",
                     "payload": {"other_team_id": AZ,
                                 "send": {"players": [pid(1)], "picks": [(SEASON + 1, 2, CHI)]},
                                 "receive": {"players": [pid(3)], "picks": []}}}]
    p = plan("REGULAR_WEEK", deps=deps)
    assert p["db"]["pending"][0]["status"] == "WRITTEN"
    assert any("picks not moved (no proposal_id" in w for w in p["warnings"])
    lo.record_written(FakeCursor(), p, 77)
    assert called == []


def test_record_written_executes_written_trades_once_and_publishes(monkeypatch):
    from engines import front_office as fo
    from engines import gm_talks
    executed, published, done = [], [], set()

    def fake_proposal(cur, proposal_id):
        return {"id": proposal_id, "season": SEASON, "week": 3, "assets": [],
                "status": "EXECUTED" if proposal_id in done else "APPROVED"}

    def fake_execute(cur, proposal, executed_as):
        executed.append((proposal["id"], executed_as))
        done.add(proposal["id"])
        return {"warnings": [], "transactions": [], "contracts": []}

    monkeypatch.setattr(fo, "_proposal", fake_proposal)
    monkeypatch.setattr(fo, "_execute", fake_execute)
    monkeypatch.setattr(gm_talks, "publish_talk", lambda cur, nid: published.append(nid))
    deps = FakeDeps()
    deps.pending = [{"id": 7, "team_id": CHI, "kind": "TRADE",
                     "payload": {"proposal_id": 55, "other_team_id": AZ,
                                 "send": {"players": [pid(1)], "picks": []},
                                 "receive": {"players": [pid(3)], "picks": []}}}]
    deps.engine_props = [{"id": 41, "status": "APPROVED", "flags": ["talks:9", "auto_approved"], "refusals": 0,
                          "assets": [{"type": "PLAYER", "player_id": pid(2), "from": CHI, "to": AZ}]}]
    p = plan("REGULAR_WEEK", deps=deps)
    cur = FakeCursor()
    lo.record_written(cur, p, 77)
    lo.record_written(cur, p, 77)                       # already EXECUTED: never executed twice
    assert executed == [(55, "PROPOSED"), (41, "PROPOSED")]
    assert published == [9, 9]                          # publish_talk is idempotent
    updates = [a for s, a in cur.sql if s.startswith("UPDATE gm_trade_proposals SET status = 'EXECUTED'")]
    assert updates == [(55,), (41,)]


def test_accepted_offers_become_sign_and_extension():
    deps = FakeDeps(state=dict(STATE, spare_contract_rows=1))
    deps.offers = [{"id": 10, "kind": "FREE_AGENT", "player_id": pid(4), "team_id": CHI, "years": 2,
                    "total_value": 4_000_000, "bonus": 1_000_000, "guaranteed": 0},
                   {"id": 11, "kind": "EXTENSION", "player_id": pid(1), "team_id": CHI, "years": 3,
                    "total_value": 30_000_000, "bonus": 6_000_000, "guaranteed": 0}]
    p = plan("REGULAR_WEEK", deps=deps, state=deps.state)
    sign = p["edits"]["sign"][0]
    assert sign["pgid"] == 4 and sign["team_index"] == 0 and sign["contract"]["Length"] == 2
    assert sign["contract"]["SalaryYear0"] == 150 and sign["contract"]["BonusYear0"] == 50
    ext = next(e for e in p["edits"]["players"] if e["pgid"] == 1)
    assert ext["contract"]["Length"] == 3 and ext["contract"]["SalaryYear0"] == 800
    assert p["summary"]["written_offer_ids"] == [10, 11]


def test_accepted_offer_with_no_spare_contract_row_is_written():
    """P1-08: the writer fills an empty PlayerContract row, so a signing no longer waits on a release."""
    deps = FakeDeps()
    deps.offers = [{"id": 10, "kind": "FREE_AGENT", "player_id": pid(4), "team_id": CHI, "years": 1,
                    "total_value": 1_000_000, "bonus": 0, "guaranteed": 0}]
    p = plan("REGULAR_WEEK", deps=deps)
    assert [s["pgid"] for s in p["edits"]["sign"]] == [4]
    assert p["summary"]["written_offer_ids"] == [10] and p["deferred"] == []


def test_second_contract_for_a_player_is_refused_not_overwritten():
    deps = FakeDeps()
    new = {"length": 3, "current_year": 0, "salary_by_year": [5_000_000] * 3, "bonus_by_year": [1_000_000] * 3}
    deps.offers = [{"id": 11, "kind": "EXTENSION", "player_id": pid(1), "team_id": CHI, "years": 2,
                    "total_value": 10_000_000, "bonus": 0, "guaranteed": 0}]
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "EXTENSION", "payload": {"player_id": pid(1), "contract": new}}]
    p = plan("REGULAR_WEEK", deps=deps)
    ext = next(e for e in p["edits"]["players"] if e["pgid"] == 1)
    assert ext["contract"]["Length"] == 3                      # JR's, planned first
    assert p["summary"]["written_offer_ids"] == []


# ------------------------------------------------------------------ drift ----
def test_drift_entries_become_corrective_edits():
    deps = FakeDeps(state=dict(STATE, spare_contract_rows=0))
    stored_c = {"length": 3, "current_year": 1, "salary_by_year": [1_000_000, 2_000_000, 3_000_000],
                "bonus_by_year": [500_000, 500_000, 500_000]}
    deps.stored = {pid(2): (stored_c, SEASON - 1, CHI, "ACTIVE"),
                   pid(4): (stored_c, SEASON - 1, AZ, "ACTIVE")}
    deps.drift_report = {
        "rosters": [{"player_id": pid(3), "pgid": 3, "field": "team_id", "expected": None, "found": AZ},     # signed
                    {"player_id": pid(4), "pgid": 4, "field": "team_id", "expected": AZ, "found": None},     # cut
                    {"player_id": pid(1), "pgid": 1, "field": "team_id", "expected": AZ, "found": CHI}],     # traded
        "contracts": [{"player_id": pid(2), "pgid": 2, "field": "cap_hit", "expected": 2, "found": 3}],
        "depth_charts": [], "settings": [{"slider": "progression_by_age", "field": "int0", "expected": 0, "found": 5}],
        "ratings": [{"player_id": pid(2), "pgid": 2, "field": "Speed", "expected": 77, "found": 80},
                    {"player_id": pid(3), "pgid": 3, "field": "Speed", "expected": 77, "found": 80}],
    }
    p = plan("PRO_DAY", snapshot("OffSeason", "OffSeason", 7, 6), deps, state=deps.state)
    e = p["edits"]
    assert e["release"] == [3]                                    # Madden's signing undone
    assert e["sign"][0]["pgid"] == 4 and e["sign"][0]["team_index"] == 1   # the freed row signs the cut player back
    assert e["trade"] == [{"from_team_index": 0, "to_team_index": 1, "pgids": [1], "picks": []}]
    p2 = next(x for x in e["players"] if x["pgid"] == 2)
    assert p2["contract"]["CurrentYear"] == 2 and p2["contract"]["SalaryYear2"] == 300
    assert p2["set"]["SpeedRating"] == 77
    assert all(x["pgid"] != 3 for x in e["players"])               # released: his rating drift isn't written
    assert {"table": "int[]", "tableId": 9, "row": 0, "set": {"int0": 0}} in e["records"]
    assert p["summary"]["drift"]["rosters"] == 3


def test_rating_drift_left_to_progression_in_its_stages():
    deps = FakeDeps(drift_report={"ratings": [{"player_id": pid(2), "pgid": 2, "field": "Speed", "expected": 77,
                                               "found": 80}],
                                  "settings": [{"slider": "progression_by_age"}]})
    p = plan("REGULAR_WEEK", deps=deps)
    assert all("SpeedRating" not in x["set"] for x in p["edits"].get("players", []))
    assert p["summary"]["drift_ratings_left_to_progression"] == 1
    assert p["edits"]["records"] == [SLIDERS]           # drift's slider reset and the engine's agree: one record


def test_drift_keeps_rookies_madden_signed_in_the_offseason():
    snap = snapshot("OffSeason", "OffSeason", 15, 10, players=[player(1, 0), player(40, 0, years_pro=0)])
    deps = FakeDeps(snap, drift_report={"rosters": [{"player_id": pid(40), "pgid": 40, "field": "team_id",
                                                     "expected": None, "found": CHI}]})
    p = plan("UDFA", snap, deps)
    assert "release" not in p["edits"] and p["summary"]["drift_rookies_kept"] == 1


# -------------------------------------------------------------------- cap ----
def test_cap_fields_written_for_moved_players_and_teams():
    deps = FakeDeps(state=dict(STATE, spare_contract_rows=0))
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}},
                    {"id": 2, "team_id": AZ, "kind": "SIGN",
                     "payload": {"player_id": pid(4), "contract": {"length": 1, "salary_by_year": [2_000_000],
                                                                    "bonus_by_year": [0]}}}]
    p = plan("PRO_DAY", snapshot("OffSeason", "OffSeason", 7, 6), deps, state=deps.state)
    e = p["edits"]
    assert next(x for x in e["players"] if x["pgid"] == 4)["set"]["PLYR_CAPSALARY"] == 200
    rec = {r["row"]: r["set"] for r in e["records"] if r["table"] == "Team"}
    # CHI released player 2: current-year hit 250 - dead money (bonus years 1..3 = 150) frees 100; base 200 off
    assert rec[1] == {"TEAM_SALARY": 10000 - 200, "SalCapCapRoom": 150000 + 100, "SalCapSpendingMoney": 150000 + 100,
                      "SalCapNextYearCapRoom": 150000 + 350}
    assert rec[2] == {"TEAM_SALARY": 9000 + 200, "SalCapCapRoom": 160000 - 200, "SalCapSpendingMoney": 160000 - 200}
    assert all(r["tableId"] == 5934 for r in e["records"] if r["table"] == "Team")


def test_cap_records_skipped_without_league_state():
    deps = FakeDeps()
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    p = plan("PRO_DAY", snapshot("OffSeason", "OffSeason", 7, 6), deps, state=None)
    assert "records" not in p["edits"] and any("cap fields" in w for w in p["warnings"])


# -------------------------------------------------------------------- run ----
@pytest.fixture
def save(tmp_path):
    path = tmp_path / "CAREER-TEST"
    path.write_bytes(b"ORIGINAL SAVE")
    return str(path)


def _run(save, deps, snap=None, cur=None, **kw):
    cur = cur or FakeCursor()
    snap = snap or deps.snap or snapshot()
    deps.snap, deps.cur = snap, cur
    deps.seen["save"] = save
    out = []
    run_id = lo.run(cur, snap, save, kw.pop("stage", "auto"), deps=deps, out=out.append, **kw)
    return cur, run_id, out


def _release_deps(**kw):
    deps = FakeDeps(snapshot(), prep_doc={"players": [{"pgid": 1, "set": {"SpeedRating": 81}}], "records": []}, **kw)
    deps.pending = [{"id": 1, "team_id": CHI, "kind": "RELEASE", "payload": {"player_id": pid(2)}}]
    return deps


def test_prepared_operation_committed_before_the_save_is_replaced(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    deps = _release_deps()
    cur, run_id, out = _run(save, deps)
    at_write = deps.seen["at_write"]
    assert at_write["save_bytes"] == b"ORIGINAL SAVE"                       # nothing replaced yet ...
    row = at_write["committed"]["runs"][run_id]
    op = row["summary"]["operation"]
    assert row["status"] == "PLANNED" and op["phase"] == "PREPARED"        # ... but the operation is durable
    assert at_write["committed"]["events"] == {"ev-1": "PREPARED"}          # with the PREPARED events
    import hashlib
    assert op["input_sha256"] == hashlib.sha256(b"ORIGINAL SAVE").hexdigest()
    assert op["prepared"]["document"] == deps.prep_doc and op["prepared"]["event_ids"] == ["ev-1"]
    assert op["prepared"]["source_fingerprint"] == op["input_sha256"]
    assert row["edits"]["release"] == [2] and op["combined_hash"] == lo.canonical_hash(row["edits"])
    assert op["operation_id"] and op["save_path"] == os.path.abspath(save)
    # then: replaced, acknowledged with the output's fingerprint, WRITTEN
    assert open(save, "rb").read() == b"WRITTEN COPY"
    final = cur.run_row(run_id)
    assert final["status"] == "WRITTEN" and final["summary"]["operation"]["phase"] == "ACKNOWLEDGED"
    assert cur.committed["events"] == {"ev-1": "APPLIED"}
    assert any(s.startswith("UPDATE franchise_pending_moves") for s, _ in cur.sql)
    assert json.loads(out[-1])["status"] == "WRITTEN"
    write = next(c for c in deps.calls if c[0] == "write")
    assert write[1] == save and write[2] != save and os.path.basename(write[2]) == os.path.basename(save)


def test_acknowledge_only_after_a_verified_replacement(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    import hashlib
    deps = _release_deps()
    cur, run_id, _ = _run(save, deps)
    assert len(deps.acks) == 1
    ack = deps.acks[0]
    assert ack["save_bytes"] == b"WRITTEN COPY"                              # the save was already replaced
    assert ack["output_fingerprint"] == hashlib.sha256(b"WRITTEN COPY").hexdigest()
    op = ack["committed"]["runs"][run_id]["summary"]["operation"]
    assert op["phase"] == "REPLACED" and op["output_sha256"] == ack["output_fingerprint"]   # recorded first
    assert op["candidate_sha256"] == op["output_sha256"]
    # the envelope acknowledged is the one prepared: same document, hash and fingerprint
    assert ack["prepared"]["edits_hash"] == lo.canonical_hash(deps.prep_doc)
    assert ack["prepared"]["source_fingerprint"] == hashlib.sha256(b"ORIGINAL SAVE").hexdigest()


def test_failed_read_back_never_acknowledges_and_leaves_the_save(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    deps = _release_deps()
    deps.read_back = snapshot()           # the written copy still has him on the roster
    cur, run_id, out = _run(save, deps)
    assert open(save, "rb").read() == b"ORIGINAL SAVE" and deps.acks == []
    row = cur.run_row(run_id)
    assert row["status"] == "FAILED" and "read-back" in row["error"]
    assert row["summary"]["operation"]["phase"] == "FAILED"
    assert cur.committed["events"] == {"ev-1": "PREPARED"}                 # proposals kept, nothing awarded
    assert not any(s.startswith("UPDATE franchise_pending_moves") for s, _ in cur.sql)
    # the writer's own read-back failing does the same
    deps = _release_deps(write_ok=False)
    cur, run_id, _ = _run(save, deps)
    assert open(save, "rb").read() == b"ORIGINAL SAVE" and cur.run_row(run_id)["status"] == "FAILED"
    assert deps.acks == []


def test_run_refuses_while_madden_runs_without_confirm(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    deps = FakeDeps(snapshot(), running=True)
    with pytest.raises(lo.FranchiseOpenError):
        _run(save, deps)
    assert open(save, "rb").read() == b"ORIGINAL SAVE"
    assert not any(c[0] in ("write", "weekly") for c in deps.calls)
    # with the flag, or the watcher's env var, it writes
    deps = _release_deps(running=True)
    _run(save, deps, confirm_franchise_closed=True)
    assert open(save, "rb").read() == b"WRITTEN COPY"
    monkeypatch.setenv(lo.FRANCHISE_CLOSED_ENV, "1")
    assert lo.franchise_closed_confirmed(False)


def test_madden_starting_mid_run_blocks_the_replacement(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    deps = _release_deps()
    seq = iter([False, True])
    deps.madden_running = lambda: next(seq)
    cur, run_id, _ = _run(save, deps)
    assert open(save, "rb").read() == b"ORIGINAL SAVE" and deps.acks == []
    assert cur.run_row(run_id)["status"] == "FAILED"


def test_run_planning_errors_write_nothing(save):
    deps = FakeDeps(snapshot())

    def boom(*a, **k):
        raise RuntimeError("engine down")

    deps.previous_injuries = boom
    cur, run_id, _ = _run(save, deps)
    assert open(save, "rb").read() == b"ORIGINAL SAVE"
    row = cur.run_row(run_id)
    assert row["status"] == "FAILED" and "engine down" in row["error"]
    assert not any(c[0] == "write" for c in deps.calls) and deps.acks == []
    assert cur.committed["events"] == {}                                    # preparation rolled back


def test_run_missing_hook_fails_the_run(save, monkeypatch):
    from engines import progression
    monkeypatch.delattr(progression, "in_season_checkpoint")
    deps = FakeDeps(snapshot())
    deps.progression_weekly = lambda *a: lo.Deps.progression_weekly(deps, *a)
    with pytest.raises(lo.ProgressionIntegrationError):
        cur = FakeCursor()
        _run(save, deps, cur=cur)
    assert open(save, "rb").read() == b"ORIGINAL SAVE"
    assert cur.run_row(1)["status"] == "FAILED" and "in_season_checkpoint" in cur.run_row(1)["error"]


def test_dry_run_never_acknowledges(save):
    deps = _release_deps(running=True)
    cur, run_id, out = _run(save, deps, dry_run=True)
    assert open(save, "rb").read() == b"ORIGINAL SAVE"
    assert not any(c[0] == "write" for c in deps.calls) and deps.acks == []
    assert ("explain", ("ev-1",)) in deps.calls                            # public proposal reports for the log
    assert cur.committed["events"] == {}                                    # preparation rolled back
    assert [r[4] for r in cur.runs()] == ["DRY_RUN"]
    assert not any(s.startswith("UPDATE franchise_pending_moves") for s, _ in cur.sql)
    assert cur.connection.rollbacks == 1
    printed = json.loads(out[-1])
    assert printed["stage"] == "REGULAR_WEEK" and printed["edits"]["release"] == [2]
    assert printed["progression_proposals"] == [{"status": "PROPOSED", "event_id": "ev-1"}]
    assert printed["progression_edits_hash"] == lo.canonical_hash(deps.prep_doc)


def test_verify_read_back_catches_mismatches():
    snap = snapshot()
    edits = {"release": [2], "sign": [{"pgid": 4, "team_index": 0, "list": "Roster"}],
             "players": [{"pgid": 1, "set": {"ConfidenceRating": 50, "SpeedRating": 90}}]}
    bad = lo.verify_read_back(edits, snap)
    assert any("pgid 2" in b for b in bad) and any("pgid 4" in b for b in bad)
    assert any("SpeedRating" in b for b in bad) and not any("ConfidenceRating" in b for b in bad)


# --------------------------------------------------------------- recovery ----
def _crashed_after_replacement(save, monkeypatch):
    """A run whose acknowledgement failed after the save was replaced: (cur, run_id, deps)."""
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    deps = _release_deps(ack_error=RuntimeError("database went away"))
    cur = FakeCursor()
    with pytest.raises(lo.RecoveryError, match="--recover"):
        _run(save, deps, cur=cur)
    assert open(save, "rb").read() == b"WRITTEN COPY"
    row = cur.run_row(1)
    assert row["status"] == "PLANNED" and row["summary"]["operation"]["phase"] == "REPLACED"
    assert cur.committed["events"] == {"ev-1": "PREPARED"}
    return cur, 1, deps


def test_recovery_acknowledges_the_original_events(save, monkeypatch):
    import hashlib
    cur, run_id, first = _crashed_after_replacement(save, monkeypatch)
    stored = cur.run_row(run_id)["summary"]["operation"]["prepared"]
    deps = FakeDeps(snapshot())
    deps.cur, deps.seen["save"] = cur, save
    deps.written_edits = cur.run_row(run_id)["edits"]
    out = []
    assert lo.recover(cur, run_id, deps=deps, out=out.append) == "ACKNOWLEDGED"
    assert len(deps.acks) == 1 and deps.acks[0]["prepared"] == stored            # the ORIGINAL envelope
    assert deps.acks[0]["output_fingerprint"] == hashlib.sha256(b"WRITTEN COPY").hexdigest()
    assert not any(c[0] in ("weekly", "offseason", "write") for c in deps.calls)   # nothing recalculated or written
    row = cur.run_row(run_id)
    assert row["status"] == "WRITTEN" and row["summary"]["operation"]["phase"] == "ACKNOWLEDGED"
    assert row["summary"]["operation"]["recovered"] is True
    assert cur.committed["events"] == {"ev-1": "APPLIED"}
    assert any(s.startswith("UPDATE franchise_pending_moves") for s, _ in cur.sql)
    # a second recovery does nothing
    assert lo.recover(cur, run_id, deps=deps, out=out.append) == "ALREADY_WRITTEN" and len(deps.acks) == 1


def test_recovery_refuses_when_the_save_hash_does_not_match(save, monkeypatch):
    cur, run_id, _ = _crashed_after_replacement(save, monkeypatch)
    with open(save, "wb") as fh:
        fh.write(b"MADDEN ADVANCED AGAIN")
    deps = FakeDeps(snapshot())
    with pytest.raises(lo.RecoveryError, match="refusing"):
        lo.recover(cur, run_id, deps=deps, out=lambda s: None)
    assert deps.acks == [] and cur.run_row(run_id)["status"] == "PLANNED"
    assert cur.committed["events"] == {"ev-1": "PREPARED"}


def test_recovery_with_only_a_candidate_checks_whether_the_save_was_replaced(save, monkeypatch):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)

    def crash(src, dest):
        raise OSError("power cut")

    monkeypatch.setattr(lo, "replace_save", crash)
    deps = _release_deps()
    cur = FakeCursor()
    with pytest.raises(OSError):
        _run(save, deps, cur=cur)
    row = cur.run_row(1)
    assert row["status"] == "PLANNED" and row["summary"]["operation"]["phase"] == "CANDIDATE_VERIFIED"
    assert deps.acks == [] and open(save, "rb").read() == b"ORIGINAL SAVE"
    rdeps = FakeDeps(snapshot())
    assert lo.recover(cur, 1, deps=rdeps, out=lambda s: None) == "NOT_REPLACED"
    assert rdeps.acks == [] and cur.run_row(1)["status"] == "FAILED"
    assert cur.committed["events"] == {"ev-1": "PREPARED"}


def test_recovery_after_replacement_before_the_record(save, monkeypatch):
    """The replacement happened but the process died before REPLACED was committed: the candidate's sha256 proves it."""
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    real = lo.replace_save

    def replace_then_die(src, dest):
        real(src, dest)
        raise KeyboardInterrupt

    monkeypatch.setattr(lo, "replace_save", replace_then_die)
    deps = _release_deps()
    cur = FakeCursor()
    with pytest.raises(KeyboardInterrupt):
        _run(save, deps, cur=cur)
    assert cur.run_row(1)["summary"]["operation"]["phase"] == "CANDIDATE_VERIFIED"
    rdeps = FakeDeps(snapshot())
    rdeps.cur, rdeps.seen["save"], rdeps.written_edits = cur, save, cur.run_row(1)["edits"]
    assert lo.recover(cur, 1, deps=rdeps, out=lambda s: None) == "ACKNOWLEDGED"
    assert len(rdeps.acks) == 1 and cur.run_row(1)["status"] == "WRITTEN"


# ------------------------------------------------------------------ setup ----
def _neutral(players=None, **kw):
    return snapshot(players=players or [player(1, 0), player(2, 0), player(4, 32, status="FreeAgent")], **kw)


def test_setup_bootstraps_progression_on_the_neutral_save(tmp_path):
    import hashlib
    out_path = str(tmp_path / "CAREER-NEW")
    deps = FakeDeps(_neutral(), read_back=_neutral())
    cur = FakeCursor()
    report = lo.setup(str(tmp_path / "CAREER-OLD"), out_path, cur, deps)
    sha = hashlib.sha256(b"NEUTRAL SAVE").hexdigest()
    assert [c for c in deps.calls if c[0] == "bootstrap"] == [("bootstrap", SEASON, sha)]
    assert report["progression_bootstrap"] == {"application_season": SEASON, "stage": "REGULAR_WEEK",
                                               "source_sha256": sha, "players_bootstrapped": 3}
    assert cur.connection.commits == 1 and os.path.exists(out_path)
    # at the preseason the application year is the new one
    deps = FakeDeps(_neutral(), read_back=_neutral(stage="PreSeason", week_type="PreSeason", week=0, year=SEASON + 1))
    lo.setup("old", str(tmp_path / "CAREER-NEW2"), FakeCursor(), deps)
    assert [c[1] for c in deps.calls if c[0] == "bootstrap"] == [SEASON + 1]


def test_setup_refuses_a_save_that_is_not_neutral(tmp_path):
    out_path = str(tmp_path / "CAREER-NEW")
    deps = FakeDeps(_neutral(), read_back=_neutral([player(1, 0, confidence=70), player(2, 0)]))
    cur = FakeCursor()
    with pytest.raises(RuntimeError, match="ConfidenceRating 50"):
        lo.setup("old", out_path, cur, deps)
    assert not any(c[0] == "bootstrap" for c in deps.calls) and not os.path.exists(out_path)
    assert cur.connection.commits == 0
    deps = FakeDeps(_neutral(), read_back=_neutral(), form_rows=12)          # Mu form already applied
    with pytest.raises(RuntimeError, match="known-neutral"):
        lo.setup("old", out_path, FakeCursor(), deps)
    assert not any(c[0] == "bootstrap" for c in deps.calls) and not os.path.exists(out_path)


# -------------------------------------------------------------------- CLI ----
def test_cli_help_and_setup_subcommand(capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        lo.main(["--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    assert "--snapshot" in text and "--recover" in text
    existing = tmp_path / "out"
    existing.write_text("x")
    assert lo.main(["setup", "--save", str(tmp_path / "in"), "--out", str(existing)]) == 1
    assert "exists" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        lo.main(["--snapshot", "s.json", "--save", "x", "--stage", "BOGUS"])
    with pytest.raises(SystemExit):
        lo.main(["--save", "x"])                       # no snapshot and no --recover


# ------------------------------------------------------------- salary cap (U3) ----
def test_cap_room_range_widened():
    assert lo.CAP_ROOM_RANGE == (-40_000, 484_287)
    assert lo._clamp(484_287, lo.CAP_ROOM_RANGE) == 484_287
    assert lo._clamp(500_000, lo.CAP_ROOM_RANGE) == 484_287
    assert lo._clamp(-40_000, lo.CAP_ROOM_RANGE) == -40_000
    assert lo._clamp(-50_000, lo.CAP_ROOM_RANGE) == -40_000

    # _cap_records writes above 200,000 without clamping down to 200,000
    p = lo.Plan(snapshot(), "REGULAR_WEEK", SEASON, 3, None, "seed", {0: CHI}, {1: 1},
                state={"team_table_id": 5934, "teams": [{"team_index": 0, "row": 1, "SalCapCapRoom": 200_000}]})
    p._cap(0, 5_000, 0, 0)
    records = p._cap_records()
    assert len(records) == 1
    assert records[0]["set"]["SalCapCapRoom"] == 205_000


def test_league_cap_helpers():
    # state takes precedence
    assert lo.league_cap_units({"salary_cap": 30120}, {"salary_cap": {"team_salary_cap": 25540}}) == 30120
    assert lo.league_cap({"salary_cap": 30120}) == 301_200_000
    # snapshot fallback
    assert lo.league_cap_units(None, {"salary_cap": {"team_salary_cap": 28000}}) == 28000
    assert lo.league_cap(None, {"salary_cap": {"team_salary_cap": 28000}}) == 280_000_000
    # reference fallback
    assert lo.league_cap_units(None, None) is None
    assert lo.league_cap(None, None) == ce.CBA["REFERENCE_CAP"]


def test_step_salary_cap_at_offseason_start():
    # 32 teams in snapshot
    snap_teams = [{"team_index": i, "short_name": f"T{i}"} for i in range(32)]
    snap = snapshot("OffSeason", "OffSeason", 0, 1, players=[
        player(1, 0, pos="QB", ovr=85),
        player(2, 0, status="Signed", years_pro=1),  # PS player Madden promoted to Signed
    ])
    snap["teams"] = snap_teams
    snap["franchise"] = {"league_id": 42}
    snap["salary_cap"] = {"table_id": 5186, "team_salary_cap": 30120}

    state_teams = [{"team_index": i, "row": i, "TEAM_SALARY": 10000, "SalCapCapRoom": 160000,
                    "SalCapNextYearCapRoom": 160000, "SalCapSpendingMoney": 160000} for i in range(32)]
    state = {"team_table_id": 5934, "salary_info_table_id": 5186, "salary_cap": 30120, "spare_contract_rows": 5,
             "teams": state_teams}

    deps = FakeDeps(snap, ps_ids={pid(2)}, state=state)
    deps.caps = {SEASON: 301_200_000}
    deps.team_ids = lambda cur, snapshot: {i: 100 + i for i in range(32)}

    p = plan("OFFSEASON_START", snap, deps, state=state)

    seed_str = ce.cap_seed(42)
    expected_target_cap = ce.next_cap(301_200_000, SEASON + 1, seed_str)
    expected_target_units = expected_target_cap // ce.MONEY_UNIT
    expected_delta = expected_target_units - 30120

    # 1. SalaryInfo record written with -12000 offset
    sal_rec = next(r for r in p["edits"]["records"] if r.get("table") == "SalaryInfo")
    assert sal_rec["tableId"] == 5186
    assert sal_rec["row"] == 0
    assert sal_rec["set"]["TeamSalaryCap"] == expected_target_units - 12000

    # 2. Room deltas applied to all 32 teams
    team_recs = [r for r in p["edits"]["records"] if r.get("table") == "Team"]
    assert len(team_recs) == 32
    for r in team_recs:
        assert r["set"]["SalCapCapRoom"] == 160000 + expected_delta
        if r["row"] == 0:
            assert r["set"]["SalCapNextYearCapRoom"] != 160000 + expected_delta
        else:
            assert r["set"]["SalCapNextYearCapRoom"] == 160000 + expected_delta

    # 3. Counts and summary recorded
    assert p["summary"]["salary_cap_updated"] == 1
    sc_summary = p["summary"]["salary_cap"]
    assert sc_summary["season"] == SEASON + 1
    assert sc_summary["prev_cap"] == 301_200_000
    assert sc_summary["cap"] == expected_target_cap
    assert sc_summary["madden_cap"] == 301_200_000
    assert sc_summary["seed"] == seed_str
    assert sc_summary["growth_pct"] == round((expected_target_cap - 301_200_000) / 301_200_000 * 100, 2)

    # 4. Promoted PS player priced on new cap
    sets = {e["pgid"]: e for e in p["edits"]["players"]}
    block = sets[2]["contract"]
    expected_salary = int(ce.minimum_salary(2, expected_target_cap) / ce.MONEY_UNIT + 0.5)
    assert block["SalaryYear1"] == expected_salary

    # 5. Idempotent on re-run: when save already has the target cap
    idempotent_state = dict(state, salary_cap=expected_target_units)
    idempotent_snap = copy.deepcopy(snap)
    idempotent_snap["salary_cap"]["team_salary_cap"] = expected_target_units
    p_rerun = plan("OFFSEASON_START", idempotent_snap, deps, state=idempotent_state)
    assert not any(r.get("table") == "SalaryInfo" for r in p_rerun["edits"]["records"])
    assert p_rerun["summary"].get("salary_cap_updated", 0) == 0
    assert p_rerun["summary"]["salary_cap"]["cap"] == expected_target_cap


def test_drift_salary_cap_write_back():
    snap = snapshot("RegularSeason", "RegularSeason", 3, None)
    snap["salary_cap"] = {"table_id": 5186, "team_salary_cap": 25540}
    deps = FakeDeps(snap)
    deps.drift_report = {
        "salary_cap": [{"season": SEASON, "field": "team_salary_cap", "expected": 260_000_000, "found": 255_400_000}]
    }

    # On non-OFFSEASON_START stage (e.g. REGULAR_WEEK), cap drift is restored
    p = plan("REGULAR_WEEK", snap, deps, state=deps.state)
    sal_rec = next(r for r in p["edits"]["records"] if r.get("table") == "SalaryInfo")
    assert sal_rec["set"]["TeamSalaryCap"] == (260_000_000 // 10_000) - 12000
    assert p["summary"]["drift_salary_cap"] == 1
    # Check cap shift: (260M - 255.4M) / 10k = 460 units
    expected_shift = 460
    team_recs = [r for r in p["edits"]["records"] if r.get("table") == "Team"]
    assert any(r["row"] == 2 and r["set"]["SalCapCapRoom"] == 160000 + expected_shift for r in team_recs)

    # At OFFSEASON_START, step_drift ignores salary_cap drift (left to step_salary_cap)
    plan_off = lo.Plan(snap, "OFFSEASON_START", SEASON, 0, None, "seed", {0: CHI, 1: AZ}, {1: 1}, state=deps.state)
    lo.step_drift(plan_off, deps.drift_report, {})
    assert not any(r.get("table") == "SalaryInfo" for r in plan_off.records)
    assert plan_off.counts.get("drift_salary_cap", 0) == 0


def test_verify_read_back_salary_cap():
    edits = {"records": [{"table": "SalaryInfo", "tableId": 5186, "row": 0, "set": {"TeamSalaryCap": 20000}}]}

    # Match: raw matches written_raw, team_salary_cap == written_raw + 12000
    snap_good = {"salary_cap": {"raw_team_salary_cap": 20000, "team_salary_cap": 32000}}
    assert lo.verify_read_back(edits, snap_good) == []

    # Raw mismatch
    snap_bad_raw = {"salary_cap": {"raw_team_salary_cap": 18120, "team_salary_cap": 32000}}
    bad_raw = lo.verify_read_back(edits, snap_bad_raw)
    assert any("raw_team_salary_cap: wrote 20000, read 18120" in b for b in bad_raw)

    # Decoded units mismatch
    snap_bad_units = {"salary_cap": {"raw_team_salary_cap": 20000, "team_salary_cap": 30120}}
    bad_units = lo.verify_read_back(edits, snap_bad_units)
    assert any("team_salary_cap: expected 32000, read 30120" in b for b in bad_units)

    # Snapshot without salary_cap
    assert lo.verify_read_back(edits, {}) == []


def test_apply_db_sync_team_caps():
    cur = FakeCursor()
    deps = FakeDeps()

    # When db has salary_cap info
    plan_data = {
        "db": {"salary_cap": {"season": 2027, "cap": 301_200_000, "madden_cap": 280_000_000}},
        "team_ids": {0: CHI, 1: AZ},
        # Team 0 has a move reducing cap room by 80 units beyond cap change; Team 1 has only the cap change (2120 units)
        "cap": {0: [2120 - 80, 0, 0], 1: [2120, 0, 0]},
    }
    lo._apply_db(cur, plan_data, deps)

    # Check deps.sync_team_caps was executed
    sync_sqls = [s for s, a in cur.sql if "UPDATE team_cap_seasons" in s and "SET cap = %s" in s]
    assert len(sync_sqls) == 1
    # Check move delta update was executed for team 0 (move_room = -80 units = -$800k)
    move_sqls = [a for s, a in cur.sql if "UPDATE team_cap_seasons" in s and "SET cap_space = cap_space + %s" in s]
    assert len(move_sqls) == 1
    delta_dollars, neg_delta, team_id, season = move_sqls[0]
    assert delta_dollars == -800_000
    assert neg_delta == -800_000
    assert team_id == CHI
    assert season == 2027

    # A trade record_written executes moves its own team_cap_seasons rows (trade_moves): its room is left out here
    cur_trade = FakeCursor()
    trade_plan = {
        "db": {"salary_cap": {"season": 2027, "cap": 301_200_000, "madden_cap": 280_000_000}},
        "team_ids": {0: CHI, 1: AZ},
        "cap": {0: [2120 + 30, 0, 0], 1: [2120 - 30, 0, 0]},
        "trade_cap_db": {0: [30, 0, 0], 1: [-30, 0, 0]},
    }
    lo._apply_db(cur_trade, trade_plan, deps)
    assert not [a for s, a in cur_trade.sql if "SET cap_space = cap_space + %s" in s]

    # When db has no salary_cap info
    cur_noop = FakeCursor()
    lo._apply_db(cur_noop, {"db": {}}, deps)
    assert not any("team_cap_seasons" in s for s, _ in cur_noop.sql)


# ------------------------------------------------------ retirement window ----
# RETIREMENT_WINDOW_PLAN U3: the decision stage, the window at later stops, the roster image on every run.
PROOF = {"kind": "retirement-authority-proof-v1", "version": "retirement-authority-v1", "fixture_id": "g4-test",
         "proof_save_hash": "sha:proof", "schema_version": 5,
         "stages": ["OFFSEASON_START", "RESIGN", "FREE_AGENCY"], "exclusive_owner": True,
         "actions": {"KEEP_NATIVE_RETIREE": {"proven": True, "proof_id": "p1",
                                             "fields": ["ContractStatus", "TeamIndex"]}},
         "approved_by": "JR", "approved_on": "2099-01-01"}


def window_deps(snap, decision=None, prior=None, prior_stop="OFFSEASON_START", restores=None, proof=None,
                decided_extra=None, **kw):
    deps = FakeDeps(snap, **kw)
    decided = dict(decided_extra or {})
    decided.update((decision or {}).get("decided") or {})
    inputs = {"decision": decision, "decided": decided, "window_restores": restores or {},
              "prior_image": prior, "prior_stop": prior_stop, "prior_run_id": 1}
    deps.retirement_window_inputs = lambda cur, season: inputs
    deps.retirement_authority = lambda: proof
    deps.player_names = lambda cur, ids: {i: f"Name {i}" for i in ids}
    return deps


def resign_snap(players):
    return snapshot("OffSeason", "OffSeason", 1, 1, players=players)


DECISION = {"run_id": 1, "stop": "OFFSEASON_START",
            "decided": {"8": {"player_id": pid(8), "keep": True, "reason": "kept"},
                        "9": {"player_id": pid(9), "keep": False, "reason": "retired"}}}
PRIOR = {"1": ["Signed", 0], "2": ["Signed", 0], "3": ["Signed", 1], "8": ["FreeAgent", 32], "9": ["Retired", None],
         "10": ["Signed", 1]}


def test_decision_stage_is_offseason_start_and_summary_holds_decided():
    snap = snapshot("OffSeason", "OffSeason", 0, 1, players=[
        player(1, 0), player(8, 32, status="Retired", age=30, ovr=80), player(9, 32, status="Retired", age=37, ovr=70)])
    p = plan("OFFSEASON_START", snap, window_deps(snap))
    w = p["summary"]["retirement_window"]
    assert w["role"] == "decision" and w["stop"] == "OFFSEASON_START" and w["version"] == "retirement-window-v1"
    assert w["decided"]["8"]["keep"] is True and w["decided"]["9"]["keep"] is False
    # legacy writer without a proof: the keep is a free agent, and the image says so
    assert w["roster_image"]["8"] == ["FreeAgent", 32] and w["roster_image"]["9"] == ["Retired", 32]
    assert {e["pgid"]: e["set"] for e in p["edits"]["players"]}[8] == {"ContractStatus": "FreeAgent", "TeamIndex": 32}


def test_resign_is_the_decision_stage_without_an_offseason_start_run():
    snap = resign_snap([player(1, 0), player(8, 32, status="Retired", age=30, ovr=80)])
    p = plan("RESIGN", snap, window_deps(snap, decision=None, prior=PRIOR, prior_stop="PLAYOFF_WEEK"))
    w = p["summary"]["retirement_window"]
    assert w["role"] == "decision" and w["stop"] == "RESIGN" and set(w["decided"]) == {"8"}
    assert [r["player_id"] for r in p["db"]["retirements"]] == [pid(8)]


def test_window_reports_a_late_native_without_a_proof():
    snap = resign_snap([player(1, 0), player(2, 0, status="Retired"), player(3, 1),
                        player(9, 32, status="Retired"), player(10, 1)])
    p = plan("RESIGN", snap, window_deps(snap, decision=DECISION, prior=PRIOR))
    w = p["summary"]["retirement_window"]
    assert w["role"] == "window" and w["decision_stop"] == "OFFSEASON_START"
    assert w["restored"] == [] and [b["pgid"] for b in w["blocked"]] == [2]
    assert w["blocked"][0]["reasons"] == ["authority_missing"] and w["decided_retired"] == [9]
    assert "players" not in p["edits"] or all(e["pgid"] != 2 for e in p["edits"]["players"])
    assert {r["player_id"]: (r["mu_keep"], r["reason"]) for r in p["db"]["retirements"]} == {
        pid(2): (False, "window:blocked:RESIGN:authority_missing")}
    assert any("not restored" in x for x in p["warnings"])


def test_window_restores_a_late_native_with_a_proof_and_keeps_him_out_of_free_agency():
    snap = snapshot("OffSeason", "OffSeason", 3, 2, players=[
        player(1, 0), player(4, 32, status="Retired", ovr=78), player(5, 32, status="FreeAgent", ovr=60)])
    c = {"length": 2, "current_year": 0, "salary_by_year": [3_000_000, 3_000_000], "bonus_by_year": [0, 0]}
    offers = [{"team_id": CHI, "player_id": pid(4), "contract": c, "offer_id": 55}]
    decisions = [{"player_id": pid(4), "team_id": CHI, "offer_index": 0, "status": "ACCEPT"}]
    prior = {"1": ["Signed", 0], "4": ["FreeAgent", 32], "5": ["FreeAgent", 32]}
    deps = window_deps(snap, decision=DECISION, prior=prior, prior_stop="FREE_AGENCY:1", proof=PROOF, cpu=offers,
                       decisions=decisions)
    deps.signings_from_decisions = lambda d, o: lo.te.signings_from_decisions(d, o)
    p = plan("FREE_AGENCY", snap, deps, state=dict(STATE, spare_contract_rows=1))
    w = p["summary"]["retirement_window"]
    assert w["stop"] == "FREE_AGENCY:2" and [r["pgid"] for r in w["restored"]] == [4]
    assert {e["pgid"]: e["set"] for e in p["edits"]["players"]}[4] == {"ContractStatus": "FreeAgent", "TeamIndex": 32}
    assert not p["edits"].get("sign")                          # not signed in the same write
    assert w["roster_image"]["4"] == ["FreeAgent", 32]
    assert p["summary"]["window_restored"] == 1
    assert {r["player_id"]: r["reason"] for r in p["db"]["retirements"]} == {pid(4): "window:restored:FREE_AGENCY"}


def test_window_restores_baseline_team_and_flags_user_team():
    snap = resign_snap([player(1, 0), player(2, 0, status="Retired"), player(3, 1)])
    p = plan("RESIGN", snap, window_deps(snap, decision=DECISION, prior=PRIOR, proof=PROOF, users={CHI}))
    w = p["summary"]["retirement_window"]
    assert w["restored"][0]["baseline"] == {"status": "Signed", "team_index": 0} and w["restored"][0]["user_team"]
    assert {e["pgid"]: e["set"] for e in p["edits"]["players"]}[2] == {"ContractStatus": "Signed", "TeamIndex": 0}
    assert w["roster_image"]["2"] == ["Signed", 0]


def test_window_conflicts_vanished_and_reason_prefixes():
    snap = resign_snap([player(1, 0), player(2, 0), player(8, 32, status="Retired")])
    deps = window_deps(snap, decision=DECISION, prior=PRIOR, proof=PROOF, restores={"8": 1})
    deps.pgid_map = lambda cur: {pid(g): g for g in (1, 2, 3, 8, 9, 10)}   # the vanished are still in players
    p = plan("RESIGN", snap, deps)
    w = p["summary"]["retirement_window"]
    assert [c["kind"] for c in w["conflicts"]] == ["RE_RETIRED_AGAIN"]
    assert {v["pgid"]: v["kind"] for v in w["vanished"]} == {3: "VANISHED_ROSTERED", 10: "VANISHED_ROSTERED"}
    assert w["vanished"][0]["name"] == f"Name {pid(3)}" and w["purged"] == [9]
    reasons = {r["player_id"]: r["reason"] for r in p["db"]["retirements"]}
    assert reasons == {pid(8): "window:conflict:RESIGN:re_retired_again", pid(3): "window:vanished:RESIGN",
                       pid(10): "window:vanished:RESIGN"}


def test_every_run_has_a_roster_image_and_replans_identically():
    p = plan("REGULAR_WEEK")
    w = p["summary"]["retirement_window"]
    assert w["role"] == "image" and w["roster_image"]["1"] == ["Signed", 0] and "decided" not in w
    snap = resign_snap([player(1, 0), player(2, 0, status="Retired")])
    a = plan("RESIGN", snap, window_deps(snap, decision=DECISION, prior=PRIOR, proof=PROOF))
    b = plan("RESIGN", copy.deepcopy(snap), window_deps(snap, decision=DECISION, prior=PRIOR, proof=PROOF))
    assert a["edits_hash"] == b["edits_hash"] and a["summary"]["retirement_window"] == b["summary"]["retirement_window"]


def test_decision_stage_keep_uses_the_baseline_team_with_a_proof():
    snap = snapshot("OffSeason", "OffSeason", 0, 1, players=[player(1, 0), player(8, 32, status="Retired", age=30, ovr=80)])
    p = plan("OFFSEASON_START", snap, window_deps(snap, prior={"1": ["Signed", 0], "8": ["Signed", 1]},
                                                  prior_stop="PLAYOFF_WEEK", proof=PROOF))
    w = p["summary"]["retirement_window"]
    assert {e["pgid"]: e["set"] for e in p["edits"]["players"]}[8] == {"ContractStatus": "Signed", "TeamIndex": 1}
    assert [r["pgid"] for r in w["restored"]] == [8] and w["roster_image"]["8"] == ["Signed", 1]
    # a proof that does not cover the decision stage falls back to the legacy free-agent writer
    uncovered = dict(PROOF, stages=["RESIGN"])
    p = plan("OFFSEASON_START", snap, window_deps(snap, prior={"8": ["Signed", 1]}, proof=uncovered))
    assert {e["pgid"]: e["set"] for e in p["edits"]["players"]}[8] == {"ContractStatus": "FreeAgent", "TeamIndex": 32}


def test_broken_proof_stops_the_plan(tmp_path, monkeypatch):
    bad = tmp_path / "proof.json"
    bad.write_text("{not json")
    monkeypatch.setenv("MU_RETIREMENT_AUTHORITY", str(bad))
    snap = resign_snap([player(1, 0)])
    deps = FakeDeps(snap)
    deps.retirement_window_inputs = lambda cur, season: {"decision": DECISION, "decided": {}, "window_restores": {},
                                                         "prior_image": PRIOR, "prior_stop": "OFFSEASON_START"}
    p = plan("RESIGN", snap, deps)
    assert any(e.startswith("retirement authority: WindowError") for e in p["errors"])


class RowsCursor:
    def __init__(self, many, one):
        self.many, self.one, self.sql = many, one, []

    def execute(self, sql, args=None):
        self.sql.append((" ".join(sql.split()), args))

    def fetchall(self):
        return self.many

    def fetchone(self):
        return self.one


def test_retirement_window_inputs_reads_written_runs():
    rows = [(5, "decision", "OFFSEASON_START", json.dumps({"8": {"player_id": 1008, "keep": True}}), None),
            (6, "window", "RESIGN", None, json.dumps([{"pgid": 2, "player_id": 1002}])),
            (7, "window", "FREE_AGENCY:1", None, [{"pgid": 8, "player_id": 1008}])]
    cur = RowsCursor(rows, (7, "FREE_AGENCY:1", json.dumps({"1": ["Signed", 0]})))
    got = lo.Deps().retirement_window_inputs(cur, SEASON)
    assert got["decision"] == {"run_id": 5, "stop": "OFFSEASON_START",
                               "decided": {"8": {"player_id": 1008, "keep": True}}}
    assert got["window_restores"] == {"2": 1, "8": 1}
    assert got["decided"]["2"] == {"player_id": 1002, "keep": True, "reason": "window:restored:RESIGN"}
    assert got["decided"]["8"] == {"player_id": 1008, "keep": True}       # the decision record wins
    assert (got["prior_image"], got["prior_stop"], got["prior_run_id"]) == ({"1": ["Signed", 0]}, "FREE_AGENCY:1", 7)
    assert all("status = 'WRITTEN'" in s for s, _ in cur.sql)
    empty = lo.Deps().retirement_window_inputs(RowsCursor([], None), SEASON)
    assert empty["decision"] is None and empty["prior_image"] is None


def test_retirement_authority_reads_the_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MU_RETIREMENT_AUTHORITY", raising=False)
    assert lo.Deps().retirement_authority() is None
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(PROOF))
    monkeypatch.setenv("MU_RETIREMENT_AUTHORITY", str(path))
    assert lo.Deps().retirement_authority()["fixture_id"] == "g4-test"


def test_run_report_leaves_out_the_roster_image():
    s = {"stage": "RESIGN", "retirement_window": {"role": "window", "roster_image": {"1": ["Signed", 0]}}}
    assert lo.report_summary(s)["retirement_window"] == {"role": "window"}
    assert s["retirement_window"]["roster_image"]           # the stored summary keeps it
    assert lo.report_summary({"stage": "X"}) == {"stage": "X"}
