"""
Project Mu contracts onto teleported players (docs/PC_MIGRATION_PLAN.md §4.3 Step 4). pipeline/teleport.py pairs each
Project Mu player with a donor; this turns the player's ACTIVE player_contracts row (+ player_contract_years) into the
fields of the donor's own PlayerContract record, which pipeline/save_reader/save_writer.mjs edits in place (an edit's
"contract" block). Reads the database, never writes it.

The rule (the reverse of pipeline/load_save.contract_values, so a written contract reads back as the same cap hits):
  * Length = length, CurrentYear = length - years_remaining (+ --contract-years-add: years Madden's league is ahead of
    the Project Mu season). Year i of the deal is calendar year season + i - CurrentYear.
  * Cap hits of the remaining years come from player_contract_years (the Team Salary screen shows five, so a 6-7 year
    deal's later years repeat the last one shown). Years already played get what is left of total_value after the
    remaining hits, split evenly (0 when nothing is left). Madden money is $10,000 units: each hit is rounded first.
  * Signing bonus is prorated evenly: bonus / Length per year, over at most 7 years (BonusYear0-6). A year whose hit
    is below that share takes bonus = the hit and the rest is spread evenly over the other years, so the bonus total
    is kept whenever the hits can hold it. Salary = hit - bonus, so salary + bonus is exactly the rounded hit. Madden
    then charges the bonus still to come as dead money on release (load_save's cut_penalty rule).
  * NoTradeClause from Project Mu; VoidYears, ExtraYearOption, TimesRestructured and IsSalaryGuaranteedYears are
    cleared (Project Mu has no guarantees, and the donor's bits describe the donor's deal); SalaryDistribution Custom.
    Unused SalaryYear / BonusYear slots are zeroed.
  * Not written (the donor keeps their contract, counted in the report): players with no ACTIVE contract, deals that
    end before Madden's current year, and donors with no PlayerContract record (practice squad; giving one a record
    is a new record plus a reference change). Incentives (a reference) stay the donor's.
"""
import math
from collections import Counter

MONEY_UNIT = 10_000          # Madden contract money (pipeline/load_save.MONEY_UNIT)
SALARY_SLOTS, BONUS_SLOTS = 10, 7
MAX_LENGTH = 10              # PlayerContract.Length's schema maximum
UNIT_MAX = 16_383            # SalaryYear / BonusYear schema maximum ($163.83M)


def units(dollars):
    """Whole dollars -> $10K units, half up."""
    return int(math.floor(dollars / MONEY_UNIT + 0.5))


def cap_hits(contract, years):
    """[cap hit in dollars] for deal years 0..length-1 (see the module docstring), or None for an empty deal."""
    length = contract["length"] or 0
    remaining = min(contract["years_remaining"] or 0, length)
    if length < 1 or remaining < 1:
        return None
    season = contract["season"]
    shown = {y: h or 0 for y, h in (years or {}).items()}
    last = None
    future = []
    for k in range(remaining):
        hit = shown.get(season + k)
        if hit is None:
            hit = last if last is not None else (contract["total_value"] or 0) / length
        future.append(hit)
        last = hit
    played = length - remaining
    if not played:
        return future
    left = max((contract["total_value"] or 0) - sum(future), 0)
    return [left / played] * played + future


def prorate_bonus(bonus, hit_units):
    """[bonus per year] ($10K units): `bonus` split evenly over the first 7 years, never above a year's hit; what a
    small year can't take goes evenly to the others (the odd units to the earliest years). Sums to `bonus` unless the
    hits can't hold it."""
    out = [0] * len(hit_units)
    open_ = list(range(min(len(hit_units), BONUS_SLOTS)))
    left = bonus
    while open_ and left > 0:
        share = -(-left // len(open_))                        # ceiling: a year that can't take it is capped
        full = [i for i in open_ if hit_units[i] - out[i] <= share]
        if not full:
            base, extra = divmod(left, len(open_))
            for n, i in enumerate(open_):
                out[i] += base + (1 if n < extra else 0)
            break
        for i in full:
            left -= hit_units[i] - out[i]
            out[i] = hit_units[i]
            open_.remove(i)
    return out


def madden_contract(contract, years, years_add=0):
    """(PlayerContract fields, None) or (None, reason). contract: an ACTIVE player_contracts row as a dict (length,
    years_remaining, total_value, bonus, no_trade_clause, season); years: {calendar year: cap hit} from
    player_contract_years."""
    hits = cap_hits(contract, years)
    if hits is None:
        return None, "empty"
    length = len(hits)
    if length > MAX_LENGTH:
        return None, "too long"
    current = length - min(contract["years_remaining"], length) + years_add
    if current >= length:
        return None, "expired"
    hit_units = [units(h) for h in hits]
    bonus = prorate_bonus(units(contract["bonus"] or 0), hit_units)
    salary = [hit_units[i] - bonus[i] for i in range(length)]
    if max(salary + bonus) > UNIT_MAX:
        return None, "over the field maximum"
    fields = {"Length": length, "CurrentYear": current}
    fields.update({f"SalaryYear{i}": salary[i] if i < length else 0 for i in range(SALARY_SLOTS)})
    fields.update({f"BonusYear{i}": bonus[i] if i < length else 0 for i in range(BONUS_SLOTS)})
    fields.update({"NoTradeClause": bool(contract["no_trade_clause"]), "VoidYears": 0, "ExtraYearOption": False,
                   "TimesRestructured": 0, "IsSalaryGuaranteedYears": 0, "SalaryDistribution": "Custom"})
    return fields, None


def load_contracts(cur, season):
    """{player_id: (contract dict, {year: cap hit})} for the season's ACTIVE contracts."""
    cur.execute("""
        SELECT c.player_id, c.season, c.length, c.years_remaining, c.total_value, c.bonus, c.no_trade_clause,
               y.year, y.cap_hit
        FROM player_contracts c LEFT JOIN player_contract_years y ON y.contract_id = c.id
        WHERE c.season = %s AND c.status = 'ACTIVE' ORDER BY c.player_id, y.year""", (season,))
    out = {}
    for pid, season_, length, remaining, total, bonus, ntc, year, hit in cur.fetchall():
        contract, years = out.setdefault(pid, ({"season": season_, "length": length, "years_remaining": remaining,
                                                "total_value": total, "bonus": bonus, "no_trade_clause": ntc}, {}))
        if year is not None:
            years[year] = hit
    return out


def add_contracts(edits, report, contracts, snapshot, years_add=0):
    """Adds a "contract" block to each pair's edit (report["pairs"] gives pgid -> player_id) and returns the counts
    that go into the teleport report under "contracts"."""
    has_record = {p["pgid"] for p in snapshot["players"] if p.get("contract")}
    player_of = {pair["donor_pgid"]: pair["player_id"] for pair in report["pairs"]}
    counts = Counter()
    for edit in edits["players"]:
        found = contracts.get(player_of.get(edit["pgid"]))
        if found is None:
            counts["no_project_mu_contract"] += 1
        elif edit["pgid"] not in has_record:
            counts["donor_without_contract_record"] += 1
        else:
            fields, reason = madden_contract(*found, years_add=years_add)
            if fields is None:
                counts[reason.replace(" ", "_")] += 1
            else:
                edit["contract"] = fields
                counts["written"] += 1
    counts["donor_contract_kept"] = len(edits["players"]) - counts["written"]
    return dict(counts)
