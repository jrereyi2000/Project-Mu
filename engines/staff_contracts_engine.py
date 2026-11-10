"""
Staff contracts & full control (docs/in_progress/STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md §2.2-2.4, U1).

Pure, offline-testable functions over plain dicts; money in whole dollars; no DB access at import time (the one
cur-based helper, godfather_gate, is a read over staff_contracts and full_control_history that U2 calls inside its
own transaction). The engine owns the data layer and arithmetic only: the salary scales and tiering, the annual
staff payroll and owner cash deduction, contract expiry/rollover, the buyout on firing with its D5 offset clause,
and the hard Godfather-offer rarity gate (the R2 cooldown/ceiling). The enhanced opening appeal, GM hiring search,
API and frontend are other units' work.

Salary scales (§2.2, fixed by the plan). Four markets, each a band the plan names and bounds:

    HC      LEGENDARY    $16.0-22.0M   Super Bowl champion / Tier 1
            ESTABLISHED  $ 8.0-14.0M   multi-year winning HC
            PROMOTION    $ 4.5- 7.5M   rising coordinator first-timer
    GM      ELITE        $ 8.0-12.0M   proven championship architect
            STANDARD     $ 4.0- 7.5M   established personnel director
    COORD   TOP_TIER     $ 2.5- 4.0M   highly sought playcaller
            STANDARD     $ 1.5- 2.4M   position coach promotion

Tiering (salary_tier) is from experience and championships: a Super Bowl champion HC is LEGENDARY, a 3+-season HC
ESTABLISHED, everyone else PROMOTION; a champion GM ELITE, else STANDARD; a coordinator with a championship or
5+ seasons TOP_TIER, else STANDARD. market_salary returns the band midpoint as a concrete whole-dollar market value
(U2 may still name an exact offer). These tier thresholds are not in the plan and are review-only estimates.

Owner cash (§2.2). The pool itself lives in engines/report_card_engine (annual_cash_pool / base_budget /
success_dividend over the four wealth bands), which this module reuses rather than reimplementing. The active staff
payroll lands in owner_cash_ledgers.staff_payroll; the buyout load is summed from staff_contracts.buyout_owed; the
deduction is available_capex = annual_cash_pool - staff_payroll - opex - dead_buyouts, leaving what the owner can
spend on facility CapEx and OpEx perks.

Contracts (§2.4). rollover decrements remaining_years and expires at 0; fire_contract moves a terminated contract's
remaining guaranteed salary (annual_salary * remaining_years) into buyout_owed; the D5 offset clause lets a fired
person's new salary elsewhere reduce the original owner's owed buyout. godfather_eligible holds the R2 hard gate
(at most ACTIVE_FULL_CONTROL_CAP 2 active full-control coaches league-wide and at least GODFATHER_COOLDOWN 5
seasons since the last offer); the owner/candidate/vacancy conditions of §2.3 are U2's.
"""
from __future__ import annotations

from engines.report_card_engine import annual_cash_pool, base_budget, success_dividend  # noqa: F401 (re-exported)

# --------------------------------------------------------------------------------------- salary scales -----
STAFF_SALARY_SCALES = {
    "HC": {
        "LEGENDARY": (16_000_000, 22_000_000),   # Super Bowl champion / Tier 1
        "ESTABLISHED": (8_000_000, 14_000_000),  # Multi-year winning HC
        "PROMOTION": (4_500_000, 7_500_000),    # Rising coordinator first-timer
    },
    "GM": {
        "ELITE": (8_000_000, 12_000_000),       # Proven championship architect
        "STANDARD": (4_000_000, 7_500_000),     # Established personnel director
    },
    "COORD": {
        "TOP_TIER": (2_500_000, 4_000_000),     # Highly sought playcaller
        "STANDARD": (1_500_000, 2_400_000),     # Position coach promotion
    },
}

STAFF_KINDS = ("HC", "GM", "OC", "DC")

# Tiering thresholds (review-only estimates: the bands are fixed, these cutovers are not).
HC_LEGENDARY_CHAMPIONSHIPS = 1
HC_ESTABLISHED_EXPERIENCE = 3
GM_ELITE_CHAMPIONSHIPS = 1
COORD_TOP_CHAMPIONSHIPS = 1
COORD_TOP_EXPERIENCE = 5

# --------------------------------------------------------------------------------------- Godfather gate -----
ACTIVE_FULL_CONTROL_CAP = 2
GODFATHER_COOLDOWN = 5


def _scale_kind(person_kind):
    """The salary-scale key for a contract/role kind: OC and DC share the COORD market."""
    return "COORD" if person_kind in ("OC", "DC") else person_kind


def salary_tier(person_kind, experience=0, championships=0):
    """The market band key for a candidate from his experience (seasons in/near the role) and Super Bowl
    championships. person_kind is HC, GM, OC or DC (KeyError otherwise)."""
    kind = person_kind.upper() if isinstance(person_kind, str) else person_kind
    if kind == "HC":
        if championships >= HC_LEGENDARY_CHAMPIONSHIPS:
            return "LEGENDARY"
        return "ESTABLISHED" if experience >= HC_ESTABLISHED_EXPERIENCE else "PROMOTION"
    if kind == "GM":
        return "ELITE" if championships >= GM_ELITE_CHAMPIONSHIPS else "STANDARD"
    if kind in ("OC", "DC"):
        if championships >= COORD_TOP_CHAMPIONSHIPS or experience >= COORD_TOP_EXPERIENCE:
            return "TOP_TIER"
        return "STANDARD"
    raise KeyError(person_kind)


def market_salary(person_kind, experience=0, championships=0):
    """A concrete whole-dollar market salary for a candidate: the midpoint of his §2.2 band."""
    lo, hi = STAFF_SALARY_SCALES[_scale_kind(person_kind)][salary_tier(person_kind, experience, championships)]
    return (lo + hi) // 2


# --------------------------------------------------------------------------------------- owner cash -----
def staff_payroll(contracts):
    """The active annual staff payroll (§2.2): the sum of annual_salary across HC, GM, OC and DC contracts.
    contracts: an iterable of contract dicts ({person_kind, annual_salary, ...})."""
    return sum(int(c.get("annual_salary") or 0) for c in contracts if c.get("person_kind") in STAFF_KINDS)


def dead_buyouts(contracts):
    """The dead-money buyout load owed on terminated staff contracts: the sum of their buyout_owed."""
    return sum(int(c.get("buyout_owed") or 0) for c in contracts)


def available_capex(cash_pool, staff_payroll=0, opex=0, dead_buyouts=0):
    """Available CapEx = Annual Cash Pool - Staff Payroll - Active OpEx Perks - Dead Buyouts (§2.2)."""
    return int(cash_pool) - int(staff_payroll) - int(opex) - int(dead_buyouts)


def available_capex_for(wealth_band, *, wins=0, home_playoff_games=0, sb_appearance=False,
                        sb_championship=False, top5_popularity=False, staff_payroll=0, opex=0,
                        dead_buyouts=0):
    """Available CapEx from the owner's wealth band and success dividend (report_card_engine.annual_cash_pool,
    reused here), minus the staff payroll, OpEx and dead buyouts."""
    pool = annual_cash_pool(wealth_band, wins, home_playoff_games, sb_appearance, sb_championship, top5_popularity)
    return available_capex(pool, staff_payroll, opex, dead_buyouts)


# --------------------------------------------------------------------------------------- contracts -----
def rollover(contract):
    """A copy of `contract` one season later: remaining_years decremented by 1, floored at 0 (the contract is
    expired once it reaches 0)."""
    out = dict(contract)
    out["remaining_years"] = max(0, int(contract.get("remaining_years") or 0) - 1)
    return out


def is_expired(contract):
    """True once a contract has no remaining years."""
    return int(contract.get("remaining_years") or 0) <= 0


def remaining_guaranteed(contract):
    """The whole-dollar guaranteed salary still owed on a contract: annual_salary * remaining_years."""
    return int(contract.get("annual_salary") or 0) * max(0, int(contract.get("remaining_years") or 0))


def fire_contract(contract):
    """A copy of `contract` as terminated on a firing: the remaining guaranteed salary moves into buyout_owed and
    the remaining years go to 0 (§1 Out: a fired coach simply collects the remaining guaranteed salary)."""
    out = dict(contract)
    out["buyout_owed"] = int(out.get("buyout_owed") or 0) + remaining_guaranteed(contract)
    out["remaining_years"] = 0
    return out


def offset_buyout(contract, new_salary):
    """The D5 offset clause: a copy of a fired contract whose buyout_owed is reduced by the new annual salary the
    person earns with another club, floored at 0."""
    out = dict(contract)
    out["buyout_owed"] = max(0, int(out.get("buyout_owed") or 0) - int(new_salary))
    return out


# --------------------------------------------------------------------------------------- Godfather gate -----
def godfather_ceiling_ok(active_full_control):
    """True while fewer than ACTIVE_FULL_CONTROL_CAP (2) full-control coaches are active league-wide."""
    return int(active_full_control) < ACTIVE_FULL_CONTROL_CAP


def godfather_cooldown_ok(season, last_godfather_season):
    """True once at least GODFATHER_COOLDOWN (5) seasons have elapsed since the last Godfather offer (a None
    last offer = none yet, so it passes)."""
    return last_godfather_season is None or (int(season) - int(last_godfather_season)) >= GODFATHER_COOLDOWN


def godfather_eligible(season, *, active_full_control=0, last_godfather_season=None):
    """The R2 hard rarity gate, pure: at most ACTIVE_FULL_CONTROL_CAP 2 active full-control coaches league-wide
    AND at least GODFATHER_COOLDOWN 5 seasons since the last Godfather offer."""
    return godfather_ceiling_ok(active_full_control) and godfather_cooldown_ok(season, last_godfather_season)


def godfather_gate(cur, season):
    """The cur-based read of the two hard gates over staff_contracts and full_control_history (what U2 calls).
    Returns {eligible, active_full_control, last_godfather_season, ceiling_ok, cooldown_ok}."""
    # A full-control seat is either a staff contract flagged full_control or an open coach-GM tenure (the lore
    # coach-GMs are filed with full_control = false, and a coach-GM's deal that expires is refiled without the flag
    # while he still holds the seat), so counting contracts alone under-read the ceiling.
    cur.execute("""SELECT count(DISTINCT COALESCE(sc.person_id, -g.id)) FROM staff_contracts sc
                   FULL OUTER JOIN (SELECT gp.id, gp.coach_id FROM gm_profiles gp
                                    WHERE gp.end_season IS NULL AND gp.coach_id IS NOT NULL) g
                     ON g.coach_id = sc.person_id AND sc.person_kind = 'GM'
                   WHERE (sc.full_control AND sc.remaining_years > 0) OR g.coach_id IS NOT NULL""")
    active = cur.fetchone()[0]
    cur.execute("SELECT max(season) FROM full_control_history")
    last = cur.fetchone()[0]
    return {"eligible": godfather_eligible(season, active_full_control=active, last_godfather_season=last),
            "active_full_control": active, "last_godfather_season": last,
            "ceiling_ok": godfather_ceiling_ok(active),
            "cooldown_ok": godfather_cooldown_ok(season, last)}
