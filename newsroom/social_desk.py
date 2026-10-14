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

from newsroom.fantasy_desk import fantasy_phase
from newsroom.season_landscape import REGULAR_SEASON_FINAL_WEEK, division_standings

TARGET_POSTS = 100

# Fixed bucket sizes (docs/SOCIAL_MATRIX.md §2). Reactions absorb the difference to TARGET_POSTS; shares are
# one per published article, so their count varies week to week.
BUCKET_SIZES = {
    "PERFORMANCE": 12,
    "BEAT": 6,
    "STANDINGS": 4,
    "PREVIEW": 8,
    "MEME": 2,
    "REPLY": 20,
}

# Weekly post caps per account role (least-used accounts are picked first).
ROLE_CAPS = {"pundit": 3, "fantasy": 4, "beat": 2, "fan": 2, "bandwagon": 2, "meme": 3, "draft": 3}

# Archetypes that never post as a random pundit. Mirrors swarm_dispatcher.TWEET_EXCLUDED_ARCHETYPES (the
# long-form-only writers, SKIP_BAYLESS, the roundtable panel), plus roles the desk assigns on purpose.
PUNDIT_EXCLUDED = {
    "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "KIMES", "ORLOVSKY",
    "SKIP_BAYLESS", "ROUNDTABLE_PANEL",
    "LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN",
    "FANTASY_GURU", "NFL_MEMES", "DRAFT_SICKO",
}
FANTASY_ARCHETYPES = ("FANTASY_GURU", "BERRY", "YATES")

# Pundits who fit a subject position best; used as a preference, never a requirement.
PUNDIT_AFFINITY = {
    "QB": ("RG3", "COWHERD", "STEPHEN_A", "SKIP_TWEET", "HERBSTREIT", "MCAFEE"),
    "WR": ("OCHOCINCO", "SHANNON", "MCAFEE", "SIMMONS"),
    "TE": ("SHANNON", "MCAFEE", "OCHOCINCO"),
    "HB": ("CLARK", "JJ_WATT", "EISEN", "WINGO"),
    "RB": ("CLARK", "JJ_WATT", "EISEN", "WINGO"),
}

# Articles shared from a different account than their byline: Skip's column byline shares from his tweet
# account (both are @RealSkipBayless). The roundtable panel never posts.
SHARE_ACCOUNT_ARCHETYPE = {"SKIP_BAYLESS": "SKIP_TWEET"}
NO_SHARE_ARCHETYPES = {"ROUNDTABLE_PANEL"}

# Posting windows (simulated time is assigned in build step 5).
WINDOWS = {
    "REACTION": "after its game ends",
    "PERFORMANCE": "Sun night – Mon",
    "BEAT": "Mon – Wed",
    "STANDINGS": "Tue",
    "SHARE": "when the slate publishes (Tue – Wed)",
    "REPLY": "minutes to hours after its parent",
    "PREVIEW": "Thu – Sat, before kickoff",
    "MEME": "any time",
    "PLAYER": "Mon – Sat",
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
        SELECT agent_id, archetype, assigned_team_id, handle, display_name
        FROM agent_memories WHERE handle IS NOT NULL ORDER BY agent_id
    """)
    ctx["accounts"] = [dict(zip(("agent_id", "archetype", "team_id", "handle", "display_name"), r))
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
        lines.append(d)
    ctx["player_lines"] = lines

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
    return ctx


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
        self.season_totals = self._season_totals()

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
        same_pos = sorted((v for v in self.season_totals.values() if v["position"] == t["position"]),
                          key=lambda v: v["fantasy_points"], reverse=True)
        rank = next(i for i, v in enumerate(same_pos, 1) if v is t)
        return f"{t['games']} games: {stat_line(t)}", f"{t['position']}{rank} in full-PPR points ({t['fantasy_points']:.1f})"

    def performance_facts(self, l):
        g = next(g for g in self.ctx["recap_games"] if g["id"] == l["game_id"])
        opp = g["away_team_id"] if g["home_team_id"] == l["team_id"] else g["home_team_id"]
        w, _ = self.winner_loser(g)
        season, rank = self.season_line(l)
        return {
            "player": f"{l['name']} ({l['position']}, {self.team_label(l['team_id'])})",
            "week": l["week"],
            "stat_line": stat_line(l),
            "fantasy_points_ppr": round(l["fantasy_points"], 1),
            "game": self.game_label(g),
            "team_result": "won" if w == l["team_id"] else "lost" if w else "tied",
            "opponent": self.team_label(opp),
            "season_so_far": season,
            "season_rank": rank,
        }

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

    def __init__(self, accounts, seed):
        self.rng = random.Random(seed)
        self.uses = collections.Counter()
        self.by_id = {a["agent_id"]: a for a in accounts}
        self.order = {a["agent_id"]: self.rng.random() for a in accounts}  # stable tie-break
        self.role = {}
        for a in accounts:
            arch = a["archetype"]
            self.role[a["agent_id"]] = (
                "fan" if arch == "DIE_HARD_FAN" else
                "bandwagon" if arch == "BANDWAGON_FAN" else
                "beat" if arch == "LOCAL_BEAT_REPORTER" else
                "meme" if arch == "NFL_MEMES" else
                "draft" if arch == "DRAFT_SICKO" else
                "fantasy" if arch == "FANTASY_GURU" else
                "pundit" if arch not in PUNDIT_EXCLUDED else
                None
            )

    def _pick(self, candidates, prefer=(), cap_role=None):
        prefer = set(prefer)
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
        return self._pick([a for a in self.all() if a["archetype"] in FANTASY_ARCHETYPES], cap_role="fantasy",
                          prefer=("FANTASY_GURU",))

    def archetype(self, arch):
        return self._pick([a for a in self.all() if a["archetype"] == arch], cap_role="meme")

    def by_archetype_uncapped(self, arch):
        """The account that shares an article (shares never count against caps)."""
        for a in sorted(self.all(), key=lambda a: a["agent_id"]):
            if a["archetype"] == arch:
                return a
        return None


# --------------------------------------------------------------------------------------------- planner ---
def _assignment(kind, account, subject, facts, filed_team=None):
    return {
        "kind": kind,
        "agent_id": account["agent_id"] if account else None,
        "handle": account["handle"] if account else None,
        "archetype": account["archetype"] if account else None,
        "team_id": filed_team if filed_team is not None else (account or {}).get("team_id"),
        "subject": subject,
        "window": WINDOWS.get(kind, ""),
        "facts": facts,
    }


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
    acc = Accounts(ctx["accounts"], seed=f"{season}-{week}")
    notes = []
    plan = collections.OrderedDict((k, []) for k in
                                   ("SHARE", "REACTION", "PERFORMANCE", "PLAYER", "BEAT", "STANDINGS", "PREVIEW", "MEME", "REPLY"))

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
        if art["persona"] in NO_SHARE_ARCHETYPES:
            continue
        account = None
        if art["persona"] == "LOCAL_BEAT_REPORTER":
            account = acc.beat(art["team_id"])
        elif art["persona"]:
            account = acc.by_archetype_uncapped(SHARE_ACCOUNT_ARCHETYPE.get(art["persona"], art["persona"]))
        plan["SHARE"].append(_assignment("SHARE", account,
                                         {"type": "article", "id": art["id"], "label": art["headline"]},
                                         {k: art[k] for k in ("headline", "excerpt", "article_type", "slot")},
                                         filed_team=art["team_id"]))

    # 2. Player performances from the recap week (offense only until the capture loads defense/kicking).
    perf_n = BUCKET_SIZES["PERFORMANCE"] if recap else 0
    top_lines = sorted(W.recap_lines, key=lambda l: (l["fantasy_points"], l["popularity"]), reverse=True)
    seen_players = set()
    pattern = ["fantasy", "pundit", "fan", "pundit", "fan", "fantasy"]
    for l in top_lines:
        if len(plan["PERFORMANCE"]) >= perf_n:
            break
        key = l["player_id"] or (l["short_name"], l["team_id"])
        if key in seen_players:
            continue
        seen_players.add(key)
        who = pattern[len(plan["PERFORMANCE"]) % len(pattern)]
        account = (acc.fantasy() if who == "fantasy" else
                   acc.fan(l["team_id"]) if who == "fan" else None)
        if account is None:
            account = acc.pundit(prefer=PUNDIT_AFFINITY.get(l["position"], ()))
        plan["PERFORMANCE"].append(_assignment("PERFORMANCE", account,
                                               {"type": "player", "id": l["player_id"], "label": f"{l['name']} ({l['position']}, {W.teams[l['team_id']]['abbr']})"},
                                               W.performance_facts(l), filed_team=l["team_id"]))

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
        last = next((g for g in recap if tid in (g["home_team_id"], g["away_team_id"])), None)
        nxt = next((g for g in previews if tid in (g["home_team_id"], g["away_team_id"])), None)
        plan["BEAT"].append(_assignment("BEAT", account, {"type": "team", "id": tid, "label": W.team_label(tid)}, {
            "team": W.team_with_record(tid),
            "last_game": W.game_facts(last, tid) if last else "Did not play last week.",
            "next_game": W.preview_facts(nxt, tid) if nxt else "No game this week.",
        }, filed_team=tid))

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
            account = acc.bandwagon() if i == 3 else acc.pundit()
            plan["STANDINGS"].append(_assignment("STANDINGS", account, {"type": "standings", "id": None, "label": f"{title}: {scope}"},
                                                 {"topic": title, **facts, "through_week": min(week - 1, REGULAR_SEASON_FINAL_WEEK)}))

    # 5. Previews: both fan bases on the top 3 projected games, plus fantasy start/sit (in-season fantasy weeks).
    fantasy_previews = 2 if fantasy_phase(week) and 2 <= week <= 17 and ctx["projections"] else 0
    for g in previews[:3]:
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

    # 7. Replies: parents are chosen after waves 1–5 are written and scored (build step 5).
    for i in range(BUCKET_SIZES["REPLY"]):
        plan["REPLY"].append(_assignment("REPLY", None, {"type": "post", "id": None,
                                                         "label": "parent chosen from the most engaged posts after writing"}, {}))

    # 8. Reactions fill the week: at least one die-hard post per team that played; extras go to the top games.
    fixed = sum(len(v) for k, v in plan.items() if k != "REACTION")
    budget = TARGET_POSTS - fixed
    for g in recap:
        for tid in (g["away_team_id"], g["home_team_id"]):
            account = acc.fan(tid)
            if account:
                plan["REACTION"].append(_assignment("REACTION", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                                    W.game_facts(g, tid), filed_team=tid))
    floor = len(plan["REACTION"])
    extra_room = budget - floor
    if postseason and recap:
        # Postseason: a few extra reactions on the top games, the rest are eliminated fans' player posts.
        reaction_extras = min(extra_room, 2 * len(recap))
    else:
        reaction_extras = extra_room if recap else 0
    rotation = ["pundit", "bandwagon", "fan_winner", "pundit", "fan_loser"]
    i = 0
    top = recap[:5]
    while reaction_extras > 0 and top and i < 200:
        g = top[i % len(top)]
        who = rotation[(i // len(top)) % len(rotation)]
        w, l = W.winner_loser(g)
        tid = None
        if who == "pundit":
            account = acc.pundit()
        elif who == "bandwagon":
            account = acc.bandwagon()
        else:
            tid = w if who == "fan_winner" else l
            account = acc.fan(tid) if tid else None
        i += 1
        if not account:
            continue
        plan["REACTION"].append(_assignment("REACTION", account, {"type": "game", "id": g["id"], "label": W.game_label(g)},
                                            W.game_facts(g, tid), filed_team=tid))
        reaction_extras -= 1

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
    counts = {k: len(v) for k, v in plan.items() if v}
    if not recap:
        notes.append("No completed games last week: no reactions, performances or memes; the room goes to previews.")
    if sum(counts.values()) != TARGET_POSTS:
        notes.append(f"Planned {sum(counts.values())} posts (target {TARGET_POSTS}): the one-post-per-team floor "
                     "or a short slate moved the total.")
    missing = [a for a in assignments if a["kind"] != "REPLY" and a["agent_id"] is None]
    if missing:
        notes.append(f"{len(missing)} posts have no account yet (placeholder shares, or a pool that ran out).")
    return {"season": season, "week": week, "recap_week": W.recap_week, "phase": phase,
            "assignments": assignments, "counts": counts, "notes": notes}


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
            out.append(f"\n== {kind} ({plan['counts'][kind]}) · {WINDOWS.get(kind, '')}")
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
