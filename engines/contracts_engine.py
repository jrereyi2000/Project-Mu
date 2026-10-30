"""
Contracts engine: NFL CBA contract rules and cap maths (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.4; V2_MASTER_PLAN
§3). Pure functions over contract dicts; nothing here touches the database.

A contract dict, in whole dollars (the DB's unit; the save's $10,000 units only at to_madden / from_snapshot):

  {"length", "current_year", "salary_by_year": [...], "bonus_by_year": [...], "guaranteed", "contract_type",
   "signed_season", "last_restructure_season", "drafted_round"}            optional: "ltbe_by_year",
                                                                           "guaranteed_by_year", "signed_week"

  year             every `year` argument is a contract-year index, 0-based like Madden's CurrentYear (not a season).
  bonus_by_year    the signing bonus already prorated per year, as the save stores it.
  guaranteed       guaranteed base salary still owed, consumed from the year asked about forward (signing bonus is
                   always "guaranteed" and lives in bonus_by_year). "guaranteed_by_year" overrides it per year.

  cap hit_t        = base_t + prorated bonus_t + LTBE incentives_t
  dead money       pre-June 1:  every remaining year's proration (t..end) + this year's guaranteed base
                   post-June 1: this year's proration + guaranteed base; the rest of the proration hits next year
  cut savings_t    = cap hit_t - dead money_t (negative: cutting costs cap room this year)
  proration        a bonus spreads evenly over min(years left, PRORATION_MAX_YEARS = 5)
  restructure      converts up to 90% of a year's base into bonus, prorated over the years left (max 5)

Seasons and weeks follow league_state: weeks 1-18 regular season, 19-23 playoffs, 24+ the offseason of that same
season (its stages in OFFSEASON_STAGE_WEEKS order). A season's offseason therefore comes after its week 23.

Money constants come from the 2024 league year (cap $255.4M) and scale with the cap a function is given; all of
them are estimates of the real CBA tables unless a comment says otherwise.
"""
import math
import random

# ---------------------------------------------------------------- calendar ----
REGULAR_SEASON_WEEKS = 18
FINAL_REGULAR_WEEK = 18                 # 1-year deals and tagged players may negotiate from here
POSTSEASON_START = 19                   # week 19 onward: the regular season is over
# Offseason pseudo-weeks (league_state goes past 23 in the offseason). Estimate of the stage order league_office
# (§2.6) walks; it owns the real mapping and can pass these numbers.
OFFSEASON_STAGE_WEEKS = {"OFFSEASON_START": 24, "RESIGN": 25, "FREE_AGENCY": 26, "DRAFT": 29, "UDFA": 30,
                         "CUTDOWN": 31, "PRESEASON": 32}
TAG_DEADLINE_WEEK = OFFSEASON_STAGE_WEEKS["UDFA"]   # "mid-July": the last stage before training camp

TAG_TYPES = ("FRANCHISE_EXCL", "FRANCHISE_NONEXCL", "TRANSITION")
TENDER_TYPES = ("ERFA", "RFA_FIRST", "RFA_SECOND", "RFA_ROFR")
MONEY_UNIT = 10_000                     # Madden stores money in $10,000 units (pipeline/load_save.py)


# ------------------------------------------------------------- rookie scale ----
def _rookie_scale(ref_cap_total_top=41_300_000, ref_total_last=4_000_000, decay=26.0, picks=224):
    """Four-year total value by overall pick at the reference cap. Estimate fitted by eye to the 2024 slotting
    (#1 ~ $41.3M, #32 ~ $15M, #64 ~ $7.5M, #100 ~ $5M, last pick ~ $4.1M): last + (top - last) e^-(p-1)/26.
    Strictly decreasing in whole dollars."""
    return {p: int(round(ref_total_last + (ref_cap_total_top - ref_total_last) * math.exp(-(p - 1) / decay)))
            for p in range(1, picks + 1)}


CBA = {
    "REFERENCE_CAP": 255_400_000,       # 2024 salary cap (real); every dollar table below is at this cap
    "DRAFT_PICKS": 224,
    "PICKS_PER_ROUND": 32,              # compensatory picks ignored: round = (pick - 1) // 32 + 1
    "ROOKIE_YEARS": 4,
    "ROOKIE_SCALE": _rookie_scale(),    # overall pick -> 4-year total value at REFERENCE_CAP (estimate)
    "ROOKIE_ESCALATOR": 1.05,           # rookie base salaries rise ~5% a year (engines/trade_value.py uses the same)
    # Signing-bonus share of a rookie deal: ~65% at #1 falling to ~5% on day 3 (estimate).
    "ROOKIE_BONUS_SHARE": (0.05, 0.60, 40.0),            # floor + top e^-(p-1)/40
    # League minimum salary by accrued seasons (2024 table, real); 7+ uses 7.
    "MIN_SALARY": {0: 795_000, 1: 915_000, 2: 985_000, 3: 1_055_000, 4: 1_125_000, 5: 1_125_000, 6: 1_125_000,
                   7: 1_210_000},
    # Tags: average of the top N salaries at the position, or TAG_PRIOR_RAISE x last year's salary if higher.
    "TAG_TOP_N": {"FRANCHISE_EXCL": 5, "FRANCHISE_NONEXCL": 5, "TRANSITION": 10},
    "TAG_PRIOR_RAISE": 1.2,
    "TAG_HISTORY_YEARS": 5,             # non-exclusive / transition: cap-share average over the last 5 years
    # 5th-year option tiers (V2 §3.2): tier -> (rank from, rank to) of the position's salaries, 1-based inclusive.
    "OPTION_TIERS": {1: (1, 5), 2: (1, 10), 3: (3, 20), 4: (3, 25)},
    "OPTION_PLAYTIME_SHARE": 0.75,      # tier 3: 75%+ snaps in 2 of the 3 seasons
    # RFA tenders as a share of the cap (2024: $6.008M / $4.218M / $2.985M at $255.4M; real values, as shares).
    "TENDER_CAP_SHARE": {"RFA_FIRST": 6_008_000 / 255_400_000, "RFA_SECOND": 4_218_000 / 255_400_000,
                         "RFA_ROFR": 2_985_000 / 255_400_000},
    "ERFA_MAX_ACCRUED": 2,              # ERFA: 2 or fewer accrued seasons
    "RFA_ACCRUED": 3,                   # RFA: exactly 3 accrued seasons
    "RESTRUCTURE_MAX_SHARE": 0.9,       # up to 90% of a year's base converts to bonus
    "PRORATION_MAX_YEARS": 5,
    # Cap growth a year: normal(mean, sd) clamped to [lo, hi]. Estimate: the 2013-2024 cap grew ~6.5% a year
    # outside the 2021 COVID dip.
    "CAP_GROWTH": {"mean": 0.065, "sd": 0.025, "lo": -0.02, "hi": 0.12},
    "CAP_ROUND": 100_000,               # caps are announced in round $100k figures
}


# ---------------------------------------------------------------- helpers ----
def _at(values, i):
    return values[i] if values is not None and 0 <= i < len(values) else 0


def _scale(amount, cap):
    """A reference-cap dollar amount at another cap, whole dollars."""
    return int(round(amount * cap / CBA["REFERENCE_CAP"]))


def minimum_salary(accrued_seasons, cap=None):
    """League minimum for a player with this many accrued seasons, scaled to `cap` (reference cap if None)."""
    m = CBA["MIN_SALARY"][min(max(int(accrued_seasons), 0), max(CBA["MIN_SALARY"]))]
    return m if cap is None else _scale(m, cap)


def _prorate(amount, years):
    """Split a whole-dollar amount evenly over `years` years; the remainder goes to the first year so it sums
    exactly."""
    years = max(1, years)
    each, rem = divmod(int(amount), years)
    return [each + (rem if i == 0 else 0) for i in range(years)]


def _guaranteed_base(contract, year):
    """Guaranteed base salary of `year`: guaranteed_by_year if given, else the guaranteed balance up to that
    year's base."""
    by_year = contract.get("guaranteed_by_year")
    if by_year is not None:
        return min(_at(by_year, year), _at(contract["salary_by_year"], year))
    return min(int(contract.get("guaranteed") or 0), _at(contract["salary_by_year"], year))


def _dead_split(contract, year, post_june1=False):
    """(dead money this year, dead money next year) for a release in contract year `year`."""
    length = contract["length"]
    if year >= length:
        return 0, 0
    bon = contract["bonus_by_year"]
    this_year = _at(bon, year) + _guaranteed_base(contract, year)
    later = sum(_at(bon, i) for i in range(year + 1, length))
    return (this_year, later) if post_june1 else (this_year + later, 0)


def _top_avg(salaries, lo, hi):
    """Average of ranks lo..hi (1-based, inclusive) of a salary list sorted high to low; short lists use what
    there is."""
    ranked = sorted((s for s in salaries if s), reverse=True)[lo - 1:hi]
    return sum(ranked) / len(ranked) if ranked else 0.0


def _position_years(salaries_by_position, position):
    """The position's salary lists, oldest to newest. A plain list is this year only; a list of lists is one
    list per year aligned to the tail of cap_history."""
    vals = salaries_by_position.get(position) or []
    if vals and isinstance(vals[0], (list, tuple)):
        return [list(v) for v in vals]
    return [list(vals)]


# ---------------------------------------------------------------- cap maths ----
def cap_hit(contract, year):
    """Cap hit of contract year `year`: base + prorated bonus + LTBE incentives; 0 outside the contract."""
    if year < 0 or year >= contract["length"]:
        return 0
    return (_at(contract["salary_by_year"], year) + _at(contract["bonus_by_year"], year)
            + _at(contract.get("ltbe_by_year"), year))


def dead_money(contract, year, post_june1=False):
    """Dead money charged to `year`'s cap by a release in that year. Pre-June 1: all remaining proration (year..end)
    + this year's guaranteed base. Post-June 1: this year's proration + guaranteed base only (the rest is next
    year's charge)."""
    return _dead_split(contract, year, post_june1)[0]


def cut_savings(contract, year, post_june1=False):
    """Cap room a release frees this year: cap hit - dead money. Negative when the acceleration outweighs the hit."""
    return cap_hit(contract, year) - dead_money(contract, year, post_june1)


# ----------------------------------------------------------- extension rules ----
def _after_regular_season(season, week, target_season):
    """True from the end of target_season's regular season (week 19+, including its offseason) onward."""
    return season > target_season or (season == target_season and week >= POSTSEASON_START)


def extension_eligible(contract, player, season, week):
    """(eligible, reason) for an extension or renegotiation at (season, week) (V2 §3.1):
      tagged (FRANCHISE_* / TRANSITION): until TAG_DEADLINE_WEEK of the offseason, then locked until week 18;
      drafted rookie deal (ROOKIE): from the end of the 3rd season's regular season (offseason entering year 4);
      undrafted rookie deal (UDFA): from the end of the 2nd season's regular season;
      1-year deals (incl. tenders, minimums): from week 18;
      veterans: in the final contract year, or once 12 months have passed since signing or the last restructure.
    player may carry "rookie_season" (first NFL season); the contract's signed_season is the fallback."""
    ctype = contract.get("contract_type") or "STANDARD"
    length, cur_year = contract["length"], contract.get("current_year", 0)

    if ctype in TAG_TYPES:
        if week > OFFSEASON_STAGE_WEEKS["OFFSEASON_START"] - 1:            # offseason stages
            if week <= TAG_DEADLINE_WEEK:
                return True, "tagged: the multi-year window is open until training camp"
            return False, "tagged: the multi-year window closed at training camp; plays on the tag"
        if week >= FINAL_REGULAR_WEEK:
            return True, "tagged: the regular season is over, a long-term deal may be signed"
        return False, "tagged: playing on the tag; a long-term deal waits until week 18"

    if ctype in ("ROOKIE", "UDFA"):
        rookie_season = player.get("rookie_season") or contract.get("signed_season")
        if rookie_season is None:
            return False, "rookie deal without a rookie season: cannot place the window"
        seasons = 3 if ctype == "ROOKIE" else 2
        open_after = rookie_season + seasons - 1
        if _after_regular_season(season, week, open_after):
            return True, f"{ctype.lower()} deal: eligible after year {seasons} ({open_after} regular season)"
        return False, f"{ctype.lower()} deal: blocked until the end of the {open_after} regular season (year {seasons})"

    if ctype == "FIFTH_YEAR_OPTION":
        return True, "5th-year option: past year 3, may extend"

    if length <= 1 or ctype in ("RFA_TENDER", "ERFA_TENDER"):
        if week >= FINAL_REGULAR_WEEK:
            return True, "1-year deal: re-signing open from week 18"
        return False, "1-year deal: re-signing opens in week 18"

    if cur_year >= length - 1:
        return True, "veteran: final contract year"
    signed = contract.get("signed_season")
    restructured = contract.get("last_restructure_season")
    if restructured is not None and (signed is None or restructured >= signed):
        anchor_season, anchor_week = restructured, 0
    else:
        anchor_season, anchor_week = signed, contract.get("signed_week") or 0
    if anchor_season is None:
        return False, "veteran: unknown signing season, not in the final year"
    if (season, week) >= (anchor_season + 1, anchor_week):
        return True, "veteran: 12 months since signing or the last restructure"
    return False, f"veteran: 12 months not yet passed since {anchor_season} and not in the final year"


# ------------------------------------------------------------- instruments ----
def fifth_year_option(player, salaries_by_position):
    """{tier, salary} of a 1st-rounder's 5th-year option (V2 §3.2), from this year's salaries at his position:
      tier 1 (2+ Pro Bowls) franchise tag value (top-5 average); tier 2 (1 Pro Bowl) transition value (top 10);
      tier 3 (75%+ snaps in 2 of 3 seasons) average of ranks 3-20; tier 4 average of ranks 3-25.
    player: {"position", "drafted_round", "pro_bowls", "snap_shares": [3 seasons] or "playtime": bool}.
    Not a 1st-rounder: {"tier": None, "salary": 0}."""
    if player.get("drafted_round") != 1:
        return {"tier": None, "salary": 0}
    shares = player.get("snap_shares") or []
    playtime = player.get("playtime")
    if playtime is None:
        playtime = sum(1 for s in shares[-3:] if s >= CBA["OPTION_PLAYTIME_SHARE"]) >= 2
    pro_bowls = player.get("pro_bowls") or 0
    tier = 1 if pro_bowls >= 2 else 2 if pro_bowls == 1 else 3 if playtime else 4
    lo, hi = CBA["OPTION_TIERS"][tier]
    current = _position_years(salaries_by_position, player["position"])[-1]
    return {"tier": tier, "salary": int(round(_top_avg(current, lo, hi)))}


def tag_value(kind, position, salaries_by_position, prior_salary, cap_history):
    """One-year tag salary (V2 §3.2), at least TAG_PRIOR_RAISE (120%) of the player's prior salary:
      FRANCHISE_EXCL     average of this year's top 5 salaries at the position;
      FRANCHISE_NONEXCL  top-5 average as a share of each year's cap, averaged over the last 5 years, x this cap;
      TRANSITION         the same with the top 10.
    salaries_by_position[position] is this year's list, or a list of per-year lists (oldest first) aligned to the
    tail of cap_history (oldest first, last = this year's cap). With one year of salaries the cap-share average is
    simply this year's top-N average."""
    if kind not in TAG_TYPES:
        raise ValueError(f"unknown tag kind {kind!r}")
    n = CBA["TAG_TOP_N"][kind]
    years = _position_years(salaries_by_position, position)
    floor = CBA["TAG_PRIOR_RAISE"] * (prior_salary or 0)
    if kind == "FRANCHISE_EXCL" or len(years) == 1 or not cap_history:
        base = _top_avg(years[-1], 1, n)
    else:
        years = years[-CBA["TAG_HISTORY_YEARS"]:]
        caps = list(cap_history)[-len(years):]
        years = years[-len(caps):]
        shares = [_top_avg(s, 1, n) / c for s, c in zip(years, caps) if c]
        base = (sum(shares) / len(shares)) * caps[-1] if shares else _top_avg(years[-1], 1, n)
    return int(round(max(base, floor)))


def tender_value(kind, accrued_seasons, cap):
    """One-year tender for a restricted player at this cap (V2 §3.2):
      ERFA (<= 2 accrued seasons) the league minimum for his accrued seasons;
      RFA_FIRST / RFA_SECOND / RFA_ROFR (exactly 3 accrued) the tender's share of the cap.
    The real CBA also floors RFA tenders at 110% of the prior salary; callers apply that if they need it.
    Raises ValueError for an unknown kind or a player the tender cannot apply to."""
    if kind not in TENDER_TYPES:
        raise ValueError(f"unknown tender kind {kind!r}")
    if kind == "ERFA":
        if accrued_seasons > CBA["ERFA_MAX_ACCRUED"]:
            raise ValueError(f"ERFA needs <= {CBA['ERFA_MAX_ACCRUED']} accrued seasons, got {accrued_seasons}")
        return minimum_salary(accrued_seasons, cap)
    if accrued_seasons != CBA["RFA_ACCRUED"]:
        raise ValueError(f"RFA tenders need {CBA['RFA_ACCRUED']} accrued seasons, got {accrued_seasons}")
    return int(round(CBA["TENDER_CAP_SHARE"][kind] * cap))


def restructure(contract, year, amount):
    """A new contract with `amount` of year `year`'s base converted into bonus, prorated evenly over the years left
    (year..end, at most PRORATION_MAX_YEARS). Refuses (ValueError) a non-positive amount, one over
    RESTRUCTURE_MAX_SHARE (90%) of that year's base, or a year outside the contract. Guaranteed base drops by the
    guaranteed part converted (it is now paid as bonus); last_restructure_season = signed_season + year when the
    signing season is known."""
    length = contract["length"]
    if not 0 <= year < length:
        raise ValueError(f"year {year} is outside the {length}-year contract")
    base = _at(contract["salary_by_year"], year)
    amount = int(amount)
    if amount <= 0:
        raise ValueError("restructure amount must be positive")
    if amount > CBA["RESTRUCTURE_MAX_SHARE"] * base:
        raise ValueError(f"restructure of {amount} exceeds {CBA['RESTRUCTURE_MAX_SHARE']:.0%} of the {base} base")
    years = min(length - year, CBA["PRORATION_MAX_YEARS"])
    out = dict(contract)
    sal = list(contract["salary_by_year"]) + [0] * max(0, length - len(contract["salary_by_year"]))
    bon = list(contract["bonus_by_year"]) + [0] * max(0, length - len(contract["bonus_by_year"]))
    sal[year] -= amount
    for i, part in enumerate(_prorate(amount, years)):
        bon[year + i] += part
    out["salary_by_year"], out["bonus_by_year"] = sal, bon
    if contract.get("guaranteed_by_year") is not None:
        gby = list(contract["guaranteed_by_year"])
        if year < len(gby):
            gby[year] = max(0, gby[year] - amount)
        out["guaranteed_by_year"] = gby
    else:
        converted = min(amount, _guaranteed_base(contract, year))
        out["guaranteed"] = max(0, int(contract.get("guaranteed") or 0) - converted)
    if contract.get("signed_season") is not None:
        out["last_restructure_season"] = contract["signed_season"] + year
    return out


def rookie_contract(overall_pick, season, cap):
    """The slotted 4-year deal for an overall pick (1-224), scaled from the reference cap to `cap`. The signing
    bonus share falls with the pick (ROOKIE_BONUS_SHARE) and prorates over the 4 years; base salaries escalate 5% a
    year and never drop below the minimum for 0-3 accrued seasons. Guaranteed base (estimate of practice): 1st
    round all four years, 2nd round year 1, later rounds none."""
    if not 1 <= overall_pick <= CBA["DRAFT_PICKS"]:
        raise ValueError(f"overall pick {overall_pick} outside 1-{CBA['DRAFT_PICKS']}")
    years = CBA["ROOKIE_YEARS"]
    total = _scale(CBA["ROOKIE_SCALE"][overall_pick], cap)
    floor, top, decay = CBA["ROOKIE_BONUS_SHARE"]
    bonus = int(round(total * (floor + top * math.exp(-(overall_pick - 1) / decay))))
    weights = [CBA["ROOKIE_ESCALATOR"] ** i for i in range(years)]
    base_total = total - bonus
    salary = [max(minimum_salary(i, cap), int(round(base_total * w / sum(weights)))) for i, w in enumerate(weights)]
    rnd = (overall_pick - 1) // CBA["PICKS_PER_ROUND"] + 1
    guaranteed = sum(salary) if rnd == 1 else salary[0] if rnd == 2 else 0
    return {"length": years, "current_year": 0, "salary_by_year": salary, "bonus_by_year": _prorate(bonus, years),
            "guaranteed": guaranteed, "contract_type": "ROOKIE", "signed_season": season,
            "last_restructure_season": None, "drafted_round": rnd}


def next_cap(prev_cap, season, seed):
    """Next season's cap: prev x (1 + growth), growth ~ normal(CAP_GROWTH mean, sd) clamped to [lo, hi], drawn from
    a generator seeded by (seed, season) so a seed always gives the same cap path. Rounded to CAP_ROUND."""
    g = CBA["CAP_GROWTH"]
    rng = random.Random(f"contracts_engine.next_cap:{seed}:{season}")
    growth = min(max(rng.gauss(g["mean"], g["sd"]), g["lo"]), g["hi"])
    step = CBA["CAP_ROUND"]
    return int(round(prev_cap * (1 + growth) / step)) * step


# -------------------------------------------------------------- save units ----
def to_madden(contract):
    """The save writer's contract block (contract_edits.mjs): {Length, CurrentYear, SalaryYear0.., BonusYear0..} in
    $10,000 units, rounded to the nearest unit. One SalaryYear/BonusYear per slot the contract carries (at least
    Length). LTBE incentives have no Madden field and are not written."""
    sal, bon = contract["salary_by_year"], contract["bonus_by_year"]
    n = max(contract["length"], len(sal), len(bon))
    unit = lambda dollars: int(math.floor(dollars / MONEY_UNIT + 0.5))
    out = {"Length": contract["length"], "CurrentYear": contract.get("current_year", 0)}
    for i in range(n):
        out[f"SalaryYear{i}"] = unit(_at(sal, i))
    for i in range(n):
        out[f"BonusYear{i}"] = unit(_at(bon, i))
    return out


def from_snapshot(c):
    """A save snapshot's contract block (pc_save_reader.mjs readContract: length, current_year, salary_by_year,
    bonus_by_year in $10,000 units) -> a contract dict in whole dollars. Fields the snapshot doesn't carry take
    their defaults (STANDARD, nothing guaranteed beyond the bonus) unless the block has them."""
    def dollars(values):
        return [int(v or 0) * MONEY_UNIT for v in (values or [])]
    return {
        "length": c["length"],
        "current_year": c.get("current_year") or 0,
        "salary_by_year": dollars(c.get("salary_by_year")),
        "bonus_by_year": dollars(c.get("bonus_by_year")),
        "guaranteed": c.get("guaranteed", 0),
        "contract_type": c.get("contract_type", "STANDARD"),
        "signed_season": c.get("signed_season"),
        "last_restructure_season": c.get("last_restructure_season"),
        "drafted_round": c.get("drafted_round"),
    }
