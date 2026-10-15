"""Unit tests for the clutch profile (PROJECT_SPEC §2.15) and the game-script publish lock. No database required.

Run: python3 -m unittest tests.test_clutch_engine
"""
import unittest
from unittest import mock

from engines import clutch_engine as C
from engines import game_script_engine as G


def script(away, home, quarters, scoring, overtime=False):
    """A minimal game script: scoring is [(period, team, points)] in order."""
    score = {away: 0, home: 0}
    largest = {away: 0, home: 0}
    entries = []
    for i, (period, team, pts) in enumerate(scoring):
        score[team] += pts
        for t, o in ((away, home), (home, away)):
            largest[t] = max(largest[t], score[t] - score[o])
        entries.append({"period": period, "clock": "5:00", "team": team, "play": f"{team} score {i}",
                        "tags": [], "points": pts, "score_after": dict(score)})
    winner = max(score, key=score.get) if score[away] != score[home] else None
    if winner:
        loser = home if winner == away else away
        last_not = max((i for i, e in enumerate(entries) if e["score_after"][winner] <= e["score_after"][loser]),
                       default=-1)
        entries[last_not + 1]["tags"].append("lead for good")
    return {"teams": {"away": away, "home": home}, "quarter_scores": quarters, "scoring": entries,
            "largest_lead": largest, "overtime": overtime}


COMEBACK = script("BUF", "NYJ", {"BUF": [0, 7, 0, 14], "NYJ": [7, 10, 0, 0]},
                  [("Q1", "NYJ", 7), ("Q2", "NYJ", 7), ("Q2", "BUF", 7), ("Q2", "NYJ", 3), ("Q4", "BUF", 7), ("Q4", "BUF", 7)])
ROUT = script("BUF", "MIA", {"BUF": [14, 7, 7, 0], "MIA": [0, 0, 0, 3]},
              [("Q1", "BUF", 7), ("Q1", "BUF", 7), ("Q2", "BUF", 7), ("Q3", "BUF", 7), ("Q4", "MIA", 3)])


class SituationTest(unittest.TestCase):
    def test_comeback_and_blown_lead(self):
        s = C.team_situations(COMEBACK)
        buf, nyj = s["BUF"], s["NYJ"]
        self.assertEqual((buf["result"], buf["final"]), ("W", "21-17"))
        self.assertTrue(buf["fourth_quarter_comeback"])
        self.assertEqual(buf["late_deficit"], 10)            # entered the fourth down 17-7
        self.assertTrue(buf["game_winning_drive"])
        self.assertEqual(buf["halftime_margin"], -10)
        self.assertTrue(nyj["blew_double_digit_lead"])
        self.assertTrue(nyj["blew_fourth_quarter_lead"])
        self.assertFalse(nyj["fourth_quarter_comeback"])

    def test_rout_has_no_late_drama(self):
        buf = C.team_situations(ROUT)["BUF"]
        self.assertFalse(buf["fourth_quarter_comeback"])
        self.assertFalse(buf["game_winning_drive"])          # the lead for good came in the first quarter
        self.assertEqual(buf["largest_lead"], 28)

    def test_down_more_than_one_score_all_quarter_is_not_a_comeback(self):
        s = script("A", "B", {"A": [0, 0, 0, 3], "B": [14, 0, 0, 0]},
                   [("Q1", "B", 7), ("Q1", "B", 7), ("Q4", "A", 3)])
        self.assertFalse(C.team_situations(s)["A"]["fourth_quarter_comeback"])


class ProfileTest(unittest.TestCase):
    def rows(self):
        return [
            {"game_id": 1, "week": 1, "game_type": "REGULAR_SEASON", "script": COMEBACK,
             "team_ids": {"BUF": 3, "NYJ": 4}, "starters": {"BUF": (10, "Josh Allen"), "NYJ": (20, "Jets QB")}},
            {"game_id": 2, "week": 2, "game_type": "REGULAR_SEASON", "script": ROUT,
             "team_ids": {"BUF": 3, "MIA": 5}, "starters": {"BUF": (11, "Backup QB"), "MIA": (30, "Fins QB")}},
            {"game_id": 3, "week": 19, "game_type": "WILD_CARD", "script": COMEBACK,
             "team_ids": {"BUF": 3, "NYJ": 4}, "starters": {"BUF": (10, "Josh Allen"), "NYJ": (20, "Jets QB")}},
        ]

    def test_team_profile_splits_regular_season_and_playoffs(self):
        p = C.team_profile(self.rows(), 3)
        self.assertEqual(p["regular_season"]["record"], "2-0")
        self.assertEqual(p["regular_season"]["fourth_quarter_comebacks"], 1)
        self.assertEqual(p["regular_season"]["record_trailing_at_half"], "1-0")
        self.assertEqual(p["postseason"]["games"], 1)
        self.assertIn("trailed by as many as 10", p["regular_season"]["comeback_games"][0])

    def test_quarterback_gets_only_his_starts(self):
        p = C.qb_profile(self.rows(), 10)["regular_season"]
        self.assertEqual((p["games"], p["fourth_quarter_comebacks"]), (1, 1))
        self.assertEqual(C.qb_profile(self.rows(), 11)["regular_season"]["fourth_quarter_comebacks"], 0)

    def test_week_cutoff_hides_later_games(self):
        self.assertEqual(C.team_profile(self.rows(), 3, before_week=2)["regular_season"]["games"], 1)

    def test_league_leaders(self):
        leaders = C.league_leaders(self.rows())
        self.assertEqual(leaders["fourth_quarter_comebacks_qb"], ["Josh Allen (BUF): 1 in 1 starts"])
        self.assertEqual(leaders["blown_double_digit_leads_team"], ["NYJ: 1 in 1 games"])


class LockTest(unittest.TestCase):
    """--force skips scripts that something published after them may cite; changed facts always rebuild."""

    def run_ensure(self, stored_hash, published, force=True, unlock=False):
        game = {"season": 2035, "week": 5}
        cur = mock.MagicMock()
        cur.fetchone.side_effect = [(stored_hash, {"old": True}, "2026-10-15"), (1,) if published else None]
        with mock.patch.object(G, "fetch_inputs", return_value=game), \
                mock.patch.object(G, "facts_hash", return_value="h"), \
                mock.patch.object(G, "fetch_context"), mock.patch.object(G, "build_script", return_value={"new": True}):
            return G.ensure_script(cur, 1, force=force, unlock=unlock)

    def test_force_refused_after_publication(self):
        self.assertEqual(self.run_ensure("h", published=True), ({"old": True}, "locked"))

    def test_force_allowed_before_publication(self):
        self.assertEqual(self.run_ensure("h", published=False), ({"new": True}, "rebuilt"))

    def test_unlock_overrides(self):
        self.assertEqual(self.run_ensure("h", published=True, unlock=True), ({"new": True}, "rebuilt"))

    def test_changed_facts_always_rebuild(self):
        self.assertEqual(self.run_ensure("old-hash", published=True, force=False), ({"new": True}, "rebuilt"))


class RegistrationTest(unittest.TestCase):
    def test_tool_is_registered_and_named_in_both_research_blocks(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertIn("    get_clutch_profile,", src)            # import
        self.assertIn("            get_clutch_profile,", src)    # tool list
        self.assertEqual(src.count("call `get_clutch_profile`"), 1)        # national research block
        self.assertEqual(src.count("with `get_clutch_profile`"), 1)        # beat research block


if __name__ == "__main__":
    unittest.main()
