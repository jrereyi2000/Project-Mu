"""Agent Wire read API (docs/done/SOCIAL_MATRIX.md §7, build step 6).

Query functions take a dict-row cursor and return JSON-ready dicts; server/api.py exposes them under
/api/social/*. Only posts written by the social desk (news_feed rows with content_type 'TWEET' and an agent_id)
are served; the old reaction tweets have no agent_id and never appear.
"""
import datetime as dt
import hashlib
import math

FANTASY_ARCHETYPES = ("FANTASY_GURU", "BERRY", "YATES")
ARCHETYPE_LABELS = {
    "DIE_HARD_FAN": "Die-Hard Fan",
    "BANDWAGON_FAN": "Bandwagon Fan",
    "LOCAL_BEAT_REPORTER": "Beat Reporter",
    "FANTASY_GURU": "Fantasy Analyst",
    "NFL_MEMES": "Meme Page",
    "DRAFT_SICKO": "Draft Analyst",
    "SCHEFTER_TWEET": "NFL Insider",
    "RAPOPORT": "NFL Insider",
    "THE_ATHLETIC": "Publication",
    "BERRY": "Fantasy Analyst",
    "YATES": "Fantasy Analyst",
}
KIND_LABELS = {"REACTION": "Reaction", "PERFORMANCE": "Performance", "BAD_GAME": "Bad Game", "COMPARISON": "Comparison", "TEAM_STORY": "Storyline",
               "PLAYER": "Player", "BEAT": "Beat Note",
               "STANDINGS": "Standings", "SHARE": "Article", "PREVIEW": "Preview", "MEME": "Meme", "REPLY": "Reply"}

POST_SELECT = """
    SELECT n.id, n.season, n.week, n.post_kind, n.content AS text, n.posted_at, n.likes, n.reposts, n.reply_count,
           n.views, n.hashtags, n.reply_to_id, n.linked_article_id, n.subject_game_id, n.subject_player_id,
           n.team_id, n.slot,
           a.agent_id, a.handle, a.display_name, a.archetype, a.followers, a.assigned_team_id,
           aut.abbreviation AS author_team_abbr, aut.name AS author_team_name, aut.team_color AS author_team_color,
           ft.abbreviation AS team_abbr,
           (SELECT COUNT(*) FROM news_feed r WHERE r.reply_to_id = n.id) AS real_replies,
           la.headline AS article_headline, la.excerpt AS article_excerpt, la.article_type AS article_type,
           pa.handle AS parent_handle, pa.display_name AS parent_display_name
    FROM news_feed n
    JOIN agent_memories a ON a.agent_id = n.agent_id
    LEFT JOIN teams aut ON aut.id = a.assigned_team_id
    LEFT JOIN teams ft ON ft.id = n.team_id
    LEFT JOIN news_feed la ON la.id = n.linked_article_id
    LEFT JOIN news_feed pn ON pn.id = n.reply_to_id
    LEFT JOIN agent_memories pa ON pa.agent_id = pn.agent_id
    WHERE n.content_type = 'TWEET' AND n.agent_id IS NOT NULL
"""


def _team_abbr(abbr):
    a = (abbr or "").upper()
    return {"AZ": "ARI", "WAS": "WSH"}.get(a, a)


def archetype_label(row) -> str:
    arch = row.get("archetype")
    if arch == "LOCAL_BEAT_REPORTER" and row.get("author_team_name"):
        return f"{row['author_team_name']} Beat Reporter"
    if arch == "DIE_HARD_FAN" and row.get("author_team_name"):
        return f"{row['author_team_name']} Fan"
    if arch == "FORMER_PLAYER":
        return f"Former {row['author_team_name']}" if row.get("author_team_name") else "Former Player"
    return ARCHETYPE_LABELS.get(arch, "Analyst")


def _iso(t):
    if t is None:
        return None
    return t.isoformat() if isinstance(t, (dt.datetime, dt.date)) else str(t)


def shape_post(row) -> dict:
    replies = int(row.get("reply_count") or 0) + int(row.get("real_replies") or 0)
    return {
        "id": row["id"],
        "season": row["season"],
        "week": row["week"],
        "kind": row.get("post_kind"),
        "kind_label": KIND_LABELS.get(row.get("post_kind"), ""),
        "text": row.get("text") or "",
        "posted_at": _iso(row.get("posted_at")),
        "likes": int(row.get("likes") or 0),
        "reposts": int(row.get("reposts") or 0),
        "replies": replies,
        "real_replies": int(row.get("real_replies") or 0),
        "views": int(row.get("views") or 0),
        "hashtags": list(row.get("hashtags") or []),
        "team_abbr": _team_abbr(row.get("team_abbr")) or None,
        "local": (row.get("slot") or "") == "Agent Wire Local",
        "subject_game_id": row.get("subject_game_id"),
        "subject_player_id": row.get("subject_player_id"),
        "author": {
            "agent_id": row["agent_id"],
            "handle": row["handle"],
            "display_name": row.get("display_name") or row["handle"],
            "archetype": row.get("archetype"),
            "label": archetype_label(row),
            "team_abbr": _team_abbr(row.get("author_team_abbr")) or None,
            "team_color": row.get("author_team_color"),
            "followers": int(row.get("followers") or 0),
        },
        "reply_to": {"id": row["reply_to_id"], "handle": row.get("parent_handle"),
                     "display_name": row.get("parent_display_name")} if row.get("reply_to_id") else None,
        "article": {"id": row["linked_article_id"], "headline": row.get("article_headline"),
                    "excerpt": row.get("article_excerpt"), "article_type": row.get("article_type")}
                   if row.get("linked_article_id") else None,
    }


def _parse_time(t):
    if isinstance(t, str):
        return dt.datetime.fromisoformat(t)
    return t


def engagement_score(p: dict) -> float:
    return p["likes"] + 2 * p["reposts"] + 3 * p["replies"]


def for_you_order(posts: list, as_of) -> list:
    """Engagement with a mild recency weight (from 1.0 for the newest post down to 0.5 for old ones, the same
    weighting as the trends), relative to the week's latest post, so Sunday's reactions still lead the week."""
    as_of = _parse_time(as_of)

    def score(p):
        t = _parse_time(p["posted_at"])
        hours = max(0.0, (as_of - t).total_seconds() / 3600.0) if (as_of and t) else 0.0
        return engagement_score(p) * (0.5 + 0.5 * math.exp(-hours / 72.0))
    return sorted(posts, key=score, reverse=True)


# ------------------------------------------------------------------------------------------------ queries ---
def current_season(cur) -> int:
    cur.execute("SELECT current_season FROM league_state LIMIT 1")
    row = cur.fetchone()
    return row["current_season"] if row else 2035


def social_weeks(cur, season: int) -> list:
    cur.execute("""SELECT DISTINCT week FROM news_feed WHERE season = %s AND content_type = 'TWEET'
                   AND agent_id IS NOT NULL ORDER BY week""", (season,))
    return [r["week"] for r in cur.fetchall()]


def resolve_week(cur, season: int, week):
    weeks = social_weeks(cur, season)
    if week is not None:
        return week, weeks
    return (weeks[-1] if weeks else None), weeks


def _team_id(cur, abbr):
    a = (abbr or "").upper().strip()
    alt = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}.get(a, a)
    cur.execute("SELECT id, name, city, abbreviation, team_color, conference, division FROM teams "
                "WHERE abbreviation = %s OR abbreviation = %s", (a, alt))
    return cur.fetchone()


def feed(cur, season=None, week=None, tab="foryou", team=None, trend=None, limit=30, offset=0) -> dict:
    """The Home / team / trend feed for one week: top-level posts (no replies).

    trend: "KIND:KEY" from /api/social/trends (TEAM:12, PLAYER:345, GAME:678, HASHTAG:billsmafia)."""
    season = season or current_season(cur)
    week, weeks = resolve_week(cur, season, week)
    out = {"season": season, "week": week, "weeks": weeks, "tab": tab, "posts": [], "as_of": None, "next_offset": None}
    if week is None:
        return out
    sql = POST_SELECT + " AND n.season = %s AND n.week = %s AND n.reply_to_id IS NULL"
    params = [season, week]
    if team:
        t = _team_id(cur, team)
        if not t:
            return out
        sql += " AND (n.team_id = %s OR EXISTS (SELECT 1 FROM article_teams x WHERE x.article_id = n.id AND x.team_id = %s))"
        params += [t["id"], t["id"]]
    if trend and ":" in trend:
        kind, key = trend.split(":", 1)
        kind = kind.upper()
        if kind == "TEAM":
            sql += " AND EXISTS (SELECT 1 FROM article_teams x WHERE x.article_id = n.id AND x.team_id = %s)"
            params.append(int(key))
        elif kind == "PLAYER":
            sql += " AND EXISTS (SELECT 1 FROM article_players x WHERE x.article_id = n.id AND x.player_id = %s)"
            params.append(int(key))
        elif kind == "GAME":
            sql += " AND n.subject_game_id = %s"
            params.append(int(key))
        elif kind == "HASHTAG":
            sql += " AND EXISTS (SELECT 1 FROM unnest(n.hashtags) h WHERE lower(h) = %s)"
            params.append(key.lower().lstrip("#"))
    cur.execute(sql, params)
    posts = [shape_post(r) for r in cur.fetchall()]
    as_of = max((p["posted_at"] for p in posts if p["posted_at"]), default=None)
    out["as_of"] = as_of
    if tab == "latest":
        posts.sort(key=lambda p: p["posted_at"] or "", reverse=True)
    else:
        posts = for_you_order(posts, as_of)
    out["posts"] = posts[offset:offset + limit]
    out["next_offset"] = offset + limit if offset + limit < len(posts) else None
    out["total"] = len(posts)
    return out


def post_detail(cur, post_id: int) -> dict:
    cur.execute(POST_SELECT + " AND n.id = %s", (post_id,))
    row = cur.fetchone()
    if not row:
        return {"error": "Post not found."}
    post = shape_post(row)
    cur.execute(POST_SELECT + " AND n.reply_to_id = %s ORDER BY n.posted_at", (post_id,))
    replies = [shape_post(r) for r in cur.fetchall()]
    parent = None
    if post["reply_to"]:
        cur.execute(POST_SELECT + " AND n.id = %s", (post["reply_to"]["id"],))
        prow = cur.fetchone()
        parent = shape_post(prow) if prow else None
    return {"post": post, "parent": parent, "replies": replies}


def _following(handle: str) -> int:
    return 80 + int(hashlib.md5(handle.encode()).hexdigest()[:6], 16) % 900


def profile(cur, handle: str, season=None, limit=40, offset=0) -> dict:
    """An account's profile and posts. Accounts that share a handle (Skip's column byline and his tweet account,
    both @RealSkipBayless) show as one profile."""
    season = season or current_season(cur)
    h = handle if handle.startswith("@") else f"@{handle}"
    cur.execute("""
        SELECT a.agent_id, a.archetype, a.handle, a.display_name, a.bio, a.followers, a.assigned_team_id,
               t.abbreviation AS author_team_abbr, t.name AS author_team_name, t.team_color AS author_team_color,
               (SELECT COUNT(*) FROM news_feed n WHERE n.agent_id = a.agent_id AND n.content_type = 'TWEET') AS posts
        FROM agent_memories a LEFT JOIN teams t ON t.id = a.assigned_team_id
        WHERE lower(a.handle) = lower(%s)
        ORDER BY posts DESC, a.followers DESC NULLS LAST
    """, (h,))
    accounts = cur.fetchall()
    if not accounts:
        return {"error": f"No account {h}."}
    main = accounts[0]
    ids = [a["agent_id"] for a in accounts]
    cur.execute(POST_SELECT + " AND n.agent_id = ANY(%s) AND n.season = %s ORDER BY n.posted_at DESC NULLS LAST",
                (ids, season))
    posts = [shape_post(r) for r in cur.fetchall()]
    return {
        "account": {
            "handle": main["handle"],
            "display_name": main.get("display_name") or main["handle"],
            "bio": main.get("bio") or "",
            "label": archetype_label(main),
            "archetype": main["archetype"],
            "team_abbr": _team_abbr(main.get("author_team_abbr")) or None,
            "team_color": main.get("author_team_color"),
            "followers": max(int(a.get("followers") or 0) for a in accounts),
            "following": _following(main["handle"]),
            "agent_ids": ids,
        },
        "season": season,
        "post_count": len(posts),
        "posts": posts[offset:offset + limit],
        "next_offset": offset + limit if offset + limit < len(posts) else None,
    }


def trends(cur, season=None, week=None, limit=20) -> dict:
    season = season or current_season(cur)
    week, _ = resolve_week(cur, season, week)
    if week is None:
        return {"season": season, "week": None, "trends": []}
    cur.execute("""SELECT rank, kind, key, title, category, description, volume FROM social_trends
                   WHERE season = %s AND week = %s ORDER BY rank LIMIT %s""", (season, week, limit))
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["id"] = f"{r['kind']}:{r['key']}"
    return {"season": season, "week": week, "trends": rows}


def search(cur, q: str, season=None, limit=40) -> dict:
    """Posts this season whose text, author name or handle matches, plus posts about a matching team."""
    season = season or current_season(cur)
    q = (q or "").strip()
    if len(q) < 2:
        return {"q": q, "posts": [], "accounts": []}
    like = f"%{q}%"
    cur.execute(POST_SELECT + """ AND n.season = %s AND (n.content ILIKE %s OR a.handle ILIKE %s OR a.display_name ILIKE %s
                 OR EXISTS (SELECT 1 FROM article_teams x JOIN teams t ON t.id = x.team_id
                            WHERE x.article_id = n.id AND (t.name ILIKE %s OR t.city ILIKE %s)))
                 ORDER BY n.posted_at DESC NULLS LAST LIMIT %s""", (season, like, like, like, like, like, limit))
    posts = [shape_post(r) for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ON (a.handle) a.handle, a.display_name, a.archetype, a.followers,
                          t.name AS author_team_name, t.abbreviation AS author_team_abbr
                   FROM agent_memories a LEFT JOIN teams t ON t.id = a.assigned_team_id
                   WHERE a.handle IS NOT NULL AND a.archetype <> 'ROUNDTABLE_PANEL'
                     AND (a.handle ILIKE %s OR a.display_name ILIKE %s)
                   ORDER BY a.handle, a.followers DESC NULLS LAST""", (like, like))
    accounts = [{"handle": r["handle"], "display_name": r["display_name"], "label": archetype_label(r),
                 "team_abbr": _team_abbr(r.get("author_team_abbr")) or None, "followers": int(r.get("followers") or 0)}
                for r in cur.fetchall()]
    accounts.sort(key=lambda a: a["followers"], reverse=True)
    return {"q": q, "posts": posts, "accounts": accounts[:8]}


def suggestions(cur, season=None, week=None, limit=3) -> dict:
    """Who to follow: the accounts whose posts drew the most likes this week, one per handle."""
    season = season or current_season(cur)
    week, _ = resolve_week(cur, season, week)
    if week is None:
        return {"accounts": []}
    cur.execute("""
        SELECT a.handle, MAX(a.display_name) AS display_name, MAX(a.archetype) AS archetype,
               MAX(a.bio) AS bio, MAX(a.followers) AS followers, MAX(t.name) AS author_team_name,
               MAX(t.abbreviation) AS author_team_abbr, SUM(n.likes) AS week_likes
        FROM news_feed n JOIN agent_memories a ON a.agent_id = n.agent_id
        LEFT JOIN teams t ON t.id = a.assigned_team_id
        WHERE n.season = %s AND n.week = %s AND n.content_type = 'TWEET'
        GROUP BY a.handle ORDER BY week_likes DESC NULLS LAST LIMIT %s
    """, (season, week, limit))
    return {"accounts": [{"handle": r["handle"], "display_name": r["display_name"], "label": archetype_label(r),
                          "bio": r.get("bio") or "", "followers": int(r.get("followers") or 0),
                          "team_abbr": _team_abbr(r.get("author_team_abbr")) or None} for r in cur.fetchall()]}


def team_hub(cur, abbr: str, season=None, week=None, tab="latest", limit=40, offset=0) -> dict:
    """A Team Hub: the team's posts for the week (filed under it or tagged with it) and its record."""
    season = season or current_season(cur)
    t = _team_id(cur, abbr)
    if not t:
        return {"error": f"Team '{abbr}' not found."}
    data = feed(cur, season, week, tab, team=abbr, limit=limit, offset=offset)
    cur.execute("SELECT wins, losses FROM team_seasons WHERE team_id = %s AND season = %s", (t["id"], season))
    rec = cur.fetchone()
    data["team"] = {"id": t["id"], "abbr": _team_abbr(t["abbreviation"]), "name": t["name"], "city": t["city"],
                    "team_color": t.get("team_color"), "division": t.get("division"),
                    "record": f"{rec['wins']}-{rec['losses']}" if rec else None}
    return data


def fantasy_takes(cur, season: int, week: int, limit: int = 3) -> list:
    """The fantasy page's social takes: the week's most-liked posts by the fantasy accounts (no replies, no
    shares). Shaped like the old placeholder: {quote, author, tag}, plus id and handle."""
    cur.execute(POST_SELECT + """ AND n.season = %s AND n.week = %s AND a.archetype = ANY(%s)
                 AND n.reply_to_id IS NULL AND n.post_kind <> 'SHARE' ORDER BY n.likes DESC NULLS LAST LIMIT %s""",
                (season, week, list(FANTASY_ARCHETYPES), limit))
    rows = cur.fetchall()
    out = []
    for r in rows:
        tag = "START / SIT" if r.get("post_kind") == "PREVIEW" else KIND_LABELS.get(r.get("post_kind"), "").upper()
        if r.get("subject_player_id"):
            cur.execute("""SELECT COALESCE(p.full_name, p.player_name) AS name, ps.position, t.abbreviation
                           FROM players p LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                           LEFT JOIN teams t ON t.id = ps.team_id WHERE p.id = %s""", (season, r["subject_player_id"]))
            pr = cur.fetchone()
            if pr:
                tag = f"{_team_abbr(pr.get('abbreviation'))} • {pr.get('position') or ''}".strip(" •")
        out.append({"id": r["id"], "quote": r["text"], "author": (r.get("display_name") or r["handle"]).upper(),
                    "handle": r["handle"], "tag": tag})
    return out
