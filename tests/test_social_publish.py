"""Unit tests for Agent Wire publishing helpers and the reply / team-wakeup planners. No database or API calls.

Run: python3 -m unittest tests.test_social_publish
"""
import ast
import datetime as dt
import os
import unittest

from newsroom.social_desk import PUNDIT_EXCLUDED, plan_replies, plan_social_week, plan_team_wakeup
from newsroom.social_publish import (GAME_LENGTH, compute_trends, engagement, followers_for, kickoff, load_config,
                                     post_time, publishable, subject_heat, week_thursday)
from tests.test_social_desk import dominate, league

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = load_config()


def saved_posts(ctx, plan):
    """Pretend every non-reply assignment was written and saved; likes fall with plan order."""
    out = []
    for i, a in enumerate(x for x in plan["assignments"] if x["kind"] != "REPLY" and x["agent_id"]):
        out.append({"id": 1000 + i, "assignment": a, "text": f"post {i}", "likes": 10_000 - i,
                    "agent_id": a["agent_id"], "handle": a["handle"], "display_name": a["handle"]})
    return out


class CalendarTests(unittest.TestCase):
    def test_week_1_is_the_thursday_after_labor_day(self):
        self.assertEqual(week_thursday(2035, 1).date(), dt.date(2035, 9, 6))   # Labor Day 2035 = Mon Sep 3
        self.assertEqual(week_thursday(2036, 1).date(), dt.date(2036, 9, 4))   # Labor Day 2036 = Mon Sep 1
        self.assertEqual((week_thursday(2035, 18) - week_thursday(2035, 1)).days, 7 * 17)

    def test_kickoff_parses_the_slot(self):
        g = {"week": 1, "game_time": "Mon 8:30 PM"}
        self.assertEqual(kickoff(2035, g), dt.datetime(2035, 9, 10, 20, 30))
        self.assertEqual(kickoff(2035, {"week": 1, "game_time": None}), dt.datetime(2035, 9, 9, 13, 0))

    def test_post_windows(self):
        ctx = league(week=9)
        plan = plan_social_week(ctx)
        games = {g["id"]: g for g in ctx["recap_games"] + ctx["preview_games"]}
        for a in plan["assignments"]:
            if not a["agent_id"]:
                continue
            t = post_time(ctx, a)
            if a["kind"] == "REACTION":
                end = kickoff(2035, games[a["subject"]["id"]]) + GAME_LENGTH
                self.assertTrue(end < t < end + dt.timedelta(hours=2), a["subject"])
            if a["kind"] == "PREVIEW" and a["subject"]["type"] == "game":
                self.assertLess(t, kickoff(2035, games[a["subject"]["id"]]))
                self.assertGreaterEqual(t, week_thursday(2035, 9))
            if a["kind"] in ("BEAT", "STANDINGS", "SHARE"):
                self.assertTrue(week_thursday(2035, 8) < t < week_thursday(2035, 9))
        reply = {"kind": "REPLY", "agent_id": "x", "subject": {"type": "post", "id": 1}}
        parent = dt.datetime(2035, 11, 1, 12, 0)
        self.assertTrue(parent < post_time(ctx, reply, parent) < parent + dt.timedelta(hours=4, minutes=1))

    def test_storylines_post_monday_to_wednesday_and_draw_more_attention(self):
        ctx = league(week=12)
        dominate(ctx, 3)
        stories = [a for a in plan_social_week(ctx)["assignments"] if a["kind"] == "TEAM_STORY"]
        self.assertTrue(stories)
        thu = week_thursday(2035, 12)
        for a in stories:
            t = post_time(ctx, a)
            self.assertTrue(thu - dt.timedelta(days=3, hours=1) < t < thu, t)          # Mon – Wed
        a = stories[0]
        plain = {**a, "buzz_tier": None}
        self.assertGreater(subject_heat(ctx, a, CFG), subject_heat(ctx, plain, CFG))

    def test_times_are_stable(self):
        ctx = league(week=9)
        a = next(x for x in plan_social_week(ctx)["assignments"] if x["kind"] == "REACTION")
        self.assertEqual(post_time(ctx, a), post_time(ctx, a))


class EngagementTests(unittest.TestCase):
    def test_followers_by_role_and_popularity(self):
        pundit = followers_for({"agent_id": "Stephen_A", "archetype": "STEPHEN_A"}, None, CFG)
        self.assertTrue(500_000 <= pundit <= 3_000_000)
        fans = [followers_for({"agent_id": f"fan{i}", "archetype": "DIE_HARD_FAN"}, pop, CFG)
                for i, pop in enumerate((5, 95))]
        self.assertTrue(all(200 <= f <= 25_000 for f in fans))
        self.assertEqual(followers_for({"agent_id": "fanX", "archetype": "DIE_HARD_FAN"}, 50, CFG),
                         followers_for({"agent_id": "fanX", "archetype": "DIE_HARD_FAN"}, 50, CFG))

    def test_big_accounts_and_hot_subjects_get_more_likes(self):
        big = engagement(2_000_000, "national", "REACTION", 1.0, "s", CFG)["likes"]
        small = engagement(3_000, "fan", "REACTION", 1.0, "s", CFG)["likes"]
        self.assertGreater(big, small)
        hot = engagement(5_000, "fan", "REACTION", 1.6, "s", CFG)["likes"]
        cold = engagement(5_000, "fan", "REACTION", 0.7, "s", CFG)["likes"]
        self.assertGreater(hot, cold)
        e = engagement(5_000, "fan", "MEME", 1.0, "s", CFG)
        self.assertEqual(e, engagement(5_000, "fan", "MEME", 1.0, "s", CFG))
        self.assertLessEqual(e["reposts"], e["likes"])

    def test_publishable(self):
        self.assertTrue(publishable({"text": "ok", "problems": []}))
        self.assertTrue(publishable({"text": "ok", "problems": ["3 hashtags (kept 2)"]}))
        self.assertFalse(publishable({"text": "x", "problems": ["301 characters (over 280)"]}))
        self.assertFalse(publishable({"text": "x", "problems": ["mentions ratings or Madden"]}))
        self.assertFalse(publishable({"error": "boom", "text": ""}))


class TrendTests(unittest.TestCase):
    def test_trends_rank_games_and_skip_generic_hashtags(self):
        now = dt.datetime(2035, 11, 1, 12)
        teams = {1: {"name": "Bills", "city": "Buffalo", "division": "AFC East"},
                 2: {"name": "Chiefs", "city": "Kansas City", "division": "AFC West"}}
        games = {7: {"week": 8, "home_team_id": 2, "away_team_id": 1, "game_type": "REGULAR_SEASON"}}
        posts = [{"id": i, "likes": 1000, "reposts": 100, "reply_count": 10, "posted_at": now, "text": f"t{i}",
                  "team_ids": [1, 2], "player_ids": [], "game_id": 7, "hashtags": ["NFL", "Week8", "BillsMafia"]}
                 for i in range(4)]
        rows = compute_trends(posts, {"teams": teams, "players": {}, "games": games}, CFG)
        titles = [r["title"] for r in rows]
        self.assertEqual(titles[0], "Bills–Chiefs")
        self.assertIn("#BillsMafia", titles)
        self.assertNotIn("#NFL", titles)
        self.assertNotIn("#Week8", titles)
        self.assertEqual([r["rank"] for r in rows], list(range(1, len(rows) + 1)))

    def test_single_mentions_do_not_trend(self):
        posts = [{"id": 1, "likes": 99999, "reposts": 0, "reply_count": 0, "posted_at": None, "text": "x",
                  "team_ids": [1], "player_ids": [], "game_id": None, "hashtags": []}]
        teams = {1: {"name": "Bills", "city": "Buffalo", "division": "AFC East"}}
        self.assertEqual(compute_trends(posts, {"teams": teams, "players": {}, "games": {}}, CFG), [])


class ReplyAndWakeupTests(unittest.TestCase):
    def test_replies_go_to_the_most_engaged_posts_from_rivals(self):
        ctx = league(week=9)
        plan = plan_social_week(ctx)
        posts = saved_posts(ctx, plan)
        replies = plan_replies(ctx, plan, posts)
        self.assertEqual(len(replies), len([a for a in plan["assignments"] if a["kind"] == "REPLY"]))
        top = posts[0]
        self.assertEqual(replies[0]["parent"]["id"], top["id"])
        for r in replies:
            parent = next(p for p in posts if p["id"] == r["parent"]["id"])
            self.assertNotEqual(r["agent_id"], parent["agent_id"])
            pa = parent["assignment"]
            if pa["archetype"] == "DIE_HARD_FAN" and r["archetype"] == "DIE_HARD_FAN" and pa["subject"]["type"] == "game":
                g = next(g for g in ctx["recap_games"] if g["id"] == pa["subject"]["id"])
                self.assertIn(r["team_id"], (g["home_team_id"], g["away_team_id"]))

    def test_team_wakeup(self):
        ctx = league(week=9)
        team = 1                                                  # popularity 99 -> 3 pundits
        plan = plan_team_wakeup(ctx, team, local_articles=[({"id": 5, "headline": "h", "excerpt": "e",
                                                              "article_type": "LOCAL_POSTGAME", "slot": "Local Beat 1"}, "beat_1")])
        kinds = [a["kind"] for a in plan["assignments"]]
        self.assertEqual(kinds.count("SHARE"), 1)
        self.assertEqual(kinds.count("BEAT"), 1)
        fans = [a for a in plan["assignments"] if a["archetype"] == "DIE_HARD_FAN"]
        self.assertEqual({a["kind"] for a in fans}, {"REACTION", "PREVIEW"})
        nationals = [a for a in plan["assignments"] if a["archetype"] not in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER", "FORMER_PLAYER")]
        self.assertEqual(len(nationals), 3)
        self.assertTrue(all(a["kind"] in ("PERFORMANCE", "COMPARISON", "TEAM_STORY") for a in nationals))   # not the game
        legend = [a for a in plan["assignments"] if a["archetype"] == "FORMER_PLAYER"]
        self.assertEqual(len(legend), 1)                          # team 1's ex-player
        self.assertEqual(legend[0]["slant_team"], "City1 Team1")
        again = plan_team_wakeup(ctx, team, already_posted={a["agent_id"] for a in plan["assignments"]})
        self.assertFalse([a for a in again["assignments"] if a["archetype"] in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER")])
        quiet = plan_team_wakeup(ctx, 32)                         # popularity 68 -> 1 pundit
        self.assertEqual(len([a for a in quiet["assignments"] if a["archetype"] not in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER", "FORMER_PLAYER")]), 1)

    def test_sibling_replies_take_different_stances_and_fans_get_angles(self):
        ctx = league(week=9)
        plan = plan_social_week(ctx)
        replies = plan_replies(ctx, plan, saved_posts(ctx, plan))
        first = [r for r in replies if r["parent"]["id"] == replies[0]["parent"]["id"]]
        self.assertEqual(len({r["stance"] for r in first}), len(first))
        fans = [a for a in plan["assignments"] if a["archetype"] == "DIE_HARD_FAN" and a["kind"] == "REACTION"]
        self.assertTrue(all(a["angle"] for a in fans))
        self.assertGreater(len({a["angle"] for a in fans}), 4)
        self.assertFalse([a for a in plan["assignments"] if a["kind"] == "BEAT" and a["angle"]])

    def test_planned_posts_land_in_waking_hours(self):
        ctx = league(week=9)
        for a in plan_social_week(ctx)["assignments"]:
            if a["agent_id"] and a["kind"] not in ("REACTION", "REPLY") and not (a["kind"] == "MEME" and a["subject"]["type"] == "game"):
                self.assertFalse(1 <= post_time(ctx, a).hour < 7, a["kind"])

    def test_pundit_pool_matches_the_dispatcher(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            tree = ast.parse(f.read())
        excluded = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(getattr(t, "id", None) == "TWEET_EXCLUDED_ARCHETYPES" for t in n.targets))
        self.assertTrue(set(excluded) <= PUNDIT_EXCLUDED)


if __name__ == "__main__":
    unittest.main()
