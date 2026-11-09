"""Agent Wire social desk: plans each week's posts (docs/done/SOCIAL_MATRIX.md §2–§3).

Build step 3 = planning only. `plan_social_week(ctx)` turns one week's data into 80-130 post assignments sized by
`week_mix` (account + kind + subject + facts packet + focus, pick games, subjects, topic); nothing is written and no model is called. Generation,
engagement, posting times, the reply pass and trends are build steps 4–5.

A publication week N is written after Week N-1's games are final and before Week N is played, so:
  - "recap" games = Week N-1 (reactions, performances, beat notes, memes),
  - "preview" games = Week N (previews),
  - standings and season totals count only games before Week N.
Facts packets carry nothing from Week N onward, so no post can know how this week's games end.

Dry run (prints the plan):
  python3 -m newsroom.social_desk --season 2035 --week 18
  python3 -m newsroom.social_desk --season 2035 --week 18 --json plan.json
"""
import argparse
import collections
import functools
import json
import os
import random
import re
import zlib

from engines.agent_loops.player import LEAK_MAX_PER_WEEK, statement_text
from engines.game_script_engine import script_summary
from newsroom.fantasy_desk import fantasy_phase
from newsroom.roster_moves import TRADE_DEADLINE_WEEK
from newsroom.season_landscape import REGULAR_SEASON_FINAL_WEEK, division_standings

# The week's size and mix come from week_mix (AGENT_WIRE_VOICE_PLAN §2.4, JR's D4): a total of 80-130 posts that grows
# with the news, split by `mix_weights` in newsroom/config/social_desk.json scaled by the week's signals, with seeded
# jitter and per-kind floors and ceilings. Reactions (one per team that played), beat notes, shares and the news
# kinds are event-driven and count inside the total.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "social_desk.json")
# Kinds week_mix sizes from weights; everything else is event-driven.
MIX_KINDS = ("PERFORMANCE", "BAD_GAME", "COMPARISON", "TEAM_STORY", "STANDINGS", "PREVIEW", "MEME", "PLAYER", "REPLY",
             "PICKS", "HOT_TAKE", "AWARD_RACE", "POWER_TAKE", "QUESTION", "SEASON_CALL", "LIVE_MOMENT",
             "QUARTER_UPDATE", "TRADE_IDEA", "DRAFT_POSITION")
EVENT_KINDS = ("SHARE", "REACTION", "BEAT", "TRADE_NEWS", "TRADE_RUMOR", "MOVE_NEWS", "CAP_TAKE", "TRADE_REACTION",
               "MOVE_REACTION", "INJURY_SHOCK", "DRAFT_BOARD", "PLAYER_STATEMENT")
# Season calls (graded at season's end): the preseason (Week 1 publication), midseason and trade-deadline weeks.
SEASON_CALL_WEEKS = {1: "preseason", 9: "midseason", TRADE_DEADLINE_WEEK: "trade deadline"}
SEASON_CALL_TYPES = ("MVP", "SUPER_BOWL", "OPOY", "DPOY", "ROY", "PLAYOFF_TEAM", "COACH_FIRED")
PICK_GAMES_MAX = 4            # games on one writer's pick card
ONE_SCORE_MARGIN = 8

# Weekly post caps per account role (least-used accounts are picked first).
ROLE_CAPS = {"pundit": 3, "fantasy": 4, "beat": 2, "fan": 2, "bandwagon": 3, "meme": 3, "draft": 3, "former": 3,
             "aggregator": 4, "stats": 4}
# National accounts added by the voice plan (D7): a label-first aggregator and a stats account.
AGGREGATOR_ARCHETYPE = "AGGREGATOR"
STATS_ARCHETYPE = "STATS_DESK"

# Ex-player pundits keep their national voice but lean toward their old team (agreed with JR on Oct 14).
# FORMER_PLAYER accounts get the same slant from their agent_memories.assigned_team_id.
FORMER_TEAM_SLANT = {"OCHOCINCO": "CIN", "SHANNON": "DEN", "JJ_WATT": "HOU", "RG3": "WAS", "CLARK": "PIT", "MCAFEE": "IND"}
BAD_GAME_SHARE = 0.65   # a flop is at most this share of his earlier average
OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
# Defensive and kicking performances (captured games only): lines scoring DEF_PERFORMANCE_MIN+ on defense_play_score
# take the 2nd, 5th, 8th ... ordinary (non-buzz) performance post, at most DEF_PERFORMANCE_MAX a week, inside the
# same count.
DEF_PERFORMANCE_MIN = 12
DEF_PERFORMANCE_EVERY = 3
DEF_PERFORMANCE_MAX = 3
BAD_QB_INTERCEPTIONS = 2  # a QB with this many picks in a loss had a bad game (no QB threw 3 in a game in 2035)

# "The league is talking" (agreed with JR on Oct 14; frequencies checked against the 2035 season). A tier-1 game
# ("big") gets three posts instead of one, a tier-2 game ("historic") four plus a where-does-it-rank comparison.
# Each entry: stat -> (words, big, historic); per-position overrides by position.
SINGLE_GAME_BUZZ = {
    "pass_yards": ("passing yards", 400, None),
    "pass_tds": ("passing TDs", 5, 6),
    "rush_yards": ("rushing yards", 150, 200),
    "rec_yards": ("receiving yards", 150, 200),
    "total_tds": ("total TDs", 3, 4),
    "fantasy_points": ("full-PPR points", 40, 50),
}
SINGLE_GAME_BUZZ_BY_POSITION = {"TE": {"rec_yards": ("receiving yards", 90, 150)}}
# Round numbers crossed this week (regular season): (mark, makes news on its own). Common ones (a dozen QBs reach
# 4,000 yards) are only mentioned when the game is already news.
SEASON_MILESTONES = {
    "pass_yards": ("passing yards", ((4000, False), (5000, True))),
    "pass_tds": ("passing TDs", ((40, False), (50, True))),
    "rush_yards": ("rushing yards", ((1000, False), (1500, True), (2000, True))),
    "rec_yards": ("receiving yards", ((1000, False), (1500, True))),
    "total_tds": ("total TDs", ((15, False), (20, True))),
}
# Record chases (on pace for, or already past, a record) make news on their own only in the stretch run;
# earlier they are context for a game that is already news. Breaking a record is always historic.
RECORD_CHASE_FROM_WEEK = 12
# NFL single-season records as of the 2025 season. Fallback only: when the record book is in the database
# (engines/records_engine.py, PROJECT_SPEC §2.9) the desk reads the records from it, including the league's own
# 2026–2034 seasons and each franchise's records.
SEASON_RECORDS = {
    "pass_yards": ("passing yards", 5477, "Peyton Manning, 2013"),
    "pass_tds": ("passing TDs", 55, "Peyton Manning, 2013"),
    "rush_yards": ("rushing yards", 2105, "Eric Dickerson, 1984"),
    "rush_tds": ("rushing TDs", 28, "LaDainian Tomlinson, 2006"),
    "receptions": ("receptions", 149, "Michael Thomas, 2019"),
    "rec_yards": ("receiving yards", 1964, "Calvin Johnson, 2012"),
    "rec_tds": ("receiving TDs", 23, "Randy Moss, 2007"),
    "total_tds": ("total TDs", 31, "LaDainian Tomlinson, 2006"),
}
TEAM_POINTS_RECORD = (606, "2013 Denver Broncos")
# Record-book categories behind the desk's stats (season totals use season_*, single games game_*).
RECORD_STATS = {
    "pass_yards": "passing yards", "pass_tds": "passing TDs", "rush_yards": "rushing yards", "rush_tds": "rushing TDs",
    "receptions": "receptions", "rec_yards": "receiving yards", "rec_tds": "receiving TDs", "total_tds": "total TDs",
}
RECORD_CATEGORY = {"total_tds": "scrimmage_tds"}
GAME_RECORD_STATS = ("pass_yards", "pass_tds", "rush_yards", "rec_yards", "receptions", "total_tds", "rush_tds", "rec_tds")
GAMES_PER_SEASON = 17
PACE_MIN_GAMES = 6            # season paces count from this many games
STREAK_MIN = 6                # winning or losing streaks the league notices
RUN_MIN_GAMES = 5             # unbeaten / winless from this many games
HISTORIC_RUN_GAMES = 10       # ...historic from this many
BLOWOUT_MARGIN = 35
BIG_SCORE = 50
UPSET_GAP = 0.35              # winning-percentage gap before the game, both teams with RUN_MIN_GAMES games
TEAM_STORY_MAX = 8            # team storyline posts per week
# Posts per buzz tier, by role, in order. Player games take these from the performance bucket.
PLAYER_BUZZ_ROLES = {1: ("pundit", "former", "bandwagon"), 2: ("pundit", "former", "bandwagon", "fantasy")}
TEAM_STORY_ROLES = {
    (1, True): ("pundit", "bandwagon"), (2, True): ("pundit", "former", "bandwagon", "pundit"),
    (1, False): ("meme", "pundit"), (2, False): ("pundit", "meme", "bandwagon", "former"),
}
# Angles by (role, good news?); a second post by the same role takes the next angle.
BUZZ_ANGLES = {
    ("pundit", True): (
        "legacy claim: whether this is the best player in the league right now or an all-time peak at the position",
        "where this ranks around the league this season",
        "the skeptic's case: what the numbers hide and whether it holds up in January",
        "the eye test: why the tape and swagger show he is unstoppable",
    ),
    ("pundit", False): (
        "direct player accountability: calling out the disappearing act when it mattered",
        "what went wrong schematically and who is to blame",
        "whether this is a temporary slump or an exposed flaw",
    ),
    ("former", True): (
        "how it looks to someone who played: respect for the craft and physical execution",
        "declaring this player the undisputed best at his position in football right now",
        "legacy perspective: comparing this level of play to past greats you played with or against",
    ),
    ("former", False): (
        "calling out a lack of urgency, physical toughness, or business decisions",
        "how it feels from the inside when your execution lets the squad down",
    ),
    ("bandwagon", True): (
        "declaring this player the undisputed best player alive right now",
        "pure hype and aura: claiming this is an all-time peak",
    ),
    ("bandwagon", False): (
        "clowning the performance and labeling them a fraud",
        "declaring they have zero clutch gene when it matters",
    ),
    ("fantasy", True): ("what it means for fantasy managers",),
    ("meme", False): ("roast the performance with savage humor",),
    ("meme", True): ("make a meme about how absurd and unfair this performance was",),
}

# Roster moves (team management step 5, docs/done/team_management/05_NEWSROOM_INTEGRATION_PLAN.md §2.8): insiders break
# the most notable moves of the week, the team's beat reporter files on them in place of a plain note, die-hards react
# to the biggest, and one pundit takes a cap squeeze.
INSIDER_ARCHETYPES = ("SCHEFTER_TWEET", "RAPOPORT")
MOVE_NEWS_MAX = 3
MOVE_REACTION_MAX = 2
CAP_TAKE_DEAD_MONEY = 10_000_000   # a release leaving this much dead money (or a team over the cap) earns a cap take
# Trades and rumors (Phase 8): insiders break last week's executed trades (TRADE_NEWS) and at most two rumors
# (TRADE_RUMOR, through the trade deadline only); a die-hard of each team reacts to each trade (TRADE_REACTION); a team
# in a trade gets BEAT_MOVE. Trades come before rumors and moves for the insiders' pundit cap.
TRADE_NEWS_MAX = 3
# Draft board (step 5 Phase 9, JR 2026-10-20): @MockDraftMarty posts the week's biggest consensus-board mover, one a
# week in the regular season and two (top riser and top faller) from the postseason on.
DRAFT_BOARD_POSTS = {"regular": 1, "late": 2}
TRADE_RUMOR_MAX = 2

# Hot-take voices preferred for calling out bad games.
HOT_TAKE_PUNDITS = ("STEPHEN_A", "SKIP_TWEET", "COWHERD", "SHANNON", "MCAFEE")

# Archetypes that never post as a random pundit. Mirrors swarm_dispatcher.TWEET_EXCLUDED_ARCHETYPES (the
# long-form-only writers, SKIP_BAYLESS, the roundtable panel), plus roles the desk assigns on purpose.
PUNDIT_EXCLUDED = {
    "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "KIMES", "ORLOVSKY",
    "SKIP_BAYLESS", "ROUNDTABLE_PANEL", "MEL_KIPER",
    "LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN",
    "FANTASY_GURU", "BERRY", "YATES", "NFL_MEMES", "DRAFT_SICKO", "FORMER_PLAYER", "AGGREGATOR", "STATS_DESK",
}
FANTASY_ARCHETYPES = ("FANTASY_GURU", "BERRY", "YATES")

# Pundits who fit a subject position best; used as a preference, never a requirement.
PUNDIT_AFFINITY = {
    "QB": ("RG3", "COWHERD", "STEPHEN_A", "SKIP_TWEET", "HERBSTREIT", "MCAFEE"),
    "WR": ("OCHOCINCO", "SHANNON", "MCAFEE", "SIMMONS"),
    "TE": ("SHANNON", "MCAFEE", "OCHOCINCO"),
    "HB": ("CLARK", "JJ_WATT", "EISEN", "WINGO"),
    "RB": ("CLARK", "JJ_WATT", "EISEN", "WINGO"),
    "DEF": ("JJ_WATT", "SHANNON", "CLARK", "STEPHEN_A"),
    "K": ("MCAFEE", "EISEN", "WINGO"),
}

# Articles shared from a different account than their byline: Skip's column byline shares from his tweet
# account (both are @RealSkipBayless). The roundtable panel never posts.
SHARE_ACCOUNT_ARCHETYPE = {"SKIP_BAYLESS": "SKIP_TWEET"}
NO_SHARE_ARCHETYPES = {"ROUNDTABLE_PANEL"}

# Posting windows (simulated time is assigned in build step 5).
WINDOWS = {
    "REACTION": "after its game ends",
    "PERFORMANCE": "Sun night – Mon",
    "BAD_GAME": "Sun night – Tue",
    "COMPARISON": "Tue – Thu",
    "BEAT": "Mon – Wed",
    "STANDINGS": "Tue",
    "SHARE": "when the slate publishes (Tue – Wed)",
    "REPLY": "minutes to hours after its parent",
    "PREVIEW": "Thu – Sat, before kickoff",
    "MEME": "any time",
    "PLAYER": "Mon – Sat",
    "TEAM_STORY": "Mon – Wed",
    "MOVE_NEWS": "Mon – Wed",
    "BEAT_MOVE": "Mon – Wed",
    "MOVE_REACTION": "Mon – Wed",
    "CAP_TAKE": "Mon – Wed",
    "TRADE_NEWS": "Mon – Wed",
    "TRADE_REACTION": "Mon – Wed",
    "TRADE_RUMOR": "Mon – Wed",
    "PLAYER_STATEMENT": "Mon – Wed",
    "DRAFT_BOARD": "Tue – Thu",
    "PICKS": "Wed – Sat, before kickoff",
    "HOT_TAKE": "Mon – Thu",
    "AWARD_RACE": "Tue – Thu",
    "POWER_TAKE": "Tue – Wed",
    "QUESTION": "any time",
    "SEASON_CALL": "Wed – Sat, before kickoff",
    "LIVE_MOMENT": "during the game, right after the play",
    "QUARTER_UPDATE": "during the game, at the end of the quarter",
    "TRADE_IDEA": "Mon – Wed",
    "DRAFT_POSITION": "after its game ends – Mon",
}


# ---------------------------------------------------------------------------------------------- loading ---
def load_week_context(cur, season: int, week: int) -> dict:
    """Everything the planner reads for publication week `week`, from the database (one cursor)."""
    recap_week = week - 1
    ctx = {"season": season, "week": week, "recap_week": recap_week}

    cur.execute("""
        SELECT t.id, t.name, t.city, t.abbreviation, t.conference, t.division, COALESCE(ts.popularity_index, 0) AS popularity
        FROM teams t LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
    """, (season,))
    ctx["teams"] = {r[0]: {"id": r[0], "name": r[1], "city": r[2], "abbr": r[3], "conference": r[4],
                           "division": r[5], "popularity": float(r[6] or 0)} for r in cur.fetchall()}

    cur.execute("""
        SELECT agent_id, archetype, assigned_team_id, handle, display_name, persistent_memory
        FROM agent_memories WHERE handle IS NOT NULL ORDER BY agent_id
    """)
    ctx["accounts"] = [dict(zip(("agent_id", "archetype", "team_id", "handle", "display_name", "persistent_memory"), r))
                       for r in cur.fetchall()]

    # Head coaches in their seat this season (season calls may name one; missing tables mean none).
    cur.execute("SAVEPOINT agent_wire_coaches")
    try:
        cur.execute("""SELECT c.id, c.coach_name, t.team_id FROM coaches c
                       JOIN coach_tenures t ON t.coach_id = c.id
                       WHERE t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)
                       ORDER BY c.id""", (season, season))
        ctx["coaches"] = [{"id": r[0], "name": r[1], "team_id": r[2]} for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT agent_wire_coaches")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT agent_wire_coaches")
        ctx["coaches"] = []

    game_cols = ("id", "week", "home_team_id", "away_team_id", "home_score", "away_score", "status",
                 "game_time", "game_type", "excitement_index", "projected_excitement")
    cur.execute(f"""
        SELECT {', '.join(game_cols)} FROM games
        WHERE season = %s AND week <= %s AND status <> 'BYE' ORDER BY week, id
    """, (season, week))
    games = [dict(zip(game_cols, r)) for r in cur.fetchall()]
    for g in games:
        for k in ("excitement_index", "projected_excitement"):
            g[k] = float(g[k]) if g[k] is not None else None
    ctx["games"] = [g for g in games if g["week"] < week and g["status"] == "COMPLETED"]
    ctx["recap_games"] = [g for g in ctx["games"] if g["week"] == recap_week]
    ctx["preview_games"] = [g for g in games if g["week"] == week]

    ctx["player_lines"] = fetch_player_lines(cur, season, week)

    # Defensive and kicking lines from the recap week's captured box scores (game_stat_lines). Only captured games have
    # them (Week 17 of 2035 on); a game without any contributes none.
    def_cols = ("def_tackles", "def_sacks", "def_tfl", "def_ints", "def_deflections", "def_forced_fumbles",
                "def_fumble_recoveries", "def_tds", "def_safeties", "kick_fgm", "kick_fga", "kick_fg_long",
                "kick_fgm_50", "kick_fga_50", "kick_xpm", "kick_xpa")
    cur.execute(f"""
        SELECT l.week, l.game_id, l.team_id, COALESCE(pse.position, ''), l.player_name,
               COALESCE(l.full_name, l.player_name), l.player_id, COALESCE(pse.popularity_index, 0),
               {', '.join('l.' + c for c in def_cols)}
        FROM game_stat_lines l
        JOIN games g ON g.id = l.game_id
        LEFT JOIN player_seasons pse ON pse.player_id = l.player_id AND pse.season = g.season
        WHERE g.season = %s AND g.week = %s AND g.status = 'COMPLETED' AND l.team_id IS NOT NULL
          AND (l.def_tackles IS NOT NULL OR l.kick_fga IS NOT NULL)
    """, (season, recap_week))
    ctx["defense_lines"] = []
    for r in cur.fetchall():
        d = dict(zip(("week", "game_id", "team_id", "position", "short_name", "name", "player_id", "popularity") + def_cols, r))
        d["popularity"] = float(d["popularity"] or 0)
        d["name"] = tidy_name(d["name"])
        for c in def_cols:
            d[c] = float(d[c]) if d[c] is not None else None
        ctx["defense_lines"].append(d)

    cur.execute("""
        SELECT id, persona, team_id, headline, excerpt, slot, article_type
        FROM news_feed WHERE season = %s AND week = %s AND content_type = 'ARTICLE' ORDER BY id
    """, (season, week))
    ctx["articles"] = [dict(zip(("id", "persona", "team_id", "headline", "excerpt", "slot", "article_type"), r))
                       for r in cur.fetchall()]

    cur.execute("""
        SELECT player_name, team_id, position, projected_points, matchup_stars, pos_rank
        FROM fantasy_projections WHERE season = %s AND week = %s ORDER BY projected_points DESC NULLS LAST
    """, (season, week))
    ctx["projections"] = [dict(zip(("name", "team_id", "position", "projected_points", "matchup_stars", "pos_rank"), r))
                          for r in cur.fetchall()]
    for p in ctx["projections"]:
        p["projected_points"] = float(p["projected_points"] or 0)
    ctx["record_book"] = load_record_book(cur, season)

    # Weekly ground-truth injuries for publication week
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
            WHERE season = %s AND (released_week IS NULL OR released_week > %s)
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
            (rr.pos_rank = 1 AND COALESCE(ps.position, i.position) = 'QB') as is_qb_star,
            (
                (COALESCE(ps.overall_rating, i.ovr) >= 88)
                OR (COALESCE(ps.popularity_index, 0.0) >= 50.0)
                OR (rr.pos_rank = 1 AND COALESCE(ps.position, i.position) = 'QB')
            ) as is_key_injury,
            (
                (COALESCE(ps.position, i.position) IN ('QB', 'TE', 'LT', 'LG', 'C', 'RG', 'RT', 'FS', 'SS', 'K', 'P', 'FB') AND rr.pos_rank = 1)
                OR (COALESCE(ps.position, i.position) IN ('HB', 'DT', 'LEDG', 'REDG', 'LE', 'RE') AND rr.pos_rank <= 2)
                OR (COALESCE(ps.position, i.position) IN ('WR', 'CB', 'MIKE', 'SAM', 'WILL', 'MLB', 'LOLB', 'ROLB') AND rr.pos_rank <= 3)
            ) as is_starter
        FROM injuries i
        JOIN teams t ON t.id = i.team_id
        LEFT JOIN players p ON p.id = i.player_id
        LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
        LEFT JOIN ranked_roster rr ON rr.player_id = i.player_id
        WHERE i.season = %s AND i.week = %s AND (ps.released_week IS NULL OR ps.released_week > %s)
        ORDER BY COALESCE(ps.overall_rating, i.ovr) DESC;
    """, (season, week, season, q_week, week))  # released players (roster_moves.on_roster) leave the report
    injs = []
    injs_by_team = collections.defaultdict(list)
    for tid, pname, pos, ovr, pop, inj_type, dur, w_rem, is_ir, is_qb_star, is_key, is_star in cur.fetchall():
        item = {
            "team_id": tid,
            "player_name": tidy_name(pname),
            "position": pos,
            "overall_rating": int(ovr or 0),
            "popularity": round(float(pop or 0.0), 1),
            "injury_type": inj_type,
            "duration": dur,
            "weeks_remaining": int(w_rem or 0),
            "is_ir": bool(is_ir),
            "is_starting_qb": bool(is_qb_star),
            "is_key_injury": bool(is_key),
            "is_starter": bool(is_star),
        }
        injs.append(item)
        injs_by_team[tid].append(item)
    ctx["injuries"] = injs
    ctx["injuries_by_team"] = injs_by_team
    ctx["moves"], ctx["team_caps"] = load_moves(cur, season, week)
    ctx["trades"], ctx["rumors"] = load_trades(cur, season, week)
    ctx["player_statements"] = load_player_statements(cur, season, week)
    ctx["draft_movers"] = load_draft_movers(cur, season, week)
    # Each recap game's canonical story (engines/game_script_engine.py, §2.14), read or built here
    from engines.game_script_engine import ensure_script
    ctx["game_scripts"] = {}
    for g in ctx["recap_games"]:
        script, _ = ensure_script(cur, g["id"])
        if script:
            ctx["game_scripts"][g["id"]] = script
    ctx["team_needs"] = load_team_needs(cur, season, week)
    ctx["pick_history"] = load_pick_history(cur, season, week)
    ctx["team_strength"] = load_team_strength(cur, season)
    ctx["strong_relationships"] = load_strong_relationships(cur)
    ctx["league_topics"] = load_stored_topics(cur, season, week, ctx["teams"])
    return ctx


def fetch_player_lines(cur, season: int, week: int) -> list:
    """Offensive player lines from completed games before publication week `week`, matched to players on that team
    (player_stats names are abbreviated)."""
    cur.execute("""/* desk:player_lines */
        SELECT g.week, ps.game_id, ps.team_id, ps.position, ps.player_name,
               COALESCE(m.full_name, ps.player_name) AS full_name, m.player_id, COALESCE(m.popularity, 0) AS popularity,
               ps.completions, ps.pass_attempts, ps.pass_yards, ps.pass_tds, ps.interceptions,
               ps.rush_attempts, ps.rush_yards, ps.rush_tds, ps.receptions, ps.rec_yards, ps.rec_tds,
               ps.fantasy_points
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN LATERAL (
            SELECT p.id AS player_id, p.full_name, pse.popularity_index AS popularity
            FROM players p
            JOIN player_seasons pse ON pse.player_id = p.id AND pse.season = g.season AND pse.team_id = ps.team_id
            WHERE p.player_name = ps.player_name
            ORDER BY (pse.position = ps.position) DESC, p.id
            LIMIT 1
        ) m ON TRUE
        WHERE g.season = %s AND g.week < %s AND g.status = 'COMPLETED'
    """, (season, week))
    line_cols = ("week", "game_id", "team_id", "position", "short_name", "name", "player_id", "popularity",
                 "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions",
                 "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds", "fantasy_points")
    lines = []
    for r in cur.fetchall():
        d = dict(zip(line_cols, r))
        if d["week"] is None or d["week"] >= week:          # nothing from Week N onward
            continue
        d["fantasy_points"] = float(d["fantasy_points"] or 0)
        d["popularity"] = float(d["popularity"] or 0)
        d["name"] = tidy_name(d["name"])
        lines.append(d)
    return lines


def _savepoint(cur, name: str, fn, default):
    """Runs fn(); a missing table (or any query error) rolls back to a savepoint and returns `default`, so one
    absent source never breaks the week (as load_moves does)."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except Exception:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        return default
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return out


def load_team_needs(cur, season: int, week: int) -> dict:
    """{team_id: [position groups, biggest need first]} from gm_team_seasons (§2.16) when its needs were computed
    before Week `week`; {} otherwise or without the table."""
    def q():
        cur.execute("""/* desk:team_needs */
            SELECT team_id, needs, needs_week FROM gm_team_seasons WHERE season = %s AND needs_week < %s""",
                    (season, week))
        out = {}
        for tid, needs, needs_week in cur.fetchall():
            if needs_week is None or needs_week >= week:
                continue
            if isinstance(needs, str):
                needs = json.loads(needs)
            groups = [n["group"] for n in sorted(needs or [], key=lambda n: -(n.get("score") or 0)) if n.get("group")]
            if groups:
                out[tid] = groups[:3]
        return out
    return _savepoint(cur, "agent_wire_needs", q, {})


def load_pick_history(cur, season: int, week: int) -> dict:
    """{agent_id: PICKS posts this season before Week `week`}, so plan_picks rotates writers; {} without data."""
    def q():
        cur.execute("""/* desk:pick_history */
            SELECT agent_id, COUNT(*) FROM news_feed
            WHERE content_type = 'TWEET' AND post_kind = 'PICKS' AND season = %s AND week < %s AND agent_id IS NOT NULL
            GROUP BY agent_id""", (season, week))
        return dict(cur.fetchall())
    return _savepoint(cur, "agent_wire_picks", q, {})


def load_team_strength(cur, season: int) -> dict:
    """{team_id: team_ovr} for ordering contenders before any game is played. Ordering only: posts never see a
    rating."""
    def q():
        cur.execute("/* desk:team_strength */ SELECT team_id, team_ovr FROM team_seasons WHERE season = %s", (season,))
        return {t: float(o) for t, o in cur.fetchall() if o is not None}
    return _savepoint(cur, "agent_wire_strength", q, {})


STRONG_TRUST = 0.5      # |trust| at which a relationship becomes the post's angle (AGENT_WIRE_VOICE_PLAN §2.4)


def _relationship_line(target_type: str, name: str, trust: float) -> str:
    """One plain line for an assignment's angle (§2.4): a writer's strong favourite or grudge toward a subject."""
    if trust >= STRONG_TRUST:
        return {"player": f"You're a {name} believer and you back him every chance you get.",
                "coach": f"You rate {name} as one of the best in the game.",
                "team": f"You're all in on the {name}."}.get(target_type, f"You believe in {name}.")
    return {"player": f"You can't stand {name}.", "coach": f"You've lost faith in {name}.",
            "team": f"You can't stand the {name}."}.get(target_type, f"You don't rate {name}.")


def load_strong_relationships(cur) -> dict:
    """{(agent_id, target_type, target_id): sentence} for every writer relationship at |trust| >= STRONG_TRUST,
    so an assignment about that subject can carry the bias as its angle (§2.4). Missing table or person_key
    column -> {} (savepoint, as load_moves does)."""
    def q():
        cur.execute("""/* desk:relationships */
            SELECT m.agent_id, r.target_type, r.target_id, r.trust,
                   CASE r.target_type WHEN 'team' THEN t.name WHEN 'player' THEN COALESCE(p.full_name, p.player_name)
                                      WHEN 'coach' THEN c.coach_name END AS target_name
            FROM persona_relationships r
            JOIN agent_memories m ON COALESCE(m.person_key, m.agent_id) = r.persona_id
            LEFT JOIN teams t ON r.target_type = 'team' AND t.id::text = r.target_id
            LEFT JOIN players p ON r.target_type = 'player' AND p.id::text = r.target_id
            LEFT JOIN coaches c ON r.target_type = 'coach' AND c.id::text = r.target_id
            WHERE r.persona_type = 'writer' AND ABS(r.trust) >= %s""", (STRONG_TRUST,))
        out = {}
        for agent_id, ttype, tid, trust, name in cur.fetchall():
            if name and ttype in ("player", "coach", "team"):
                out[(agent_id, ttype, str(tid))] = _relationship_line(ttype, name, float(trust))
        return out
    return _savepoint(cur, "agent_wire_relationships", q, {})


def load_moves(cur, season: int, week: int) -> tuple:
    """(moves new for publication week `week`, {team_id: cap snapshot}) for the move posts. Moves are annotated with
    notability (newsroom/roster_moves.py) and carry their move line, the money strings in it, the player's new depth
    slot and his seasons with the team. Missing contract/transaction tables -> nothing."""
    from newsroom import roster_moves as rm
    from newsroom.db_tools import fetch_cap_rows, fetch_moves
    cur.execute("SAVEPOINT agent_wire_moves")
    try:
        rows = rm.annotate(rm.new_this_week(fetch_moves(cur, season, ctx=(season, week, True)), season, week))
        caps = fetch_cap_rows(cur, season)
        pids = [r["player_id"] for r in rows if r["player_id"]]
        cur.execute("""SELECT player_id, team_id, COUNT(*) FROM player_seasons
                       WHERE player_id = ANY(%s) AND season <= %s AND source <> 'SYNTH' GROUP BY 1, 2;""", (pids, season))
        tenure = {(p, t): n for p, t, n in cur.fetchall()}
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT agent_wire_moves")
        return [], {}
    moves = []
    for r in rows:
        if not r["player_id"] or r["type"] == "TRADED":       # trades post as whole trades (load_trades)
            continue
        tid = r["from_team_id"] if r["type"] == "RELEASED" else (r["to_team_id"] or r["from_team_id"])
        line = rm.move_line(r, with_notable=False)
        moves.append({
            "id": r["id"], "type": r["type"], "week": r["week"], "team_id": tid, "player_id": r["player_id"],
            "player_name": rm.display_name(r), "position": r["position"], "line": line,
            "money": re.findall(r"-?\$[\d.]+[MK]?", line), "notable": r["notable"],
            "notable_reasons": r["notable_reasons"], "rank_score": r["rank_score"], "depth_slot": r.get("depth_slot"),
            "dead_money": r.get("dead_money"), "seasons_with_team": tenure.get((r["player_id"], tid)),
        })
    team_caps = {c["team_id"]: {"line": rm.cap_line(c["team"], c, c["basis"]), "cap_space": c["cap_space"],
                                "dead_money": c["cap_penalty"], "basis": c["basis"]} for c in caps}
    return moves, team_caps


def load_draft_movers(cur, season: int, week: int) -> dict:
    """The consensus board's top risers and fallers visible to publication week `week` (engines/scouting_read,
    public fields only, never Madden ratings) as writer lines: {"draft_class", "risers", "fallers"}. Missing step 4
    tables or no visible board -> {}."""
    from engines.scouting_read import risers_fallers
    from newsroom.db_tools import draft_mover_line
    cur.execute("SAVEPOINT agent_wire_draft")
    try:
        rf = risers_fallers(cur, season, week=week, limit=2)
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT agent_wire_draft")
        return {}
    if not rf:
        return {}
    return {"draft_class": rf["draft_season"], "risers": [draft_mover_line(r) for r in rf["risers"]],
            "fallers": [draft_mover_line(r) for r in rf["fallers"]]}


def draft_board_subjects(movers: dict, n: int) -> list:
    """The n draft-board posts' subjects (pure): one post takes the bigger of the top riser and top faller; two take
    both. Each is {"direction", "mover"}."""
    top = [("riser", r) for r in (movers.get("risers") or [])[:1]] + \
          [("faller", r) for r in (movers.get("fallers") or [])[:1]]
    top.sort(key=lambda dm: -abs(dm[1].get("rank_change") or 0))
    return [{"direction": d, "mover": m} for d, m in top[:n]]


def load_trades(cur, season: int, week: int) -> tuple:
    """(executed trades, rumors) new for publication week `week` (swarm_dispatcher.new_week_trades: last week's
    executed GM trades, rumors from last week's rumor_ok proposals through the deadline), as facts for the trade
    posts: team ids, the trade or rumor line, the money strings in it and each GM's stated thinking. A leak
    (`source: LEAK`) with `insider_key` is fresh to that insider's pool in its first edition (`fresh: True`) and open
    to the other insiders from the following edition (`fresh: False`). Missing step 3 tables -> nothing."""
    from newsroom import roster_moves as rm
    from newsroom.db_tools import _team_names, new_week_trades
    cur.execute("SAVEPOINT agent_wire_trades")
    try:
        trades, rumors = new_week_trades(cur, season, week)
        older = []
        if week >= 3 and rm.rumor_visible(week - 2, week):   # leaks others may see the edition after their first
            _, earlier = new_week_trades(cur, season, week - 1)
            older = [r for r in earlier if r.get("source") == "LEAK" and r.get("insider_key")]
        seen = {r.get("rumor_id") for r in rumors}
        older = [r for r in older if r.get("rumor_id") not in seen]
        names = _team_names(cur)
        cur.execute("SELECT abbreviation, id FROM teams WHERE abbreviation IS NOT NULL;")
        ids = dict(cur.fetchall())
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT agent_wire_trades")
        return [], []
    out_t = []
    for t in trades:
        line = rm.trade_line(t, names)
        out_t.append({"id": t["proposal_id"], "week": t["week"], "teams": [ids[t["team_a"]], ids[t["team_b"]]],
                      "line": line, "money": re.findall(r"-?\$[\d.]+[MK]?", line),
                      "players": [l["player_name"] for l in t["legs"] if l.get("type") == "PLAYER"],
                      "gm_said": {ids[t["team_a"]]: t.get("rationale_a"), ids[t["team_b"]]: t.get("rationale_b")}})
    out_r = []
    for r in list(rumors) + older:
        leak = r.get("source") == "LEAK"
        row = {"id": r["rumor_id"], "week": r["week"], "teams": [ids[x] for x in r["teams"]],
               "line": rm.leak_line(r, names) if leak else rm.rumor_line(r, names),
               "outcome": r.get("outcome") or ("stalled" if r.get("stage") == "STALLED" else "open")}
        if leak:
            row.update({"source": "LEAK", "insider_key": r.get("insider_key"), "named": r.get("named") or [],
                        "fresh": bool(r.get("insider_key")) and r["week"] == week - 1})
        out_r.append(row)
    return out_t, out_r


def load_player_statements(cur, season: int, week: int) -> list:
    """The week's public player statements (COACH_PLAYER_AGENT_LOOPS_PLAN §2.5): public `LEAK` decisions from
    `engines.agent_loops.player` for the publication week's news window — the recap week (`week - 1`) and the
    publication week itself, plus the offseason tick 24 when the recap week is before Week 1. At most
    `LEAK_MAX_PER_WEEK`, ordered by (tick, subject_key). Each item carries only what the recorded row holds: the
    player's name, position, team, statement text, player_id and the row's season/tick (plus `offered`/`asked`
    when the row has them). A row with no model text uses the player loop's figure-free template, never invented
    words. `[]` when the table is missing."""
    cur.execute("SELECT to_regclass('agent_loop_decisions') IS NOT NULL")
    found = cur.fetchone()
    if not found or not found[0]:
        return []
    ticks = sorted({t for t in (week - 1, week) if t >= 1})
    if week - 1 < 1:
        ticks.append(24)                    # the offseason edition's tick
    cur.execute("""/* desk:player_statements */
        SELECT season, tick, subject_key, text, facts FROM agent_loop_decisions
        WHERE kind = 'LEAK' AND visibility = 'public' AND season = %s AND tick = ANY(%s)
        ORDER BY tick, subject_key
        LIMIT %s""", (season, ticks, LEAK_MAX_PER_WEEK))
    out = []
    for s, tick, _subject_key, text, facts in cur.fetchall()[:LEAK_MAX_PER_WEEK]:
        if isinstance(facts, str):
            facts = json.loads(facts)
        facts = facts or {}
        pid, name = facts.get("player_id"), facts.get("player")
        if pid is None or not name:
            continue                        # malformed row: no player to post about
        statement = (text or "").strip() or statement_text(facts)
        item = {"player_id": pid, "name": name, "position": facts.get("position"), "team": facts.get("team"),
                "statement": statement, "season": int(s), "tick": int(tick)}
        for k in ("offered", "asked"):
            if facts.get(k):
                item[k] = facts[k]
        out.append(item)
    return out[:LEAK_MAX_PER_WEEK]


def load_record_book(cur, season: int):
    """The record book as it stood before `season` (engines/records_engine.py): franchise records by (team, category)
    and all-time entries by category, each [(value, holder, season)]. None when the record tables don't exist yet."""
    cur.execute("SELECT to_regclass('franchise_records') IS NOT NULL AND to_regclass('league_record_entries') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    book = {"franchise": collections.defaultdict(list), "league": collections.defaultdict(list), "full": set()}
    # Only categories with the real NFL history behind them (coverage FULL) have league-wide records and top 10s;
    # the record book's own categories (BOOK) hold just what it lists, so they give franchise records only.
    cur.execute("SELECT category FROM record_categories WHERE coverage = 'FULL'")
    book["full"] = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT team_id, category, value, holder, season FROM franchise_records WHERE season < %s", (season,))
    for team_id, cat, value, holder, s in cur.fetchall():
        book["franchise"][(team_id, cat)].append((float(value), holder, s))
        book["league"][cat].append((float(value), holder, s))     # a franchise record is also a league entry
    cur.execute("SELECT category, value, holder, season FROM league_record_entries WHERE season < %s", (season,))
    for cat, value, holder, s in cur.fetchall():
        if (float(value), holder, s) not in book["league"][cat]:
            book["league"][cat].append((float(value), holder, s))
    return book


# ------------------------------------------------------------------------------------------------ facts ---
def _display_excitement(raw):
    # Same mapping as engines.gotw_engine.display_excitement (kept local so the planner imports no DB driver).
    if raw is None:
        return None
    return round(min(100.0, max(70.0, 70.0 + 30.0 * (float(raw) - 40.0) / 60.0)), 1)


def excitement_label(display):
    """Plain-words description only: posts must never cite the index itself."""
    if display is None:
        return None
    if display >= 90:
        return "a classic"
    if display >= 82:
        return "an entertaining game"
    if display >= 76:
        return "a typical game"
    return "a dull game"


def stat_line(l: dict) -> str:
    parts = []
    if l["pass_attempts"]:
        parts.append(f"{l['completions']}/{l['pass_attempts']}, {l['pass_yards']} pass yds, {l['pass_tds']} TD"
                     + (f", {l['interceptions']} INT" if l["interceptions"] else ""))
    if l["rush_attempts"] or l["rush_yards"]:
        carries = f"{l['rush_attempts']} {'rush' if l['rush_attempts'] == 1 else 'rushes'}, " if l["rush_attempts"] else ""
        yds = "yd" if abs(l["rush_yards"]) == 1 else "yds"
        parts.append(f"{carries}{l['rush_yards']} rush {yds}" + (f", {l['rush_tds']} rush TD" if l["rush_tds"] else ""))
    if l["receptions"] or l["rec_yards"]:
        parts.append(f"{l['receptions']} rec, {l['rec_yards']} rec yds" + (f", {l['rec_tds']} rec TD" if l["rec_tds"] else ""))
    return "; ".join(parts) or "no recorded stats"


def _n(v):
    """A stat for prose: whole numbers without .0 (sacks come in halves)."""
    v = float(v or 0)
    return int(v) if v.is_integer() else v


def defense_stat_line(l: dict) -> str:
    """A captured defensive or kicking game line, e.g. '6 tkl, 2.5 sacks, 1 FF' or '4/5 FG (long 56), 3/3 XP';
    zero categories are left out."""
    parts = []
    if l.get("def_tackles") is not None:
        d = [f"{_n(l['def_tackles'])} tkl"]
        for k, label in (("def_sacks", "sacks"), ("def_tfl", "TFL"), ("def_ints", "INT"), ("def_deflections", "PD"),
                         ("def_forced_fumbles", "FF"), ("def_fumble_recoveries", "FR"), ("def_tds", "def TD"),
                         ("def_safeties", "safety")):
            if l.get(k):
                d.append(f"{_n(l[k])} {'sack' if label == 'sacks' and l[k] == 1 else label}")
        parts.append(", ".join(d))
    if l.get("kick_fga"):
        kick = f"{_n(l['kick_fgm'])}/{_n(l['kick_fga'])} FG" + (f" (long {_n(l['kick_fg_long'])})" if l.get("kick_fg_long") else "")
        if l.get("kick_fga_50"):
            kick += f", {_n(l['kick_fgm_50'])}/{_n(l['kick_fga_50'])} from 50+"
        if l.get("kick_xpa"):
            kick += f", {_n(l['kick_xpm'])}/{_n(l['kick_xpa'])} XP"
        parts.append(kick)
    return "; ".join(parts) or "no recorded stats"


def defense_play_score(l: dict) -> float:
    """How big a captured defensive or kicking game was, or 0 if it isn't a performance worth a post. A big day is
    DEF_PERFORMANCE_MIN points: 2 sacks, an interception plus a forced fumble, a defensive TD, two forced fumbles,
    4 field goals, a 55+ yard field goal or two from 50+."""
    d = (6 * (l.get("def_sacks") or 0) + 8 * (l.get("def_ints") or 0) + 4 * (l.get("def_forced_fumbles") or 0)
         + 3 * (l.get("def_fumble_recoveries") or 0) + 12 * (l.get("def_tds") or 0) + 6 * (l.get("def_safeties") or 0))
    k = (3 * (l.get("kick_fgm") or 0) + 3 * (l.get("kick_fgm_50") or 0)
         + (12 if (l.get("kick_fg_long") or 0) >= 55 else 0))
    score = max(d, k)
    return score if score >= DEF_PERFORMANCE_MIN else 0.0


def norm_pos(pos):
    p = (pos or "").upper()
    return "RB" if p in ("HB", "FB", "RB") else p


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _stat(l, k):
    if k == "total_tds":
        return (l.get("rush_tds") or 0) + (l.get("rec_tds") or 0)
    return l.get(k) or 0


def tidy_name(name):
    """Some imported rosters store names in capitals ("JERRY RAYFORD"); post them in normal case."""
    if not name or not name.isupper():
        return name
    def word(w):
        if w.rstrip(".") in ("II", "III", "IV", "V"):
            return w
        return "-".join("'".join(x[:1].upper() + x[1:].lower() for x in part.split("'")) for part in w.split("-"))
    return " ".join(word(w) for w in name.split())


def former_position(account) -> str:
    """An ex-player's position, from his identity line ("I played WR for the ...")."""
    m = re.search(r"I played (\w+) for", account.get("persistent_memory") or "")
    return norm_pos(m.group(1)) if m else ""


class Week:
    """Derived views of a context: records, standings, leaders and season totals."""

    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.season, self.week, self.recap_week = ctx["season"], ctx["week"], ctx["recap_week"]
        self.teams = ctx["teams"]
        self.records = self._records()
        self.lines_by_game = collections.defaultdict(list)
        for l in ctx["player_lines"]:
            if l["week"] == self.recap_week:
                self.lines_by_game[l["game_id"]].append(l)
        self.recap_lines = [l for l in ctx["player_lines"] if l["week"] == self.recap_week]
        recap_ids = {g["id"] for g in ctx["recap_games"]}
        self.defense_lines = [l for l in ctx.get("defense_lines", ()) if l["game_id"] in recap_ids]
        self.season_totals = self._season_totals()
        self.fantasy_active = fantasy_phase(self.week) is not None

    # -- records and standings (regular season only, before this publication week)
    def _records(self):
        last = min(self.week - 1, REGULAR_SEASON_FINAL_WEEK)
        rec = {tid: {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0} for tid in self.teams}
        for g in self.ctx["games"]:
            if g["week"] > last or g["home_score"] is None:
                continue
            for team, pf, pa in ((g["home_team_id"], g["home_score"], g["away_score"]),
                                 (g["away_team_id"], g["away_score"], g["home_score"])):
                r = rec.setdefault(team, {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
                r["pf"] += pf
                r["pa"] += pa
                r["w" if pf > pa else "l" if pf < pa else "t"] += 1
        return rec

    def record(self, tid) -> str:
        r = self.records.get(tid, {"w": 0, "l": 0, "t": 0})
        return f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else "")

    def win_pct(self, tid) -> float:
        r = self.records.get(tid, {"w": 0, "l": 0, "t": 0})
        n = r["w"] + r["l"] + r["t"]
        return (r["w"] + 0.5 * r["t"]) / n if n else 0.0

    def diff(self, tid) -> int:
        r = self.records.get(tid, {"pf": 0, "pa": 0})
        return r["pf"] - r["pa"]

    def standings_lines(self):
        games = [{k: g[k] for k in ("week", "home_team_id", "away_team_id", "home_score", "away_score")}
                 for g in self.ctx["games"]]
        teams = [{k: t[k] for k in ("id", "name", "city", "conference", "division")} for t in self.teams.values()]
        return division_standings(teams, games, before_week=self.week)

    def conference_order(self, conf):
        tids = [t for t, v in self.teams.items() if v["conference"] == conf]
        return sorted(tids, key=lambda t: (self.win_pct(t), self.diff(t)), reverse=True)

    def team_label(self, tid):
        t = self.teams[tid]
        return f"{t['city']} {t['name']}"

    def team_with_record(self, tid):
        return f"{self.team_label(tid)} ({self.record(tid)})"

    # -- games
    def game_label(self, g):
        a, h = self.teams[g["away_team_id"]]["name"], self.teams[g["home_team_id"]]["name"]
        # Only games before this publication week show a score: replanning an old week must not leak results.
        if g["status"] == "COMPLETED" and g["week"] < self.week:
            return f"{a} {g['away_score']} @ {h} {g['home_score']}"
        return f"{a} @ {h}"

    def winner_loser(self, g):
        if g["home_score"] == g["away_score"]:
            return None, None
        if g["home_score"] > g["away_score"]:
            return g["home_team_id"], g["away_team_id"]
        return g["away_team_id"], g["home_team_id"]

    def leaders(self, g, team_id, n=2):
        rows = sorted((l for l in self.lines_by_game[g["id"]] if l["team_id"] == team_id),
                      key=lambda l: l["fantasy_points"], reverse=True)[:n]
        return [f"{l['name']} ({l['position']}): {stat_line(l)}" for l in rows]

    def game_facts(self, g, author_team=None):
        w, l = self.winner_loser(g)
        facts = {
            "game": self.game_label(g),
            "week": g["week"],
            "game_type": g["game_type"],
            "kickoff_slot": g["game_time"],
            "result": (f"{self.team_label(w)} beat the {self.team_label(l)} "
                       f"{max(g['home_score'], g['away_score'])}-{min(g['home_score'], g['away_score'])}")
                      if w else f"Tie, {g['home_score']}-{g['away_score']}",
            "how_the_game_played": excitement_label(_display_excitement(g["excitement_index"])),
            "records_after_game": {self.team_label(t): self.record(t) for t in (g["away_team_id"], g["home_team_id"])},
            "top_performers": {self.team_label(t): self.leaders(g, t) for t in (g["away_team_id"], g["home_team_id"])},
        }
        script = self.ctx.get("game_scripts", {}).get(g["id"])
        if script:
            facts["how_it_happened"] = script_summary(script)
        if author_team:
            facts["your_team"] = self.team_label(author_team)
            facts["your_team_result"] = "won" if author_team == w else "lost" if w else "tied"
        return facts

    def preview_facts(self, g, author_team=None):
        facts = {
            "game": self.game_label(g),
            "week": g["week"],
            "kickoff": g["game_time"],
            "game_type": g["game_type"],
            "records": {self.team_label(t): self.record(t) for t in (g["away_team_id"], g["home_team_id"])},
            "expected_to_be": excitement_label(_display_excitement(g["projected_excitement"])),
            "last_game": {self.team_label(t): self.last_result(t) for t in (g["away_team_id"], g["home_team_id"])},
        }
        if author_team:
            facts["your_team"] = self.team_label(author_team)
        return facts

    def team_games(self, tid):
        """(last week's completed game or None, this week's game or None) for a team."""
        last = next((g for g in self.ctx["recap_games"] if tid in (g["home_team_id"], g["away_team_id"])), None)
        nxt = next((g for g in self.ctx["preview_games"] if tid in (g["home_team_id"], g["away_team_id"])), None)
        return last, nxt

    def beat_facts(self, tid):
        last, nxt = self.team_games(tid)
        return {
            "team": self.team_with_record(tid),
            "last_game": self.game_facts(last, tid) if last else "Did not play last week.",
            "next_game": self.preview_facts(nxt, tid) if nxt else "No game this week.",
        }

    def team_injuries(self, tid):
        return self.ctx.get("injuries_by_team", {}).get(tid, [])

    def beat_injury_facts(self, tid, inj):
        status = "Placed on Injured Reserve (IR)" if inj["is_ir"] else f"Ruled out / {inj['duration']} ({inj['injury_type']})"
        return {
            "team": self.team_with_record(tid),
            "player_name": inj["player_name"],
            "position": inj["position"],
            "injury_type": inj["injury_type"],
            "duration": inj["duration"],
            "weeks_remaining": inj["weeks_remaining"],
            "is_ir": inj["is_ir"],
            "is_starter": inj["is_starter"],
            "status_update": status
        }

    def injury_shock_facts(self, tid, inj):
        return {
            "team": self.team_with_record(tid),
            "superstar_name": inj["player_name"],
            "position": inj["position"],
            "injury_type": inj["injury_type"],
            "duration": inj["duration"],
            "is_ir": inj["is_ir"],
            "is_starting_qb": inj.get("is_starting_qb", False),
            "headline": f"Devastating injury news: {inj['player_name']} ({inj['position']}) {inj['duration']} with {inj['injury_type']}"
        }

    # Roster moves (load_moves). Every term and figure in a move post comes from its move line or the cap line.
    def team_moves(self, tid):
        return [m for m in self.ctx.get("moves", []) if m["team_id"] == tid]

    def notable_moves(self):
        return sorted((m for m in self.ctx.get("moves", []) if m["notable"] and m["team_id"] in self.teams),
                      key=lambda m: -m["rank_score"])

    def move_facts(self, m):
        facts = {"team": self.team_with_record(m["team_id"]), "move": m["line"],
                 "grounding": "Every term and dollar figure is in `move`; don't add any. Who, what and the terms are "
                              "fact; how talks went and why are yours to imagine."}
        if m.get("depth_slot"):
            facts["new_role"] = f"{norm_pos(m['position'])}{m['depth_slot']} on the depth chart"
        return facts

    def move_reaction_facts(self, m):
        facts = self.move_facts(m)
        n = m.get("seasons_with_team")
        if m["type"] in ("RELEASED", "RE_SIGNED", "EXTENDED", "RESTRUCTURED") and n and n >= 2:
            facts["seasons_with_your_team"] = n
        return facts

    def beat_move_facts(self, tid):
        facts = {"team": self.team_with_record(tid),
                 "moves_this_week": [m["line"] for m in sorted(self.team_moves(tid), key=lambda m: -m["rank_score"])]}
        trades = self.team_trades(tid)
        if trades:
            facts["trades_this_week"] = [t["line"] for t in trades]
        cap = self.ctx.get("team_caps", {}).get(tid)
        if cap:
            facts["cap"] = cap["line"]
        return facts

    # Trades and rumors (load_trades). A trade post's terms come only from its trade line; a rumor post never says
    # a trade happened.
    def team_trades(self, tid):
        return [t for t in self.ctx.get("trades", []) if tid in t["teams"]]

    def trade_facts(self, t):
        a, b = t["teams"]
        return {"teams": [self.team_with_record(a), self.team_with_record(b)], "trade": t["line"],
                "grounding": "This trade was executed: who went where, the picks and every dollar figure are in "
                             "`trade`; don't add any. How it came together is yours to imagine."}

    def trade_reaction_facts(self, t, tid):
        facts = self.trade_facts(t)
        facts["your_team"] = self.team_label(tid)
        if t.get("gm_said", {}).get(tid):
            facts["your_gm_said"] = t["gm_said"][tid]
        return facts

    def rumor_facts(self, r):
        a, b = r["teams"]
        grounding = ("A RUMOR, not a done deal: these two front offices talked; no trade happened. Say "
                     "'talks', 'discussed', 'sources say'. Never say a trade was made or agreed, and no dollar "
                     "figures.")
        if r.get("named"):
            grounding += " A player the leak names may be named, but never as traded."
        else:
            grounding += " Never name a player."
        return {"teams": [self.team_with_record(a), self.team_with_record(b)], "rumor": r["line"],
                "grounding": grounding}

    def cap_take_subject(self):
        """(team_id, release move or None) for the week's cap take: the deepest team over the cap, else the release
        that left the most dead money (CAP_TAKE_DEAD_MONEY+), else None."""
        caps = self.ctx.get("team_caps", {})
        over = sorted((c["cap_space"], t) for t, c in caps.items() if t in self.teams and c.get("cap_space") is not None
                      and c["cap_space"] < 0)
        if over:
            return over[0][1], None
        big = sorted((m for m in self.ctx.get("moves", []) if m["type"] == "RELEASED"
                      and (m.get("dead_money") or 0) >= CAP_TAKE_DEAD_MONEY and m["team_id"] in self.teams),
                     key=lambda m: -m["dead_money"])
        return (big[0]["team_id"], big[0]) if big else None

    def cap_take_facts(self, tid, release=None):
        facts = {"team": self.team_with_record(tid), "cap": self.ctx.get("team_caps", {}).get(tid, {}).get("line"),
                 "grounding": "Cap figures only as given in `cap` (an 'estimated' figure is approximate); no other "
                              "dollar amounts."}
        if release:
            facts["release"] = release["line"]
        return facts

    def last_result(self, tid):
        for g in reversed(self.ctx["games"]):
            if tid in (g["home_team_id"], g["away_team_id"]):
                w, _ = self.winner_loser(g)
                opp = g["away_team_id"] if g["home_team_id"] == tid else g["home_team_id"]
                res = "W" if w == tid else "L" if w else "T"
                us = g["home_score"] if g["home_team_id"] == tid else g["away_score"]
                them = g["away_score"] if g["home_team_id"] == tid else g["home_score"]
                where = "vs" if g["home_team_id"] == tid else "at"
                return f"Week {g['week']}: {res} {us}-{them} {where} {self.teams[opp]['name']}"
        return "no games yet"

    def alive_teams(self) -> set:
        """Postseason: teams still playing. This week's games if any are scheduled (Wild Card through the
        Super Bowl); in the Super Bowl bye week, last week's playoff winners."""
        alive = {t for g in self.ctx["preview_games"] for t in (g["home_team_id"], g["away_team_id"])}
        if not alive:
            for g in self.ctx["recap_games"]:
                w, _ = self.winner_loser(g)
                if w and g["game_type"] != "REGULAR_SEASON":
                    alive.add(w)
        return alive

    # -- players
    def _season_totals(self):
        tot = {}
        for l in self.ctx["player_lines"]:
            key = (l["player_id"] or l["short_name"], l["team_id"], l["position"])
            t = tot.setdefault(key, {"name": l["name"], "team_id": l["team_id"], "position": l["position"],
                                     "player_id": l["player_id"], "popularity": l["popularity"], "games": 0,
                                     "fantasy_points": 0.0, **{k: 0 for k in (
                                         "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions",
                                         "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds")}})
            t["games"] += 1
            t["fantasy_points"] += l["fantasy_points"]
            for k in ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions",
                      "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds"):
                t[k] += l[k] or 0
        return tot

    def season_line(self, line):
        key = (line["player_id"] or line.get("short_name"), line["team_id"], line["position"])
        t = self.season_totals.get(key)
        if not t:
            return None, None
        pos = norm_pos(t["position"])
        if self.fantasy_active:
            same_pos = sorted((v for v in self.season_totals.values() if v["position"] == t["position"]),
                              key=lambda v: v["fantasy_points"], reverse=True)
            rank = next(i for i, v in enumerate(same_pos, 1) if v is t)
            return f"{t['games']} games: {stat_line(t)}", f"{pos}{rank} in full-PPR points ({t['fantasy_points']:.1f})"
        else:
            if pos == "QB":
                stat_key, stat_name = "pass_yards", "passing yards"
            elif pos == "RB":
                stat_key, stat_name = "rush_yards", "rushing yards"
            elif pos in ("WR", "TE"):
                stat_key, stat_name = "rec_yards", "receiving yards"
            else:
                stat_key, stat_name = "rush_yards", "yards"
            same_pos = sorted((v for v in self.season_totals.values() if norm_pos(v["position"]) == pos),
                              key=lambda v: (v.get(stat_key) or 0, v.get("pass_tds" if pos == "QB" else "rec_tds" if pos in ("WR", "TE") else "rush_tds") or 0),
                              reverse=True)
            rank = next((i for i, v in enumerate(same_pos, 1) if v is t), 1)
            val = t.get(stat_key) or 0
            return f"{t['games']} games: {stat_line(t)}", f"{pos}{rank} in {stat_name} ({val:,})"

    def performance_facts(self, l):
        g = next(g for g in self.ctx["recap_games"] if g["id"] == l["game_id"])
        opp = g["away_team_id"] if g["home_team_id"] == l["team_id"] else g["home_team_id"]
        w, _ = self.winner_loser(g)
        season, rank = self.season_line(l)
        facts = {
            "player": f"{l['name']} ({l['position']}, {self.team_label(l['team_id'])})",
            "week": l["week"],
            "stat_line": stat_line(l),
            "game": self.game_label(g),
            "team_result": "won" if w == l["team_id"] else "lost" if w else "tied",
            "opponent": self.team_label(opp),
            "season_so_far": season,
            "season_rank": rank,
        }
        if self.fantasy_active:
            facts["fantasy_points_ppr"] = round(l["fantasy_points"], 1)
        return facts

    def defense_performances(self):
        """Last week's big defensive and kicking games from captured box scores, biggest first."""
        rows = [(defense_play_score(l), l) for l in self.defense_lines]
        rows = [(sc, l) for sc, l in rows if sc]
        rows.sort(key=lambda x: (x[0], x[1].get("def_tackles") or 0, x[1]["popularity"]), reverse=True)
        return [l for _, l in rows]

    def defense_performance_facts(self, l):
        g = next(g for g in self.ctx["recap_games"] if g["id"] == l["game_id"])
        opp = g["away_team_id"] if g["home_team_id"] == l["team_id"] else g["home_team_id"]
        w, _ = self.winner_loser(g)
        return {
            "player": f"{l['name']} ({l['position'] or ('K' if l.get('kick_fga') else 'DEF')}, {self.team_label(l['team_id'])})",
            "week": l["week"],
            "stat_line": defense_stat_line(l),
            "game": self.game_label(g),
            "team_result": "won" if w == l["team_id"] else "lost" if w else "tied",
            "opponent": self.team_label(opp),
            "note": "Game line from the captured box score; no season totals or fantasy points are given for it.",
        }

    def position_leaders(self, pos, n=5):
        pos = norm_pos(pos)
        rows = sorted((t for t in self.season_totals.values() if norm_pos(t["position"]) == pos),
                      key=lambda t: t["fantasy_points"], reverse=True)
        return rows[:n]

    def team_best(self, tid, pos=None):
        rows = [t for t in self.season_totals.values() if t["team_id"] == tid
                and (norm_pos(t["position"]) == norm_pos(pos) if pos else norm_pos(t["position"]) in OFFENSE_POSITIONS)]
        return max(rows, key=lambda t: t["fantasy_points"]) if rows else None

    def player_summary(self, t):
        line = {"player_id": t["player_id"], "short_name": None, "team_id": t["team_id"], "position": t["position"]}
        season, rank = self.season_line(line)
        return {"player": f"{t['name']} ({norm_pos(t['position'])}, {self.team_label(t['team_id'])})",
                "season_so_far": season, "season_rank": rank}

    def bad_games(self):
        """Last week's flops: players averaging 12+ full-PPR points in 3+ earlier games who scored at most 65% of
        that, biggest drop first; then any other QB with BAD_QB_INTERCEPTIONS+ interceptions in a loss. Players with no line at all are skipped
        (they may have been hurt or inactive, which is not a flop)."""
        out, seen = [], set()
        for l in self.recap_lines:
            key = (l["player_id"] or l["short_name"], l["team_id"], l["position"])
            t = self.season_totals.get(key)
            if not t or t["games"] < 4:
                continue
            avg = (t["fantasy_points"] - l["fantasy_points"]) / (t["games"] - 1)
            if avg >= 12 and l["fantasy_points"] <= BAD_GAME_SHARE * avg:
                out.append((avg - l["fantasy_points"], l, avg))
                seen.add(key)
        out.sort(key=lambda x: x[0], reverse=True)
        for l in self.recap_lines:
            key = (l["player_id"] or l["short_name"], l["team_id"], l["position"])
            if key not in seen and norm_pos(l["position"]) == "QB" and (l["interceptions"] or 0) >= BAD_QB_INTERCEPTIONS \
                    and self.line_result(l) == "lost":
                t = self.season_totals.get(key)
                avg = (t["fantasy_points"] - l["fantasy_points"]) / max(1, t["games"] - 1) if t else 0.0
                out.append((0, l, avg))
        return [(l, avg) for _, l, avg in out]

    def line_result(self, l):
        g = next((g for g in self.ctx["recap_games"] if g["id"] == l["game_id"]), None)
        if not g:
            return None
        w, _ = self.winner_loser(g)
        return "won" if w == l["team_id"] else "lost" if w else "tied"

    # -- record book (franchise and NFL records before this season; None without the record tables)
    @staticmethod
    def _holders(rows, best):
        """Who holds a record: "Peyton Manning, 2013", "the 2013 Denver Broncos", two names joined with "and", or for a
        bigger tie "9 players, most recently Sam Darnold, 2029"."""
        held = sorted({(s, h) for v, h, s in rows if v == best}, reverse=True)
        name = lambda s, h: f"the {h}" if str(h)[:4].isdigit() else f"{h}, {s}"
        if len(held) > 2:
            return f"{len(held)} {'teams' if str(held[0][1])[:4].isdigit() else 'players'}, most recently {name(*held[0])}"
        return " and ".join(name(s, h) for s, h in held)

    def franchise_record(self, team_id, category, extra=()):
        """(value, holders) of a franchise's record before this season, counting `extra` [(value, holder, season)]
        from this season too; None if the book has no record for it."""
        book = self.ctx.get("record_book")
        rows = list((book or {}).get("franchise", {}).get((team_id, category), [])) + list(extra)
        if not book or not (book["franchise"].get((team_id, category))):
            return None
        best = max(v for v, _, _ in rows)
        return best, self._holders(rows, best)

    def league_record(self, category, extra=()):
        """(value, holders, all values) of the league-wide record before this season plus `extra`; None if unknown."""
        book = self.ctx.get("record_book")
        if not book or category not in book.get("full", ()) or not book["league"].get(category):
            return None
        rows = list(book["league"][category]) + list(extra)
        best = max(v for v, _, _ in rows)
        return best, self._holders(rows, best), [v for v, _, _ in rows]

    def season_record_marks(self):
        """{stat: (words, mark, holders)}: the NFL single-season records, from the record book when it is loaded."""
        out = {}
        for stat, (words, mark, holder) in SEASON_RECORDS.items():
            rec = self.league_record(f"season_{RECORD_CATEGORY.get(stat, stat)}")
            out[stat] = (words, int(rec[0]), rec[1]) if rec else (words, mark, holder)
        return out

    def _earlier_lines(self, l):
        """This season's earlier single games (before the line's week), as record-book rows for each stat."""
        return [x for x in self.ctx["player_lines"] if x["week"] < l["week"]]

    def game_record_reasons(self, l):
        """[(tier, text)] for single-game records: NFL (tier 2 if tied or broken, tier 1 for the all-time top 10) and
        the player's franchise (tier 1), counting this season's earlier games."""
        out = []
        team = self.teams[l["team_id"]]["name"]
        earlier = self._earlier_lines(l)
        for stat in GAME_RECORD_STATS:
            v = _stat(l, stat)
            if not v:
                continue
            words = RECORD_STATS[stat]
            cat = f"game_{RECORD_CATEGORY.get(stat, stat)}"
            same_season = [(float(_stat(x, stat)), x["name"], self.season) for x in earlier if _stat(x, stat)]
            nfl = self.league_record(cat, same_season)
            if nfl:
                best, holders, values = nfl
                if v >= best:
                    out.append((2, f"broke the NFL single-game record for {words}: {v:g} (old record {best:g}, {holders})"
                                   if v > best else f"tied the NFL single-game record for {words}: {v:g}, shared with {holders}"))
                    continue
                rank = 1 + sum(1 for x in values if x > v)
                if rank <= 10:
                    out.append((1, f"{v:g} {words}, the {ordinal(rank)}-most in a game in NFL history"))
            same_team = [(float(_stat(x, stat)), x["name"], self.season) for x in earlier
                         if _stat(x, stat) and x["team_id"] == l["team_id"]]
            fr = self.franchise_record(l["team_id"], cat, same_team)
            if fr and v >= fr[0]:
                out.append((1, f"a {team} single-game record for {words}: {v:g} (old record {fr[0]:g}, {fr[1]})"
                               if v > fr[0] else f"tied the {team} single-game record for {words}: {v:g}, shared with {fr[1]}"))
        return out

    def season_record_reasons(self, l, tot, chase, key=None):
        """[(tier, text)] for season totals against the record book: the player's franchise record broken this week
        (tier 1) or already his (context), and the NFL all-time top 10 (context; tier 1 from the stretch run for a
        top-3 season)."""
        out = []
        team = self.teams[l["team_id"]]["name"]
        for stat, words in RECORD_STATS.items():
            after = _stat(tot, stat)
            gained = _stat(l, stat)
            if not after:
                continue
            before = after - gained
            cat = f"season_{RECORD_CATEGORY.get(stat, stat)}"
            # This season's other players count too (a teammate may already have passed the old record).
            rivals = [t for k, t in self.season_totals.items() if k != key and _stat(t, stat)]
            row = lambda t: (float(_stat(t, stat)), t["name"], self.season)
            fr = self.franchise_record(l["team_id"], cat, [row(t) for t in rivals if t["team_id"] == l["team_id"]])
            nfl = self.league_record(cat, [row(t) for t in rivals])
            if nfl and after > nfl[0]:
                continue                       # the NFL record line already says it
            if nfl and after == nfl[0] and before < nfl[0]:
                out.append((2, f"tied the NFL single-season record for {words}: {after:,}, shared with {nfl[1]}"))
                continue
            if fr and before <= fr[0] < after:
                out.append((1, f"the most {words} in a season in {team} history: {after:,} (old record {fr[0]:,.0f}, {fr[1]})"))
            elif fr and before > fr[0] and gained:
                out.append((0, f"already the most {words} in a season in {team} history ({after:,}; old record {fr[0]:,.0f})"))
            if nfl:
                rank = 1 + sum(1 for x in nfl[2] if x > after)
                was = 1 + sum(1 for x in nfl[2] if x > before)
                if 1 < rank <= 10 and gained and rank < was:
                    out.append((chase if rank <= 3 else 0, f"{after:,} {words}, already {ordinal(rank)} in a season in NFL history"))
        return out

    # -- buzz: what the league is talking about
    def _game_rank(self, stat, value, pos=None):
        """1 + the number of single-game lines this season (through the recap week) that beat `value`."""
        return 1 + sum(1 for x in self.ctx["player_lines"] if _stat(x, stat) > value
                       and (pos is None or norm_pos(x["position"]) == pos))

    def player_buzz(self, l):
        """(tier, reasons) for one recap-week line: 0 = ordinary, 1 = the league is talking, 2 = historic."""
        tier, reasons = 0, []
        pos = norm_pos(l["position"])
        rules = dict(SINGLE_GAME_BUZZ)
        if not self.fantasy_active:
            rules.pop("fantasy_points", None)
        rules.update(SINGLE_GAME_BUZZ_BY_POSITION.get(pos, {}))
        for stat, (words, big, historic) in rules.items():
            v = round(_stat(l, stat), 1) if stat == "fantasy_points" else _stat(l, stat)
            if v < big:
                continue
            t = 2 if historic is not None and v >= historic else 1
            tier = max(tier, t)
            # Fantasy points and position-specific thresholds rank within the position; yards and TDs league-wide.
            rank_pos = pos if stat == "fantasy_points" or stat in SINGLE_GAME_BUZZ_BY_POSITION.get(pos, {}) else None
            rank = self._game_rank(stat, v, rank_pos)
            where = f" by a {pos}" if rank_pos else ""
            text = f"{v:g} {words}"
            if rank <= 3:
                text += f", the {'most' if rank == 1 else ordinal(rank) + '-most'}{where} in a game this season"
            reasons.append((t, text))
        if self.fantasy_active and tier and self.recap_week >= 4 and self._game_rank("fantasy_points", l["fantasy_points"], pos) == 1 \
                and not any("most by a" in r for _, r in reasons):
            tier = 2
            reasons.append((2, f"the best single game by a {pos} this season (full PPR)"))
        for t, text in self.game_record_reasons(l):
            tier = max(tier, t)
            reasons.append((t, text))
        context = []   # (tier it earns on its own, text)
        chase = 1 if self.recap_week >= RECORD_CHASE_FROM_WEEK else 0
        if self.recap_week <= REGULAR_SEASON_FINAL_WEEK:
            key = (l["player_id"] or l["short_name"], l["team_id"], l["position"])
            tot = self.season_totals.get(key)
            if tot:
                for stat, (words, marks) in SEASON_MILESTONES.items():
                    after = _stat(tot, stat)
                    before = after - _stat(l, stat)
                    for m, alone in marks:
                        if before < m <= after:
                            context.append((1 if alone else 0, f"crossed {m:,} {words} for the season ({after:,})"))
                for stat, (words, mark, holder) in self.season_record_marks().items():
                    after = _stat(tot, stat)
                    before = after - _stat(l, stat)
                    if before <= mark < after:
                        tier = 2
                        reasons.append((2, f"broke the NFL single-season record for {words}: now {after:,} (old record {mark:,}, {holder})"))
                    elif before > mark and _stat(l, stat):
                        context.append((chase, f"{after:,} {words} this season, past the NFL single-season record of {mark:,} ({holder})"))
                    elif tot["games"] >= PACE_MIN_GAMES and after <= mark:
                        pace = round(after / tot["games"] * GAMES_PER_SEASON)
                        if pace > mark:
                            context.append((chase, f"on pace for {pace:,} {words} ({after:,} in {tot['games']} games); "
                                                   f"the NFL single-season record is {mark:,} ({holder})"))
                for t, text in self.season_record_reasons(l, tot, chase, key):
                    if t and text.startswith(("the most", "tied the")):
                        tier = max(tier, t)
                        reasons.append((t, text))
                    else:
                        context.append((t, text))
        for t, text in context:
            tier = max(tier, t)
        if tier:
            reasons += context
        reasons.sort(key=lambda r: -r[0])
        return tier, [r for _, r in reasons[:3]]

    def buzz_lines(self):
        """[(line, tier, reasons)] for the recap week's tier 1+ lines, biggest first."""
        out = []
        for l in self.recap_lines:
            if norm_pos(l["position"]) not in OFFENSE_POSITIONS:
                continue
            tier, reasons = self.player_buzz(l)
            if tier:
                out.append((l, tier, reasons))
        out.sort(key=lambda x: (x[1], x[0]["fantasy_points"]), reverse=True)
        return out

    def best_single_games(self, pos, n=5):
        pos = norm_pos(pos)
        rows = sorted((x for x in self.ctx["player_lines"] if norm_pos(x["position"]) == pos),
                      key=lambda x: x["fantasy_points"], reverse=True)[:n]
        if self.fantasy_active:
            return [f"{x['name']} ({self.teams[x['team_id']]['abbr']}), Week {x['week']}: {stat_line(x)} "
                    f"({x['fantasy_points']:.1f} PPR)" for x in rows]
        return [f"{x['name']} ({self.teams[x['team_id']]['abbr']}), Week {x['week']}: {stat_line(x)}" for x in rows]

    def buzz_facts(self, l, tier, reasons):
        facts = self.performance_facts(l)
        facts["why_people_are_talking"] = reasons
        facts["how_big"] = "historic" if tier == 2 else "the league is talking about it"
        return facts

    def ranking_facts(self, l, reasons):
        pos = norm_pos(l["position"])
        return {"question": f"Where does this game rank among the best by a {pos} this season?",
                "this_game": self.buzz_facts(l, 2, reasons), f"best_{pos}_games_this_season": self.best_single_games(pos)}

    # -- team storylines
    def team_results(self, tid):
        """Regular-season results through the recap week, oldest first: [(res, us, them, opp, game)]."""
        last = min(self.recap_week, REGULAR_SEASON_FINAL_WEEK)
        out = []
        for g in self.ctx["games"]:
            if g["week"] > last or tid not in (g["home_team_id"], g["away_team_id"]) or g["home_score"] is None:
                continue
            home = g["home_team_id"] == tid
            us, them = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
            out.append(("W" if us > them else "L" if us < them else "T", us, them,
                        g["away_team_id"] if home else g["home_team_id"], g))
        return out

    def team_stories(self):
        """Team storylines from last week's regular-season games: [{team_id, tier, positive, reasons, history}],
        biggest first. One story per team; its reasons come from every trigger it hit."""
        if not self.ctx["recap_games"] or self.recap_week > REGULAR_SEASON_FINAL_WEEK:
            return []
        stories = []
        for g in self.ctx["recap_games"]:
            for tid in (g["away_team_id"], g["home_team_id"]):
                res = self.team_results(tid)
                if not res or res[-1][4]["id"] != g["id"]:
                    continue
                hits, history = [], []
                r, us, them, opp, _ = res[-1]
                n = len(res)
                wins = sum(1 for x in res if x[0] == "W")
                losses = sum(1 for x in res if x[0] == "L")
                ties = n - wins - losses
                streak = 1
                while streak < n and res[-1 - streak][0] == r:
                    streak += 1
                prior = res[:-1]
                prior_streak = 0
                if prior:
                    p = prior[-1][0]
                    while prior_streak < len(prior) and prior[-1 - prior_streak][0] == p:
                        prior_streak += 1
                rec = self.record(tid)
                opp_name = self.teams[opp]["name"]
                if losses == 0 and n >= RUN_MIN_GAMES:
                    t = 2 if n >= HISTORIC_RUN_GAMES else 1
                    hits.append((t, True, f"{'Perfect' if not ties else 'Unbeaten'} at {rec}"))
                    if t == 2:
                        history.append("The 1972 Dolphins are the only NFL team to finish unbeaten and win the Super Bowl "
                                       "(17-0, playoffs included); the 2007 Patriots went 16-0 in the regular season and lost the Super Bowl.")
                elif wins == 0 and n >= RUN_MIN_GAMES:
                    t = 2 if n >= HISTORIC_RUN_GAMES else 1
                    hits.append((t, False, f"Still winless at {rec}"))
                    if t == 2:
                        history.append("The 2008 Lions and the 2017 Browns went 0-16; no team has gone 0-17.")
                elif streak >= STREAK_MIN and r in "WL":
                    hits.append((2 if streak >= HISTORIC_RUN_GAMES else 1, r == "W",
                                 f"{'Won' if r == 'W' else 'Lost'} {streak} straight"))
                if r == "L" and prior and prior[-1][0] == "W" and prior_streak >= STREAK_MIN:
                    if prior_streak == len(prior) and prior_streak >= 8:
                        hits.append((2, False, f"First loss of the season after a {prior_streak}-0 start: {us}-{them} to the {opp_name}"))
                    else:
                        hits.append((1, False, f"Their {prior_streak}-game winning streak ended: {us}-{them} to the {opp_name}"))
                if r == "W" and wins == 1 and n - 1 >= RUN_MIN_GAMES:
                    hits.append((1, True, f"First win of the season after an 0-{n - 1} start: {us}-{them} over the {opp_name}"))
                if r == "W" and us - them >= BLOWOUT_MARGIN:
                    hits.append((1, True, f"Won by {us - them}: {us}-{them} over the {opp_name}"))
                if us >= BIG_SCORE:
                    hits.append((1, True, f"Scored {us} points against the {opp_name}"))
                if r == "W" and len(prior) >= RUN_MIN_GAMES:
                    opp_prior = self.team_results(opp)[:-1]
                    if len(opp_prior) >= RUN_MIN_GAMES:
                        pct = lambda xs: sum(1 if x[0] == "W" else 0.5 if x[0] == "T" else 0 for x in xs) / len(xs)
                        if pct(opp_prior) - pct(prior) >= UPSET_GAP:
                            rec_of = lambda xs: f"{sum(x[0] == 'W' for x in xs)}-{sum(x[0] == 'L' for x in xs)}"
                            hits.append((1, True, f"Upset the {rec_of(opp_prior)} {opp_name} as a {rec_of(prior)} team, {us}-{them}"))
                pf = self.records[tid]["pf"]
                mark, holder = TEAM_POINTS_RECORD
                book_points = self.league_record("season_team_points")
                if book_points:
                    mark, holder = int(book_points[0]), book_points[1].removeprefix("the ")
                team_name = self.teams[tid]["name"]
                for cat, now, prev, words in (("season_team_wins", wins, wins - (r == "W"), "wins"),
                                              ("season_team_points", pf, pf - us, "points")):
                    fr = self.franchise_record(tid, cat)
                    if fr and prev <= fr[0] < now and not (cat == "season_team_points" and prev <= mark < now):
                        hits.append((1, True, f"Most {words} in a season in {team_name} history: {now} "
                                              f"(old record {fr[0]:.0f}, {fr[1]})"))
                if pf - us <= mark < pf:
                    hits.append((2, True, f"Broke the NFL record for points in a season: {pf} (old record {mark}, {holder})"))
                elif pf - us > mark:
                    hits.append((1, True, f"{pf} points this season, past the NFL record of {mark} ({holder})"))
                elif n >= PACE_MIN_GAMES and pf / n * GAMES_PER_SEASON > mark:
                    hits.append((1, True, f"On pace for {round(pf / n * GAMES_PER_SEASON)} points ({pf} in {n} games); "
                                          f"the NFL record is {mark} ({holder})"))
                if not hits:
                    continue
                hits.sort(key=lambda h: -h[0])
                stories.append({"team_id": tid, "tier": hits[0][0], "positive": hits[0][1],
                                "reasons": [h[2] for h in hits[:3]], "history": history})
        stories.sort(key=lambda s: (s["tier"], self.teams[s["team_id"]]["popularity"]), reverse=True)
        return stories

    def team_story_facts(self, story):
        tid = story["team_id"]
        r = self.records[tid]
        games = r["w"] + r["l"] + r["t"]
        order = sorted(self.teams, key=lambda t: (self.win_pct(t), self.diff(t)), reverse=True)
        _, nxt = self.team_games(tid)
        facts = {
            "team": self.team_with_record(tid),
            "why_people_are_talking": story["reasons"],
            "how_big": "historic" if story["tier"] == 2 else "the league is talking about it",
            "points_for": r["pf"], "points_against": r["pa"], "point_differential": f"{self.diff(tid):+d}",
            "league_rank_by_record": ordinal(order.index(tid) + 1),
            "games_left_in_regular_season": max(0, GAMES_PER_SEASON - games),
            "last_game": self.last_result(tid),
            "next_game": (f"Week {nxt['week']}: {self.game_label(nxt)} (opponent "
                          f"{self.record(nxt['home_team_id'] if nxt['away_team_id'] == tid else nxt['away_team_id'])})")
                         if nxt else "No game this week.",
        }
        best = self.team_best(tid)
        if best:
            facts["best_player"] = self.player_summary(best)
        if story["history"]:
            facts["nfl_history_through_2025"] = story["history"]
        return facts

    def bad_game_facts(self, l, avg):
        facts = self.performance_facts(l)
        if self.fantasy_active:
            facts["season_average_before_this_game_ppr"] = round(avg, 1)
        facts["note"] = "A bad game by his standard."
        return facts

    def comparison_facts(self, a, b, pos):
        return {"position": norm_pos(pos), "your_player": self.player_summary(a), "their_player": self.player_summary(b),
                "records": {self.team_label(a["team_id"]): self.record(a["team_id"]),
                            self.team_label(b["team_id"]): self.record(b["team_id"])}}

    def leaderboard_facts(self, pos, slant_tid=None):
        facts = {"position": norm_pos(pos), "top_five_this_season": [self.player_summary(t) for t in self.position_leaders(pos)]}
        if slant_tid:
            best = self.team_best(slant_tid, pos)
            if best:
                facts["your_old_team_player"] = self.player_summary(best)
        return facts

    def season_player_facts(self, t):
        line = {"player_id": t["player_id"], "short_name": None, "team_id": t["team_id"], "position": t["position"]}
        season, rank = self.season_line(line)
        return {
            "player": f"{t['name']} ({t['position']}, {self.team_label(t['team_id'])})",
            "season_so_far": season,
            "season_rank": rank,
            "team_record": self.record(t["team_id"]),
        }


# ----------------------------------------------------------------------------------------------- topics ---
# League topics for the posts that aren't about one game (HOT_TAKE, AWARD_RACE, POWER_TAKE, QUESTION; AGENT_WIRE_VOICE_PLAN
# §2.4). Each is {topic_id, kind_hint, label, subjects: [{type, id}], focus: [facts], heat}, built only from data
# stored before publication week N. `week_topics` derives what the week's own context holds (leaders, record chases,
# who's real, the playoff picture, division races); `load_stored_topics` adds the stored ones (power rankings,
# coach of the year and the hot seat, contract years, rookies, injuries). A missing table gives no topics of its type.
def _topic(topic_id, kind_hint, label, subjects, focus, heat):
    return {"topic_id": topic_id, "kind_hint": kind_hint, "label": label, "subjects": subjects,
            "focus": [f for f in focus if f], "heat": round(float(heat), 2)}


def week_topics(W) -> list:
    """Topics from a Week's own data (games and lines before the publication week). Pure."""
    out = []
    season, week, rw = W.season, W.week, min(W.recap_week, REGULAR_SEASON_FINAL_WEEK)
    if rw < 1:
        return out
    abbr = lambda t: W.teams[t]["abbr"]
    late = 0.03 * min(rw, 18)
    if W.season_totals and rw >= 3:
        qbs = sorted((t for t in W.season_totals.values() if norm_pos(t["position"]) == "QB" and t["games"] >= 2),
                     key=lambda t: t["pass_yards"] / 25 + 4 * t["pass_tds"] - 2 * t["interceptions"]
                     + 30 * W.win_pct(t["team_id"]), reverse=True)[:3]
        if qbs:
            out.append(_topic(f"mvp_race:{season}:{week}", "AWARD_RACE", "The MVP race",
                              [{"type": "player", "id": t["player_id"]} for t in qbs if t["player_id"]],
                              [f"{t['name']} ({abbr(t['team_id'])}): {t['pass_yards']:,} pass yds, {t['pass_tds']} TD, "
                               f"{t['interceptions']} INT in {t['games']} games; {abbr(t['team_id'])} {W.record(t['team_id'])}"
                               for t in qbs], 0.55 + late))
        scrim = lambda t: (t["rush_yards"] or 0) + (t["rec_yards"] or 0)
        skill = sorted((t for t in W.season_totals.values() if norm_pos(t["position"]) in ("RB", "WR", "TE")),
                       key=scrim, reverse=True)[:3]
        if skill:
            out.append(_topic(f"opoy_race:{season}:{week}", "AWARD_RACE", "Offensive Player of the Year race",
                              [{"type": "player", "id": t["player_id"]} for t in skill if t["player_id"]],
                              [f"{t['name']} ({norm_pos(t['position'])}, {abbr(t['team_id'])}): {scrim(t):,} scrimmage yds, "
                               f"{(t['rush_tds'] or 0) + (t['rec_tds'] or 0)} TD in {t['games']} games" for t in skill],
                              0.45 + late))
        marks = W.season_record_marks()
        for stat, (words, mark, holder) in marks.items():
            best = None
            for t in W.season_totals.values():
                if t["games"] < PACE_MIN_GAMES or W.recap_week > REGULAR_SEASON_FINAL_WEEK:
                    continue
                pace = round(_stat(t, stat) / t["games"] * GAMES_PER_SEASON)
                if pace > mark and (best is None or pace > best[0]):
                    best = (pace, t)
            if best:
                pace, t = best
                out.append(_topic(f"record_chase:{stat}:{t['player_id'] or t['name']}:{season}:{week}", "HOT_TAKE",
                                  f"{t['name']} chasing the {words} record",
                                  [{"type": "player", "id": t["player_id"]}] if t["player_id"] else [],
                                  [f"{t['name']} ({abbr(t['team_id'])}) is on pace for {pace:,} {words} "
                                   f"({_stat(t, stat):,} in {t['games']} games); the NFL single-season record is {mark:,} ({holder})"],
                                  0.6 + (0.3 if rw >= RECORD_CHASE_FROM_WEEK else 0)))
    if 3 <= week <= REGULAR_SEASON_FINAL_WEEK:
        order = sorted(W.teams, key=lambda t: (W.win_pct(t), W.diff(t)), reverse=True)
        top = order[:3]
        out.append(_topic(f"best_teams:{season}:{week}", "POWER_TAKE", "Who is the best team in football",
                          [{"type": "team", "id": t} for t in top],
                          [f"{W.team_label(t)} {W.record(t)}, point differential {W.diff(t):+d}" for t in top], 0.5 + late))
        frauds = [t for t in order[:12] if W.win_pct(t) >= 0.6 and W.diff(t) <= 10]
        if frauds:
            t = frauds[0]
            out.append(_topic(f"real_or_not:{t}:{season}:{week}", "POWER_TAKE", f"Are the {W.teams[t]['name']} for real",
                              [{"type": "team", "id": t}],
                              [f"{W.team_label(t)} are {W.record(t)} with a point differential of only {W.diff(t):+d}"],
                              0.55 + late))
        if week >= 6:
            for conf in ("AFC", "NFC"):
                bubble = W.conference_order(conf)[5:9]
                if bubble:
                    out.append(_topic(f"playoff_bubble:{conf}:{season}:{week}", "QUESTION", f"{conf} playoff bubble",
                                      [{"type": "team", "id": t} for t in bubble],
                                      [f"{conf} seeds 6-9 by record (no tiebreakers): "
                                       + ", ".join(f"{abbr(t)} {W.record(t)}" for t in bubble)], 0.4 + late))
        if week >= 8:
            races = []
            for div in sorted({v["division"] for v in W.teams.values()}):
                tids = sorted((t for t, v in W.teams.items() if v["division"] == div),
                              key=lambda t: (W.win_pct(t), W.diff(t)), reverse=True)
                if len(tids) >= 2:
                    races.append((abs(W.win_pct(tids[0]) - W.win_pct(tids[1])), div, tids[:3]))
            races.sort()
            if races and races[0][0] <= 0.15:
                _, div, tids = races[0]
                out.append(_topic(f"division_race:{div}:{season}:{week}", "HOT_TAKE", f"The {div} race",
                                  [{"type": "team", "id": t} for t in tids],
                                  [f"{div}: " + ", ".join(f"{W.team_label(t)} {W.record(t)}" for t in tids)], 0.5 + late))
    return out


def _team_name(teams, tid):
    t = (teams or {}).get(tid)
    return f"{t['city']} {t['name']}" if t else f"team {tid}"


def load_stored_topics(cur, season: int, week: int, teams: dict) -> list:
    """Topics from stored data before Week `week` (each source in its own savepoint; a missing table or column means
    no topics of that type). Rows carrying a week are filtered again here, so nothing from Week N onward slips in."""
    out = []

    def power():
        cur.execute("""/* topics:power */
            SELECT week, rank, team_id, movement, record FROM power_rankings
            WHERE season = %s AND week = (SELECT MAX(week) FROM power_rankings WHERE season = %s AND week < %s)""",
                    (season, season, week))
        rows = [r for r in cur.fetchall() if r[0] is not None and r[0] < week]
        res = []
        if not rows:
            return res
        w = rows[0][0]
        rows.sort(key=lambda r: r[1])
        top = rows[0]
        res.append(_topic(f"power_top:{season}:{w}", "POWER_TAKE", f"Power rankings #1: {_team_name(teams, top[2])}",
                          [{"type": "team", "id": top[2]}],
                          [f"Week {w} power rankings: {_team_name(teams, top[2])} #1"
                           + (f" ({top[4]})" if top[4] else "")] +
                          [f"#{r[1]} {_team_name(teams, r[2])}" for r in rows[1:4]], 0.6))
        moved = [r for r in rows if r[3] is not None and abs(r[3]) >= 3]
        for r in sorted(moved, key=lambda r: -abs(r[3]))[:2]:
            direction = "climbed" if r[3] > 0 else "fell"
            res.append(_topic(f"power_move:{r[2]}:{season}:{w}", "POWER_TAKE",
                              f"{_team_name(teams, r[2])} {direction} {abs(r[3])} spots",
                              [{"type": "team", "id": r[2]}],
                              [f"Week {w} power rankings: {_team_name(teams, r[2])} {direction} {abs(r[3])} spots to #{r[1]}"],
                              0.45 + 0.02 * abs(r[3])))
        return res

    def coty():
        if week <= REGULAR_SEASON_FINAL_WEEK:       # stored scores carry no week: only once the regular season is over
            return []
        cur.execute("""/* topics:coty */
            SELECT s.coty_rank, s.team_id, c.coach_name, s.historical_notes, s.coach_id FROM coty_scores s
            JOIN coaches c ON c.id = s.coach_id WHERE s.season = %s ORDER BY s.coty_rank LIMIT 3""", (season,))
        rows = cur.fetchall()
        if not rows:
            return []
        return [_topic(f"coty_race:{season}", "AWARD_RACE", "Coach of the Year",
                       [{"type": "coach", "id": r[4]} for r in rows],
                       [f"{r[2]} ({_team_name(teams, r[1])})" + (f": {r[3][0]}" if r[3] else "") for r in rows], 0.5)]

    def hot_seat():
        res = []
        cur.execute("""/* topics:hot_seat */
            SELECT h.season, h.team_id, h.coach_id, c.coach_name, h.score, h.fired, h.phase FROM coach_hot_seat h
            JOIN coaches c ON c.id = h.coach_id WHERE h.season IN (%s, %s)""", (season - 1, season))
        rows = cur.fetchall()
        fired_now = [r for r in rows if r[0] == season and r[5] and week > REGULAR_SEASON_FINAL_WEEK]
        if fired_now:
            res.append(_topic(f"coaches_fired:{season}", "HOT_TAKE", "Black Monday",
                              [{"type": "coach", "id": r[2]} for r in fired_now],
                              [f"{r[3]} fired by the {_team_name(teams, r[1])}" for r in fired_now[:4]], 0.7))
        shaky = sorted((r for r in rows if r[0] == season - 1 and not r[5] and float(r[4] or 0) >= 0.6),
                       key=lambda r: -float(r[4] or 0))[:3]
        if shaky and week <= REGULAR_SEASON_FINAL_WEEK:
            res.append(_topic(f"hot_seat:{season}:{week}", "HOT_TAKE", "Coaches on the hot seat",
                              [{"type": "coach", "id": r[2]} for r in shaky],
                              [f"{r[3]} ({_team_name(teams, r[1])}) kept his job after last season, barely" for r in shaky],
                              0.5))
        return res

    def contract_year():
        cur.execute("""/* topics:contract_year */
            SELECT pc.player_id, COALESCE(p.full_name, p.player_name), pse.position, pse.team_id
            FROM player_contracts pc
            JOIN players p ON p.id = pc.player_id
            JOIN player_seasons pse ON pse.player_id = pc.player_id AND pse.season = pc.season AND pse.team_id = pc.team_id
            WHERE pc.season = %s AND pc.years_remaining = 1 AND COALESCE(pc.extension_years, 0) = 0
              AND (pc.signed_week IS NULL OR pc.signed_season < %s OR pc.signed_week < %s)
              AND (pse.released_week IS NULL OR pse.released_week >= %s)
            ORDER BY pse.overall_rating DESC NULLS LAST LIMIT 3""", (season, season, week, week))
        rows = cur.fetchall()
        if not rows:
            return []
        return [_topic(f"contract_year:{season}:{week}", "HOT_TAKE", "Stars playing for a new contract",
                       [{"type": "player", "id": r[0]} for r in rows],
                       [f"{tidy_name(r[1])} ({norm_pos(r[2])}, {_team_name(teams, r[3])}) is in the last year of his deal"
                        for r in rows], 0.4)]

    def rookies():
        cur.execute("""/* topics:rookies */
            SELECT ps.player_id, COALESCE(p.full_name, p.player_name), pse.position, pse.team_id, MAX(g.week),
                   COUNT(*), SUM(ps.pass_yards), SUM(ps.pass_tds), SUM(ps.rush_yards), SUM(ps.rec_yards),
                   SUM(ps.rush_tds) + SUM(ps.rec_tds)
            FROM player_stats ps
            JOIN games g ON g.id = ps.game_id
            JOIN players p ON p.id = ps.player_id
            JOIN player_seasons pse ON pse.player_id = ps.player_id AND pse.season = g.season
            WHERE g.season = %s AND g.week < %s AND g.status = 'COMPLETED' AND g.game_type = 'REGULAR_SEASON'
              AND pse.years_of_experience = 0
            GROUP BY 1, 2, 3, 4
            ORDER BY SUM(ps.pass_yards) / 25.0 + SUM(ps.rush_yards) / 10.0 + SUM(ps.rec_yards) / 10.0 DESC LIMIT 3""",
                    (season, week))
        rows = [r for r in cur.fetchall() if r[4] is not None and r[4] < week]
        if not rows:
            return []
        def line(r):
            pos = norm_pos(r[2])
            if pos == "QB":
                return f"{r[6] or 0:,} pass yds, {r[7] or 0} TD"
            return f"{(r[8] or 0) + (r[9] or 0):,} scrimmage yds, {r[10] or 0} TD"
        return [_topic(f"roy_race:{season}:{week}", "AWARD_RACE", "Rookie of the Year race",
                       [{"type": "player", "id": r[0]} for r in rows],
                       [f"{tidy_name(r[1])} ({norm_pos(r[2])}, {_team_name(teams, r[3])}): {line(r)} in {r[5]} games"
                        for r in rows], 0.45)]

    def injuries():
        cur.execute("""/* topics:injuries */
            SELECT i.week, i.team_id, i.player_id, COALESCE(p.full_name, i.player_name), i.position, i.duration,
                   i.injury_type, i.is_ir
            FROM injuries i LEFT JOIN players p ON p.id = i.player_id
            WHERE i.season = %s AND i.week = (SELECT MAX(week) FROM injuries WHERE season = %s AND week < %s)
              AND (i.is_ir OR i.weeks_remaining >= 4) AND COALESCE(i.ovr, 0) >= 85
            ORDER BY i.ovr DESC LIMIT 3""", (season, season, week))
        rows = [r for r in cur.fetchall() if r[0] is not None and r[0] < week]
        return [_topic(f"injury:{r[2]}:{season}:{r[0]}", "QUESTION", f"{tidy_name(r[3])} injury",
                       [{"type": "player", "id": r[2]}, {"type": "team", "id": r[1]}],
                       [f"{tidy_name(r[3])} ({norm_pos(r[4])}, {_team_name(teams, r[1])}) "
                        + ("is on injured reserve" if r[7] else f"is out {r[5]}") + f" ({r[6]})"], 0.5) for r in rows]

    for name, fn in (("power", power), ("coty", coty), ("hot_seat", hot_seat), ("contract_year", contract_year),
                     ("rookies", rookies), ("injuries", injuries)):
        out += _savepoint(cur, f"agent_wire_topics_{name}", fn, [])
    return out


def load_league_topics(cur, season: int, week: int) -> list:
    """League topics for publication week `week`, from stored data before Week N only: the week's own leaders, record
    chases, standings and races (week_topics) plus the stored ones (load_stored_topics). Hottest first."""
    def teams_q():
        cur.execute("/* desk:teams */ SELECT id, name, city, abbreviation, conference, division FROM teams")
        return {r[0]: {"id": r[0], "name": r[1], "city": r[2], "abbr": r[3], "conference": r[4], "division": r[5],
                       "popularity": 0.0} for r in cur.fetchall()}
    teams = _savepoint(cur, "agent_wire_topic_teams", teams_q, {})

    def games_q():
        cols = ("id", "week", "home_team_id", "away_team_id", "home_score", "away_score", "status", "game_time",
                "game_type", "excitement_index", "projected_excitement")
        cur.execute(f"""/* desk:games */ SELECT {', '.join(cols)} FROM games
                        WHERE season = %s AND week < %s AND status = 'COMPLETED' ORDER BY week, id""", (season, week))
        return [dict(zip(cols, r)) for r in cur.fetchall() if r[1] is not None and r[1] < week]
    games = _savepoint(cur, "agent_wire_topic_games", games_q, [])
    lines = _savepoint(cur, "agent_wire_topic_lines", lambda: fetch_player_lines(cur, season, week), [])
    topics = []
    if teams:
        ctx = {"season": season, "week": week, "recap_week": week - 1, "teams": teams, "games": games,
               "recap_games": [g for g in games if g["week"] == week - 1], "preview_games": [], "player_lines": lines}
        topics = week_topics(Week(ctx))
    topics += load_stored_topics(cur, season, week, teams)
    return sorted(topics, key=lambda t: -t["heat"])


# ------------------------------------------------------------------------------------------- accounts ---
class Accounts:
    """Account pools by role, with least-used selection under weekly caps. Deterministic for a seed."""

    def __init__(self, accounts, seed, fantasy_enabled: bool = True):
        self.rng = random.Random(seed)
        self.uses = collections.Counter()
        self.by_id = {a["agent_id"]: a for a in accounts}
        self.order = {a["agent_id"]: self.rng.random() for a in accounts}  # stable tie-break
        self.fantasy_enabled = fantasy_enabled
        self.role = {}
        for a in accounts:
            arch = a["archetype"]
            self.role[a["agent_id"]] = (
                "fan" if arch == "DIE_HARD_FAN" else
                "bandwagon" if arch == "BANDWAGON_FAN" else
                "beat" if arch == "LOCAL_BEAT_REPORTER" else
                "meme" if arch == "NFL_MEMES" else
                "draft" if arch == "DRAFT_SICKO" else
                "fantasy" if arch in FANTASY_ARCHETYPES else
                "former" if arch == "FORMER_PLAYER" else
                "aggregator" if arch == AGGREGATOR_ARCHETYPE else
                "stats" if arch == STATS_ARCHETYPE else
                "pundit" if arch not in PUNDIT_EXCLUDED and arch not in FANTASY_ARCHETYPES else
                None
            )

    def replay(self, assignments):
        """Count posts already assigned (e.g. the week's plan before the reply pass); shares don't count."""
        for a in assignments:
            if a.get("agent_id") and a["kind"] != "SHARE":
                self.uses[a["agent_id"]] += 1
        return self

    def block(self, agent_ids):
        """Accounts that must not be picked again (e.g. already posted in this team's wakeup)."""
        for aid in agent_ids:
            self.uses[aid] += 1000
        return self

    def _pick(self, candidates, prefer=(), cap_role=None):
        prefer = set(prefer)
        if not self.fantasy_enabled:
            candidates = [a for a in candidates if a.get("archetype") not in FANTASY_ARCHETYPES and self.role.get(a["agent_id"]) != "fantasy"]
        ok = [a for a in candidates
              if self.uses[a["agent_id"]] < ROLE_CAPS.get(cap_role or self.role.get(a["agent_id"]) or "pundit", 3)]
        if not ok:
            return None
        best = min(ok, key=lambda a: (self.uses[a["agent_id"]], a["archetype"] not in prefer, self.order[a["agent_id"]]))
        self.uses[best["agent_id"]] += 1
        return best

    def all(self):
        return self.by_id.values()

    def fan(self, team_id):
        return self._pick([a for a in self.all() if a["archetype"] == "DIE_HARD_FAN" and a["team_id"] == team_id])

    def beat(self, team_id):
        return self._pick([a for a in self.all() if a["archetype"] == "LOCAL_BEAT_REPORTER" and a["team_id"] == team_id])

    def bandwagon(self):
        return self._pick([a for a in self.all() if a["archetype"] == "BANDWAGON_FAN"])

    def pundit(self, prefer=()):
        return self._pick([a for a in self.all() if self.role.get(a["agent_id"]) == "pundit"], prefer)

    def fantasy(self):
        if not self.fantasy_enabled:
            return None
        return self._pick([a for a in self.all() if a["archetype"] in FANTASY_ARCHETYPES], cap_role="fantasy",
                          prefer=("FANTASY_GURU",))

    def former(self, teams=(), pos=None):
        """An ex-player, preferring one whose old team is in `teams`, then one who played `pos`."""
        teams, pos = set(teams), norm_pos(pos)
        ok = [a for a in self.all() if a["archetype"] == "FORMER_PLAYER" and self.uses[a["agent_id"]] < ROLE_CAPS["former"]]
        if not ok:
            return None
        best = min(ok, key=lambda a: (self.uses[a["agent_id"]], a["team_id"] not in teams,
                                      former_position(a) != pos, self.order[a["agent_id"]]))
        self.uses[best["agent_id"]] += 1
        return best

    def archetype(self, arch):
        return self._pick([a for a in self.all() if a["archetype"] == arch], cap_role="meme")

    def insider(self, key=None):
        """A news-breaking insider (Schefter, Rapoport) under the pundit cap; with `key`, the archetype that got a
        leak first is preferred (it still counts against the cap)."""
        cands = [a for a in self.all() if a["archetype"] in INSIDER_ARCHETYPES]
        return self._pick(cands, prefer=(key,) if key else (), cap_role="pundit")

    def aggregator(self):
        """The label-first aggregator account (D7), if seeded."""
        return self._pick([a for a in self.all() if a["archetype"] == AGGREGATOR_ARCHETYPE])

    def stats(self):
        """The stats account (D7), if seeded."""
        return self._pick([a for a in self.all() if a["archetype"] == STATS_ARCHETYPE])

    def take(self, account):
        """Count a post for an account chosen elsewhere, if it is under its cap; returns it or None."""
        if not account:
            return None
        return self._pick([account])

    def by_archetype_uncapped(self, arch):
        """The account that shares an article (shares never count against caps)."""
        for a in sorted(self.all(), key=lambda a: a["agent_id"]):
            if a["archetype"] == arch:
                return a
        return None


# --------------------------------------------------------------------------------------------- planner ---
# No canned angle lists: a post's angle is an optional hint from its topic or (U4) the writer's relationships, and
# `focus` carries the one to three facts the post is about (AGENT_WIRE_VOICE_PLAN §2.4, §2.7).
FAN_ARCHETYPES = ("DIE_HARD_FAN", "BANDWAGON_FAN")
PERIOD_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "OT": 5}
QUARTER_NAMES = {"Q1": "end of the 1st quarter", "Q2": "halftime", "Q3": "end of the 3rd quarter"}
LIVE_MOMENT_TYPES = ("takeaway", "sack", "missed_fg", "fourth_down", "big_play")
FOCUS_KEYS = ("why_people_are_talking", "stat_line", "trade", "rumor", "move", "moves_this_week", "status_update",
              "release", "headline", "cap", "your_player", "top_five_this_season", "last_game", "expected_to_be",
              "target", "top_four_by_record", "seeds_6_to_9", "standings", "bottom_five", "season_so_far")


def _seed_int(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def _subject_refs(subject: dict) -> list:
    """[{type, id}] for relationship lookup, from a subject dict (games are filled in by _fill_subjects)."""
    out = []
    kind = subject.get("type")
    if kind in ("player", "team", "coach") and subject.get("id") is not None:
        out.append({"type": kind, "id": subject["id"]})
    if subject.get("team_id") is not None and kind != "team":
        out.append({"type": "team", "id": subject["team_id"]})
    return out


def _assignment(kind, account, subject, facts, filed_team=None, angle=None, focus=None, subjects=None,
                topic_id=None, pick_games=None):
    return {
        "angle": angle,
        "kind": kind,
        "agent_id": account["agent_id"] if account else None,
        "handle": account["handle"] if account else None,
        "archetype": account["archetype"] if account else None,
        "team_id": filed_team if filed_team is not None else (
            (account or {}).get("team_id") if (account or {}).get("archetype") in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER") else None),
        "subject": subject,
        "window": WINDOWS.get(kind, ""),
        "facts": facts,
        "focus": list(focus or []),
        "pick_games": list(pick_games or []),
        "subjects": list(subjects) if subjects is not None else _subject_refs(subject),
        "topic_id": topic_id,
    }


def _team_id_by_label(teams, label):
    """A recorded team name/abbreviation -> its team id against ctx["teams"] (None when unknown)."""
    if label is None:
        return None
    wanted = " ".join(str(label).lower().split())
    for tid, team in teams.items():
        names = {str(team.get(k) or "").lower() for k in ("name", "abbr")}
        names.add(" ".join(f"{team.get('city') or ''} {team.get('name') or ''}".lower().split()))
        if wanted in names:
            return tid
    return None


def _fill_subjects_and_focus(assignments, ctx):
    """Game subjects get both teams; an assignment without a focus takes its packet's lead fact; a writer with a
    strong relationship (|trust| >= STRONG_TRUST) to one of the post's subjects gets that bias as its angle."""
    games = {g["id"]: g for g in ctx.get("recap_games", []) + ctx.get("preview_games", [])}
    strong = ctx.get("strong_relationships") or {}
    for a in assignments:
        if not a.get("subjects") and a["subject"].get("type") == "game" and a["subject"].get("id") in games:
            g = games[a["subject"]["id"]]
            a["subjects"] = [{"type": "team", "id": g["away_team_id"]}, {"type": "team", "id": g["home_team_id"]}]
        if a.get("agent_id") and strong and a.get("subjects"):
            for s in a["subjects"]:
                line = strong.get((a["agent_id"], s.get("type"), str(s.get("id"))))
                if line:
                    a["angle"] = line
                    a["relationship_angle"] = True
                    break
        if not a.get("focus") and isinstance(a.get("facts"), dict):
            for k in FOCUS_KEYS:
                v = a["facts"].get(k)
                if isinstance(v, dict):         # a player summary or a game packet: its headline line
                    v = " ".join(str(x) for x in (v.get("player"), v.get("season_so_far") or v.get("result")) if x)
                elif isinstance(v, list):
                    v = [" ".join(str(y) for y in (x.get("player"), x.get("season_so_far")) if y) if isinstance(x, dict) else x
                         for x in v[:2]]
                if v:
                    a["focus"] = list(v) if isinstance(v, list) else [v]
                    break


@functools.lru_cache(maxsize=1)
def load_desk_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _shareable(articles, fantasy_active) -> list:
    """Articles that get a share post: not the roundtable panel, not local beat articles (shared in their team's
    wakeup), and no fantasy articles outside the fantasy season."""
    out = []
    for art in articles:
        if art["persona"] in NO_SHARE_ARCHETYPES or art["persona"] == "LOCAL_BEAT_REPORTER" \
                or (art.get("slot") or "").startswith("Local Beat"):
            continue
        if not fantasy_active and ((art.get("slot") or "").startswith("Fantasy")
                                   or (art.get("article_type") or "").startswith("FANTASY_")
                                   or art.get("persona") in FANTASY_ARCHETYPES):
            continue
        out.append(art)
    return out


def league_topic_list(ctx, W=None) -> list:
    """The week's topics: stored ones (ctx["league_topics"], load_stored_topics) plus the week's own (week_topics),
    one per topic_id, hottest first."""
    W = W or Week(ctx)
    seen, out = set(), []
    for t in list(ctx.get("league_topics") or []) + week_topics(W):
        if t["topic_id"] not in seen:
            seen.add(t["topic_id"])
            out.append(t)
    return sorted(out, key=lambda t: (-t["heat"], t["topic_id"]))


def _has_live_data(ctx) -> bool:
    return any((ctx.get("game_scripts") or {}).get(g["id"], {}).get("scoring") for g in ctx["recap_games"])


def week_signals(ctx, W=None) -> dict:
    """The news signals week_mix scales by."""
    W = W or Week(ctx)
    recap, previews = ctx["recap_games"], ctx["preview_games"]
    tiers = ([t for _, t, _ in W.buzz_lines()] + [s["tier"] for s in W.team_stories()]) if recap else []
    week = W.week
    topics = league_topic_list(ctx, W)
    return {
        "week": week,
        "tier1": sum(1 for t in tiers if t == 1),
        "tier2": sum(1 for t in tiers if t == 2),
        "trades": len(ctx.get("trades") or []),
        "one_score": sum(1 for g in recap if g["home_score"] is not None and g["away_score"] is not None
                         and abs(g["home_score"] - g["away_score"]) <= ONE_SCORE_MARGIN),
        "playoff": int(any((g.get("game_type") or "REGULAR_SEASON") != "REGULAR_SEASON" for g in recap + previews)),
        "postseason": week > REGULAR_SEASON_FINAL_WEEK,
        "preseason": not recap,
        "previews": bool(previews),
        "stories": len(W.team_stories()) if recap else 0,
        "bad_games": len(W.bad_games()) if recap else 0,
        "live": _has_live_data(ctx),
        "call_phase": SEASON_CALL_WEEKS.get(week),
        "topics": collections.Counter(t["kind_hint"] for t in topics),
        "n_topics": len(topics),
    }


def event_counts(ctx, W=None) -> dict:
    """The event-driven kinds for the week (they count inside week_mix's total): one reaction per team that played,
    a share per shareable article in ctx["articles"], beat notes for teams with news (config `event_limits`), and the
    trade, move, injury and draft-board posts their own rules allow."""
    W = W or Week(ctx)
    cfg = load_desk_config()
    lo, hi = cfg.get("event_limits", {}).get("BEAT", [3, 8])
    played = {t for g in ctx["recap_games"] for t in (g["away_team_id"], g["home_team_id"])}
    trades = (ctx.get("trades") or [])[:TRADE_NEWS_MAX]
    notable = W.notable_moves()
    has_agg = any(a["archetype"] == AGGREGATOR_ARCHETYPE for a in ctx["accounts"])
    beat_news = {t for t_ in trades for t in t_["teams"]} | {m["team_id"] for m in notable} | \
                {t for t, injs in (ctx.get("injuries_by_team") or {}).items() if any(i.get("is_starter") for i in injs)}
    postseason = W.week > REGULAR_SEASON_FINAL_WEEK
    out = {
        "SHARE": len(ctx.get("articles") or []),
        "REACTION": len(played),
        "BEAT": max(lo, min(hi, len(beat_news))),
        "TRADE_NEWS": len(trades) + (1 if has_agg and trades else 0),
        "TRADE_RUMOR": min(len(ctx.get("rumors") or []), TRADE_RUMOR_MAX),
        "MOVE_NEWS": min(len(notable), MOVE_NEWS_MAX) + (1 if has_agg and notable and not trades else 0),
        "CAP_TAKE": 1 if ctx.get("moves") and W.cap_take_subject() else 0,
        "TRADE_REACTION": 2 * len(trades),
        "MOVE_REACTION": min(len(notable), MOVE_REACTION_MAX),
        "INJURY_SHOCK": min(2, sum(1 for i in ctx.get("injuries") or [] if i.get("is_key_injury"))),
        "DRAFT_BOARD": len(draft_board_subjects(ctx.get("draft_movers") or {},
                                                DRAFT_BOARD_POSTS["late" if postseason else "regular"])),
        "PLAYER_STATEMENT": min(len(ctx.get("player_statements") or []), LEAK_MAX_PER_WEEK),
    }
    return {k: v for k, v in out.items() if v}


def _mix_weights(sig, W, cfg, rng) -> dict:
    base = cfg["mix_weights"]
    week, post = sig["week"], sig["postseason"]
    recap = not sig["preseason"]
    applicable = {
        "PERFORMANCE": recap and bool(W.recap_lines),
        "BAD_GAME": recap and sig["bad_games"] > 0,
        "COMPARISON": bool(W.season_totals),
        "TEAM_STORY": sig["stories"] > 0,
        "STANDINGS": 3 <= week <= REGULAR_SEASON_FINAL_WEEK,
        "PREVIEW": sig["previews"],
        "MEME": recap or week >= 6,
        "PLAYER": post,
        "REPLY": True,
        "PICKS": sig["previews"],
        "HOT_TAKE": sig["n_topics"] > 0,
        "AWARD_RACE": sig["topics"].get("AWARD_RACE", 0) > 0,
        "POWER_TAKE": sig["topics"].get("POWER_TAKE", 0) > 0,
        "QUESTION": sig["n_topics"] > 0,
        "SEASON_CALL": bool(sig["call_phase"]),
        "LIVE_MOMENT": recap and sig["live"],
        "QUARTER_UPDATE": recap and sig["live"],
        "TRADE_IDEA": 2 <= week <= REGULAR_SEASON_FINAL_WEEK,
        "DRAFT_POSITION": recap and 6 <= week <= REGULAR_SEASON_FINAL_WEEK,
    }
    m = collections.defaultdict(lambda: 1.0)
    buzz = sig["tier1"] + 2 * sig["tier2"]
    m["PERFORMANCE"] = min(1.6, 1.0 + 0.12 * buzz) if buzz else 0.7          # no buzz -> fewer performances
    if sig["trades"]:
        m["TRADE_IDEA"] *= 1.5                                                 # a trade week -> more trade talk
    if week > TRADE_DEADLINE_WEEK:
        m["TRADE_IDEA"] *= 0.5
    if 12 <= week <= REGULAR_SEASON_FINAL_WEEK:                                # the stretch run
        for k, f in (("AWARD_RACE", 1.6), ("POWER_TAKE", 1.6), ("STANDINGS", 1.5), ("DRAFT_POSITION", 1.5)):
            m[k] *= f
    if week <= 4:
        m["AWARD_RACE"] *= 0.4
        m["POWER_TAKE"] *= 0.6
    if post:
        for k, f in (("PICKS", 1.5), ("SEASON_CALL", 1.5), ("AWARD_RACE", 1.3)):
            m[k] *= f
    if sig["preseason"]:
        m["PREVIEW"] *= 3.0
    m["LIVE_MOMENT"] *= 1.0 + 0.1 * sig["one_score"]
    jitter = float(cfg.get("mix_jitter", 0.25))
    out = {}
    for k in MIX_KINDS:                                   # fixed order: the jitter draws are the same every run
        j = rng.uniform(1 - jitter, 1 + jitter)
        w = float(base.get(k, 0)) * m[k] * j
        if applicable[k] and w > 0:
            out[k] = w
    return out


def _allocate(n: int, weights: dict, limits: dict) -> tuple:
    """Split n posts across kinds by weight, each kept within its [floor, ceiling]. Returns (counts, unplaced)."""
    counts = {k: min(limits.get(k, [0, n])[0], limits.get(k, [0, n])[1]) for k in weights}
    rem = n - sum(counts.values())
    while rem > 0:
        open_ = [k for k in weights if counts[k] < limits.get(k, [0, n + 999])[1]]
        if not open_:
            break
        tot = sum(weights[k] for k in open_)
        share = {k: rem * weights[k] / tot for k in open_}
        added = 0
        for k in open_:
            add = min(limits.get(k, [0, n + 999])[1] - counts[k], int(share[k]))
            counts[k] += add
            added += add
        if added == 0:
            for k in sorted(open_, key=lambda k: (-(share[k] - int(share[k])), k)):
                if added >= rem:
                    break
                counts[k] += 1
                added += 1
        rem -= added
    return counts, max(0, rem)


def week_mix(ctx: dict, seed=None) -> dict:
    """The week's post counts by kind (AGENT_WIRE_VOICE_PLAN §2.4, JR's D4).

    total = clamp(base + 4·tier-1 buzz + 6·tier-2 buzz + 3·trades + 2·one-score games + 5·playoff week, 80, 130)
    (config `mix_total`). Event-driven kinds (event_counts) count inside it; the rest is split over `mix_weights`
    scaled by the week's signals, with seeded ±`mix_jitter` and each kind kept within its `mix_limits` floor and
    ceiling. What the ceilings can't place goes to the week's spill kind (previews in the preseason, eliminated
    fans' player posts in the postseason, performances otherwise). Deterministic for a seed."""
    cfg = load_desk_config()
    W = Week(ctx)
    seed = seed if seed is not None else f"{W.season}-{W.week}"
    sig = week_signals(ctx, W)
    tc = cfg["mix_total"]
    total = (tc["base"] + tc["tier1"] * sig["tier1"] + tc["tier2"] * sig["tier2"] + tc["trade"] * sig["trades"]
             + tc["one_score"] * sig["one_score"] + tc["playoff"] * sig["playoff"])
    total = max(tc["min"], min(tc["max"], total))
    events = event_counts(ctx, W)
    weights = _mix_weights(sig, W, cfg, random.Random(f"mix|{seed}"))
    limits = {k: list(v) for k, v in cfg["mix_limits"].items()}
    # The news sets floors too: every buzz game gets its posts, the top storyline its voices, a historic game its
    # where-does-it-rank comparison.
    if ctx["recap_games"]:
        buzz = W.buzz_lines()
        need = sum(len(PLAYER_BUZZ_ROLES[t]) for _, t, _ in buzz)
        lo, hi = limits["PERFORMANCE"]
        limits["PERFORMANCE"][0] = max(lo, min(hi, need))
        lo, hi = limits["TEAM_STORY"]
        need = 0
        for st in W.team_stories():
            n = len(TEAM_STORY_ROLES[(st["tier"], st["positive"])])
            if need + n > hi:
                break
            need += n
        limits["TEAM_STORY"][0] = max(lo, need)
        lo, hi = limits["COMPARISON"]
        limits["COMPARISON"][0] = max(lo, min(hi, lo + sum(1 for _, t, _ in buzz if t == 2)))
    elif ctx["preview_games"]:
        # Preseason: every team's fans preview their opener.
        n = 2 * len(ctx["preview_games"])
        limits["PREVIEW"] = [max(limits["PREVIEW"][0], n), max(limits["PREVIEW"][1], n + limits["PREVIEW"][1])]
    flex, left = _allocate(max(0, total - sum(events.values())), weights, limits)
    if left:
        spill = next((k for k in (("PREVIEW",) if sig["preseason"] else ("PLAYER",) if sig["postseason"] else ())
                      + ("PERFORMANCE", "HOT_TAKE", "PREVIEW", "REPLY") if k in weights), None)
        if spill:
            flex[spill] += left
    mix = {**events, **{k: v for k, v in flex.items() if v}}
    # A week whose events and floors alone pass the ceiling: shrink the biggest weighted kinds (never events).
    while sum(mix.values()) > tc["max"]:
        above = [k for k in flex if mix.get(k, 0) > limits.get(k, [0, 0])[0]]
        cut = max(above or [k for k in flex if mix.get(k, 0) > 0], key=lambda k: (mix[k], k), default=None)
        if cut is None:
            break
        mix[cut] -= 1
        if not mix[cut]:
            del mix[cut]
    return mix


# --------------------------------------------------------------------------------------- picks and calls ---
def _pick_card(W, g) -> dict:
    a, h = g["away_team_id"], g["home_team_id"]
    return {"game_id": g["id"], "game": f"{W.teams[a]['abbr']} @ {W.teams[h]['abbr']}",
            "away": W.team_label(a), "home": W.team_label(h), "away_id": a, "home_id": h, "kickoff": g["game_time"]}


def plan_picks(ctx: dict, acc, n_writers: int, W=None) -> list:
    """PICKS assignments for this week's slate (D3: public posts): about n_writers writers (10-16 a week from
    week_mix), least-used this season first (ctx["pick_history"]), about half national voices, a third die-hards
    (each picks his own team's game first) and the rest bandwagon fans, ex-players and the stats account. Each gets
    `pick_games`: up to PICK_GAMES_MAX of this week's games, the slate's most exciting first, spread so writers don't
    all get the same games."""
    W = W or Week(ctx)
    slate = sorted(ctx["preview_games"], key=lambda g: (-(g["projected_excitement"] or 0), g["id"]))
    if not slate or n_writers <= 0:
        return []
    history = ctx.get("pick_history") or {}
    order = lambda a: (history.get(a["agent_id"], 0), acc.uses[a["agent_id"]], acc.order[a["agent_id"]])
    playing = [t for g in slate for t in (g["away_team_id"], g["home_team_id"])]
    fans = []
    for tid in playing:
        pool = sorted((a for a in acc.all() if a["archetype"] == "DIE_HARD_FAN" and a["team_id"] == tid), key=order)
        if pool:
            fans.append(pool[0])
    fans.sort(key=lambda a: (history.get(a["agent_id"], 0), playing.index(a["team_id"])))
    national = sorted((a for a in acc.all() if acc.role.get(a["agent_id"]) == "pundit"), key=order)
    other = sorted((a for a in acc.all() if acc.role.get(a["agent_id"]) in ("bandwagon", "former", "stats")), key=order)
    n_fan, n_nat = round(n_writers * 0.3), round(n_writers * 0.5)
    chosen = []

    def take(pool, k):
        got = 0
        for a in pool:
            if got >= k or len(chosen) >= n_writers:
                break
            if a not in chosen and acc.take(a):
                chosen.append(a)
                got += 1

    take(national, n_nat)
    take(fans, n_fan)
    take(other, n_writers - n_nat - n_fan)
    take(national + other + fans, n_writers - len(chosen))
    k = min(len(slate), 8)
    out = []
    for i, a in enumerate(chosen):
        n_games = 1 + _seed_int("pickcard", W.season, W.week, a["agent_id"]) % PICK_GAMES_MAX
        if a["archetype"] == "DIE_HARD_FAN":
            own = next((g for g in slate if a["team_id"] in (g["away_team_id"], g["home_team_id"])), None)
            games = ([own] if own else []) + [g for g in slate[:k] if g is not own][:min(2, n_games - 1)]
        else:
            idx = sorted({(2 * i + j) % k for j in range(n_games)})
            games = [slate[x] for x in idx]
        cards = [_pick_card(W, g) for g in games]
        facts = {"week": W.week,
                 "games": [f"{c['away']} ({W.record(c['away_id'])}) @ {c['home']} ({W.record(c['home_id'])}), "
                           f"{c['kickoff'] or 'Sun 1:00 PM'}" for c in cards],
                 "how_to_pick": "Call any 1-4 of these games: the winner, a score if you like, and how sure you are "
                                "(lock, lean or upset). Only these games."}
        subjects = [{"type": "team", "id": t} for c in cards for t in (c["away_id"], c["home_id"])]
        out.append(_assignment("PICKS", a, {"type": "slate", "id": None, "label": f"Week {W.week} picks"}, facts,
                               focus=[facts["games"][0]], subjects=subjects, pick_games=cards))
    return out


CALL_WORDS = {"MVP": "who wins MVP", "SUPER_BOWL": "who wins the Super Bowl", "OPOY": "Offensive Player of the Year",
              "DPOY": "Defensive Player of the Year", "ROY": "a Rookie of the Year", "PLAYOFF_TEAM": "a team that makes "
              "(or misses) the playoffs", "COACH_FIRED": "a head coach who gets fired"}


def plan_season_calls(ctx: dict, acc, n: int, W=None, topics=None) -> list:
    """SEASON_CALL assignments (graded at season's end) in the preseason (Week 1), midseason (Week 9) and the
    trade-deadline week: n writers (6-10 from week_mix), national voices first, each asked for two call types."""
    W = W or Week(ctx)
    phase = SEASON_CALL_WEEKS.get(W.week)
    if not phase or n <= 0:
        return []
    topics = topics if topics is not None else league_topic_list(ctx, W)
    strength = ctx.get("team_strength") or {}
    played = any(W.records.get(t, {}).get("w", 0) + W.records.get(t, {}).get("l", 0) for t in W.teams)
    field = sorted(W.teams, key=lambda t: ((W.win_pct(t), W.diff(t)) if played else
                                           (strength.get(t, 0), W.teams[t]["popularity"])), reverse=True)[:8]
    leaders = [f for t in topics if t["kind_hint"] == "AWARD_RACE" for f in t["focus"]][:6]
    pressure = [f for t in topics if t["topic_id"].startswith(("hot_seat", "coaches_fired")) for f in t["focus"]][:3]
    # Named entities a call may resolve to (social_writer.parse_post_json resolves the call's subject against these):
    # every team, so a playoff or Super Bowl call can name any of them; the award-race leaders the topics show; and
    # every head coach in his seat this season.
    players = {t["player_id"]: tidy_name(t["name"]) for t in (W.season_totals or {}).values() if t.get("player_id")}
    candidates = [{"type": "team", "id": t, "name": W.team_label(t)} for t in W.teams]
    seen = {(c["type"], c["id"]) for c in candidates}
    for topic in topics:
        for s in topic.get("subjects") or []:
            if s["type"] == "player" and s["id"] in players and ("player", s["id"]) not in seen:
                seen.add(("player", s["id"]))
                candidates.append({"type": "player", "id": s["id"], "name": players[s["id"]]})
    for c in ctx.get("coaches") or []:
        if ("coach", c["id"]) not in seen:
            seen.add(("coach", c["id"]))
            candidates.append({"type": "coach", "id": c["id"], "name": c["name"]})
    rot = ("pundit", "stats", "former", "pundit", "bandwagon", "pundit")
    out = []
    tries = 0
    while len(out) < n and tries < 4 * n:
        tries += 1
        role = rot[len(out) % len(rot)]
        account = (acc.stats() if role == "stats" else acc.former() if role == "former" else
                   acc.bandwagon() if role == "bandwagon" else None) or acc.pundit(prefer=HOT_TAKE_PUNDITS)
        if not account:
            break
        i = len(out)
        calls = [SEASON_CALL_TYPES[(2 * i) % len(SEASON_CALL_TYPES)], SEASON_CALL_TYPES[(2 * i + 1) % len(SEASON_CALL_TYPES)]]
        facts = {"call_phase": phase, "season": W.season,
                 "contenders": [W.team_with_record(t) if played else W.team_label(t) for t in field],
                 "candidates": candidates,
                 "grounding": "A prediction graded at season's end: name the player, team or coach plainly."}
        if leaders:
            facts["leaders_so_far"] = leaders
        if pressure:
            facts["coaches_under_pressure"] = pressure
        out.append(_assignment("SEASON_CALL", account, {"type": "season", "id": W.season,
                                                        "label": f"{phase.capitalize()} calls: {', '.join(calls)}"},
                               facts, focus=[f"Your call: {CALL_WORDS[c]}" for c in calls], subjects=[]))
        out[-1]["call_types"] = calls
    return out


# ------------------------------------------------------------------------------------------ league topics ---
TOPIC_ROLES = {
    "HOT_TAKE": ("pundit", "former", "pundit", "bandwagon"),
    "AWARD_RACE": ("stats", "pundit", "pundit", "bandwagon"),
    "POWER_TAKE": ("pundit", "bandwagon", "fan", "pundit"),
    "QUESTION": ("aggregator", "pundit", "fan", "bandwagon"),
}


def plan_topic_posts(kind: str, n: int, topics: list, acc, W) -> list:
    """n posts of a league-topic kind. AWARD_RACE and POWER_TAKE take topics with that hint; HOT_TAKE and QUESTION
    take any, their own hint first. Hottest topics first, at most ceil(n / topics) posts per topic. The topic's
    facts are the post's focus."""
    if kind in ("AWARD_RACE", "POWER_TAKE"):
        pool = [t for t in topics if t["kind_hint"] == kind]
    else:
        pool = sorted(topics, key=lambda t: (t["kind_hint"] != kind, -t["heat"], t["topic_id"]))
    if not pool or n <= 0:
        return []
    per = max(2, -(-n // len(pool)))
    used = collections.Counter()
    rot = TOPIC_ROLES[kind]
    out, i = [], 0
    while len(out) < n and i < 4 * n + len(pool):
        t = pool[i % len(pool)]
        i += 1
        if used[t["topic_id"]] >= per:
            continue
        role = rot[len(out) % len(rot)]
        team = next((s["id"] for s in t["subjects"] if s["type"] == "team"), None)
        account = (acc.stats() if role == "stats" else acc.aggregator() if role == "aggregator" else
                   acc.former() if role == "former" else acc.bandwagon() if role == "bandwagon" else
                   acc.fan(team) if role == "fan" and team in W.teams else None)
        account = account or acc.pundit(prefer=HOT_TAKE_PUNDITS if kind == "HOT_TAKE" else ()) or acc.bandwagon()
        if not account:
            break
        used[t["topic_id"]] += 1
        facts = {"topic": t["label"], "through_week": min(W.week - 1, REGULAR_SEASON_FINAL_WEEK)}
        filed = team if account["archetype"] == "DIE_HARD_FAN" else None
        out.append(_assignment(kind, account, {"type": "topic", "id": t["topic_id"], "label": t["label"], "team_id": team},
                               facts, filed_team=filed, focus=t["focus"][:3], subjects=t["subjects"],
                               topic_id=t["topic_id"]))
    return out


# --------------------------------------------------------------------------------------- live and fan focus ---
def _clock_secs(clock) -> int:
    try:
        m, s = str(clock).split(":")
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return 0


def _when(period, clock):
    return (PERIOD_ORDER.get(period, 9), -_clock_secs(clock))


def _score_then(script, period, clock, inclusive=True) -> str:
    """The running score at a moment, from the script's scoring (abbreviations), never the final."""
    t = _when(period, clock)
    last = None
    for s in script.get("scoring", []):
        st = _when(s["period"], s["clock"])
        if st < t or (inclusive and st == t):
            last = s
    teams = list((script.get("quarter_scores") or {}).keys()) or list((last or {}).get("score_after", {}).keys())
    after = (last or {}).get("score_after") or {k: 0 for k in teams}
    return ", ".join(f"{k} {after.get(k, 0)}" for k in teams) if teams else "0-0"


def live_moments(script) -> list:
    """Plays a live post can be about, in game order: scores that tied the game, took the lead or put it away, any
    touchdown, and the recorded takeaways, sacks, missed kicks, fourth downs and big plays."""
    out = []
    for s in script.get("scoring", []):
        tags = set(s.get("tags") or ())
        if (s.get("points") or 0) >= 3 and (tags & {"go-ahead", "lead for good", "tying", "set up by a takeaway"}
                                            or (s.get("points") or 0) >= 6):
            out.append({"period": s["period"], "clock": s["clock"], "team": s["team"], "text": s["play"], "scoring": True})
    for m in script.get("key_moments", []):
        if m.get("type") in LIVE_MOMENT_TYPES:
            out.append({"period": m["period"], "clock": m.get("clock"), "team": m.get("team"), "text": m["text"],
                        "scoring": False})
    return sorted(out, key=lambda m: _when(m["period"], m["clock"]))


def reaction_focus(W, g, tid, salt) -> list:
    """One thing a die-hard's reaction is about (§2.7 rule 2): a key moment, a player's line or a coaching call from
    the game's script and lines, seeded per fan so two fans of a team don't get the same one."""
    script = W.ctx.get("game_scripts", {}).get(g["id"]) or {}
    ab = W.teams[tid]["abbr"]
    opp = g["away_team_id"] if g["home_team_id"] == tid else g["home_team_id"]
    opts = [f"{m['period']} {m['clock']}: {m['text']}" for m in live_moments(script)]
    opts += [a["text"] for a in script.get("adjustments", []) if a.get("text")]
    opts += [f"your best player: {x}" for x in W.leaders(g, tid, 1)]
    opts += [f"their best player: {x}" for x in W.leaders(g, opp, 1)]
    mine = [o for o in opts if ab in o or o.startswith("your")]
    pool = opts if len(mine) < 2 else mine + [o for o in opts if o.startswith("their")]
    if not pool:
        return []
    return [pool[_seed_int(salt) % len(pool)]]


def _game_name(W, g) -> str:
    return f"{W.teams[g['away_team_id']]['name']} @ {W.teams[g['home_team_id']]['name']}"


def plan_live_moments(ctx, acc, n, W) -> list:
    """LIVE_MOMENT (§2.7 rule 3, D8): fans and pundits on one play from a recap game's script, posted during the game
    (`live` carries the period and clock) and knowing only the score at that moment. Most exciting games first, one
    play per game per round; authors rotate: a fan of the team that made the play, a fan of the other side, a pundit."""
    games = [g for g in sorted(ctx["recap_games"], key=lambda g: -(g["excitement_index"] or 0))
             if (ctx.get("game_scripts") or {}).get(g["id"], {}).get("scoring")]
    per_game = {g["id"]: live_moments(ctx["game_scripts"][g["id"]]) for g in games}
    abbr_to_id = {v["abbr"]: t for t, v in W.teams.items()}
    out, rnd = [], 0
    while len(out) < n and games and rnd < 12:
        for g in games:
            if len(out) >= n:
                break
            moments = per_game[g["id"]]
            if not moments:
                continue
            m = moments[(rnd * 3 + _seed_int(g["id"])) % len(moments)]
            actor = abbr_to_id.get(m["team"])
            other = g["home_team_id"] if actor == g["away_team_id"] else g["away_team_id"]
            role = len(out) % 3
            account = (acc.fan(actor) if role == 0 and actor else acc.fan(other) if role == 1 else None) \
                or acc.pundit(prefer=HOT_TAKE_PUNDITS) or acc.bandwagon()
            if not account:
                return out
            script = ctx["game_scripts"][g["id"]]
            facts = {"game": _game_name(W, g), "when": f"{m['period']} {m['clock']}", "play": m["text"],
                     "score_at_the_time": _score_then(script, m["period"], m["clock"]),
                     "note": "Posted live, right after this play: you don't know how the game ends."}
            filed = account["team_id"] if account["archetype"] == "DIE_HARD_FAN" else None
            if filed:
                facts["your_team"] = W.team_label(filed)
            a = _assignment("LIVE_MOMENT", account, {"type": "game", "id": g["id"], "label": f"{_game_name(W, g)}, {m['period']} {m['clock']}"},
                            facts, filed_team=filed, focus=[f"{m['period']} {m['clock']}: {m['text']}"])
            a["live"] = {"game_id": g["id"], "period": m["period"], "clock": m["clock"]}
            out.append(a)
        rnd += 1
    return out


def plan_quarter_updates(ctx, acc, n, W) -> list:
    """QUARTER_UPDATE (§2.7 rule 3, D8): a beat writer of a team in a recap game posts the score at the end of the 1st
    quarter, halftime or the end of the 3rd, with that quarter's scores and moments; most exciting games first."""
    out = []
    games = [g for g in sorted(ctx["recap_games"], key=lambda g: -(g["excitement_index"] or 0))
             if (ctx.get("game_scripts") or {}).get(g["id"], {}).get("scoring")]
    for i, g in enumerate(games):
        if len(out) >= n:
            break
        sides = (g["away_team_id"], g["home_team_id"]) if i % 2 else (g["home_team_id"], g["away_team_id"])
        account, tid = None, None
        for t in sides:
            account = acc.beat(t)
            if account:
                tid = t
                break
        if not account:
            continue
        script = ctx["game_scripts"][g["id"]]
        period = ("Q1", "Q2", "Q3")[_seed_int("quarter", g["id"]) % 3]
        upto = PERIOD_ORDER[period]
        qs = script.get("quarter_scores") or {}
        score = ", ".join(f"{k} {sum((v or [])[:upto])}" for k, v in qs.items()) if qs else _score_then(script, period, "0:00")
        in_q = lambda x: x.get("period") == period
        facts = {"game": _game_name(W, g), "your_team": W.team_label(tid), "end_of": QUARTER_NAMES[period],
                 "score": score,
                 "scoring_this_quarter": [f"{s['clock']} {s['team']}: {s['play']}" for s in script.get("scoring", []) if in_q(s)][:4],
                 "moments_this_quarter": [f"{m.get('clock')} {m.get('team')}: {m['text']}" for m in script.get("key_moments", [])
                                          if in_q(m) and m.get("type") in LIVE_MOMENT_TYPES][:2],
                 "note": "Posted live at the end of the quarter: you don't know how the game ends."}
        a = _assignment("QUARTER_UPDATE", account, {"type": "game", "id": g["id"], "label": f"{_game_name(W, g)}: {QUARTER_NAMES[period]}"},
                        facts, filed_team=tid, focus=[f"{QUARTER_NAMES[period]}: {score}"])
        a["live"] = {"game_id": g["id"], "period": period, "clock": "0:00"}
        out.append(a)
    return out


def team_needs(W, tid) -> list:
    """A team's biggest needs: stored (ctx["team_needs"], §2.16) or, without them, positions of injured starters and
    its weakest offensive position by season production."""
    stored = (W.ctx.get("team_needs") or {}).get(tid)
    if stored:
        return list(stored)
    out = [norm_pos(i["position"]) for i in W.team_injuries(tid) if i.get("is_starter")]
    ranks = []
    for pos in ("QB", "RB", "WR", "TE"):
        best = W.team_best(tid, pos)
        leaders = sorted((t for t in W.season_totals.values() if norm_pos(t["position"]) == pos),
                         key=lambda t: t["fantasy_points"], reverse=True)
        rank = next((i for i, t in enumerate(leaders, 1) if t is best), len(leaders) + 1)
        ranks.append((rank, pos))
    for _, pos in sorted(ranks, reverse=True):
        if pos not in out:
            out.append(pos)
    return out[:3]


def plan_trade_ideas(ctx, acc, n, W) -> list:
    """TRADE_IDEA (§2.7 rule 3): fans (and some pundits) propose a trade from a team's needs, last week's losers
    first. A made-up proposal, never news; no dollar figures."""
    losers = []
    for g in ctx["recap_games"]:
        _, l = W.winner_loser(g)
        if l:
            losers.append(l)
    order = sorted(set(losers), key=lambda t: (-W.teams[t]["popularity"], t)) + \
        sorted((t for t in W.teams if t not in losers), key=lambda t: (-W.teams[t]["popularity"], t))
    out = []
    for tid in order:
        if len(out) >= n:
            break
        needs = team_needs(W, tid)
        if not needs:
            continue
        account = acc.fan(tid) if len(out) % 3 != 2 else None
        account = account or acc.pundit()
        if not account:
            break
        facts = {"team": W.team_with_record(tid), "biggest_needs": needs,
                 "trade_deadline": f"Week {TRADE_DEADLINE_WEEK}" + (" (passed: an offseason wish)" if W.week > TRADE_DEADLINE_WEEK else ""),
                 "grounding": "Your own made-up trade idea, not news: no trade happened. No dollar figures."}
        out.append(_assignment("TRADE_IDEA", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: trade idea"},
                               facts, filed_team=tid, focus=[f"{W.team_label(tid)} need help at {needs[0]}"]))
    return out


def plan_draft_position(ctx, acc, n, W) -> list:
    """DRAFT_POSITION (§2.7 rule 3): die-hards of teams that lost last week and sit in the bottom ten by record, on
    where their team would pick (by record, no tiebreakers)."""
    order = sorted(W.teams, key=lambda t: (W.win_pct(t), W.diff(t)))
    losers = set()
    for g in ctx["recap_games"]:
        _, l = W.winner_loser(g)
        if l:
            losers.add(l)
    out = []
    for rank, tid in enumerate(order[:10], 1):
        if len(out) >= n:
            break
        if tid not in losers:
            continue
        account = acc.fan(tid)
        if not account:
            continue
        facts = {"team": W.team_with_record(tid), "last_game": W.last_result(tid),
                 "pick_if_the_season_ended_today": ordinal(rank),
                 "worst_records": [W.team_with_record(t) for t in order[:5]]}
        out.append(_assignment("DRAFT_POSITION", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: draft position"},
                               facts, filed_team=tid,
                               focus=[f"would pick {ordinal(rank)} if the season ended today (by record, no tiebreakers)"]))
    return out


def _slant_team_id(account, W):
    """The old team an ex-player (or ex-player pundit) leans toward, as a team id, else None."""
    if not account:
        return None
    if account.get("archetype") == "FORMER_PLAYER":
        return account.get("team_id")
    abbr = FORMER_TEAM_SLANT.get(account.get("archetype"))
    if abbr:
        return next((t for t, v in W.teams.items() if v["abbr"] == abbr), None)
    return None


def _add_slants(assignments, acc, W):
    for a in assignments:
        tid = _slant_team_id(acc.by_id.get(a.get("agent_id")), W)
        if tid in W.teams:
            a["slant_team"] = W.team_label(tid)
            if a["archetype"] == "FORMER_PLAYER":
                a["former_position"] = former_position(acc.by_id[a["agent_id"]])


def expected_articles(week: int) -> list:
    """Placeholder articles for a dry run of a week whose slates aren't written yet: the national matrix
    (Hero, Slots 1–8, Standalone) plus the Mu Fantasy slate. Authors are chosen when the slates run."""
    slots = ["Hero Slot"] + [f"Slot {i}" for i in range(1, 9)] + ["Standalone"]
    out = [{"id": None, "persona": None, "team_id": None, "headline": f"{s} article (not written yet)",
            "excerpt": None, "slot": s, "article_type": None} for s in slots]
    if fantasy_phase(week):
        from newsroom.fantasy_desk import fantasy_slate
        for s in fantasy_slate(week):
            out.append({"id": None, "persona": None, "team_id": None,
                        "headline": f"{s.get('article_type')} (not written yet)", "excerpt": None,
                        "slot": s.get("slot"), "article_type": s.get("article_type")})
    return out


PLAN_BUCKETS = ("SHARE", "MOVES", "PLAYER_STATEMENT", "REACTION", "PERFORMANCE", "BAD_GAME", "COMPARISON", "TEAM_STORY",
                "LIVE", "PLAYER",
                "BEAT", "STANDINGS", "TOPICS", "PICKS", "SEASON_CALL", "TRADE_IDEA", "DRAFT_POSITION", "PREVIEW", "MEME",
                "REPLY")
REPLY_MAX = 20                # sum of REPLY_SPREAD: replies plan_replies can place


def plan_social_week(ctx: dict, use_expected_articles: bool = False, seed=None) -> dict:
    """Plan one week's Agent Wire posts. Returns {"season", "week", "phase", "assignments", "counts", "mix", "notes"}.

    week_mix sets how many posts of each kind the week gets; the planner then fills each kind (accounts run out or
    subjects are missing -> fewer), and any room left goes to performances (regular season), eliminated fans'
    player posts (postseason) or previews (preseason)."""
    W = Week(ctx)
    season, week = W.season, W.week
    fantasy_active = fantasy_phase(week) is not None
    acc = Accounts(ctx["accounts"], seed=f"{season}-{week}", fantasy_enabled=fantasy_active)
    notes = []
    plan = collections.OrderedDict((k, []) for k in PLAN_BUCKETS)

    recap = sorted(ctx["recap_games"], key=lambda g: g["excitement_index"] or 0, reverse=True)
    previews = sorted(ctx["preview_games"], key=lambda g: g["projected_excitement"] or 0, reverse=True)
    postseason = week > REGULAR_SEASON_FINAL_WEEK
    phase = "preseason" if not recap else "postseason" if postseason else "regular season"
    played = [t for g in recap for t in (g["away_team_id"], g["home_team_id"])]

    articles = ctx["articles"]
    if not articles and use_expected_articles:
        articles = expected_articles(week)
        notes.append(f"No articles stored for Week {week} yet; planned placeholder shares (one per slate slot). "
                     "Their accounts are picked when the slates have run.")
    articles = _shareable(articles, fantasy_active)
    mix = week_mix({**ctx, "articles": articles}, seed if seed is not None else f"{season}-{week}")
    total = sum(mix.values())
    topics = league_topic_list(ctx, W)

    # 1. Article shares: one per article published this week, from its writer's account.
    for art in articles:
        account = None
        if art["persona"]:
            account = acc.by_archetype_uncapped(SHARE_ACCOUNT_ARCHETYPE.get(art["persona"], art["persona"]))
        plan["SHARE"].append(_assignment("SHARE", account,
                                         {"type": "article", "id": art["id"], "label": art["headline"]},
                                         {k: art[k] for k in ("headline", "excerpt", "article_type", "slot")},
                                         filed_team=art["team_id"]))

    # 2. Reactions: one die-hard post for each team that played, planned early so the fan pools are never used up
    # first. Each gets one focus (a moment, a player's line, a coaching call); the score and records stay out of the
    # packet's lead (§2.7 rule 2: the result stays as background).
    for g in recap:
        for tid in (g["away_team_id"], g["home_team_id"]):
            account = acc.fan(tid)
            if account:
                facts = W.game_facts(g, tid)
                facts.pop("records_after_game", None)
                plan["REACTION"].append(_assignment("REACTION", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                                    facts, filed_team=tid,
                                                    focus=reaction_focus(W, g, tid, f"{account['agent_id']}|{g['id']}")))

    # 3. Trades (Phase 8): insiders break last week's executed trades, then at most TRADE_RUMOR_MAX rumors. The
    # label-first aggregator (D7) echoes the biggest trade, or the biggest move when there is no trade.
    for t in ctx.get("trades", [])[:TRADE_NEWS_MAX]:
        account = acc.insider()
        if not account:
            break
        a, b = t["teams"]
        plan["MOVES"].append(_assignment("TRADE_NEWS", account, {"type": "trade", "id": t["id"], "team_id": a,
                                                                  "label": f"{W.teams[a]['abbr']}-{W.teams[b]['abbr']} trade"},
                                         W.trade_facts(t), filed_team=a, subjects=[{"type": "team", "id": x} for x in t["teams"]]))
    for r in ctx.get("rumors", [])[:TRADE_RUMOR_MAX]:
        account = acc.insider(r.get("insider_key") if r.get("fresh") else None)
        if not account:
            break
        a, b = r["teams"]
        plan["MOVES"].append(_assignment("TRADE_RUMOR", account, {"type": "rumor", "id": r["id"], "team_id": a,
                                                                   "label": f"{W.teams[a]['abbr']}-{W.teams[b]['abbr']} trade talks"},
                                         W.rumor_facts(r), filed_team=a, subjects=[{"type": "team", "id": x} for x in r["teams"]]))
    # 3c. Player statements (COACH_PLAYER_AGENT_LOOPS_PLAN §2.5): the week's public contract leaks, one post each,
    # capped at LEAK_MAX_PER_WEEK. An insider reports it; a pundit when no insider is left under the cap. The facts
    # are the recorded statement only ("a statement, not a signing"; no new figures, no trade or deal claim).
    for stmt in (ctx.get("player_statements") or [])[:LEAK_MAX_PER_WEEK]:
        account = acc.insider() or acc.pundit()
        if not account:
            continue
        tid = _team_id_by_label(W.teams, stmt.get("team"))
        pos, team_label = stmt.get("position") or "", stmt.get("team") or ""
        label = f"{stmt['name']} ({pos}, {team_label})" if pos else f"{stmt['name']} ({team_label})"
        facts = {"player": stmt["name"], "position": pos or None, "team": stmt.get("team"),
                 "statement": stmt["statement"],
                 "grounding": "A STATEMENT, not a signing: he said he wants a deal. Never say a deal was made, "
                              "agreed or signed, and use only the figures the statement itself contains."}
        for k in ("offered", "asked"):
            if stmt.get(k):
                facts[k] = stmt[k]
        plan["PLAYER_STATEMENT"].append(_assignment(
            "PLAYER_STATEMENT", account, {"type": "player", "id": stmt.get("player_id"), "team_id": tid,
                                          "label": label},
            facts, filed_team=tid, focus=[stmt["statement"]]))
    for m in W.notable_moves()[:MOVE_NEWS_MAX]:
        account = acc.insider()
        if not account:
            break
        plan["MOVES"].append(_assignment("MOVE_NEWS", account, {"type": "player", "id": m["player_id"], "team_id": m["team_id"],
                                                                 "label": f"{m['player_name']} ({m['type'].lower().replace('_', '-')}, {W.teams[m['team_id']]['abbr']})"},
                                         W.move_facts(m), filed_team=m["team_id"]))
    trades, notable = ctx.get("trades") or [], W.notable_moves()
    if trades or notable:
        account = acc.aggregator()
        if account and trades:
            t = trades[0]
            a, b = t["teams"]
            plan["MOVES"].append(_assignment("TRADE_NEWS", account, {"type": "trade", "id": t["id"], "team_id": a,
                                                                      "label": f"{W.teams[a]['abbr']}-{W.teams[b]['abbr']} trade"},
                                             W.trade_facts(t), filed_team=a, subjects=[{"type": "team", "id": x} for x in t["teams"]]))
        elif account:
            m = notable[0]
            plan["MOVES"].append(_assignment("MOVE_NEWS", account, {"type": "player", "id": m["player_id"], "team_id": m["team_id"],
                                                                     "label": f"{m['player_name']} ({m['type'].lower().replace('_', '-')}, {W.teams[m['team_id']]['abbr']})"},
                                             W.move_facts(m), filed_team=m["team_id"]))
    cap_subject = W.cap_take_subject() if ctx.get("moves") else None
    if cap_subject:
        account = acc.pundit()
        if account:
            tid, release = cap_subject
            plan["MOVES"].append(_assignment("CAP_TAKE", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: cap squeeze"},
                                             W.cap_take_facts(tid, release), filed_team=tid))

    # 3b. Die-hards on marquee injuries, the week's biggest moves and each trade (appended to reactions).
    for inj in [i for i in ctx.get("injuries", []) if i.get("is_key_injury")][:2]:
        tid = inj["team_id"]
        account = acc.fan(tid)
        if account:
            plan["REACTION"].append(_assignment("INJURY_SHOCK", account,
                                                {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: {inj['player_name']} Injury Shock"},
                                                W.injury_shock_facts(tid, inj), filed_team=tid,
                                                focus=[f"{inj['player_name']} ({inj['position']}): {inj['duration']} ({inj['injury_type']})"]))
    for m in notable[:MOVE_REACTION_MAX]:
        account = acc.fan(m["team_id"])
        if account:
            plan["REACTION"].append(_assignment("MOVE_REACTION", account,
                                                {"type": "player", "id": m["player_id"], "team_id": m["team_id"],
                                                 "label": f"{W.team_label(m['team_id'])}: {m['player_name']} {m['type'].lower().replace('_', '-')}"},
                                                W.move_reaction_facts(m), filed_team=m["team_id"]))
    for t in trades[:TRADE_NEWS_MAX]:
        for tid in t["teams"]:
            account = acc.fan(tid)
            if account:
                plan["REACTION"].append(_assignment("TRADE_REACTION", account,
                                                    {"type": "trade", "id": t["id"], "team_id": tid,
                                                     "label": f"{W.team_label(tid)}: trade reaction"},
                                                    W.trade_reaction_facts(t, tid), filed_team=tid,
                                                    subjects=[{"type": "team", "id": x} for x in t["teams"]]))

    # 4. Beat notes (event-driven count): teams in last week's trades, teams with news, the most exciting games, then
    # the most popular teams that played.
    news_teams = [x for t_ in trades for x in t_["teams"]] + [m["team_id"] for m in notable] + \
        [t for t, injs in (ctx.get("injuries_by_team") or {}).items() if any(i.get("is_starter") for i in injs)]
    beat_teams = []
    for t in news_teams + played + sorted(W.teams, key=lambda t: W.teams[t]["popularity"], reverse=True):
        if t not in beat_teams and t in W.teams:
            beat_teams.append(t)
    for tid in beat_teams:
        if len(plan["BEAT"]) >= mix.get("BEAT", 0):
            break
        account = acc.beat(tid)
        if not account:
            continue
        team_starter_injuries = [inj for inj in W.team_injuries(tid) if inj.get("is_starter")]
        if team_starter_injuries:
            top_inj = team_starter_injuries[0]
            plan["BEAT"].append(_assignment("BEAT_INJURY", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: {top_inj['player_name']} Injury"},
                                            W.beat_injury_facts(tid, top_inj), filed_team=tid))
        elif any(m["notable"] for m in W.team_moves(tid)) or W.team_trades(tid):
            plan["BEAT"].append(_assignment("BEAT_MOVE", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: roster moves"},
                                            W.beat_move_facts(tid), filed_team=tid))
        else:
            plan["BEAT"].append(_assignment("BEAT", account, {"type": "team", "id": tid, "label": W.team_label(tid)},
                                            W.beat_facts(tid), filed_team=tid))

    # 5. Picks and season calls (D3: public posts, graded later).
    plan["PICKS"] = plan_picks(ctx, acc, mix.get("PICKS", 0), W)
    plan["SEASON_CALL"] = plan_season_calls(ctx, acc, mix.get("SEASON_CALL", 0), W, topics)

    # 6. Player performances from the recap week: buzz first (tier 1 three posts, tier 2 four plus a ranking
    # comparison; the aggregator adds a label-first post on the week's biggest game), then the top full-PPR offensive
    # lines with big defensive or kicking games on the 2nd, 5th, 8th ... ordinary post; the stats account takes one.
    perf_n = mix.get("PERFORMANCE", 0)
    top_lines = sorted(W.recap_lines, key=lambda l: (l["fantasy_points"], l["popularity"]), reverse=True)
    seen_players = set()
    pattern = (["fantasy", "pundit", "former", "bandwagon", "former", "pundit", "bandwagon"]
               if fantasy_active else
               ["pundit", "former", "bandwagon", "former", "pundit", "bandwagon"])

    def pick(role, team_id=None, pos=None, avoid=()):
        """An account for a buzz post by role, never one already posting about the same subject."""
        if not fantasy_active and role == "fantasy":
            role = "pundit"
        for _ in range(4):
            account = (acc.fantasy() if role == "fantasy" else
                       acc.former([team_id] if team_id else [], pos) if role == "former" else
                       acc.bandwagon() if role == "bandwagon" else
                       acc.aggregator() if role == "aggregator" else
                       acc.stats() if role == "stats" else
                       acc.archetype("NFL_MEMES") if role == "meme" else None)
            if account is None:
                account = acc.pundit(prefer=HOT_TAKE_PUNDITS if role == "meme" else PUNDIT_AFFINITY.get(pos, ()))
            if account is None or account["agent_id"] not in avoid:
                return account
        return None

    def with_angle(a, role, roles, i, good=True):
        if not fantasy_active and role == "fantasy":
            role = "pundit"
        options = BUZZ_ANGLES.get((role, good), ())
        if options:
            a["angle"] = options[roles[:i].count(role) % len(options)]
        return a

    buzz = W.buzz_lines() if recap else []
    ranking_posts = []
    for n_buzz, (l, tier, reasons) in enumerate(buzz):
        roles = PLAYER_BUZZ_ROLES[tier]
        if not fantasy_active and tier == 2:
            roles = ("pundit", "former", "bandwagon", "pundit")
        if n_buzz == 0 and any(a["archetype"] == AGGREGATOR_ARCHETYPE for a in acc.all()):
            roles = roles + ("aggregator",)
        if len(plan["PERFORMANCE"]) + len(roles) > perf_n:
            continue
        key = l["player_id"] or (l["short_name"], l["team_id"])
        seen_players.add(key)
        used = set()
        subject = {"type": "player", "id": l["player_id"], "team_id": l["team_id"],
                   "label": f"{l['name']} ({l['position']}, {W.teams[l['team_id']]['abbr']})"}
        for i, role in enumerate(roles):
            account = pick(role, l["team_id"], l["position"], used)
            if not account:
                continue
            used.add(account["agent_id"])
            a = _assignment("PERFORMANCE", account, subject, W.buzz_facts(l, tier, reasons), focus=reasons[:2])
            a["buzz_tier"] = tier
            plan["PERFORMANCE"].append(with_angle(a, role, roles, i))
        if tier == 2:
            account = pick("pundit", pos=l["position"], avoid=used)
            if account:
                a = _assignment("COMPARISON", account, {**subject, "label": f"Where {l['name']}'s game ranks"},
                                W.ranking_facts(l, reasons), focus=reasons[:1])
                a["buzz_tier"] = tier
                ranking_posts.append(a)

    offense_queue = [l for l in top_lines if norm_pos(l["position"]) in OFFENSE_POSITIONS]
    defense_queue = W.defense_performances()[:DEF_PERFORMANCE_MAX] if recap else []
    stats_used = [False]

    def next_line():
        n = sum(1 for a in plan["PERFORMANCE"] if not a.get("buzz_tier")) + 1
        if defense_queue and (n % DEF_PERFORMANCE_EVERY == 2 or not offense_queue):
            return defense_queue.pop(0), True
        if offense_queue:
            return offense_queue.pop(0), False
        return None, False

    def add_performances(limit):
        while len(plan["PERFORMANCE"]) < limit:
            l, defensive = next_line()
            if l is None:
                break
            key = l["player_id"] or (l["short_name"], l["team_id"])
            if key in seen_players:
                continue
            seen_players.add(key)
            pos = l["position"]
            affinity = "K" if defensive and l.get("kick_fga") else "DEF" if defensive else pos
            who = pattern[len(plan["PERFORMANCE"]) % len(pattern)]
            account = None
            if not stats_used[0]:
                account = acc.stats()
                stats_used[0] = True
            account = account or (acc.fantasy() if who == "fantasy" else
                                  acc.former([l["team_id"]], pos) if who == "former" else
                                  acc.bandwagon() if who == "bandwagon" else None)
            if account is None:
                account = acc.pundit(prefer=PUNDIT_AFFINITY.get(affinity, ()))
            if account is None:
                continue
            facts = W.defense_performance_facts(l) if defensive else W.performance_facts(l)
            plan["PERFORMANCE"].append(_assignment("PERFORMANCE", account,
                                                   {"type": "player", "id": l["player_id"], "team_id": l["team_id"], "label": f"{l['name']} ({pos or affinity}, {W.teams[l['team_id']]['abbr']})"},
                                                   facts))

    add_performances(perf_n)

    # 6b. Bad games: bandwagon fans and the meme page clown the flop, hot-take pundits call it out, an ex-player weighs in.
    if recap:
        bad_pattern = ["bandwagon", "bandwagon", "pundit", "meme", "bandwagon", "former", "pundit", "bandwagon"]
        for i, (l, avg) in enumerate(W.bad_games()[:mix.get("BAD_GAME", 0)]):
            who = bad_pattern[i % len(bad_pattern)]
            account = (acc.bandwagon() if who == "bandwagon" else
                       acc.archetype("NFL_MEMES") if who == "meme" else
                       acc.former([], l["position"]) if who == "former" else None)
            if account is None:
                account = acc.pundit(prefer=HOT_TAKE_PUNDITS)
            if account:
                plan["BAD_GAME"].append(_assignment("BAD_GAME", account,
                                                    {"type": "player", "id": l["player_id"], "team_id": l["team_id"], "label": f"{l['name']} ({l['position']}, {W.teams[l['team_id']]['abbr']})"},
                                                    W.bad_game_facts(l, avg)))

    # 6c. Comparisons: die-hards (half), ex-players (three in ten) and the stats account or pundits on a position.
    comp_n = mix.get("COMPARISON", 0)
    if W.season_totals and comp_n:
        fan_n, former_n = comp_n // 2, (comp_n * 3) // 10
        fan_teams = []
        for t in played + sorted(W.teams, key=lambda t: W.teams[t]["popularity"], reverse=True):
            if t not in fan_teams:
                fan_teams.append(t)
        for tid in fan_teams:
            if len(plan["COMPARISON"]) >= fan_n:
                break
            mine = W.team_best(tid)
            if not mine:
                continue
            last, nxt = W.team_games(tid)
            g = last or nxt
            rival = (g["away_team_id"] if g["home_team_id"] == tid else g["home_team_id"]) if g else None
            theirs = W.team_best(rival, mine["position"]) if rival else None
            account = acc.fan(tid) if theirs else None
            if account:
                plan["COMPARISON"].append(_assignment("COMPARISON", account,
                                                      {"type": "player", "id": mine["player_id"], "team_id": tid, "label": f"{mine['name']} vs {theirs['name']}"},
                                                      W.comparison_facts(mine, theirs, mine["position"]), filed_team=tid,
                                                      subjects=[{"type": "player", "id": x} for x in (mine["player_id"], theirs["player_id"]) if x]))
        plan["COMPARISON"][:0] = ranking_posts[:max(0, comp_n - len(plan["COMPARISON"]))]
        positions = ["QB", "WR", "RB", "TE"]
        for i in range(former_n):
            if len(plan["COMPARISON"]) >= comp_n:
                break
            account = acc.former()
            if not account:
                break
            pos = former_position(account)
            if pos not in OFFENSE_POSITIONS:
                best = W.team_best(account["team_id"])
                pos = norm_pos(best["position"]) if best else positions[i % len(positions)]
            plan["COMPARISON"].append(_assignment("COMPARISON", account, {"type": "position", "id": None, "label": f"Best {pos} in the league"},
                                                  W.leaderboard_facts(pos, account["team_id"])))
        i = 0
        while len(plan["COMPARISON"]) < comp_n and i < 8:
            pos = positions[(week + i) % len(positions)]
            account = (acc.stats() if i == 0 else None) or acc.pundit(prefer=PUNDIT_AFFINITY.get(pos, ()))
            i += 1
            if not account:
                break
            slant = _slant_team_id(account, W)
            plan["COMPARISON"].append(_assignment("COMPARISON", account, {"type": "position", "id": None, "label": f"Best {pos} in the league"},
                                                  W.leaderboard_facts(pos, slant)))

    # 6d. Team storylines (up to the week's TEAM_STORY count).
    for story in W.team_stories():
        roles = TEAM_STORY_ROLES[(story["tier"], story["positive"])]
        if len(plan["TEAM_STORY"]) + len(roles) > mix.get("TEAM_STORY", 0):
            continue
        tid = story["team_id"]
        used = set()
        for i, role in enumerate(roles):
            account = pick(role, tid, None, used)
            if not account:
                continue
            used.add(account["agent_id"])
            a = _assignment("TEAM_STORY", account, {"type": "team", "id": tid, "label": f"{W.team_with_record(tid)}: {story['reasons'][0]}"},
                            W.team_story_facts(story), filed_team=tid, focus=story["reasons"][:2])
            a["buzz_tier"] = story["tier"]
            plan["TEAM_STORY"].append(with_angle(a, role, roles, i, story["positive"]))

    # 7. Live posts (D8): written after the save is read, timed during the game, only from plays the save records.
    plan["LIVE"] = plan_quarter_updates(ctx, acc, mix.get("QUARTER_UPDATE", 0), W) + \
        plan_live_moments(ctx, acc, mix.get("LIVE_MOMENT", 0), W)

    # 8. League topics: award races, power takes, hot takes and questions.
    for kind in ("AWARD_RACE", "POWER_TAKE", "HOT_TAKE", "QUESTION"):
        plan["TOPICS"] += plan_topic_posts(kind, mix.get(kind, 0), topics, acc, W)

    # 9. Standings and races (from Week 3; regular season only).
    if 3 <= week <= REGULAR_SEASON_FINAL_WEEK and mix.get("STANDINGS"):
        stand = W.standings_lines()
        subjects = []
        for conf in ("AFC", "NFC"):
            order = W.conference_order(conf)
            subjects.append(("Race for the 1 seed", conf, {"conference": conf, "top_four_by_record": [W.team_with_record(t) for t in order[:4]]}))
            subjects.append(("Playoff bubble", conf, {"conference": conf, "note": "By record only (no tiebreakers).",
                                                      "seeds_6_to_9": [W.team_with_record(t) for t in order[5:9]]}))
        tight = sorted(stand.items(), key=lambda kv: abs(W.win_pct(int(kv[1][0].split("(ID: ")[1].split(")")[0]))
                                                          - W.win_pct(int(kv[1][1].split("(ID: ")[1].split(")")[0]))))
        for div, lines in tight[:2]:
            subjects.append(("Division race", div, {"division": div, "standings": [re.sub(r" \(ID: \d+\)", "", x) for x in lines]}))
        order = [subjects[4], subjects[0], subjects[2], subjects[5], subjects[1], subjects[3]]
        for i, (title, scope, facts) in enumerate(order[:mix["STANDINGS"]]):
            account = acc.bandwagon() if i == 1 else acc.pundit()
            plan["STANDINGS"].append(_assignment("STANDINGS", account, {"type": "standings", "id": None, "label": f"{title}: {scope}"},
                                                 {"topic": title, **facts, "through_week": min(week - 1, REGULAR_SEASON_FINAL_WEEK)}))

    # 10. Previews: both fan bases on the top projected games, fantasy start/sit in fantasy weeks, then pundits.
    preview_n = mix.get("PREVIEW", 0)
    fantasy_previews = min(2, preview_n) if fantasy_phase(week) and 2 <= week <= 17 and ctx["projections"] else 0
    for g in (previews if not recap else previews[:(preview_n - fantasy_previews + 1) // 2]):
        for tid in (g["away_team_id"], g["home_team_id"]):
            if len(plan["PREVIEW"]) >= preview_n - fantasy_previews:
                break
            account = acc.fan(tid)
            if account:
                plan["PREVIEW"].append(_assignment("PREVIEW", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                                   W.preview_facts(g, tid), filed_team=tid))
    for i in range(fantasy_previews):
        pos_groups = (("QB", "RB"), ("WR", "TE"))[i % 2]
        picks = {}
        for pos in pos_groups:
            picks[pos] = [f"{p['name']} ({W.teams.get(p['team_id'], {}).get('abbr', '?')}): {p['projected_points']:.1f} proj, "
                          f"matchup {p['matchup_stars']}/5" for p in ctx["projections"]
                          if (p["position"] or "").upper() in ((pos, "HB") if pos == "RB" else (pos,))][:5]
        plan["PREVIEW"].append(_assignment("PREVIEW", acc.fantasy(), {"type": "fantasy", "id": None, "label": f"Start/sit: {'/'.join(pos_groups)}"},
                                           {"topic": f"Week {week} start/sit, full PPR", "top_projected": picks}))
    while len(plan["PREVIEW"]) < preview_n and previews:
        g = previews[len(plan["PREVIEW"]) % min(3, len(previews))]
        account = acc.pundit()
        if not account:
            break
        plan["PREVIEW"].append(_assignment("PREVIEW", account, {"type": "game", "id": g["id"], "label": W.game_label(g)}, W.preview_facts(g)))

    # 11. Memes: the worst loss of the week, the draft order once it means something, a second blowout.
    meme_n = mix.get("MEME", 0)
    if recap and meme_n:
        blowout = max(recap, key=lambda g: abs(g["home_score"] - g["away_score"]))
        _, loser = W.winner_loser(blowout)
        plan["MEME"].append(_assignment("MEME", acc.archetype("NFL_MEMES"), {"type": "game", "id": blowout["id"], "label": W.game_label(blowout)},
                                        {**W.game_facts(blowout), "target": W.team_label(loser) if loser else None}))
    if week >= 6 and len(plan["MEME"]) < meme_n:
        pool = [t for t in W.teams if t not in W.alive_teams()] if postseason else list(W.teams)
        bottom = sorted(pool, key=lambda t: (W.win_pct(t), W.diff(t)))[:5]
        plan["MEME"].append(_assignment("MEME", acc.archetype("DRAFT_SICKO"), {"type": "draft", "id": None, "label": "Draft order watch"},
                                        {"topic": "Projected top of the draft, by record (no tiebreakers)",
                                         "bottom_five": [W.team_with_record(t) for t in bottom]}))
    if recap and len(plan["MEME"]) < meme_n:
        for g in sorted(recap, key=lambda g: abs(g["home_score"] - g["away_score"]), reverse=True)[1:1 + meme_n - len(plan["MEME"])]:
            _, loser = W.winner_loser(g)
            plan["MEME"].append(_assignment("MEME", acc.archetype("NFL_MEMES"), {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                            {**W.game_facts(g), "target": W.team_label(loser) if loser else None}))
    movers = ctx.get("draft_movers") or {}
    for subj in draft_board_subjects(movers, DRAFT_BOARD_POSTS["late" if postseason else "regular"]):
        account = acc.archetype("DRAFT_SICKO")
        if not account:
            break
        m = subj["mover"]
        plan["MEME"].append(_assignment("DRAFT_BOARD", account, {"type": "prospect", "id": None,
                                                                 "label": f"Draft board {subj['direction']}: {m['name']}"},
                                        {"draft_class": movers["draft_class"], "direction": subj["direction"], **m}))

    # 12. Fans' trade ideas and draft-position gallows humour (§2.7 rule 3).
    plan["TRADE_IDEA"] = plan_trade_ideas(ctx, acc, mix.get("TRADE_IDEA", 0), W)
    plan["DRAFT_POSITION"] = plan_draft_position(ctx, acc, mix.get("DRAFT_POSITION", 0), W)

    # 13. Replies: placeholders; parents are chosen after the rest is written and scored (plan_replies).
    for _ in range(min(REPLY_MAX, mix.get("REPLY", 0))):
        plan["REPLY"].append(_assignment("REPLY", None, {"type": "post", "id": None,
                                                         "label": "parent chosen from the most engaged posts after writing"}, {}))

    # Whatever room is left (pools that ran out, missing subjects) goes to player posts (postseason: eliminated fans),
    # previews (preseason) or performances.
    room = total - sum(len(v) for v in plan.values())
    if room > 0 and postseason:
        alive = W.alive_teams()
        eliminated = [t for t in W.teams if t not in alive]
        best_by_team = collections.defaultdict(list)
        for t in W.season_totals.values():
            best_by_team[t["team_id"]].append(t)
        for v in best_by_team.values():
            v.sort(key=lambda t: t["fantasy_points"], reverse=True)
        alive_players = sorted((t for t in W.season_totals.values() if t["team_id"] in alive),
                               key=lambda t: t["fantasy_points"], reverse=True)
        k = guard = 0
        while room > 0 and eliminated and guard < 500:
            guard += 1
            tid = eliminated[k % len(eliminated)]
            nth = k // len(eliminated)
            k += 1
            account = acc.fan(tid)
            if not account:
                continue
            if nth % 2 == 0 and best_by_team[tid]:
                subj = best_by_team[tid][min(nth // 2, len(best_by_team[tid]) - 1)]
                angle = "Your team's season is over: this player's season and what's next for him."
            elif alive_players:
                subj = alive_players[(k // 2) % len(alive_players)]
                angle = "Your team is out: a player still alive in the playoffs."
            else:
                continue
            plan["PLAYER"].append(_assignment("PLAYER", account, {"type": "player", "id": subj["player_id"], "team_id": subj["team_id"],
                                                                  "label": f"{subj['name']} ({subj['position']}, {W.teams[subj['team_id']]['abbr']})"},
                                              {"angle": angle, **W.season_player_facts(subj)}, filed_team=tid))
            room -= 1
    elif room > 0 and recap:
        add_performances(len(plan["PERFORMANCE"]) + room)
        room = total - sum(len(v) for v in plan.values())
        if room > 0:                                   # national pools ran out: league topics can still use fans
            for kind in ("HOT_TAKE", "QUESTION"):
                extra = plan_topic_posts(kind, room, topics, acc, W)
                plan["TOPICS"] += extra
                room -= len(extra)
    elif room > 0 and not recap and previews:
        # Preseason: every team's fans preview their opener, then pundits on the top games.
        for g in previews:
            for tid in (g["away_team_id"], g["home_team_id"]):
                if room <= 0:
                    break
                account = acc.fan(tid)
                if account:
                    plan["PREVIEW"].append(_assignment("PREVIEW", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                                       W.preview_facts(g, tid), filed_team=tid))
                    room -= 1
        while room > 0:
            account = acc.pundit() or acc.bandwagon()
            if not account:
                break
            g = previews[room % min(5, len(previews))]
            plan["PREVIEW"].append(_assignment("PREVIEW", account, {"type": "game", "id": g["id"], "label": W.game_label(g)}, W.preview_facts(g)))
            room -= 1

    assignments = [a for v in plan.values() for a in v]
    if not fantasy_active:
        assignments = [a for a in assignments if a.get("archetype") not in FANTASY_ARCHETYPES
                       and a.get("subject", {}).get("type") != "fantasy"
                       and a.get("angle") != "what it means for fantasy managers"
                       and a.get("kind") != "FANTASY_PREVIEW"]
    _add_slants(assignments, acc, W)
    _fill_subjects_and_focus(assignments, ctx)
    counts = dict(collections.Counter(a["kind"] for a in assignments))
    if not recap:
        notes.append("No completed games last week: no reactions, performances or memes; the room goes to previews.")
    notes.append(f"week_mix planned {total} posts; {sum(counts.values())} assigned (pools that run out or missing "
                 "subjects plan fewer).")
    missing = [a for a in assignments if a["kind"] != "REPLY" and a["agent_id"] is None]
    if missing:
        notes.append(f"{len(missing)} posts have no account yet (placeholder shares, or a pool that ran out).")
    return {"season": season, "week": week, "recap_week": W.recap_week, "phase": phase,
            "assignments": assignments, "counts": counts, "mix": mix, "notes": notes}


# ------------------------------------------------------------------------------------------------ replies ---
# Who answers whom when a pundit's post takes off.
CONTRARIAN = {
    "SHANNON": "SKIP_TWEET", "SKIP_TWEET": "SHANNON", "STEPHEN_A": "MCAFEE", "MCAFEE": "STEPHEN_A",
    "COWHERD": "CLARK", "CLARK": "COWHERD", "RG3": "JJ_WATT", "JJ_WATT": "RG3", "OCHOCINCO": "SHANNON",
    "FANTASY_GURU": "BERRY", "BERRY": "FANTASY_GURU", "YATES": "BERRY", "HERBSTREIT": "STEPHEN_A",
    "WINGO": "EISEN", "EISEN": "WINGO", "THE_ATHLETIC": "STEPHEN_A", "SCHEFTER_TWEET": "RAPOPORT",
    "RAPOPORT": "SCHEFTER_TWEET", "SIMMONS": "COWHERD", "NFL_MEMES": None, "DRAFT_SICKO": None,
    # national writers (their article shares draw replies too)
    "BARNWELL": "SKIP_TWEET", "SANDO": "COWHERD", "ROSENTHAL": "STEPHEN_A", "JOYNER": "MCAFEE",
    "CHADIHA": "CLARK", "ACHO": "SHANNON", "KIMES": "STEPHEN_A", "ORLOVSKY": "RG3",
}
REPLY_SPREAD = (3, 3, 3, 3, 2, 2, 2, 2)  # replies per parent, most engaged parent first


def _subject_teams(ctx, a):
    """Teams a post is about: both teams of its game, else the team it's filed under."""
    if a["subject"]["type"] == "game":
        g = next((g for g in ctx["recap_games"] + ctx["preview_games"] if g["id"] == a["subject"]["id"]), None)
        if g:
            return [g["away_team_id"], g["home_team_id"]]
    return [a["team_id"]] if a.get("team_id") else []


def plan_replies(ctx: dict, plan: dict, posts: list, total: int = None) -> list:
    """Reply assignments for the week's most engaged posts (build step 5).

    posts: saved posts [{"id", "assignment", "likes", "agent_id", "handle", "display_name", "text"}].
    Each reply carries its parent ({"id", "handle", "display_name", "text"}) and the parent's facts.
    Authors are rivals or natural foils: the other team's fans, a contrarian pundit, the roasted team's fans.
    """
    if total is None:
        total = sum(1 for a in plan["assignments"] if a["kind"] == "REPLY") or 15
    fantasy_active = fantasy_phase(ctx["week"]) is not None
    acc = Accounts(ctx["accounts"], seed=f"{ctx['season']}-{ctx['week']}-replies",
                   fantasy_enabled=fantasy_active).replay(plan["assignments"])
    parents = sorted((p for p in posts if p["assignment"]["kind"] != "REPLY" and p.get("text")),
                     key=lambda p: p.get("likes") or 0, reverse=True)
    out = []
    # The most engaged posts get REPLY_SPREAD replies; when authors run out under the weekly caps, the next parents
    # take one each until the week's reply count is placed.
    for parent, n in zip(parents, list(REPLY_SPREAD) + [1] * max(0, len(parents) - len(REPLY_SPREAD))):
        a = parent["assignment"]
        teams = _subject_teams(ctx, a)
        author_team = a.get("team_id") if a.get("archetype") == "DIE_HARD_FAN" else None
        rivals = [t for t in teams if t != author_team]
        used_here = {parent["agent_id"]}

        def fan_of(tids):
            pool = [x for x in acc.all() if x["archetype"] == "DIE_HARD_FAN" and x["team_id"] in tids
                    and x["agent_id"] not in used_here]
            return acc._pick(pool)

        def foil():
            arch = CONTRARIAN.get(a.get("archetype"))
            if arch:
                if not fantasy_active and arch in FANTASY_ARCHETYPES:
                    return None
                pool = [x for x in acc.all() if x["archetype"] == arch and x["agent_id"] not in used_here]
                return acc._pick(pool, cap_role="pundit")
            return None

        def pundit():
            pool = [x for x in acc.all() if acc.role.get(x["agent_id"]) == "pundit" and x["agent_id"] not in used_here]
            return acc._pick(pool)

        def bandwagon():
            return acc._pick([x for x in acc.all() if x["archetype"] == "BANDWAGON_FAN" and x["agent_id"] not in used_here])

        def beat_of(tids):
            return acc._pick([x for x in acc.all() if x["archetype"] == "LOCAL_BEAT_REPORTER" and x["team_id"] in tids
                              and x["agent_id"] not in used_here])

        if a.get("archetype") in INSIDER_ARCHETYPES and teams:
            order = [lambda: beat_of(teams), lambda: fan_of(teams), foil, pundit]
        elif a.get("archetype") == "DIE_HARD_FAN":
            order = [lambda: fan_of(rivals), lambda: fan_of([author_team]), bandwagon, pundit]
        elif a["kind"] == "MEME":
            target = [t for t in teams if t] if a["subject"]["type"] == "game" else []
            order = [lambda: fan_of(target[:1] or target), lambda: fan_of(target), bandwagon]
        elif a.get("archetype") == "LOCAL_BEAT_REPORTER":
            order = [lambda: fan_of(teams), pundit]
        else:
            order = [foil, lambda: fan_of(teams), bandwagon, pundit]
        made = 0
        tries = 0
        while made < n and len(out) < total and tries < 3 * n:
            tries += 1
            author = None
            for f in order:
                author = f()
                if author:
                    break
            if not author:
                break
            used_here.add(author["agent_id"])
            filed = author.get("team_id") or a.get("team_id")
            out.append(_assignment("REPLY", author,
                                   {"type": "post", "id": parent["id"], "label": f"reply to {parent['handle']}"},
                                   a.get("facts") or {}, filed_team=filed))
            out[-1]["parent"] = {"id": parent["id"], "handle": parent["handle"],
                                 "display_name": parent["display_name"], "text": parent["text"]}
            out[-1]["subjects"] = list(a.get("subjects") or [])
            out[-1]["topic_id"] = a.get("topic_id")
            # Quote-posts (§2.7 rule 4): a beat writer on an insider's news, anyone on the aggregator or stats
            # account, and otherwise about one reply in three (seeded).
            out[-1]["quote"] = bool(
                (a.get("archetype") in INSIDER_ARCHETYPES + (AGGREGATOR_ARCHETYPE, STATS_ARCHETYPE))
                or _seed_int("quote", ctx["season"], ctx["week"], parent["id"], author["agent_id"]) % 3 == 0)
            made += 1
        if len(out) >= total:
            break
    _add_slants(out, acc, Week(ctx))
    _fill_subjects_and_focus(out, ctx)
    return out


# ------------------------------------------------------------------------------------------- team wakeup ---
def plan_team_wakeup(ctx: dict, team_id: int, already_posted=(), local_articles=()) -> dict:
    """Posts for an on-demand team wakeup (team page or Team Hub view), on top of the weekly plan.

    Each of the team's beat reporters posts a note; each die-hard fan reacts to last week's game or previews
    this week's (alternating when both exist); 3 pundits if the team's popularity is above 70, 1 above 55 (at least
    one when the league is talking about the team, and that one posts the storyline).
    local_articles: [(article dict, agent_id)] written in this wakeup; each is shared by its reporter.
    already_posted: agent ids that already posted in an earlier wakeup for this team and week.
    """
    W = Week(ctx)
    acc = Accounts(ctx["accounts"], seed=f"{W.season}-{W.week}-team{team_id}",
                   fantasy_enabled=fantasy_phase(W.week) is not None).block(already_posted)
    last, nxt = W.team_games(team_id)
    out = []
    for art, agent_id in local_articles:
        account = acc.by_id.get(agent_id)
        if account:
            out.append(_assignment("SHARE", account, {"type": "article", "id": art["id"], "label": art["headline"]},
                                   {k: art.get(k) for k in ("headline", "excerpt", "article_type", "slot")}, filed_team=team_id))
    starter_injuries = [inj for inj in W.team_injuries(team_id) if inj.get("is_starter")]
    for idx, b in enumerate(sorted((x for x in acc.all() if x["archetype"] == "LOCAL_BEAT_REPORTER" and x["team_id"] == team_id
                                   and acc.uses[x["agent_id"]] < 1000), key=lambda x: x["agent_id"])):
        acc.uses[b["agent_id"]] += 1
        if idx == 0 and starter_injuries:
            top_inj = starter_injuries[0]
            out.append(_assignment("BEAT_INJURY", b, {"type": "team", "id": team_id, "label": f"{W.team_label(team_id)}: {top_inj['player_name']} Injury"},
                                   W.beat_injury_facts(team_id, top_inj), filed_team=team_id))
        elif idx == (1 if starter_injuries else 0) and (any(m["notable"] for m in W.team_moves(team_id))
                                                        or W.team_trades(team_id)):
            out.append(_assignment("BEAT_MOVE", b, {"type": "team", "id": team_id, "label": f"{W.team_label(team_id)}: roster moves"},
                                   W.beat_move_facts(team_id), filed_team=team_id))
        else:
            out.append(_assignment("BEAT", b, {"type": "team", "id": team_id, "label": W.team_label(team_id)},
                                   W.beat_facts(team_id), filed_team=team_id))
    fans = sorted((x for x in acc.all() if x["archetype"] == "DIE_HARD_FAN" and x["team_id"] == team_id
                   and acc.uses[x["agent_id"]] < 1000), key=lambda x: acc.order[x["agent_id"]])
    key_injuries = [inj for inj in starter_injuries if inj.get("is_key_injury")]
    for i, f in enumerate(fans):
        acc.uses[f["agent_id"]] += 1
        if i == 0 and key_injuries:
            kinj = key_injuries[0]
            out.append(_assignment("INJURY_SHOCK", f, {"type": "team", "id": team_id, "label": f"{W.team_label(team_id)}: {kinj['player_name']} Injury"},
                                   W.injury_shock_facts(team_id, kinj), filed_team=team_id))
            continue
        g = (last if (i % 2 == 0 or not nxt) else nxt) or nxt
        if not g:
            break
        if g is last:
            facts = W.game_facts(g, team_id)
            facts.pop("records_after_game", None)
            out.append(_assignment("REACTION", f, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                   facts, filed_team=team_id, focus=reaction_focus(W, g, team_id, f"{f['agent_id']}|{g['id']}")))
        else:
            out.append(_assignment("PREVIEW", f, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                   W.preview_facts(g, team_id), filed_team=team_id))
    # National voices talk about the team's players, not the game: 3 pundits if the team's popularity is above
    # 70, 1 above 55; plus the team's ex-player, if it has one.
    top_lines = sorted((l for l in W.recap_lines if l["team_id"] == team_id), key=lambda l: l["fantasy_points"], reverse=True)
    best = W.team_best(team_id)
    pop = W.teams[team_id]["popularity"]
    story = next((st for st in W.team_stories() if st["team_id"] == team_id), None)
    n_pundits = 3 if pop > 70 else 1 if pop > 55 else 0
    if story:
        n_pundits = max(n_pundits, 1)      # a team the league is talking about always gets a national take
    for i in range(n_pundits):
        p = acc.pundit()
        if not p:
            break
        if story and i == 0:
            a = _assignment("TEAM_STORY", p, {"type": "team", "id": team_id, "label": f"{W.team_with_record(team_id)}: {story['reasons'][0]}"},
                            W.team_story_facts(story), filed_team=team_id)
            a["buzz_tier"] = story["tier"]
            out.append(a)
            continue
        i -= 1 if story else 0
        if i < len(top_lines):
            l = top_lines[i]
            out.append(_assignment("PERFORMANCE", p, {"type": "player", "id": l["player_id"], "team_id": team_id, "label": f"{l['name']} ({l['position']}, {W.teams[team_id]['abbr']})"},
                                   W.performance_facts(l), filed_team=team_id))
        elif best:
            out.append(_assignment("COMPARISON", p, {"type": "position", "id": None, "label": f"Best {norm_pos(best['position'])} in the league"},
                                   W.leaderboard_facts(best["position"], team_id), filed_team=team_id))
    legend = next((a for a in acc.all() if a["archetype"] == "FORMER_PLAYER" and a["team_id"] == team_id
                   and acc.uses[a["agent_id"]] < 1000), None)
    if legend and best:
        acc.uses[legend["agent_id"]] += 1
        pos = former_position(legend)
        pos = pos if pos in OFFENSE_POSITIONS and W.team_best(team_id, pos) else best["position"]
        out.append(_assignment("COMPARISON", legend, {"type": "position", "id": None, "label": f"Best {norm_pos(pos)} in the league"},
                               W.leaderboard_facts(pos, team_id), filed_team=team_id))
    _add_slants(out, acc, W)
    _fill_subjects_and_focus(out, ctx)
    counts = collections.Counter(a["kind"] for a in out)
    return {"season": W.season, "week": W.week, "recap_week": W.recap_week, "phase": "team wakeup",
            "team_id": team_id, "assignments": out, "counts": dict(counts), "notes": []}


# ------------------------------------------------------------------------------------------------- CLI ---
def format_plan(plan: dict, teams: dict) -> str:
    looks_back = f"reacting to Week {plan['recap_week']}" if plan["recap_week"] >= 1 else "no games played yet"
    out = [f"Agent Wire plan: {plan['season']} Week {plan['week']} ({plan['phase']}; {looks_back})",
           "Counts: " + ", ".join(f"{k} {v}" for k, v in plan["counts"].items()) + f"  =  {sum(plan['counts'].values())}"]
    out += [f"Note: {n}" for n in plan["notes"]]
    kind = None
    for a in plan["assignments"]:
        if a["kind"] != kind:
            kind = a["kind"]
            count = plan["counts"].get(kind, sum(1 for x in plan["assignments"] if x["kind"] == kind))
            out.append(f"\n== {kind} ({count}) · {WINDOWS.get(kind, '')}")
        team = teams.get(a["team_id"], {}).get("abbr", "") if a["team_id"] else ""
        out.append(f"  {(a['handle'] or '(tbd)'):<24} {team:<4} {a['subject']['label']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Plan a week of Agent Wire posts (dry run; nothing is written).")
    ap.add_argument("--season", type=int, default=2035)
    ap.add_argument("--week", type=int, required=True, help="publication week (reacts to week - 1)")
    ap.add_argument("--json", help="also write the full plan, with facts packets, to this file")
    ap.add_argument("--dsn", help="Postgres connection string (defaults to DATABASE_URL / the local `madden` db)")
    args = ap.parse_args()

    import psycopg
    from db.db_handler import DatabaseHandler
    with psycopg.connect(args.dsn or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            ctx = load_week_context(cur, args.season, args.week)
    plan = plan_social_week(ctx, use_expected_articles=True)
    print(format_plan(plan, ctx["teams"]))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(plan, f, indent=1, default=str, ensure_ascii=False)
        print(f"\nFull plan with facts packets: {args.json}")


if __name__ == "__main__":
    main()
