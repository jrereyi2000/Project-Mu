"""Unit tests for the Agent Wire read API helpers (server/social_api.py). No database required.

Run: python3 -m unittest tests.test_social_api
"""
import datetime as dt
import unittest

from server.social_api import archetype_label, for_you_order, shape_post


def row(**kw):
    base = {"id": 1, "season": 2035, "week": 18, "post_kind": "REACTION", "text": "t", "posted_at": dt.datetime(2035, 12, 30, 20),
            "likes": 10, "reposts": 1, "reply_count": 2, "views": 300, "hashtags": ["Skol"], "reply_to_id": None,
            "linked_article_id": None, "subject_game_id": 5, "subject_player_id": None, "team_id": 12, "slot": "Agent Wire",
            "agent_id": "fan", "handle": "@fan", "display_name": "Jay", "archetype": "DIE_HARD_FAN", "followers": 900,
            "assigned_team_id": 12, "author_team_abbr": "MIN", "author_team_name": "Vikings", "author_team_color": "#4F2683",
            "team_abbr": "MIN", "real_replies": 3, "article_headline": None, "article_excerpt": None, "article_type": None,
            "parent_handle": None, "parent_display_name": None}
    base.update(kw)
    return base


class ShapeTests(unittest.TestCase):
    def test_post_shape(self):
        p = shape_post(row())
        self.assertEqual(p["replies"], 5)                       # simulated 2 + real 3
        self.assertEqual(p["author"]["label"], "Vikings Fan")
        self.assertEqual(p["posted_at"], "2035-12-30T20:00:00")
        self.assertIsNone(p["article"])
        self.assertIsNone(p["reply_to"])

    def test_share_and_reply_shapes(self):
        share = shape_post(row(post_kind="SHARE", linked_article_id=77, article_headline="H", archetype="BARNWELL"))
        self.assertEqual(share["article"]["id"], 77)
        self.assertEqual(share["author"]["label"], "Analyst")
        reply = shape_post(row(reply_to_id=9, parent_handle="@billbarnwell"))
        self.assertEqual(reply["reply_to"]["handle"], "@billbarnwell")

    def test_team_abbr_display(self):
        self.assertEqual(shape_post(row(author_team_abbr="AZ", team_abbr="WAS"))["author"]["team_abbr"], "ARI")
        self.assertEqual(shape_post(row(team_abbr="WAS"))["team_abbr"], "WSH")

    def test_labels(self):
        self.assertEqual(archetype_label({"archetype": "LOCAL_BEAT_REPORTER", "author_team_name": "Bears"}), "Bears Beat Reporter")
        self.assertEqual(archetype_label({"archetype": "SCHEFTER_TWEET"}), "NFL Insider")
        self.assertEqual(archetype_label({"archetype": "NFL_MEMES"}), "Meme Page")


class ForYouTests(unittest.TestCase):
    def test_big_older_post_beats_small_new_one(self):
        old_big = shape_post(row(id=1, likes=5000, posted_at=dt.datetime(2035, 12, 28, 20)))
        new_small = shape_post(row(id=2, likes=50, posted_at=dt.datetime(2036, 1, 4, 12)))
        order = for_you_order([new_small, old_big], new_small["posted_at"])
        self.assertEqual([p["id"] for p in order], [1, 2])

    def test_recency_breaks_close_calls(self):
        a = shape_post(row(id=1, likes=1000, posted_at=dt.datetime(2035, 12, 28, 20)))
        b = shape_post(row(id=2, likes=900, posted_at=dt.datetime(2036, 1, 4, 12)))
        self.assertEqual([p["id"] for p in for_you_order([a, b], b["posted_at"])], [2, 1])


if __name__ == "__main__":
    unittest.main()
