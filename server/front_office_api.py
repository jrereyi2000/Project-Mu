"""Team hub Front Office tab backend (/api/team/{abbr}/front-office; docs/team_management, PROJECT_SPEC §4.2).

Read-only, public. Phase 1 serves the cap sheet and the active contracts; later phases add the season's moves,
draft capital and the staff. The rows come from the same fetch helpers the writer tools use (newsroom/db_tools.py),
but go out as raw numbers for the page to format, each carrying its basis (scanned, estimated, from a signing,
projected). A block is null when its table doesn't exist yet (contract migration not applied) or has no rows.

Nothing here reads the GM queue: no proposals, rumors or front-office scores (JR, 2026-10-22 defaults).
"""
import os
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException

from newsroom import roster_moves as rm
from newsroom.db_tools import fetch_cap_rows, fetch_team_contracts

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
YEAR_COLUMNS = 5          # this season and the four after it, the Team Salary screen's year columns
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}

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
                   "cap": None, "contracts": None, "committed": None}
            if not season or not _tables_present(cur):
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
