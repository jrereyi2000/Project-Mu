"""
Transactions engine: the team side of every transaction for all 32 teams (docs/in_progress/V2_CORE_BUILD_PLAN.md
§2.4, U11; docs/done/LEAGUE_OFFICE_PLAN.md §4.4-§4.6, §4.11).

Re-signings, extensions, tags, tenders and 5th-year options; CPU free-agent offers; cut-down; waivers; the practice
squad; rookie deals. Each public function reads the league with the small fetch_* functions, decides with a pure
plan_* function over plain dicts, and returns move dicts that pipeline/league_office.py turns into writer edits:

  {"kind": "SIGN"|"RELEASE"|"CONTRACT"|"EXTENSION"|"TAG"|"OPTION"|"PS_PROMOTE"|"PS_DEMOTE"|"WAIVER_CLAIM"|"LET_GO",
   "player_id", "team_id", "reason", ...details}

`contract` (where a move carries one) is a contracts_engine contract dict, whole dollars, current_year 0 in the
league year the move counts against. cpu_offers returns offer dicts (run_wave's shape) instead and stores them in
contract_offers; waivers stores every claim in waiver_claims.

League year. `season` is league_state's season. Offseason work (weeks 24+, the stages of
contracts_engine.OFFSEASON_STAGE_WEEKS) counts against the next league year, T = season + 1; in-season work against
T = season. The DB keeps contracts by scan season with cap hits per calendar year, so a contract's year index at T is
T - its first season, and a contract that ends in `season` is expiring at the offseason stages.

Values (engines/trade_value.py): V_T = player_value(...) with a hypothetical deal at the asking price; asking price
= W(ovr, group) x age factor (1.0 to age 29, -6% a year after, floor 0.6) x the dev premium the market sees,
floored at the league minimum for his accrued seasons; years = free_agency_engine.ideal_years(age). Team direction
enters through trade_value's discounts, needs through utility(); GM persona inputs are neutral (D4: no position
bias, youth_bias 0). Cut-down, practice squad and waiver ranking use the one-season on-field value W x u x dev,
rounded to $10,000, so near-equal players tie and the cap decides.

Randomness: sha256 of the seed string only (no global random state), so a seed always gives the same moves.
Every threshold below is an estimate unless a comment says otherwise.
"""
import hashlib

from engines import contracts_engine as ce
from engines import trade_value as tv
from engines.free_agency_engine import ideal_years, wave_tier
from engines.gm_direction import (SLOTS, league_lines, positional_needs, position_group, slot_ovrs,
                                  starter_slots)

ROSTER_LIMIT = 65                       # active roster, the roster-size mod (JR, D5)
PRACTICE_SQUAD_LIMIT = 12               # the save's PracticeSquad list holds 12 slots (LEAGUE_OFFICE_PLAN §4.6)
PS_MAX_ACCRUED = 2                      # NFL: 2 or fewer accrued seasons are practice-squad eligible
PS_VETERAN_SLOTS = 4                    # NFL 2024 allows 6 of 16 with any accrued seasons; scaled to 12 slots
OFFSEASON_START_WEEK = ce.OFFSEASON_STAGE_WEEKS["OFFSEASON_START"]
RESIGN_WEEK = ce.OFFSEASON_STAGE_WEEKS["RESIGN"]
OFFSEASON_STAGES = set(ce.OFFSEASON_STAGE_WEEKS)

# asking price
ASK_AGE_PIVOT, ASK_AGE_STEP, ASK_AGE_FLOOR = 29, 0.06, 0.6
# re-signing
CAP_BUFFER_SHARE = 0.02                 # every signing leaves 2% of the cap free
TAG_MIN_OVR = 80                        # tag candidates: 80+ OVR
TEST_MARKET_P = 0.35                    # chance an 80+ unrestricted player wants to test the market (neutral persona)
TAG_KIND = "FRANCHISE_NONEXCL"
RFA_LEVELS = ((80, "RFA_FIRST"), (72, "RFA_SECOND"), (0, "RFA_ROFR"))
EXTEND_MIN_SURPLUS = 0.15               # extension surplus must reach 15% of the new money
EXTEND_MAX_AGE = 30                     # no early extensions for players past 30 in the league year
RESIGN_BONUS_SHARE, RESIGN_GUAR_SHARE, RESIGN_GUAR_SHARE_VET = 0.25, 0.40, 0.60
# free agency
OFFERS_PER_WAVE = 4                     # a team's offers per wave, at most (never more than its roster spots)
OFFER_NEED_PREMIUM = 0.10               # top need pays up to +10% over the asking price
OFFER_NOISE = 0.05                      # seeded +-5% on each offer
SITUATIONAL_WITHIN = 4                  # within 4 OVR of the weakest starter = a role (free_agency_engine's rule)
VET_AGE = 30


# ---------------------------------------------------------------- helpers ----
def _u01(*parts):
    h = hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


def _noise(width, *parts):
    return (2 * _u01(*parts) - 1) * width


def target_season(season, week=None, stage=None):
    """The league year a move counts against: season + 1 in the offseason (week 24+ or an offseason stage, and when
    neither is given), else season."""
    if stage is not None:
        return season + 1 if stage in OFFSEASON_STAGES else season
    if week is None:
        return season + 1
    return season + 1 if week >= OFFSEASON_START_WEEK else season


def accrued(p, league):
    """Accrued seasons in the league year: years_of_experience counts seasons before `season`."""
    return max(0, (p.get("yoe") or 0) + league["target"] - league["season"])


def contract_year(p, season):
    """Index of `season` in the player's contract (0-based), or None outside it."""
    c = p.get("contract")
    if not c or p.get("contract_start") is None:
        return None
    y = season - p["contract_start"]
    return y if 0 <= y < c["length"] else None


def end_season(p):
    c = p.get("contract")
    if not c or p.get("contract_start") is None:
        return None
    return p["contract_start"] + c["length"] - 1


def hit_at(p, season):
    y = contract_year(p, season)
    return 0 if y is None else ce.cap_hit(p["contract"], y)


def make_contract(aav, years, ctype="STANDARD", signed_season=None, bonus_share=RESIGN_BONUS_SHARE,
                  guar_share=RESIGN_GUAR_SHARE, drafted_round=None):
    """A flat contracts_engine dict: total = aav x years, the bonus prorated over min(years, 5), base split evenly,
    guaranteed = guar_share of the base."""
    years = max(1, int(years))
    total = int(round(aav * years))
    bonus = int(round(total * bonus_share))
    pro = ce._prorate(bonus, min(years, ce.CBA["PRORATION_MAX_YEARS"]))
    bon = pro + [0] * (years - len(pro))
    sal = ce._prorate(total - bonus, years)
    return {"length": years, "current_year": 0, "salary_by_year": sal, "bonus_by_year": bon,
            "guaranteed": int(round((total - bonus) * guar_share)), "contract_type": ctype,
            "signed_season": signed_season, "last_restructure_season": None, "drafted_round": drafted_round}


def contract_from_db(row):
    """A player_contracts row (with its {calendar year: cap hit} years) -> (contract dict, first season). bonus is
    the total signing bonus, prorated evenly; base = cap hit - proration (or the even split when a year is
    missing)."""
    length = max(1, int(row.get("length") or row.get("years_remaining") or 1))
    rem = int(row.get("years_remaining") or length)
    end = row["season"] + rem - 1
    start = end - length + 1
    bonus = int(row.get("bonus") or 0)
    pro = ce._prorate(bonus, min(length, ce.CBA["PRORATION_MAX_YEARS"]))
    bon = pro + [0] * (length - len(pro))
    total = int(row.get("total_value") or 0)
    years = row.get("years") or {}
    sal = []
    for i in range(length):
        hit = years.get(start + i)
        sal.append(max(0, int(hit) - bon[i]) if hit is not None else max(0, (total - bonus) // length))
    c = {"length": length, "current_year": max(0, row["season"] - start), "salary_by_year": sal,
         "bonus_by_year": bon, "guaranteed": int(row.get("guaranteed") or 0),
         "contract_type": row.get("contract_type") or "STANDARD", "signed_season": row.get("signed_season"),
         "last_restructure_season": row.get("last_restructure_season"), "drafted_round": row.get("drafted_round")}
    if row.get("signed_week") is not None:
        c["signed_week"] = row["signed_week"]
    return c, start


def _tv_player(p, years, end):
    return {"id": p["id"], "team_id": p.get("team_id"), "group": p["group"], "ovr": p.get("ovr"),
            "age": p.get("age"), "dev": p.get("dev"), "contract": {"end_season": end, "years": years}}


def asking_price(p, market, accrued_seasons=None, cap=None):
    """Season asking price: W(ovr, group) x age factor x the market's view of the dev premium, floored at the
    league minimum."""
    floor = ce.minimum_salary(accrued_seasons if accrued_seasons is not None else (p.get("yoe") or 0), cap)
    if not p.get("group"):
        return floor
    age = p.get("age") or 27
    age_f = max(ASK_AGE_FLOOR, 1 - ASK_AGE_STEP * max(0, age - ASK_AGE_PIVOT))
    w = tv.worth(p.get("ovr"), p["group"], market) * age_f * tv.dev_premium(p, own=False)
    return int(round(max(floor, w)))


def deal_value(p, ctx, fits, start, hits):
    """V_T of a deal paying hits[k] in season start + k (0 hits = he isn't paid that year but plays)."""
    if not p.get("group"):
        return 0
    years = {start + k: (h, False) for k, h in enumerate(hits)}
    return tv.player_value(_tv_player(p, years, start + len(hits) - 1), ctx, fits, start)["value"]


def current_value(p, ctx, fits, season):
    """V_T of his current contract from `season` on (0 without a contract year there)."""
    if not p.get("group") or contract_year(p, season) is None:
        return 0
    end = end_season(p)
    hits = [hit_at(p, s) for s in range(season, end + 1)]
    return deal_value(p, ctx, fits, season, hits)


def field_value(p, ctx, fits):
    """One season's on-field value to the team: W(ovr, group) x u_T x youth x dev premium, rounded to $10,000."""
    if not p.get("group"):
        return 0
    u = tv.utility(p, ctx) if ctx else 1.0
    own = bool(ctx) and p.get("team_id") == ctx.get("team_id")
    ym = (tv.youth_mult(p.get("age"), ctx.get("youth_bias")) if ctx else 1.0) * tv.dev_premium(p, own)
    w = tv.worth(p.get("ovr"), p["group"], fits["market"]) * u * ym
    return int(round(w / ce.MONEY_UNIT)) * ce.MONEY_UNIT


# ----------------------------------------------------------- league state ----
def rosters(league):
    """{team_id: {"roster": [...], "ps": [...]}} for every team (players sorted by id)."""
    out = {t: {"roster": [], "ps": []} for t in league["teams"]}
    for p in sorted(league["players"].values(), key=lambda p: p["id"]):
        if p.get("status") in ("ROSTER", "PS") and p.get("team_id") in out:
            out[p["team_id"]]["roster" if p["status"] == "ROSTER" else "ps"].append(p)
    return out


def payroll(league, team_id, roster=None):
    roster = roster if roster is not None else rosters(league)[team_id]["roster"]
    return sum(hit_at(p, league["target"]) for p in roster)


def cap_of(league, team_id):
    return int(league["cap"].get(team_id) or ce.CBA["REFERENCE_CAP"])


def win_pct(team):
    w, l = team.get("wins") or 0, team.get("losses") or 0
    return w / (w + l) if w + l else 0.5


def waiver_order(league, seed):
    """Team ids, worst record first (priority 1); ties broken by a seeded draw (the NFL uses strength of
    schedule)."""
    teams = league["teams"]
    return sorted(teams, key=lambda t: (win_pct(teams[t]), _u01(seed, "waiver_order", t), t))


def contexts(league, ros=None, expiring=None):
    """{team_id: ctx} for trade_value.utility / player_value, plus starter_ovr {group: weakest starter OVR},
    needs_map {group: score}, cap_room, roster_count. Needs come from gm_direction.positional_needs against the
    league lines (expiring starters count as half-gaps); direction from gm_team_seasons, else RETOOL; GM persona
    neutral (D4)."""
    ros = ros or rosters(league)
    if expiring is None:
        expiring = {p["id"] for p in league["players"].values()
                    if p.get("status") == "ROSTER" and end_season(p) is not None and end_season(p) < league["target"]}
    slots = {t: starter_slots([p for p in r["roster"] if p.get("group")], ()) for t, r in ros.items()}
    lines = league_lines({t: slot_ovrs(s) for t, s in slots.items()}) if slots else {}
    out = {}
    for t, r in ros.items():
        s = slots[t]
        team = league["teams"][t]
        direction = team.get("direction") or "RETOOL"
        needs, _ = positional_needs([p for p in r["roster"] if p.get("group")], (), expiring, lines, direction)
        lns, rlines, rot = {}, {}, set()
        starter_ovr = {}
        for g, n in SLOTS.items():
            lns[g] = min((p.get("ovr") or 0) for p in s[g]) if len(s[g]) == n else None
            starter_ovr[g] = lns[g] if lns[g] is not None else 0
            depth = s["_depth"][g]
            rlines[g] = (depth[0].get("ovr") or 0) if depth else None
            if depth:
                rot.add(depth[0]["id"])
        out[t] = {"team_id": t, "direction": direction, "needs": needs, "needs_map": {n["group"]: n["score"]
                                                                                      for n in needs},
                  "starter_ids": {p["id"] for g in SLOTS for p in s[g]}, "rotation_ids": rot, "lines": lns,
                  "rotation_lines": rlines, "starter_ovr": starter_ovr, "position_bias": {}, "youth_bias": 0.0,
                  "selling": False, "roster_count": len(r["roster"]), "ps_count": len(r["ps"]),
                  "cap": cap_of(league, t), "cap_room": cap_of(league, t) - payroll(league, t, r["roster"]),
                  "win_pct": win_pct(team), "popularity_index": team.get("popularity_index")}
    return out


def projected_depth(p, ctx):
    """1 starter, 2 a role (within 4 OVR of the weakest starter), 3 depth; a group with an open slot starts him."""
    line = ctx["lines"].get(p.get("group"))
    ovr = p.get("ovr") or 0
    if line is None or ovr > line:
        return 1
    return 2 if ovr >= line - SITUATIONAL_WITHIN else 3


def salaries_by_group(league):
    """{group: [cap hits in the league year]} across every rostered contract (tags and 5th-year options)."""
    out = {}
    for p in league["players"].values():
        if p.get("status") == "ROSTER" and p.get("group"):
            h = hit_at(p, league["target"])
            if h:
                out.setdefault(p["group"], []).append(h)
    return out


def _outsider(p):
    """The player as another team sees him (trade_value.utility's outside view: starts if he beats the weakest
    starter)."""
    return dict(p, team_id=None)


def _move(kind, p, team_id, reason, **extra):
    m = {"kind": kind, "player_id": p["id"], "team_id": team_id, "reason": reason}
    m.update(extra)
    return m


# ------------------------------------------------------------- re-signing ----
def plan_resign(league, seed):
    """Every team's re-sign stage: for each expiring contract (ends in `season`) CONTRACT (re-sign at the asking
    price, or an ERFA / RFA tender for 0-2 / 3 accrued seasons), TAG (an 80+ player who wants to test the market,
    one tag a team) or LET_GO; for each contract entering its final year EXTENSION when
    contracts_engine.extension_eligible allows it and the surplus is clear; for a 1st-round rookie deal entering
    year 4 without an extension, OPTION when the 5th year is worth its salary. Candidates go most valuable first;
    every signing must fit the cap room left (2% buffer)."""
    S, T, fits = league["season"], league["target"], league["fits"]
    ros = rosters(league)
    ctxs = contexts(league, ros)
    sal_groups = salaries_by_group(league)
    moves = []
    for t in sorted(ros):
        ctx = ctxs[t]
        cap = ctx["cap"]
        room = ctx["cap_room"] - int(cap * CAP_BUFFER_SHARE)
        tag_used = False
        cands = []
        for p in ros[t]["roster"]:
            end = end_season(p)
            if end is None or not p.get("group"):
                continue
            if end == S:
                ask = asking_price(p, fits["market"], accrued(p, league), cap)
                yrs = ideal_years((p.get("age") or 27) + 1)
                v = deal_value(p, ctx, fits, T, [ask] * yrs)
                cands.append((v, p, "EXPIRING", ask, yrs))
            elif end == T:
                cands.append((current_value(p, ctx, fits, T), p, "FINAL_YEAR", None, None))
        cands.sort(key=lambda c: (-c[0], c[1]["id"]))
        for v, p, mode, ask, yrs in cands:
            if mode == "EXPIRING":
                m, cost = _resign_one(league, ctx, p, v, ask, yrs, tag_used, room, sal_groups, seed)
                if m["kind"] == "TAG":
                    tag_used = True
                room -= cost
                moves.append(m)
            else:
                m = _extend_or_option(league, ctx, p, sal_groups)
                if m:
                    moves.append(m)
    return moves


def _resign_one(league, ctx, p, v, ask, yrs, tag_used, room, sal_groups, seed):
    """(move, cap used in the league year) for one expiring player."""
    T, fits, cap, t = league["target"], league["fits"], ctx["cap"], ctx["team_id"]
    acc = accrued(p, league)
    if acc <= ce.CBA["RFA_ACCRUED"]:
        if acc <= ce.CBA["ERFA_MAX_ACCRUED"]:
            kind, ctype = "ERFA", "ERFA_TENDER"
        else:
            kind = next(k for lo, k in RFA_LEVELS if (p.get("ovr") or 0) >= lo)
            ctype = "RFA_TENDER"
        amount = ce.tender_value(kind, acc, cap)
        tv_ = deal_value(p, ctx, fits, T, [amount])
        if tv_ > 0 and amount <= room:
            c = make_contract(amount, 1, ctype, T, bonus_share=0.0, guar_share=0.0)
            return _move("CONTRACT", p, t, f"{kind} tender", offer_kind="TENDER", tender=kind, contract=c,
                         value=tv_), amount
        return _move("LET_GO", p, t, f"not worth the {kind} tender" if tv_ <= 0 else "no cap room for the tender",
                     value=tv_), 0
    if v <= 0:
        return _move("LET_GO", p, t, "asking price above his value to the team", value=v, ask=ask), 0
    if (p.get("ovr") or 0) >= TAG_MIN_OVR and _u01(seed, "test_market", league["season"], p["id"]) < TEST_MARKET_P:
        prior = hit_at(p, league["season"])
        salary = ce.tag_value(TAG_KIND, p["group"], sal_groups, prior, [cap])
        v_tag = deal_value(p, ctx, fits, T, [salary])
        if not tag_used and v_tag > 0 and salary <= room:
            c = make_contract(salary, 1, TAG_KIND, T, bonus_share=0.0, guar_share=1.0)
            return _move("TAG", p, t, "wants to test the market: tagged", tag=TAG_KIND, contract=c,
                         value=v_tag), salary
        return _move("LET_GO", p, t, "wants to test the market", value=v, ask=ask), 0
    vet = (p.get("age") or 27) + 1 >= VET_AGE
    c = make_contract(ask, yrs, "STANDARD", T, guar_share=RESIGN_GUAR_SHARE_VET if vet else RESIGN_GUAR_SHARE)
    cost = ce.cap_hit(c, 0)
    if cost > room:
        return _move("LET_GO", p, t, "no cap room to re-sign", value=v, ask=ask), 0
    return _move("CONTRACT", p, t, "re-signed", offer_kind="RESIGN", contract=c, value=v, ask=ask,
                 years=yrs, total_value=ask * yrs), cost


def _extend_or_option(league, ctx, p, sal_groups):
    """EXTENSION for an eligible starter with a clear surplus at his asking price, else OPTION for a 1st-round
    rookie deal entering year 4, else nothing (he plays out the deal)."""
    S, T, fits, t = league["season"], league["target"], league["fits"], ctx["team_id"]
    c = p["contract"]
    elig_c = dict(c, current_year=max(0, S - p["contract_start"]))
    ok, why = ce.extension_eligible(elig_c, p, S, RESIGN_WEEK)
    age_t = (p.get("age") or 27) + (T - S)
    this_hit = hit_at(p, T)
    if ok and p["id"] in ctx["starter_ids"] and age_t <= EXTEND_MAX_AGE:
        ask = asking_price(p, fits["market"], accrued(p, league), ctx["cap"])
        yrs = ideal_years(age_t + 1)
        marginal = (deal_value(p, ctx, fits, T, [this_hit] + [ask] * yrs)
                    - deal_value(p, ctx, fits, T, [this_hit]))
        if marginal > EXTEND_MIN_SURPLUS * ask * yrs:
            y = T - p["contract_start"]
            new = make_contract(ask, yrs, "STANDARD", T)
            out = {"length": 1 + yrs, "current_year": 0,
                   "salary_by_year": [c["salary_by_year"][y]] + new["salary_by_year"],
                   "bonus_by_year": [c["bonus_by_year"][y]] + new["bonus_by_year"],
                   "guaranteed": new["guaranteed"], "contract_type": "STANDARD", "signed_season": T,
                   "last_restructure_season": None, "drafted_round": c.get("drafted_round")}
            return _move("EXTENSION", p, t, why, offer_kind="EXTENSION", contract=out, value=marginal, ask=ask,
                         years=yrs, total_value=ask * yrs)
    if (c.get("contract_type") == "ROOKIE" and c.get("drafted_round") == 1 and c["length"] == 4
            and T - p["contract_start"] == 3):
        opt = ce.fifth_year_option({"position": p["group"], "drafted_round": 1, "pro_bowls": p.get("pro_bowls") or 0,
                                    "playtime": p["id"] in ctx["starter_ids"]}, sal_groups)
        marginal = (deal_value(p, ctx, fits, T, [this_hit, opt["salary"]])
                    - deal_value(p, ctx, fits, T, [this_hit]))
        if opt["salary"] and marginal > 0:
            # from T: the rookie deal's year 4 plus the option year (fully guaranteed, as since the 2020 CBA)
            out = dict(c, length=2, current_year=0, salary_by_year=[c["salary_by_year"][3], opt["salary"]],
                       bonus_by_year=[c["bonus_by_year"][3], 0], contract_type="FIFTH_YEAR_OPTION",
                       guaranteed=opt["salary"])
            return _move("OPTION", p, t, f"5th-year option, tier {opt['tier']}", tier=opt["tier"],
                         salary=opt["salary"], contract=out, value=marginal)
    return None


# ------------------------------------------------------------ free agency ----
def free_agents(league):
    return [p for p in sorted(league["players"].values(), key=lambda p: p["id"])
            if p.get("status") == "FA" and p.get("group")]


def plan_offers(league, stage, wave, seed):
    """CPU offers for one free-agency wave (or an in-season pass with wave None: 1-year deals). Each team with
    roster spots under ROSTER_LIMIT and cap room makes at most min(OFFERS_PER_WAVE, spots) offers, one per group
    (need groups first, highest need first, to a free agent who would start or hold a role; then any other group, to
    one who would start), to the free agent with the best value at the offer price among those of wave tier <= wave;
    the offers' league-year cap hits together fit its room (2% buffer), so
    even if every offer is accepted the team stays under the cap and the limit. Offers carry `team` (offer_utility's
    team dict) and `market` (the trade-value fit) for free_agency_engine.run_wave."""
    T, fits = league["target"], league["fits"]
    ros = rosters(league)
    ctxs = contexts(league, ros)
    pool = [p for p in free_agents(league) if wave is None or wave_tier(p) <= wave]
    in_season = stage not in OFFSEASON_STAGES
    offer_kind = "UDFA" if stage == "UDFA" else "FREE_AGENT"
    offers = []
    for t in sorted(ros):
        ctx = ctxs[t]
        spots = ROSTER_LIMIT - ctx["roster_count"]
        room = ctx["cap_room"] - int(ctx["cap"] * CAP_BUFFER_SHARE)
        left = min(OFFERS_PER_WAVE, spots)
        if left <= 0 or room <= 0:
            continue
        total_need = sum(ctx["needs_map"].values()) or 1.0
        team = {"team_id": t, "win_pct": ctx["win_pct"], "direction": ctx["direction"],
                "popularity_index": ctx.get("popularity_index"), "projected_depth": {}}
        taken = set()
        groups = [(n["group"], n["score"]) for n in ctx["needs"]]
        groups += [(g, 0.0) for g in SLOTS if g not in ctx["needs_map"]]
        for group, score in groups:
            if left <= 0:
                break
            best = None
            for p in pool:
                if p["group"] != group or p["id"] in taken:
                    continue
                depth = projected_depth(p, ctx)
                if depth > (2 if score > 0 else 1):
                    continue
                age = p.get("age") or 27
                ask = asking_price(p, fits["market"], accrued(p, league), ctx["cap"])
                mult = 1 + OFFER_NEED_PREMIUM * score / total_need + _noise(OFFER_NOISE, seed, "offer", stage,
                                                                                    wave, t, p["id"])
                aav = int(round(ask * mult))
                yrs = 1 if in_season else ideal_years(age + (T - league["season"]))
                c = make_contract(aav, yrs, "STANDARD", T,
                                  guar_share=RESIGN_GUAR_SHARE_VET if age >= VET_AGE else RESIGN_GUAR_SHARE)
                hit = ce.cap_hit(c, 0)
                if hit > room:
                    continue
                v = deal_value(_outsider(p), ctx, fits, T, [ce.cap_hit(c, k) for k in range(yrs)])
                if v <= 0:
                    continue
                if best is None or (v, -p["id"]) > (best[0], -best[1]["id"]):
                    best = (v, p, c, hit, depth, aav, yrs)
            if best is None:
                continue
            v, p, c, hit, depth, aav, yrs = best
            taken.add(p["id"])
            team["projected_depth"][p["id"]] = depth
            total = sum(c["salary_by_year"]) + sum(c["bonus_by_year"])
            offers.append({"team_id": t, "player_id": p["id"], "years": yrs, "total_value": total,
                           "bonus": sum(c["bonus_by_year"]), "guaranteed": c["guaranteed"],
                           "salary_by_year": c["salary_by_year"], "projected_depth": depth, "contract": c,
                           "kind": offer_kind, "stage": stage, "wave": wave, "value": v,
                           "team": team, "market": fits["market"]})
            room -= hit
            left -= 1
    return offers


def market_needs(league):
    """The `needs` dict free_agency_engine.street_market / udfa_market take, with roster_spots under ROSTER_LIMIT
    (0 when the team can't afford a minimum deal)."""
    out = {}
    for t, ctx in contexts(league).items():
        afford = ctx["cap_room"] >= ce.minimum_salary(0, ctx["cap"])
        out[t] = {"needs": dict(ctx["needs_map"]), "starter_ovr": dict(ctx["starter_ovr"]),
                  "roster_spots": max(0, ROSTER_LIMIT - ctx["roster_count"]) if afford else 0,
                  "win_pct": ctx["win_pct"], "direction": ctx["direction"],
                  "popularity_index": ctx.get("popularity_index")}
    return out


def signings_from_decisions(decisions, offers):
    """free_agency_engine.run_wave decisions -> SIGN moves for the ACCEPTed offers (contract from the offer)."""
    out = []
    for d in decisions:
        if d.get("status") != "ACCEPT":
            continue
        o = offers[d["offer_index"]] if d.get("offer_index") is not None else d["offer"]
        out.append({"kind": "SIGN", "player_id": d["player_id"], "team_id": d["team_id"], "list": "Roster",
                    "reason": "free agency", "contract": o.get("contract"), "offer_id": o.get("offer_id")})
    return out


# --------------------------------------------------------------- cut-down ----
def plan_cutdown(league, limit=ROSTER_LIMIT):
    """RELEASE moves taking every team's active roster to `limit`: starters are kept; the rest go lowest on-field
    value first and, at equal value, highest cut savings (contracts_engine.cut_savings in the league year) first."""
    T, fits = league["target"], league["fits"]
    ros = rosters(league)
    ctxs = contexts(league, ros)
    moves = []
    for t in sorted(ros):
        roster, ctx = ros[t]["roster"], ctxs[t]
        excess = len(roster) - limit
        if excess <= 0:
            continue
        ranked = []
        for p in roster:
            if p["id"] in ctx["starter_ids"]:
                continue
            y = contract_year(p, T)
            sav = ce.cut_savings(p["contract"], y) if y is not None else 0
            dead = ce.dead_money(p["contract"], y) if y is not None else 0
            ranked.append((field_value(p, ctx, fits), -sav, p["id"], p, sav, dead))
        ranked.sort(key=lambda r: r[:3])
        for val, _, _, p, sav, dead in ranked[:excess]:
            moves.append(_move("RELEASE", p, t, "cut-down", value=val, cut_savings=sav, dead_money=dead,
                               cap_hit=hit_at(p, T), list="Roster"))
    return moves


# ---------------------------------------------------------------- waivers ----
def plan_waivers(league, released, seed, limit=ROSTER_LIMIT):
    """Claims for the released players, best first. Every other team, in waiver order (worst record first,
    priority 1), claims a player who would start or hold a role (or fills a need group), whose contract is worth
    its cap hit to it (V_T > 0) and fits its cap room; a team at the limit claims only by releasing its lowest-value
    non-starter worth less than him (a paired RELEASE). The lowest priority number wins; unclaimed players stay
    free agents. Returns (moves, claims) with claims = [{player_id, team_id, priority, awarded}]."""
    T, fits = league["target"], league["fits"]
    gone = set(released)
    order = waiver_order(league, seed)
    prio = {t: i + 1 for i, t in enumerate(order)}
    ros = rosters(league)
    for t in ros:
        ros[t]["roster"] = [p for p in ros[t]["roster"] if p["id"] not in gone]
    ctxs = contexts(league, ros)
    counts = {t: len(ros[t]["roster"]) for t in ros}
    rooms = {t: ctxs[t]["cap_room"] for t in ros}
    dropped = set()
    players = [league["players"][pid] for pid in dict.fromkeys(released) if pid in league["players"]]
    players = [p for p in players if p.get("group")]
    players.sort(key=lambda p: (-field_value(p, None, fits), p["id"]))
    former = {p["id"]: p.get("from_team_id", p.get("team_id")) for p in players}
    moves, claims = [], []
    for p in players:
        claimants = []
        for t in order:
            if t == former[p["id"]]:
                continue
            ctx = ctxs[t]
            q = _outsider(p)
            if projected_depth(q, ctx) > 2 and ctx["needs_map"].get(p["group"], 0) <= 0:
                continue
            hit = hit_at(p, T)
            if hit > rooms[t] or current_value(q, ctx, fits, T) <= 0:
                continue
            drop = None
            if counts[t] >= limit:
                mine = field_value(q, ctx, fits)
                pool = [(field_value(r, ctx, fits), r["id"], r) for r in ros[t]["roster"]
                        if r["id"] not in ctx["starter_ids"] and r["id"] not in dropped]
                if not pool:
                    continue
                val, _, r = min(pool, key=lambda x: x[:2])
                if val >= mine:
                    continue
                drop = r
            claimants.append((t, drop))
        for i, (t, drop) in enumerate(claimants):
            claims.append({"player_id": p["id"], "team_id": t, "priority": prio[t], "awarded": i == 0})
        if not claimants:
            continue
        t, drop = claimants[0]
        c = p.get("contract")
        y = contract_year(p, T)
        inherited = dict(c, current_year=0, length=c["length"] - y,
                         salary_by_year=c["salary_by_year"][y:], bonus_by_year=c["bonus_by_year"][y:]) if y is not None else None
        moves.append(_move("WAIVER_CLAIM", p, t, "waiver claim", priority=prio[t], from_team_id=former[p["id"]],
                           claimants=[x[0] for x in claimants], contract=inherited))
        rooms[t] -= hit_at(p, T)
        counts[t] += 1
        if drop is not None:
            dropped.add(drop["id"])
            counts[t] -= 1
            rooms[t] += ce.cut_savings(drop["contract"], contract_year(drop, T)) if contract_year(drop, T) is not None else 0
            moves.append(_move("RELEASE", drop, t, "roster room for a waiver claim", list="Roster",
                               for_player_id=p["id"]))
    return moves, claims


# ---------------------------------------------------------- practice squad ----
def ps_eligible(p, league):
    """'REGULAR' (<= 2 accrued seasons), 'VETERAN' (any accrued seasons: one of PS_VETERAN_SLOTS exceptions)."""
    return "REGULAR" if accrued(p, league) <= PS_MAX_ACCRUED else "VETERAN"


def plan_practice_squad(league, seed, limit=ROSTER_LIMIT):
    """Practice squad moves for every team, worst record first (signings compete for the same free agents):
    PS_DEMOTE eligible non-starters, lowest value first, while the active roster is over `limit` and the squad has
    room; PS_PROMOTE the best squad players who would start or hold a role, or fill a need group, into open active
    spots (a 1-year minimum deal that fits the cap room); RELEASE (list PracticeSquad) squad players over
    PRACTICE_SQUAD_LIMIT or over the PS_VETERAN_SLOTS veteran exceptions, lowest value first; SIGN eligible free
    agents (list PracticeSquad, no contract: the save's squad players have no contract row) into the open slots,
    best value to the team first, a seeded tie-break."""
    T, fits = league["target"], league["fits"]
    ros = rosters(league)
    ctxs = contexts(league, ros)
    pool = {p["id"]: p for p in free_agents(league)}
    moves = []
    for t in waiver_order(league, seed):
        ctx = ctxs[t]
        roster, ps = list(ros[t]["roster"]), list(ros[t]["ps"])
        room = ctx["cap_room"]
        vets = lambda: sum(1 for q in ps if ps_eligible(q, league) == "VETERAN")
        # demotions
        if len(roster) > limit:
            cands = sorted((p for p in roster if p["id"] not in ctx["starter_ids"]),
                           key=lambda p: (field_value(p, ctx, fits), p["id"]))
            for p in cands:
                if len(roster) <= limit or len(ps) >= PRACTICE_SQUAD_LIMIT:
                    break
                kind = ps_eligible(p, league)
                if kind == "VETERAN" and vets() >= PS_VETERAN_SLOTS:
                    continue
                roster.remove(p)
                ps.append(p)
                room += ce.cut_savings(p["contract"], contract_year(p, T)) if contract_year(p, T) is not None else 0
                moves.append(_move("PS_DEMOTE", p, t, f"demoted ({kind.lower()} eligibility)",
                                   eligibility=kind, accrued=accrued(p, league)))
        # promotions
        for p in sorted(ps, key=lambda p: (-field_value(p, ctx, fits), p["id"])):
            if len(roster) >= limit:
                break
            if projected_depth(p, ctx) > 2 and ctx["needs_map"].get(p.get("group"), 0) <= 0:
                continue
            salary = ce.minimum_salary(accrued(p, league), ctx["cap"])
            if salary > room:
                continue
            c = make_contract(salary, 1, "MINIMUM", T, bonus_share=0.0, guar_share=0.0)
            ps.remove(p)
            roster.append(p)
            room -= salary
            moves.append(_move("PS_PROMOTE", p, t, "promoted to the active roster", contract=c))
        # releases: over the veteran exceptions, then over the limit
        by_low = sorted(ps, key=lambda p: (field_value(p, ctx, fits), p["id"]))
        for p in by_low:
            if ps_eligible(p, league) == "VETERAN" and vets() > PS_VETERAN_SLOTS:
                ps.remove(p)
                moves.append(_move("RELEASE", p, t, "practice squad: over the veteran exceptions",
                                   list="PracticeSquad"))
        for p in sorted(ps, key=lambda p: (field_value(p, ctx, fits), p["id"])):
            if len(ps) <= PRACTICE_SQUAD_LIMIT:
                break
            ps.remove(p)
            moves.append(_move("RELEASE", p, t, "practice squad over its limit", list="PracticeSquad"))
        # signings
        while len(ps) < PRACTICE_SQUAD_LIMIT:
            best = None
            for p in pool.values():
                kind = ps_eligible(p, league)
                if kind == "VETERAN" and vets() >= PS_VETERAN_SLOTS:
                    continue
                key = (field_value(_outsider(p), ctx, fits), _u01(seed, "ps", t, p["id"]))
                if best is None or key > best[0]:
                    best = (key, p, kind)
            if best is None:
                break
            _, p, kind = best
            del pool[p["id"]]
            ps.append(p)
            moves.append(_move("SIGN", p, t, f"practice squad ({kind.lower()} eligibility)", list="PracticeSquad",
                               contract=None, eligibility=kind, accrued=accrued(p, league)))
    return moves


# ----------------------------------------------------------- rookie deals ----
def league_cap(league):
    caps = sorted(v for v in league["cap"].values() if v)
    return caps[len(caps) // 2] if caps else ce.CBA["REFERENCE_CAP"]


def plan_rookies(league, prospects):
    """Drafted players -> the slotted rookie deal by overall pick (contracts_engine.rookie_contract at the league
    cap, signed in the league year). prospects: [{player_id, team_id, overall_pick}]. A pick past 224 (a
    compensatory pick) takes pick 224's slot. A player already on his team gets CONTRACT, else SIGN to the
    roster."""
    T = league["target"]
    cap = league_cap(league)
    moves = []
    for d in sorted(prospects, key=lambda d: d["overall_pick"]):
        if d.get("player_id") is None or d.get("team_id") is None:
            continue
        pick = min(max(1, int(d["overall_pick"])), ce.CBA["DRAFT_PICKS"])
        c = ce.rookie_contract(pick, T, cap)
        p = league["players"].get(d["player_id"]) or {}
        on_team = p.get("team_id") == d["team_id"] and p.get("status") in ("ROSTER", "PS")
        moves.append({"kind": "CONTRACT" if on_team else "SIGN", "player_id": d["player_id"],
                      "team_id": d["team_id"], "reason": f"rookie deal, pick {d['overall_pick']}",
                      "overall_pick": d["overall_pick"], "round": c["drafted_round"], "list": "Roster",
                      "contract": c})
    return moves


# --------------------------------------------------------------------- DB ----
def fetch_teams(cur, season):
    """{team_id: {team_id, abbr, wins, losses, popularity_index, direction}} for every team with an abbreviation."""
    cur.execute("""SELECT t.id, t.abbreviation, s.wins, s.losses, s.popularity_index, g.direction
                   FROM teams t
                   LEFT JOIN team_seasons s ON s.team_id = t.id AND s.season = %s
                   LEFT JOIN gm_team_seasons g ON g.team_id = t.id AND g.season = %s
                   WHERE t.abbreviation IS NOT NULL ORDER BY t.id""", (season, season))
    return {r[0]: {"team_id": r[0], "abbr": r[1], "wins": r[2] or 0, "losses": r[3] or 0,
                   "popularity_index": r[4], "direction": r[5]} for r in cur.fetchall()}


def fetch_caps(cur, season, target):
    """{team_id: cap} for the league year: that year's team_cap_seasons row if it exists, else this season's
    (adjusted cap, else cap)."""
    cur.execute("""SELECT team_id, season, COALESCE(adjusted_cap, cap) FROM team_cap_seasons
                   WHERE season IN (%s, %s)""", (season, target))
    out = {}
    for tid, s, cap in sorted(cur.fetchall(), key=lambda r: r[1] == target):
        if cap is not None:
            out[tid] = int(cap)
    return out


def fetch_players(cur, season):
    """{player_id: player} for every player with a player_seasons row this season: team_id None when released or
    unsigned."""
    cur.execute("""SELECT ps.player_id, ps.team_id, ps.position, ps.overall_rating, ps.age, ps.dev_trait,
                          ps.years_of_experience, ps.released_week
                   FROM player_seasons ps WHERE ps.season = %s AND COALESCE(ps.position, '') <> 'DEF'""", (season,))
    out = {}
    for pid, tid, pos, ovr, age, dev, yoe, rel in cur.fetchall():
        out[pid] = {"id": pid, "team_id": tid if rel is None else None, "last_team_id": tid, "pos": pos,
                    "group": position_group(pos), "ovr": ovr, "age": age, "dev": dev, "yoe": yoe,
                    "rookie_season": season - (yoe or 0), "released": rel is not None}
    return out


def fetch_contracts(cur, season):
    """{player_id: contract row with years {calendar year: cap hit}}: the ACTIVE row, else the newest RELEASED one
    (a waived player's deal)."""
    cur.execute("""SELECT c.id, c.player_id, c.team_id, c.season, c.length, c.years_remaining, c.total_value, c.bonus,
                          c.status, c.contract_type, c.guaranteed, c.signed_season, c.signed_week,
                          c.last_restructure_season, c.drafted_round
                   FROM player_contracts c WHERE c.season = %s AND c.status IN ('ACTIVE', 'RELEASED')
                   ORDER BY (c.status = 'ACTIVE'), c.id""", (season,))
    keys = ("id", "player_id", "team_id", "season", "length", "years_remaining", "total_value", "bonus", "status",
            "contract_type", "guaranteed", "signed_season", "signed_week", "last_restructure_season", "drafted_round")
    rows = {}
    for r in cur.fetchall():
        d = dict(zip(keys, r), years={})
        rows[d["player_id"]] = d                    # ACTIVE sorts last and wins
    by_id = {d["id"]: d for d in rows.values()}
    if by_id:
        cur.execute("SELECT contract_id, year, cap_hit FROM player_contract_years WHERE contract_id = ANY(%s)",
                    (list(by_id),))
        for cid, year, hit in cur.fetchall():
            if cid in by_id:
                by_id[cid]["years"][year] = hit
    return rows


def fetch_prospects(cur, season):
    """This draft's picks (draft_season = season + 1): [{player_id, team_id, overall_pick}]."""
    cur.execute("""SELECT player_id, drafted_team_id, actual_pick FROM draft_prospects
                   WHERE draft_season = %s AND actual_pick IS NOT NULL ORDER BY actual_pick""", (season + 1,))
    return [{"player_id": r[0], "team_id": r[1], "overall_pick": r[2]} for r in cur.fetchall()]


def fetch_fits(cur, season):
    return tv.fits_from_db(cur, season)


def build_league(season, target, teams, caps, players, contracts, fits):
    """The plain-dict league the plan_* functions take. Status: ROSTER (on a team with an ACTIVE contract), PS (on
    a team without one: the save's practice-squad players have no contract row), FA (no team)."""
    out = {}
    for pid, p in players.items():
        p = dict(p)
        row = contracts.get(pid)
        if row:
            p["contract"], p["contract_start"] = contract_from_db(row)
        else:
            p["contract"], p["contract_start"] = None, None
        if p.get("team_id") is None:
            p["status"] = "FA"
            if p.get("released") and row:      # a waived player still carries his deal
                p["from_team_id"] = p.get("last_team_id")
            else:
                p["contract"], p["contract_start"] = None, None
        elif row and row.get("status") == "ACTIVE":
            p["status"] = "ROSTER"
        else:
            p["status"] = "PS"
        out[pid] = p
    return {"season": season, "target": target, "teams": teams, "cap": caps, "players": out, "fits": fits}


def fetch_league(cur, season, target=None):
    target = target_season(season) if target is None else target
    return build_league(season, target, fetch_teams(cur, season), fetch_caps(cur, season, target),
                        fetch_players(cur, season), fetch_contracts(cur, season), fetch_fits(cur, season))


def store_offers(cur, offers, season, seed):
    """INSERT each offer into contract_offers (OPEN, from_user false); sets offer["offer_id"]."""
    for o in offers:
        cur.execute("""INSERT INTO contract_offers (season, stage, wave, kind, player_id, team_id, from_user, years,
                                                    total_value, bonus, guaranteed, status, seed)
                       VALUES (%s, %s, %s, %s, %s, %s, false, %s, %s, %s, %s, 'OPEN', %s) RETURNING id""",
                    (season, o["stage"], o["wave"], o["kind"], o["player_id"], o["team_id"], o["years"],
                     o["total_value"], o["bonus"], o["guaranteed"], seed))
        row = cur.fetchone()
        o["offer_id"] = row[0] if row else None
    return offers


def store_claims(cur, claims, season, week):
    for c in claims:
        cur.execute("""INSERT INTO waiver_claims (season, week, player_id, team_id, priority, awarded)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (season, week, c["player_id"], c["team_id"], c["priority"], c["awarded"]))


# ----------------------------------------------------------------- public ----
def resign_decisions(cur, season, seed):
    """Re-sign stage moves for all 32 teams (CONTRACT, TAG, EXTENSION, OPTION, LET_GO)."""
    return plan_resign(fetch_league(cur, season), seed)


def cpu_offers(cur, season, stage, wave, seed):
    """CPU free-agent offers (run_wave's offer dicts with team and market), stored in contract_offers."""
    league = fetch_league(cur, season, target_season(season, stage=stage))
    return store_offers(cur, plan_offers(league, stage, wave, seed), season, seed)


def cutdown(cur, season, seed, limit=ROSTER_LIMIT):
    """RELEASE moves to bring every active roster to `limit`. Deterministic: the seed is accepted for the
    league office's uniform call shape."""
    return plan_cutdown(fetch_league(cur, season), limit)


def waivers(cur, season, week, released, seed):
    """WAIVER_CLAIM (and room-making RELEASE) moves for the released players; every claim stored in
    waiver_claims with its awarded flag."""
    league = fetch_league(cur, season, target_season(season, week))
    moves, claims = plan_waivers(league, released, seed)
    store_claims(cur, claims, season, week)
    return moves


def practice_squad(cur, season, seed, week=None):
    """PS_DEMOTE / PS_PROMOTE / RELEASE / SIGN moves within PRACTICE_SQUAD_LIMIT (offseason unless an in-season
    week is given)."""
    return plan_practice_squad(fetch_league(cur, season, target_season(season, week)), seed)


def rookie_signings(cur, season):
    """Rookie-scale deals for this draft's picks."""
    league = fetch_league(cur, season)
    return plan_rookies(league, fetch_prospects(cur, season))
