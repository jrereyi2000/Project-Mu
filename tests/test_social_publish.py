"""Unit tests for Agent Wire publishing helpers and the reply / team-wakeup planners. No database or API calls.

Run: python3 -m unittest tests.test_social_publish
"""
import ast
import datetime as dt
import json
import os
import unittest
from unittest import mock
from unittest.mock import MagicMock

from newsroom import social_publish as social
from newsroom import social_writer as SW
from newsroom.social_desk import PUNDIT_EXCLUDED, plan_replies, plan_social_week, plan_team_wakeup
from newsroom.social_publish import (GAME_LENGTH, compute_trends, engagement, find_duplicate_content_tweet,
                                     followers_for, kickoff, load_config, normalize_tweet_text, post_time,
                                     publishable, save_post, subject_heat, week_thursday, _publish)
from newsroom.social_writer import parse_post_json
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

    def test_sibling_replies_quote_and_angles_need_a_strong_relationship(self):
        """U3 replaced reply stances with the quote form and made the angle relationship-driven only."""
        ctx = league(week=9)
        plan = plan_social_week(ctx)
        replies = plan_replies(ctx, plan, saved_posts(ctx, plan))
        self.assertTrue(all("quote" in r and "stance" not in r for r in replies))
        self.assertFalse([a for a in plan["assignments"] if a.get("relationship_angle")])
        from newsroom.social_desk import _relationship_line
        ctx["strong_relationships"] = {("fan_1_0", "team", "1"): _relationship_line("team", "Team1", 0.8)}
        strong = plan_social_week(ctx)
        angled = [a for a in strong["assignments"] if a.get("relationship_angle")]
        self.assertTrue(angled)
        self.assertTrue(all(a["agent_id"] == "fan_1_0" and a["angle"] for a in angled))
        self.assertFalse([a for a in strong["assignments"] if a["kind"] == "BEAT" and a.get("relationship_angle")])

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


class DeduplicationTests(unittest.TestCase):
    def test_normalize_tweet_text(self):
        self.assertEqual(normalize_tweet_text("  Hello  World!  "), "hello world")
        self.assertEqual(normalize_tweet_text("TOUCHDOWN PANTHERS!!!"), "touchdown panthers")
        self.assertEqual(normalize_tweet_text("Line 1\nLine 2..."), "line 1 line 2")
        self.assertEqual(normalize_tweet_text(""), "")

    def test_find_duplicate_content_tweet(self):
        cur = MagicMock()
        cur.fetchall.return_value = [
            (101, "Great game by Carolina today!"),
            (102, "Another take.")
        ]
        # Exact match
        self.assertEqual(find_duplicate_content_tweet(cur, 2035, 19, "agent_1", "Great game by Carolina today!"), 101)
        # Normalized match (different case / punctuation / spacing)
        self.assertEqual(find_duplicate_content_tweet(cur, 2035, 19, "agent_1", "  great GAME by Carolina today!!!  "), 101)
        # Non-matching
        self.assertIsNone(find_duplicate_content_tweet(cur, 2035, 19, "agent_1", "Totally different content."))

    def test_save_post_skips_duplicate_content(self):
        cur = MagicMock()
        cur.fetchall.return_value = [(101, "Existing text")]
        ctx = {"season": 2035, "week": 19, "recap_games": [], "preview_games": []}
        a = {"agent_id": "test_agent", "kind": "BEAT", "subject": {"type": "team", "id": 1, "label": "Panthers"}, "archetype": "LOCAL_BEAT_REPORTER"}
        post = {"text": "Existing text."}
        eng = {"likes": 10, "reposts": 2, "reply_count": 0, "views": 100}

        post_id = save_post(cur, ctx, a, post, dt.datetime.now(), eng, "Agent Wire")
        self.assertIsNone(post_id)
        # Ensure no INSERT was executed
        for call in cur.execute.call_args_list:
            self.assertNotIn("INSERT INTO news_feed", call[0][0])

    def test_publish_skips_already_published_assignments(self):
        cur = MagicMock()
        cur.fetchone.return_value = (999, "Already published text", 500, dt.datetime(2035, 9, 10), "agent_1")
        ctx = {"season": 2035, "week": 19, "recap_games": [], "preview_games": []}
        a = {"agent_id": "agent_1", "kind": "BEAT", "subject": {"type": "team", "id": 1, "label": "Panthers"}, "archetype": "LOCAL_BEAT_REPORTER", "handle": "@agent_1"}
        writer = MagicMock()
        writer.accounts = {"agent_1": {"handle": "@agent_1", "display_name": "Agent 1"}}

        saved, failed = _publish(conn=MagicMock(), cur=cur, ctx=ctx, assignments=[a], writer=writer,
                                 followers={"agent_1": 1000}, cfg=CFG, slot="Agent Wire", tagger=None, log=lambda *args: None)
        writer.write.assert_not_called()


class RelationshipAndPickStorageTests(unittest.TestCase):
    """A post's structured JSON grows takes, stores picks and season calls, and reaches the prompt (U4)."""

    @staticmethod
    def pick_assignment():
        """The planner's real card shape (social_desk._pick_card): labels as strings, ids as away_id/home_id."""
        return {
            "kind": "PICKS", "week": 9,
            "subject": {"type": "slate", "id": None, "label": "Week 9 picks"},
            "subjects": [{"type": "team", "id": 1}, {"type": "team", "id": 2},
                         {"type": "player", "id": 9, "name": "Jalen Hurts"}],
            "focus": ["Team1 @ Team2"],
            "facts": {"week": 9, "games": ["Team1 @ Team2"]},
            "pick_games": [{"game_id": 77, "game": "T1 @ T2", "away": "Team1", "home": "Team2",
                            "away_id": 1, "home_id": 2, "kickoff": "Sun 1:00 PM"}],
        }

    def fake_json(self):
        return json.dumps({
            "text": "T2 all day. 24-17.",
            "about": [{"type": "team", "name": "T2", "lean": 1},
                      {"type": "player", "name": "Jalen Hurts", "lean": 1}],
            "picks": [{"game": "T1 @ T2", "winner": "T2", "score": "24-17", "confidence": "lock"}],
            "calls": [{"call_type": "MVP", "subject": "Jalen Hurts", "detail": "wire to wire"}],
        })

    def test_picks_calls_and_about_are_stored_from_the_writers_json(self):
        parsed = parse_post_json(self.fake_json(), social.normalized_assignment(self.pick_assignment()))
        self.assertTrue(parsed["structured"])
        self.assertEqual(len(parsed["picks"]), 1, parsed)
        self.assertEqual(len(parsed["calls"]), 1, parsed)
        self.assertEqual(len(parsed["about"]), 2, parsed)
        cur = MagicMock()
        with mock.patch("newsroom.relationships.person_key", return_value="pk"), \
                mock.patch("newsroom.relationships.apply_take", return_value=2) as take:
            social._record_post_relationships(cur, {"season": 2035, "week": 9},
                                              {"agent_id": "writer", "kind": "PICKS"}, parsed, 555)
        take.assert_called_once()
        self.assertEqual(take.call_args.args, (cur, "pk", 2035, 9, parsed["about"]))
        params = {call.args[0]: call.args[1] for call in cur.execute.call_args_list}
        pred = next(p for sql, p in params.items() if "INSERT INTO agent_predictions" in sql)
        self.assertEqual(pred, ("writer", 2035, 9, 555, 77, "lock", 1, 17, 2, 24, "SCORE", 2))
        season = next(p for sql, p in params.items() if "INSERT INTO agent_season_calls" in sql)
        self.assertEqual(season, ("writer", "pk", 2035, 9, 555, "MVP", "player", 9, "wire to wire"))

    def test_normalized_assignment_leaves_the_planner_card_untouched(self):
        a = self.pick_assignment()
        norm = social.normalized_assignment(a)
        self.assertEqual(a["pick_games"][0]["away"], "Team1")
        card = norm["pick_games"][0]
        self.assertEqual((card["away"]["id"], card["away"]["abbr"]), (1, "T1"))
        self.assertEqual((card["home"]["id"], card["home"]["abbr"]), (2, "T2"))
        self.assertEqual((card["away_team_id"], card["home_team_id"]), (1, 2))
        self.assertEqual(card["game"], "T1 @ T2")               # the prompt sees the same card

    def test_save_post_records_the_relationships_after_it_posts(self):
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.side_effect = [None, (555,)]              # no existing assignment, then the insert's id
        ctx = {"season": 2035, "week": 9, "recap_games": [], "preview_games": []}
        a = {"agent_id": "writer", "kind": "BEAT", "archetype": "LOCAL_BEAT_REPORTER", "handle": "@writer",
             "subject": {"type": "team", "id": 1, "label": "Team1", "team_id": 1}, "team_id": 1}
        post = {"text": "ok", "about": [], "picks": [], "season_calls": []}
        eng = {"likes": 1, "reposts": 0, "reply_count": 0, "views": 1}
        with mock.patch("newsroom.social_publish._record_post_relationships") as rec:
            pid = save_post(cur, ctx, a, post, dt.datetime(2035, 9, 1), eng, "Agent Wire")
        self.assertEqual(pid, 555)
        rec.assert_called_once_with(cur, ctx, a, post, 555)

    def test_recent_post_shapes_come_back_most_recent_last(self):
        cur = MagicMock()
        cur.fetchall.return_value = [("question", "what a catch?"), (None, "ok")]
        self.assertEqual(social.recent_post_shapes(cur, "fan"), ["one_liner", "question"])

    def test_recent_post_shapes_prefer_the_stored_column(self):
        cur = MagicMock()
        cur.fetchall.return_value = [("sarcasm", "great, another fourth-quarter collapse"), (None, "go team")]
        self.assertEqual(social.recent_post_shapes(cur, "fan"), ["one_liner", "sarcasm"])

    def test_recent_post_shapes_fall_back_when_the_column_is_missing(self):
        class Cur:
            def __init__(self):
                self.calls = 0

            def execute(self, sql, *a):
                self.calls += 1
                if "post_shape, content" in sql:
                    raise RuntimeError('column "post_shape" does not exist')

            def fetchall(self):
                return [("what a catch?",), ("ok",)]
        self.assertEqual(social.recent_post_shapes(Cur(), "fan"), ["one_liner", "question"])

    def test_save_post_stores_the_sampled_shape(self):
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.side_effect = [None, (555,)]
        ctx = {"season": 2035, "week": 9, "recap_games": [], "preview_games": []}
        a = {"agent_id": "writer", "kind": "BEAT", "archetype": "LOCAL_BEAT_REPORTER", "handle": "@writer",
             "subject": {"type": "team", "id": 1, "label": "Team1", "team_id": 1}, "team_id": 1,
             "post_shape": "sarcasm"}
        post = {"text": "ok", "about": [], "picks": [], "season_calls": []}
        eng = {"likes": 1, "reposts": 0, "reply_count": 0, "views": 1}
        with mock.patch("newsroom.social_publish._record_post_relationships"):
            self.assertEqual(save_post(cur, ctx, a, post, dt.datetime(2035, 9, 1), eng, "Agent Wire"), 555)
        insert = next(c for c in cur.execute.call_args_list if "INSERT INTO news_feed" in c.args[0])
        self.assertIn("post_shape", insert.args[0])
        self.assertEqual(insert.args[1][-1], "sarcasm")

    def test_write_gives_the_parser_the_normalized_assignment(self):
        class Cur:
            def execute(self, *a, **k):
                pass
            def fetchone(self):
                return None
            def fetchall(self):
                return []
        ctx = {"season": 2035, "week": 9, "teams": {1: {"name": "Team1"}, 2: {"name": "Team2"}}}
        acct = {"agent_id": "fan", "archetype": "PUNDIT_PANEL", "team_id": None, "handle": "@fan",
                "display_name": "Fan", "persistent_memory": ""}
        a = dict(self.pick_assignment(), agent_id="fan", handle="@fan", team_id=None, archetype="PUNDIT_PANEL")
        writer = social._Writer(Cur(), ctx, {"fan": acct}, CFG, None, "gemini-3.8-flash", 1, lambda *args: None)
        with mock.patch.object(social, "write_post", return_value={"text": "ok", "problems": []}) as wp:
            writer.write([a])
        passed = wp.call_args.kwargs["assignment"]
        self.assertEqual((passed["pick_games"][0]["away_team_id"], passed["pick_games"][0]["home_team_id"]), (1, 2))

    def test_prompt_passes_the_relationship_block_and_shape(self):
        class Cur:
            def execute(self, *a, **k):
                pass
            def fetchone(self):
                return None
            def fetchall(self):
                return []
        ctx = {"season": 2035, "week": 9, "teams": {1: {"name": "Team1"}, 2: {"name": "Team2"}}}
        acct = {"agent_id": "fan", "archetype": "DIE_HARD_FAN", "team_id": 1, "handle": "@fan",
                "display_name": "Fan", "persistent_memory": ""}
        a = {"agent_id": "fan", "kind": "REACTION", "subject": {"type": "game", "id": 3},
             "subjects": [{"type": "team", "id": 2}], "facts": {"week": 9}, "team_id": 1, "handle": "@fan"}
        writer = social._Writer(Cur(), ctx, {"fan": acct}, CFG, None, "gemini-3.8-flash", 1, lambda *args: None)
        captured = {}

        def fake_prompt(*args, **kwargs):
            captured["args"], captured["kwargs"] = args, kwargs
            return "system", "prompt"

        with mock.patch.object(social, "build_post_prompt", fake_prompt), \
                mock.patch("newsroom.relationships.relationship_block",
                           return_value="You can't stand the Team2."):
            writer.prompt(a)
        self.assertEqual(captured["kwargs"]["relationship_block"], "You can't stand the Team2.")
        self.assertIn(captured["kwargs"]["shape"], SW.POST_SHAPES)

    def test_generated_post_prompt_has_the_bias_block_once(self):
        class Cur:
            def execute(self, *a, **k):
                pass
            def fetchone(self):
                return None
            def fetchall(self):
                return []
        ctx = {"season": 2035, "week": 9, "teams": {1: {"name": "Team1"}, 2: {"name": "Team2"}}}
        acct = {"agent_id": "fan", "archetype": "DIE_HARD_FAN", "team_id": 1, "handle": "@fan",
                "display_name": "Fan", "persistent_memory": ""}
        a = {"agent_id": "fan", "kind": "REACTION", "subject": {"type": "game", "id": 3},
             "subjects": [{"type": "team", "id": 2}], "facts": {"week": 9}, "team_id": 1, "handle": "@fan"}
        writer = social._Writer(Cur(), ctx, {"fan": acct}, CFG, None, "gemini-3.8-flash", 1, lambda *args: None)
        with mock.patch("newsroom.relationships.relationship_block",
                        return_value="You can't stand the Team2.") as rel:
            system, _ = writer.prompt(a)
        self.assertEqual(rel.call_count, 1)                     # posts get it only through build_post_prompt
        self.assertEqual(system.count("YOUR BIASES"), 1)
        self.assertIn("You can't stand the Team2.", system)


class FantasySuppressionTests(unittest.TestCase):
    def test_publishable_blocks_fantasy_when_inactive(self):
        # When fantasy_active is False (postseason/offseason), fantasy text is blocked
        post = {"text": "He scored 25.4 fantasy points today!"}
        self.assertFalse(publishable(post, fantasy_active=False))
        post_ppr = {"text": "Great game in full-PPR scoring."}
        self.assertFalse(publishable(post_ppr, fantasy_active=False))
        # Normal football text is allowed
        clean_post = {"text": "Great game: 120 rushing yards and 2 TDs!"}
        self.assertTrue(publishable(clean_post, fantasy_active=False))
        # When fantasy_active is True (regular season), fantasy text is allowed
        self.assertTrue(publishable(post, fantasy_active=True))

    def test_save_post_blocks_fantasy_archetypes_and_content_in_postseason(self):
        cur = MagicMock()
        cur.fetchall.return_value = []
        ctx = {"season": 2035, "week": 19, "recap_games": [], "preview_games": []}
        eng = {"likes": 10, "reposts": 2, "reply_count": 0, "views": 100}

        # Fantasy archetype blocked in week 19
        a_guru = {"agent_id": "guru", "kind": "PERFORMANCE", "subject": {"type": "player", "id": 1, "label": "Star"}, "archetype": "FANTASY_GURU"}
        self.assertIsNone(save_post(cur, ctx, a_guru, {"text": "Nice run."}, dt.datetime.now(), eng, "Agent Wire"))

        # Fantasy content blocked in week 19
        a_pundit = {"agent_id": "pundit", "kind": "PERFORMANCE", "subject": {"type": "player", "id": 1, "label": "Star"}, "archetype": "STEPHEN_A"}
        self.assertIsNone(save_post(cur, ctx, a_pundit, {"text": "He won your fantasy league today!"}, dt.datetime.now(), eng, "Agent Wire"))


if __name__ == "__main__":
    unittest.main()
