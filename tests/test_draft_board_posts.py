"""Draft board coverage (docs/team_management/05_NEWSROOM_INTEGRATION_PLAN.md Phase 9, JR-approved text 2026-10-20):
@MockDraftMarty DRAFT_BOARD posts and the three draft directive additions. Pure: no database, no model calls."""
import json
import unittest
from unittest import mock

from newsroom.social_desk import (DRAFT_BOARD_POSTS, TARGET_POSTS, WINDOWS, draft_board_subjects, load_draft_movers,
                                  plan_social_week)
from tests.test_social_desk import by_kind, league


def mover(name, change, rank=10):
    return {"name": name, "pos": "EDGE", "college": "Iowa", "class_year": "Jr", "consensus_rank": rank, "grade": 88.4,
            "round_label": "Round 1", "rank_change": change, "grade_change": 1.2, "why": [f"{name} wrecked Saturday."]}


MOVERS = {"draft_class": 2037, "risers": [mover("Riser One", 9), mover("Riser Two", 4)],
          "fallers": [mover("Faller One", -14), mover("Faller Two", -3)]}


class DraftBoardSubjectsTests(unittest.TestCase):
    def test_one_post_takes_the_bigger_move(self):
        got = draft_board_subjects(MOVERS, 1)
        self.assertEqual([(s["direction"], s["mover"]["name"]) for s in got], [("faller", "Faller One")])

    def test_two_posts_take_top_riser_and_top_faller(self):
        got = draft_board_subjects(MOVERS, 2)
        self.assertEqual(sorted((s["direction"], s["mover"]["name"]) for s in got),
                         [("faller", "Faller One"), ("riser", "Riser One")])

    def test_no_board_no_subjects(self):
        self.assertEqual(draft_board_subjects({}, 2), [])
        self.assertEqual(draft_board_subjects({"draft_class": 2037, "risers": [], "fallers": []}, 1), [])


class DraftBoardPlanTests(unittest.TestCase):
    def test_regular_week_one_post_from_mockdraftmarty(self):
        ctx = league(week=9)
        ctx["draft_movers"] = MOVERS
        plan = plan_social_week(ctx)
        posts = by_kind(plan)["DRAFT_BOARD"]
        self.assertEqual(len(posts), DRAFT_BOARD_POSTS["regular"])
        self.assertEqual(posts[0]["archetype"], "DRAFT_SICKO")
        facts = posts[0]["facts"]
        self.assertEqual((facts["draft_class"], facts["direction"], facts["name"]), (2037, "faller", "Faller One"))
        self.assertNotIn("ovr", json.dumps(facts).lower())
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_no_board_no_posts(self):
        plan = plan_social_week(league(week=9))
        self.assertEqual(by_kind(plan)["DRAFT_BOARD"], [])

    def test_window(self):
        self.assertIn("DRAFT_BOARD", WINDOWS)


class LoadDraftMoversTests(unittest.TestCase):
    def test_missing_tables_or_errors_give_nothing(self):
        cur = mock.MagicMock()
        with mock.patch("engines.scouting_read.risers_fallers", side_effect=RuntimeError("no table")):
            self.assertEqual(load_draft_movers(cur, 2036, 5), {})
        with mock.patch("engines.scouting_read.risers_fallers", return_value=None):
            self.assertEqual(load_draft_movers(cur, 2036, 5), {})

    def test_board_rows_become_public_lines(self):
        row = {"full_name": "JOHN DOE", "position": "CB", "college": "Utah", "class_year": "Sr", "rank": 12,
               "grade": 86.0, "round_label": "Round 1", "delta_rank": 7, "delta_grade": 2.0,
               "events": [{"blurb": "Two picks against Oregon.", "headline": None}]}
        rf = {"draft_season": 2037, "step": 3, "week": 5, "stage": None, "risers": [row], "fallers": []}
        with mock.patch("engines.scouting_read.risers_fallers", return_value=rf):
            got = load_draft_movers(mock.MagicMock(), 2036, 5)
        self.assertEqual(got["draft_class"], 2037)
        self.assertEqual((got["risers"][0]["name"], got["risers"][0]["rank_change"]), ("John Doe", 7))
        self.assertEqual(got["risers"][0]["why"], ["Two picks against Oregon."])


class DraftBoardTextTests(unittest.TestCase):
    def test_post_prompt(self):
        from newsroom.social_writer import KIND_INSTRUCTIONS, kind_key
        text = KIND_INSTRUCTIONS["DRAFT_BOARD"]
        self.assertIn("never mention Madden ratings", text)
        self.assertIn("exactly as given", text)
        self.assertEqual(kind_key({"kind": "DRAFT_BOARD", "subject": {"type": "prospect"}}), "DRAFT_BOARD")

    def test_directives_name_the_draft_tools(self):
        with open("newsroom/config/article_types.json") as f:
            types = json.load(f)["article_types"]
        mock_draft = types["OFFSEASON_MOCK_DRAFT"]
        self.assertIn("get_draft_risers_fallers", mock_draft["curatorial_autonomy"])
        self.assertIn("Never mention a prospect's Madden rating or OVR", mock_draft["curatorial_autonomy"])
        for key in ("OFFSEASON_MOCK_DRAFT", "DRAFT_BLUEPRINT_ROSTER_AUDIT", "FRANCHISE_STATE_OF_THE_UNION"):
            self.assertIn("get_team_draft_record", types[key]["curatorial_autonomy"], key)
            self.assertIn("Team Draft Record (get_team_draft_record)", types[key]["data_queried"], key)
        for key in ("DRAFT_BLUEPRINT_ROSTER_AUDIT", "FRANCHISE_STATE_OF_THE_UNION"):
            self.assertIn("hidden", types[key]["curatorial_autonomy"], key)


if __name__ == "__main__":
    unittest.main()
