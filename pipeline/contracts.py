"""
Contract and cap writes (docs/done/TEAM_MANAGEMENT_PLAN.md §2.1; PROJECT_SPEC §3.3d). The Team Salary load
(pipeline/load_capture.load_contracts) and the transactions load (team management step 2) share them.
Money is whole dollars (pipeline/money.parse_money). Nothing here commits.
"""

CAP_COLUMNS = ("cap", "rollover", "adjusted_cap", "cap_space", "salaries", "cap_penalty", "total_spend",
               "next_rookie_reserve")


def supersede_active(cur, season, team_ids, player_ids):
    """Mark the season's ACTIVE contracts of these teams, and any ACTIVE row of these players on another team
    (a move step 2 hasn't recorded yet), SUPERSEDED. Returns how many rows changed."""
    cur.execute("UPDATE player_contracts SET status = 'SUPERSEDED' WHERE season = %s AND status = 'ACTIVE' "
                "AND (team_id = ANY(%s) OR player_id = ANY(%s))", (season, list(team_ids), list(player_ids)))
    return cur.rowcount


def insert_contract(cur, *, player_id, team_id, season, length, years_remaining, total_value, bonus, cut_savings,
                    cut_penalty, no_trade_clause, incentive, years, source="SCAN", capture_id=None):
    """One ACTIVE player_contracts row and a player_contract_years row per year ({2036: cap hit, ...}; $0 is 0).
    Returns the contract id."""
    cur.execute("INSERT INTO player_contracts (player_id, team_id, season, length, years_remaining, total_value, "
                "bonus, cut_savings, cut_penalty, no_trade_clause, incentive, status, source, capture_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s) RETURNING id",
                (player_id, team_id, season, length, years_remaining, total_value, bonus, cut_savings, cut_penalty,
                 bool(no_trade_clause), incentive, source, capture_id))
    cid = cur.fetchone()[0]
    for year, hit in sorted(years.items()):
        cur.execute("INSERT INTO player_contract_years (contract_id, year, cap_hit) VALUES (%s, %s, %s)",
                    (cid, year, hit or 0))
    return cid


def upsert_team_cap(cur, team_id, season, values, capture_id=None):
    """The team's cap strip for the season; the latest scan wins. values: {column in CAP_COLUMNS: dollars}."""
    cols = [c for c in CAP_COLUMNS if c in values]
    cur.execute(f"INSERT INTO team_cap_seasons (team_id, season, {', '.join(cols)}, scanned_at, capture_id) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}, now(), %s) "
                f"ON CONFLICT (team_id, season) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
                + ", scanned_at = now(), capture_id = EXCLUDED.capture_id",
                (team_id, season, *[values[c] for c in cols], capture_id))
