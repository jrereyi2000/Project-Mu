import os
import re
import json
import psycopg
import functools
import random
import collections
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from db.db_handler import DatabaseHandler
from newsroom.slot_targets import load_league_snapshot, resolve_slot_targets
from newsroom.entity_patterns import team_mention_pattern
from newsroom.run_context import reset_article_context, set_article_context
from newsroom.local_portfolio import PRIMER as LOCAL_PRIMER, plan_local_portfolio
from newsroom.season_landscape import REGULAR_SEASON_FINAL_WEEK, division_standings, records_by_team
from newsroom import agent_memory
from newsroom.playoff_overrides import find_active_override, normalize_temporal_mode, validate_playoff_overrides
from newsroom.power_rankings import (
    fix_prompt as power_rankings_fix_prompt,
    load_team_index,
    parse_power_rankings,
    previous_rankings_directive,
    store_power_rankings,
)
from engines.gotw_engine import display_excitement
from engines.game_script_engine import get_scripts, script_summary
from newsroom.db_tools import (
    get_game_box_score,
    get_player_season_stats,
    get_player_context,
    get_team_context,
    get_team_season_schedule,
    get_league_leaderboards,
    get_advanced_team_analytics,
    get_advanced_game_analytics,
    get_opponent_adjusted_credit,
    get_coach_of_the_year_race,
    get_clutch_profile,
    get_coach_profile,
    get_player_scheme_splits,
    get_team_efficiency_profile,
    get_matchup_edges,
    get_weekly_slate,
    get_season_standings,
    get_top_popular_players,
    get_top_rated_players,
    get_defense_special_teams_leaders,
    get_all_team_popularities,
    get_draft_order,
    get_team_draft_needs,
    get_team_roster_depth_chart,
    get_draft_trade_value,
    get_franchise_rebuild_status,
    get_mock_draft,
    get_scouted_draft_board,
    get_team_articles,
    get_player_articles,
    get_team_injury_report,
)
from newsroom.fantasy_tools import FANTASY_TOOLS
from newsroom.fantasy_desk import (
    FANTASY_DESK_GUIDELINES,
    fantasy_phase,
    fantasy_slate,
    fantasy_temporal_instruction,
    fantasy_week_label,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

TEMPORAL_ARTICLE_MODES = {
    # Retrospective Recaps (focus on week - 1 completed slate)
    "MACRO_SLATE_RECAP": "RETROSPECTIVE",
    "SINGLE_GAME_SCHEMATIC_AUTOPSY": "RETROSPECTIVE",
    "LOCAL_POSTGAME_REPORT_CARD": "RETROSPECTIVE",
    "PLAYOFF_AUTOPSY": "RETROSPECTIVE",

    # Prospective Previews (focus on upcoming week scheduled matchups)
    "SLATE_LOOKAHEAD_PREDICTIONS": "PROSPECTIVE",
    "LOCAL_MATCHUP_PRIMER": "PROSPECTIVE",
    "SUPER_BOWL_COMPREHENSIVE_PREVIEW": "PROSPECTIVE",
    "SUPER_BOWL_ROUNDTABLE_CONSENSUS": "PROSPECTIVE",
    "QUARTERBACK_DUEL_TAPE_BREAKDOWN": "PROSPECTIVE",
    "SUPER_BOWL_MEDIA_INTERVIEW_AUDIT": "PROSPECTIVE",
    "X_FACTOR_VOLATILITY_SPOTLIGHT": "PROSPECTIVE",

    # Cumulative / Season-to-Date (focus on Weeks 1 through week - 1 cumulative)
    "POWER_RANKINGS": "CUMULATIVE",
    "AWARDS_TRACKER": "CUMULATIVE",
    "PLAYER_DEEP_PROFILE": "CUMULATIVE",
    "SEASON_PROGRESS_AUDIT": "CUMULATIVE",
    "FRANCHISE_STATE_OF_THE_UNION": "CUMULATIVE",
    "PLAYOFF_PREDICTOR": "CUMULATIVE",
    "SUPER_BOWL_CONTENDER_BLUEPRINT": "CUMULATIVE",
    "POSITIONAL_PLAYER_TIERS": "CUMULATIVE",
    "POSITIONAL_TOP_10_HIERARCHY": "CUMULATIVE",
    "PLAYER_PROGRESS_REPORT": "CUMULATIVE",
    "PRE_SEASON_ROSTER_TIERS": "CUMULATIVE",
    "NFL_TOP_100_HIERARCHY": "CUMULATIVE",
    "NATIONAL_OPINION_COLUMN": "CUMULATIVE",
    "OFFICIAL_AP_ALL_PRO_ROSTER": "CUMULATIVE",
    "MARKET_GRAVITY_LEGACY_AUDIT": "CUMULATIVE",
    "FRANCHISE_ARCHITECTURE_BLUEPRINT": "CUMULATIVE",
    "DRAFT_BLUEPRINT_ROSTER_AUDIT": "CUMULATIVE",
    "OFFSEASON_MOCK_DRAFT": "CUMULATIVE",
    "LOCAL_PLAYER_SPOTLIGHT": "CUMULATIVE",
    "LOCAL_SEASON_PROGRESS_REPORT": "CUMULATIVE",
}

def build_weekly_assignment_brief(db_conn_info: str, season: int, week: int) -> dict:
    """Builds a dual-horizon Weekly Slate Digest containing completed recaps (from week - 1),
    upcoming previews (for week), standout performances, and cumulative league context."""
    recap_week = max(1, week - 1) if week > 1 else 1

    brief = {
        "season": season,
        "calendar_week": week,
        "recap_week": recap_week if week > 1 else None,
        # Explicit recap slate (from the just-concluded week)
        "recap_slate": {
            "week": recap_week if week > 1 else week,
            "status": "COMPLETED",
            "total_games": 0,
            "all_games": []
        },
        # Explicit preview slate (for the upcoming week)
        "preview_slate": {
            "week": week,
            "status": "SCHEDULED",
            "total_games": 0,
            "all_games": []
        },
        "standout_performances": [],
        # League as it stood entering this week: regular-season division standings plus culture leaders
        "season_landscape": {
            "standings_through_week": min(week - 1, REGULAR_SEASON_FINAL_WEEK),
            "standings_note": (
                "Regular-season division standings from completed games before this week, best record first "
                "(PF/PA = points for/against, diff = point differential). Playoff games are not included."
            ),
            "division_standings": {},
            "most_watchable_teams": [],
            "most_popular_teams": []
        },
        "excitement_scale_note": (
            "Excitement Index values are on a 70–100 scale: about 70 is a dull game, about 80 is typical, "
            "90+ is a classic. projected_excitement is a pre-game projection; actual games often land well "
            "above or below it."
        ),
        "curatorial_guidance": (
            "CURATORIAL AUTONOMY: If your instructions include an EDITORIAL DESK SUBJECT ASSIGNMENT, that "
            "team or game is your subject and is not negotiable; the freedom below applies only to the angles, "
            "players and storylines you choose within it. Otherwise, you have complete editorial independence "
            "to choose which matchups, teams, or players from this slate to highlight. Do NOT feel obligated to "
            "write about the same games as other journalists. Select games and storylines that align with your "
            "distinct journalistic beat and persona. You have access to autonomous database tools to investigate "
            "box scores, player stats, season standings, and team analytics for any game or franchise."
        )
    }

    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            # 1. Fetch completed games for the RECAP slate (recap_week)
            cur.execute("""
                SELECT 
                    g.id,
                    at.name AS away_team, g.away_score,
                    ht.name AS home_team, g.home_score,
                    ABS(COALESCE(g.home_score, 0) - COALESCE(g.away_score, 0)) AS margin,
                    COALESCE(g.excitement_index, 0) as excitement_index,
                    g.home_team_id, g.away_team_id
                FROM games g
                JOIN teams ht ON g.home_team_id = ht.id
                JOIN teams at ON g.away_team_id = at.id
                WHERE g.season = %s AND g.week = %s AND g.status = 'COMPLETED'
                ORDER BY g.excitement_index DESC NULLS LAST;
            """, (season, recap_week))
            recap_games = cur.fetchall()

            for g in recap_games:
                game_dict = {
                    "game_id": g[0],
                    "matchup": f"{g[1]} {g[2]} @ {g[3]} {g[4]}",
                    "away_team": f"{g[1]} (ID: {g[8]})",
                    "home_team": f"{g[3]} (ID: {g[7]})",
                    "away_score": g[2],
                    "home_score": g[4],
                    "margin": g[5],
                    "excitement_index": display_excitement(g[6]) if g[6] else None
                }
                brief["recap_slate"]["all_games"].append(game_dict)

            brief["recap_slate"]["total_games"] = len(recap_games)
            if recap_games and week > 1:
                # Each game's canonical story (§2.14); the full script is in get_game_box_score
                scripts = get_scripts(db_conn_info, [g[0] for g in recap_games])
                for game_dict in brief["recap_slate"]["all_games"]:
                    if game_dict["game_id"] in scripts:
                        game_dict["game_story"] = script_summary(scripts[game_dict["game_id"]], moments=2)

            # 2. Fetch upcoming scheduled games for the PREVIEW slate (week)
            cur.execute("""
                SELECT 
                    g.id,
                    at.name AS away_team,
                    ht.name AS home_team,
                    COALESCE(g.game_time, 'TBD') as kickoff_time,
                    g.projected_excitement,
                    g.home_team_id, g.away_team_id
                FROM games g
                JOIN teams ht ON g.home_team_id = ht.id
                JOIN teams at ON g.away_team_id = at.id
                WHERE g.season = %s AND g.week = %s AND g.status IN ('SCHEDULED', 'BYE')
                ORDER BY g.projected_excitement DESC NULLS LAST;
            """, (season, week))
            preview_games = cur.fetchall()

            # Query key injuries for upcoming matchup previews (threshold: OVR >= 88 OR Pop >= 50 OR starting QB)
            cur.execute("""
                SELECT DISTINCT week FROM injuries
                WHERE season = %s AND week <= %s
                ORDER BY week DESC LIMIT 1;
            """, (season, week))
            w_row = cur.fetchone()
            q_week = w_row[0] if w_row else week

            cur.execute("""
                WITH ranked_roster AS (
                    SELECT 
                        player_id,
                        position,
                        overall_rating,
                        ROW_NUMBER() OVER (PARTITION BY team_id, position ORDER BY overall_rating DESC) as pos_rank
                    FROM player_seasons
                    WHERE season = %s
                )
                SELECT 
                    i.team_id,
                    COALESCE(p.full_name, p.player_name, i.player_name) as player_name,
                    COALESCE(ps.position, i.position) as position,
                    COALESCE(ps.overall_rating, i.ovr) as ovr,
                    COALESCE(ps.popularity_index, 0.0) as pop,
                    i.injury_type,
                    i.duration,
                    i.weeks_remaining,
                    i.is_ir,
                    (rr.pos_rank = 1 AND COALESCE(ps.position, i.position) = 'QB') as is_qb_star
                FROM injuries i
                JOIN teams t ON t.id = i.team_id
                LEFT JOIN players p ON p.id = i.player_id
                LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
                LEFT JOIN ranked_roster rr ON rr.player_id = i.player_id
                WHERE i.season = %s AND i.week = %s
                  AND (
                      COALESCE(ps.overall_rating, i.ovr) >= 88
                      OR COALESCE(ps.popularity_index, 0.0) >= 50.0
                      OR (rr.pos_rank = 1 AND COALESCE(ps.position, i.position) = 'QB')
                  )
                ORDER BY COALESCE(ps.overall_rating, i.ovr) DESC;
            """, (season, season, q_week))
            key_injuries_by_team = defaultdict(list)
            for tid, pname, pos, ovr, pop, inj_type, dur, w_rem, is_ir, is_qb_star in cur.fetchall():
                key_injuries_by_team[tid].append({
                    "player_name": pname,
                    "position": pos,
                    "overall_rating": int(ovr or 0),
                    "popularity_index": round(float(pop or 0.0), 1),
                    "injury_type": inj_type,
                    "duration": dur,
                    "weeks_remaining": int(w_rem or 0),
                    "is_ir": bool(is_ir),
                    "is_starting_qb": bool(is_qb_star),
                })

            for pg in preview_games:
                home_tid, away_tid = pg[5], pg[6]
                game_dict = {
                    "game_id": pg[0],
                    "matchup": f"{pg[1]} @ {pg[2]}",
                    "away_team": f"{pg[1]} (ID: {away_tid})",
                    "home_team": f"{pg[2]} (ID: {home_tid})",
                    "kickoff_time": pg[3],
                    "projected_excitement": display_excitement(pg[4])
                }
                away_injs = key_injuries_by_team.get(away_tid, [])
                home_injs = key_injuries_by_team.get(home_tid, [])
                if away_injs or home_injs:
                    game_dict["key_injuries"] = {
                        "away": away_injs,
                        "home": home_injs
                    }
                brief["preview_slate"]["all_games"].append(game_dict)
            brief["preview_slate"]["total_games"] = len(preview_games)

            # 3. Fetch standout player stat lines from the completed recap week
            cur.execute("""
                SELECT 
                    ps.player_name, ps.position, t.name AS team_name,
                    ps.pass_yards, ps.pass_tds, ps.rush_yards, ps.rush_tds, 
                    ps.rec_yards, ps.rec_tds, ps.team_id, p.id AS player_id
                FROM player_stats ps
                JOIN games g ON ps.game_id = g.id
                JOIN teams t ON ps.team_id = t.id
                LEFT JOIN players p ON ps.player_name = p.player_name
                WHERE g.season = %s AND g.week = %s
                ORDER BY (
                    COALESCE(ps.pass_yards, 0) * 0.04 + COALESCE(ps.pass_tds, 0) * 4 +
                    COALESCE(ps.rush_yards, 0) * 0.1 + COALESCE(ps.rush_tds, 0) * 6 +
                    COALESCE(ps.rec_yards, 0) * 0.1 + COALESCE(ps.rec_tds, 0) * 6
                ) DESC NULLS LAST
                LIMIT 5;
            """, (season, recap_week))
            standouts = cur.fetchall()

            for s in standouts:
                line_parts = []
                if s[3] and s[3] > 0: line_parts.append(f"{s[3]} pass yds, {s[4]} pass TD")
                if s[5] and s[5] > 0: line_parts.append(f"{s[5]} rush yds, {s[6]} rush TD")
                if s[7] and s[7] > 0: line_parts.append(f"{s[7]} rec yds, {s[8]} rec TD")
                perf_item = {
                    "player": f"{s[0]} ({s[1]}, {s[2]})",
                    "player_id": s[10],
                    "team_id": s[9],
                    "week_earned": recap_week,
                    "stats": ", ".join(line_parts)
                }
                brief["standout_performances"].append(perf_item)

            # 4. Season landscape: division standings entering this week (newsroom/season_landscape.py)
            cur.execute("SELECT id, name, city, conference, division FROM teams;")
            teams = [dict(zip(("id", "name", "city", "conference", "division"), r)) for r in cur.fetchall()]
            cur.execute("""
                SELECT week, home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE season = %s AND week < %s AND status = 'COMPLETED'
                  AND home_score IS NOT NULL AND away_score IS NOT NULL;
            """, (season, week))
            games = [dict(zip(("week", "home_team_id", "away_team_id", "home_score", "away_score"), r))
                     for r in cur.fetchall()]
            standings = division_standings(teams, games, before_week=week)
            landscape = brief["season_landscape"]
            landscape["division_standings"] = standings
            records = records_by_team(standings)

            # Top watchable and popular teams (records from the standings above)
            cur.execute("""
                SELECT t.name, ts.team_id, ts.watchability_index
                FROM teams t JOIN team_seasons ts ON t.id = ts.team_id
                WHERE ts.season = %s ORDER BY ts.watchability_index DESC NULLS LAST LIMIT 5;
            """, (season,))
            landscape["most_watchable_teams"] = [
                f"{r[0]} ({records.get(r[1], '0-0')}, Watchability: {round(float(r[2] or 0), 1)})" for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT t.name, ts.team_id, ts.popularity_index
                FROM teams t JOIN team_seasons ts ON t.id = ts.team_id
                WHERE ts.season = %s ORDER BY ts.popularity_index DESC NULLS LAST LIMIT 5;
            """, (season,))
            landscape["most_popular_teams"] = [
                f"{r[0]} ({records.get(r[1], '0-0')}, Popularity: {round(float(r[2] or 0), 1)})" for r in cur.fetchall()
            ]

    return brief

# ==============================================================================
# NFL EDITORIAL MATRIX CONFIGURATION (18-Week Regular Season & 5-Week Postseason)
# ==============================================================================

REGULAR_SEASON_FEATURE_POOLS = {
    1: {  # Phase 1: Early Identity & Overreactions (Weeks 1–5)
        "pool": [
            "PLAYER_DEEP_PROFILE",
            "PLAYER_PROGRESS_REPORT",
            "FRANCHISE_STATE_OF_THE_UNION",
            "SEASON_PROGRESS_AUDIT",
            "ROTATING_COLUMNIST",
        ],
        "columnists": ["SIMMONS", "KIMES", "ACHO", "SKIP_BAYLESS"],
    },
    2: {  # Phase 2: Midseason Separation & Roster Diagnostics (Weeks 6–10)
        "pool": [
            "POSITIONAL_PLAYER_TIERS",
            "POSITIONAL_TOP_10_HIERARCHY",
            "AWARDS_TRACKER",
            "SEASON_PROGRESS_AUDIT",
            "PLAYER_PROGRESS_REPORT",
            "FRANCHISE_STATE_OF_THE_UNION",
            "ROTATING_COLUMNIST",
        ],
        "columnists": ["YATES", "KIMES", "ACHO", "SIMMONS", "SKIP_BAYLESS"],
    },
    3: {  # Phase 3: The Stretch Run & Postseason Pathing (Weeks 11–14)
        "pool": [
            "PLAYOFF_PREDICTOR",
            "AWARDS_TRACKER",
            "POSITIONAL_PLAYER_TIERS",
            "POSITIONAL_TOP_10_HIERARCHY",
            "SEASON_PROGRESS_AUDIT",
            "PLAYER_PROGRESS_REPORT",
            "ROTATING_COLUMNIST",
        ],
        "columnists": ["SKIP_BAYLESS", "KIMES", "YATES", "ACHO"],
    },
    4: {  # Phase 4: Elimination & Championship Windows (Weeks 15–18)
        "pool": [
            "SUPER_BOWL_CONTENDER_BLUEPRINT",
            "PLAYOFF_PREDICTOR",
            "AWARDS_TRACKER",
            "FRANCHISE_STATE_OF_THE_UNION",
            "SEASON_PROGRESS_AUDIT",
            "ROTATING_COLUMNIST",
        ],
        "columnists": ["SIMMONS", "SKIP_BAYLESS", "YATES", "KIMES"],
    },
}

POSTSEASON_EDITORIAL_SLATES = {
    19: [  # Week 19: Wild Card Eve
        ("Hero Slot", "MACRO_SLATE_RECAP"),
        ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS"),
        ("Slot 2", "SINGLE_GAME_SCHEMATIC_AUTOPSY"),
        ("Slot 3", "PLAYOFF_PREDICTOR"),
        ("Slot 4", "OFFICIAL_AP_ALL_PRO_ROSTER"),
        ("Slot 5", "NATIONAL_OPINION_COLUMN"),
        ("Slot 6", "FRANCHISE_STATE_OF_THE_UNION"),
        ("Slot 7", "FRANCHISE_STATE_OF_THE_UNION"),
        ("Slot 8", "LOCAL_MATCHUP_PRIMER"),
        ("Standalone", "POWER_RANKINGS"),
    ],
    20: [  # Week 20: Divisional Eve
        ("Hero Slot", "MACRO_SLATE_RECAP"),
        ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS"),
        ("Slot 2", "SINGLE_GAME_SCHEMATIC_AUTOPSY"),
        ("Slot 3", "SUPER_BOWL_CONTENDER_BLUEPRINT"),
        ("Slot 4", "OFFSEASON_MOCK_DRAFT"),
        ("Slot 5", "NATIONAL_OPINION_COLUMN"),
        ("Slot 6", "PLAYOFF_AUTOPSY"),
        ("Slot 7", "PLAYOFF_AUTOPSY"),
        ("Slot 8", "LOCAL_MATCHUP_PRIMER"),
        ("Standalone", "POWER_RANKINGS"),
    ],
    21: [  # Week 21: Championship Eve
        ("Hero Slot", "MACRO_SLATE_RECAP"),
        ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS"),
        ("Slot 2", "SINGLE_GAME_SCHEMATIC_AUTOPSY"),
        ("Slot 3", "SUPER_BOWL_CONTENDER_BLUEPRINT"),
        ("Slot 4", "AWARDS_TRACKER"),
        ("Slot 5", "DRAFT_BLUEPRINT_ROSTER_AUDIT"),
        ("Slot 6", "PLAYOFF_AUTOPSY"),
        ("Slot 7", "PLAYOFF_AUTOPSY"),
        ("Slot 8", "LOCAL_MATCHUP_PRIMER"),
        ("Standalone", "POWER_RANKINGS"),
    ],
    22: [  # Week 22: Super Bowl Bye
        ("Hero Slot", "MACRO_SLATE_RECAP"),
        ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS"),
        ("Slot 2", "SINGLE_GAME_SCHEMATIC_AUTOPSY"),
        ("Slot 3", "AWARDS_TRACKER"),
        ("Slot 4", "FRANCHISE_ARCHITECTURE_BLUEPRINT"),
        ("Slot 5", "SEASON_PROGRESS_AUDIT"),
        ("Slot 6", "PLAYOFF_AUTOPSY"),
        ("Slot 7", "PLAYOFF_AUTOPSY"),
        ("Slot 8", "PLAYER_DEEP_PROFILE"),
        ("Standalone", "POWER_RANKINGS"),
    ],
    23: [  # Week 23: Super Bowl Week
        ("Hero Slot", "SUPER_BOWL_COMPREHENSIVE_PREVIEW"),
        ("Slot 1", "SINGLE_GAME_SCHEMATIC_AUTOPSY"),
        ("Slot 2", "QUARTERBACK_DUEL_TAPE_BREAKDOWN"),
        ("Slot 3", "SUPER_BOWL_ROUNDTABLE_CONSENSUS"),
        ("Slot 4", "SUPER_BOWL_MEDIA_INTERVIEW_AUDIT"),
        ("Slot 5", "X_FACTOR_VOLATILITY_SPOTLIGHT"),
        ("Slot 6", "LOCAL_MATCHUP_PRIMER"),
        ("Slot 7", "LOCAL_MATCHUP_PRIMER"),
        ("Slot 8", "MARKET_GRAVITY_LEGACY_AUDIT"),
        ("Standalone", "POWER_RANKINGS"),
    ],
}

# Agents never drawn as random "pundits" for Agent Wire posts (newsroom/social_desk.PUNDIT_EXCLUDED mirrors this list): long-form-only writers (their personas are
# article voices; SKIP_BAYLESS's tweets come from SKIP_TWEET, so Skip never tweets twice), the roundtable panel,
# beat reporters, and fans (fans tweet through their own selection rules).
TWEET_EXCLUDED_ARCHETYPES = (
    "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "KIMES", "ORLOVSKY",
    "SKIP_BAYLESS", "ROUNDTABLE_PANEL",
    "LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN",
)

# Old or shorthand writer names -> the archetype used in agent_personas.json, curatorial directives and agent_memories.
WRITER_ARCHETYPE_ALIASES = {
    "BAYLESS": "SKIP_BAYLESS",
}


def canonical_writer_archetype(archetype: str) -> str:
    arch = (archetype or "").upper().strip()
    return WRITER_ARCHETYPE_ALIASES.get(arch, arch)


class EntityTaggingService:
    """Extracts and resolves mentioned teams and players from generated articles, storing them in article_teams and article_players."""

    def __init__(self, db_conn_info: str):
        self.db_conn_info = db_conn_info
        self._team_cache = None
        self._player_cache = {}

    def _load_team_cache(self, cur):
        if self._team_cache is not None:
            return self._team_cache

        cur.execute("SELECT id, name, city, abbreviation FROM teams;")
        teams = []
        for tid, name, city, abbr in cur.fetchall():
            # Case-sensitive, and word-like abbreviations (NO, WAS, TEN...) are skipped: see newsroom/entity_patterns.py
            combined_pat = team_mention_pattern(name, city, abbr)
            teams.append({
                "id": tid,
                "name": name,
                "city": city,
                "abbr": abbr,
                "pattern": combined_pat
            })

        self._team_cache = teams
        return self._team_cache

    def _load_player_cache(self, cur, season: int = None):
        cache_key = season or 0
        if cache_key in self._player_cache:
            return self._player_cache[cache_key]

        if season:
            cur.execute("""
                SELECT p.id, p.full_name, ps.team_id, ps.overall_rating
                FROM players p
                JOIN player_seasons ps ON p.id = ps.player_id
                WHERE ps.season = %s AND p.full_name IS NOT NULL AND length(trim(p.full_name)) > 2
                ORDER BY ps.overall_rating DESC, p.id ASC;
            """, (season,))
        else:
            cur.execute("""
                SELECT p.id, p.full_name, ps.team_id, ps.overall_rating
                FROM players p
                LEFT JOIN player_seasons ps ON p.id = ps.player_id
                WHERE p.full_name IS NOT NULL AND length(trim(p.full_name)) > 2
                ORDER BY ps.overall_rating DESC NULLS LAST, p.id ASC;
            """)

        raw_players = cur.fetchall()
        # Group by normalized lowercase full_name to deduplicate and pick highest overall rating / lowest id
        grouped = {}
        for pid, full_name, team_id, ovr in raw_players:
            clean_name = full_name.strip()
            norm = clean_name.lower()
            ovr_val = ovr or 0
            if norm not in grouped:
                grouped[norm] = {
                    "id": pid,
                    "full_name": clean_name,
                    "team_id": team_id,
                    "ovr": ovr_val,
                    "pattern": re.compile(r"\b" + re.escape(clean_name) + r"\b", re.IGNORECASE)
                }

        players_list = list(grouped.values())
        self._player_cache[cache_key] = players_list
        return players_list

    def extract_and_store_entities(
        self,
        cur,
        article_id: int,
        headline: str,
        content: str,
        season: int = None,
        assigned_team_id: int = None
    ) -> dict:
        """Extracts referenced teams and players from the headline and content, classifying and storing them."""
        if not article_id:
            return {"teams": [], "players": []}

        headline_text = headline or ""
        body_text = content or ""
        full_text = f"{headline_text}\n{body_text}"

        # Extract top-level Markdown headers for primary focus detection
        header_lines = [line.strip() for line in full_text.splitlines() if line.strip().startswith("#")]
        header_text = f"{headline_text}\n" + "\n".join(header_lines)

        # 1. Resolve Teams
        teams = self._load_team_cache(cur)
        team_tags = {}
        for t in teams:
            tid = t["id"]
            body_matches = t["pattern"].findall(full_text)
            count = len(body_matches)
            header_hit = bool(t["pattern"].search(header_text))

            if count > 0 or tid == assigned_team_id:
                if tid == assigned_team_id or header_hit or count >= 5:
                    mtype = "PRIMARY"
                elif count >= 3:
                    mtype = "FEATURED"
                else:
                    mtype = "MENTIONED"
                team_tags[tid] = {
                    "team_id": tid,
                    "mention_type": mtype,
                    "mention_count": max(1, count)
                }

        # 2. Resolve Players
        players = self._load_player_cache(cur, season)
        player_tags = {}
        for p in players:
            matches = p["pattern"].findall(full_text)
            count = len(matches)
            if count > 0:
                pid = p["id"]
                header_hit = bool(p["pattern"].search(header_text))
                if header_hit or count >= 5:
                    mtype = "PRIMARY"
                elif count >= 3:
                    mtype = "FEATURED"
                else:
                    mtype = "MENTIONED"
                player_tags[pid] = {
                    "player_id": pid,
                    "team_id": p["team_id"],
                    "mention_type": mtype,
                    "mention_count": count
                }

        # 3. Insert into article_teams
        for tag in team_tags.values():
            cur.execute("""
                INSERT INTO article_teams (article_id, team_id, mention_type, mention_count, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (article_id, team_id) DO UPDATE SET
                    mention_type = EXCLUDED.mention_type,
                    mention_count = EXCLUDED.mention_count;
            """, (article_id, tag["team_id"], tag["mention_type"], tag["mention_count"]))

        # 4. Insert into article_players
        for tag in player_tags.values():
            cur.execute("""
                INSERT INTO article_players (article_id, player_id, team_id, mention_type, mention_count, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (article_id, player_id) DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    mention_type = EXCLUDED.mention_type,
                    mention_count = EXCLUDED.mention_count;
            """, (article_id, tag["player_id"], tag["team_id"], tag["mention_type"], tag["mention_count"]))

        return {
            "teams": list(team_tags.values()),
            "players": list(player_tags.values())
        }

class SwarmDispatcher:
    def __init__(self, api_key: str = None, debug_mode: bool = True):
        self.db = DatabaseHandler()
        self.debug_mode = debug_mode
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            # Check for local or user .env files if any
            for env_path in [
                os.path.join(PROJECT_ROOT, ".env"),
                os.path.expanduser("~/Downloads/.env"),
                os.path.expanduser("~/Documents/.env"),
            ]:
                if os.path.exists(env_path):
                    try:
                        with open(env_path, "r") as ef:
                            for line in ef:
                                line = line.strip()
                                if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                                    self.api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                                    if self.api_key:
                                        break
                    except Exception:
                        pass
                if self.api_key:
                    break

        # Load archetype prompts
        personas_path = os.path.join(CONFIG_DIR, "agent_personas.json")
        with open(personas_path, "r") as f:
            personas_json = json.load(f)
            self.personas_data = personas_json.get("archetypes", {})
            # Long-form voices for writers whose archetype persona is written for tweets (articles only)
            self.article_personas = {k: v for k, v in personas_json.get("article_personas", {}).items()
                                     if not k.startswith("_")}

        # Load article structure directives
        directives_path = os.path.join(CONFIG_DIR, "article_structure_directives.json")
        if os.path.exists(directives_path):
            with open(directives_path, "r") as f:
                self.article_structure_directives = json.load(f)
        else:
            self.article_structure_directives = {}

        # Fantasy desk voices (FANTASY_* articles only; see newsroom/config/fantasy_writers.json)
        fantasy_writers_path = os.path.join(CONFIG_DIR, "fantasy_writers.json")
        if os.path.exists(fantasy_writers_path):
            with open(fantasy_writers_path, "r") as f:
                self.fantasy_writers = json.load(f).get("writers", {})
        else:
            self.fantasy_writers = {}

        # Load curatorial autonomy directives
        curatorial_path = os.path.join(CONFIG_DIR, "curatorial_autonomy_directives.json")
        if os.path.exists(curatorial_path):
            with open(curatorial_path, "r") as f:
                self.curatorial_autonomy_directives = json.load(f)
        else:
            self.curatorial_autonomy_directives = {}

        # Load article types taxonomy
        article_types_path = os.path.join(CONFIG_DIR, "article_types.json")
        if os.path.exists(article_types_path):
            with open(article_types_path, "r") as f:
                self.article_types_data = json.load(f).get("article_types", {})
        else:
            self.article_types_data = {}

        # Load playoff specific overrides
        playoff_overrides_path = os.path.join(CONFIG_DIR, "playoff_overrides.json")
        if os.path.exists(playoff_overrides_path):
            with open(playoff_overrides_path, "r") as f:
                self.playoff_overrides = json.load(f).get("playoff_overrides", {})
        else:
            self.playoff_overrides = {}
        for problem in validate_playoff_overrides(self.playoff_overrides, self.article_types_data.keys()):
            print(f"⚠️ playoff_overrides.json: {problem}")

        self.tools = [
            get_game_box_score,
            get_player_season_stats,
            get_player_context,
            get_team_context,
            get_team_season_schedule,
            get_league_leaderboards,
            get_advanced_team_analytics,
            get_advanced_game_analytics,
            get_opponent_adjusted_credit,
            get_coach_of_the_year_race,
            get_clutch_profile,
            get_coach_profile,
            get_player_scheme_splits,
            get_team_efficiency_profile,
            get_matchup_edges,
            get_weekly_slate,
            get_season_standings,
            get_top_popular_players,
            get_top_rated_players,
            get_defense_special_teams_leaders,
            get_all_team_popularities,
            get_draft_order,
            get_team_draft_needs,
            get_team_roster_depth_chart,
            get_draft_trade_value,
            get_franchise_rebuild_status,
            get_mock_draft,
            get_scouted_draft_board,
            get_team_articles,
            get_player_articles,
            get_team_injury_report,
        ]

        self.entity_tagger = EntityTaggingService(self.db.db_conn_info)

    def tools_for(self, article_type: str = None) -> list:
        """Research tools for an article: the base set, plus the fantasy tools (newsroom/fantasy_tools.py) for
        FANTASY_* article types only, so other prompts don't carry their descriptions."""
        if article_type and str(article_type).upper().startswith("FANTASY_"):
            return self.tools + FANTASY_TOOLS
        return self.tools

    def get_article_type_for_writer(self, archetype: str, requested_type: str = None) -> dict:
        """Finds the article type configuration assigned to a writer archetype or requested type."""
        if requested_type:
            req_upper = requested_type.upper().strip()
            if req_upper in self.article_types_data:
                return {"id": req_upper, **self.article_types_data[req_upper]}
            normalized = req_upper.replace("-", "_").replace(" ", "_")
            if normalized in self.article_types_data:
                return {"id": normalized, **self.article_types_data[normalized]}
            for at_id, at_info in self.article_types_data.items():
                if req_upper == at_id.replace("_", ""):
                    return {"id": at_id, **at_info}
                if req_upper in at_info.get("name", "").upper() or req_upper in at_info.get("writing_archetype_category", "").upper():
                    return {"id": at_id, **at_info}

        arch_upper = archetype.upper().strip()
        if arch_upper == "LOCAL_BEAT_REPORTER":
            # For beat reporters without a specified type, default to postgame report card
            default_id = "LOCAL_POSTGAME_REPORT_CARD"
            if default_id in self.article_types_data:
                return {"id": default_id, **self.article_types_data[default_id]}

        for at_id, at_info in self.article_types_data.items():
            if arch_upper in [w.upper() for w in at_info.get("allowed_writers", [])]:
                return {"id": at_id, **at_info}
            if arch_upper == at_info.get("writing_archetype_category", "").upper():
                return {"id": at_id, **at_info}
        return None

    def compile_agent_prompt(
        self,
        agent_id: str,
        archetype: str,
        persistent_memory: str,
        handle: str,
        display_name: str,
        assigned_team_id: int,
        brief: dict,
        season: int,
        week: int,
        content_type: str,
        article_type: str = None,
        slot_target: dict = None,
        desk_note: str = None,
    ) -> dict:
        """Assembles the exact persona, memory, assignment brief, and tools prompt for an agent.

        slot_target (from newsroom.slot_targets) pins the article's subject chosen by the Editorial Desk.
        desk_note is a plain-text assignment from the desk (the fantasy desk uses it, e.g. a draft-tier position)."""
        archetype = canonical_writer_archetype(archetype)
        is_fantasy = bool(content_type == "ARTICLE" and article_type and str(article_type).upper().startswith("FANTASY_"))
        fantasy_voice = self.fantasy_writers.get(archetype, {}) if is_fantasy else {}
        article_persona = self.article_personas.get(archetype) if content_type == "ARTICLE" else None
        base_archetype_prompt = fantasy_voice.get("persona") or article_persona or self.personas_data.get(
            archetype,
            f"You are an NFL media agent with archetype {archetype}."
        )

        if not display_name and persistent_memory and persistent_memory.startswith("I am "):
            try:
                extracted_name = persistent_memory.split("I am ", 1)[1].split(",", 1)[0].strip()
                if extracted_name:
                    display_name = extracted_name
                    if not handle:
                        handle = f"@{extracted_name.replace(' ', '')}"
            except Exception:
                pass

        tool_descriptions = "\n".join([f"- `{t.name}`: {t.description}" for t in self.tools_for(article_type)])

        article_type_info = None
        article_type_directive = ""
        format_display = content_type
        active_playoff_override = None
        if content_type == "ARTICLE":
            article_type_info = self.get_article_type_for_writer(archetype, requested_type=article_type)
            if article_type_info:
                # Check for playoff-specific overrides based on target_weeks
                active_playoff_override = find_active_override(self.playoff_overrides, article_type_info.get("id"), week)

                format_display = f"ARTICLE - {article_type_info['name']} (Writing Category: {article_type_info['writing_archetype_category']})"
                if active_playoff_override:
                    format_display += f" [PLAYOFF OVERRIDE: {active_playoff_override.get('override_name')}]"

                chosen_directive = (
                    active_playoff_override.get("directive_override")
                    if active_playoff_override and active_playoff_override.get("directive_override")
                    else article_type_info["directive"]
                )
                article_type_directive = f"""
{chosen_directive}
Example Prompt Hook: {article_type_info.get('example_prompt_hook', '')}
"""

        # Evolving memory (newsroom/agent_memory.py): season summary, recent takes, takes about this subject, and
        # graded predictions, all from before this publication week. Missing tables -> no block.
        memory_block = ""
        try:
            with psycopg.connect(self.db.db_conn_info) as mconn, mconn.cursor() as mcur:
                subject_team = slot_target["team_id"] if slot_target else assigned_team_id
                block = agent_memory.build_memory_block(mcur, agent_id, season, week, subject_team)
                memory_block = f"\n{block}\n" if block else ""
        except Exception as e:
            print(f"⚠️  [MEMORY UNAVAILABLE] {agent_id}: {e}")

        system_instruction = f"""ROLE & PERSONA:
{base_archetype_prompt}

IDENTITY & HANDLE:
- Name: {display_name or agent_id}
- Handle: {handle or '@NFL_Insider'}
- Content Format: {format_display} (Write in character matching this format!)

PERSISTENT WORLDVIEW & MEMORIES:
{persistent_memory or 'No specific grudges or previous memories recorded.'}
{memory_block}
AVAILABLE DATABASE REAC/TOOLS:
You have autonomous access to query the simulation database using these functions:
{tool_descriptions}

WEEK CONTEXT:
Calendar Week: Season {season} | Week {week}
Assigned Team ID: {assigned_team_id if assigned_team_id is not None else 'National Coverage'}
"""

        # Determine Temporal Mode (RETROSPECTIVE, PROSPECTIVE, or CUMULATIVE)
        at_id = article_type_info.get("id") if article_type_info else None
        temporal_mode = normalize_temporal_mode(active_playoff_override.get("temporal_mode")) if active_playoff_override else None
        if not temporal_mode and at_id:
            temporal_mode = TEMPORAL_ARTICLE_MODES.get(at_id, "CUMULATIVE")

        recap_week = brief.get("recap_week") or (max(1, week - 1) if week > 1 else 1)

        temporal_instruction = ""
        preview_week = (active_playoff_override or {}).get("preview_week") or week
        if temporal_mode == "RETROSPECTIVE":
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (RETROSPECTIVE SLATE REVIEW):
- You are publishing during Week {week} looking back at the completed Week {recap_week} slate that just concluded.
- Focus strictly on the games, outcomes, and standout tape from Week {recap_week}.
- Do NOT treat Week {week} matchups as completed; Week {week} has not kicked off yet.
- Use your database tools to investigate the completed Week {recap_week} box scores, margins, and player stats.
"""
        elif temporal_mode == "PROSPECTIVE":
            # An override can preview a later week (Week 22, the Super Bowl bye, previews the Week 23 game).
            preview_lookup = (
                f"\n- Week {preview_week} matchups are not in the assignment brief's preview_slate; call `get_weekly_slate` with week={preview_week} to get them."
                if preview_week != week else ""
            )
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (PROSPECTIVE MATCHUP PREVIEW):
- You are publishing during Week {week}, ahead of the Week {preview_week} games.
- Focus strictly on previewing the upcoming scheduled matchups for Week {preview_week}; they have not been played yet.
- Analyze team records, head-to-head match-ups, and tactical blueprints for the games taking place in Week {preview_week}.
- INJURY REPORTING RULE (THRESHOLD 88 OVR / 50 POP / STARTING QB): You may cite official injuries from your injury tool or the Assignment Brief, but ONLY for marquee players (OVR 88+, Popularity 50+, or starting Quarterbacks). Do not discuss minor injuries to role players or backups. Never invent injuries, timelines, or diagnoses not present in the official data.{preview_lookup}
"""
        elif temporal_mode == "CUMULATIVE":
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (CUMULATIVE / ENTERING WEEK {week}):
- You are publishing ahead of Week {week}, evaluating cumulative performance across Weeks 1 through {recap_week}.
- Focus on overall season standings, trajectory, award races, or roster construction entering Week {week}.
- Week {week} games have not taken place yet.
"""

        if is_fantasy:
            temporal_mode = "FANTASY"
            temporal_instruction = fantasy_temporal_instruction(week)

        national_writers_list = ("BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "YATES", "SIMMONS", "ACHO", "THE_ATHLETIC", "ROUNDTABLE_PANEL", "SKIP_BAYLESS", "KIMES", "ORLOVSKY", "CLARK")

        national_writer_guidelines = ""
        if content_type == "ARTICLE" and archetype in national_writers_list:
            national_writer_guidelines = """
CRITICAL WRITER RESEARCH TOOLS & OPTIONAL HISTORICAL COMPARISONS:
1. DEEP INVESTIGATIVE RESEARCH: Conduct thorough investigations into your subjects. Query division standings, team advanced analytics, season schedules, game box scores, and player context for all contenders, pretenders, or matchup subjects you evaluate. Call whatever tools you need to build an ironclad case with granular facts and box-score details. Once you have gathered sufficient evidence across your subjects, synthesize your findings into the full article.

2. RESEARCH TOOLS: First, analyze the provided JSON ASSIGNMENT BRIEF. You have autonomous database tools to dive into any game, team, box score, season schedule, standings, or player across the league.
- If you need a team's complete season schedule and game log (scores, margins, one-score game outcomes, game IDs), call `get_team_season_schedule`.
- If you need full division standings and point differentials, call `get_season_standings`.
- If you need schedule-adjusted team strength (Opponent-Adjusted Game Credit, strength of schedule, expected wins, and which records are lucky or unlucky), call `get_opponent_adjusted_credit` (no team = all 32 ranked; with a team = game-by-game credit log). It also gives each team's Trajectory (Surging to Sliding over the last 4 games).
- If you need the Coach of the Year race (head coaches ranked by COTY score with record, wins above expectation, roster outperformance and improvement over last season), call `get_coach_of_the_year_race`.
- If you need who wins and loses late (fourth-quarter comebacks, game-winning drives, blown leads, records when trailing at halftime) for a team or a quarterback, or the league leaders in those, call `get_clutch_profile`.
- If you need a team's head coach (tenure, who he replaced, offensive scheme and defensive front, tendencies, this season's and career records, Super Bowls and Coach of the Year awards), call `get_coach_profile`.
- If you need how a player produces against different kinds of defenses, call `get_player_scheme_splits`. `get_player_scheme_splits` shows how a QB, back or receiver produces against Strong / Average / Weak scoring defenses, pass rushes and coverages (this season and last, real game lines only). Cite a split with its game count as a tendency ("in five games against top-11 pass rushes"); if its `verdict` says there aren't enough real games, don't cite splits for that player.
- If you need Pythagorean wins (what points for/against say the record should be), team pace, per-game passing/rushing yards and giveaways, or situational football (third- and fourth-down conversion rates, red-zone TD rate on offense and allowed on defense, penalties per game, each with a league rank), call `get_team_efficiency_profile`.
- If you need where one team's offense has the edge over another's defense (passing vs. coverage, rushing vs. defensive rating, ball security, offensive line vs. pass rush), call `get_matchup_edges` (no opponent = their next game).
- If you need all 32 team popularity rankings and watchability indices, call `get_all_team_popularities`.
- If you need the top popular players in the NFL, call `get_top_popular_players`.
- If you need the highest-rated players at every position (for league-wide player rankings), call `get_top_rated_players`.
- If you need defensive or special-teams production (sack, interception, tackle, tackles-for-loss, pass-deflection, forced-fumble, field-goal, punting or return leaders), league-wide or for one team, call `get_defense_special_teams_leaders`. `get_player_season_stats` gives any one player's full line, and `get_game_box_score` lists a game's defensive playmakers and kickers when that game's stats were captured.
- If you need a team's official weekly injury report (player status, injury type, duration, IR status, and whether an injury is a key injury), call `get_team_injury_report`. Remember national coverage is strictly restricted to marquee key injuries (OVR 88+, Popularity 50+, or starting QBs).
- If you need a game's detailed player contributions, call `get_game_box_score`.
- If you need the Round 1 selection order (Picks 1-32, with which picks are locked and which are still provisional), call `get_draft_order`.
- If you need the scouted draft prospect board (projected pick, position, college, height, weight, age, scouted OVR; filter by position), call `get_scouted_draft_board`. Never invent a prospect or fill in a field it leaves null.
- If you need this week's Round 1 mock draft (the same picks every writer sees this week), call `get_mock_draft`.
- If you need a team's primary roster needs ranked by urgency, call `get_team_draft_needs`.
- If you need a team's complete positional depth chart with age brackets, call `get_team_roster_depth_chart`.
- If you need draft pick trade points (Jimmy Johnson model) and trade package scenarios, call `get_draft_trade_value`.
- If you need a franchise's rebuild lifecycle stage and draft posture, call `get_franchise_rebuild_status`.

3. DEFENSIVE DATA: Defense and special teams now have real season numbers: tackles, sacks, tackles for loss, interceptions, pass deflections, forced fumbles and recoveries, defensive TDs, kicking, punting and returns, plus sacks allowed for linemen and sacks taken for quarterbacks. Use them to back defensive claims with named players. The limits: Madden lists only its leaders in each category, so a player with no line has unknown numbers (not zeros); listed defenders don't add up to a team total, so never sum them into team sacks or takeaways; and pressures, blitzes, coverage, snap counts and targets aren't recorded, so never give them as numbers or rates. Describing them in words is fine when it fits the box score and the game script: a pass rush that lived in the backfield, a corner who blanketed his man, a defense that brought more heat after halftime. COACHING TENDENCIES: `get_coach_profile` and the `coaching_matchup` block of `get_matchup_edges` give each head coach's schemes and three tendencies (pass lean, fourth-down aggressiveness, man coverage) as Low / Medium / High against the league. Pass lean and fourth-down aggressiveness are measured from this league's results (allowing for the score), so you can state them as what the team does ("one of the league's most aggressive fourth-down teams"). Man coverage is the staff's real-world philosophy, not measured here: present it as reputation ("a staff known for zone"), never as what the defense has done this season. There is no blitz data, so never put a number on how often a defense blitzes or call it a season-long habit. Never turn a tier into a percentage, and never put a count or rate on how often a team blitzed or played man in a specific game; describing in words what a defense did in one game is fine when it fits the game script. Team situational numbers work the same way: season third-down, red-zone and penalty rates come from `get_team_efficiency_profile`, and a single game's come only from its box score's `team_situational` line. If a game has no such line, don't invent its third-down or red-zone totals. Third-down defense has no season total, so never cite a season third-down rate allowed beyond what the tool shows, and down-and-distance splits (third-and-short, third-and-long) aren't recorded. Season numbers (player season stats, the defense and special-teams leaders, team situational rates and the standings) cover the regular season only; playoff numbers come only from playoff box scores (`get_game_box_score`) and the `postseason_stats` line of `get_player_season_stats`. Keep them separate: never add playoff stats into a season total or call a regular-season total a season-long figure that includes the playoffs.

4. HISTORICAL COMPARISONS (OPTIONAL): You may compare a team or player to a real NFL team or season from 1990–2023 when the parallel genuinely sharpens your argument (similar record shape, scoring profile, roster age, or trajectory). It is not required: most articles need none, and you should use at most one. Never invent a historical team or season, and don't reach for the most famous examples by default; pick the comparison that actually fits the numbers.
"""
        if is_fantasy:
            national_writer_guidelines = FANTASY_DESK_GUIDELINES
        elif content_type == "ARTICLE" and archetype == "LOCAL_BEAT_REPORTER":
            national_writer_guidelines = """
CRITICAL BEAT WRITER RESEARCH & LOCAL REPORTING DIRECTIVE:
1. TEAM-SPECIFIC INVESTIGATION: Your focus is strictly your assigned franchise. Do NOT review unrelated games across the national schedule.
2. RESEARCH TOOLS:
- Query your team's most recent box score and player performances with `get_game_box_score`.
- Query your team's complete game schedule, results, and margins with `get_team_season_schedule`.
- Query individual roster stats, age and experience with `get_player_season_stats` and `get_player_context`.
- BEAT INJURY REPORTING RULE: Check your team's injury status in the Assignment Brief or query `get_team_injury_report`. Report on any injured starters across offense, defense, and specialists. Detail who steps into the starting lineup ('next man up') and how the coordinator must adjust the gameplan. Never invent injuries not on the report.
- Query team baseline ratings and top stars with `get_team_context`.
- Query how your players (and the opponent's) produce against different kinds of defenses with `get_player_scheme_splits`. `get_player_scheme_splits` shows how a QB, back or receiver produces against Strong / Average / Weak scoring defenses, pass rushes and coverages (this season and last, real game lines only). Cite a split with its game count as a tendency ("in five games against top-11 pass rushes"); if its `verdict` says there aren't enough real games, don't cite splits for that player.
- Query your head coach (tenure, schemes, tendencies, records, hardware) with `get_coach_profile`, and the opponent's coach before a game. COACHING TENDENCIES: `get_coach_profile` and the `coaching_matchup` block of `get_matchup_edges` give each head coach's schemes and three tendencies (pass lean, fourth-down aggressiveness, man coverage) as Low / Medium / High against the league. Pass lean and fourth-down aggressiveness are measured from this league's results (allowing for the score), so you can state them as what the team does ("one of the league's most aggressive fourth-down teams"). Man coverage is the staff's real-world philosophy, not measured here: present it as reputation ("a staff known for zone"), never as what the defense has done this season. There is no blitz data, so never put a number on how often a defense blitzes or call it a season-long habit. Never turn a tier into a percentage, and never put a count or rate on how often a team blitzed or played man in a specific game; describing in words what a defense did in one game is fine when it fits the game script.
- Query your team's and quarterback's late-game record (fourth-quarter comebacks, game-winning drives, blown leads, record when trailing at halftime) with `get_clutch_profile`.
- Query your team's and the opponent's third-down, red-zone and penalty rates with league ranks with `get_team_efficiency_profile`; a single game's come only from its box score's `team_situational` line, so don't invent them for a game without one.
- Season numbers (player season stats, the defense and special-teams leaders, team situational rates and the standings) cover the regular season only; playoff numbers come only from playoff box scores (`get_game_box_score`) and the `postseason_stats` line of `get_player_season_stats`. Keep them separate: never add playoff stats into a season total or call a regular-season total a season-long figure that includes the playoffs.
- Query your team's and the opponent's defensive and special-teams leaders (sacks, interceptions, tackles for loss, forced fumbles, kicking, returns) with `get_defense_special_teams_leaders` and the team name. Madden lists only its leaders, so a player with no line has unknown numbers, not zeros; don't add them up into team totals. Pressures, snap counts, targets and blitz or coverage rates aren't recorded, so never give them as numbers; in words, consistent with the game script, they're fine.
3. AUTHENTIC BEAT REPORTING: Deliver your reporting with the immediacy, nuance, and accountability of an everyday beat writer inside the locker room. Quote coaching staff sentiment, cite specific situational downs (3rd down conversions, red zone execution, line protection), and speak directly to the local fan base without academic or algebraic math.
"""


        if active_playoff_override and active_playoff_override.get("curatorial_autonomy_override"):
            article_curatorial = active_playoff_override["curatorial_autonomy_override"].strip()
        else:
            article_curatorial = (article_type_info.get("curatorial_autonomy") if article_type_info else "").strip()
        writer_curatorial = (fantasy_voice.get("selection") if is_fantasy
                             else self.curatorial_autonomy_directives.get(archetype, "")).strip()

        if article_curatorial and writer_curatorial:
            curatorial_directive = f"""
EDITORIAL SELECTION & CURATORIAL ASSIGNMENT:
{article_curatorial}

YOUR SPECIFIC WRITER SELECTION FILTER ({display_name or archetype}):
{writer_curatorial}
"""
        elif article_curatorial:
            curatorial_directive = article_curatorial
        else:
            curatorial_directive = writer_curatorial

        if active_playoff_override and active_playoff_override.get("structure_directive_override"):
            override_title = active_playoff_override.get("override_name", article_type_info["name"] if article_type_info else "Article")
            structure_directive = f"""
ARTICLE STRUCTURE & FORMATTING DIRECTIVE ({override_title}):
{active_playoff_override['structure_directive_override']}
"""
        elif article_type_info and article_type_info.get("structure_directive"):
            structure_directive = f"""
ARTICLE STRUCTURE & FORMATTING DIRECTIVE ({article_type_info['name']}):
{article_type_info['structure_directive']}
"""
        else:
            writer_struct = self.article_structure_directives.get(archetype, "")
            format_label = article_type_info['name'] if article_type_info else "Column"
            structure_directive = f"""
ARTICLE STRUCTURE & SIGNATURE COLUMN FORMAT ({display_name or archetype} on {format_label}):
{writer_struct}
"""

        natural_language_rule = """
PROHIBITED INTERNAL METRIC SYMBOLS & NATURAL LANGUAGE TRANSLATION DIRECTIVE:
CRITICAL: You must NEVER output internal database variable names, algebraic symbols, or mathematical shorthand in your published text. Never write terms like "$P$", "(P)", "Y_prod", "$Y_\\text{prod}$", "P_draft", "$R_\\text{rating}$", "(88.17 W)", "(64.84 POP)", "$U$", "$T$", or "$S_\\text{all-time}$".
Readers must experience this as professional, authentic sports journalism. You must translate all internal mathematical concepts into fluid, natural football language:
- Instead of "Offensive Pace (P)" or "$P$" -> write "offensive tempo", "play volume", "per-possession scoring pace", or "offensive rhythm".
- Instead of "Under-25 Production (Y_prod)" or "$Y_\\text{prod}$" -> write "production from the under-25 core", "young offensive nucleus", or "youth production share".
- Instead of "Blue-Chip Pedigree (P_draft)" -> write "first-round draft pedigree", "blue-chip draft capital", or "draft equity".
- Instead of "Watchability (W)" or "(88.17 W)" -> write "must-see television rating", "entertaining brand of football", or "electric watchability".
- Instead of "Popularity (POP)" or "(64.84 POP)" -> write "national cultural footprint", "massive national following", or "media gravity".
- Instead of "Opponent-Adjusted Game Credit" or "+4.7 credit" -> write "schedule-adjusted point margin", "plays like a +5 team against an average opponent", or "résumé once you account for who they played".
- Instead of "Trajectory Multiplier (T)" or "1.11" -> write "surging", "trending up over the last month", or "fading down the stretch".
- Instead of "Pythagorean wins" or "pyth luck" -> write "their point differential says they should be closer to 10-6", "winning more close games than the margins justify".
- Instead of "Upset Leverage (U)" -> write "narrative-shifting upset", "high-leverage shocker".
- Instead of "Baseline Rating (R_rating)" -> write "baseline roster talent", "roster foundation".
- Instead of "Legacy Floor (S_all-time)" -> write "historical franchise prestige", "championship pedigree".
- Instead of "SpikeIndex" -> write "explosive ceiling", "boom-or-bust volatility", "single-game scoring explosion".
Strict Rule: No raw algebraic symbols, formula notation, or LaTeX math equations ($...$) may appear in your final text.

GAME SCRIPTS ARE CANON (WHAT YOU MAY AND MAY NOT INVENT):
Every completed game has a game script: the `game_script` at the end of `get_game_box_score`, summarized as `game_story` in the brief's recap slate. Its quarter scores, scoring plays (who scored, how, from how far, on what drive, at what point on the clock), lead changes, the score that won it, key moments, adjustments and defensive standouts are the official account every writer shares. When you describe how a game unfolded, follow it and never contradict it. Beyond it, invent the texture no data records: the snaps between the scores, play calls, pressure and coverage, a block, a drop, a sideline scene, the crowd, quotes. Keep it consistent with the script and the box score. Never invent anything the data records (scores, stats, records, standings, injuries), and a defender with no recorded line for a game gets praise in words, not numbers.

NO MADDEN RATINGS IN PUBLISHED TEXT:
Player and team ratings (OVR, overall, team/offense/defense overall, depth-chart tiers built from them) are for your own judgment of talent only. Never cite a rating number or refer to ratings in your text: no "92 OVR", "a 92 overall", "rated 85", "the league's highest-rated QB", "a 78-overall starter", "team OVR", and never mention Madden, the video game, or game ratings. This league is covered as real football. Show talent through production, role, age and results instead, e.g. "an All-Pro-caliber left tackle", "the best cover corner in the conference", "a starter-quality backup", "a roster built on elite trench play".
"""

        article_header_directive = ""
        if content_type == "ARTICLE":
            article_header_directive = """
ARTICLE TITLE & EXCERPT PUBLISHING DIRECTIVE (STRICT FORMAT):
You MUST begin your article text on the very first lines with an explicit Markdown title and a 1-to-2 sentence editorial teaser excerpt formatted exactly as follows:

# [Your Creative, Engaging Headline]
> **EXCERPT:** [A sharp, 1-to-2 sentence summary (under 250 characters) capturing the core thesis, revelation, or hook of the piece for newsroom cards.]

Immediately follow the excerpt with the rest of your article sections and analysis.
"""

        slot_target_directive = ""
        if slot_target:
            subject = f"{slot_target['team_name']} (Team ID: {slot_target['team_id']})"
            if slot_target.get("game_id"):
                subject += f" vs. {slot_target['opponent_name']} (Team ID: {slot_target['opponent_team_id']}, Game ID: {slot_target['game_id']})"
            slot_target_directive = f"""
EDITORIAL DESK SUBJECT ASSIGNMENT (MANDATORY — SUPERSEDES ANY SUBJECT-SELECTION INSTRUCTIONS ABOVE):
- Subject: {subject}
- Assignment: {slot_target['note']}
- The Editorial Desk has already chosen this subject. Do not substitute a different team or game; any instruction to select your own team, game, or franchise applies only to choosing angles within this subject.
"""

        if desk_note:
            slot_target_directive += f"""
EDITORIAL DESK ASSIGNMENT (MANDATORY):
- {desk_note}
"""

        power_rankings_directive = ""
        if at_id == "POWER_RANKINGS":
            try:
                with psycopg.connect(self.db.db_conn_info) as conn:
                    with conn.cursor() as cur:
                        power_rankings_directive = previous_rankings_directive(cur, season, week)
            except Exception as e:
                print(f"⚠️  Could not load previous Power Rankings: {e}")

        brief_for_agent = brief
        if archetype == "LOCAL_BEAT_REPORTER" and assigned_team_id:
            try:
                with psycopg.connect(self.db.db_conn_info) as bconn, bconn.cursor() as bcur:
                    bcur.execute("""
                        SELECT DISTINCT week FROM injuries
                        WHERE team_id = %s AND season = %s AND week <= %s
                        ORDER BY week DESC LIMIT 1;
                    """, (assigned_team_id, season, week))
                    bw_row = bcur.fetchone()
                    bq_week = bw_row[0] if bw_row else week

                    bcur.execute("""
                        WITH ranked_roster AS (
                            SELECT 
                                player_id,
                                position,
                                overall_rating,
                                ROW_NUMBER() OVER (PARTITION BY position ORDER BY overall_rating DESC) as pos_rank
                            FROM player_seasons
                            WHERE team_id = %s AND season = %s
                        )
                        SELECT 
                            i.player_id,
                            COALESCE(p.full_name, p.player_name, i.player_name) as player_name,
                            COALESCE(ps.position, i.position) as position,
                            COALESCE(ps.overall_rating, i.ovr) as ovr,
                            COALESCE(ps.popularity_index, 0.0) as pop,
                            i.injury_type,
                            i.duration,
                            i.weeks_remaining,
                            i.is_ir,
                            rr.pos_rank
                        FROM injuries i
                        LEFT JOIN players p ON p.id = i.player_id
                        LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
                        LEFT JOIN ranked_roster rr ON rr.player_id = i.player_id
                        WHERE i.team_id = %s AND i.season = %s AND i.week = %s
                        ORDER BY COALESCE(ps.overall_rating, i.ovr) DESC;
                    """, (assigned_team_id, season, assigned_team_id, season, bq_week))
                    beat_rows = bcur.fetchall()

                    def is_starter_rank(pos, rank):
                        if not rank:
                            return False
                        if pos in ("QB", "TE", "LT", "LG", "C", "RG", "RT", "FS", "SS", "K", "P", "FB"):
                            return rank == 1
                        if pos in ("HB", "DT", "LEDG", "REDG", "LE", "RE"):
                            return rank <= 2
                        if pos in ("WR", "CB", "MIKE", "SAM", "WILL", "MLB", "LOLB", "ROLB"):
                            return rank <= 3
                        return rank == 1

                    starter_injuries = []
                    for pid, pname, pos, ovr, pop, inj_type, dur, w_rem, is_ir, pos_rank in beat_rows:
                        if is_starter_rank(pos, pos_rank):
                            starter_injuries.append({
                                "player_name": pname,
                                "position": pos,
                                "overall_rating": int(ovr or 0),
                                "injury_type": inj_type,
                                "duration": dur,
                                "weeks_remaining": int(w_rem or 0),
                                "is_ir": bool(is_ir),
                                "depth_starter": True
                            })
                    brief_for_agent = dict(brief)
                    brief_for_agent["team_injury_report"] = {
                        "team_id": assigned_team_id,
                        "week": bq_week,
                        "starter_injuries_count": len(starter_injuries),
                        "starter_injuries": starter_injuries,
                        "reporting_guidance": "Report on any injured starters across offense, defense, and specialists. Detail who steps into the starting lineup ('next man up') and how the gameplan must adjust."
                    }
            except Exception as e:
                print(f"⚠️  Could not load beat injury report for team {assigned_team_id}: {e}")

        task_instruction = f"""ASSIGNMENT BRIEF:
{json.dumps(brief_for_agent, indent=2)}

TASK:
You must analyze the action through your specific analytical lens.
If you need specific stats, use your database tools before producing your final text.
{"Write a long-form, analytical article with markdown headings and deep analysis." if content_type == 'ARTICLE' else "Write a short, punchy, viral tweet (under 280 characters) with appropriate tone, slang, hashtags, or emojis."}
{article_header_directive}
{temporal_instruction}
{article_type_directive}
{curatorial_directive}
{slot_target_directive}
{power_rankings_directive}
{national_writer_guidelines}
{structure_directive}
{natural_language_rule}"""

        return {
            "agent_id": agent_id,
            "display_name": display_name or agent_id,
            "handle": handle or "@NFL",
            "archetype": archetype,
            "content_type": content_type,
            "article_type": article_type_info["id"] if article_type_info else None,
            "article_type_name": article_type_info["name"] if article_type_info else None,
            "playoff_override": active_playoff_override.get("override_name") if active_playoff_override else None,
            "temporal_mode": temporal_mode,
            "recap_week": recap_week,
            "preview_week": preview_week,
            "system_instruction": system_instruction,
            "task_instruction": task_instruction,
        }

    def _execute_agent(
        self,
        compiled_prompt: dict,
        season: int,
        week: int,
        team_id: int,
        live: bool = False,
        model: str = "gemini-3.8-flash",
        save_to_db: bool = False,
        slot: str = None,
        article_type: str = None
    ):
        """Prints prompt in debug mode or executes LLM, saving to news_feed only if save_to_db=True."""
        display = compiled_prompt["display_name"]
        handle = compiled_prompt["handle"]
        ctype = compiled_prompt["content_type"]
        at_id = article_type or compiled_prompt.get("article_type")
        at_name = compiled_prompt.get("article_type_name")
        override_name = compiled_prompt.get("playoff_override")
        assigned_slot = slot or compiled_prompt.get("slot")
        if at_name:
            if override_name:
                format_badge = f"{ctype} [{at_name} | {override_name}]"
            else:
                format_badge = f"{ctype} [{at_name}]"
        else:
            format_badge = ctype

        if self.debug_mode:
            print("\n" + "=" * 70)
            print(f"🤖 [DEBUG PROMPT] {display} ({handle}) | Format: {format_badge} | Slot: {assigned_slot}")
            print("-" * 70)
            print(">>> SYSTEM INSTRUCTION & PERSONA PROMPT:")
            print(compiled_prompt["system_instruction"].strip())
            print("\n>>> ASSIGNMENT & USER PROMPT:")
            print(compiled_prompt["task_instruction"].strip())
            print("=" * 70)

        # Set when a live call produced nothing publishable; such output is never saved to news_feed.
        failure_reason = None

        # Writer tools (newsroom/db_tools.py) read this article's season and hide results from its publication
        # week onward. Gemini runs the tools on this thread, so the context covers every tool call below.
        run_ctx_token = set_article_context(season, week)

        # Call Gemini if live mode is active
        if live:
            if not self.api_key:
                print("\n⚠️  [GEMINI LIVE CALL SKIPPED]: GEMINI_API_KEY environment variable is not set.")
                print("   Set it via: export GEMINI_API_KEY=\"your_key\" or pass --api-key YOUR_KEY")
                failure_reason = "missing GEMINI_API_KEY"
                generated_text = f"[{ctype}] Gemini execution skipped: Missing GEMINI_API_KEY."
            else:
                print(f"\n🚀 Sending prompt to Gemini ({model}) with Autonomous Database Tools...")
                usage = None
                try:
                    from google import genai
                    from google.genai import types
                    from db.llm_usage import UsageMeter

                    client = genai.Client(api_key=self.api_key)
                    usage = UsageMeter(client)          # every call, the tool loop's included -> llm_usage

                    # Prepare raw functions with logging wrappers for Gemini AFC (preserving parameter signatures)
                    tool_call_counts = collections.defaultdict(int)
                    tools_for_gemini = []
                    for t in self.tools_for(at_id):  # same tool set the prompt listed
                        fn = getattr(t, "func", t)
                        def make_wrapper(inner_fn):
                            @functools.wraps(inner_fn)
                            def tool_wrapper(*args, **kwargs):
                                tool_call_counts[inner_fn.__name__] += 1
                                call_idx = sum(tool_call_counts.values())
                                call_repr = f"{inner_fn.__name__}({', '.join([str(a) for a in args] + [f'{k}={v}' for k, v in kwargs.items()])})"
                                print(f"\n⚙️  [DATABASE TOOL #{call_idx} INVOKED] {call_repr}")
                                try:
                                    res = inner_fn(*args, **kwargs)
                                    preview = str(res)[:160] + "..." if len(str(res)) > 160 else str(res)
                                    print(f"   -> Result preview: {preview}")
                                    return str(res)
                                except Exception as ex:
                                    print(f"   -> Tool Error: {ex}")
                                    return f"Tool Error: {ex}"
                            return tool_wrapper
                        tools_for_gemini.append(make_wrapper(fn))

                    chat = client.chats.create(
                        model=model,
                        config=types.GenerateContentConfig(
                            system_instruction=compiled_prompt["system_instruction"],
                            tools=tools_for_gemini,
                            temperature=0.7,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                maximum_remote_calls=50
                            ),
                        )
                    )

                    response = chat.send_message(compiled_prompt["task_instruction"])
                    generated_text = response.text

                    if not generated_text and hasattr(response, "candidates") and response.candidates:
                        parts = response.candidates[0].content.parts
                        text_chunks = [p.text for p in parts if hasattr(p, "text") and p.text]
                        if text_chunks:
                            generated_text = "\n".join(text_chunks)

                    # Fallback if AFC cycle completed without final synthesized markdown text
                    if not generated_text:
                        print("\n⚠️ [AFC TURN COMPLETED WITHOUT FINAL TEXT] Prompting model to synthesize research into final output...")
                        followup = chat.send_message(
                            "Now, using the database research and data you gathered, write your complete final article following your assigned article structure and formatting directives. Do not execute any further tool calls."
                        )
                        generated_text = followup.text
                        if not generated_text and hasattr(followup, "candidates") and followup.candidates:
                            parts = followup.candidates[0].content.parts
                            text_chunks = [p.text for p in parts if hasattr(p, "text") and p.text]
                            if text_chunks:
                                generated_text = "\n".join(text_chunks)

                    # Power Rankings must name all 32 teams exactly once to be stored; give the writer one rewrite
                    if at_id == "POWER_RANKINGS" and generated_text:
                        _, pr_errors = parse_power_rankings(generated_text, self._power_rankings_team_index())
                        if pr_errors:
                            print("\n⚠️ [POWER RANKINGS FAILED VALIDATION] Requesting one rewrite:\n   " + "\n   ".join(pr_errors))
                            retry = chat.send_message(power_rankings_fix_prompt(pr_errors))
                            retry_text = retry.text
                            if not retry_text and hasattr(retry, "candidates") and retry.candidates:
                                parts = retry.candidates[0].content.parts
                                retry_text = "\n".join(p.text for p in parts if hasattr(p, "text") and p.text)
                            if retry_text:
                                generated_text = retry_text

                    if not (generated_text or "").strip():
                        failure_reason = "model returned no text"
                        generated_text = ""

                    total_tool_calls = sum(tool_call_counts.values())
                    print("\n📊 --- RESEARCH TOOL USAGE SUMMARY ---")
                    print(f"Total Database Tool Calls: {total_tool_calls} (Cap: 50)")
                    if tool_call_counts:
                        for tool_name, count in sorted(tool_call_counts.items(), key=lambda x: x[1], reverse=True):
                            print(f"   • {tool_name}: {count} call{'s' if count != 1 else ''}")
                    else:
                        print("   • No database tools invoked (used Assignment Brief context directly).")
                    print("---------------------------------------\n")

                    print("\n" + "=" * 70)
                    print(f"📰 GENERATED {format_badge} BY {display} ({handle}):")
                    print("=" * 70)
                    print(generated_text)
                    print("=" * 70 + "\n")
                except Exception as e:
                    print(f"\n❌ Error calling Gemini: {e}")
                    import traceback
                    traceback.print_exc()
                    failure_reason = f"Gemini error: {e}"
                    generated_text = f"Error calling Gemini: {e}"
                finally:
                    if usage is not None:
                        usage.record("newsroom", label=f"{handle} {at_id or ctype}", model=model)
        else:
            t_mode = compiled_prompt.get("temporal_mode")
            r_week = compiled_prompt.get("recap_week") or (max(1, week - 1) if week > 1 else 1)
            if t_mode == "RETROSPECTIVE":
                generated_text = f"[{ctype}] Mock analysis by {display} ({handle}) reviewing Week {r_week} action."
            else:
                generated_text = f"[{ctype}] Mock analysis by {display} ({handle}) analyzing Week {week} action."

        reset_article_context(run_ctx_token)

        excerpt = None
        if ctype == "ARTICLE":
            t_mode = compiled_prompt.get("temporal_mode")
            r_week = compiled_prompt.get("recap_week") or (max(1, week - 1) if week > 1 else 1)
            
            # Default fallback headline
            if t_mode == "RETROSPECTIVE" and at_name:
                default_headline = f"{at_name}: Week {r_week} Recap by {display}"
            elif t_mode == "PROSPECTIVE" and at_name:
                default_headline = f"{at_name}: Week {week} Preview by {display}"
            elif at_name:
                default_headline = f"{at_name}: Entering Week {week} by {display}"
            else:
                default_headline = f"Week {week} Breakdown by {display}"

            # 1. Parse creative title from markdown `# [Title]`
            h_match = re.search(r"^#\s+(.+)$", generated_text, re.MULTILINE)
            if h_match:
                extracted_headline = h_match.group(1).strip()
                headline = extracted_headline if len(extracted_headline) > 3 else default_headline
            else:
                headline = default_headline

            # 2. Parse teaser excerpt from `> **EXCERPT:** [Text]`
            e_match = re.search(r"^>\s*(?:\*\*)?EXCERPT:(?:\*\*)?\s*(.+)$", generated_text, re.MULTILINE | re.IGNORECASE)
            if e_match:
                excerpt = e_match.group(1).strip()
            else:
                # Fallback: first non-header, non-blockquote body paragraph
                clean_lines = [
                    line.strip() for line in generated_text.splitlines() 
                    if line.strip() and not line.strip().startswith("#") and not line.strip().startswith(">")
                ]
                if clean_lines:
                    first_p = re.sub(r"[*_`]", "", clean_lines[0]).strip()
                    excerpt = first_p[:280] + ("..." if len(first_p) > 280 else "")
                else:
                    excerpt = f"Analysis by {display} ({handle}) covering {at_name or 'NFL action'}."
        else:
            headline = None
            excerpt = None

        # Save output to news_feed table ONLY if explicitly enabled, and only real model output:
        # error text, empty responses and offline mock text are never published.
        article_id = None
        if save_to_db and not live:
            print(f"⏭️  [NOT SAVED] {display} [{assigned_slot}]: mock output (live=False) is never written to news_feed.")
        elif save_to_db and failure_reason:
            print(f"⏭️  [NOT SAVED] {display} [{assigned_slot}]: {failure_reason}.")
        elif save_to_db:
            try:
                with psycopg.connect(self.db.db_conn_info) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO news_feed (season, week, team_id, content_type, persona, article_type, slot, headline, excerpt, content, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            RETURNING id;
                        """, (
                            season,
                            week,
                            team_id,
                            ctype,
                            compiled_prompt["archetype"],
                            at_id,
                            assigned_slot,
                            headline,
                            excerpt,
                            generated_text
                        ))
                        row = cur.fetchone()
                        article_id = row[0] if row else None

                        if article_id and ctype == "ARTICLE":
                            tag_res = self.entity_tagger.extract_and_store_entities(
                                cur=cur,
                                article_id=article_id,
                                headline=headline,
                                content=generated_text,
                                season=season,
                                assigned_team_id=team_id
                            )
                            t_cnt = len(tag_res.get("teams", []))
                            p_cnt = len(tag_res.get("players", []))
                            print(f"🏷️  [ENTITY TAGGING] Article #{article_id} tagged with {t_cnt} teams and {p_cnt} players.")

                        if article_id and ctype == "ARTICLE" and at_id == "POWER_RANKINGS":
                            entries, pr_errors = parse_power_rankings(generated_text, load_team_index(cur))
                            if pr_errors:
                                print(f"⚠️  [POWER RANKINGS NOT STORED] Article #{article_id}: " + " | ".join(pr_errors))
                            else:
                                n = store_power_rankings(cur, season, week, article_id, compiled_prompt["archetype"], entries)
                                print(f"📊 [POWER RANKINGS STORED] Season {season} Week {week}: {n} teams from article #{article_id}.")
                    conn.commit()
            except Exception as e:
                print(f"Failed to record news_feed entry for {display}: {e}")

            if article_id:
                self._record_agent_memory(compiled_prompt, season, week, team_id, article_id, headline, generated_text, model)

        return {
            "text": generated_text,
            "headline": headline,
            "excerpt": excerpt,
            "article_id": article_id,
            "slot": assigned_slot,
            "article_type": at_id,
            "error": failure_reason,
        }

    def _record_agent_memory(self, compiled_prompt: dict, season: int, week: int, team_id, article_id: int,
                             headline: str, text: str, model: str):
        """After a saved post: tweets become a take as-is; articles get their score predictions parsed and up to 3
        takes extracted with one extra model call. Failures are logged and never affect the saved article."""
        agent_id = compiled_prompt.get("agent_id")
        display = compiled_prompt.get("display_name") or agent_id
        try:
            with psycopg.connect(self.db.db_conn_info) as conn, conn.cursor() as cur:
                if not agent_memory.tables_exist(cur):
                    return
                team_index = load_team_index(cur)
                if compiled_prompt.get("content_type") == "TWEET":
                    agent_memory.store_tweet_take(cur, agent_id, season, week, article_id, text, team_id)
                    conn.commit()
                    return
                n_preds = 0
                if compiled_prompt.get("temporal_mode") == "PROSPECTIVE":
                    preds = agent_memory.parse_score_predictions(
                        text, team_index, panel=compiled_prompt.get("article_type") == "SUPER_BOWL_ROUNDTABLE_CONSENSUS")
                    n_preds = agent_memory.store_predictions(cur, agent_id, season, compiled_prompt.get("preview_week") or week,
                                                             article_id, preds)
                conn.commit()
                raw = agent_memory.gemini_text(self.api_key, model, agent_memory.takes_extraction_prompt(display, headline, text))
                n_takes = agent_memory.store_takes(cur, agent_id, season, week, article_id,
                                                   agent_memory.parse_takes_json(raw), team_index)
                conn.commit()
                print(f"🧠 [MEMORY] {display}: {n_takes} takes, {n_preds} predictions from article #{article_id}.")
        except Exception as e:
            print(f"⚠️  [MEMORY NOT RECORDED] {display} article #{article_id}: {e}")

    def _power_rankings_team_index(self) -> list:
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                return load_team_index(cur)

    def get_agent_record(self, archetype: str) -> tuple:
        """Retrieves or synthesizes the agent memory record for a writer archetype."""
        # Exact archetype match only: Skip Bayless's tweet account (SKIP_TWEET) is a separate agent and must
        # never be picked up for his columns.
        arch_upper = canonical_writer_archetype(archetype)

        display_names = {
            "THE_ATHLETIC": "The Athletic Staff",
            "ROUNDTABLE_PANEL": "Super Bowl Roundtable Panel (Kimes, Simmons, Acho, Yates)",
            "SKIP_BAYLESS": "Skip Bayless",
            "BARNWELL": "Bill Barnwell",
            "SANDO": "Mike Sando",
            "JOYNER": "KC Joyner",
            "ROSENTHAL": "Gregg Rosenthal",
            "CHADIHA": "Jeffri Chadiha",
            "YATES": "Field Yates",
            "SIMMONS": "Bill Simmons",
            "ACHO": "Emmanuel Acho",
            "KIMES": "Mina Kimes",
            "ORLOVSKY": "Dan Orlovsky",
            "CLARK": "Ryan Clark",
            "BERRY": "Matthew Berry",
        }

        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE UPPER(archetype) = %s
                    ORDER BY id ASC LIMIT 1;
                """, (arch_upper,))
                agent = cur.fetchone()
                if agent:
                    agent_id, arch, memory, handle, display, assigned_team = agent
                    if not display:
                        display = display_names.get(arch_upper, agent_id.replace("_", " "))
                    if not handle:
                        handle = f"@{display.replace(' ', '')}"
                    return (agent_id, arch, memory, handle, display, assigned_team)

        # Fallback if not yet in database
        display = display_names.get(arch_upper, arch_upper.replace("_", " "))
        handle = f"@{display.replace(' ', '')}"
        memory = "Standard national football analyst perspective."
        return (arch_upper, arch_upper, memory, handle, display, None)

    def _get_distinct_beat_reporter(self, used_ids: set, season: int = None, team_id: int = None) -> tuple:
        """Finds a distinct beat reporter not yet assigned this week (restricted to team_id when given)."""
        try:
            with psycopg.connect(self.db.db_conn_info) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id
                        FROM agent_memories
                        WHERE archetype = 'LOCAL_BEAT_REPORTER'
                          AND NOT (agent_id = ANY(%s))
                          AND (%s::int IS NULL OR assigned_team_id = %s::int)
                        ORDER BY RANDOM() LIMIT 1;
                    """, (list(used_ids), team_id, team_id))
                    row = cur.fetchone()
                    if row:
                        agent_id, arch, memory, handle, display, assigned_team = row
                        if not display:
                            display = agent_id.replace("_", " ")
                        if not handle:
                            handle = f"@{display.replace(' ', '')}"
                        return (agent_id, arch, memory, handle, display, assigned_team)
        except Exception:
            pass
        return self.get_agent_record("LOCAL_BEAT_REPORTER")

    def commission_weekly_editorial_slate(self, season: int, week: int, target_articles: int = None) -> list[dict]:
        """Editorial Desk: Decides which article types to commission and assigns qualified writer agents
        following the official NFL 18-Week Regular Season & 5-Week Postseason Editorial Matrix."""
        if not self.article_types_data:
            return []

        # 1. Determine the planned article slots for this week based on the Editorial Matrix
        if week in POSTSEASON_EDITORIAL_SLATES:
            raw_slots = list(POSTSEASON_EDITORIAL_SLATES[week])
        else:
            if week == 1:
                hero = ("Hero Slot", "PRE_SEASON_ROSTER_TIERS")
                slot1 = ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS")
                slot2 = ("Slot 2", "NFL_TOP_100_HIERARCHY")
            else:
                hero = ("Hero Slot", "MACRO_SLATE_RECAP")
                slot1 = ("Slot 1", "SLATE_LOOKAHEAD_PREDICTIONS")
                slot2 = ("Slot 2", "SINGLE_GAME_SCHEMATIC_AUTOPSY")

            if 1 <= week <= 5:
                phase = 1
            elif 6 <= week <= 10:
                phase = 2
            elif 11 <= week <= 14:
                phase = 3
            else:
                phase = 4

            phase_data = REGULAR_SEASON_FEATURE_POOLS[phase]
            pool = list(phase_data["pool"])
            if phase == 2:
                if week < 10 and "PLAYOFF_PREDICTOR" in pool:
                    pool.remove("PLAYOFF_PREDICTOR")
                elif week >= 10 and "PLAYOFF_PREDICTOR" not in pool:
                    pool.append("PLAYOFF_PREDICTOR")

            # Filter pool against available article types
            available_pool = [t for t in pool if t == "ROTATING_COLUMNIST" or t in self.article_types_data]
            sample_size = min(3, len(available_pool))
            selected_features = random.sample(available_pool, sample_size)

            feature_slots = []
            for idx, feature_type in enumerate(selected_features, 3):
                if feature_type == "ROTATING_COLUMNIST":
                    columnist = random.choice(phase_data["columnists"])
                    feature_slots.append((f"Slot {idx}", "NATIONAL_OPINION_COLUMN", columnist))
                else:
                    feature_slots.append((f"Slot {idx}", feature_type))

            raw_slots = [
                hero,
                slot1,
                slot2,
                *feature_slots,
                ("Slot 6", "LOCAL_MATCHUP_PRIMER"),
                ("Slot 7", "LOCAL_MATCHUP_PRIMER"),
                ("Slot 8", "LOCAL_MATCHUP_PRIMER"),
                ("Standalone", "POWER_RANKINGS"),
            ]

        # Optional truncation for tests/debugging only; production runs the full 10-slot matrix
        if target_articles is not None:
            raw_slots = raw_slots[:target_articles]

        # 2. Resolve slot subjects (teams/games) per the matrix's Slot Targeting Rules
        slot_targets = self._resolve_slot_targets(season, week, raw_slots)

        # 3. Assign qualified writer agents to each commissioned article slot
        writer_assignment_counts = collections.defaultdict(int)
        commissioned_slate = []
        used_beat_reporter_ids = set()

        for slot_tuple, slot_target in zip(raw_slots, slot_targets):
            slot_name = slot_tuple[0]
            at_id = slot_tuple[1]
            preferred_archetype = slot_tuple[2] if len(slot_tuple) > 2 else None

            at_info = self.article_types_data.get(at_id)
            if not at_info:
                continue

            if preferred_archetype:
                chosen_archetype = canonical_writer_archetype(preferred_archetype)
            elif at_id == "LOCAL_MATCHUP_PRIMER":
                chosen_archetype = "LOCAL_BEAT_REPORTER"
            else:
                allowed_writers = at_info.get("allowed_writers", [])
                if not allowed_writers:
                    continue
                min_count = min(writer_assignment_counts[w] for w in allowed_writers)
                eligible_candidates = [w for w in allowed_writers if writer_assignment_counts[w] == min_count]
                chosen_archetype = random.choice(eligible_candidates)

            writer_assignment_counts[chosen_archetype] += 1

            # Fetch agent record
            if chosen_archetype == "LOCAL_BEAT_REPORTER":
                target_team_id = slot_target["team_id"] if slot_target else None
                agent_rec = self._get_distinct_beat_reporter(used_beat_reporter_ids, season, team_id=target_team_id)
                if agent_rec:
                    used_beat_reporter_ids.add(agent_rec[0])
            else:
                agent_rec = self.get_agent_record(chosen_archetype)

            commissioned_slate.append({
                "slot": slot_name,
                "article_type_id": at_id,
                "article_type_name": at_info.get("name", at_id),
                "archetype": chosen_archetype,
                "agent_id": agent_rec[0],
                "handle": agent_rec[3],
                "display_name": agent_rec[4] or agent_rec[0],
                "agent_record": agent_rec,
                "target": slot_target,
            })

        return commissioned_slate

    def _resolve_slot_targets(self, season: int, week: int, raw_slots: list) -> list:
        """Chooses each targeted slot's subject; falls back to writer-chosen subjects if league data is unavailable."""
        try:
            snapshot = load_league_snapshot(self.db.db_conn_info, season, week)
            return resolve_slot_targets(raw_slots, snapshot)
        except Exception as e:
            print(f"⚠️ Slot targeting unavailable ({e}); writers will choose their own subjects.")
            return [None] * len(raw_slots)

    def dispatch_single_archetype(self, archetype: str, brief: dict, season: int, week: int, live: bool = False, model: str = "gemini-3.8-flash", save_to_db: bool = False, article_type: str = None):
        """Dispatches a single specific agent archetype for targeted testing and generation."""
        arch_upper = archetype.upper().strip()
        print(f"\n🎯 --- DISPATCHING TARGETED AGENT ({arch_upper}, Season {season}, Week {week}) ---")

        agent = self.get_agent_record(arch_upper)

        if article_type:
            content_type = "ARTICLE"
        else:
            content_type = "ARTICLE" if arch_upper in ("BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "LOCAL_BEAT_REPORTER", "THE_ATHLETIC") else "TWEET"
        compiled = self.compile_agent_prompt(
            agent[0], agent[1], agent[2], agent[3], agent[4], agent[5], brief, season, week, content_type, article_type=article_type
        )
        return self._execute_agent(compiled, season, week, agent[5], live=live, model=model, save_to_db=save_to_db)

    def dispatch_weekly_national(
        self,
        brief: dict,
        season: int,
        week: int,
        article_count: int = None,
        live: bool = False,
        model: str = "gemini-3.8-flash",
        save_to_db: bool = False,
        max_workers: int = 5
    ):
        """Runs the global weekly wakeup logic with article-first editorial commissioning.

        Commissions the full 10-slot Editorial Matrix (docs/EDITORIAL_MATRIX.md) by default.
        article_count truncates the slate and is intended for tests/debugging only."""
        print(f"\n🌍 --- INITIATING WEEKLY NATIONAL WAKEUP (Season {season}, Week {week}) ---")
        print(f"-> Target Article Volume: {article_count if article_count is not None else 'Full Editorial Matrix'}")
        print(f"-> Concurrency Level: {max_workers} worker threads")

        # 1. Commission the Editorial Matrix slate (Hero, Slots 1–8, Standalone)
        commissioned_articles = self.commission_weekly_editorial_slate(season, week, target_articles=article_count)
        print(f"\n📰 --- COMMISSIONED EDITORIAL DESK ({len(commissioned_articles)} Articles Planned) ---")
        for idx, item in enumerate(commissioned_articles, 1):
            slot_name = item.get("slot", f"Slot {idx}")
            target = item.get("target")
            target_str = f" | Subject: {target['team_name']}" if target else ""
            print(f"   [{idx}] [{slot_name}] {item['article_type_name']} ({item['article_type_id']}) -> {item['display_name']} ({item['archetype']}){target_str}")

        # Agent Wire posts (reactions, shares, replies) are written after the slates by
        # newsroom/social_publish.run_social_week, called from newsroom/weekly_run.py.

        print(f"\n-> Dispatching: {len(commissioned_articles)} Commissioned Editorial Matrix Articles.")

        # Compile article tasks
        article_tasks = []
        for item in commissioned_articles:
            agent = item["agent_record"]
            target = item.get("target")
            # Targeted slots are filed under their subject team; otherwise under the writer's assigned team
            subject_team_id = target["team_id"] if target else agent[5]
            p = self.compile_agent_prompt(
                agent[0], agent[1], agent[2], agent[3], agent[4], subject_team_id,
                brief, season, week, "ARTICLE", article_type=item["article_type_id"], slot_target=target
            )
            article_tasks.append((p, subject_team_id, item["article_type_name"], agent[4] or agent[0], item.get("slot"), item["article_type_id"]))

        # Helper executor for articles
        def _run_article_job(task):
            prompt, team_id, art_type, writer_name, slot_name, at_id = task
            print(f"🚀 [PARALLEL WORKER] Starting [{slot_name}]: {art_type} by {writer_name}")
            res = self._execute_agent(
                prompt, season, week, team_id, live=live, model=model, save_to_db=save_to_db, slot=slot_name, article_type=at_id
            )
            print(f"✅ [PARALLEL WORKER] Completed [{slot_name}]: {art_type} by {writer_name}")
            return res

        # Execute Articles (in Parallel if max_workers > 1)
        if max_workers > 1 and len(article_tasks) > 1:
            print(f"\n⚡ Executing {len(article_tasks)} Articles in Parallel (ThreadPoolExecutor with {max_workers} workers)...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_run_article_job, t) for t in article_tasks]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        print(f"❌ Worker article generation generated an exception: {exc}")
        else:
            print(f"\n🐢 Executing {len(article_tasks)} Articles sequentially...")
            for t in article_tasks:
                _run_article_job(t)


    def commission_fantasy_slate(self, season: int, week: int) -> list[dict]:
        """Fantasy desk: the week's FANTASY_* slots (newsroom/fantasy_desk.py) with a writer for each.
        Writers are spread across each type's allowed_writers (fewest assignments this week first)."""
        counts = collections.defaultdict(int)
        slate = []
        for item in fantasy_slate(week):
            at_info = self.article_types_data.get(item["article_type"])
            if not at_info or not at_info.get("allowed_writers"):
                print(f"⚠️ Fantasy slot {item['slot']}: article type {item['article_type']} is not configured; skipped.")
                continue
            allowed = at_info["allowed_writers"]
            fewest = min(counts[w] for w in allowed)
            archetype = canonical_writer_archetype(random.choice([w for w in allowed if counts[w] == fewest]))
            counts[archetype] += 1
            agent_rec = self.get_agent_record(archetype)
            slate.append({
                "slot": item["slot"],
                "article_type_id": item["article_type"],
                "article_type_name": at_info.get("name", item["article_type"]),
                "archetype": archetype,
                "agent_id": agent_rec[0],
                "display_name": agent_rec[4] or agent_rec[0],
                "agent_record": agent_rec,
                "desk_note": item.get("note") or None,
            })
        return slate

    def dispatch_weekly_fantasy(
        self,
        season: int,
        week: int,
        live: bool = False,
        model: str = "gemini-3.8-flash",
        save_to_db: bool = False,
        max_workers: int = 5,
    ) -> list:
        """Writes the week's Mu Fantasy slate (docs/FANTASY_EDITORIAL_MATRIX.md). No slate in Weeks 19–23."""
        phase = fantasy_phase(week)
        if phase is None:
            print(f"[Fantasy] Week {week}: offseason, no fantasy slate (the page keeps the Week 18 slate).")
            return []
        slate = self.commission_fantasy_slate(season, week)
        print(f"\n🏈 --- FANTASY DESK (Season {season}, Week {week}: {phase}, {len(slate)} articles) ---")
        for idx, item in enumerate(slate, 1):
            print(f"   [{idx}] [{item['slot']}] {item['article_type_name']} ({item['article_type_id']}) -> "
                  f"{item['display_name']} ({item['archetype']})" + (f" | {item['desk_note']}" if item["desk_note"] else ""))

        brief = {
            "desk": "Mu Fantasy",
            "season": season,
            "publication_week": week,
            "fantasy_phase": phase,
            "this_week": fantasy_week_label(week) if week > 1 else "preseason draft prep",
            "results_available_through_week": week - 1,
            "scoring": "full PPR",
        }

        def run(item):
            agent = item["agent_record"]
            prompt = self.compile_agent_prompt(
                agent[0], agent[1], agent[2], agent[3], agent[4], None, brief, season, week, "ARTICLE",
                article_type=item["article_type_id"], desk_note=item["desk_note"],
            )
            print(f"🚀 [FANTASY] Starting [{item['slot']}]: {item['article_type_name']} by {item['display_name']}")
            res = self._execute_agent(prompt, season, week, None, live=live, model=model, save_to_db=save_to_db,
                                      slot=item["slot"], article_type=item["article_type_id"])
            print(f"✅ [FANTASY] Completed [{item['slot']}]: {item['article_type_name']}")
            return res

        results = []
        if max_workers > 1 and len(slate) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run, item) for item in slate]
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        print(f"❌ Fantasy article generation raised an exception: {exc}")
        else:
            results = [run(item) for item in slate]
        return results

    def dispatch_team_local(
        self,
        team_id: int,
        brief: dict,
        season: int,
        week: int,
        live: bool = False,
        model: str = "gemini-3.8-flash",
        save_to_db: bool = False,
        max_workers: int = 5
    ):
        """Runs the on-demand local wakeup when a team page or Team Hub is viewed: the local beat portfolio, then
        the team's Agent Wire posts (newsroom/social_publish.run_team_social), which also share the new articles."""
        print(f"\n📍 --- INITIATING LOCAL TEAM WAKEUP (Team {team_id}, Week {week}) ---")
        print(f"-> Concurrency Level: {max_workers} worker threads")

        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # 1. Wake up ALL Beat Reporters for this team
                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE assigned_team_id = %s AND archetype = 'LOCAL_BEAT_REPORTER';
                """, (team_id,))
                local_beats = cur.fetchall()

                # 2. Inputs for the local beat portfolio: last week's result, this week's game, and what exists
                cur.execute("""
                    SELECT 1 FROM games
                    WHERE season = %s AND week = %s AND status = 'COMPLETED' AND %s IN (home_team_id, away_team_id)
                    LIMIT 1;
                """, (season, week - 1, team_id))
                played_last_week = cur.fetchone() is not None
                cur.execute("""
                    SELECT g.id, t.name, o.id, o.name
                    FROM games g
                    JOIN teams t ON t.id = %s
                    JOIN teams o ON o.id = CASE WHEN g.home_team_id = %s THEN g.away_team_id ELSE g.home_team_id END
                    WHERE g.season = %s AND g.week = %s AND g.status <> 'BYE' AND %s IN (g.home_team_id, g.away_team_id)
                    LIMIT 1;
                """, (team_id, team_id, season, week, team_id))
                this_week_game = cur.fetchone()
                cur.execute("""
                    SELECT DISTINCT article_type FROM news_feed
                    WHERE season = %s AND week = %s AND team_id = %s AND content_type = 'ARTICLE'
                      AND article_type LIKE 'LOCAL\\_%%';
                """, (season, week, team_id))
                existing_local_types = {r[0] for r in cur.fetchall()}

        print(f"-> Waking up {len(local_beats)} Beat Reporters.")

        # Full local beat portfolio every wakeup, dealt across the team's reporters (newsroom/local_portfolio.py):
        # postgame report (if they played last week), matchup primer (if they play this week), player spotlight,
        # and season progress report (Week 6+), skipping types the team already has this week.
        portfolio = plan_local_portfolio(
            len(local_beats), week, played_last_week, this_week_game is not None, existing_local_types
        )
        print(f"-> Local beat portfolio: {[t for _, t in portfolio] or 'nothing new to write'}")
        primer_target = None
        if this_week_game:
            game_id, team_name, opp_id, opp_name = this_week_game
            primer_target = {
                "team_id": team_id, "team_name": team_name, "game_id": game_id,
                "opponent_team_id": opp_id, "opponent_name": opp_name,
                "note": f"Preview the {team_name}' Week {week} game against the {opp_name} from the {team_name} locker room.",
            }
        article_tasks = []
        for idx, (reporter_idx, assigned_local_type) in enumerate(portfolio):
            b = local_beats[reporter_idx]
            local_slot = f"Local Beat {idx + 1}"
            p = self.compile_agent_prompt(
                b[0], b[1], b[2], b[3], b[4], b[5],
                brief, season, week, "ARTICLE", article_type=assigned_local_type,
                slot_target=primer_target if assigned_local_type == LOCAL_PRIMER else None,
            )
            article_tasks.append((p, team_id, assigned_local_type, b[4] or b[0], local_slot, assigned_local_type, b[0]))

        # Saved local articles, with their reporter, for the Agent Wire shares below
        written = []

        # Helper executor for articles
        def _run_article_job(task):
            prompt, t_id, art_type, writer_name, slot_name, at_id, agent_id = task
            print(f"🚀 [LOCAL WORKER] Starting [{slot_name}]: {art_type} by {writer_name}")
            res = self._execute_agent(
                prompt, season, week, t_id, live=live, model=model, save_to_db=save_to_db, slot=slot_name, article_type=at_id
            )
            print(f"✅ [LOCAL WORKER] Completed [{slot_name}]: {art_type} by {writer_name}")
            if res and res.get("article_id"):
                written.append(({"id": res["article_id"], "headline": res.get("headline"), "excerpt": res.get("excerpt"),
                                 "article_type": at_id, "slot": slot_name}, agent_id))
            return res

        # Execute Articles (in Parallel if max_workers > 1)
        if max_workers > 1 and len(article_tasks) > 1:
            print(f"\n⚡ Executing {len(article_tasks)} Local Beat Articles in Parallel ({max_workers} workers)...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_run_article_job, t) for t in article_tasks]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        print(f"❌ Local Beat article exception: {exc}")
        else:
            for t in article_tasks:
                _run_article_job(t)

        # Agent Wire: beat notes, fan reactions and previews, pundits by popularity, and shares of the new articles
        if live and save_to_db:
            try:
                from newsroom.social_publish import run_team_social
                run_team_social(self.db.db_conn_info, season, week, team_id, local_articles=written,
                                model=model, max_workers=max_workers, tagger=self.entity_tagger)
            except Exception as exc:
                print(f"❌ Agent Wire team posts failed: {exc}")
        else:
            print("-> Agent Wire team posts are only written on live, saved wakeups (newsroom/social_publish.py).")
