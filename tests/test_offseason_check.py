"""tools/offseason_check: the pure invariant classifier, the guards and the e2e comparisons, offline.

The database and save parts are opt-in: they run only with OFFSEASON_CHECK_DSN_OK=1, and the e2e one also needs
OFFSEASON_CHECK_SAVE=<a save to copy>. They create and drop their own mu_scratch_offseason_<random>_test database.
"""
import os

import pytest

from tools.offseason_check import checks, invariant as inv, report as rp, safety


def row(**kw):
    base = {"id": 1, "team_id": 1, "person_kind": "HC", "person_id": 7, "annual_salary": 6_000_000, "total_years": 4,
            "remaining_years": 4, "signed_season": 2026, "full_control": False, "buyout_owed": 0,
            "buyout_total": None, "buyout_fired_season": None, "buyout_years": None}
    base.update(kw)
    return base


class FakeTruth:
    """held: {(team, kind, pid): set of seasons}; moved: same shape; staff: {season: [(team, kind, pid)]}."""

    def __init__(self, held=None, moved=None):
        self._held, self._moved = held or {}, moved or {}

    def held(self, team, kind, pid, y):
        return y in self._held.get((team, kind, pid), set())

    def moved(self, team, kind, pid, y):
        return y in self._moved.get((team, kind, pid), set())

    def name(self, kind, pid):
        return f"{kind}{pid}"

    def staff_held(self, y):
        return [k for k, ys in self._held.items() if y in ys]


# ------------------------------------------------------------------------------------------- the classifier
class TestClassifyYear:
    def test_salary_while_held_is_ok(self):
        v = inv.classify_year(row(), 2026, held=True, moved=False)
        assert (v.actual, v.expected, v.ok) == ("S", "S", True)

    def test_both_salary_and_buyout_is_a_double_charge(self):
        r = row(buyout_total=12_000_000, buyout_fired_season=2026, buyout_years=2)
        v = inv.classify_year(r, 2026, held=True, moved=False)
        assert v.actual == "SB" and not v.ok and "both" in v.note

    def test_neither_while_held_is_unpaid(self):
        v = inv.classify_year(row(remaining_years=0), 2027, held=True, moved=False)
        assert (v.actual, v.ok) == ("-", False) and "neither" in v.note

    def test_neither_after_a_firing_is_unpaid(self):
        v = inv.classify_year(row(remaining_years=0), 2028, held=False, moved=False)
        assert (v.actual, v.expected, v.ok) == ("-", "B", False)

    def test_beyond_the_deal_is_flagged(self):
        # a 4-yr deal from 2026 still charging salary in 2030
        v = inv.classify_year(row(remaining_years=1), 2030, held=True, moved=False)
        assert not v.ok and "beyond the deal 2026-2029" in v.note

    def test_nothing_beyond_the_deal_is_fine(self):
        v = inv.classify_year(row(remaining_years=0), 2030, held=True, moved=False)
        assert v.ok and v.actual == "-"

    def test_buyout_after_firing_is_ok(self):
        r = row(remaining_years=0, buyout_total=12_000_000, buyout_fired_season=2028, buyout_years=2)
        v = inv.classify_year(r, 2028, held=False, moved=False)
        assert (v.actual, v.expected, v.ok) == ("B", "B", True)

    def test_moved_owes_nothing(self):
        v = inv.classify_year(row(remaining_years=0), 2028, held=False, moved=True)
        assert (v.actual, v.expected, v.ok) == ("-", "-", True)
        r = row(remaining_years=0, buyout_total=1_950_000, buyout_fired_season=2028, buyout_years=1)
        assert not inv.classify_year(r, 2028, held=False, moved=True).ok      # a buyout to a mover is wrong

    def test_firing_season_booked_as_buyout_counts_as_paid(self):
        # the full-control / GM hot-seat rule: the firing season itself is the first instalment
        r = row(annual_salary=20_000_000, total_years=5, remaining_years=0, full_control=True,
                buyout_total=60_000_000, buyout_fired_season=2028, buyout_years=3)
        v = inv.classify_year(r, 2028, held=True, moved=False)
        assert v.ok and v.actual == "B" and "firing season" in v.note

    def test_a_buyout_in_a_held_season_that_is_not_the_firing_season_fails(self):
        r = row(remaining_years=0, buyout_total=12_000_000, buyout_fired_season=2027, buyout_years=2)
        assert not inv.classify_year(r, 2028, held=True, moved=False).ok


class TestLedgerReport:
    def test_fired_and_replaced_hc_timeline_passes(self):
        key = (1, "HC", 7)
        live = row()
        fired = row(remaining_years=0, buyout_owed=12_000_000, buyout_total=12_000_000, buyout_fired_season=2028,
                    buyout_years=2)
        snaps = {2026: [live], 2027: [live], 2028: [fired], 2029: [fired]}
        rep = inv.ledger_report(snaps, FakeTruth(held={key: {2026, 2027}}))
        assert rep["ok"], rep
        assert rep["deals"][0]["total_paid"] == 24_000_000

    def test_held_season_with_two_salaries_is_reported(self):
        a, b = row(id=1), row(id=2, signed_season=2026)
        rep = inv.ledger_report({2026: [a, b]}, FakeTruth(held={(1, "HC", 7): {2026}}))
        assert not rep["ok"]
        assert rep["held_unpaid"][0]["payments"] == 2
        assert rep["live_duplicates"] == [[1, "HC", 7]]

    def test_firing_season_buyout_satisfies_held_unpaid(self):
        r = row(annual_salary=20_000_000, total_years=5, remaining_years=0, full_control=True,
                buyout_total=100_000_000, buyout_fired_season=2031, buyout_years=5, signed_season=2031)
        snaps = {y: [r] for y in range(2031, 2036)}
        rep = inv.ledger_report(snaps, FakeTruth(held={(1, "HC", 7): {2031}}))
        assert rep["ok"], rep
        assert rep["deals"][0]["total_paid"] == 100_000_000

    def test_live_duplicates(self):
        assert inv.live_duplicates([row(id=1), row(id=2), row(id=3, person_id=8)]) == [(1, "HC", 7)]
        assert inv.live_duplicates([row(id=1), row(id=2, remaining_years=0)]) == []


# ------------------------------------------------------------------------------------------- the guards
class TestDsnGuard:
    @pytest.mark.parametrize("dsn", [
        "host=localhost dbname=madden_test user=postgres",
        "host=localhost dbname=madden_mu2026 user=postgres",
        "host=localhost dbname=madden_2036",
        "host=localhost dbname=madden",
        "dbname=madden_tune_x",
        "postgresql://postgres:postgres@localhost:5432/madden_mu2026",
        "host=localhost dbname=postgres",
        "host=localhost dbname=scratch_mu_test",
        "host=localhost user=postgres",                                   # no dbname: never a default
        "dbname='mu_scratch_x' dbname=madden_test",                       # the last one wins, as libpq reads it
    ])
    def test_refused(self, dsn):
        with pytest.raises(safety.SafetyError):
            safety.check_scratch_dsn(dsn)

    @pytest.mark.parametrize("dsn,name", [
        ("host=localhost dbname=mu_scratch_offseason_ab12cd34_test user=postgres",
         "mu_scratch_offseason_ab12cd34_test"),
        ("postgresql://postgres:postgres@localhost/mu_scratch_builder_3_test", "mu_scratch_builder_3_test"),
    ])
    def test_scratch_accepted(self, dsn, name):
        assert safety.check_scratch_dsn(dsn) == name

    def test_unparseable_dsn_refused(self):
        with pytest.raises(safety.SafetyError):
            safety.check_scratch_dsn("dbname='unterminated")

    def test_connected_database_is_rechecked(self):
        class Cur:
            def __init__(self, name):
                self.name = name

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql):
                assert "current_database" in sql

            def fetchone(self):
                return (self.name,)

        class Conn:
            def __init__(self, name):
                self.name = name

            def cursor(self):
                return Cur(self.name)

        assert safety.assert_connected_scratch(Conn("mu_scratch_x_test")) == "mu_scratch_x_test"
        with pytest.raises(safety.SafetyError):
            safety.assert_connected_scratch(Conn("madden_test"))
        with pytest.raises(safety.SafetyError):
            safety.assert_connected_scratch(Conn("mu_scratch_other_test"), expected="mu_scratch_x_test")


class TestSaveGuards:
    def test_sha_guard_detects_a_change(self, tmp_path):
        f = tmp_path / "CAREER-X"
        f.write_bytes(b"franchise")
        g = safety.ShaGuard(f)
        assert g.verify() == g.before
        f.write_bytes(b"franchise!")
        with pytest.raises(safety.SafetyError, match="ORIGINAL SAVE CHANGED"):
            g.verify()

    def test_pristine_baseline_refused(self, tmp_path):
        d = tmp_path / "data" / "baselines"
        d.mkdir(parents=True)
        p = d / "CAREER-MUBASE.pristine"
        p.write_bytes(b"x")
        with pytest.raises(safety.SafetyError, match="pristine"):
            safety.check_save_path(p)
        other = tmp_path / "CAREER-MUBASE.pristine"
        other.write_bytes(b"x")
        with pytest.raises(safety.SafetyError):
            safety.check_save_path(other)

    def test_missing_save_refused(self, tmp_path):
        with pytest.raises(safety.SafetyError):
            safety.check_save_path(tmp_path / "nope")

    def test_writes_refused_in_the_saves_folder_and_baselines(self, tmp_path):
        with pytest.raises(safety.SafetyError, match="saves folder"):
            safety.check_writable_path("/mnt/c/Users/jr/Documents/Madden NFL 27/saves/CAREER-COPY")
        with pytest.raises(safety.SafetyError, match="saves folder"):
            safety.check_writable_path("/mnt/c/Users/jr/Documents/MADDEN NFL 27/Saves")
        with pytest.raises(safety.SafetyError, match="baselines"):
            safety.check_writable_path(tmp_path / "data" / "baselines" / "x.json")
        assert safety.check_writable_path(tmp_path / "out.json").endswith("out.json")

    def test_reading_from_the_saves_folder_is_allowed(self, tmp_path):
        d = tmp_path / "Madden NFL 27" / "saves"
        d.mkdir(parents=True)
        (d / "CAREER-MUBASE").write_bytes(b"x")
        assert safety.check_save_path(d / "CAREER-MUBASE").endswith("CAREER-MUBASE")

    def test_pin_lookup(self, tmp_path):
        save = tmp_path / "CAREER-MUBASE"
        save.write_bytes(b"x")
        pins = tmp_path / "pins"
        pins.mkdir()
        (pins / "CAREER-MUBASE.sha256").write_text(f"abc123  {save}\n")
        assert safety.pinned_sha(save, [str(pins)]) == "abc123"
        assert safety.pinned_sha(tmp_path / "other", [str(pins)]) is None


# ------------------------------------------------------------------------------------------- e2e comparisons
SNAP = {"coaches": [{"pgid": 10, "first_name": "A", "last_name": "Hc", "contract_length": 4, "contract_salary": 600,
                     "contract_years_remaining": 4},
                    {"pgid": 11, "first_name": "B", "last_name": "Oc", "contract_length": 3, "contract_salary": 195,
                     "contract_years_remaining": 3},
                    {"pgid": 12, "first_name": "C", "last_name": "Dc", "contract_length": 0, "contract_salary": 0,
                     "contract_years_remaining": 0}],
        "staff": [{"team_index": 0, "hc": 10, "oc": 11, "dc": 12}]}


class TestSeatContracts:
    def test_zero_and_mismatch_are_problems(self):
        profiles = {10: ("COACH", 1, "SAVE"), 11: ("COORD", 2, "CREATED"), 12: ("COORD", 3, "SAVE")}
        ledger = {("HC", 1): (4, 600, 4), ("OC", 2): (3, 195, 2), ("DC", 3): (3, 195, 3)}

        def wanted(team_id, kind, pid):
            got = ledger.get((kind, pid))
            return dict(zip(checks.CONTRACT_FIELDS, got)) if got else None
        seats, by_source, problems = checks.seat_contract_problems(SNAP, profiles, {0: 5}, wanted)
        assert seats == 3 and by_source == {"SAVE": 2, "CREATED": 1}
        assert len(problems) == 3          # OC mismatched; DC zero in the save, and so mismatched too
        assert any("OC" in p and "!=" in p for p in problems)
        assert any("DC" in p and "(0, 0, 0)" in p for p in problems)

    def test_all_matching_is_clean(self):
        snap = {"coaches": SNAP["coaches"][:2], "staff": [{"team_index": 0, "hc": 10, "oc": 11, "dc": 11}]}
        profiles = {10: ("COACH", 1, "SAVE"), 11: ("COORD", 2, "SAVE")}
        rows = {("HC", 1): (4, 600, 4), ("OC", 2): (3, 195, 3), ("DC", 2): (3, 195, 3)}
        _s, _b, problems = checks.seat_contract_problems(
            snap, profiles, {0: 5}, lambda t, k, p: dict(zip(checks.CONTRACT_FIELDS, rows[(k, p)])))
        assert problems == []


class TestStartYears:
    STAFF = [{"team_id": 1, "person_kind": "HC", "person_id": 1, "name": "Ryans", "start_season": 2023},
             {"team_id": 1, "person_kind": "GM", "person_id": 9, "name": "GM", "start_season": 2024},
             {"team_id": 1, "person_kind": "OC", "person_id": 2, "name": "New OC", "start_season": 2026}]

    @staticmethod
    def years(kind):
        return {"HC": 4, "GM": 4}.get(kind, 3)

    def test_save_years_win_and_the_tenure_rule_covers_gms_and_zero(self):
        rows = {(1, "HC", 1): {"total_years": 5, "remaining_years": 2},
                (1, "GM", 9): {"total_years": 4, "remaining_years": 2},     # 2024 + 4 years -> 2 left in 2026
                (1, "OC", 2): {"total_years": 3, "remaining_years": 3}}
        save = {(1, "HC", 1): (5, 2), (1, "OC", 2): (3, 0)}
        n, problems = checks.start_years_problems(self.STAFF, lambda *k: rows.get(k), lambda *k: save.get(k),
                                                  self.years, 2026)
        assert n == 3 and problems == []

    def test_a_fresh_full_term_from_league_start_is_flagged(self):
        # F1 as found on CAREER-MUBASE: the link signs an incumbent for a full new term
        rows = {(1, "HC", 1): {"total_years": 4, "remaining_years": 4},
                (1, "GM", 9): {"total_years": 4, "remaining_years": 2},
                (1, "OC", 2): {"total_years": 3, "remaining_years": 3}}
        save = {(1, "HC", 1): (5, 2)}
        _n, problems = checks.start_years_problems(self.STAFF, lambda *k: rows.get(k), lambda *k: save.get(k),
                                                   self.years, 2026)
        assert len(problems) == 1 and "Ryans" in problems[0] and "5/2" in problems[0]

    def test_tenure_rule(self):
        assert checks.tenure_rule(4, 2023, 2026) == (4, 1)
        assert checks.tenure_rule(4, 2026, 2026) == (4, 4)
        assert checks.tenure_rule(4, 2020, 2026) == (4, 4)          # long over: a fresh term
        assert checks.tenure_rule(3, None, 2026) == (3, 3)


def test_expectation_hook():
    rows = [row(id=1, team_id=3, remaining_years=1), row(id=2, team_id=3, person_kind="OC", person_id=8)]
    names, teams = {("HC", 7): "DeMeco Ryans"}, {3: "HOU"}
    ok = [{"team": "HOU", "kind": "HC", "name": "DeMeco Ryans", "remaining_years": 1}]
    assert checks.expectation_problems(rows, names, teams, ok) == []
    bad = [{"team": "HOU", "kind": "HC", "remaining_years": 2}, {"team": "HOU", "kind": "DC"}]
    probs = checks.expectation_problems(rows, names, teams, bad)
    assert len(probs) == 2 and "remaining_years is 1" in probs[0] and "0 live rows" in probs[1]


def test_report_table_and_known_open():
    found = [rp.Check("write", "OFFSEASON_START", rp.PASS, "ok"),
             rp.Check("start_years_from_save", "load", rp.FAIL, "39 differ"),
             rp.Check("link_counts", "load", rp.INFO, "linked 71")]
    assert rp.overall(found) == rp.FAIL
    rp.apply_known_open(found, ["start_years_from_save"])
    assert found[1].status == rp.KNOWN and rp.overall(found) == rp.PASS
    text = rp.table(found)
    assert "OVERALL PASS" in text and "KNOWN-OPEN" in text
    assert '"overall": "PASS"' in rp.to_json(found)


def test_last_json_reads_the_league_office_report():
    from tools.offseason_check.e2e import last_json
    out = 'INFO x\n{\n "annual_steps": {"a": 1}\n}\narticle text\n{\n "status": "WRITTEN",\n "annual_steps": ' \
          '{\n  "staff_contracts": {"filed": 0}\n }\n}\n'
    assert last_json(out)["annual_steps"]["staff_contracts"]["filed"] == 0
    assert last_json('{"a": 1}') == {"a": 1}
    assert last_json("no json here") is None


def test_projection_summary():
    deals = [{"team": "DEN", "kind": "HC", "person_id": 1, "who": "Payton", "deal": "2024-2027"},
             {"team": "DEN", "kind": "HC", "person_id": 1, "who": "Payton", "deal": "2027-2030"},
             {"team": "HOU", "kind": "HC", "person_id": 2, "who": "Ryans", "deal": "2023-2028",
              "fired_season": 2027, "buyout_total": 22_000_000, "buyout_years": 2},
             {"team": "HOU", "kind": "HC", "person_id": 3, "who": "Harbaugh", "deal": "2027-2030"}]
    got = checks.projection_summary({"deals": deals}, 2026)
    assert got["resigned"] == ["DEN HC Payton"]                     # a new hire is not a re-sign
    assert got["buyouts"] == [("HOU", "HC", "Ryans", 22_000_000, 2)]


def test_staff_action_counts():
    acts = [{"action": "release"}, {"action": "assign", "contract": {"ContractLength": 3}}, {"action": "create"}]
    assert checks.staff_action_counts(acts) == {"release": 1, "assign": 1, "create": 1, "with_contract": 1}


def test_expected_table_covers_the_scenarios():
    from tools.offseason_check import timelines
    assert set(timelines.EXPECTED) <= set(timelines.SCENARIOS)
    assert timelines.EXPECTED["fc-y1-replaced"][("HC", "Godfather", "2031-2035")][0] == 100_000_000
    assert timelines.EXPECTED["carousel-A"][("HC", "HC MIA", "2028-2032")][1] == 60_000_000
    assert timelines.EXPECTED["hc-mid-replaced"][("HC", "Old HC", "2026-2029")][1:] == (12_000_000, 2028)


def test_expected_mismatches():
    from tools.offseason_check import timelines
    rep = {"deals": [{"kind": "HC", "who": "Old HC", "deal": "2026-2029", "total_paid": 24_000_000,
                      "buyout_total": 12_000_000, "fired_season": 2028}]}
    assert timelines.expected_mismatches(rep, {("HC", "Old HC", "2026-2029"): (24_000_000, 12_000_000, 2028)}) == []
    assert timelines.expected_mismatches(rep, {("HC", "Old HC", "2026-2029"): (24_000_000, 18_000_000, 2027)})
    assert timelines.expected_mismatches(rep, {("GM", None, "2026-2029"): (1, None, None)})


# ------------------------------------------------------------------------------------------- opt-in: DB + save
DB_OK = os.environ.get("OFFSEASON_CHECK_DSN_OK") == "1"
SAVE = os.environ.get("OFFSEASON_CHECK_SAVE")


@pytest.mark.skipif(not DB_OK, reason="set OFFSEASON_CHECK_DSN_OK=1 to run the scratch-database timelines")
def test_timelines_on_a_scratch_db():
    from tools.offseason_check import scratch, timelines
    with scratch.scratch_db("offseason", log=lambda *a: None) as (_name, dsn):
        scratch.init_schema(dsn)
        results = timelines.run_all(dsn, ["hc-mid-replaced", "fc-y1-replaced", "gm-league-start", "rerun"],
                                    log=lambda *a: None)
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]


@pytest.mark.skipif(not (DB_OK and SAVE), reason="set OFFSEASON_CHECK_DSN_OK=1 and OFFSEASON_CHECK_SAVE=<save>")
def test_e2e_on_a_save_copy(tmp_path):
    from tools.offseason_check import e2e
    found, meta = e2e.run(SAVE, workdir=str(tmp_path), known_open=["start_years_from_save"], log=lambda *a: None)
    failed = [c for c in found if c.status == rp.FAIL]
    assert not failed, rp.table(failed)
    assert safety.sha256_file(SAVE) == meta["save_sha256"]
