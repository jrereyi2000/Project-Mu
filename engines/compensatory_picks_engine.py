"""Compensatory draft picks engine (docs/in_progress/COMPENSATORY_PICKS_PLAN.md; PROJECT_SPEC §2.17/§3.3c/§4.6,
planned §4.6x). Pure calculation logic: importable offline, no database connection at import time.

Implements the CBA Article 6, Section 2 compensatory draft pick formula and the 2020 Resolution JC-2A minority
coach/executive development awards. Every valuation, cancellation and allocation function here is a pure function
over plain dicts, so the whole engine runs against synthetic inputs with no database. The only functions that touch
the database are the explicitly-named load/save helpers at the bottom, each taking a `cur`; nothing else opens a
connection.

A QFA (Qualifying Free Agent) is a player dict with these keys (the DB loader below produces the same shape):

    player_id          int (or any hashable key), required
    team_id            int, required: the franchise this QFA counts for (old team for a LOST player, new team for a
                       GAINED player)
    apy                int, required: average per year of the new contract, whole dollars
    snap_pct           float, default 0.0: fraction 0..1 of offensive/defensive snaps in the preceding season
    all_pro_first      bool, default False: first-team All-Pro in the preceding season
    pro_bowl           bool, default False: Pro Bowl selection in the preceding season
    honors_points      int, optional: precomputed honor points (20 AP1 + 10 Pro Bowl); overrides the two booleans
    waived             bool, default False: cut/waived by the former team (never a QFA)
    is_unrestricted    bool, default True: unrestricted free agent (RFA/ERFA/tendered are not QFAs)
    contract_expired   bool, default True: the contract expired naturally (did not end by cut/waiver)
    signed_date        date, required when a deadline is passed: when he signed with the new team

Formula (plan §2.1).  Each departing QFA scores P = APY_Score + Snap_Bonus + Honor_Bonus:
  * APY_Score by percentile of his APY against every league contract APY (league_apys), the round tier and value:
        >= 95th pct -> Round 3 (100);  >= 90th -> Round 4 (80);  >= 85th -> Round 5 (60);
        >= 75th pct -> Round 6 (40);  >= 65th -> Round 7 (20);  below the 65th percentile -> non-qualifier.
  * Snap_Bonus: +5 at >= 50% snaps, +10 at >= 75% snaps.
  * Honor_Bonus: +20 first-team All-Pro, +10 Pro Bowl.
The round tier for cancellation/allocation is set by the APY percentile; snap and honor add to the total score P
(used to order picks when the league or franchise caps must drop someone).

Cancellation (plan §2.2).  For each franchise, gained QFAs cancel lost QFAs one-for-one: each gained QFA (most
valuable first) cancels the lost QFA closest in round tier (same tier first, then nearest; ties broken toward the
less valuable lost QFA), so the most valuable uncancelled lost QFAs survive.  Each survivor yields a CBA
compensatory pick in his round tier, capped at FRANCHISE_COMP_PICK_CAP (4) per franchise and LEAGUE_COMP_PICK_CAP
(32) league-wide, best total score first.

Resolution JC-2A (plan §1/§2.2).  A minority OC / DC / assistant-GM hired away by another club as a Head Coach or
GM with at least two years of tenure awards the originating club two Round-3 picks, one in each of the two
subsequent drafts.  JC-2A picks are NOT counted against the 4-pick franchise limit or the 32-pick league ceiling.
    OFF BY DEFAULT (JR, 2026-11-08): `calculate_comp_picks` awards no JC-2A picks unless called with
    `jc2a_enabled=True`; the rule and `jc2a_awards` stay available for when it is switched on.

    IMPORTANT SCHEMA GAP (see NOTES at the bottom): there is NO demographic/minority field on coaches, coordinators
    or staff_moves, and staff_moves tracks only HC/OC/DC (no GM / assistant-GM or executive roles).  This engine does
    NOT invent demographic data and does NOT add demographic columns.  JC-2A is therefore an explicit-input
    evaluation: jc2a_eligible takes `is_minority`, `hire_from_role`, `hire_to_role` and `tenure_years` as arguments
    (plus `hired_away`), and jc2a_awards takes a list of such departure dicts.  A follow-up is needed to seed a
    demographic source and GM/executive-role tracking before the carousel (engines/coaching_carousel.py) can feed it
    automatically.

Tests: tests/test_compensatory_picks_engine.py.  Run: pytest -m "not integration" tests/test_compensatory_picks_engine.py
"""
from collections import defaultdict

# ---------------------------------------------------------------- constants (plan §2.1 / §2.2 / §1) ----
LEAGUE_COMP_PICK_CAP = 32      # max CBA Article 6 comp picks league-wide per season (plan §2.2)
FRANCHISE_COMP_PICK_CAP = 4    # max CBA comp picks per franchise per season (plan §2.2)
JC2A_TENURE_YEARS = 2          # Resolution JC-2A: >= 2 years of tenure
JC2A_ROUND = 3                 # Resolution JC-2A: two Round-3 picks
JC2A_PICK_SPREAD = (1, 2)      # one pick in each of the two drafts after the season just played

# APY value tiers: (percentile floor, round tier, APY score). Ordered highest percentile first.
APY_TIERS = (
    (0.95, 3, 100),
    (0.90, 4, 80),
    (0.85, 5, 60),
    (0.75, 6, 40),
    (0.65, 7, 20),
)
APY_QUALIFY_PERCENTILE = 0.65  # below this percentile the player is not a qualifier (plan §2.1)

SNAP_BONUS_HALF = 5            # >= 50% snaps
SNAP_BONUS_THREE_QUARTERS = 10  # >= 75% snaps
HONOR_AP1 = 20                 # first-team All-Pro
HONOR_PRO_BOWL = 10            # Pro Bowl

JC2A_FROM_ROLES = frozenset(("OC", "DC", "ASSISTANT_GM"))
JC2A_TO_ROLES = frozenset(("HC", "GM"))
JC2A_ENABLED = False           # off by default (JR, 2026-11-08): calculate_comp_picks awards CBA picks only


# ---------------------------------------------------------------- QFA qualification ----
def qfa_eligible(qfa, post_draft_deadline=None):
    """True when a player is a Qualifying Free Agent for his former team: not cut/waived, an unrestricted free
    agent whose contract expired naturally, and (when a deadline is given) signed before the post-draft
    deadline (the Monday following the NFL Draft). `post_draft_deadline` is a date; a player signed on or after
    it does not qualify. With no deadline the signing-date check is skipped."""
    if qfa.get("waived") or qfa.get("cut"):
        return False
    if qfa.get("is_unrestricted") is False:
        return False
    if qfa.get("contract_expired") is False:
        return False
    if post_draft_deadline is not None:
        signed = qfa.get("signed_date")
        if signed is None or signed >= post_draft_deadline:
            return False
    return True


# ---------------------------------------------------------------- APY percentile & tiers ----
def percentile_rank(value, values):
    """0..1 rank of `value` among `values` (0 = lowest, 1 = highest), ties averaged (the standard
    percentile-rank formula: (count below + 0.5 * count equal) / n). An empty pool ranks 0.0."""
    values = list(values)
    if not values:
        return 0.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / len(values)


def tier_for_percentile(pct):
    """(round_tier, apy_value) for a percentile rank, or (None, None) below the 65th percentile."""
    for floor, rnd, val in APY_TIERS:
        if pct >= floor:
            return rnd, val
    return None, None


def apy_tier(apy, league_apys):
    """(round_tier, apy_value) of `apy` ranked against `league_apys`, or (None, None) for a non-qualifier."""
    return tier_for_percentile(percentile_rank(apy, league_apys))


# ---------------------------------------------------------------- formula score ----
def snap_bonus(snap_pct):
    """+10 at >= 75% snaps, +5 at >= 50%, else 0. snap_pct is a fraction 0..1."""
    snap_pct = snap_pct or 0.0
    if snap_pct >= 0.75:
        return SNAP_BONUS_THREE_QUARTERS
    if snap_pct >= 0.50:
        return SNAP_BONUS_HALF
    return 0


def honor_points(all_pro_first=False, pro_bowl=False):
    """+20 first-team All-Pro, +10 Pro Bowl (a player can earn both: 30)."""
    return (HONOR_AP1 if all_pro_first else 0) + (HONOR_PRO_BOWL if pro_bowl else 0)


def formula_score(apy_value, snap_pct, all_pro_first=False, pro_bowl=False):
    """P = APY_Score + Snap_Bonus + Honor_Bonus (plan §2.1)."""
    return apy_value + snap_bonus(snap_pct) + honor_points(all_pro_first, pro_bowl)


def evaluate_qfa(qfa, league_apys):
    """Valuation of one QFA against the league APY distribution. Returns a dict:
    {player_id, team_id, percentile, round_tier, apy_value, snap_bonus, honor, score, qualifies}.
    `round_tier`/`qualifies` are None/False when the APY is below the 65th percentile (no comp pick)."""
    pct = percentile_rank(qfa["apy"], league_apys)
    rnd, val = tier_for_percentile(pct)
    sb = snap_bonus(qfa.get("snap_pct", 0.0))
    hon = qfa.get("honors_points")
    if hon is None:
        hon = honor_points(qfa.get("all_pro_first", False), qfa.get("pro_bowl", False))
    return {
        "player_id": qfa.get("player_id"),
        "team_id": qfa.get("team_id"),
        "percentile": round(pct, 6),
        "round_tier": rnd,
        "apy_value": val if val is not None else 0,
        "snap_bonus": sb,
        "honor": int(hon),
        "score": (val + sb + int(hon)) if val is not None else 0,
        "qualifies": rnd is not None,
    }


# ---------------------------------------------------------------- cancellation & allocation ----
def _value_key(q):
    """Sort key for QFAs: lowest round (most valuable) first, then highest score."""
    return (q["round_tier"], -q.get("score", 0))


def cancel_qfas_pairs(lost, gained):
    """Cancel a team's lost QFAs against its gained QFAs, keeping the pairing. Each gained QFA (most valuable first)
    cancels the lost QFA closest in round tier — same tier first, then nearest, ties broken toward the less valuable
    lost QFA — so the most valuable uncancelled lost QFAs survive and yield picks. Returns
    `(survivors, pairs)`: the uncancelled lost QFAs (most valuable first) and `[(gained, cancelled), ...]` in the
    order the cancels happened, which is what the ledger's `canceled_by_qfa_id` records."""
    lost = sorted(lost, key=_value_key)
    gained = sorted(gained, key=_value_key)
    pairs = []
    for g in gained:
        if not lost:
            break
        i = min(range(len(lost)),
                key=lambda k: (abs(lost[k]["round_tier"] - g["round_tier"]),
                               -lost[k]["round_tier"], lost[k].get("score", 0)))
        pairs.append((g, lost[i]))
        del lost[i]
    return lost, pairs


def cancel_qfas(lost, gained):
    """The uncancelled lost QFAs only (see `cancel_qfas_pairs`)."""
    return cancel_qfas_pairs(lost, gained)[0]


def allocate_comp_picks(uncancelled, league_cap=LEAGUE_COMP_PICK_CAP, franchise_cap=FRANCHISE_COMP_PICK_CAP):
    """Allocate CBA comp picks from uncancelled lost QFAs: best total score first, at most `league_cap` picks
    league-wide and `franchise_cap` per franchise. Returns the awarded QFAs (a subset of `uncancelled`) in award
    order (descending score, then lower round, then player_id)."""
    per_team = defaultdict(int)
    awarded = []
    order = sorted(uncancelled, key=lambda q: (-q.get("score", 0), q["round_tier"], q.get("player_id") or 0))
    for q in order:
        if len(awarded) >= league_cap:
            break
        if per_team[q["team_id"]] >= franchise_cap:
            continue
        per_team[q["team_id"]] += 1
        awarded.append(q)
    return awarded


# ---------------------------------------------------------------- Resolution JC-2A ----
def jc2a_eligible(departure):
    """Resolution JC-2A trigger, evaluated from explicit inputs (the schema has no demographic or executive-role
    fields — see the module NOTES). `departure`: {is_minority, hire_from_role, hire_to_role, tenure_years,
    hired_away}. True when a minority OC/DC/assistant-GM is hired away by another club as a Head Coach or GM
    with at least JC2A_TENURE_YEARS (2) years of tenure."""
    if not departure.get("is_minority"):
        return False
    if departure.get("hired_away") is False:
        return False
    if departure.get("hire_from_role") not in JC2A_FROM_ROLES:
        return False
    if departure.get("hire_to_role") not in JC2A_TO_ROLES:
        return False
    return (departure.get("tenure_years") or 0) >= JC2A_TENURE_YEARS


def jc2a_awards(departures, draft_season):
    """Two Round-3 JC-2A picks per eligible departure, one in each of the two drafts after `draft_season` (the
    season just played). `departures`: [{team_id, is_minority, hire_from_role, hire_to_role, tenure_years,
    hired_away}]. Returns [{draft_season, team_id, round, award_kind: 'JC2A', from_role, to_role}], never counted
    against the CBA franchise/league caps."""
    awards = []
    for d in departures:
        if not jc2a_eligible(d):
            continue
        for offset in JC2A_PICK_SPREAD:
            awards.append({
                "draft_season": draft_season + offset,
                "team_id": d["team_id"],
                "round": JC2A_ROUND,
                "award_kind": "JC2A",
                "from_role": d.get("hire_from_role"),
                "to_role": d.get("hire_to_role"),
            })
    return awards


# ---------------------------------------------------------------- full season calculation ----
def calculate_comp_picks(lost, gained, league_apys, *, post_draft_deadline=None, jc2a_departures=(),
                         draft_season=None, league_cap=LEAGUE_COMP_PICK_CAP,
                         franchise_cap=FRANCHISE_COMP_PICK_CAP, jc2a_enabled=JC2A_ENABLED):
    """Run the whole season: QFA qualification, valuation, cancellation and allocation.

    `lost` / `gained`: QFA dicts (see module docstring).  `league_apys`: every league contract APY for percentile
    ranking.  `post_draft_deadline`: optional date for the signing-date check.  `jc2a_departures`: JC-2A departure
    dicts; `draft_season` required only when any are supplied.  `league_cap` / `franchise_cap` default to 32 / 4.

    `jc2a_enabled` (default `JC2A_ENABLED`, False): Resolution JC-2A is off by default (JR, 2026-11-08), so a run
    awards only CBA Article 6 picks. Pass True to award the two Round-3 JC-2A picks per eligible departure;
    departures supplied while it is off are ignored rather than erroring.

    Returns {cba_picks, jc2a_picks, total, uncancelled, cancellations}: `cba_picks` are the awarded CBA picks
    (uncancelled lost QFAs, cap applied), `jc2a_picks` the JC-2A awards ([] unless `jc2a_enabled`), `total` their
    sum, `uncancelled` every survivor before the caps (so callers can see what the caps dropped), and
    `cancellations` the `{team_id, gained_player_id, cancelled_player_id}` pairings the ledger records."""
    lost_ev = [evaluate_qfa(q, league_apys) for q in lost if qfa_eligible(q, post_draft_deadline)]
    gained_ev = [evaluate_qfa(q, league_apys) for q in gained if qfa_eligible(q, post_draft_deadline)]
    lost_ev = [v for v in lost_ev if v["qualifies"]]
    gained_ev = [v for v in gained_ev if v["qualifies"]]

    lost_by = defaultdict(list)
    gained_by = defaultdict(list)
    for v in lost_ev:
        lost_by[v["team_id"]].append(v)
    for v in gained_ev:
        gained_by[v["team_id"]].append(v)

    uncancelled, cancellations = [], []
    for tid in sorted(set(lost_by) | set(gained_by)):
        survivors, pairs = cancel_qfas_pairs(lost_by.get(tid, []), gained_by.get(tid, []))
        uncancelled.extend(survivors)
        cancellations.extend({"team_id": tid, "gained_player_id": g.get("player_id"),
                              "cancelled_player_id": c.get("player_id")} for g, c in pairs)

    cba_picks = allocate_comp_picks(uncancelled, league_cap=league_cap, franchise_cap=franchise_cap)

    if jc2a_enabled and jc2a_departures and draft_season is None:
        raise ValueError("JC-2A awards need draft_season")
    jc2a_picks = (jc2a_awards(jc2a_departures, draft_season)
                  if jc2a_enabled and draft_season is not None else [])

    return {"cba_picks": cba_picks, "jc2a_picks": jc2a_picks,
            "total": len(cba_picks) + len(jc2a_picks), "uncancelled": uncancelled,
            "cancellations": cancellations}


# ---------------------------------------------------------------- database helpers (not called offline) ----
# Everything below takes `cur` and is never exercised by the offline unit tests. It maps the pure engine onto the
# tables in db/migrations/2027_02_06_compensatory_draft_picks.sql.

def load_league_apys(cur, season):
    """Every active contract's APY for `season`, for percentile ranking (total_value / length)."""
    cur.execute("""SELECT total_value / NULLIF(length, 0) FROM player_contracts
                   WHERE season = %s AND status = 'ACTIVE' AND length > 0""", (season,))
    return [r[0] for r in cur.fetchall() if r[0] is not None]


def load_qfas(cur, season):
    """qualifying_free_agents rows for `season` as the engine's QFA dict shape. `team_id` is old_team_id for a
    LOST player and new_team_id for a GAINED player — the caller decides by which side it passes the row to."""
    cur.execute("""SELECT player_id, old_team_id, new_team_id, signed_date, total_value, years, apy,
                          snap_pct, honors_points, round_tier, qualifies, canceled_by_qfa_id
                   FROM qualifying_free_agents WHERE season = %s""", (season,))
    out = []
    for pid, old, new, signed, total, years, apy, snap, honors, tier, qualifies, canceled in cur.fetchall():
        out.append({
            "player_id": pid, "old_team_id": old, "new_team_id": new, "signed_date": signed,
            "total_value": total, "years": years, "apy": apy, "snap_pct": float(snap or 0.0),
            "honors_points": honors, "round_tier": tier, "qualifies": qualifies,
            "canceled_by_qfa_id": canceled,
        })
    return out


def save_qfas(cur, rows):
    """Insert qualifying_free_agents rows. `rows`: dicts with season, player_id, old_team_id, new_team_id,
    signed_date, total_value, years, apy, snap_pct, honors_points, round_tier, qualifies, canceled_by_qfa_id."""
    for r in rows:
        cur.execute("""INSERT INTO qualifying_free_agents
                       (season, player_id, old_team_id, new_team_id, signed_date, total_value, years, apy,
                        snap_pct, honors_points, round_tier, qualifies, canceled_by_qfa_id)
                       VALUES (%(season)s, %(player_id)s, %(old_team_id)s, %(new_team_id)s, %(signed_date)s,
                               %(total_value)s, %(years)s, %(apy)s, %(snap_pct)s, %(honors_points)s,
                               %(round_tier)s, %(qualifies)s, %(canceled_by_qfa_id)s)""", r)


def save_awards(cur, awards):
    """Insert compensatory_pick_awards rows. `awards`: dicts with draft_season, team_id, round, slot_in_round,
    overall_pick, award_kind, source_player_id, source_coach_id, reason."""
    for a in awards:
        cur.execute("""INSERT INTO compensatory_pick_awards
                       (draft_season, team_id, round, slot_in_round, overall_pick, award_kind,
                        source_player_id, source_coach_id, reason)
                       VALUES (%(draft_season)s, %(team_id)s, %(round)s, %(slot_in_round)s, %(overall_pick)s,
                               %(award_kind)s, %(source_player_id)s, %(source_coach_id)s, %(reason)s)""", a)


# ------------------------------------------------------------------------------------------------ NOTES ----
# Schema gaps that a follow-up must close before this engine is fully wired (flagged under BLOCKERS in U1's report):
#   1. No demographic/minority field on coaches, coordinators or staff_moves, so JC-2A cannot be detected from the
#      database.  jc2a_eligible/jc2a_awards are explicit-input functions on purpose.
#   2. staff_moves tracks only HC/OC/DC roles and has no GM / assistant-GM or front-office executive rows, so the
#      "hired away as GM" and "assistant GM" legs of JC-2A have no source data yet.
#   3. This module does not invent demographic data and adds no demographic columns to the existing people tables.
