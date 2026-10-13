import os
import re
import json
import psycopg
import functools
import random
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
from db.db_handler import DatabaseHandler
from newsroom.slot_targets import load_league_snapshot, resolve_slot_targets
from engines.gotw_engine import display_excitement
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
    get_team_efficiency_profile,
    get_matchup_edges,
    get_weekly_slate,
    get_season_standings,
    get_top_popular_players,
    get_all_team_popularities,
    get_draft_order,
    get_team_draft_needs,
    get_team_roster_depth_chart,
    get_draft_trade_value,
    get_franchise_rebuild_status,
    get_team_articles,
    get_player_articles,
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
        # Default slate_manifest points to the completed games (for backward compatibility)
        "slate_manifest": {
            "target_week": recap_week if week > 1 else week,
            "total_games": 0,
            "all_games": []
        },
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
        "league_leaders": {
            "most_watchable_teams": [],
            "most_popular_teams": []
        },
        "excitement_scale_note": (
            "Excitement Index values are on a 70–100 scale: about 70 is a dull game, about 80 is typical, "
            "90+ is a classic. projected_excitement is a pre-game projection; actual games often land well "
            "above or below it."
        ),
        "curatorial_guidance": (
            "CURATORIAL AUTONOMY: You have complete editorial independence to choose which matchups, "
            "teams, or players from this slate to highlight. Do NOT feel obligated to write about "
            "the same games as other journalists. Select games and storylines that align with your distinct "
            "journalistic beat and persona. You have access to autonomous database tools to investigate "
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
                # Populate default slate_manifest with completed games
                brief["slate_manifest"]["all_games"].append(game_dict)

            brief["recap_slate"]["total_games"] = len(recap_games)
            brief["slate_manifest"]["total_games"] = len(recap_games)

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

            for pg in preview_games:
                brief["preview_slate"]["all_games"].append({
                    "game_id": pg[0],
                    "matchup": f"{pg[1]} @ {pg[2]}",
                    "away_team": f"{pg[1]} (ID: {pg[6]})",
                    "home_team": f"{pg[2]} (ID: {pg[5]})",
                    "kickoff_time": pg[3],
                    "projected_excitement": display_excitement(pg[4])
                })
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

            # 4. Fetch top watchable and popular teams
            cur.execute("""
                SELECT t.name, ts.team_id, ts.wins, ts.losses, ts.watchability_index
                FROM teams t JOIN team_seasons ts ON t.id = ts.team_id
                WHERE ts.season = %s ORDER BY ts.watchability_index DESC NULLS LAST LIMIT 5;
            """, (season,))
            brief["league_leaders"]["most_watchable_teams"] = [
                f"{r[0]} ({r[2]}-{r[3]}, Watchability: {round(float(r[4] or 0), 1)})" for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT t.name, ts.team_id, ts.wins, ts.losses, ts.popularity_index
                FROM teams t JOIN team_seasons ts ON t.id = ts.team_id
                WHERE ts.season = %s ORDER BY ts.popularity_index DESC NULLS LAST LIMIT 5;
            """, (season,))
            brief["league_leaders"]["most_popular_teams"] = [
                f"{r[0]} ({r[2]}-{r[3]}, Popularity: {round(float(r[4] or 0), 1)})" for r in cur.fetchall()
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
        "columnists": ["SIMMONS", "KIMES", "ACHO", "BAYLESS"],
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
        "columnists": ["YATES", "KIMES", "ACHO", "SIMMONS", "BAYLESS"],
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
        "columnists": ["BAYLESS", "KIMES", "YATES", "ACHO"],
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
        "columnists": ["SIMMONS", "BAYLESS", "YATES", "KIMES"],
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
            regex_parts = []
            # Full team name (e.g. "Buffalo Bills")
            full_name = f"{city} {name}".strip()
            regex_parts.append(r"\b" + re.escape(full_name) + r"\b")

            # Team nickname (e.g. "Bills", "49ers", "Cowboys")
            regex_parts.append(r"\b" + re.escape(name) + r"\b")

            # Team abbreviation (e.g. "KC", "BUF", "NE")
            if len(abbr) >= 2:
                regex_parts.append(r"\b" + re.escape(abbr) + r"\b")

            # City name if distinct and non-generic
            if city not in ("New York", "Los Angeles"):
                regex_parts.append(r"\b" + re.escape(city) + r"\b")

            combined_pat = re.compile("|".join(regex_parts), re.IGNORECASE)
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
            self.personas_data = json.load(f).get("archetypes", {})

        # Load article structure directives
        directives_path = os.path.join(CONFIG_DIR, "article_structure_directives.json")
        if os.path.exists(directives_path):
            with open(directives_path, "r") as f:
                self.article_structure_directives = json.load(f)
        else:
            self.article_structure_directives = {}

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
            get_team_efficiency_profile,
            get_matchup_edges,
            get_weekly_slate,
            get_season_standings,
            get_top_popular_players,
            get_all_team_popularities,
            get_draft_order,
            get_team_draft_needs,
            get_team_roster_depth_chart,
            get_draft_trade_value,
            get_franchise_rebuild_status,
            get_team_articles,
            get_player_articles,
        ]

        self.entity_tagger = EntityTaggingService(self.db.db_conn_info)

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
    ) -> dict:
        """Assembles the exact persona, memory, assignment brief, and tools prompt for an agent.

        slot_target (from newsroom.slot_targets) pins the article's subject chosen by the Editorial Desk."""
        base_archetype_prompt = self.personas_data.get(
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

        tool_descriptions = "\n".join([f"- `{t.name}`: {t.description}" for t in self.tools])

        article_type_info = None
        article_type_directive = ""
        format_display = content_type
        active_playoff_override = None
        if content_type == "ARTICLE":
            article_type_info = self.get_article_type_for_writer(archetype, requested_type=article_type)
            if article_type_info:
                # Check for playoff-specific overrides based on target_weeks
                at_id = article_type_info.get("id")
                if at_id in self.playoff_overrides:
                    for ov in self.playoff_overrides[at_id]:
                        if week in ov.get("target_weeks", []):
                            active_playoff_override = ov
                            break

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

        system_instruction = f"""ROLE & PERSONA:
{base_archetype_prompt}

IDENTITY & HANDLE:
- Name: {display_name or agent_id}
- Handle: {handle or '@NFL_Insider'}
- Content Format: {format_display} (Write in character matching this format!)

PERSISTENT WORLDVIEW & MEMORIES:
{persistent_memory or 'No specific grudges or previous memories recorded.'}

AVAILABLE DATABASE REAC/TOOLS:
You have autonomous access to query the simulation database using these functions:
{tool_descriptions}

WEEK CONTEXT:
Calendar Week: Season {season} | Week {week}
Assigned Team ID: {assigned_team_id if assigned_team_id is not None else 'National Coverage'}
"""

        # Determine Temporal Mode (RETROSPECTIVE, PROSPECTIVE, or CUMULATIVE)
        at_id = article_type_info.get("id") if article_type_info else None
        temporal_mode = active_playoff_override.get("temporal_mode") if active_playoff_override else None
        if not temporal_mode and at_id:
            temporal_mode = TEMPORAL_ARTICLE_MODES.get(at_id, "CUMULATIVE")

        recap_week = brief.get("recap_week") or (max(1, week - 1) if week > 1 else 1)

        temporal_instruction = ""
        if temporal_mode == "RETROSPECTIVE":
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (RETROSPECTIVE SLATE REVIEW):
- You are publishing during Week {week} looking back at the completed Week {recap_week} slate that just concluded.
- Focus strictly on the games, outcomes, and standout tape from Week {recap_week}.
- Do NOT treat Week {week} matchups as completed; Week {week} has not kicked off yet.
- Use your database tools to investigate the completed Week {recap_week} box scores, margins, and player stats.
"""
        elif temporal_mode == "PROSPECTIVE":
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (PROSPECTIVE MATCHUP PREVIEW):
- You are publishing ahead of Week {week} action.
- Focus strictly on previewing the upcoming scheduled matchups for Week {week}.
- Analyze team records, head-to-head match-ups, injury statuses, and tactical blueprints for the games taking place in Week {week}.
"""
        elif temporal_mode == "CUMULATIVE":
            temporal_instruction = f"""
TEMPORAL PUBLISHING CONTEXT (CUMULATIVE / ENTERING WEEK {week}):
- You are publishing ahead of Week {week}, evaluating cumulative performance across Weeks 1 through {recap_week}.
- Focus on overall season standings, trajectory, award races, or roster construction entering Week {week}.
- Week {week} games have not taken place yet.
"""

        national_writers_list = ("BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "YATES", "SIMMONS", "ACHO", "THE_ATHLETIC", "ROUNDTABLE_PANEL", "SKIP_BAYLESS", "BAYLESS", "KIMES", "ORLOVSKY", "CLARK")

        national_writer_guidelines = ""
        if content_type == "ARTICLE" and archetype in national_writers_list:
            national_writer_guidelines = """
CRITICAL WRITER RESEARCH TOOLS & HISTORICAL COMPARISONS:
1. DEEP INVESTIGATIVE RESEARCH: Conduct thorough investigations into your subjects. Query division standings, team advanced analytics, season schedules, game box scores, and player context for all contenders, pretenders, or matchup subjects you evaluate. Call whatever tools you need to build an ironclad case with granular facts and box-score details. Once you have gathered sufficient evidence across your subjects, synthesize your findings into the full article.

2. RESEARCH TOOLS: First, analyze the provided JSON ASSIGNMENT BRIEF. You have autonomous database tools to dive into any game, team, box score, season schedule, standings, or player across the league.
- If you need a team's complete season schedule and game log (scores, margins, one-score game outcomes, game IDs), call `get_team_season_schedule`.
- If you need full division standings and point differentials, call `get_season_standings`.
- If you need schedule-adjusted team strength (Opponent-Adjusted Game Credit, strength of schedule, expected wins, and which records are lucky or unlucky), call `get_opponent_adjusted_credit` (no team = all 32 ranked; with a team = game-by-game credit log). It also gives each team's Trajectory (Surging to Sliding over the last 4 games).
- If you need Pythagorean wins (what points for/against say the record should be), team pace, or per-game passing/rushing yards and giveaways, call `get_team_efficiency_profile`.
- If you need where one team's offense has the edge over another's defense (passing vs. coverage, rushing vs. defensive rating, ball security, offensive line vs. pass rush), call `get_matchup_edges` (no opponent = their next game).
- If you need all 32 team popularity rankings and watchability indices, call `get_all_team_popularities`.
- If you need the top popular players in the NFL, call `get_top_popular_players`.
- If you need a game's detailed player contributions, call `get_game_box_score`.
- If you need the official Round 1 selection order (Picks 1-32), call `get_draft_order`.
- If you need a team's primary roster needs ranked by urgency, call `get_team_draft_needs`.
- If you need a team's complete positional depth chart with age brackets, call `get_team_roster_depth_chart`.
- If you need draft pick trade points (Jimmy Johnson model) and trade package scenarios, call `get_draft_trade_value`.
- If you need a franchise's rebuild lifecycle stage and draft posture, call `get_franchise_rebuild_status`.

3. HISTORICAL COMPARISONS (1990–2023): Do not invent or hallucinate historical teams. You possess deep knowledge of NFL history. When analyzing the current week's data, you must independently match the team's statistical profile (e.g., their tempo, watchability, or trajectory) to a real-life historical NFL team from 1990–2023.
- If a team is winning ugly with low tempo but high Win%, compare them to teams like the 2000 Ravens, 2015 Broncos, 2011 Giants or a team with a similar profile.
- If a team has an explosive scoring pace but loses shootouts, compare them to the 2018 Chiefs, 2020 Cowboys or a team with a similar profile.
- If a team is suffering from an aging roster and bloated cap, compare them to the late-era Drew Brees Saints or the 2022 Colts or a team with a similar profile.
State the historical comparison explicitly in your analysis to validate your argument.
"""
        elif content_type == "ARTICLE" and archetype == "LOCAL_BEAT_REPORTER":
            national_writer_guidelines = """
CRITICAL BEAT WRITER RESEARCH & LOCAL REPORTING DIRECTIVE:
1. TEAM-SPECIFIC INVESTIGATION: Your focus is strictly your assigned franchise. Do NOT review unrelated games across the national schedule.
2. RESEARCH TOOLS:
- Query your team's most recent box score and player performances with `get_game_box_score`.
- Query your team's complete game schedule, results, and margins with `get_team_season_schedule`.
- Query individual roster stats and injury/development notes with `get_player_season_stats` and `get_player_context`.
- Query team baseline ratings and top stars with `get_team_context`.
3. AUTHENTIC BEAT REPORTING: Deliver your reporting with the immediacy, nuance, and accountability of an everyday beat writer inside the locker room. Quote coaching staff sentiment, cite specific situational downs (3rd down conversions, red zone execution, line protection), and speak directly to the local fan base without academic or algebraic math.
"""


        if active_playoff_override and active_playoff_override.get("curatorial_autonomy_override"):
            article_curatorial = active_playoff_override["curatorial_autonomy_override"].strip()
        else:
            article_curatorial = (article_type_info.get("curatorial_autonomy") if article_type_info else "").strip()
        writer_curatorial = self.curatorial_autonomy_directives.get(archetype, "").strip()

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

        task_instruction = f"""ASSIGNMENT BRIEF:
{json.dumps(brief, indent=2)}

TASK:
You must analyze the action through your specific analytical lens.
If you need specific stats, use your database tools before producing your final text.
{"Write a long-form, analytical article with markdown headings and deep analysis." if content_type == 'ARTICLE' else "Write a short, punchy, viral tweet (under 280 characters) with appropriate tone, slang, hashtags, or emojis."}
{article_header_directive}
{temporal_instruction}
{article_type_directive}
{curatorial_directive}
{slot_target_directive}
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

        # Call Gemini if live mode is active
        if live:
            if not self.api_key:
                print("\n⚠️  [GEMINI LIVE CALL SKIPPED]: GEMINI_API_KEY environment variable is not set.")
                print("   Set it via: export GEMINI_API_KEY=\"your_key\" or pass --api-key YOUR_KEY")
                generated_text = f"[{ctype}] Gemini execution skipped: Missing GEMINI_API_KEY."
            else:
                print(f"\n🚀 Sending prompt to Gemini ({model}) with Autonomous Database Tools...")
                try:
                    from google import genai
                    from google.genai import types

                    client = genai.Client(api_key=self.api_key)

                    # Prepare raw functions with logging wrappers for Gemini AFC (preserving parameter signatures)
                    tool_call_counts = collections.defaultdict(int)
                    tools_for_gemini = []
                    for t in self.tools:
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
                    generated_text = f"Error calling Gemini: {e}"
        else:
            t_mode = compiled_prompt.get("temporal_mode")
            r_week = compiled_prompt.get("recap_week") or (max(1, week - 1) if week > 1 else 1)
            if t_mode == "RETROSPECTIVE":
                generated_text = f"[{ctype}] Mock analysis by {display} ({handle}) reviewing Week {r_week} action."
            else:
                generated_text = f"[{ctype}] Mock analysis by {display} ({handle}) analyzing Week {week} action."

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

        # Save output to news_feed table ONLY if explicitly enabled
        article_id = None
        if save_to_db:
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
                    conn.commit()
            except Exception as e:
                print(f"Failed to record news_feed entry for {display}: {e}")

        return {
            "text": generated_text,
            "headline": headline,
            "excerpt": excerpt,
            "article_id": article_id,
            "slot": assigned_slot,
            "article_type": at_id
        }

    def get_agent_record(self, archetype: str) -> tuple:
        """Retrieves or synthesizes the agent memory record for a writer archetype."""
        arch_upper = archetype.upper().strip()
        aliases = [arch_upper]
        if arch_upper in ("SKIP_BAYLESS", "BAYLESS", "SKIP_TWEET"):
            aliases = ["SKIP_BAYLESS", "BAYLESS", "SKIP_TWEET"]

        display_names = {
            "THE_ATHLETIC": "The Athletic Staff",
            "ROUNDTABLE_PANEL": "Super Bowl Roundtable Panel (Kimes, Simmons, Acho, Yates)",
            "SKIP_BAYLESS": "Skip Bayless",
            "BAYLESS": "Skip Bayless",
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
        }

        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE UPPER(archetype) = ANY(%s) 
                    ORDER BY id ASC LIMIT 1;
                """, (aliases,))
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
                chosen_archetype = preferred_archetype
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

        # 2. Wake up reaction agents (National Pundits, Bandwagons, Die-Hards)
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Random National Pundits & Bandwagons
                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE archetype NOT IN ('BARNWELL', 'SANDO', 'JOYNER', 'ROSENTHAL', 'CHADIHA', 'KIMES', 'ORLOVSKY', 'LOCAL_BEAT_REPORTER', 'DIE_HARD_FAN', 'BANDWAGON_FAN') 
                    ORDER BY RANDOM() LIMIT 4;
                """)
                pundits = cur.fetchall()

                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE archetype = 'BANDWAGON_FAN' 
                    ORDER BY RANDOM() LIMIT 2;
                """)
                bandwagons = cur.fetchall()

                # Die-Hard Homers from exciting games
                exciting_game_ids = [g["game_id"] for g in brief.get("slate_manifest", {}).get("all_games", [])[:5]]
                homers = []
                if exciting_game_ids:
                    cur.execute("""
                        SELECT home_team_id, away_team_id FROM games WHERE id = ANY(%s)
                    """, (exciting_game_ids,))
                    teams_involved = []
                    for r in cur.fetchall():
                        teams_involved.extend([r[0], r[1]])

                    if teams_involved:
                        cur.execute("""
                            WITH RankedHomers AS (
                                SELECT *, ROW_NUMBER() OVER(PARTITION BY assigned_team_id ORDER BY RANDOM()) as rn
                                FROM agent_memories WHERE assigned_team_id = ANY(%s) AND archetype = 'DIE_HARD_FAN'
                            )
                            SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                            FROM RankedHomers WHERE rn <= 1;
                        """, (teams_involved,))
                        homers = cur.fetchall()

        print(f"\n-> Dispatching: {len(commissioned_articles)} Commissioned Editorial Matrix Articles.")
        print(f"-> Dispatching: {len(pundits)} Pundit Reactions, {len(bandwagons)} Bandwagon Posts, {len(homers)} Fan Posts.")

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

        # Dispatch Tweets
        tweet_tasks = []
        for pundit in pundits:
            p = self.compile_agent_prompt(pundit[0], pundit[1], pundit[2], pundit[3], pundit[4], pundit[5], brief, season, week, "TWEET")
            tweet_tasks.append((p, pundit[5]))

        for fan in bandwagons + homers:
            p = self.compile_agent_prompt(fan[0], fan[1], fan[2], fan[3], fan[4], fan[5], brief, season, week, "TWEET")
            tweet_tasks.append((p, fan[5]))

        if max_workers > 1 and len(tweet_tasks) > 1:
            print(f"\n⚡ Executing {len(tweet_tasks)} Reaction Tweets in Parallel...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(self._execute_agent, t[0], season, week, t[1], live, model, save_to_db) for t in tweet_tasks]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        print(f"❌ Tweet generation exception: {exc}")
        else:
            for t in tweet_tasks:
                self._execute_agent(t[0], season, week, t[1], live=live, model=model, save_to_db=save_to_db)

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
        """Runs the on-demand local wakeup logic when a team page is viewed."""
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

                # 2. Wake up Die-Hard Homers for this team
                cur.execute("""
                    SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                    FROM agent_memories 
                    WHERE assigned_team_id = %s AND archetype = 'DIE_HARD_FAN';
                """, (team_id,))
                local_homers = cur.fetchall()

                # 3. Determine how many National Agents care based on POP
                cur.execute("SELECT popularity_index FROM team_seasons WHERE team_id = %s AND season = %s", (team_id, season))
                row = cur.fetchone()
                pop = row[0] if row and row[0] is not None else 50.0

                num_pundits = 3 if pop > 70 else (1 if pop > 55 else 0)

                pundits = []
                if num_pundits > 0:
                    cur.execute("""
                        SELECT agent_id, archetype, persistent_memory, handle, display_name, assigned_team_id 
                        FROM agent_memories 
                        WHERE archetype NOT IN ('BARNWELL', 'SANDO', 'JOYNER', 'ROSENTHAL', 'CHADIHA', 'KIMES', 'ORLOVSKY', 'LOCAL_BEAT_REPORTER', 'DIE_HARD_FAN', 'BANDWAGON_FAN') 
                        ORDER BY RANDOM() LIMIT %s;
                    """, (num_pundits,))
                    pundits = cur.fetchall()

        print(f"-> Waking up {len(local_beats)} Beat Reporters and {len(local_homers)} Die-Hards.")
        print(f"-> Team POP is {pop:.1f}. Waking up {len(pundits)} National Pundits to comment.")

        # Compile Beat Article tasks using full local beat portfolio:
        # 1. LOCAL_POSTGAME_REPORT_CARD (Unit grades & instant recap)
        # 2. LOCAL_MATCHUP_PRIMER (Tactical preview & micro battles)
        # 3. LOCAL_PLAYER_SPOTLIGHT (Film breakdown & player profile)
        # 4. LOCAL_SEASON_PROGRESS_REPORT (Midseason/season franchise audit)
        local_article_types_pool = [
            "LOCAL_POSTGAME_REPORT_CARD",
            "LOCAL_MATCHUP_PRIMER",
            "LOCAL_PLAYER_SPOTLIGHT",
            "LOCAL_SEASON_PROGRESS_REPORT" if week >= 6 else "LOCAL_POSTGAME_REPORT_CARD",
        ]
        article_tasks = []
        for idx, b in enumerate(local_beats):
            assigned_local_type = local_article_types_pool[idx % len(local_article_types_pool)]
            local_slot = f"Local Beat {idx + 1}"
            p = self.compile_agent_prompt(
                b[0], b[1], b[2], b[3], b[4], b[5],
                brief, season, week, "ARTICLE", article_type=assigned_local_type
            )
            article_tasks.append((p, team_id, assigned_local_type, b[4] or b[0], local_slot, assigned_local_type))

        # Helper executor for articles
        def _run_article_job(task):
            prompt, t_id, art_type, writer_name, slot_name, at_id = task
            print(f"🚀 [LOCAL WORKER] Starting [{slot_name}]: {art_type} by {writer_name}")
            res = self._execute_agent(
                prompt, season, week, t_id, live=live, model=model, save_to_db=save_to_db, slot=slot_name, article_type=at_id
            )
            print(f"✅ [LOCAL WORKER] Completed [{slot_name}]: {art_type} by {writer_name}")
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

        # Compile and execute Tweet tasks
        tweet_tasks = []
        for h in local_homers:
            p = self.compile_agent_prompt(h[0], h[1], h[2], h[3], h[4], h[5], brief, season, week, "TWEET")
            tweet_tasks.append((p, team_id, h[4] or h[0]))

        for pundit in pundits:
            p = self.compile_agent_prompt(pundit[0], pundit[1], pundit[2], pundit[3], pundit[4], pundit[5], brief, season, week, "TWEET")
            tweet_tasks.append((p, team_id, pundit[4] or pundit[0]))

        if max_workers > 1 and len(tweet_tasks) > 1:
            print(f"\n⚡ Executing {len(tweet_tasks)} Local Reactions & Pundit Tweets in Parallel...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(self._execute_agent, t[0], season, week, t[1], live, model, save_to_db)
                    for t in tweet_tasks
                ]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        print(f"❌ Local Tweet generation exception: {exc}")
        else:
            for t in tweet_tasks:
                self._execute_agent(t[0], season, week, t[1], live=live, model=model, save_to_db=save_to_db)
