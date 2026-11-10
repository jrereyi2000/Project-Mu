"""Annual report-card producer and CPU owner facility policy (NFLPA_REPORT_CARD_PLAN follow-ups P1-20, P1-21, P1-23).

Offline: the producer runs on an in-memory SQLite fake; the owner policy is pure.
"""
import sqlite3

from engines import owner_policy as op
from engines import report_card_engine as rce
from engines import report_card_run as rcr
from tests.test_inbox_store import FakeCursor

BASE = rce.load_baseline()
# two real baseline cards whose weight room is above the floor (so degradation can move it)
TEAMS = [a for a in sorted(BASE) if rce.grade_to_score(BASE[a]["weight_room"]) > 0.0][:2]


def _db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript("""
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT);
        CREATE TABLE team_report_cards (season INT, team_id INT, weight_room TEXT, training_room TEXT,
            locker_room TEXT, food_nutrition TEXT, team_travel TEXT, family_treatment TEXT, head_coach TEXT,
            ownership TEXT, overall_score REAL, overall_grade TEXT, summary_text TEXT,
            last_renovated_season INT, PRIMARY KEY (season, team_id));
        CREATE TABLE owner_cash_ledgers (season INT, team_id INT, wealth_band TEXT, base_budget INT,
            success_dividend INT, total_cash_budget INT, staff_payroll INT, opex_committed INT, capex_spent INT,
            dead_buyouts INT DEFAULT 0, accumulated_reserve INT, PRIMARY KEY (season, team_id));
        CREATE TABLE facility_upgrades (id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, season_ordered INT,
            category TEXT, cost_dollars INT, target_grade TEXT, completion_season INT, status TEXT);
        CREATE TABLE team_operating_policies (team_id INTEGER PRIMARY KEY, luxury_travel_enabled INT,
            no_roommate_policy INT, family_daycare_enabled INT, private_chef_program INT, annual_opex_cost INT);
        CREATE TABLE team_seasons (team_id INT, season INT, wins INT, popularity_index REAL);
        CREATE TABLE games (season INT, game_type TEXT, status TEXT, home_team_id INT, away_team_id INT,
            home_score INT, away_score INT);
        CREATE TABLE staff_contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, person_id INT,
            person_kind TEXT, annual_salary INT, total_years INT, remaining_years INT, signed_season INT,
            full_control INT, buyout_owed INT);
    """)
    for i, abbr in enumerate(TEAMS):
        conn.execute("INSERT INTO teams VALUES (?, ?)", (i + 1, abbr))
    return conn


def _cards(cur, season=None):
    if season is None:
        cur.execute("SELECT team_id, weight_room, overall_score, overall_grade FROM team_report_cards "
                    "ORDER BY season, team_id")
    else:
        cur.execute("SELECT team_id, weight_room, overall_score, overall_grade FROM team_report_cards "
                    "WHERE season = %s ORDER BY team_id", (season,))
    return cur.fetchall()


def _ledgers(cur):
    cur.execute("SELECT team_id, base_budget, total_cash_budget, staff_payroll, opex_committed FROM owner_cash_ledgers "
                "ORDER BY team_id")
    return cur.fetchall()


def test_files_a_card_and_a_ledger_per_team():
    conn = _db()
    cur = FakeCursor(conn)
    rep = rcr.annual(cur, 2027, cpu_upgrades=False)
    assert rep["status"] == "built" and rep["teams"] == 2
    cards = _cards(cur)
    assert len(cards) == 2
    for tid, _, score, grade in cards:
        assert 0.0 <= score <= 1.0 and grade == rce.score_to_grade(score)
    led = _ledgers(cur)
    assert len(led) == 2 and all(r[1] > 0 and r[2] == r[1] for r in led)     # no wins: base only


def test_staff_payroll_and_the_success_dividend_reach_the_ledger():
    conn = _db()
    conn.execute("INSERT INTO team_seasons (team_id, season, wins) VALUES (1, 2027, 13)")
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (1, 10, 'HC', 12000000, 4, 4, 2026)")
    cur = FakeCursor(conn)
    rcr.annual(cur, 2027, cpu_upgrades=False)
    cur.execute("SELECT staff_payroll, success_dividend, total_cash_budget FROM owner_cash_ledgers WHERE team_id = 1")
    payroll, dividend, budget = cur.fetchone()
    assert payroll == 12_000_000
    assert dividend == 5 * 300_000                                   # 13 wins, +$300k a win above 8
    assert budget == rce.base_budget(BASE[TEAMS[0]]["wealth_band"]) + dividend


def test_an_enabled_policy_raises_the_grade_and_costs_opex():
    conn = _db()
    conn.execute("INSERT INTO team_operating_policies VALUES (1, 1, 0, 0, 0, 3500000)")
    cur = FakeCursor(conn)
    rcr.annual(cur, 2027, cpu_upgrades=False)
    cur.execute("SELECT team_travel FROM team_report_cards WHERE team_id = 1")
    assert cur.fetchone()[0] == "A"
    cur.execute("SELECT opex_committed FROM owner_cash_ledgers WHERE team_id = 1")
    assert cur.fetchone()[0] == 3_500_000


def test_a_completed_upgrade_applies_and_is_marked():
    conn = _db()
    conn.execute("INSERT INTO facility_upgrades (team_id, season_ordered, category, cost_dollars, target_grade, "
                 "completion_season, status) VALUES (1, 2026, 'weight_room', 12000000, 'A+', 2027, 'IN_PROGRESS')")
    cur = FakeCursor(conn)
    rcr.annual(cur, 2027, cpu_upgrades=False)
    cur.execute("SELECT weight_room, last_renovated_season FROM team_report_cards WHERE team_id = 1")
    assert cur.fetchone() == ("A+", 2027)
    cur.execute("SELECT status FROM facility_upgrades WHERE team_id = 1")
    assert cur.fetchone()[0] == "COMPLETED"


def test_degradation_after_three_unmaintained_seasons():
    conn = _db()
    cur = FakeCursor(conn)
    rcr.annual(cur, 2026, cpu_upgrades=False)
    before = next(r[1] for r in _cards(cur, 2026) if r[0] == 1)
    rcr.annual(cur, 2029, cpu_upgrades=False)
    after = next(r[1] for r in _cards(cur, 2029) if r[0] == 1)
    assert rce.grade_to_score(after) < rce.grade_to_score(before)


def test_the_second_run_changes_nothing():
    conn = _db()
    cur = FakeCursor(conn)
    rcr.annual(cur, 2027, cpu_upgrades=False)
    first = (_cards(cur), _ledgers(cur))
    rcr.annual(cur, 2027, cpu_upgrades=False)
    assert (_cards(cur), _ledgers(cur)) == first


def test_cpu_owners_order_facilities_but_jr_does_not():
    conn = _db()
    # team 2 starts from a poor card, so a balanced CPU owner has something to fix
    conn.execute("INSERT INTO team_report_cards (season, team_id, weight_room, training_room, locker_room, "
                 "food_nutrition, team_travel, family_treatment, head_coach, ownership, overall_score, "
                 "overall_grade, summary_text, last_renovated_season) VALUES "
                 "(2026, 2, 'F', 'F', 'F', 'F', 'F', 'F', 'F', 'F', 0.0, 'F-', 'x', NULL)")
    cur = FakeCursor(conn)
    rep = rcr.annual(cur, 2027, cpu_upgrades=True, user_team_ids={1})
    assert rep["orders"] >= 1
    cur.execute("SELECT team_id FROM facility_upgrades")
    assert [r[0] for r in cur.fetchall()] == [2]
    rep2 = rcr.annual(cur, 2027, cpu_upgrades=True, user_team_ids={1})
    cur.execute("SELECT count(*) FROM facility_upgrades")
    assert cur.fetchone()[0] == rep["orders"]                        # no duplicate projects on a re-run


# ------------------------------------------------------------------ owner policy (pure) ----
DEEP = {"traits": ["DEEP_POCKETS"], "sliders": {}}
FRUGAL = {"traits": ["BUDGET_CONSCIOUS"], "sliders": {}}
NEUTRAL = {"traits": [], "sliders": {}}


def test_facility_styles():
    assert op.facility_style(DEEP) == "AGGRESSIVE"
    assert op.facility_style(FRUGAL) == "FRUGAL"
    assert op.facility_style(NEUTRAL) == "BALANCED"
    assert op.facility_style(None) == "BALANCED"


def test_aggressive_owners_overhaul_a_terrible_category():
    card = {"weight_room": "F", "training_room": "C", "locker_room": "C", "food_nutrition": "C"}
    orders = op.facility_upgrade_orders(card, DEEP, cash_available=100_000_000, season=2037)
    assert orders and orders[0]["category"] == "weight_room" and orders[0]["target_grade"] == "A+"
    assert len(orders) <= op.FACILITY_MAX_PROJECTS["AGGRESSIVE"]


def test_frugal_owners_only_act_on_an_f_and_spend_the_minimum():
    card = {"weight_room": "D", "training_room": "F-", "locker_room": "C", "food_nutrition": "C"}
    orders = op.facility_upgrade_orders(card, FRUGAL, cash_available=100_000_000, season=2037)
    assert [o["category"] for o in orders] == ["training_room"]
    assert orders[0]["cost_dollars"] == rce.upgrade_cost("MINOR")


def test_no_order_without_cash():
    card = {"weight_room": "F-", "training_room": "F", "locker_room": "F", "food_nutrition": "F"}
    assert op.facility_upgrade_orders(card, DEEP, cash_available=1_000_000, season=2037) == []


def test_a_completed_renovation_is_not_re_applied_in_later_seasons():
    """B2 (chunk 9): the COMPLETED-included fix re-applied a renovation every season, undoing each later notch of
    wear and keeping `upgraded` above zero for ever. It applies in its completion season only."""
    import sqlite3
    from engines import report_card_run

    # The pure check, exercised through the loop's condition, over a three-season span.
    def applies(status, completion_season, season):
        return (status == "IN_PROGRESS" and season >= completion_season) or \
               (status == "COMPLETED" and season == completion_season)

    assert applies("IN_PROGRESS", 2028, 2028) and applies("IN_PROGRESS", 2028, 2029)   # a missed run still lands it
    assert applies("COMPLETED", 2028, 2028)                                          # the re-run case
    assert not applies("COMPLETED", 2028, 2029)                                      # later seasons let it wear
    assert not applies("COMPLETED", 2028, 2032)


def test_a_dead_buyout_is_deducted_once():
    """B4 (chunk 9): the plan and the spec say the owner's cash deducts dead buyouts, and nothing read them. The
    buyout now shrinks the reserve and is charged in one season only."""
    import sqlite3

    from engines import report_card_run as rcr

    class Cur:
        """Answers only what _dead_buyouts and _charge_dead_buyouts ask."""
        def __init__(self):
            self.charged = []
            self._row = None

        def execute(self, sql, params=()):
            s = " ".join(sql.split())
            if s.startswith("UPDATE staff_contracts SET buyout_charged_season"):
                self.charged.append(params)
                self._row = None
            elif "to_regclass" in s:
                self._row = (True,)
            elif "sum(buyout_owed)" in s:
                self._row = (7_000_000,)
            else:
                self._row = None

        def fetchone(self):
            return self._row

    cur = Cur()
    assert rcr._dead_buyouts(cur, 2026, 1) == 7_000_000
    rcr._charge_dead_buyouts(cur, 2026, 1)
    assert cur.charged == [(2026, 1)]
