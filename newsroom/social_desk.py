"""Agent Wire social desk: plans each week's posts (docs/SOCIAL_MATRIX.md §2–§3).

Build step 3 = planning only. `plan_social_week(ctx)` turns one week's data into ~100 post assignments
(account + kind + subject + facts packet); nothing is written and no model is called. Generation,
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
import json
import random
import re

from engines.game_script_engine import script_summary
from newsroom.fantasy_desk import fantasy_phase
from newsroom.season_landscape import REGULAR_SEASON_FINAL_WEEK, division_standings

TARGET_POSTS = 100

# Bucket sizes (docs/SOCIAL_MATRIX.md §2, player-first mix agreed with JR on Oct 14). Die-hard reactions are one
# per team that played; shares are one per published article; replies flex between REPLY_RANGE to keep the week
# near TARGET_POSTS.
BUCKET_SIZES = {
    "PERFORMANCE": 14,
    "BAD_GAME": 8,
    "COMPARISON": 10,
    "BEAT": 5,
    "STANDINGS": 2,
    "PREVIEW": 6,
    "MEME": 2,
    "REPLY": 15,
}
REPLY_RANGE = (10, 15)

# Weekly post caps per account role (least-used accounts are picked first).
ROLE_CAPS = {"pundit": 3, "fantasy": 4, "beat": 2, "fan": 2, "bandwagon": 3, "meme": 3, "draft": 3, "former": 3}

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
# Bucket floors when a busy week has to trim to stay near TARGET_POSTS (last-added posts go first).
TRIM_ORDER = (("PERFORMANCE", 10), ("COMPARISON", 7), ("BAD_GAME", 5), ("TEAM_STORY", 4))

# Hot-take voices preferred for calling out bad games.
HOT_TAKE_PUNDITS = ("STEPHEN_A", "SKIP_TWEET", "COWHERD", "SHANNON", "MCAFEE")

# Archetypes that never post as a random pundit. Mirrors swarm_dispatcher.TWEET_EXCLUDED_ARCHETYPES (the
# long-form-only writers, SKIP_BAYLESS, the roundtable panel), plus roles the desk assigns on purpose.
PUNDIT_EXCLUDED = {
    "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "KIMES", "ORLOVSKY",
    "SKIP_BAYLESS", "ROUNDTABLE_PANEL",
    "LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN",
    "FANTASY_GURU", "BERRY", "YATES", "NFL_MEMES", "DRAFT_SICKO", "FORMER_PLAYER",
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

    # Player lines through the recap week, matched to players on that team (player_stats names are abbreviated).
    cur.execute("""
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
        d["fantasy_points"] = float(d["fantasy_points"] or 0)
        d["popularity"] = float(d["popularity"] or 0)
        d["name"] = tidy_name(d["name"])
        lines.append(d)
    ctx["player_lines"] = lines

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
    # Each recap game's canonical story (engines/game_script_engine.py, §2.14), read or built here
    from engines.game_script_engine import ensure_script
    ctx["game_scripts"] = {}
    for g in ctx["recap_games"]:
        script, _ = ensure_script(cur, g["id"])
        if script:
            ctx["game_scripts"][g["id"]] = script
    return ctx


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

    def by_archetype_uncapped(self, arch):
        """The account that shares an article (shares never count against caps)."""
        for a in sorted(self.all(), key=lambda a: a["agent_id"]):
            if a["archetype"] == arch:
                return a
        return None


# --------------------------------------------------------------------------------------------- planner ---
# Fan posts each get one angle so a team's fans don't all write the same post (docs/SOCIAL_MATRIX.md §3).
FAN_ANGLES = (
    "one specific player who dominated or struggled",
    "the turning point of the game as you remember it (no invented stats)",
    "what this result means for the rest of the season",
    "trash talk or banter aimed at the other team's fans",
    "the coaching, preparation, and play-calling decisions",
    "the atmosphere in the stadium or watching with friends",
    "one memorable number or moment from the facts",
    "looking ahead to next week's matchup",
    "declaring your player the best in the league or best at his position right now",
    "legacy debate: measuring this team or player against franchise or all-time history",
    "fanbase trauma or anxiety: living on the edge of your seat ('I have seen this movie before')",
    "keeping receipts: calling out media and doubters who counted your team out",
    "admitting you were wrong about a player and eating your words (apology form)",
    "attacking an underperforming star for disappearing when the lights were bright",
)
BANDWAGON_ANGLES = (
    "declaring this player the undisputed best in the league right now",
    "pure hype: crown them, this team or player has unmatched aura",
    "calling out a player for being a total fraud when the lights got bright",
    "jumping on the bandwagon: this offense is the most electric thing in football",
    "the eye test: why numbers don't do justice to how unstoppable they look",
    "legacy claim: claiming this is an all-time great season we will talk about for decades",
)
BEAT_ANGLES = (
    "practice observation and physical execution in the trenches",
    "locker room focus, leadership quotes, or coach press conference tone",
    "tactical and schematic matchup key for the upcoming opponent",
    "roster depth, rotational players, or young player development",
)
PREVIEW_ANGLES = (
    "fear and dread: terrified of what the opponent's star player might do to your team",
    "swagger and confidence: why your team has the upper hand and will not back down",
    "fan anxiety and nervous energy: pacing the floor before kickoff",
    "matchup battle: the key battle in the trenches or on the perimeter that decides the game",
    "revenge or statement: a chance to prove the doubters wrong",
)
FAN_ARCHETYPES = ("DIE_HARD_FAN", "BANDWAGON_FAN")
# Replies to the same post take different stances, in this order.
REPLY_STANCES = ("push back on it", "agree and add a point of your own", "make a joke about it")


def _assignment(kind, account, subject, facts, filed_team=None, angle=None):
    if angle is None and account:
        arch = account.get("archetype")
        seed = sum(ord(c) for c in f"{account.get('agent_id', '')}|{kind}|{subject.get('id', '')}")
        if arch == "DIE_HARD_FAN" and kind == "REACTION":
            angle = FAN_ANGLES[seed % len(FAN_ANGLES)]
        elif arch == "DIE_HARD_FAN" and kind == "PREVIEW":
            angle = PREVIEW_ANGLES[seed % len(PREVIEW_ANGLES)]
        elif arch == "BANDWAGON_FAN" and kind in ("PERFORMANCE", "BAD_GAME", "STANDINGS"):
            angle = BANDWAGON_ANGLES[seed % len(BANDWAGON_ANGLES)]
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
    }


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


def plan_social_week(ctx: dict, use_expected_articles: bool = False) -> dict:
    """Plan one week's Agent Wire posts. Returns {"season", "week", "phase", "assignments", "counts", "notes"}."""
    W = Week(ctx)
    season, week = W.season, W.week
    fantasy_active = fantasy_phase(week) is not None
    acc = Accounts(ctx["accounts"], seed=f"{season}-{week}", fantasy_enabled=fantasy_active)
    notes = []
    plan = collections.OrderedDict((k, []) for k in
                                   ("SHARE", "REACTION", "PERFORMANCE", "BAD_GAME", "COMPARISON", "TEAM_STORY", "PLAYER", "BEAT",
                                    "STANDINGS", "PREVIEW", "MEME", "REPLY"))

    recap = sorted(ctx["recap_games"], key=lambda g: g["excitement_index"] or 0, reverse=True)
    previews = sorted(ctx["preview_games"], key=lambda g: g["projected_excitement"] or 0, reverse=True)
    postseason = week > REGULAR_SEASON_FINAL_WEEK
    phase = "preseason" if not recap else "postseason" if postseason else "regular season"
    played = [t for g in recap for t in (g["away_team_id"], g["home_team_id"])]

    # 1. Article shares: one per article published this week, from its writer's account.
    articles = ctx["articles"]
    if not articles and use_expected_articles:
        articles = expected_articles(week)
        notes.append(f"No articles stored for Week {week} yet; planned {len(articles)} placeholder shares "
                     "(one per slate slot). Their accounts are picked when the slates have run.")
    for art in articles:
        # The roundtable panel never posts; local beat articles are shared in their team's wakeup instead.
        if art["persona"] in NO_SHARE_ARCHETYPES or art["persona"] == "LOCAL_BEAT_REPORTER" \
                or (art.get("slot") or "").startswith("Local Beat"):
            continue
        if not fantasy_active and ((art.get("slot") or "").startswith("Fantasy")
                                   or (art.get("article_type") or "").startswith("FANTASY_")
                                   or art.get("persona") in FANTASY_ARCHETYPES):
            continue
        account = None
        if art["persona"]:
            account = acc.by_archetype_uncapped(SHARE_ACCOUNT_ARCHETYPE.get(art["persona"], art["persona"]))
        plan["SHARE"].append(_assignment("SHARE", account,
                                         {"type": "article", "id": art["id"], "label": art["headline"]},
                                         {k: art[k] for k in ("headline", "excerpt", "article_type", "slot")},
                                         filed_team=art["team_id"]))

    # 2. Player performances from the recap week: the top full-PPR offensive lines, with the 2nd, 5th, 8th ... ordinary
    # post a big defensive or kicking game where the game's box score was captured.
    # League-wide voices only: pundits, ex-players (leaning to their old team, then their position), bandwagon
    # fans and the fantasy accounts. Die-hards talk about their own team in reactions and comparisons.
    perf_n = BUCKET_SIZES["PERFORMANCE"] if recap else 0
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

    # 2a. Games the league is talking about (tier 1: three posts, tier 2: four plus a ranking comparison).
    # They come first, so a busy week trims the ordinary performances below them.
    buzz = W.buzz_lines() if recap else []
    ranking_posts = []
    for l, tier, reasons in buzz:
        roles = PLAYER_BUZZ_ROLES[tier]
        if not fantasy_active and tier == 2:
            roles = ("pundit", "former", "bandwagon", "pundit")
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
            a = _assignment("PERFORMANCE", account, subject, W.buzz_facts(l, tier, reasons))
            a["buzz_tier"] = tier
            plan["PERFORMANCE"].append(with_angle(a, role, roles, i))
        if tier == 2:
            account = pick("pundit", pos=l["position"], avoid=used)
            if account:
                a = _assignment("COMPARISON", account, {**subject, "label": f"Where {l['name']}'s game ranks"},
                                W.ranking_facts(l, reasons))
                a["buzz_tier"] = tier
                ranking_posts.append(a)

    offense_queue = [l for l in top_lines if norm_pos(l["position"]) in OFFENSE_POSITIONS]
    defense_queue = W.defense_performances()[:DEF_PERFORMANCE_MAX] if recap else []

    def next_line():
        """The next performance subject: a defensive or kicking game on the 2nd, 5th, 8th ... ordinary post (while
        any are left), otherwise the next offensive line; falls back to whichever queue still has lines."""
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
            account = (acc.fantasy() if who == "fantasy" else
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

    # 2b. Bad games: stars who flopped against their own average. Bandwagon fans and the meme page clown them,
    # hot-take pundits call them out, and an ex-player weighs in.
    if recap:
        bad_pattern = ["bandwagon", "bandwagon", "pundit", "meme", "bandwagon", "former", "pundit", "bandwagon"]
        for i, (l, avg) in enumerate(W.bad_games()[:BUCKET_SIZES["BAD_GAME"]]):
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

    # 2c. Player comparisons: die-hards put their team's best player against a rival's at the same position,
    # ex-players argue for their old team's guy against the league's best, pundits rank a position.
    if W.season_totals:
        comp_n = BUCKET_SIZES["COMPARISON"]
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
                                                      W.comparison_facts(mine, theirs, mine["position"]), filed_team=tid))
        plan["COMPARISON"][:0] = ranking_posts
        positions = ["QB", "WR", "RB", "TE"]
        for i in range(former_n):
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
            i += 1
            account = acc.pundit(prefer=PUNDIT_AFFINITY.get(pos, ()))
            if not account:
                break
            slant = _slant_team_id(account, W)
            plan["COMPARISON"].append(_assignment("COMPARISON", account, {"type": "position", "id": None, "label": f"Best {pos} in the league"},
                                                  W.leaderboard_facts(pos, slant)))

    # 2d. Team storylines: unbeaten and winless runs, streaks and how they end, blowouts, upsets, scoring records.
    # National voices, the meme page and bandwagon fans; the team's own ex-player if it has one.
    for story in W.team_stories():
        roles = TEAM_STORY_ROLES[(story["tier"], story["positive"])]
        if len(plan["TEAM_STORY"]) + len(roles) > TEAM_STORY_MAX:
            continue
        tid = story["team_id"]
        used = set()
        for i, role in enumerate(roles):
            account = pick(role, tid, None, used)
            if not account:
                continue
            used.add(account["agent_id"])
            a = _assignment("TEAM_STORY", account, {"type": "team", "id": tid, "label": f"{W.team_with_record(tid)}: {story['reasons'][0]}"},
                            W.team_story_facts(story), filed_team=tid)
            a["buzz_tier"] = story["tier"]
            plan["TEAM_STORY"].append(with_angle(a, role, roles, i, story["positive"]))

    # 3. Beat notes: teams from the most exciting games, then the most popular teams that played.
    beat_n = BUCKET_SIZES["BEAT"]
    beat_teams = []
    for t in played + sorted(W.teams, key=lambda t: W.teams[t]["popularity"], reverse=True):
        if t not in beat_teams:
            beat_teams.append(t)
    for tid in beat_teams:
        if len(plan["BEAT"]) >= beat_n:
            break
        account = acc.beat(tid)
        if not account:
            continue
        team_starter_injuries = [inj for inj in W.team_injuries(tid) if inj.get("is_starter")]
        if team_starter_injuries:
            top_inj = team_starter_injuries[0]
            plan["BEAT"].append(_assignment("BEAT_INJURY", account, {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: {top_inj['player_name']} Injury"},
                                            W.beat_injury_facts(tid, top_inj), filed_team=tid))
        else:
            plan["BEAT"].append(_assignment("BEAT", account, {"type": "team", "id": tid, "label": W.team_label(tid)},
                                            W.beat_facts(tid), filed_team=tid))

    # 4. Standings and races (from Week 3; regular season only).
    if 3 <= week <= REGULAR_SEASON_FINAL_WEEK:
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
        # Interleave so both conferences and a division race appear in the first 4.
        order = [subjects[4], subjects[0], subjects[2], subjects[5], subjects[1], subjects[3]]
        for i, (title, scope, facts) in enumerate(order[:BUCKET_SIZES["STANDINGS"]]):
            account = acc.bandwagon() if i == 1 else acc.pundit()
            plan["STANDINGS"].append(_assignment("STANDINGS", account, {"type": "standings", "id": None, "label": f"{title}: {scope}"},
                                                 {"topic": title, **facts, "through_week": min(week - 1, REGULAR_SEASON_FINAL_WEEK)}))

    # 5. Previews: both fan bases on the top 3 projected games, plus fantasy start/sit (in-season fantasy weeks).
    fantasy_previews = 2 if fantasy_phase(week) and 2 <= week <= 17 and ctx["projections"] else 0
    for g in previews[:(BUCKET_SIZES["PREVIEW"] - fantasy_previews + 1) // 2]:
        for tid in (g["away_team_id"], g["home_team_id"]):
            if len(plan["PREVIEW"]) >= BUCKET_SIZES["PREVIEW"] - fantasy_previews:
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
    while len(plan["PREVIEW"]) < BUCKET_SIZES["PREVIEW"] and previews:
        g = previews[(len(plan["PREVIEW"])) % min(3, len(previews))]
        account = acc.pundit()
        if not account:
            break
        plan["PREVIEW"].append(_assignment("PREVIEW", account, {"type": "game", "id": g["id"], "label": W.game_label(g)}, W.preview_facts(g)))

    # 6. Memes: the worst loss of the week, and the draft order once it means something.
    if recap:
        blowout = max(recap, key=lambda g: abs(g["home_score"] - g["away_score"]))
        _, loser = W.winner_loser(blowout)
        plan["MEME"].append(_assignment("MEME", acc.archetype("NFL_MEMES"), {"type": "game", "id": blowout["id"], "label": W.game_label(blowout)},
                                        {**W.game_facts(blowout), "target": W.team_label(loser) if loser else None}))
    if week >= 6:
        if postseason:
            alive = W.alive_teams()
            pool = [t for t in W.teams if t not in alive]
        else:
            pool = list(W.teams)
        bottom = sorted(pool, key=lambda t: (W.win_pct(t), W.diff(t)))[:5]
        plan["MEME"].append(_assignment("MEME", acc.archetype("DRAFT_SICKO"), {"type": "draft", "id": None, "label": "Draft order watch"},
                                        {"topic": "Projected top of the draft, by record (no tiebreakers)",
                                         "bottom_five": [W.team_with_record(t) for t in bottom]}))
    elif recap:
        second = sorted(recap, key=lambda g: abs(g["home_score"] - g["away_score"]), reverse=True)[1:2]
        for g in second:
            _, loser = W.winner_loser(g)
            plan["MEME"].append(_assignment("MEME", acc.archetype("NFL_MEMES"), {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                            {**W.game_facts(g), "target": W.team_label(loser) if loser else None}))

    # 7. Reactions: one die-hard post for each team that played. Nobody else reacts to games; league-wide voices
    # post about players (performances, bad games, comparisons).
    for g in recap:
        for tid in (g["away_team_id"], g["home_team_id"]):
            account = acc.fan(tid)
            if account:
                plan["REACTION"].append(_assignment("REACTION", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                                    W.game_facts(g, tid), filed_team=tid))

    # 7b. Key injury shock reactions: die-hard fan reactions for marquee injuries (OVR 88+, Pop 50+, or starting QB)
    key_injuries = [inj for inj in ctx.get("injuries", []) if inj.get("is_key_injury")]
    for inj in key_injuries[:2]:
        tid = inj["team_id"]
        account = acc.fan(tid)
        if account:
            plan["REACTION"].append(_assignment("INJURY_SHOCK", account,
                                                {"type": "team", "id": tid, "label": f"{W.team_label(tid)}: {inj['player_name']} Injury Shock"},
                                                W.injury_shock_facts(tid, inj), filed_team=tid))

    # 8. Replies: parents are chosen after the rest is written and scored. Their number flexes within
    # REPLY_RANGE to keep the week near TARGET_POSTS.
    so_far = sum(len(v) for v in plan.values())
    # A full slate with a big article week (or a week full of storylines) can push past the target even at the
    # reply floor: trim the last-added (lowest-ranked) performances, then comparisons, then bad games, each down to
    # its floor in TRIM_ORDER. Buzz posts sit at the front of their buckets, so they go last.
    over = so_far + REPLY_RANGE[0] - TARGET_POSTS
    for bucket, floor in TRIM_ORDER:
        while over > 0 and len(plan[bucket]) > floor:
            plan[bucket].pop()
            so_far -= 1
            over -= 1
    n_replies = max(REPLY_RANGE[0], min(REPLY_RANGE[1], TARGET_POSTS - so_far))
    for i in range(n_replies):
        plan["REPLY"].append(_assignment("REPLY", None, {"type": "post", "id": None,
                                                         "label": "parent chosen from the most engaged posts after writing"}, {}))

    # Whatever room is left goes to player posts (postseason: eliminated fans) or previews (preseason).
    room = TARGET_POSTS - sum(len(v) for v in plan.values())
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
        k = 0
        guard = 0
        while room > 0 and eliminated and guard < 500:
            guard += 1
            tid = eliminated[k % len(eliminated)]
            nth = k // len(eliminated)
            k += 1
            account = acc.fan(tid)
            if not account:
                continue
            # Alternate: own team's players (season review), then players still alive in the playoffs.
            if nth % 2 == 0 and best_by_team[tid]:
                subj = best_by_team[tid][min(nth // 2, len(best_by_team[tid]) - 1)]
                angle = "Your team's season is over: this player's season and what's next for him."
            elif alive_players:
                subj = alive_players[(k // 2) % len(alive_players)]
                angle = "Your team is out: a player still alive in the playoffs."
            else:
                continue
            plan["PLAYER"].append(_assignment("PLAYER", account, {"type": "player", "id": subj["player_id"],
                                                                  "label": f"{subj['name']} ({subj['position']}, {W.teams[subj['team_id']]['abbr']})"},
                                              {"angle": angle, **W.season_player_facts(subj)}, filed_team=tid))
            room -= 1
    elif room > 0 and recap:
        # Regular season: any room left goes to more player performances.
        add_performances(len(plan["PERFORMANCE"]) + room)
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
    counts = {k: len(v) for k, v in plan.items() if v}
    if not recap:
        notes.append("No completed games last week: no reactions, performances or memes; the room goes to previews.")
    if sum(counts.values()) != TARGET_POSTS:
        notes.append(f"Planned {sum(counts.values())} posts (target {TARGET_POSTS}): the week's article count and "
                     "the one-reaction-per-team floor move the total; replies flex between 10 and 15.")
    missing = [a for a in assignments if a["kind"] != "REPLY" and a["agent_id"] is None]
    if missing:
        notes.append(f"{len(missing)} posts have no account yet (placeholder shares, or a pool that ran out).")
    return {"season": season, "week": week, "recap_week": W.recap_week, "phase": phase,
            "assignments": assignments, "counts": counts, "notes": notes}


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
    total = BUCKET_SIZES["REPLY"] if total is None else total
    fantasy_active = fantasy_phase(ctx["week"]) is not None
    acc = Accounts(ctx["accounts"], seed=f"{ctx['season']}-{ctx['week']}-replies",
                   fantasy_enabled=fantasy_active).replay(plan["assignments"])
    parents = sorted((p for p in posts if p["assignment"]["kind"] != "REPLY" and p.get("text")),
                     key=lambda p: p.get("likes") or 0, reverse=True)
    out = []
    for parent, n in zip(parents, REPLY_SPREAD):
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

        if a.get("archetype") == "DIE_HARD_FAN":
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
            out[-1]["stance"] = REPLY_STANCES[made % len(REPLY_STANCES)]
            made += 1
        if len(out) >= total:
            break
    _add_slants(out, acc, Week(ctx))
    return out


# ------------------------------------------------------------------------------------------- team wakeup ---
def plan_team_wakeup(ctx: dict, team_id: int, already_posted=(), local_articles=()) -> dict:
    """Posts for an on-demand team wakeup (team page or Team Hub view), on top of the weekly 100.

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
        else:
            angle = BEAT_ANGLES[idx % len(BEAT_ANGLES)]
            out.append(_assignment("BEAT", b, {"type": "team", "id": team_id, "label": W.team_label(team_id)},
                                   W.beat_facts(team_id), filed_team=team_id, angle=angle))
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
            out.append(_assignment("REACTION", f, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                   W.game_facts(g, team_id), filed_team=team_id))
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
    args = ap.parse_args()

    import psycopg
    from db.db_handler import DatabaseHandler
    with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
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
