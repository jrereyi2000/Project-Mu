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
PERFORMANCE_FLOOR = 10   # performances can shrink to this to keep a full week at the target

# Weekly post caps per account role (least-used accounts are picked first).
ROLE_CAPS = {"pundit": 3, "fantasy": 4, "beat": 2, "fan": 2, "bandwagon": 3, "meme": 3, "draft": 3, "former": 3}

# Ex-player pundits keep their national voice but lean toward their old team (agreed with JR on Oct 14).
# FORMER_PLAYER accounts get the same slant from their agent_memories.assigned_team_id.
FORMER_TEAM_SLANT = {"OCHOCINCO": "CIN", "SHANNON": "DEN", "JJ_WATT": "HOU", "RG3": "WAS", "CLARK": "PIT", "MCAFEE": "IND"}
BAD_GAME_SHARE = 0.65   # a flop is at most this share of his earlier average
OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
# Hot-take voices preferred for calling out bad games.
HOT_TAKE_PUNDITS = ("STEPHEN_A", "SKIP_TWEET", "COWHERD", "SHANNON", "MCAFEE")

# Archetypes that never post as a random pundit. Mirrors swarm_dispatcher.TWEET_EXCLUDED_ARCHETYPES (the
# long-form-only writers, SKIP_BAYLESS, the roundtable panel), plus roles the desk assigns on purpose.
PUNDIT_EXCLUDED = {
    "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL", "CHADIHA", "KIMES", "ORLOVSKY",
    "SKIP_BAYLESS", "ROUNDTABLE_PANEL",
    "LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN",
    "FANTASY_GURU", "NFL_MEMES", "DRAFT_SICKO", "FORMER_PLAYER",
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
    "BAD_GAME": "Sun night – Tue",
    "COMPARISON": "Tue – Thu",
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


def norm_pos(pos):
    p = (pos or "").upper()
    return "RB" if p in ("HB", "FB", "RB") else p


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
        return f"{t['games']} games: {stat_line(t)}", f"{norm_pos(t['position'])}{rank} in full-PPR points ({t['fantasy_points']:.1f})"

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
        that, biggest drop first; then any other QB with 3+ interceptions. Players with no line at all are skipped
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
            if key not in seen and norm_pos(l["position"]) == "QB" and (l["interceptions"] or 0) >= 3:
                t = self.season_totals.get(key)
                avg = (t["fantasy_points"] - l["fantasy_points"]) / max(1, t["games"] - 1) if t else 0.0
                out.append((0, l, avg))
        return [(l, avg) for _, l, avg in out]

    def bad_game_facts(self, l, avg):
        facts = self.performance_facts(l)
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
                "former" if arch == "FORMER_PLAYER" else
                "pundit" if arch not in PUNDIT_EXCLUDED else
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
    "one specific player from the facts",
    "the turning point of the game as you remember it (no invented stats)",
    "what this result means for the rest of the season",
    "trash talk aimed at the other team's fans",
    "the coaching and play-calling",
    "the atmosphere in the stadium or at your watch party",
    "one number from the facts that tells the story",
    "looking ahead to next week's game",
)
FAN_ARCHETYPES = ("DIE_HARD_FAN", "BANDWAGON_FAN")
# Replies to the same post take different stances, in this order.
REPLY_STANCES = ("push back on it", "agree and add a point of your own", "make a joke about it")


def _assignment(kind, account, subject, facts, filed_team=None):
    angle = None
    if account and account.get("archetype") == "DIE_HARD_FAN" and kind in ("REACTION", "PREVIEW"):
        seed = sum(ord(c) for c in f"{account['agent_id']}|{kind}|{subject.get('id')}")
        angle = FAN_ANGLES[seed % len(FAN_ANGLES)]
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
    acc = Accounts(ctx["accounts"], seed=f"{season}-{week}")
    notes = []
    plan = collections.OrderedDict((k, []) for k in
                                   ("SHARE", "REACTION", "PERFORMANCE", "BAD_GAME", "COMPARISON", "PLAYER", "BEAT",
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
        account = None
        if art["persona"]:
            account = acc.by_archetype_uncapped(SHARE_ACCOUNT_ARCHETYPE.get(art["persona"], art["persona"]))
        plan["SHARE"].append(_assignment("SHARE", account,
                                         {"type": "article", "id": art["id"], "label": art["headline"]},
                                         {k: art[k] for k in ("headline", "excerpt", "article_type", "slot")},
                                         filed_team=art["team_id"]))

    # 2. Player performances from the recap week (offense only until the capture loads defense/kicking).
    # League-wide voices only: pundits, ex-players (leaning to their old team, then their position), bandwagon
    # fans and the fantasy accounts. Die-hards talk about their own team in reactions and comparisons.
    perf_n = BUCKET_SIZES["PERFORMANCE"] if recap else 0
    top_lines = sorted(W.recap_lines, key=lambda l: (l["fantasy_points"], l["popularity"]), reverse=True)
    seen_players = set()
    pattern = ["fantasy", "pundit", "former", "bandwagon", "former", "pundit", "bandwagon"]

    def add_performances(limit):
        for l in top_lines:
            if len(plan["PERFORMANCE"]) >= limit:
                break
            key = l["player_id"] or (l["short_name"], l["team_id"])
            if key in seen_players:
                continue
            seen_players.add(key)
            who = pattern[len(plan["PERFORMANCE"]) % len(pattern)]
            account = (acc.fantasy() if who == "fantasy" else
                       acc.former([l["team_id"]], l["position"]) if who == "former" else
                       acc.bandwagon() if who == "bandwagon" else None)
            if account is None:
                account = acc.pundit(prefer=PUNDIT_AFFINITY.get(l["position"], ()))
            if account is None:
                continue
            plan["PERFORMANCE"].append(_assignment("PERFORMANCE", account,
                                                   {"type": "player", "id": l["player_id"], "team_id": l["team_id"], "label": f"{l['name']} ({l['position']}, {W.teams[l['team_id']]['abbr']})"},
                                                   W.performance_facts(l)))

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

    # 8. Replies: parents are chosen after the rest is written and scored. Their number flexes within
    # REPLY_RANGE to keep the week near TARGET_POSTS.
    so_far = sum(len(v) for v in plan.values())
    # A full slate with a big article week can push past the target even at the reply floor: trim the newest
    # (lowest-ranked) performances first, never below PERFORMANCE_FLOOR.
    over = so_far + REPLY_RANGE[0] - TARGET_POSTS
    while over > 0 and len(plan["PERFORMANCE"]) > PERFORMANCE_FLOOR:
        plan["PERFORMANCE"].pop()
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
    acc = Accounts(ctx["accounts"], seed=f"{ctx['season']}-{ctx['week']}-replies").replay(plan["assignments"])
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
    this week's (alternating when both exist); 3 pundits if the team's popularity is above 70, 1 above 55.
    local_articles: [(article dict, agent_id)] written in this wakeup; each is shared by its reporter.
    already_posted: agent ids that already posted in an earlier wakeup for this team and week.
    """
    W = Week(ctx)
    acc = Accounts(ctx["accounts"], seed=f"{W.season}-{W.week}-team{team_id}").block(already_posted)
    last, nxt = W.team_games(team_id)
    out = []
    for art, agent_id in local_articles:
        account = acc.by_id.get(agent_id)
        if account:
            out.append(_assignment("SHARE", account, {"type": "article", "id": art["id"], "label": art["headline"]},
                                   {k: art.get(k) for k in ("headline", "excerpt", "article_type", "slot")}, filed_team=team_id))
    for b in sorted((x for x in acc.all() if x["archetype"] == "LOCAL_BEAT_REPORTER" and x["team_id"] == team_id
                     and acc.uses[x["agent_id"]] < 1000), key=lambda x: x["agent_id"]):
        acc.uses[b["agent_id"]] += 1
        out.append(_assignment("BEAT", b, {"type": "team", "id": team_id, "label": W.team_label(team_id)},
                               W.beat_facts(team_id), filed_team=team_id))
    fans = sorted((x for x in acc.all() if x["archetype"] == "DIE_HARD_FAN" and x["team_id"] == team_id
                   and acc.uses[x["agent_id"]] < 1000), key=lambda x: acc.order[x["agent_id"]])
    for i, f in enumerate(fans):
        acc.uses[f["agent_id"]] += 1
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
    for i in range(3 if pop > 70 else 1 if pop > 55 else 0):
        p = acc.pundit()
        if not p:
            break
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
