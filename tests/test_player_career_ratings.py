"""Pure tests for get_player_career's ratings by season (newsroom.db_tools.rating_history). No DB."""
import unittest

from newsroom.db_tools import rating_history


class RatingHistoryTest(unittest.TestCase):
    def test_change_against_the_season_before(self):
        got = rating_history([(2035, "CAR", 24, 78, "Star"), (2036, "CAR", 25, 82, "Superstar")])
        self.assertNotIn("ovr_change", got[0])
        self.assertEqual(got[1]["ovr_change"], 4)
        self.assertEqual(got[1]["dev_trait"], "Superstar")

    def test_no_change_across_a_gap_or_unrated_card(self):
        got = rating_history([(2035, "NO", 30, 80, "Normal"), (2036, "NO", 31, None, "Normal"),
                              (2037, "NO", 32, 77, "Normal")])
        self.assertNotIn("ovr_change", got[1])
        self.assertNotIn("ovr_change", got[2])
        got = rating_history([(2035, "NO", 30, 80, "Normal"), (2037, "TB", 32, 77, "Normal")])
        self.assertNotIn("ovr_change", got[1])

    def test_hidden_or_missing_dev_is_unknown(self):
        got = rating_history([(2036, "ATL", 21, 70, "Hidden"), (2037, "ATL", 22, 73, None)])
        self.assertEqual([g["dev_trait"] for g in got], ["unknown", "unknown"])


if __name__ == "__main__":
    unittest.main()
