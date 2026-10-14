"""Preseason fantasy value board and ADP → fantasy_projections week 1 (PROJECT_SPEC §3.4).

draft_score is a consensus value for every QB/HB/WR/TE from OVR, positional scarcity, team offense ranks,
last season's awards and popularity. It is the board the simulated leagues draft from
(engines/fantasy_league_sim.py); ADP is the result of those drafts, not draft_score order.

CLI:
    python3 -m engines.fantasy_adp_engine                  # season from league_state
    python3 -m engines.fantasy_adp_engine --season 2035
    python3 -m engines.fantasy_adp_engine --season 2035 --redraft   # replace an existing simulated draft
"""
import argparse
from typing import Optional

import psycopg

from db.db_handler import DatabaseHandler

BOARD_SIZE = 300  # players written to the week 1 ADP board


def score_player(row: dict, awards: list) -> float:
    base_value = float(row['overall_rating'])

    # --- 1. POSITIONAL SCARCITY & ELITE QB PREMIUM ---
    if row['position'] == 'HB':
        base_value *= 1.35
    elif row['position'] == 'WR':
        base_value *= 1.30
    elif row['position'] == 'TE':
        base_value *= 1.15
    elif row['position'] == 'QB':
        if row['overall_rating'] >= 98:
            base_value *= 1.10  # Transcendent Tier
        elif row['overall_rating'] >= 90:
            base_value *= 1.00  # Franchise Tier
        else:
            base_value *= 0.90  # Streamer Tier

    # --- 2. SITUATIONAL ECOSYSTEM BUMP ---
    if row['position'] in ['WR', 'TE', 'QB'] and row['pass_off_rank'] and row['pass_off_rank'] <= 10:
        base_value += (11 - row['pass_off_rank']) * 0.5
    if row['position'] == 'HB' and row['rush_off_rank'] and row['rush_off_rank'] <= 10:
        base_value += (11 - row['rush_off_rank']) * 0.5

    # --- 3. PRESTIGE BUMP (Previous Year Awards) ---
    if 'MVP' in awards:
        base_value += 8.0
    if 'OPOY' in awards:
        base_value += 6.0
    if 'SBMVP' in awards:
        base_value += 4.0
    if 'OROY' in awards:
        base_value += 5.0  # High breakout hype!

    # --- 4. CULTURAL RELEVANCE (POPULARITY TIEBREAKER) ---
    pop_idx = float(row.get('popularity_index') or 50.0)
    base_value += (pop_idx / 100.0) * 3.0  # 0-100 popularity → +0 to +3.0

    # --- 5. ROSTER CLOGGING PENALTY ---
    if row['overall_rating'] < 80:
        base_value *= 0.7
    return round(base_value, 2)


def load_draft_board(cur, season: int) -> list:
    """Every rostered QB/HB/WR/TE with its draft_score, best first."""
    cur.execute("""
        SELECT p.id, p.player_name, COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating,
               ps.popularity_index, t.id, ts.pass_off_rank, ts.rush_off_rank
        FROM player_seasons ps
        JOIN players p ON ps.player_id = p.id
        JOIN teams t ON ps.team_id = t.id
        JOIN team_seasons ts ON t.id = ts.team_id AND ts.season = ps.season
        WHERE ps.season = %s AND ps.position IN ('QB', 'HB', 'WR', 'TE')
    """, (season,))
    players = [dict(zip(("player_id", "player_name", "full_name", "position", "overall_rating",
                         "popularity_index", "team_id", "pass_off_rank", "rush_off_rank"), r))
               for r in cur.fetchall()]

    cur.execute("""
        SELECT award_type, player_name FROM awards
        WHERE season = %s AND award_type IN ('MVP', 'OPOY', 'OROY', 'SBMVP')
    """, (season - 1,))
    award_winners = {}
    for award, name in cur.fetchall():
        award_winners.setdefault(name, []).append(award)

    for p in players:
        p["overall_rating"] = p["overall_rating"] or 0
        p["draft_score"] = score_player(p, award_winners.get(p["full_name"], []))
    return sorted(players, key=lambda p: (-p["draft_score"], p["player_id"]))


def generate_preseason_adp(season: int = 2035, redraft: bool = False, conn_info: Optional[str] = None) -> None:
    """Run the simulated drafts (if not already run) and write the week 1 ADP board from their results."""
    from engines import fantasy_league_sim as sim

    print(f"🏈 Generating Preseason ADP Board for {season}...")
    with psycopg.connect(conn_info or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            board = load_draft_board(cur, season)
        adp = sim.ensure_draft(conn, season, board, redraft=redraft)

        ranked = sorted(board, key=lambda p: (adp[p["player_id"]]["adp"], -p["draft_score"], p["player_id"]))
        print(f"\n🏆 {season} PRESEASON ADP (TOP 20, {sim.LEAGUES} simulated drafts)")
        print("=" * 78)
        for i, p in enumerate(ranked[:20], start=1):
            a = adp[p["player_id"]]
            print(f"{i:>3}. {p['full_name']:<26} {p['position']:<3} OVR {p['overall_rating']:<3} "
                  f"ADP {a['adp']:>5.1f}  range {a['min_pick']}–{a['max_pick']}")

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO fantasy_projections (season, week, player_id, player_name, team_id, adp_rank, position)
                VALUES (%s, 1, %s, %s, %s, %s, %s)
                ON CONFLICT (season, week, player_id) DO UPDATE SET
                    player_name = EXCLUDED.player_name,
                    team_id = EXCLUDED.team_id,
                    adp_rank = EXCLUDED.adp_rank,
                    position = EXCLUDED.position
            """, [(season, p["player_id"], p["player_name"], p["team_id"], i, p["position"])
                  for i, p in enumerate(ranked[:BOARD_SIZE], start=1)])
            # Weeks 2–18 carry adp_rank for the ROS TREND; keep them in step with the new board.
            cur.execute("""
                UPDATE fantasy_projections fp SET adp_rank = b.adp_rank
                FROM fantasy_projections b
                WHERE fp.season = %s AND fp.week > 1 AND b.season = fp.season AND b.week = 1
                  AND b.player_id = fp.player_id
            """, (season,))
        conn.commit()
    print("✅ Preseason ADP board saved (fantasy_projections week 1).")


def main():
    parser = argparse.ArgumentParser(description="Preseason fantasy ADP from simulated drafts")
    parser.add_argument("--season", type=int)
    parser.add_argument("--redraft", action="store_true", help="Replace an existing simulated draft")
    args = parser.parse_args()
    season = args.season
    if season is None:
        with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
            season = conn.execute("SELECT current_season FROM league_state LIMIT 1").fetchone()[0]
    generate_preseason_adp(season, redraft=args.redraft)


if __name__ == "__main__":
    main()
