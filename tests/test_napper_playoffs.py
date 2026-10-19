"""engines/napper_playoffs bracket logic (no database)."""
import unittest

from engines.napper_playoffs import build_bracket, team_week_note

# Team id = 100 + seed, 8 teams.
SEEDS = {100 + s: s for s in range(1, 9)}


def T(seed):
    return 100 + seed


class BuildBracketTest(unittest.TestCase):
    def test_quarterfinals_before_any_playoff_week_is_final(self):
        b = build_bracket(SEEDS, {}, set())
        self.assertEqual(b["rounds"], {15: [{"home": T(3), "away": T(6), "winner": None},
                                             {"home": T(4), "away": T(5), "winner": None}]})
        self.assertEqual(b["finish"], {T(7): "MISSED", T(8): "MISSED"})
        self.assertIsNone(b["champion"])

    def test_top_seed_plays_the_lowest_seed_left(self):
        # 6 upsets 3, 4 beats 5 -> 1 v 6, 2 v 4
        b = build_bracket(SEEDS, {15: {T(3): 90, T(6): 110, T(4): 100, T(5): 80}}, {15})
        self.assertEqual([(g["home"], g["away"]) for g in b["rounds"][16]], [(T(1), T(6)), (T(2), T(4))])
        self.assertEqual(b["finish"][T(3)], "QUARTERFINAL")
        self.assertEqual(b["finish"][T(5)], "QUARTERFINAL")
        self.assertNotIn(17, b["rounds"])

    def test_full_bracket_crowns_a_champion(self):
        scores = {15: {T(3): 120, T(6): 100, T(4): 90, T(5): 95},
                  16: {T(1): 100, T(5): 130, T(2): 110, T(3): 105},
                  17: {T(5): 99, T(2): 101}}
        b = build_bracket(SEEDS, scores, {15, 16, 17})
        self.assertEqual(b["rounds"][16][0], {"home": T(1), "away": T(5), "winner": T(5)})
        self.assertEqual(b["rounds"][17], [{"home": T(2), "away": T(5), "winner": T(2)}])
        self.assertEqual(b["champion"], T(2))
        self.assertEqual(b["finish"][T(5)], "RUNNER_UP")
        self.assertEqual(b["finish"][T(1)], "SEMIFINAL")

    def test_ties_go_to_the_higher_seed(self):
        b = build_bracket(SEEDS, {15: {T(3): 100, T(6): 100, T(4): 90, T(5): 90}}, {15})
        self.assertEqual([g["winner"] for g in b["rounds"][15]], [T(3), T(4)])


class TeamWeekNoteTest(unittest.TestCase):
    def test_notes(self):
        self.assertIn("regular season", team_week_note(None, SEEDS, T(1), 15))
        self.assertIsNone(team_week_note(None, SEEDS, T(1), 10))
        b = build_bracket(SEEDS, {15: {T(3): 120, T(6): 100, T(4): 90, T(5): 95}}, {15})
        self.assertIn("bye", team_week_note(b, SEEDS, T(2), 15))
        self.assertIsNone(team_week_note(b, SEEDS, T(3), 15))
        self.assertIn("missed the playoffs", team_week_note(b, SEEDS, T(8), 16))
        self.assertIn("quarterfinals", team_week_note(b, SEEDS, T(4), 16))
        self.assertIn("once Week 16 is final", team_week_note(b, SEEDS, T(1), 17))


if __name__ == "__main__":
    unittest.main()
