"""Napper Fantasy Platform Database Seeder (engines/napper_seeder.py).

Populates:
1. User profile (@jrereyi2000 - 'The Straw Hat Pirates')
2. 3 Reference leagues populated strictly with authentic simulation players:
   - 'Solo 8-Team SF PPR' (8-team, Superflex, active season, week 6, Straw Hats vs Team 3)
   - 'The Justice League' (12-team PPR, in-season)
   - 'The Avengers' (8-team Dynasty PPR, status: PRE_DRAFT)
3. 14-week schedules and head-to-head matchups
4. Full starting & bench rosters from authentic simulation players
5. Pre-seeded league chat banter & bot award cards
6. Pending AI trade offers
"""

import os
import random
from typing import List, Dict, Any, Optional
from collections import defaultdict
import psycopg
from psycopg.rows import dict_row

from engines.napper_draft_engine import ensure_defenses_exist, ensure_kickers_exist

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def get_current_season_week(cur) -> tuple[int, int]:
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if not row or not row["current_season"]:
        raise RuntimeError("The active NFL season is not configured in league_state")
    return int(row["current_season"]), max(1, int(row["current_week"] or 1))


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


def ensure_kicker_projections(cur, season: int):
    """Ensures simulation kickers have baseline fantasy projections and ADP ranks."""
    ensure_kickers_exist(cur)


def fetch_id(cur) -> int:
    r = cur.fetchone()
    if isinstance(r, dict):
        return r["id"]
    return r[0]


def seed_napper_database(wipe_all: bool = False):
    print("🚀 Seeding Napper Fantasy Database with authentic simulation players...")
    conn = psycopg.connect(CONN_INFO, row_factory=dict_row)
    with conn.cursor() as cur:
        if wipe_all:
            cur.execute("DELETE FROM napper_chat;")
            cur.execute("DELETE FROM napper_trades;")
            cur.execute("DELETE FROM napper_matchups;")
            cur.execute("DELETE FROM napper_rosters;")
            cur.execute("DELETE FROM napper_teams;")
            cur.execute("DELETE FROM napper_draft_picks;")
            cur.execute("DELETE FROM napper_drafts;")
            cur.execute("DELETE FROM napper_leagues;")
            cur.execute("DELETE FROM napper_user_profiles;")
            cur.execute("ALTER SEQUENCE napper_matchups_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_rosters_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_teams_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_draft_picks_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_drafts_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_leagues_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_user_profiles_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_chat_id_seq RESTART WITH 1;")
            cur.execute("ALTER SEQUENCE napper_trades_id_seq RESTART WITH 1;")
        else:
            # Delete only existing seed leagues (cascades to their child teams, rosters, matchups, chat, trades, drafts)
            # Preserves all active user-created leagues (is_seed = FALSE)
            cur.execute("DELETE FROM napper_leagues WHERE is_seed = TRUE OR id IN (1, 2, 3);")
            cur.execute("SELECT setval('napper_matchups_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_matchups));")
            cur.execute("SELECT setval('napper_rosters_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_rosters));")
            cur.execute("SELECT setval('napper_teams_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_teams));")
            cur.execute("SELECT setval('napper_draft_picks_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_draft_picks));")
            cur.execute("SELECT setval('napper_drafts_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_drafts));")
            cur.execute("SELECT setval('napper_leagues_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_leagues));")
            cur.execute("SELECT setval('napper_user_profiles_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_user_profiles));")
            cur.execute("SELECT setval('napper_chat_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_chat));")
            cur.execute("SELECT setval('napper_trades_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_trades));")


        # 1. User Profile
        cur.execute(
            """
            INSERT INTO napper_user_profiles (id, handle, display_name, avatar_url, trophies)
            VALUES (1, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET handle = EXCLUDED.handle, display_name = EXCLUDED.display_name,
                avatar_url = EXCLUDED.avatar_url, trophies = EXCLUDED.trophies
            RETURNING id
            """,
            (
                "jrereyi2000",
                "The Straw Hat Pirates",
                "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&h=256&q=80",
                3
            )
        )
        user_id = fetch_id(cur)
        cur.execute("SELECT setval('napper_user_profiles_id_seq', (SELECT GREATEST(MAX(id), 1) FROM napper_user_profiles));")
        print(f"👤 Created user profile: @jrereyi2000 (ID: {user_id})")

        season, active_week = get_current_season_week(cur)

        # 2. Use the current season's generated team-defense and kicker records.
        ensure_defenses_exist(cur)
        ensure_kicker_projections(cur, season)

        # 3. Fetch authentic simulation player pools
        def get_pool(positions: tuple, limit: int = 50):
            cur.execute("""
                SELECT p.id, p.player_name, p.full_name, ps.position, ps.overall_rating,
                       fp.projected_points as proj
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s AND ps.team_id IS NOT NULL
                JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = %s
                WHERE ps.position = ANY(%s) AND fp.projected_points IS NOT NULL
                ORDER BY ps.overall_rating DESC, proj DESC
                LIMIT %s;
            """, (season, season, active_week, list(positions), limit))
            return [
                {
                    "id": r["id"],
                    "player_name": r["player_name"],
                    "full_name": r["full_name"],
                    "position": r["position"],
                    "overall_rating": r["overall_rating"],
                    "proj": float(r["proj"])
                }
                for r in cur.fetchall()
            ]

        pool = {
            "QB": get_pool(("QB",), 35),
            "HB": get_pool(("HB", "RB"), 50),
            "WR": get_pool(("WR",), 50),
            "TE": get_pool(("TE",), 30),
            "K": get_pool(("K",), 25),
            "DEF": get_pool(("DEF",), 25),
        }

        # Track used players across league rosters
        used_pids = set()

        def take_player(pos_list: List[str], preferred_id: Optional[int] = None) -> Optional[dict]:
            for pkey in pos_list:
                cand_list = pool.get(pkey, [])
                if preferred_id:
                    for cand in cand_list:
                        if cand["id"] == preferred_id and cand["id"] not in used_pids:
                            used_pids.add(cand["id"])
                            return cand
                for cand in cand_list:
                    if cand["id"] not in used_pids:
                        used_pids.add(cand["id"])
                        return cand
            return None

        # 4. Seed Leagues
        # League A: 'Solo 8-Team SF PPR'
        cur.execute(
            """
            INSERT INTO napper_leagues (id, name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week, is_seed)
            VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "Solo 8-Team SF PPR",
                season,
                8,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6
                }),
                "IN_SEASON",
                "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=256&h=256&q=80",
                6,
                True
            )
        )
        l1_id = fetch_id(cur)

        # League B: 'The Justice League'
        cur.execute(
            """
            INSERT INTO napper_leagues (id, name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week, is_seed)
            VALUES (2, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "The Justice League",
                season,
                12,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6
                }),
                "IN_SEASON",
                "https://images.unsplash.com/photo-1563089145-599997674d42?auto=format&fit=crop&w=256&h=256&q=80",
                6,
                True
            )
        )
        l2_id = fetch_id(cur)

        # League C: 'The Avengers'
        cur.execute(
            """
            INSERT INTO napper_leagues (id, name, season, teams_count, scoring_type, roster_format, status, avatar_url, current_week, is_seed)
            VALUES (3, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "The Avengers",
                season,
                8,
                "PPR",
                psycopg.types.json.Jsonb({
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 8
                }),
                "PRE_DRAFT",
                "https://images.unsplash.com/photo-1635863138275-d9b33299680b?auto=format&fit=crop&w=256&h=256&q=80",
                1,
                True
            )
        )
        l3_id = fetch_id(cur)
        cur.execute("SELECT setval('napper_leagues_id_seq', (SELECT GREATEST(MAX(id), 3) FROM napper_leagues));")
        print(f"🏆 Seeded 3 leagues: {l1_id} (Solo SF), {l2_id} (Justice League), {l3_id} (Avengers)")

        # 5. Teams for League 1: 'Solo 2026 8-Team SF PPR'
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
            l1_team_ids.append(fetch_id(cur))

        user_team_id = l1_team_ids[0]
        team_3_id = l1_team_ids[2]

        # 6. Assign Rosters for League 1 using Authentic Simulation Players
        # User Team Starters
        user_qb = take_player(["QB"])  # George Crawford or Caleb Williams
        user_rb1 = take_player(["HB"], preferred_id=310) or take_player(["HB"])  # Jeremiyah Love (HB)
        user_rb2 = take_player(["HB"])
        user_wr1 = take_player(["WR"])
        user_wr2 = take_player(["WR"])
        user_te = take_player(["TE"])
        user_flex = take_player(["WR", "HB", "TE"])
        user_superflex = take_player(["QB", "HB", "WR", "TE"])
        user_k = take_player(["K"])
        user_def = take_player(["DEF"])

        user_starters = [
            (user_qb["id"], "QB"),
            (user_rb1["id"], "RB"),
            (user_rb2["id"], "RB"),
            (user_wr1["id"], "WR"),
            (user_wr2["id"], "WR"),
            (user_te["id"], "TE"),
            (user_flex["id"], "FLEX"),
            (user_superflex["id"], "SUPERFLEX"),
            (user_k["id"], "K"),
            (user_def["id"], "DEF"),
        ]

        # User Team Bench (1 QB, 2 HBs, 2 WRs, 1 TE/K)
        user_bn_qb = take_player(["QB"])
        user_bn_hb1 = take_player(["HB"])
        user_bn_hb2 = take_player(["HB"])
        user_bn_wr1 = take_player(["WR"])
        user_bn_wr2 = take_player(["WR"])
        user_bn_k = take_player(["K"]) or take_player(["TE"])

        user_bench = [
            (user_bn_qb["id"], "BN"),
            (user_bn_hb1["id"], "BN"),
            (user_bn_hb2["id"], "BN"),
            (user_bn_wr1["id"], "BN"),
            (user_bn_wr2["id"], "BN"),
            (user_bn_k["id"], "BN"),
        ]

        # Team 3 Starters
        t3_qb = take_player(["QB"])
        t3_rb1 = take_player(["HB"])
        t3_rb2 = take_player(["HB"])
        t3_wr1 = take_player(["WR"])
        t3_wr2 = take_player(["WR"])
        t3_te = take_player(["TE"])
        t3_flex = take_player(["WR", "HB", "TE"])
        t3_superflex = take_player(["QB", "HB", "WR", "TE"])
        t3_k = take_player(["K"])
        t3_def = take_player(["DEF"])

        team_3_starters = [
            (t3_qb["id"], "QB"),
            (t3_rb1["id"], "RB"),
            (t3_rb2["id"], "RB"),
            (t3_wr1["id"], "WR"),
            (t3_wr2["id"], "WR"),
            (t3_te["id"], "TE"),
            (t3_flex["id"], "FLEX"),
            (t3_superflex["id"], "SUPERFLEX"),
            (t3_k["id"], "K"),
            (t3_def["id"], "DEF"),
        ]

        # Team 3 Bench
        t3_bn_qb = take_player(["QB"])
        t3_bn_hb1 = take_player(["HB"])
        t3_bn_hb2 = take_player(["HB"])
        t3_bn_wr1 = take_player(["WR"])
        t3_bn_wr2 = take_player(["WR"])
        t3_bn_te = take_player(["TE"])

        team_3_bench = [
            (t3_bn_qb["id"], "BN"),
            (t3_bn_hb1["id"], "BN"),
            (t3_bn_hb2["id"], "BN"),
            (t3_bn_wr1["id"], "BN"),
            (t3_bn_wr2["id"], "BN"),
            (t3_bn_te["id"], "BN"),
        ]

        # Assign rosters for all 14 weeks
        for wk in range(1, 15):
            for pid, slot in user_starters + user_bench:
                cur.execute(
                    """
                    INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = EXCLUDED.slot
                    """,
                    (l1_id, user_team_id, wk, pid, slot)
                )

            for pid, slot in team_3_starters + team_3_bench:
                cur.execute(
                    """
                    INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = EXCLUDED.slot
                    """,
                    (l1_id, team_3_id, wk, pid, slot)
                )

        # Assign rosters to remaining 6 teams in League 1
        other_teams_rosters = {}
        for tid in l1_team_ids:
            if tid in (user_team_id, team_3_id):
                continue
            tm_roster = [
                (take_player(["QB"])["id"], "QB"),
                (take_player(["HB"])["id"], "RB"),
                (take_player(["HB"])["id"], "RB"),
                (take_player(["WR"])["id"], "WR"),
                (take_player(["WR"])["id"], "WR"),
                (take_player(["TE"])["id"], "TE"),
                (take_player(["WR", "HB"])["id"], "FLEX"),
                (take_player(["QB", "WR", "HB"])["id"], "SUPERFLEX"),
                (take_player(["K"])["id"], "K"),
                (take_player(["DEF"])["id"], "DEF"),
                (take_player(["QB", "HB"])["id"], "BN"),
                (take_player(["HB"])["id"], "BN"),
                (take_player(["WR"])["id"], "BN"),
                (take_player(["WR"])["id"], "BN"),
                (take_player(["TE"])["id"], "BN"),
                (take_player(["K", "DEF"])["id"], "BN"),
            ]
            other_teams_rosters[tid] = tm_roster
            for wk in range(1, 15):
                for pid, slot in tm_roster:
                    cur.execute(
                        """
                        INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = EXCLUDED.slot
                        """,
                        (l1_id, tid, wk, pid, slot)
                    )

        # 7. Schedules & Matchups for League 1 (14 Weeks)
        # Note: in week 6, User Team (index 0) MUST play Team 3 (index 2)!
        sched_8 = build_schedule(8, 14)

        # Adjust week 6 pairing so team 0 plays team 2:
        w6_pairs = list(sched_8[5])
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

        # 8. Pre-seed League 2: 'The Justice League' (12 teams, in-season)
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
            l2_team_ids.append(fetch_id(cur))

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

        # 9. Pre-seed League 3: 'The Avengers' (8 teams, PRE_DRAFT)
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
            l3_team_ids.append(fetch_id(cur))

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

        # 10. Pre-seed League Chat for League 1
        bench_hb_name = user_bn_hb1["player_name"]
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
                f"🪑 RB Benchwarmer of the Week: {bench_hb_name} scored 18.2 FPTS on The Straw Hat Pirates bench!",
                psycopg.types.json.Jsonb({
                    "award_type": "BENCHWARMER",
                    "title": "RB Benchwarmer of the Week",
                    "player_name": bench_hb_name,
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
                f"{user_wr1['player_name']} on my bench in another league... 😭 Never benching him again.",
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

        # 11. Pre-seed Pending AI Trade Proposal in League 1 (Team 3 proposing to User Team)
        offered_pids = [t3_bn_hb1["id"]]
        requested_pids = [user_bn_hb1["id"]]
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
                True, f"I have RB depth and need another explosive playmaker. {t3_bn_hb1['player_name']} for {user_bn_hb1['player_name']} straight up?"
            )
        )

        conn.commit()
        print("✅ Napper fantasy database successfully seeded with 100% authentic simulation players!")


if __name__ == "__main__":
    seed_napper_database()
