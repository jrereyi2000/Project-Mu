"""Agent Wire publishing (docs/SOCIAL_MATRIX.md §2–§5, build step 5).

Runs a planned week (newsroom/social_desk.py) end to end: writes each post with Gemini (newsroom/social_writer.py),
gives it a simulated posting time and computed engagement, saves it to news_feed with its tags and agent memory,
then writes the reply pass and recomputes the week's trends.

  run_social_week(conn_info, season, week)          # the weekly ~100 posts (called by newsroom/weekly_run.py)
  run_team_social(conn_info, season, week, team_id) # a team wakeup's posts (called by dispatch_team_local)

Posts are saved with content_type 'TWEET' and slot 'Agent Wire' (weekly) or 'Agent Wire Local' (team wakeups).
The pure helpers (calendar, engagement, trends) need no database and are unit-tested in tests/test_social_publish.py.
"""
import collections
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor

from newsroom.social_desk import (REGULAR_SEASON_FINAL_WEEK, Week, format_plan, load_week_context, plan_replies,
                                  plan_social_week, plan_team_wakeup)
from newsroom.social_writer import MONEY_PROBLEM, RUMOR_PROBLEM, allowed_money, build_post_prompt, money_values, write_post

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEKLY_SLOT = "Agent Wire"
LOCAL_SLOT = "Agent Wire Local"


def load_config() -> dict:
    with open(os.path.join(ROOT, "newsroom", "config", "social_desk.json")) as f:
        return json.load(f)


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16))


# --------------------------------------------------------------------------------------------- calendar ---
DAY_OFFSET = {"Thu": 0, "Fri": 1, "Sat": 2, "Sun": 3, "Mon": 4, "Tue": 5, "Wed": 6}
GAME_LENGTH = dt.timedelta(hours=3, minutes=10)


def week_thursday(season: int, week: int) -> dt.datetime:
    """Thursday that opens a week. Week 1's is the first Thursday after Labor Day (first Monday of September)
    in the season year; every week after is 7 days later, postseason included."""
    sept1 = dt.date(season, 9, 1)
    labor_day = sept1 + dt.timedelta(days=(0 - sept1.weekday()) % 7)
    thursday = labor_day + dt.timedelta(days=3 + 7 * (week - 1))
    return dt.datetime.combine(thursday, dt.time(0, 0))


def kickoff(season: int, game: dict) -> dt.datetime:
    """Kickoff from games.game_time ('Sun 4:15 PM'); unknown slots default to Sunday 1:00 PM."""
    day, clock = "Sun", "1:00 PM"
    parts = (game.get("game_time") or "").split(" ", 1)
    if len(parts) == 2 and parts[0] in DAY_OFFSET:
        day, clock = parts
    try:
        t = dt.datetime.strptime(clock.strip(), "%I:%M %p").time()
    except ValueError:
        t = dt.time(13, 0)
    return week_thursday(season, game["week"]) + dt.timedelta(days=DAY_OFFSET[day], hours=t.hour, minutes=t.minute)


def _between(rng, start: dt.datetime, end: dt.datetime) -> dt.datetime:
    if end <= start:
        return start
    return start + dt.timedelta(seconds=rng.uniform(0, (end - start).total_seconds()))


def _find_game(ctx, a):
    games = {g["id"]: g for g in ctx["recap_games"] + ctx["preview_games"]}
    if a["subject"]["type"] == "game":
        return games.get(a["subject"]["id"])
    team = a["subject"].get("team_id") or a.get("team_id")
    if a["kind"] in ("PERFORMANCE", "BAD_GAME") and team:
        return next((g for g in ctx["recap_games"] if team in (g["home_team_id"], g["away_team_id"])), None)
    return None


def _awake(t: dt.datetime, rng) -> dt.datetime:
    """Moves a time between 1:00 and 6:59 to that morning between 7:00 and 9:59."""
    if 1 <= t.hour < 7:
        return t.replace(hour=7 + rng.randrange(3), minute=rng.randrange(60))
    return t


def post_time(ctx: dict, a: dict, parent_time: dt.datetime = None) -> dt.datetime:
    """Simulated posting time; see _post_time. Planned posts (not reactions, game memes or replies) are kept to
    waking hours."""
    t = _post_time(ctx, a, parent_time)
    if a["kind"] in ("REACTION", "REPLY") or (a["kind"] == "MEME" and a["subject"].get("type") == "game"):
        return t
    rng = _rng("awake", ctx["season"], ctx["week"], a.get("agent_id"), a["kind"], a["subject"].get("id"))
    return _awake(t, rng)


def _post_time(ctx: dict, a: dict, parent_time: dt.datetime = None) -> dt.datetime:
    """Simulated posting time (docs/SOCIAL_MATRIX.md §3). Publication week N looks back at Week N-1's games
    (Thu–Mon) and is published Tue–Wed; previews run Thu–Sat of Week N before kickoff."""
    season, week = ctx["season"], ctx["week"]
    rng = _rng(season, week, a.get("agent_id"), a["kind"], a["subject"].get("type"), a["subject"].get("id"))
    this_thu = week_thursday(season, week)
    tue = this_thu - dt.timedelta(days=2)                 # Tuesday before this week's games
    if not ctx["recap_games"]:                            # preseason: the slate goes out the week before
        tue = this_thu - dt.timedelta(days=3)
    kind = a["kind"]
    game = _find_game(ctx, a)

    if kind == "REPLY" and parent_time:
        return parent_time + dt.timedelta(minutes=rng.uniform(3, 240))
    if kind in ("REACTION", "MEME") and game and game["week"] < week:
        end = kickoff(season, game) + GAME_LENGTH
        return end + dt.timedelta(minutes=rng.uniform(5, 90) if kind == "REACTION" else rng.uniform(30, 600))
    if kind in ("PERFORMANCE", "BAD_GAME") and game:
        end = kickoff(season, game) + GAME_LENGTH
        return _between(rng, end + dt.timedelta(minutes=20), max(end + dt.timedelta(hours=1), tue - dt.timedelta(hours=8)))
    if kind == "PREVIEW":
        if game:
            ko = kickoff(season, game)
            start = max(this_thu + dt.timedelta(hours=8), ko - dt.timedelta(days=2))
            return _between(rng, min(start, ko - dt.timedelta(hours=3)), ko - dt.timedelta(hours=1))
        return _between(rng, this_thu + dt.timedelta(days=1, hours=8), this_thu + dt.timedelta(days=3, hours=11))
    if kind == "COMPARISON":
        return _between(rng, tue + dt.timedelta(hours=9), this_thu + dt.timedelta(hours=20))
    if kind in ("TEAM_STORY", "MOVE_NEWS", "BEAT_MOVE", "MOVE_REACTION", "CAP_TAKE", "TRADE_NEWS", "TRADE_REACTION",
                "TRADE_RUMOR"):                       # Monday morning – Wednesday evening
        return _between(rng, tue - dt.timedelta(hours=15), tue + dt.timedelta(days=1, hours=20))
    if kind == "STANDINGS":
        return _between(rng, tue + dt.timedelta(hours=10), tue + dt.timedelta(hours=22))
    if kind == "PLAYER":
        return _between(rng, tue + dt.timedelta(hours=9), this_thu + dt.timedelta(days=2, hours=21))
    # BEAT, SHARE, draft MEME, anything else: Tuesday morning – Wednesday evening
    return _between(rng, tue + dt.timedelta(hours=7), tue + dt.timedelta(days=1, hours=20))


# ------------------------------------------------------------------------------------------- engagement ---
def account_role(account: dict, cfg: dict) -> str:
    arch = account.get("archetype")
    if arch == "DIE_HARD_FAN":
        return "fan"
    if arch == "BANDWAGON_FAN":
        return "bandwagon"
    if arch == "LOCAL_BEAT_REPORTER":
        return "beat"
    if arch == "FANTASY_GURU":
        return "fantasy_account"
    if arch == "NFL_MEMES":
        return "meme"
    if arch == "DRAFT_SICKO":
        return "draft"
    if arch == "FORMER_PLAYER":
        return "former"
    if arch in cfg.get("insider_archetypes", ()):
        return "insider"
    return "national"


def followers_for(account: dict, team_popularity: float, cfg: dict) -> int:
    """Simulated follower count, stable per account (seeded by agent_id). Beat reporters and fans scale with
    their team's popularity (0–100) on a log scale; other roles spread across their range."""
    role = account_role(account, cfg)
    lo, hi = cfg["followers"][role]
    u = _rng("followers", account["agent_id"]).random()
    x = u
    if role in ("beat", "fan") and team_popularity is not None:
        x = min(1.0, max(0.0, 0.6 * (team_popularity / 100.0) + 0.4 * u))
    return int(round(lo * (hi / lo) ** x, -1 if hi < 100000 else -3))


def subject_heat(ctx: dict, a: dict, cfg: dict) -> float:
    """How much attention the subject draws: game excitement (actual or projected) and team popularity,
    player popularity, or the slot of a shared article."""
    teams = ctx["teams"]
    game = _find_game(ctx, a)
    if a["kind"] == "SHARE":
        return cfg["share_slot_heat"].get((a.get("facts") or {}).get("slot") or "", 1.0)
    heat = 1.0
    if game:
        raw = game["excitement_index"] if game["week"] < ctx["week"] else game["projected_excitement"]
        disp = 70.0 if raw is None else min(100.0, max(70.0, 70.0 + 30.0 * (float(raw) - 40.0) / 60.0))
        heat = 0.7 + 0.9 * (disp - 70.0) / 30.0
        pops = [teams[t]["popularity"] for t in (game["home_team_id"], game["away_team_id"]) if t in teams]
        heat *= 0.75 + (sum(pops) / len(pops)) / 200.0 if pops else 1.0
    elif a["subject"]["type"] == "player":
        pop = next((l["popularity"] for l in ctx["player_lines"] if l["player_id"] == a["subject"]["id"]), 50.0)
        heat = 0.8 + min(100.0, pop) / 150.0
    elif a.get("team_id") in teams:
        heat = 0.75 + teams[a["team_id"]]["popularity"] / 200.0
    if a.get("buzz_tier"):                                # a game or team the league is talking about
        heat *= float(cfg.get("buzz_heat", {}).get(str(a["buzz_tier"]), 1.0))
    return heat


def engagement(followers: int, role: str, kind: str, heat: float, seed, cfg: dict) -> dict:
    """Likes, reposts, simulated reply count and views for one post (deterministic for a seed)."""
    rng = _rng("engagement", seed)
    noise = math.exp(rng.gauss(0, 0.5))
    likes = int(followers * cfg["like_rate"][role] * heat * cfg["kind_factor"].get(kind, 1.0) * noise)
    likes = max(likes, rng.randint(0, 3))
    repost_rate = rng.uniform(0.2, 0.4) if kind == "MEME" else rng.uniform(0.08, 0.2)
    return {"likes": likes, "reposts": int(likes * repost_rate),
            "reply_count": int(likes * rng.uniform(0.02, 0.06)), "views": int(likes * rng.uniform(25, 60)) + rng.randint(10, 200)}


# ----------------------------------------------------------------------------------------------- trends ---
def compute_trends(posts: list, lookups: dict, cfg: dict) -> list:
    """Weekly trends from saved posts (docs/SOCIAL_MATRIX.md §4): teams, players, games and hashtags, scored by
    engagement with a recency weight. posts: [{"id", "likes", "reposts", "reply_count", "posted_at", "text",
    "team_ids", "player_ids", "game_id", "hashtags"}]; lookups: {"teams": {id: {...}}, "players": {id: name},
    "games": {id: {...}}}."""
    if not posts:
        return []
    latest = max(p["posted_at"] for p in posts if p.get("posted_at")) if any(p.get("posted_at") for p in posts) else None
    cands = collections.defaultdict(lambda: {"score": 0.0, "posts": 0, "top": None, "top_eng": -1})
    for p in posts:
        eng = (p.get("likes") or 0) + 2 * (p.get("reposts") or 0) + 3 * (p.get("reply_count") or 0)
        w = 1.0
        if latest and p.get("posted_at"):
            hours = (latest - p["posted_at"]).total_seconds() / 3600.0
            w = 0.5 + 0.5 * math.exp(-hours / 72.0)
        keys = [("TEAM", t) for t in set(p.get("team_ids") or [])]
        keys += [("PLAYER", x) for x in set(p.get("player_ids") or [])]
        if p.get("game_id"):
            keys.append(("GAME", p["game_id"]))
        stop = set(cfg.get("trend_hashtag_stoplist", ()))
        keys += [("HASHTAG", h.lower()) for h in set(p.get("hashtags") or [])
                 if h.lower() not in stop and not re.fullmatch(r"week\d+", h.lower())]
        for k in keys:
            c = cands[k]
            c["score"] += eng * w
            c["posts"] += 1
            if eng > c["top_eng"]:
                c["top_eng"], c["top"] = eng, p
    rows = []
    teams, players, games = lookups["teams"], lookups["players"], lookups["games"]
    for (kind, key), c in cands.items():
        if c["posts"] < cfg["trend_min_posts"][kind]:
            continue
        if kind == "TEAM" and key in teams:
            t = teams[key]
            title, category = f"{t['city']} {t['name']}", t["division"]
        elif kind == "PLAYER" and key in players:
            title, category = players[key]["name"], players[key].get("team") or "Players"
        elif kind == "GAME" and key in games:
            g = games[key]
            title = f"{teams[g['away_team_id']]['name']}–{teams[g['home_team_id']]['name']}"
            category = f"Week {g['week']}" if g.get("game_type", "REGULAR_SEASON") == "REGULAR_SEASON" else "Playoffs"
        elif kind == "HASHTAG":
            title, category = f"#{c['top']['hashtags'][[h.lower() for h in c['top']['hashtags']].index(key)]}", "Trending"
        else:
            continue
        text = (c["top"].get("text") or "").replace("\n", " ")
        rows.append({"kind": kind, "key": str(key), "title": title, "category": category,
                     "description": text[:140] + ("…" if len(text) > 140 else ""),
                     "score": c["score"] * cfg["trend_kind_weight"][kind],
                     "volume": int(c["score"] / 40 + 30 * c["posts"])})
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:cfg.get("trend_count", 20)]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def refresh_trends(cur, season: int, week: int, cfg: dict) -> list:
    cur.execute("""
        SELECT n.id, n.likes, n.reposts, n.reply_count, n.posted_at, n.content, n.subject_game_id, n.hashtags,
               ARRAY(SELECT team_id FROM article_teams WHERE article_id = n.id) AS team_ids,
               ARRAY(SELECT player_id FROM article_players WHERE article_id = n.id) AS player_ids
        FROM news_feed n
        WHERE n.season = %s AND n.week = %s AND n.content_type = 'TWEET' AND n.slot LIKE 'Agent Wire%%'
    """, (season, week))
    cols = ("id", "likes", "reposts", "reply_count", "posted_at", "text", "game_id", "hashtags", "team_ids", "player_ids")
    posts = [dict(zip(cols, r)) for r in cur.fetchall()]
    for p in posts:
        if isinstance(p["posted_at"], str):
            p["posted_at"] = dt.datetime.fromisoformat(p["posted_at"])
    cur.execute("SELECT id, name, city, division FROM teams")
    teams = {r[0]: {"name": r[1], "city": r[2], "division": r[3]} for r in cur.fetchall()}
    pids = sorted({x for p in posts for x in (p["player_ids"] or [])})
    players = {}
    if pids:
        cur.execute("""SELECT p.id, COALESCE(p.full_name, p.player_name), t.name FROM players p
                       LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                       LEFT JOIN teams t ON t.id = ps.team_id WHERE p.id = ANY(%s)""", (season, pids))
        players = {r[0]: {"name": r[1], "team": r[2]} for r in cur.fetchall()}
    gids = sorted({p["game_id"] for p in posts if p["game_id"]})
    games = {}
    if gids:
        cur.execute("SELECT id, week, home_team_id, away_team_id, game_type FROM games WHERE id = ANY(%s)", (gids,))
        games = {r[0]: dict(zip(("id", "week", "home_team_id", "away_team_id", "game_type"), r)) for r in cur.fetchall()}
    rows = compute_trends(posts, {"teams": teams, "players": players, "games": games}, cfg)
    cur.execute("DELETE FROM social_trends WHERE season = %s AND week = %s", (season, week))
    for r in rows:
        cur.execute("""INSERT INTO social_trends (season, week, rank, kind, key, title, category, description, volume, score)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (season, week, r["rank"], r["kind"], r["key"], r["title"], r["category"], r["description"],
                     r["volume"], r["score"]))
    return rows


# ------------------------------------------------------------------------------------------------ saving ---
def social_slate_exists(cur, season: int, week: int) -> bool:
    cur.execute("""SELECT 1 FROM news_feed WHERE season = %s AND week = %s AND content_type = 'TWEET'
                   AND slot = %s LIMIT 1""", (season, week, WEEKLY_SLOT))
    return cur.fetchone() is not None


def ensure_followers(cur, ctx: dict, cfg: dict) -> dict:
    """Followers for every account, set once (agent_memories.followers) and returned as {agent_id: n}."""
    cur.execute("SELECT agent_id, archetype, assigned_team_id, followers FROM agent_memories WHERE handle IS NOT NULL")
    out = {}
    for agent_id, arch, team_id, followers in cur.fetchall():
        if followers is None:
            pop = ctx["teams"].get(team_id, {}).get("popularity") if team_id else None
            followers = followers_for({"agent_id": agent_id, "archetype": arch}, pop, cfg)
            cur.execute("UPDATE agent_memories SET followers = %s WHERE agent_id = %s", (followers, agent_id))
        out[agent_id] = int(followers)
    return out


def normalize_tweet_text(text: str) -> str:
    """Normalize tweet text for duplicate matching: lowercase, collapsed whitespace, trimmed punctuation."""
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    cleaned = cleaned.strip(".!?,;:\"'")
    return cleaned


def find_duplicate_content_tweet(cur, season: int, week: int, agent_id: str, content: str) -> int | None:
    """Returns the id of an existing tweet if the author already posted identical or normalized-matching content
    in the same season and week."""
    if not agent_id or not content:
        return None
    cur.execute("""
        SELECT id, content FROM news_feed
        WHERE season = %s AND week = %s AND agent_id = %s AND content_type = 'TWEET'
    """, (season, week, agent_id))
    target_norm = normalize_tweet_text(content)
    for existing_id, existing_content in cur.fetchall():
        if normalize_tweet_text(existing_content) == target_norm:
            return existing_id
    return None


def find_existing_tweet_for_assignment(cur, ctx: dict, a: dict, slot: str, parent_id: int = None) -> dict | None:
    """Find an existing tweet in news_feed that matches this planned assignment."""
    if not a.get("agent_id") or not a.get("kind"):
        return None
    subj = a.get("subject") or {}
    game = _find_game(ctx, a)
    game_id = game["id"] if game else (a.get("parent_game_id") if a.get("kind") == "REPLY" else None)
    art_id = subj.get("id") if a.get("kind") == "SHARE" else None
    player_id = subj.get("id") if subj.get("type") == "player" else None
    rep_id = parent_id or (a.get("parent") or {}).get("id")

    query = """
        SELECT id, content, likes, posted_at, agent_id
        FROM news_feed
        WHERE season = %s AND week = %s AND content_type = 'TWEET' AND slot = %s
          AND agent_id = %s AND post_kind = %s
          AND linked_article_id IS NOT DISTINCT FROM %s
          AND subject_game_id IS NOT DISTINCT FROM %s
          AND subject_player_id IS NOT DISTINCT FROM %s
          AND reply_to_id IS NOT DISTINCT FROM %s
        ORDER BY id ASC
        LIMIT 1
    """
    cur.execute(query, (ctx["season"], ctx["week"], slot, a.get("agent_id"), a.get("kind"),
                        art_id, game_id, player_id, rep_id))
    row = cur.fetchone()
    if row:
        return {"id": row[0], "text": row[1], "likes": row[2], "posted_at": row[3], "agent_id": row[4]}
    return None


def save_post(cur, ctx, a, post, posted_at, eng, slot, tagger=None, parent_id=None) -> int | None:
    """Insert one post into news_feed, tag its teams and players, and record it as the author's take.
    Returns post_id, or None if duplicate content or assignment already exists in news_feed."""
    dup_id = find_duplicate_content_tweet(cur, ctx["season"], ctx["week"], a.get("agent_id"), post.get("text"))
    if dup_id is not None:
        return None
    existing = find_existing_tweet_for_assignment(cur, ctx, a, slot, parent_id)
    if existing is not None:
        return None

    subj = a["subject"]
    from newsroom.fantasy_desk import fantasy_phase
    fantasy_active = fantasy_phase(ctx["week"]) is not None
    if not fantasy_active:
        if a.get("archetype") in ("FANTASY_GURU", "BERRY", "YATES") or a.get("subject", {}).get("type") == "fantasy":
            return None
        if re.search(r"\b(?:fantasy|full-ppr|\bppr\b|start/sit|waiver\s*wire)\b", post.get("text", ""), re.I):
            return None

    game = _find_game(ctx, a)
    game_id = game["id"] if game else (a.get("parent_game_id") if a["kind"] == "REPLY" else None)
    cur.execute("""
        INSERT INTO news_feed (season, week, team_id, content_type, persona, slot, content, created_at,
                               agent_id, post_kind, posted_at, reply_to_id, linked_article_id, subject_game_id,
                               subject_player_id, likes, reposts, reply_count, views, hashtags)
        VALUES (%s, %s, %s, 'TWEET', %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (ctx["season"], ctx["week"], a.get("team_id"), a["archetype"], slot, post["text"], a["agent_id"], a["kind"],
          posted_at, parent_id, subj["id"] if a["kind"] == "SHARE" else None, game_id,
          subj["id"] if subj["type"] == "player" else None, eng["likes"], eng["reposts"], eng["reply_count"],
          eng["views"], post.get("hashtags") or []))
    post_id = cur.fetchone()[0]
    if tagger:
        tagger.extract_and_store_entities(cur=cur, article_id=post_id, headline="", content=post["text"],
                                          season=ctx["season"], assigned_team_id=a.get("team_id"))
    team_ids = [game["home_team_id"], game["away_team_id"]] if game else []
    for t in team_ids:
        cur.execute("""INSERT INTO article_teams (article_id, team_id, mention_type, mention_count, created_at)
                       VALUES (%s, %s, 'MENTIONED', 1, NOW()) ON CONFLICT (article_id, team_id) DO NOTHING""", (post_id, t))
    if subj["type"] == "player" and subj["id"]:
        cur.execute("""INSERT INTO article_players (article_id, player_id, team_id, mention_type, mention_count, created_at)
                       VALUES (%s, %s, %s, 'PRIMARY', 1, NOW()) ON CONFLICT (article_id, player_id) DO NOTHING""",
                    (post_id, subj["id"], subj.get("team_id") or a.get("team_id")))
    if a["kind"] != "SHARE":
        try:
            from newsroom import agent_memory
            agent_memory.store_tweet_take(cur, a["agent_id"], ctx["season"], ctx["week"], post_id, post["text"], a.get("team_id"))
        except Exception as e:  # memory never blocks a post
            print(f"⚠️  MEMORY NOT RECORDED for post #{post_id}: {e}")
    return post_id


def publishable(post: dict, fantasy_active: bool = True) -> bool:
    if post.get("error") or not post.get("text"):
        return False
    blocking = ("empty", "mentions ratings or Madden", "mentions fantasy after season is over")
    if any(p.startswith((MONEY_PROBLEM, RUMOR_PROBLEM)) for p in post.get("problems", [])):
        return False                       # the money / rumor guards: still wrong after its one rewrite
    if not fantasy_active and re.search(r"\b(?:fantasy|full-ppr|\bppr\b|start/sit|waiver\s*wire)\b", post.get("text", ""), re.I):
        return False
    return not any(p in blocking or "over 280" in p for p in post.get("problems", []))


# --------------------------------------------------------------------------------------------------- run ---
class _Writer:
    """Builds prompts (needs the DB for memory blocks) and writes posts in parallel."""

    def __init__(self, cur, ctx, accounts_full, cfg, client, model, max_workers, log):
        self.cur, self.ctx, self.cfg, self.client, self.log = cur, ctx, cfg, client, log
        self.accounts = accounts_full
        self.model, self.max_workers = model, max_workers
        with open(os.path.join(ROOT, "newsroom", "config", "agent_personas.json")) as f:
            self.personas = json.load(f)["archetypes"]
        self.usage = collections.Counter()
        self.cost = 0.0
        try:                                            # every post's calls -> llm_usage (source agent_wire)
            from db.llm_usage import UsageMeter
            self.meter = getattr(client, "_usage_meter", None) or UsageMeter(client)
            client._usage_meter = self.meter            # one meter per client, so a reused client isn't counted twice
        except Exception:                               # a test's fake client has no request method to wrap
            self.meter = None

    def prompt(self, a):
        acct = self.accounts[a["agent_id"]]
        team = self.ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
        memory = ""
        try:
            from newsroom import agent_memory
            memory = agent_memory.build_memory_block(self.cur, a["agent_id"], self.ctx["season"], self.ctx["week"], a.get("team_id"))
        except Exception:
            pass
        return build_post_prompt(a, acct, self.personas.get(acct["archetype"], ""), team, memory, a.get("parent"))

    def write(self, assignments):
        prompts = [self.prompt(a) for a in assignments]
        from newsroom.fantasy_desk import fantasy_phase
        fantasy_active = fantasy_phase(self.ctx["week"]) is not None

        def one(args):
            a, (system, prompt) = args
            model = self.cfg.get("model_by_kind", {}).get(a["kind"], self.model)
            try:
                with self.meter.labelled(f"{a['agent_id']} {a['kind']}") if self.meter else contextlib.nullcontext():
                    return write_post(self.client, model, system, prompt, fantasy_active=fantasy_active,
                                      allowed=allowed_money(a.get("facts"))
                                      | money_values((a.get("parent") or {}).get("text", "")),
                                      rumor=a["kind"] == "TRADE_RUMOR")
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}", "text": "", "problems": ["call failed"]}

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            results = list(ex.map(one, zip(assignments, prompts)))
        for r in results:
            for k, v in (r.get("usage") or {}).items():
                self.usage[k] += v
            self.cost += r.get("cost_usd", 0.0)
        if self.meter:
            self.meter.record("agent_wire")
        return results


def _gemini_client():
    from google import genai
    from newsroom.social_writer import load_api_key
    key = load_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (environment or .env)")
    return genai.Client(api_key=key)


def _load_accounts_full(cur) -> dict:
    cur.execute("""SELECT agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name
                   FROM agent_memories WHERE handle IS NOT NULL""")
    return {r[0]: dict(zip(("agent_id", "archetype", "team_id", "persistent_memory", "handle", "display_name"), r))
            for r in cur.fetchall()}


def _publish(conn, cur, ctx, assignments, writer, followers, cfg, slot, tagger, log, parent_times=None):
    """Write, time, score and save assignments. Returns saved posts [{id, assignment, text, likes, ...}]."""
    saved, failed = [], 0
    to_write = []
    from newsroom.fantasy_desk import fantasy_phase
    fantasy_active = fantasy_phase(ctx["week"]) is not None
    if not fantasy_active:
        assignments = [a for a in assignments if a.get("archetype") not in ("FANTASY_GURU", "BERRY", "YATES")
                       and a.get("subject", {}).get("type") != "fantasy"]

    for a in assignments:
        parent = a.get("parent")
        parent_id = parent["id"] if parent else None
        existing = find_existing_tweet_for_assignment(cur, ctx, a, slot, parent_id)
        if existing:
            acct = writer.accounts.get(a.get("agent_id"), {})
            saved.append({"id": existing["id"], "assignment": a, "text": existing["text"], "likes": existing["likes"],
                          "posted_at": existing["posted_at"], "agent_id": a.get("agent_id"),
                          "handle": acct.get("handle", a.get("handle")), "display_name": acct.get("display_name", a.get("handle"))})
            log(f"⏭️  [ALREADY PUBLISHED] {a.get('handle', a.get('agent_id'))} {a['kind']} (#{existing['id']})")
        else:
            to_write.append(a)

    if not to_write:
        return saved, failed

    results = writer.write(to_write)
    for a, post in zip(to_write, results):
        if not publishable(post, fantasy_active=fantasy_active):
            failed += 1
            log(f"⏭️  [NOT SAVED] {a['handle']} {a['kind']} ({a['subject']['label']}): "
                f"{post.get('error') or ', '.join(post.get('problems', [])) or 'no text'}")
            continue
        parent = a.get("parent")
        posted_at = post_time(ctx, a, (parent_times or {}).get(parent["id"]) if parent else None)
        acct = writer.accounts[a["agent_id"]]
        role = account_role(acct, cfg)
        eng = engagement(followers.get(a["agent_id"], 1000), role, a["kind"], subject_heat(ctx, a, cfg),
                         (ctx["season"], ctx["week"], a["agent_id"], a["kind"], a["subject"]["id"], post["text"][:40]), cfg)
        try:
            post_id = save_post(cur, ctx, a, post, posted_at, eng, slot, tagger, parent["id"] if parent else None)
            if post_id is None:
                log(f"⏭️  [DUPLICATE SKIPPED] {a.get('handle', a.get('agent_id'))} {a['kind']}: duplicate tweet detected")
                continue
            conn.commit()
        except Exception as e:
            conn.rollback()
            failed += 1
            log(f"❌ [SAVE FAILED] {a['handle']} {a['kind']}: {e}")
            continue
        saved.append({"id": post_id, "assignment": a, "text": post["text"], "likes": eng["likes"], "posted_at": posted_at,
                      "agent_id": a["agent_id"], "handle": acct["handle"], "display_name": acct["display_name"]})
    return saved, failed


def run_social_week(conn_info: str, season: int, week: int, model: str = None, dry_run: bool = False,
                    force: bool = False, max_workers: int = None, client=None, tagger=None, log=print) -> dict:
    """Plan, write and publish the week's Agent Wire posts, then the reply pass and trends."""
    import psycopg
    cfg = load_config()
    model = model or cfg["model"]
    max_workers = max_workers or cfg.get("max_workers", 5)
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            if not dry_run and not force and social_slate_exists(cur, season, week):
                log(f"[Agent Wire] Season {season} Week {week} already has its social slate; skipping.")
                return {"status": "skipped_exists"}
            ctx = load_week_context(cur, season, week)
            plan = plan_social_week(ctx, use_expected_articles=dry_run)
            if dry_run:
                log(format_plan(plan, ctx["teams"]))
                return {"status": "dry_run", "planned": sum(plan["counts"].values())}
            followers = ensure_followers(cur, ctx, cfg)
            conn.commit()
            if tagger is None:
                from newsroom.swarm_dispatcher import EntityTaggingService
                tagger = EntityTaggingService(conn_info)
            client = client or _gemini_client()
            writer = _Writer(cur, ctx, _load_accounts_full(cur), cfg, client, model, max_workers, log)

            first = [a for a in plan["assignments"] if a["kind"] != "REPLY" and a.get("agent_id")]
            log(f"[Agent Wire] Writing {len(first)} posts for Season {season} Week {week} with {model}...")
            saved, failed = _publish(conn, cur, ctx, first, writer, followers, cfg, WEEKLY_SLOT, tagger, log)

            replies = plan_replies(ctx, plan, saved)
            for r in replies:
                parent = next(p for p in saved if p["id"] == r["parent"]["id"])
                g = _find_game(ctx, parent["assignment"])
                r["parent_game_id"] = g["id"] if g else None
            log(f"[Agent Wire] Writing {len(replies)} replies...")
            saved_r, failed_r = _publish(conn, cur, ctx, replies, writer, followers, cfg, WEEKLY_SLOT, tagger, log,
                                         parent_times={p["id"]: p["posted_at"] for p in saved})
            trends = refresh_trends(cur, season, week, cfg)
            conn.commit()
    total_saved = len(saved) + len(saved_r)
    log(f"[Agent Wire] Saved {total_saved} posts ({failed + failed_r} not saved); {len(trends)} trends. "
        f"Tokens: {dict(writer.usage)}; cost ${writer.cost:.4f}.")
    return {"status": "published", "saved": total_saved, "not_saved": failed + failed_r, "trends": len(trends),
            "cost_usd": round(writer.cost, 4), "usage": dict(writer.usage)}


def run_team_social(conn_info: str, season: int, week: int, team_id: int, local_articles=(), model: str = None,
                    max_workers: int = None, client=None, tagger=None, log=print) -> dict:
    """A team wakeup's Agent Wire posts (plan_team_wakeup), saved with slot 'Agent Wire Local'; trends refreshed.
    local_articles: [(article dict with id/headline/excerpt/article_type/slot, agent_id)] to share."""
    import psycopg
    cfg = load_config()
    model = model or cfg["model"]
    max_workers = max_workers or cfg.get("max_workers", 5)
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            ctx = load_week_context(cur, season, week)
            cur.execute("""SELECT DISTINCT agent_id FROM news_feed WHERE season = %s AND week = %s AND team_id = %s
                           AND slot = %s AND agent_id IS NOT NULL""", (season, week, team_id, LOCAL_SLOT))
            already = {r[0] for r in cur.fetchall()}
            plan = plan_team_wakeup(ctx, team_id, already, local_articles)
            if not plan["assignments"]:
                log(f"[Agent Wire] Team {team_id} Week {week}: nothing new to post.")
                return {"status": "nothing_to_post"}
            followers = ensure_followers(cur, ctx, cfg)
            conn.commit()
            if tagger is None:
                from newsroom.swarm_dispatcher import EntityTaggingService
                tagger = EntityTaggingService(conn_info)
            client = client or _gemini_client()
            writer = _Writer(cur, ctx, _load_accounts_full(cur), cfg, client, model, max_workers, log)
            log(f"[Agent Wire] Team {team_id} wakeup: writing {len(plan['assignments'])} posts...")
            saved, failed = _publish(conn, cur, ctx, plan["assignments"], writer, followers, cfg, LOCAL_SLOT, tagger, log)
            trends = refresh_trends(cur, season, week, cfg)
            conn.commit()
    log(f"[Agent Wire] Team {team_id}: saved {len(saved)} posts ({failed} not saved); cost ${writer.cost:.4f}.")
    return {"status": "published", "saved": len(saved), "not_saved": failed, "trends": len(trends),
            "cost_usd": round(writer.cost, 4)}
