"""Team hub Front Office tab backend (/api/team/{abbr}/front-office; docs/done/team_management, PROJECT_SPEC §4.2).

Read-only, public. It serves the cap sheet and the active contracts (phase 1), the season's moves and draft capital
(phase 2), and the staff: head coach, coordinators, GM, the team's direction and its top needs (phase 3). The rows come from the same fetch helpers the writer tools use (newsroom/db_tools.py),
but go out as raw numbers for the page to format, each carrying its basis (scanned, estimated, from a signing,
projected). A block is null when its table doesn't exist yet (contract migration not applied) or has no rows.

Nothing here reads the GM queue: no proposals, rumors or front-office scores (JR, 2026-10-22 defaults). Executed
trades are served as their assets only, without the GMs' rationale. The GM card carries his persona and tenure, the
direction and the need groups with their reasons in words: no direction or need scores, no persona dials, no
fo_quality / dev_quality / hot-seat numbers, and not who controls the team in Madden. The page shows the league as it stands, so every
loaded move of the season is visible (no article week cutoff).
"""
import os
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from newsroom import roster_moves as rm
from engines import front_office
from engines import report_card_engine as rce
from engines.staff_contracts_engine import godfather_gate
from engines.coach_profile import (PLACEHOLDER_COORDINATOR, TENDENCY_WORDS, coach_profile, effective_tendencies,
                                  fetch_observed, fetch_team_coach)
from newsroom.db_tools import fetch_cap_rows, fetch_moves, fetch_team_contracts, fetch_trades

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
YEAR_COLUMNS = 5          # this season and the four after it, the Team Salary screen's year columns
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
DRAFTS_AHEAD = 3          # draft_picks seeds the next three drafts
NO_CUTOFF = (None, None, True)   # fetch_moves / fetch_trades context: everything loaded is visible
TOP_NEEDS = 3
NEED_GROUPS = {"QB": "Quarterback", "RB": "Running back", "WR": "Receiver", "TE": "Tight end", "OT": "Offensive tackle",
               "IOL": "Interior line", "EDGE": "Edge rusher", "DT": "Defensive tackle", "IDL": "Interior defensive line",
               "LB": "Linebacker", "CB": "Cornerback", "S": "Safety", "K": "Kicker", "P": "Punter"}
NEED_REASONS = (("slot_gap", "thin at the starting spots"), ("injury", "injuries"), ("expiring", "expiring deals"))

router = APIRouter()


def shape_cap(row: dict, teams: int) -> dict:
    """One fetch_cap_rows row as the page's cap block."""
    return {
        "cap": row["cap"],
        "adjusted_cap": row["adjusted_cap"],
        "cap_space": row["cap_space"],
        "cap_space_rank": row["cap_space_rank"],
        "teams": teams,
        "salaries": row["salaries"],
        "dead_money": row["cap_penalty"],
        "total_spend": row["total_spend"],
        "next_rookie_reserve": row["next_rookie_reserve"],
        "estimated": bool(row["derived"]),
        "basis": row["basis"],
    }


def shape_contract(c: dict, season: int) -> dict:
    """One fetch_team_contracts row: every year's hit (projected years flagged), the season it runs through,
    and whether this is his contract year. NULL terms stay null (the page prints 'not known', never $0)."""
    years = sorted(c.get("years") or [], key=lambda y: y[0])
    this_year = next((h for y, h, p in years if y == season), None)
    remaining = c["years_remaining"]
    return {
        "contract_id": c["contract_id"],
        "player_id": c["player_id"],
        "name": c["name"],
        "position": c.get("position"),
        "length": c["length"],
        "years_remaining": remaining,
        "through": season + (remaining or 1) - 1,
        "expiring": remaining == 1,
        "cap_hit": this_year,
        "total_value": c["total_value"],
        "bonus": c["bonus"],
        "cut_savings": c["cut_savings"],
        "cut_penalty": c["cut_penalty"],
        "no_trade_clause": bool(c["no_trade_clause"]),
        "incentive": bool(c.get("incentive")),
        "source": c["source"],
        "basis": rm.contract_basis(c["source"], rm.scan_label(c.get("capture_week"), c.get("scanned_at"))),
        "years": [{"year": y, "cap_hit": h, "projected": bool(p)} for y, h, p in years],
        "new_deal": ({"years": c["extension_years"], "total_value": c["extension_value"],
                      "starts": season + (remaining or 0)} if c.get("extension_years") else None),
    }


def committed(contracts: list, season: int) -> list:
    """Committed cap for each year column: scanned hits and projected new-deal hits kept apart."""
    out = []
    for y in range(season, season + YEAR_COLUMNS):
        hits = [yr for c in contracts for yr in c["years"] if yr["year"] == y and yr["cap_hit"]]
        out.append({"year": y,
                    "scanned": sum(h["cap_hit"] for h in hits if not h["projected"]),
                    "projected": sum(h["cap_hit"] for h in hits if h["projected"]),
                    "players": len({c["player_id"] for c in contracts
                                    if any(yr["year"] == y and yr["cap_hit"] for yr in c["years"])})})
    return out


def shape_move(r: dict, team_id: int, abbrs: dict) -> dict:
    """One fetch_moves row (annotated) as a timeline entry. direction: 'in' (he joins or stays: signed, re-signed,
    extended, restructured) or 'out' (released)."""
    out = r["type"] == "RELEASED" and r["from_team_id"] == team_id
    return {
        "id": r["id"],
        "week": r["week"],
        "type": r["type"],
        "direction": "out" if out else "in",
        "player_id": r["player_id"],
        "name": rm.display_name(r),
        "position": r["position"],
        "from_team": abbrs.get(r["from_team_id"]) if r["type"] == "SIGNED" else None,
        "contract_years": r["contract_years"],
        "contract_value": r["contract_value"],
        "dead_money": r.get("dead_money"),
        "depth_slot": r.get("depth_slot"),
        "is_starter": bool(r.get("is_starter")),
        "practice_squad": bool(r.get("practice_squad")),
        "new_deal_start": r.get("new_deal_start"),
        "later_team": r.get("later_team"),
        "later_week": r.get("later_week"),
        "notable": bool(r.get("notable")),
    }


def shape_trade(t: dict, abbr: str) -> dict:
    """One executed trade from this team's side: the partner and what moved each way (players with their cap hit
    now, picks by draft, round and original team)."""
    def leg(l):
        if l["type"] == "PLAYER":
            return {"type": "PLAYER", "player_id": l["player_id"], "name": l["player_name"],
                    "position": l["position"], "cap_hit": l.get("cap_hit_now")}
        p = l["pick"]
        return {"type": "PICK", "draft_season": p["draft_season"], "round": p["round"],
                "original_team": p["original_team"]}
    return {
        "proposal_id": t["proposal_id"],
        "week": t["week"],
        "partner": t["team_b"] if t["team_a"] == abbr else t["team_a"],
        "received": [leg(l) for l in t["legs"] if l["to"] == abbr],
        "sent": [leg(l) for l in t["legs"] if l["from"] == abbr],
    }


def fetch_picks(cur, team_id: int, season: int) -> list:
    """Every unused pick of the next DRAFTS_AHEAD drafts this team owns or originally held, with the week of the
    trade that moved it (when a GM trade did)."""
    cur.execute("""
        SELECT d.draft_season, d.round, d.overall_pick, d.source, o.abbreviation, w.abbreviation,
               d.original_team_id = %s, d.owner_team_id = %s, g.week
        FROM draft_picks d
        JOIN teams o ON o.id = d.original_team_id JOIN teams w ON w.id = d.owner_team_id
        LEFT JOIN gm_trade_proposals g ON g.id = d.acquired_proposal_id
        WHERE (d.owner_team_id = %s OR d.original_team_id = %s) AND NOT d.used
          AND d.draft_season > %s AND d.draft_season <= %s
        ORDER BY d.draft_season, d.round, d.overall_pick NULLS LAST, o.abbreviation;
    """, (team_id, team_id, team_id, team_id, season, season + DRAFTS_AHEAD))
    out = []
    for ds, rnd, overall, source, orig, owner, is_orig, is_owner, week in cur.fetchall():
        out.append({"draft_season": ds, "round": rnd, "overall_pick": overall, "original_team": orig,
                    "owner_team": owner,
                    "status": "OWN" if is_orig and is_owner else ("ACQUIRED" if is_owner else "TRADED_AWAY"),
                    "source": source, "trade_week": week})
    return out


def shape_needs(needs: list, urgent: dict | None) -> tuple:
    """(top needs, urgent need) in words: the ranked gm_team_seasons needs, each with the reasons whose component is
    above zero, and the injury trigger's group. No scores."""
    def label(g):
        return NEED_GROUPS.get(g, g)
    top = [{"group": n["group"], "label": label(n["group"]),
            "reasons": [words for key, words in NEED_REASONS if (n.get(key) or 0) > 0]}
           for n in (needs or [])[:TOP_NEEDS]]
    urgent_out = {"group": urgent["group"], "label": label(urgent["group"])} if urgent and urgent.get("group") else None
    return top, urgent_out


def shape_head_coach(profile: dict, coach: dict) -> dict:
    """coach_profile's facts the card shows, with tendencies as {tier, meaning, measured}."""
    tendencies = {}
    for key, value in effective_tendencies(coach).items():
        if value:
            tier, source = value
            tendencies[key] = {"tier": tier, "meaning": TENDENCY_WORDS[key][tier],
                               "measured": source.startswith("measured")}
    return {
        "name": profile["coach"],
        "slant": profile["slant"],
        "since": profile["with_team_since"],
        "season_with_team": profile["season_with_team"],
        "replaced": profile["replaced"],
        "offense": profile["schemes"]["offense"],
        "defensive_front": profile["schemes"]["defensive_front"],
        "tendencies": tendencies,
        "this_season": profile["this_season"],
        "record_with_team": profile["record_with_team"],
        "playoff_record_with_team": profile["playoff_record_with_team"],
        "record_with_team_since": profile.get("record_with_team_since"),
        "career": profile["career"],
        "career_scope": profile.get("career_scope", "full career"),
        "super_bowls_with_team": profile["super_bowls_with_team"],
        "coach_of_the_year": profile["coach_of_the_year"],
    }


def fetch_staff(cur, team_id: int, season: int) -> dict | None:
    """Head coach, OC/DC (a stand-in coordinator has no name), GM, direction and top needs; None without a coach."""
    profile = coach_profile(cur, team_id, season, lambda s, w: True, awards_visible=False)
    if not profile:
        return None
    coach = fetch_team_coach(cur, team_id, season)
    coach["observed"] = fetch_observed(cur, team_id, season, coach["id"])
    cur.execute("""
        SELECT ct.role, c.id, c.coordinator_name, c.scheme, c.playbook, ct.start_season
        FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
        WHERE ct.team_id = %s AND ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
        ORDER BY ct.start_season, ct.id;
    """, (team_id, season, season))
    coords = {}
    for role, coord_id, name, scheme, playbook, since in cur.fetchall():   # latest tenure per role wins (fetch_team_staff)
        coords[role] = {"role": role, "id": coord_id, "name": None if PLACEHOLDER_COORDINATOR.search(name or "") else name,
                        "scheme": scheme, "playbook": playbook, "since": since}
    out = {"head_coach": dict(shape_head_coach(profile, coach), id=coach["id"]),   # ids open the Personality row
           "coordinators": [coords[r] for r in ("OC", "DC") if r in coords],
           "gm": None, "direction": None, "needs": [], "urgent_need": None}
    if front_office.installed(cur):
        gm = front_office.get_gm(cur, team_id, season)
        if gm:
            cur.execute("SELECT coach_id FROM gm_profiles WHERE id = %s;", (gm["gm_id"],))
            out["gm"] = {"id": gm["gm_id"], "name": gm["gm_name"], "bio": gm["bio"], "since": gm["start_season"],
                         "head_coach_too": cur.fetchone()[0] is not None}
            out["direction"] = gm["direction"]
            out["needs"], out["urgent_need"] = shape_needs(gm["needs"], gm["urgent_need"])
    return out


def _regclass(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (table,))
    return bool(cur.fetchone()[0])


def _tables_present(cur) -> bool:
    cur.execute("SELECT to_regclass('team_cap_seasons') IS NOT NULL AND to_regclass('player_contracts') IS NOT NULL "
                "AND to_regclass('player_contract_years') IS NOT NULL;")
    return bool(cur.fetchone()[0])


@router.get("/api/team/{abbr}/front-office")
def team_front_office(abbr: str, season: Optional[int] = None):
    clean = (abbr or "").upper().strip()
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, abbreviation FROM teams WHERE abbreviation IN (%s, %s);",
                        (clean, ABBR_ALIASES.get(clean, clean)))
            team = cur.fetchone()
            if not team:
                raise HTTPException(404, f"Team '{abbr}' not found")
            team_id, name, team_abbr = team
            if season is None:
                cur.execute("SELECT current_season FROM league_state LIMIT 1;")
                row = cur.fetchone()
                season = row[0] if row else None
            out = {"season": season, "team": {"id": team_id, "name": name, "abbreviation": team_abbr},
                   "year_columns": list(range(season, season + YEAR_COLUMNS)) if season else [],
                   "cap": None, "contracts": None, "committed": None, "transactions": None, "draft_picks": None,
                   "staff": None, "staff_contracts": None}
            if not season:
                return out
            if _regclass(cur, "coach_tenures"):
                out["staff"] = fetch_staff(cur, team_id, season)
            if _regclass(cur, "staff_contracts"):
                out["staff_contracts"] = fetch_staff_contracts(cur, team_id)
            if front_office.installed(cur) and _regclass(cur, "draft_picks"):
                picks = fetch_picks(cur, team_id, season)
                out["draft_picks"] = {"drafts": list(range(season + 1, season + 1 + DRAFTS_AHEAD)), "picks": picks}
            if _regclass(cur, "transactions"):
                cur.execute("SELECT id, abbreviation FROM teams;")
                abbrs = dict(cur.fetchall())
                moves = rm.annotate([r for r in fetch_moves(cur, season, team_id, ctx=NO_CUTOFF)
                                     if r["type"] != "TRADED"])
                moves.sort(key=lambda r: (-(r["week"] if r["week"] is not None else -1), -r["id"]))
                trades = [shape_trade(t, team_abbr) for t in fetch_trades(cur, season, ctx=NO_CUTOFF)
                          if team_abbr in (t["team_a"], t["team_b"])]
                trades.sort(key=lambda t: -(t["week"] or 0))
                out["transactions"] = {"moves": [shape_move(r, team_id, abbrs) for r in moves], "trades": trades}
            if not _tables_present(cur):
                return out
            caps = fetch_cap_rows(cur, season)
            row = next((c for c in caps if c["team_id"] == team_id), None)
            if row:
                out["cap"] = shape_cap(row, len(caps))
            contracts = [shape_contract(c, season) for c in fetch_team_contracts(cur, season, team_id)]
            if contracts:
                contracts.sort(key=lambda c: (-(c["cap_hit"] or 0), c["name"]))
                out["contracts"] = contracts
                out["committed"] = committed(contracts, season)
            return out


# ------------------------------------------------------------- report cards ----
# NFLPA team report cards (docs/in_progress/NFLPA_REPORT_CARD_PLAN.md §2.5, U3): the read-only card (current +
# history, league rank and percentile, the OpEx policy toggles and the open CapEx projects), plus the two write
# actions JR funds from the owner cash pool. Every read is guarded with to_regclass and a block is null/empty when
# the migration isn't applied or a table has no rows; a grade is never invented. The validation/planning math is in
# the pure helpers below (testable offline), the handlers stay thin and DB-guarded.

REPORT_CARD_COLS = ("season", "weight_room", "training_room", "locker_room", "food_nutrition",
                    "team_travel", "family_treatment", "head_coach", "ownership",
                    "overall_score", "overall_grade", "summary_text")


class UpgradeIn(BaseModel):
    category: str
    tier: str


class PolicyIn(BaseModel):
    policy: str
    enabled: bool


def shape_report_card(row: dict) -> dict:
    """One team_report_cards row as the API's card: the eight category grades, the composite score/grade and the
    summary. The numeric score is normalized to float (it arrives as NUMERIC)."""
    return {
        "season": row["season"],
        "weight_room": row["weight_room"],
        "training_room": row["training_room"],
        "locker_room": row["locker_room"],
        "food_nutrition": row["food_nutrition"],
        "team_travel": row["team_travel"],
        "family_treatment": row["family_treatment"],
        "head_coach": row["head_coach"],
        "ownership": row["ownership"],
        "overall_score": float(row["overall_score"]),
        "overall_grade": row["overall_grade"],
        "summary_text": row["summary_text"],
    }


def rank_percentile(scores: list, value: float) -> tuple:
    """(rank, percentile) of `value` among `scores` (the league's overall_scores for one season). rank is the 1-based
    competition rank (1 = best/highest, ties share the top rank); percentile is the share of teams at or below
    `value` as a 0-100 float rounded to one decimal. Empty scores -> (None, None)."""
    if not scores:
        return None, None
    ordered = sorted(scores, reverse=True)
    rank = next((i + 1 for i, s in enumerate(ordered) if s <= value), len(ordered))
    at_or_below = sum(1 for s in scores if s <= value)
    return rank, round(100.0 * at_or_below / len(scores), 1)


def validate_upgrade_body(category: str, tier: str):
    """400 for a category outside the four physical (CapEx) categories or a tier outside MINOR/STANDARD/OVERHAUL.
    A OpEx category is a policy toggle, not a renovation: the engine only upgrades the physical four."""
    if category not in rce.CAPEX_CATEGORIES:
        raise HTTPException(400, f"category must be one of: {', '.join(rce.CAPEX_CATEGORIES)}")
    if tier not in rce.CAPEX_TIERS:
        raise HTTPException(400, f"tier must be one of: {', '.join(rce.CAPEX_TIERS)}")


def plan_upgrade(card: dict, category: str, tier: str, season: int) -> dict:
    """The facility_upgrades order for a validated CapEx request: cost and target grade from the engine
    (upgrade_cost / upgrade_target_grade) and the engine's 1-season construction delay."""
    return {
        "category": category,
        "cost_dollars": rce.upgrade_cost(tier),
        "target_grade": rce.upgrade_target_grade(card.get(category), tier),
        "completion_season": season + rce.CONSTRUCTION_DELAY_SEASONS,
        "status": "IN_PROGRESS",
    }


def check_cash(available: int, cost: int):
    """409 when the owner's remaining CapEx this season can't fund the order (the message carries both numbers)."""
    if available < cost:
        raise HTTPException(409, f"the owner cash pool can't fund this: ${cost:,} needed, ${available:,} available")


def validate_policy(policy: str):
    """400 for a policy outside luxury_travel / family_daycare / private_chef."""
    if policy not in rce.OPEX_POLICIES:
        raise HTTPException(400, f"policy must be one of: {', '.join(rce.OPEX_POLICIES)}")


def policy_effect(toggles: dict, card: dict, policy: str, enabled: bool) -> tuple:
    """(next_toggles, annual_opex_cost, updated_card) for toggling `policy` to `enabled`. The cost is the sum of
    report_card_engine.opex_cost over every enabled policy after the change; the card gets the policy's immediate
    grade effect (raised to target, never lowered) when enabling, and is unchanged when disabling (a cut only
    affects the next season's survey, plan §2.3b)."""
    next_toggles = dict(toggles)
    next_toggles[policy] = enabled
    cost = sum(rce.opex_cost(p) for p, on in next_toggles.items() if on)
    updated = rce.apply_policy(card, policy) if enabled else dict(card)
    return next_toggles, cost, updated


def _resolve_team(cur, abbr: str) -> tuple:
    """(team_id, name, abbreviation) with the ARI/WSH aliases; 404 for an unknown team."""
    clean = (abbr or "").upper().strip()
    cur.execute("SELECT id, name, abbreviation FROM teams WHERE abbreviation IN (%s, %s);",
                (clean, ABBR_ALIASES.get(clean, clean)))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Team '{abbr}' not found")
    return row[0], row[1], row[2]


def _current_season(cur):
    """league_state.current_season, or None."""
    cur.execute("SELECT current_season FROM league_state LIMIT 1;")
    row = cur.fetchone()
    return row[0] if row else None


@router.get("/api/team/{abbr}/report-card")
def team_report_card(abbr: str, season: Optional[int] = None):
    clean = (abbr or "").upper().strip()
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            team_id, name, team_abbr = _resolve_team(cur, clean)
            if season is None:
                season = _current_season(cur)
            out = {"season": season, "team": {"id": team_id, "name": name, "abbreviation": team_abbr},
                   "cards": None, "current": None, "rank": None, "policies": None, "upgrades": None}
            if not season:
                return out
            if _regclass(cur, "team_report_cards"):
                cur.execute(f"""SELECT {', '.join(REPORT_CARD_COLS)} FROM team_report_cards
                                WHERE team_id = %s ORDER BY season DESC;""", (team_id,))
                cards = [shape_report_card(dict(zip(REPORT_CARD_COLS, r))) for r in cur.fetchall()]
                out["cards"] = cards or None
                current = next((c for c in cards if c["season"] == season), None)
                out["current"] = current
                if current is not None:
                    cur.execute("SELECT overall_score FROM team_report_cards WHERE season = %s;", (season,))
                    scores = [float(r[0]) for r in cur.fetchall()]
                    if scores:
                        rank, pct = rank_percentile(scores, current["overall_score"])
                        out["rank"] = {"rank": rank, "percentile": pct, "teams": len(scores)}
            if _regclass(cur, "team_operating_policies"):
                cur.execute("""SELECT luxury_travel_enabled, family_daycare_enabled, private_chef_program,
                                      annual_opex_cost
                               FROM team_operating_policies WHERE team_id = %s;""", (team_id,))
                p = cur.fetchone()
                if p:
                    out["policies"] = {"luxury_travel": bool(p[0]), "family_daycare": bool(p[1]),
                                       "private_chef": bool(p[2]), "annual_opex_cost": int(p[3])}
            if _regclass(cur, "facility_upgrades"):
                cur.execute("""SELECT id, season_ordered, category, cost_dollars, target_grade, completion_season,
                                      status
                               FROM facility_upgrades WHERE team_id = %s AND status = 'IN_PROGRESS'
                               ORDER BY id;""", (team_id,))
                out["upgrades"] = [{"id": r[0], "season_ordered": r[1], "category": r[2],
                                    "cost_dollars": int(r[3]), "target_grade": r[4], "completion_season": r[5],
                                    "status": r[6]} for r in cur.fetchall()] or None
            return out


@router.post("/api/team/{abbr}/facilities/upgrade")
def facilities_upgrade(abbr: str, body: UpgradeIn):
    validate_upgrade_body(body.category, body.tier)
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            team_id, name, team_abbr = _resolve_team(cur, abbr)
            season = _current_season(cur)
            if season is None:
                raise HTTPException(409, "league_state has no season")
            if not (_regclass(cur, "team_report_cards") and _regclass(cur, "owner_cash_ledgers")
                    and _regclass(cur, "facility_upgrades")):
                raise HTTPException(409, "the report card migration isn't applied")
            cur.execute(f"""SELECT {', '.join(REPORT_CARD_COLS)} FROM team_report_cards
                            WHERE team_id = %s AND season = %s;""", (team_id, season))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, f"{team_abbr} has no report card for season {season}")
            card = dict(zip(REPORT_CARD_COLS, row))
            order = plan_upgrade(card, body.category, body.tier, season)
            cur.execute("""SELECT total_cash_budget, capex_spent, staff_payroll, opex_committed
                           FROM owner_cash_ledgers WHERE season = %s AND team_id = %s;""", (season, team_id))
            ledger = cur.fetchone()
            if not ledger:
                raise HTTPException(409, f"{team_abbr} has no owner cash ledger for season {season}")
            # The cash check has to see the whole ledger (staff payroll and OpEx are commitments too), not only
            # what CapEx already spent.
            check_cash(ledger[0] - ledger[1] - ledger[2] - ledger[3], order["cost_dollars"])
            cur.execute("""SELECT id FROM facility_upgrades WHERE team_id = %s AND category = %s
                           AND status = 'IN_PROGRESS' LIMIT 1;""", (team_id, body.category))
            if cur.fetchone():
                raise HTTPException(409, f"{team_abbr} already has a {body.category} renovation in progress")
            cur.execute("""INSERT INTO facility_upgrades (team_id, season_ordered, category, cost_dollars,
                                                          target_grade, completion_season, status)
                           VALUES (%s, %s, %s, %s, %s, %s, 'IN_PROGRESS') RETURNING id;""",
                        (team_id, season, body.category, order["cost_dollars"], order["target_grade"],
                         order["completion_season"]))
            order["id"] = cur.fetchone()[0]
            # The order spends the owner's cash now, so the ledger records it now (the producer re-reads it).
            cur.execute("""UPDATE owner_cash_ledgers SET capex_spent = capex_spent + %s
                           WHERE season = %s AND team_id = %s;""", (order["cost_dollars"], season, team_id))
            order["season_ordered"] = season
            conn.commit()
            return {"team": team_abbr, "season": season, "order": order}


@router.post("/api/team/{abbr}/facilities/policy")
def facilities_policy(abbr: str, body: PolicyIn):
    validate_policy(body.policy)
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            team_id, name, team_abbr = _resolve_team(cur, abbr)
            season = _current_season(cur)
            if season is None:
                raise HTTPException(409, "league_state has no season")
            if not (_regclass(cur, "team_report_cards") and _regclass(cur, "team_operating_policies")):
                raise HTTPException(409, "the report card migration isn't applied")
            cur.execute(f"""SELECT {', '.join(REPORT_CARD_COLS)} FROM team_report_cards
                            WHERE team_id = %s AND season = %s;""", (team_id, season))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, f"{team_abbr} has no report card for season {season}")
            card = dict(zip(REPORT_CARD_COLS, row))
            cur.execute("""SELECT luxury_travel_enabled, family_daycare_enabled, private_chef_program
                           FROM team_operating_policies WHERE team_id = %s;""", (team_id,))
            p = cur.fetchone()
            toggles = ({"luxury_travel": bool(p[0]), "family_daycare": bool(p[1]), "private_chef": bool(p[2])}
                       if p else {"luxury_travel": False, "family_daycare": False, "private_chef": False})
            next_toggles, cost, updated = policy_effect(toggles, card, body.policy, body.enabled)
            cur.execute("""INSERT INTO team_operating_policies (team_id, luxury_travel_enabled,
                                                                family_daycare_enabled, private_chef_program,
                                                                annual_opex_cost)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (team_id) DO UPDATE SET
                             luxury_travel_enabled = EXCLUDED.luxury_travel_enabled,
                             family_daycare_enabled = EXCLUDED.family_daycare_enabled,
                             private_chef_program = EXCLUDED.private_chef_program,
                             annual_opex_cost = EXCLUDED.annual_opex_cost,
                             updated_at = NOW();""",
                        (team_id, next_toggles["luxury_travel"], next_toggles["family_daycare"],
                         next_toggles["private_chef"], cost))
            # An OpEx toggle is a cash commitment too: the ledger tracks the enabled policies' annual cost, and a
            # switch JR could not fund is refused before the card changes.
            cur.execute("""SELECT total_cash_budget, capex_spent, staff_payroll, opex_committed
                           FROM owner_cash_ledgers WHERE season = %s AND team_id = %s;""", (season, team_id))
            ledger = cur.fetchone()
            if ledger:
                delta = int(cost or 0) - int(ledger[3] or 0)
                if delta > 0:
                    check_cash(ledger[0] - ledger[1] - ledger[2] - ledger[3], delta)
                cur.execute("""UPDATE owner_cash_ledgers SET opex_committed = %s
                               WHERE season = %s AND team_id = %s;""", (cost, season, team_id))
            spec = rce.OPEX_POLICIES[body.policy]
            score = round(rce.facility_score(updated), 3)
            grade = rce.score_to_grade(score)
            if body.enabled:
                # Enabling raises the category immediately (never lowers); a cut only affects the next survey, so
                # the current card's grade is left alone when a policy is turned off.
                cur.execute(f"""UPDATE team_report_cards SET {spec['category']} = %s, overall_score = %s,
                                       overall_grade = %s
                                WHERE team_id = %s AND season = %s;""",
                            (updated[spec["category"]], score, grade, team_id, season))
            conn.commit()
            return {"team": team_abbr, "season": season, "policy": body.policy, "enabled": body.enabled,
                    "annual_opex_cost": cost, "policies": next_toggles, "category": spec["category"],
                    "grade": updated[spec["category"]], "overall_score": score, "overall_grade": grade}


# ------------------------------------------------------------- staff contracts ----
# Staff contracts & full control (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.5, U3): the read-only staff_contracts block
# on GET /api/team/{abbr}/front-office (HC, GM, OC, DC salary, years and full control), and POST /api/team/{abbr}/
# staff/offer so JR can construct a custom offer during the coaching carousel. The read is guarded with to_regclass
# and the block is null/empty when the migration isn't applied or no rows remain; a salary is never invented. The
# offer validates its shape offline and enforces the engine's Godfather rarity gate (at most 2 active full-control
# coaches, a 5-season league-wide cooldown) on a full-control head-coach offer.

STAFF_CONTRACT_COLS = ("person_id", "person_kind", "annual_salary", "total_years", "remaining_years",
                       "signed_season", "full_control")
STAFF_OFFER_KINDS = ("HC", "GM", "OC", "DC")
FULL_CONTROL_KIND = "HC"


def shape_staff_contract(row: dict) -> dict:
    """One staff_contracts row as the page's contract: the role, the annual salary, the total and remaining years,
    and whether it carries full control (a head coach with the final 53-man roster say)."""
    return {
        "person_id": row["person_id"],
        "person_kind": row["person_kind"],
        "annual_salary": int(row["annual_salary"]),
        "total_years": row["total_years"],
        "remaining_years": row["remaining_years"],
        "signed_season": row["signed_season"],
        "full_control": bool(row["full_control"]),
    }


def fetch_staff_contracts(cur, team_id: int) -> list | None:
    """Every active staff contract (remaining_years > 0) in HC, GM, OC, DC order; None without rows. The person's
    name is the StaffCard's job (person_id is polymorphic), so only the contract terms are served here."""
    cur.execute(f"""SELECT {', '.join(STAFF_CONTRACT_COLS)} FROM staff_contracts
                    WHERE team_id = %s AND remaining_years > 0
                    ORDER BY CASE person_kind WHEN 'HC' THEN 1 WHEN 'GM' THEN 2 WHEN 'OC' THEN 3 ELSE 4 END;""",
                (team_id,))
    rows = [shape_staff_contract(dict(zip(STAFF_CONTRACT_COLS, r))) for r in cur.fetchall()]
    return rows or None


class StaffOfferIn(BaseModel):
    person_kind: str
    person_id: int
    annual_salary: int
    total_years: int
    full_control: bool = False
    signed_season: Optional[int] = None


STAFF_OFFER_MAX_YEARS = 10          # a coaching contract is short: the SMALLINT column must not be trusted
STAFF_OFFER_MAX_SALARY = 50_000_000


def validate_staff_offer(person_kind: str, annual_salary: int, total_years: int, full_control: bool):
    """400 for an offer the staff-contract rules don't allow: an unknown role, a salary or length outside the
    sane bounds, or full control — which JR cannot grant at all (plan Q3/D6; only the Godfather path does)."""
    if person_kind not in STAFF_OFFER_KINDS:
        raise HTTPException(400, f"person_kind must be one of: {', '.join(STAFF_OFFER_KINDS)}")
    if not 0 < annual_salary <= STAFF_OFFER_MAX_SALARY:
        raise HTTPException(400, f"annual_salary must be between 1 and {STAFF_OFFER_MAX_SALARY}")
    if not 0 < total_years <= STAFF_OFFER_MAX_YEARS:
        raise HTTPException(400, f"total_years must be between 1 and {STAFF_OFFER_MAX_YEARS}")
    if full_control:
        raise HTTPException(400, "full control is granted only through the Godfather offer, never by hand")


@router.post("/api/team/{abbr}/staff/offer")
def staff_offer(abbr: str, body: StaffOfferIn):
    validate_staff_offer(body.person_kind, body.annual_salary, body.total_years, body.full_control)
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            team_id, name, team_abbr = _resolve_team(cur, abbr)
            season = _current_season(cur)
            if season is None:
                raise HTTPException(409, "league_state has no season")
            if not _regclass(cur, "staff_contracts"):
                raise HTTPException(409, "the staff contracts migration isn't applied")
            from server.franchise_api import user_teams
            teams = user_teams()
            if teams and team_abbr.upper() not in teams:
                raise HTTPException(403, f"{team_abbr} isn't one of your teams ({', '.join(teams)})")
            # The person must exist and must not already hold an active deal of that kind on this team.
            cur.execute("SELECT count(*) FROM staff_contracts WHERE team_id = %s AND person_kind = %s "
                        "AND person_id = %s AND remaining_years > 0", (team_id, body.person_kind, body.person_id))
            if cur.fetchone()[0]:
                raise HTTPException(409, f"{body.person_kind} {body.person_id} already has an active contract")
            signed = body.signed_season if body.signed_season is not None else season
            if signed != season:
                raise HTTPException(400, f"signed_season must be the current season ({season})")
            cur.execute("""INSERT INTO staff_contracts
                           (team_id, person_id, person_kind, annual_salary, total_years, remaining_years,
                            signed_season, full_control)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           RETURNING person_id, person_kind, annual_salary, total_years, remaining_years,
                                     signed_season, full_control;""",
                        (team_id, body.person_id, body.person_kind, body.annual_salary, body.total_years,
                         body.total_years, signed, body.full_control))
            row = cur.fetchone()
            conn.commit()
            return {"team": team_abbr, "season": season,
                    "contract": shape_staff_contract(dict(zip(STAFF_CONTRACT_COLS, row)))}
