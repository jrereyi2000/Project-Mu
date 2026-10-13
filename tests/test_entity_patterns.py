"""Unit tests for team-mention matching in the article tagger (newsroom/entity_patterns.py). No database required.

Run: python3 -m unittest tests.test_entity_patterns
"""
import unittest

from newsroom.entity_patterns import team_mention_pattern

COMMANDERS = team_mention_pattern("Commanders", "Washington", "WAS")
SAINTS = team_mention_pattern("Saints", "New Orleans", "NO")
TITANS = team_mention_pattern("Titans", "Tennessee", "TEN")
BILLS = team_mention_pattern("Bills", "Buffalo", "BUF")
CHIEFS = team_mention_pattern("Chiefs", "Kansas City", "KC")
GIANTS = team_mention_pattern("Giants", "New York", "NYG")
NINERS = team_mention_pattern("49ers", "San Francisco", "SF")


def count(pattern, text):
    return len(pattern.findall(text))


class TeamMentionTests(unittest.TestCase):
    def test_ordinary_words_are_not_teams(self):
        text = "It was a game with no rhythm. Ten drives, no points, and he was benched. That pays the bills."
        for pattern in (COMMANDERS, SAINTS, TITANS, BILLS):
            self.assertEqual(count(pattern, text), 0, pattern.pattern)

    def test_all_caps_words_are_not_teams(self):
        text = "THERE WAS NO WAY. TEN MIN LEFT AND NO TIMEOUTS."
        self.assertEqual(count(COMMANDERS, text), 0)
        self.assertEqual(count(SAINTS, text), 0)
        self.assertEqual(count(TITANS, text), 0)

    def test_real_mentions_still_match(self):
        self.assertEqual(count(COMMANDERS, "Washington beat the Commanders' rival; the Washington Commanders are 6-2."), 3)
        self.assertEqual(count(SAINTS, "The Saints went to New Orleans."), 2)
        self.assertEqual(count(CHIEFS, "KC won. The Kansas City Chiefs are rolling; Kansas City fans agree."), 3)

    def test_full_name_counts_once(self):
        self.assertEqual(count(BILLS, "The Buffalo Bills won."), 1)

    def test_safe_abbreviations_match_uppercase_only(self):
        self.assertEqual(count(BILLS, "BUF 27, MIA 24"), 1)
        self.assertEqual(count(BILLS, "the buf fan"), 0)

    def test_shared_city_alone_is_not_a_mention(self):
        self.assertEqual(count(GIANTS, "New York traffic"), 0)
        self.assertEqual(count(GIANTS, "The New York Giants and the Giants"), 2)

    def test_numeric_nickname(self):
        self.assertEqual(count(NINERS, "The 49ers and San Francisco"), 2)


if __name__ == "__main__":
    unittest.main()
