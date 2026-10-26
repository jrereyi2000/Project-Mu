"""Team hub Front Office tab backend (/api/team/{abbr}/front-office; docs/team_management, PROJECT_SPEC §4.2).

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

from newsroom import roster_moves as rm
from engines import front_office
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
        SELECT ct.role, c.coordinator_name, c.scheme, c.playbook, ct.start_season
        FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
        WHERE ct.team_id = %s AND ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
        ORDER BY ct.start_season, ct.id;
    """, (team_id, season, season))
    coords = {}
    for role, name, scheme, playbook, since in cur.fetchall():   # latest tenure per role wins (fetch_team_staff)
        coords[role] = {"role": role, "name": None if PLACEHOLDER_COORDINATOR.search(name or "") else name,
                        "scheme": scheme, "playbook": playbook, "since": since}
    out = {"head_coach": shape_head_coach(profile, coach),
           "coordinators": [coords[r] for r in ("OC", "DC") if r in coords],
           "gm": None, "direction": None, "needs": [], "urgent_need": None}
    if front_office.installed(cur):
        gm = front_office.get_gm(cur, team_id, season)
        if gm:
            cur.execute("SELECT coach_id FROM gm_profiles WHERE id = %s;", (gm["gm_id"],))
            out["gm"] = {"name": gm["gm_name"], "bio": gm["bio"], "since": gm["start_season"],
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
                   "staff": None}
            if not season:
                return out
            if _regclass(cur, "coach_tenures"):
                out["staff"] = fetch_staff(cur, team_id, season)
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
