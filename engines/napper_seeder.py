"""Napper Fantasy Platform Database Seeder (engines/napper_seeder.py).

Populates:
1. User profile (@jrereyi2000 - 'The Straw Hat Pirates')
2. Reference players and their 2035 projections
3. 3 Reference leagues:
   - 'Solo 2026 8-Team SF PPR' (8-team, Superflex, season 2035, week 6, Straw Hats vs Team 3)
   - 'The Justice League' (12-team PPR, in-season)
   - 'The Avengers' (8-team Dynasty PPR, status: PRE_DRAFT)
4. 14-week schedules and head-to-head matchups
5. Full starting & bench rosters matching reference specs
6. Pre-seeded league chat banter & bot award cards
7. Pending AI trade offers
"""

import os
import random
from typing import List, Dict, Any, Optional
import psycopg
from psycopg.rows import dict_row

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

REFERENCE_PLAYERS = [
    # User team reference starters
    {"player_name": "J. Allen", "full_name": "Josh Allen", "pos": "QB", "team": "BUF", "espn_id": 3918298, "proj": 24.95, "num": 17},
    {"player_name": "J. Love", "full_name": "Jordan Love", "pos": "QB", "team": "GB", "espn_id": 4036378, "proj": 0.00, "num": 10},
    {"player_name": "D. Swift", "full_name": "D'Andre Swift", "pos": "RB", "team": "CHI", "espn_id": 4258137, "proj": 14.50, "num": 4},
    {"player_name": "N. Collins", "full_name": "Nico Collins", "pos": "WR", "team": "HOU", "espn_id": 4258168, "proj": 17.80, "num": 12},
    {"player_name": "P. Nacua", "full_name": "Puka Nacua", "pos": "WR", "team": "LAR", "espn_id": 4426515, "proj": 18.20, "num": 17},
    {"player_name": "S. LaPorta", "full_name": "Sam LaPorta", "pos": "TE", "team": "DET", "espn_id": 4428989, "proj": 12.80, "num": 87},
    {"player_name": "A. St. Brown", "full_name": "Amon-Ra St. Brown", "pos": "WR", "team": "DET", "espn_id": 4374302, "proj": 18.40, "num": 14},
    {"player_name": "P. Mahomes", "full_name": "Patrick Mahomes", "pos": "QB", "team": "KC", "espn_id": 3139477, "proj": 22.40, "num": 15},
    {"player_name": "B. Aubrey", "full_name": "Brandon Aubrey", "pos": "K", "team": "DAL", "espn_id": 4684074, "proj": 9.50, "num": 17},
    {"player_name": "BUF Defense", "full_name": "Buffalo Bills D/ST", "pos": "DEF", "team": "BUF", "espn_id": 99001, "proj": 7.00, "num": 0},
    # User team bench
    {"player_name": "A. Jones", "full_name": "Aaron Jones", "pos": "RB", "team": "MIN", "espn_id": 3042519, "proj": 12.50, "num": 33},
    {"player_name": "C. Lamb", "full_name": "CeeDee Lamb", "pos": "WR", "team": "DAL", "espn_id": 4241389, "proj": 16.90, "num": 88},
    {"player_name": "B. Purdy", "full_name": "Brock Purdy", "pos": "QB", "team": "SF", "espn_id": 4361741, "proj": 17.10, "num": 13},
    {"player_name": "B. Hall", "full_name": "Breece Hall", "pos": "RB", "team": "NYJ", "espn_id": 4427366, "proj": 15.30, "num": 20},
    {"player_name": "D. Goedert", "full_name": "Dallas Goedert", "pos": "TE", "team": "PHI", "espn_id": 3121422, "proj": 8.70, "num": 88},
    {"player_name": "T. Bass", "full_name": "Tyler Bass", "pos": "K", "team": "BUF", "espn_id": 4038942, "proj": 8.00, "num": 2},

    # Team 3 reference starters
    {"player_name": "D. Prescott", "full_name": "Dak Prescott", "pos": "QB", "team": "DAL", "espn_id": 2577417, "proj": 18.45, "num": 4},
    {"player_name": "K. Walker", "full_name": "Kenneth Walker III", "pos": "RB", "team": "SEA", "espn_id": 4430737, "proj": 22.76, "num": 9},
    {"player_name": "D. Montgomery", "full_name": "David Montgomery", "pos": "RB", "team": "DET", "espn_id": 4035538, "proj": 13.90, "num": 5},
    {"player_name": "J. Smith-Njigba", "full_name": "Jaxon Smith-Njigba", "pos": "WR", "team": "SEA", "espn_id": 4430878, "proj": 15.60, "num": 11},
    {"player_name": "J. Jefferson", "full_name": "Justin Jefferson", "pos": "WR", "team": "MIN", "espn_id": 4262921, "proj": 19.40, "num": 18},
    {"player_name": "T. Kelce", "full_name": "Travis Kelce", "pos": "TE", "team": "KC", "espn_id": 15847, "proj": 11.20, "num": 87},
    {"player_name": "S. Barkley", "full_name": "Saquon Barkley", "pos": "RB", "team": "PHI", "espn_id": 3929630, "proj": 17.50, "num": 26},
    {"player_name": "J. Hurts", "full_name": "Jalen Hurts", "pos": "QB", "team": "PHI", "espn_id": 4040715, "proj": 21.00, "num": 1},
    {"player_name": "J. Tucker", "full_name": "Justin Tucker", "pos": "K", "team": "BAL", "espn_id": 15683, "proj": 8.50, "num": 9},
    {"player_name": "BAL Defense", "full_name": "Baltimore Ravens D/ST", "pos": "DEF", "team": "BAL", "espn_id": 99002, "proj": 6.50, "num": 0},
    # Team 3 bench
    {"player_name": "J. Cook", "full_name": "James Cook", "pos": "RB", "team": "BUF", "espn_id": 4361536, "proj": 13.20, "num": 4},
    {"player_name": "T. McLaurin", "full_name": "Terry McLaurin", "pos": "WR", "team": "WSH", "espn_id": 3121422, "proj": 12.80, "num": 17},
    {"player_name": "D. Smith", "full_name": "DeVonta Smith", "pos": "WR", "team": "PHI", "espn_id": 4241478, "proj": 13.50, "num": 6},
    {"player_name": "B. Mayfield", "full_name": "Baker Mayfield", "pos": "QB", "team": "TB", "espn_id": 3052587, "proj": 16.00, "num": 6},
    {"player_name": "J. Ferguson", "full_name": "Jake Ferguson", "pos": "TE", "team": "DAL", "espn_id": 4242372, "proj": 9.10, "num": 87},
    {"player_name": "H. Butker", "full_name": "Harrison Butker", "pos": "K", "team": "KC", "espn_id": 3055899, "proj": 8.50, "num": 7},
]


def get_or_create_player(cur, p_info: dict) -> int:
    """Finds or inserts player into players table and returns player_id."""
    cur.execute(
        "SELECT id FROM players WHERE full_name = %s OR player_name = %s LIMIT 1",
        (p_info["full_name"], p_info["player_name"])
    )
    row = cur.fetchone()
    if row:
        player_id = row[0]
    else:
        cur.execute(
            """
            INSERT INTO players (player_name, full_name, espn_id, player_number)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (p_info["player_name"], p_info["full_name"], p_info.get("espn_id"), p_info.get("num", 0))
        )
        player_id = cur.fetchone()[0]

    # Find team
    TEAM_ABBR_MAP = {"WSH": "WAS", "ARI": "AZ", "GNB": "GB", "KAN": "KC", "SFO": "SF", "NOR": "NO", "NWE": "NE", "LVR": "LV"}
    abbr = TEAM_ABBR_MAP.get(p_info["team"], p_info["team"])
    cur.execute(
        "SELECT id FROM teams WHERE abbreviation = %s OR name ILIKE %s LIMIT 1",
        (abbr, f"%{abbr}%")
    )
    team_row = cur.fetchone()
    if not team_row:
        cur.execute("SELECT id FROM teams LIMIT 1")
        team_row = cur.fetchone()
    team_id = team_row[0]

    # Insert player_seasons for 2035
    cur.execute(
        """
        INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating)
        VALUES (%s, %s, 2035, %s, 26, 88)
        ON CONFLICT (player_id, season) DO UPDATE
        SET position = EXCLUDED.position, team_id = EXCLUDED.team_id
        """,
        (player_id, team_id, p_info["pos"])
    )

    # Insert fantasy_projections for weeks 1..14
    for wk in range(1, 15):
        base_proj = p_info["proj"]
        cur.execute(
            """
            INSERT INTO fantasy_projections (season, week, player_id, player_name, team_id, projected_points, position)
            VALUES (2035, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (wk, player_id, p_info["player_name"], team_id, base_proj, p_info["pos"])
        )

    return player_id


def build_schedule(n_teams: int, weeks: int = 14) -> List[List[tuple]]:
    """Round robin generator for n teams across weeks."""
    order = list(range(n_teams))
    rounds = []
    for _ in range(n_teams - 1):
        pairings = []
        for i in range(n_teams // 2):
            home = order[i]
            away = order[n_teams - 1 - i]
            pairings.append((home, away))
        rounds.append(pairings)
        order = [order[0]] + [order[-1]] + order[1:-1]
    return [rounds[w % len(rounds)] for w in range(weeks)]


def seed_napper_database():
    print("🚀 Seeding Napper Fantasy Database...")
    conn = psycopg.connect(CONN_INFO)
    with conn.cursor() as cur:
        # Clear existing napper data to allow clean idempotent re-seed
        cur.execute("TRUNCATE TABLE napper_chat, napper_trades, napper_matchups, napper_rosters, napper_teams, napper_leagues, napper_user_profiles RESTART IDENTITY CASCADE;")

        # 1. User Profile
        cur.execute(
            """
            INSERT INTO napper_user_profiles (handle, display_name, avatar_url, trophies)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (
                "jrereyi2000",
                "The Straw Hat Pirates",
                "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&h=256&q=80",
                3
            )
        )
        user_id = cur.fetchone()[0]
        print(f"👤 Created user profile: @jrereyi2000 (ID: {user_id})")

        # 2. Reference Players
        player_map = {}
        for p in REFERENCE_PLAYERS:
            pid = get_or_create_player(cur, p)
            player_map[p["player_name"]] = pid
        print(f"🏈 Seeded {len(player_map)} reference fantasy players.")

        # Also get a pool of existing players in DB for additional teams/bench
        cur.execute("""
            SELECT p.id, p.player_name, ps.position
            FROM players p
            JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = 2035
            LIMIT 200;
        """)
        extra_players = cur.fetchall()

        # 3. Seed Leagues
        # League A: 'Solo 2026 8-Team SF PPR'
        cur.execute(
            """
            INSERT INTO napper_leagues (name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "Solo 2026 8-Team SF PPR",
                2035,
                8,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6
                }),
                "IN_SEASON",
                "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=256&h=256&q=80",
                6
            )
        )
        l1_id = cur.fetchone()[0]

        # League B: 'The Justice League'
        cur.execute(
            """
            INSERT INTO napper_leagues (name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "The Justice League",
                2035,
                12,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6
                }),
                "IN_SEASON",
                "https://images.unsplash.com/photo-1563089145-599997674d42?auto=format&fit=crop&w=256&h=256&q=80",
                6
            )
        )
        l2_id = cur.fetchone()[0]

        # League C: 'The Avengers'
        cur.execute(
            """
            INSERT INTO napper_leagues (name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "The Avengers",
                2035,
                8,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 8
                }),
                "PRE_DRAFT",
                "https://images.unsplash.com/photo-1635863138275-d9b33299680b?auto=format&fit=crop&w=256&h=256&q=80",
                1
            )
        )
        l3_id = cur.fetchone()[0]
        print(f"🏆 Seeded 3 leagues: {l1_id} (Solo SF), {l2_id} (Justice League), {l3_id} (Avengers)")

        # 4. Teams for League 1: 'Solo 2026 8-Team SF PPR'
        l1_team_configs = [
            # index 0: User team
            {"name": "The Straw Hat Pirates", "handle": "jrereyi2000", "user_id": user_id, "is_ai": False, "persona": "USER", "w": 5, "l": 0, "t": 0, "pf": 785.40, "pa": 610.20, "seed": 1},
            # index 1: Team 2
            {"name": "Team 2", "handle": "gridiron_guru", "user_id": None, "is_ai": True, "persona": "ANALYTICS_NERD", "w": 3, "l": 2, "t": 0, "pf": 654.20, "pa": 620.10, "seed": 4},
            # index 2: Team 3 (Week 6 opponent)
            {"name": "Team 3", "handle": "touchdown_todd", "user_id": None, "is_ai": True, "persona": "TRADE_FIEND", "w": 4, "l": 1, "t": 0, "pf": 712.50, "pa": 645.00, "seed": 2},
            # index 3: Team 4
            {"name": "Team 4", "handle": "waiver_hawk", "user_id": None, "is_ai": True, "persona": "WAIVER_HAWK", "w": 3, "l": 2, "t": 0, "pf": 641.80, "pa": 632.50, "seed": 3},
            # index 4: Team 5
            {"name": "Team 5", "handle": "blitz_bro", "user_id": None, "is_ai": True, "persona": "TRASH_TALKER", "w": 2, "l": 3, "t": 0, "pf": 592.10, "pa": 651.40, "seed": 5},
            # index 5: Team 6
            {"name": "Team 6", "handle": "zero_rb_dan", "user_id": None, "is_ai": True, "persona": "ZERO_RB_ZEALOT", "w": 2, "l": 3, "t": 0, "pf": 583.40, "pa": 662.00, "seed": 6},
            # index 6: Team 7
            {"name": "Team 7", "handle": "dynasty_dave", "user_id": None, "is_ai": True, "persona": "DIE_HARD", "w": 1, "l": 4, "t": 0, "pf": 542.00, "pa": 681.30, "seed": 7},
            # index 7: Team 8
            {"name": "Team 8", "handle": "sleeper_steve", "user_id": None, "is_ai": True, "persona": "UNDERDOG", "w": 0, "l": 5, "t": 0, "pf": 511.90, "pa": 720.50, "seed": 8},
        ]
        l1_team_ids = []
        for tc in l1_team_configs:
            cur.execute(
                """
                INSERT INTO napper_teams (
                    league_id, user_id, team_name, manager_handle, avatar_url,
                    is_ai, persona, wins, losses, ties, points_for, points_against, seed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    l1_id, tc["user_id"], tc["name"], tc["handle"],
                    f"https://api.dicebear.com/7.x/identicon/svg?seed={tc['name']}",
                    tc["is_ai"], tc["persona"], tc["w"], tc["l"], tc["t"], tc["pf"], tc["pa"], tc["seed"]
                )
            )
            l1_team_ids.append(cur.fetchone()[0])

        user_team_id = l1_team_ids[0]
        team_3_id = l1_team_ids[2]

        # 5. Rosters for League 1
        # User team starters matching reference:
        user_starters = [
            ("J. Allen", "QB"),
            ("J. Love", "RB"),
            ("D. Swift", "RB"),
            ("N. Collins", "WR"),
            ("P. Nacua", "WR"),
            ("S. LaPorta", "TE"),
            ("A. St. Brown", "FLEX"),
            ("P. Mahomes", "SUPERFLEX"),
            ("B. Aubrey", "K"),
            ("BUF Defense", "DEF"),
        ]
        user_bench = [
            ("A. Jones", "BN"),
            ("C. Lamb", "BN"),
            ("B. Purdy", "BN"),
            ("B. Hall", "BN"),
            ("D. Goedert", "BN"),
            ("T. Bass", "BN"),
        ]

        # Team 3 starters matching reference:
        team_3_starters = [
            ("D. Prescott", "QB"),
            ("K. Walker", "RB"),
            ("D. Montgomery", "RB"),
            ("J. Smith-Njigba", "WR"),
            ("J. Jefferson", "WR"),
            ("T. Kelce", "TE"),
            ("S. Barkley", "FLEX"),
            ("J. Hurts", "SUPERFLEX"),
            ("J. Tucker", "K"),
            ("BAL Defense", "DEF"),
        ]
        team_3_bench = [
            ("J. Cook", "BN"),
            ("T. McLaurin", "BN"),
            ("D. Smith", "BN"),
            ("B. Mayfield", "BN"),
            ("J. Ferguson", "BN"),
            ("H. Butker", "BN"),
        ]

        # Populate rosters for all weeks (1..14) so lineup queries work for any week
        for wk in range(1, 15):
            for pname, slot in user_starters + user_bench:
                pid = player_map[pname]
                cur.execute(
                    """
                    INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = EXCLUDED.slot
                    """,
                    (l1_id, user_team_id, wk, pid, slot)
                )

            for pname, slot in team_3_starters + team_3_bench:
                pid = player_map[pname]
                cur.execute(
                    """
                    INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = EXCLUDED.slot
                    """,
                    (l1_id, team_3_id, wk, pid, slot)
                )

            # Assign extra players to other teams in League 1
            if extra_players:
                p_idx = 0
                for tid in l1_team_ids:
                    if tid in (user_team_id, team_3_id):
                        continue
                    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF", "BN", "BN", "BN"]
                    for slot in slots:
                        if p_idx < len(extra_players):
                            extra_pid = extra_players[p_idx][0]
                            p_idx += 1
                            cur.execute(
                                """
                                INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (team_id, week, player_id) DO NOTHING
                                """,
                                (l1_id, tid, wk, extra_pid, slot)
                            )

        # 6. Schedules & Matchups for League 1 (14 Weeks)
        # Note: in week 6, User Team (index 0) MUST play Team 3 (index 2)!
        sched_8 = build_schedule(8, 14)

        # Adjust week 6 pairing so team 0 plays team 2:
        # Find pairing with team 0 in week 6 (index 5)
        w6_pairs = list(sched_8[5])
        # Find who team 0 is playing, and who team 2 is playing
        p0 = next((p for p in w6_pairs if 0 in p), None)
        p2 = next((p for p in w6_pairs if 2 in p), None)
        if p0 and p2 and p0 != p2:
            other0 = p0[1] if p0[0] == 0 else p0[0]
            other2 = p2[1] if p2[0] == 2 else p2[0]
            w6_pairs.remove(p0)
            w6_pairs.remove(p2)
            w6_pairs.append((0, 2))
            w6_pairs.append((other0, other2))
            sched_8[5] = w6_pairs

        for wk_idx, week_pairings in enumerate(sched_8):
            wk_num = wk_idx + 1
            for home_idx, away_idx in week_pairings:
                h_id = l1_team_ids[home_idx]
                a_id = l1_team_ids[away_idx]

                if wk_num < 6:
                    # Completed weeks
                    # Give user team wins in weeks 1..5
                    if h_id == user_team_id:
                        h_score, a_score = 138.40 + wk_num * 3.5, 115.20 + wk_num * 2.1
                    elif a_id == user_team_id:
                        a_score, h_score = 142.10 + wk_num * 3.1, 120.40 + wk_num * 1.8
                    else:
                        h_score, a_score = 118.00 + random.randint(0, 30), 112.00 + random.randint(0, 30)
                    cur.execute(
                        """
                        INSERT INTO napper_matchups (
                            league_id, week, home_team_id, away_team_id,
                            home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (l1_id, wk_num, h_id, a_id, h_score, a_score, h_score, a_score, 0.55 if h_score > a_score else 0.45, True)
                    )
                elif wk_num == 6:
                    # Current week: Straw Hats vs Team 3
                    if (h_id == user_team_id and a_id == team_3_id) or (a_id == user_team_id and h_id == team_3_id):
                        if h_id == user_team_id:
                            cur.execute(
                                """
                                INSERT INTO napper_matchups (
                                    league_id, week, home_team_id, away_team_id,
                                    home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (l1_id, wk_num, h_id, a_id, 152.14, 113.95, 152.14, 113.95, 0.760, False)
                            )
                        else:
                            cur.execute(
                                """
                                INSERT INTO napper_matchups (
                                    league_id, week, home_team_id, away_team_id,
                                    home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (l1_id, wk_num, h_id, a_id, 113.95, 152.14, 113.95, 152.14, 0.240, False)
                            )
                    else:
                        cur.execute(
                            """
                            INSERT INTO napper_matchups (
                                league_id, week, home_team_id, away_team_id,
                                home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (l1_id, wk_num, h_id, a_id, 0.0, 0.0, 120.0, 118.0, 0.510, False)
                        )
                else:
                    # Future weeks (7..14)
                    cur.execute(
                        """
                        INSERT INTO napper_matchups (
                            league_id, week, home_team_id, away_team_id,
                            home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (l1_id, wk_num, h_id, a_id, 0.0, 0.0, 125.0, 120.0, 0.520, False)
                    )

        # 7. Pre-seed League 2: 'The Justice League' (12 teams, in-season)
        justice_team_names = [
            ("Metropolis Kryptonians", "clark_kent", True, "DIE_HARD"),
            ("Gotham Dark Knights", "bruce_wayne", True, "ANALYTICS_NERD"),
            ("The Straw Hat Pirates", "jrereyi2000", False, "USER"),
            ("Themyscira Amazons", "diana_prince", True, "TRASH_TALKER"),
            ("Central City Speedsters", "barry_allen", True, "WAIVER_HAWK"),
            ("Atlantis Tridents", "arthur_curry", True, "TRADE_FIEND"),
            ("Star City Archers", "oliver_queen", True, "ZERO_RB_ZEALOT"),
            ("Coast City Lanterns", "hal_jordan", True, "DIE_HARD"),
            ("Detroit Cyborgs", "victor_stone", True, "ANALYTICS_NERD"),
            ("Mars Manhunters", "j_jonzz", True, "UNDERDOG"),
            ("Thanagar Hawks", "katar_hol", True, "TRASH_TALKER"),
            ("Titans Tower", "dick_grayson", True, "WAIVER_HAWK"),
        ]
        l2_team_ids = []
        for idx, (tname, handle, is_ai, persona) in enumerate(justice_team_names):
            u_id = user_id if not is_ai else None
            w = 3 if idx % 2 == 0 else 2
            l = 5 - w
            cur.execute(
                """
                INSERT INTO napper_teams (
                    league_id, user_id, team_name, manager_handle, avatar_url,
                    is_ai, persona, wins, losses, ties, points_for, points_against, seed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (l2_id, u_id, tname, handle, f"https://api.dicebear.com/7.x/identicon/svg?seed={tname}", is_ai, persona, w, l, 0, 600.0 + idx * 10, 590.0 + idx * 8, idx + 1)
            )
            l2_team_ids.append(cur.fetchone()[0])

        # 14 weeks matchups for League 2
        sched_12 = build_schedule(12, 14)
        for wk_idx, week_pairings in enumerate(sched_12):
            wk_num = wk_idx + 1
            for h_idx, a_idx in week_pairings:
                h_id = l2_team_ids[h_idx]
                a_id = l2_team_ids[a_idx]
                is_final = wk_num < 6
                cur.execute(
                    """
                    INSERT INTO napper_matchups (
                        league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (l2_id, wk_num, h_id, a_id, 120.0 if is_final else 0.0, 115.0 if is_final else 0.0, 122.0, 118.0, 0.52, is_final)
                )

        # 8. Pre-seed League 3: 'The Avengers' (8 teams, PRE_DRAFT)
        avengers_team_names = [
            ("Stark Industries", "tony_stark", True, "TRADE_FIEND"),
            ("Wakanda Forever", "t_challa", True, "ANALYTICS_NERD"),
            ("Asgard Thunder", "thor_odinson", True, "TRASH_TALKER"),
            ("The Straw Hat Pirates", "jrereyi2000", False, "USER"),
            ("Web Slingers", "peter_parker", True, "UNDERDOG"),
            ("Super Soldiers", "steve_rogers", True, "DIE_HARD"),
            ("Green Goliaths", "bruce_banner", True, "WAIVER_HAWK"),
            ("S.H.I.E.L.D. Agents", "nick_fury", True, "ANALYTICS_NERD"),
        ]
        l3_team_ids = []
        for idx, (tname, handle, is_ai, persona) in enumerate(avengers_team_names):
            u_id = user_id if not is_ai else None
            cur.execute(
                """
                INSERT INTO napper_teams (
                    league_id, user_id, team_name, manager_handle, avatar_url,
                    is_ai, persona, wins, losses, ties, points_for, points_against, seed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (l3_id, u_id, tname, handle, f"https://api.dicebear.com/7.x/identicon/svg?seed={tname}", is_ai, persona, 0, 0, 0, 0.0, 0.0, idx + 1)
            )
            l3_team_ids.append(cur.fetchone()[0])

        # 14 weeks matchups for League 3 (all pending / pre-draft)
        for wk_idx, week_pairings in enumerate(sched_8):
            wk_num = wk_idx + 1
            for h_idx, a_idx in week_pairings:
                h_id = l3_team_ids[h_idx]
                a_id = l3_team_ids[away_idx]
                cur.execute(
                    """
                    INSERT INTO napper_matchups (
                        league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_proj, away_proj, win_prob_home, is_final
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (l3_id, wk_num, h_id, a_id, 0.0, 0.0, 115.0, 115.0, 0.50, False)
                )

        # 9. Pre-seed League Chat for League 1
        chat_entries = [
            (
                l1_id,
                "SYSTEM_BOT",
                "Napper Awards Bot",
                "napper_bot",
                "https://api.dicebear.com/7.x/bottts/svg?seed=NapperBot",
                "🏆 Week 5 Awards: Highest Scorer of the Week goes to The Straw Hat Pirates (158.4 pts)!",
                psycopg.types.json.Jsonb({
                    "award_type": "HIGH_SCORER",
                    "title": "High Scorer of Week 5",
                    "recipient_name": "The Straw Hat Pirates",
                    "points": 158.4,
                    "badge": "🔥"
                })
            ),
            (
                l1_id,
                "SYSTEM_BOT",
                "Napper Awards Bot",
                "napper_bot",
                "https://api.dicebear.com/7.x/bottts/svg?seed=NapperBot",
                "🪑 RB Benchwarmer of the Week: Aaron Jones scored 18.2 FPTS on The Straw Hat Pirates bench!",
                psycopg.types.json.Jsonb({
                    "award_type": "BENCHWARMER",
                    "title": "RB Benchwarmer of the Week",
                    "player_name": "Aaron Jones",
                    "points": 18.2,
                    "manager": "@jrereyi2000"
                })
            ),
            (
                l1_id,
                "AI_MANAGER",
                "Todd (Team 3)",
                "touchdown_todd",
                "https://api.dicebear.com/7.x/identicon/svg?seed=Team%203",
                "Good luck this week @jrereyi2000! My squad is ready for Week 6.",
                None
            ),
            (
                l1_id,
                "AI_MANAGER",
                "Dan (Zero RB)",
                "zero_rb_dan",
                "https://api.dicebear.com/7.x/identicon/svg?seed=Team%206",
                "Puka on my bench in another league... 😭 Never benching him again.",
                None
            ),
            (
                l1_id,
                "AI_MANAGER",
                "Guru (Team 2)",
                "gridiron_guru",
                "https://api.dicebear.com/7.x/identicon/svg?seed=Team%202",
                "Looking to move an elite receiver for starting running back depth. Send offers!",
                None
            )
        ]
        for c in chat_entries:
            cur.execute(
                """
                INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                c
            )

        # 10. Pre-seed Pending AI Trade Proposal in League 1 (Team 3 proposing to User Team)
        offered_pids = [player_map["K. Walker"]]
        requested_pids = [player_map["D. Swift"]]
        cur.execute(
            """
            INSERT INTO napper_trades (
                league_id, week, proposer_team_id, target_team_id,
                offered_player_ids, requested_player_ids, status,
                initiated_by_ai, ai_rationale
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                l1_id, 6, team_3_id, user_team_id,
                offered_pids, requested_pids, "PENDING",
                True, "I have RB depth with Montgomery and Barkley and need another explosive playmaker. Walker for Swift straight up?"
            )
        )

        conn.commit()
        print("✅ Napper fantasy database successfully seeded!")


if __name__ == "__main__":
    seed_napper_database()
