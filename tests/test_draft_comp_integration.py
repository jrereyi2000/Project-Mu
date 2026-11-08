"""Compensatory picks in the draft pipeline (COMPENSATORY_PICKS_PLAN follow-up, U5): a round with N comp picks has
32 + N slots and later rounds shift, lock_order fills comp rows without touching a standard slot, start_round
accepts a round of more than 32, and the league office signs a comp pick with its awarded round. Pure functions and
fake cursors; no database."""
import collections

from engines import contracts_engine as ce
from engines import draft_order as do
from engines import draft_room as dr
from pipeline import league_office as lo

DS = 2037


# ------------------------------------------------------------------ fixtures ----
def teams_rows():
    return [(t, f"Team {t}", f"T{t}", "AFC" if t % 2 else "NFC") for t in range(1, 33)]


def standard_rows():
    """Every team's standard pick in rounds 1-7 (source SEED, empty suffix)."""
    return [(rnd, tid, tid, "", "SEED") for rnd in range(1, 8) for tid in range(1, 33)]


def comp_rows(round_no=3, teams=(1, 2, 3, 4), source="COMP", owners=None):
    """The round's comp draft_picks rows (round, original_team_id, owner_team_id, pick_suffix, source)."""
    owners = owners or {}
    return [(round_no, tid, owners.get(tid, tid), str(i), source) for i, tid in enumerate(teams, 1)]


class OrderCursor:
    """Answers the three reads draft_order_with_owners makes."""

    def __init__(self, rows):
        self.rows = rows
        self._ret = None

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            self._ret = (1,)
        elif s.startswith("SELECT id, name, abbreviation, conference FROM teams"):
            self._ret = teams_rows()
        elif s.startswith("SELECT home_team_id"):
            self._ret = []
        elif s.startswith("SELECT round, original_team_id, owner_team_id"):
            self._ret = list(self.rows)
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def fetchall(self):
        return self._ret

    def fetchone(self):
        return self._ret[0] if self._ret else None


class StoreCursor:
    """A lock_order cursor: keeps draft_picks rows keyed (round, original_team_id, pick_suffix) and applies the
    overall_pick writes, so the order can be inspected and lock_order can be run twice."""

    def __init__(self, rows):
        self.rows = rows
        self.store = {(rnd, orig, suffix): {"overall_pick": None, "owner": owner}
                      for rnd, orig, owner, suffix, _ in rows}
        self.many = []
        self._ret = None

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        self._ret = None
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            self._ret = (1,)
        elif s.startswith("SELECT to_regclass('scout_draft_picks')"):
            self._ret = (True,)
        elif s.startswith("SELECT count(*) FROM scout_draft_picks"):
            self._ret = (0,)
        elif s.startswith("SELECT t.id, t.abbreviation"):
            self._ret = [(t, f"T{t}", f"Team {t}", False, None, None) for t in range(1, 33)]
        elif s.startswith("SELECT id, name, abbreviation, conference FROM teams"):
            self._ret = teams_rows()
        elif s.startswith("SELECT home_team_id"):
            self._ret = []
        elif s.startswith("SELECT round, original_team_id, owner_team_id"):
            self._ret = list(self.rows)
        elif s.startswith("UPDATE draft_picks SET overall_pick = NULL"):
            for v in self.store.values():
                v["overall_pick"] = None
        elif s.startswith("UPDATE draft_picks SET owner_team_id"):
            raise AssertionError("no fixes expected")
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def executemany(self, sql, rows):
        s = " ".join(sql.split())
        assert "pick_suffix = %s" in s               # a comp row is never matched by its standard slot
        rows = list(rows)
        self.many.append(rows)
        for overall, _season, rnd, orig, suffix in rows:
            self.store[(rnd, orig, suffix)]["overall_pick"] = overall

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return self._ret


class SlotsCursor:
    """round_slots: the earlier rounds' comp count, then the round's locked rows."""

    def __init__(self, comps_before, rows):
        self.comps_before = comps_before
        self.rows = rows
        self._ret = None

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            self._ret = (1,)
        elif s.startswith("SELECT count(*) FROM draft_picks"):
            self._ret = (self.comps_before,)
        elif s.startswith("SELECT overall_pick, original_team_id, owner_team_id"):
            self._ret = list(self.rows)
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return self._ret


class EmptyCursor:
    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class InputsCursor:
    """Deps.draft_inputs: locked slots, the class's comp counts, no stored board, no JR war-room picks."""

    def __init__(self, comps):
        self.comps = comps
        self._ret = None

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            self._ret = (1,)
        elif s.startswith("SELECT overall_pick, owner_team_id FROM draft_picks"):
            self._ret = [(1, 1), (97, 4)]
        elif s.startswith("SELECT round, count(*)"):
            self._ret = self.comps
        elif s.startswith("SELECT max(step) FROM scout_team_boards"):
            self._ret = (None,)
        elif "choice_source = 'JR_TEAM'" in s:
            self._ret = []
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return self._ret


class NoSuffixCursor:
    """A database where 2027_02_06_compensatory_draft_picks.sql was never applied: draft_picks has no pick_suffix
    column. Only the information_schema probe may name the column; every other query must be the old plain one."""

    def __init__(self, rows):
        self.rows = rows                       # (round, original_team_id, owner_team_id, source)
        self.store = {(rnd, orig): {"overall_pick": None} for rnd, orig, _owner, _src in rows}
        self.locked_rows = []
        self.sql = []
        self.many = []
        self._ret = None

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            self._ret = None                   # the column does not exist
        elif s.startswith("SELECT id, name, abbreviation, conference FROM teams"):
            self._ret = teams_rows()
        elif s.startswith("SELECT home_team_id"):
            self._ret = []
        elif s.startswith("SELECT round, original_team_id, owner_team_id FROM draft_picks"):
            self._ret = [(rnd, orig, owner) for rnd, orig, owner, _src in self.rows]
        elif s.startswith("SELECT overall_pick, original_team_id, owner_team_id FROM draft_picks"):
            self._ret = list(self.locked_rows)
        elif s.startswith("SELECT overall_pick, owner_team_id FROM draft_picks"):
            self._ret = [(1, 1)]
        elif s.startswith("SELECT to_regclass('scout_draft_picks')"):
            self._ret = (True,)
        elif s.startswith("SELECT count(*) FROM scout_draft_picks"):
            self._ret = (0,)
        elif s.startswith("SELECT t.id, t.abbreviation"):
            self._ret = [(t, f"T{t}", f"Team {t}", False, None, None) for t in range(1, 33)]
        elif s.startswith("SELECT max(step) FROM scout_team_boards"):
            self._ret = (None,)
        elif "choice_source = 'JR_TEAM'" in s:
            self._ret = []
        elif s.startswith("UPDATE draft_picks SET overall_pick = NULL"):
            for v in self.store.values():
                v["overall_pick"] = None
        elif s.startswith("UPDATE draft_picks SET owner_team_id"):
            raise AssertionError("no fixes expected")
        elif "count(*) FROM draft_picks" in s:
            raise AssertionError("the comp sweep must not run without pick_suffix")
        else:
            raise AssertionError(f"unexpected SQL: {s}")
        assert "pick_suffix" not in s or "information_schema" in s, s

    def executemany(self, sql, rows):
        s = " ".join(sql.split())
        assert "pick_suffix" not in s                      # the old 4-column update form
        rows = list(rows)
        self.many.append(rows)
        for overall, _season, rnd, orig in rows:
            self.store[(rnd, orig)]["overall_pick"] = overall

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return self._ret


# ----------------------------------------------------------- draft_order (§1) ----
def test_no_comp_class_is_byte_identical_to_the_old_order():
    slots = do.draft_order_with_owners(OrderCursor(standard_rows()), DS, rounds=range(1, 8))
    assert len(slots) == 224
    assert [s["pick"] for s in slots] == list(range(1, 225))
    for s in slots:
        # the old formula, unchanged: (round - 1) * 32 + the round's slot
        assert s["pick"] == (s["round"] - 1) * 32 + s["round_pick"]
        assert s["round_pick"] == s["original_team_id"]        # the provisional order: team t picks t-th
        assert set(s) == {"pick", "round", "round_pick", "original_team_id", "owner_team_id", "locked"}
    # apply_owners still returns exactly the old six keys and values
    order = [{"pick": i, "team_id": 100 + i, "locked": True} for i in range(1, 33)]
    expected = [{"pick": i, "round": 1, "round_pick": i, "original_team_id": 100 + i,
                 "owner_team_id": 120 if i == 3 else 100 + i, "locked": True} for i in range(1, 33)]
    assert do.apply_owners(order, {(1, 103): 120}, 1) == expected


def test_four_round_3_comps_shift_round_4_to_101():
    slots = do.draft_order_with_owners(OrderCursor(standard_rows() + comp_rows(owners={1: 9})), DS,
                                       rounds=range(1, 8))
    by_round = collections.defaultdict(list)
    for s in slots:
        by_round[s["round"]].append(s)
    assert len(slots) == 228

    # Round 1 has no comp picks and stays 1-32; Round 2 is untouched by Round-3 comps.
    assert [s["pick"] for s in by_round[1]] == list(range(1, 33))
    assert [s["pick"] for s in by_round[2]] == list(range(33, 65))

    r3 = by_round[3]
    assert len(r3) == 36
    assert [s["pick"] for s in r3[:32]] == list(range(65, 97))
    comps = r3[32:]
    assert [s["pick"] for s in comps] == [97, 98, 99, 100]
    assert [s["round_pick"] for s in comps] == [33, 34, 35, 36]
    assert [s["pick_suffix"] for s in comps] == ["1", "2", "3", "4"]
    assert all(s["locked"] and s["round"] == 3 for s in comps)
    # a comp row never shadows its team's standard slot: team 1's standard pick is still team 1's,
    # its comp pick belongs to team 9
    assert r3[0]["owner_team_id"] == 1
    assert comps[0]["original_team_id"] == 1 and comps[0]["owner_team_id"] == 9

    assert [s["pick"] for s in by_round[4]] == list(range(101, 133))
    assert [s["round_pick"] for s in by_round[4]] == list(range(1, 33))
    assert [s["pick"] for s in by_round[7]] == list(range(197, 229))


# ------------------------------------------------------------ draft_room (§2) ----
def test_lock_order_fills_every_row_once_including_comps_and_is_idempotent(monkeypatch):
    cur = StoreCursor(standard_rows() + comp_rows())
    monkeypatch.setattr(dr, "state", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(dr, "clock_trades", lambda *a, **k: None)
    monkeypatch.setattr(dr.se, "stored_steps", lambda *a, **k: [dr.se.STEP_DRAFT])

    dr.lock_order(cur, DS)
    first = {k: dict(v) for k, v in cur.store.items()}
    assert len(cur.store) == 228
    assert all(v["overall_pick"] is not None for v in cur.store.values())
    assert len(cur.many) == 1
    written = [(rnd, orig, suffix) for _overall, _season, rnd, orig, suffix in cur.many[0]]
    assert len(written) == len(set(written)) == 228          # every row once, no duplicates
    assert cur.store[(1, 1, "")]["overall_pick"] == 1
    assert cur.store[(3, 1, "")]["overall_pick"] == 65
    assert [cur.store[(3, tid, str(tid))]["overall_pick"] for tid in (1, 2, 3, 4)] == [97, 98, 99, 100]
    assert cur.store[(4, 1, "")]["overall_pick"] == 101      # shifted by the four Round-3 comps
    assert cur.store[(7, 32, "")]["overall_pick"] == 228

    dr.lock_order(cur, DS)
    assert {k: dict(v) for k, v in cur.store.items()} == first


def test_round_slots_handles_a_round_with_more_than_32():
    # Round 3: 32 standard (overall 65-96) then the four comps (97-100), no earlier comps.
    r3 = [(p, (i % 32) + 1, (i % 32) + 1) for i, p in enumerate(range(65, 101))]
    slots3 = dr.round_slots(SlotsCursor(0, r3), DS, 3)
    assert len(slots3) == 36
    assert [s["round_pick"] for s in slots3] == list(range(1, 37))
    assert [s["pick"] for s in slots3] == list(range(65, 101))
    # Round 4: four earlier comps, its own 32 slots start at overall 101 with round_pick 1.
    r4 = [(p, (i % 32) + 1, (i % 32) + 1) for i, p in enumerate(range(101, 133))]
    slots4 = dr.round_slots(SlotsCursor(4, r4), DS, 4)
    assert len(slots4) == 32
    assert slots4[0]["pick"] == 101 and slots4[0]["round_pick"] == 1
    assert [s["round_pick"] for s in slots4] == list(range(1, 33))


def test_start_round_accepts_a_round_with_more_than_32(monkeypatch):
    slots = [{"pick": 65 + i, "round_pick": i + 1, "key": (DS, 3, i + 1), "original_team_id": i + 1,
              "owner_team_id": i + 1} for i in range(36)]
    room = {"locked": True, "recorded": [{}] * 32, "available": set(), "info": {}, "boards": {}, "cons": {},
            "ctxs": {}}
    monkeypatch.setattr(dr, "rounds_installed", lambda cur: True)
    monkeypatch.setattr(dr, "load_room", lambda cur, ds: room)
    monkeypatch.setattr(dr, "rounds_started", lambda cur, ds: {2: 0})
    monkeypatch.setattr(dr, "round_slots", lambda cur, ds, n: slots)
    monkeypatch.setattr(dr, "_season", lambda cur: DS - 1)
    monkeypatch.setattr(dr, "state", lambda *a, **k: {"rounds": {"current": 3}})
    calls = []
    monkeypatch.setattr("engines.gm_trade_engine.run_draft_round",
                        lambda *a, **k: calls.append(a) or {"proposals": []})

    out = dr.start_round(EmptyCursor(), DS, 3)              # 36 slots: not refused by a "32 slots" guard
    assert out == {"rounds": {"current": 3}}
    assert calls and len(calls[0][5]) == len(slots)         # run_draft_round got every slot (the books dict)


# ------------------------------------------------------- league_office (§3) ----
class FakePlan:
    def __init__(self):
        self.players = {10: {"pgid": 10, "first_name": "Al", "last_name": "Pha", "position": "QB",
                             "status": "Draft", "overall": 70}}
        self.snapshot = {"draft_class": [{"pgid": 10, "initial_draft_rank": 0}]}
        self.target = DS
        self.team_index = {1: 0}
        self.league_cap_dollars = ce.CBA["REFERENCE_CAP"]
        self.signs = []
        self.counts = collections.Counter()

    def sign_player(self, pgid, team, lst, contract, priority, reason):
        self.signs.append((pgid, team, contract, reason))
        return True


def test_league_office_gives_a_comp_pick_its_awarded_round():
    slots = [{"overall_pick": 97, "team_id": 1}]
    plan = FakePlan()
    lo.step_draft(plan, slots, {}, {}, 1, [0, 0, 4, 0, 0, 0, 0])
    assert plan.signs[0][2]["drafted_round"] == 3            # pick 97 is a Round-3 comp
    assert plan.signs[0][3] == "drafted 97 (fallback)"

    # a class with no comp rows behaves exactly as before (97 is a static Round-4 pick)
    for plan, schedule in ((FakePlan(), [0] * 7), (FakePlan(), None)):
        lo.step_draft(plan, slots, {}, {}, 1, schedule)
        assert plan.signs[0][2]["drafted_round"] == 4


def test_draft_inputs_builds_the_comp_schedule_from_draft_picks():
    slots, boards, jr, schedule = lo.Deps().draft_inputs(InputsCursor([(3, 4)]), 2036)
    assert schedule == [0, 0, 4, 0, 0, 0, 0]
    assert slots == [{"overall_pick": 1, "team_id": 1}, {"overall_pick": 97, "team_id": 4}]
    assert boards == {} and jr == {}

    _, _, _, none = lo.Deps().draft_inputs(InputsCursor([]), 2036)
    assert none == [0] * 7


# ------------------------------------- pre-migration degradation (no pick_suffix column) ----
def test_missing_pick_suffix_column_degrades_to_the_old_draft_flow(monkeypatch):
    rows = [(rnd, tid, tid, "SEED") for rnd in range(1, 8) for tid in range(1, 33)]

    # draft_order: 224 standard slots on the old formula, and no comp sweep
    cur = NoSuffixCursor(rows)
    slots = do.draft_order_with_owners(cur, DS, rounds=range(1, 8))
    assert len(slots) == 224
    assert [s["pick"] for s in slots] == list(range(1, 225))
    assert all(s["pick"] == (s["round"] - 1) * 32 + s["round_pick"] for s in slots)
    assert all(set(s) == {"pick", "round", "round_pick", "original_team_id", "owner_team_id", "locked"}
               for s in slots)

    # round_slots: plain 32-per-round numbering (Round 3 starts at overall 65, round_pick 1)
    cur = NoSuffixCursor(rows)
    cur.locked_rows = [(65 + i, i + 1, i + 1) for i in range(32)]
    slots3 = dr.round_slots(cur, DS, 3)
    assert [s["pick"] for s in slots3] == list(range(65, 97))
    assert [s["round_pick"] for s in slots3] == list(range(1, 33))

    # lock_order: the old 4-column write, every standard row filled
    cur = NoSuffixCursor(rows)
    monkeypatch.setattr(dr, "state", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(dr, "clock_trades", lambda *a, **k: None)
    monkeypatch.setattr(dr.se, "stored_steps", lambda *a, **k: [dr.se.STEP_DRAFT])
    dr.lock_order(cur, DS)
    assert len(cur.store) == 224 and all(v["overall_pick"] is not None for v in cur.store.values())
    assert len(cur.many) == 1 and all(len(r) == 4 for r in cur.many[0])
    assert cur.store[(1, 1)]["overall_pick"] == 1 and cur.store[(7, 32)]["overall_pick"] == 224

    # draft_inputs: an all-zero comp schedule, the comp query never runs
    cur = NoSuffixCursor(rows)
    _, _, _, schedule = lo.Deps().draft_inputs(cur, 2036)
    assert schedule == [0] * 7
