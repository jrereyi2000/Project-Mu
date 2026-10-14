"""Unit tests for the Agent Wire social desk planner (newsroom/social_desk.py). No database required.

Run: python3 -m unittest tests.test_social_desk
"""
import collections
import json
import re
import unittest

from newsroom.social_desk import ROLE_CAPS, TARGET_POSTS, Accounts, plan_social_week

DIVS = [f"{c} {d}" for c in ("AFC", "NFC") for d in ("East", "North", "South", "West")]


def league(season=2035, week=9, played_weeks=None, playoff=None, articles=None, projections=True):
    """A synthetic 32-team league. Regular-season games every week before `week` (or `played_weeks`);
    `playoff` = {week: [(away, home, status)]} adds postseason games."""
    teams = {i: {"id": i, "name": f"Team{i}", "city": f"City{i}", "abbr": f"T{i:02d}", "conference": DIVS[(i - 1) // 4][:3],
                 "division": DIVS[(i - 1) // 4], "popularity": float(100 - i)} for i in range(1, 33)}
    accounts = []
    for t in teams:
        for n in range(2):
            accounts.append({"agent_id": f"fan_{t}_{n}", "archetype": "DIE_HARD_FAN", "team_id": t, "handle": f"@fan{t}_{n}", "display_name": "x"})
        accounts.append({"agent_id": f"beat_{t}", "archetype": "LOCAL_BEAT_REPORTER", "team_id": t, "handle": f"@beat{t}", "display_name": "x"})
    for n in range(6):
        accounts.append({"agent_id": f"bw_{n}", "archetype": "BANDWAGON_FAN", "team_id": None, "handle": f"@bw{n}", "display_name": "x"})
    for arch in ("STEPHEN_A", "MCAFEE", "SHANNON", "OCHOCINCO", "JJ_WATT", "RG3", "COWHERD", "WINGO", "EISEN", "CLARK",
                 "SKIP_TWEET", "BERRY", "YATES", "FANTASY_GURU", "NFL_MEMES", "DRAFT_SICKO", "SKIP_BAYLESS", "BARNWELL",
                 "ROUNDTABLE_PANEL"):
        accounts.append({"agent_id": arch.lower(), "archetype": arch, "team_id": None, "handle": f"@{arch.lower()}", "display_name": "x"})

    games, gid = [], 0
    weeks = played_weeks if played_weeks is not None else range(1, week)
    for w in list(weeks) + [week]:
        for i in range(1, 17):
            gid += 1
            done = w < week
            games.append({"id": gid, "week": w, "home_team_id": i, "away_team_id": i + 16,
                          "home_score": (20 + (i * w) % 17) if done else 0, "away_score": (13 + (i * 3 + w) % 21) if done else 0,
                          "status": "COMPLETED" if done else "SCHEDULED", "game_time": "Sun 1:00 PM",
                          "game_type": "REGULAR_SEASON", "excitement_index": 40.0 + i * 3, "projected_excitement": 40.0 + i * 2})
    for w, matchups in (playoff or {}).items():
        games = [g for g in games if g["week"] != w]
        for away, home, status in matchups:
            gid += 1
            games.append({"id": gid, "week": w, "home_team_id": home, "away_team_id": away,
                          "home_score": 27 if status == "COMPLETED" else 0, "away_score": 20 if status == "COMPLETED" else 0,
                          "status": status, "game_time": "Sat 4:30 PM", "game_type": "PLAYOFF",
                          "excitement_index": 80.0, "projected_excitement": 70.0})
    lines = []
    for g in games:
        if g["status"] != "COMPLETED" or g["week"] >= week:
            continue
        for team in (g["home_team_id"], g["away_team_id"]):
            for pos, k in (("QB", 0), ("HB", 1), ("WR", 2)):
                lines.append({"week": g["week"], "game_id": g["id"], "team_id": team, "position": pos,
                              "short_name": f"P.{team}{pos}", "name": f"Player {team}{pos}", "player_id": team * 10 + k,
                              "popularity": float(team), "completions": 20 if pos == "QB" else 0, "pass_attempts": 30 if pos == "QB" else 0,
                              "pass_yards": 250 if pos == "QB" else 0, "pass_tds": 2 if pos == "QB" else 0, "interceptions": 0,
                              "rush_attempts": 15 if pos == "HB" else 0, "rush_yards": 60 + team if pos == "HB" else 0, "rush_tds": 1 if pos == "HB" else 0,
                              "receptions": 6 if pos == "WR" else 0, "rec_yards": 70 + g["id"] % 50 if pos == "WR" else 0, "rec_tds": 1 if pos == "WR" else 0,
                              "fantasy_points": float(10 + team + g["id"] % 7)})
    ctx = {"season": season, "week": week, "recap_week": week - 1, "teams": teams, "accounts": accounts,
           "games": [g for g in games if g["week"] < week and g["status"] == "COMPLETED"],
           "recap_games": [g for g in games if g["week"] == week - 1 and g["status"] == "COMPLETED"],
           "preview_games": [g for g in games if g["week"] == week],
           "player_lines": lines, "articles": articles or [],
           "projections": [{"name": f"Player {t}{p}", "team_id": t, "position": p, "projected_points": 30.0 - t / 2,
                            "matchup_stars": 3, "pos_rank": None} for t in range(1, 33) for p in ("QB", "HB", "WR", "TE")] if projections else []}
    return ctx


def by_kind(plan):
    out = collections.defaultdict(list)
    for a in plan["assignments"]:
        out[a["kind"]].append(a)
    return out


class SocialDeskTests(unittest.TestCase):
    def test_regular_week_is_100_posts_with_one_reaction_per_team(self):
        plan = plan_social_week(league(week=9))
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        reacting_teams = {a["team_id"] for a in by_kind(plan)["REACTION"] if a["archetype"] == "DIE_HARD_FAN"}
        self.assertEqual(reacting_teams, set(range(1, 33)))

    def test_extra_reactions_go_to_the_most_exciting_games(self):
        plan = plan_social_week(league(week=9))
        per_game = collections.Counter(a["subject"]["id"] for a in by_kind(plan)["REACTION"])
        top = max(league(week=9)["recap_games"], key=lambda g: g["excitement_index"])["id"]
        self.assertEqual(per_game.most_common(1)[0][0], top)

    def test_shares_reduce_reactions_and_keep_the_total(self):
        arts = [{"id": i, "persona": p, "team_id": None, "headline": f"A{i}", "excerpt": "e", "slot": f"Slot {i}",
                 "article_type": "X"} for i, p in enumerate(["BARNWELL", "SKIP_BAYLESS", "ROUNDTABLE_PANEL", "YATES"], 1)]
        plan = plan_social_week(league(week=9, articles=arts))
        shares = by_kind(plan)["SHARE"]
        self.assertEqual(len(shares), 3)                       # the roundtable panel never posts
        skip = next(s for s in shares if s["subject"]["id"] == 2)
        self.assertEqual(skip["archetype"], "SKIP_TWEET")       # Skip's column is shared from @RealSkipBayless
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_previews_never_show_this_weeks_scores(self):
        ctx = league(week=9)
        for g in ctx["preview_games"]:                          # replanning an old week: games already played
            g.update(status="COMPLETED", home_score=99, away_score=98)
        plan = plan_social_week(ctx)
        text = json.dumps(plan)
        self.assertNotIn("99", text)
        self.assertFalse([a for a in by_kind(plan)["PREVIEW"] if re.search(r" \d+ @ \S+ \d+$", a["subject"]["label"])])

    def test_weekly_caps_hold(self):
        plan = plan_social_week(league(week=9))
        acc = Accounts(league()["accounts"], seed="x")
        uses = collections.Counter(a["agent_id"] for a in plan["assignments"] if a["agent_id"] and a["kind"] != "SHARE")
        for agent_id, n in uses.items():
            self.assertLessEqual(n, ROLE_CAPS[acc.role[agent_id] or "pundit"], agent_id)

    def test_fantasy_start_sit_only_in_fantasy_season(self):
        self.assertEqual(len([a for a in by_kind(plan_social_week(league(week=9)))["PREVIEW"] if a["subject"]["type"] == "fantasy"]), 2)
        self.assertFalse([a for a in by_kind(plan_social_week(league(week=18)))["PREVIEW"] if a["subject"]["type"] == "fantasy"])

    def test_plan_is_deterministic(self):
        self.assertEqual(plan_social_week(league(week=9)), plan_social_week(league(week=9)))

    def test_preseason_fills_with_previews(self):
        plan = plan_social_week(league(week=1, played_weeks=[]))
        kinds = by_kind(plan)
        self.assertFalse(kinds["REACTION"])
        self.assertFalse(kinds["PERFORMANCE"])
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        self.assertEqual({a["team_id"] for a in kinds["PREVIEW"] if a["archetype"] == "DIE_HARD_FAN"}, set(range(1, 33)))

    def test_postseason_keeps_100_and_eliminated_fans_post_about_players(self):
        # Week 20 (Divisional) after a Wild Card round: 4 teams scheduled this week, 6 played last week.
        wc = [(17, 1, "COMPLETED"), (18, 2, "COMPLETED"), (19, 3, "COMPLETED"), (20, 4, "COMPLETED"), (21, 5, "COMPLETED"), (22, 6, "COMPLETED")]
        div = [(1, 7, "SCHEDULED"), (2, 8, "SCHEDULED")]
        ctx = league(week=20, played_weeks=range(1, 19), playoff={19: wc, 20: div})
        plan = plan_social_week(ctx)
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        player_posts = by_kind(plan)["PLAYER"]
        self.assertTrue(player_posts)
        alive = {1, 2, 7, 8}
        self.assertTrue(all(a["team_id"] not in alive for a in player_posts))
        self.assertFalse([a for a in by_kind(plan)["PREVIEW"] if a["subject"]["type"] == "fantasy"])

    def test_super_bowl_bye_week_keeps_conference_champions_alive(self):
        conf = [(1, 2, "COMPLETED"), (17, 18, "COMPLETED")]       # home teams 2 and 18 win
        ctx = league(week=22, played_weeks=range(1, 19), playoff={21: conf, 22: []})
        plan = plan_social_week(ctx)
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)
        self.assertFalse([a for a in by_kind(plan)["PLAYER"] if a["team_id"] in (2, 18)])


if __name__ == "__main__":
    unittest.main()
