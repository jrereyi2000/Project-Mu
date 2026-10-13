"""Unit tests for parsing and validating the weekly Power Rankings article. No database required.

Run: python3 -m unittest tests.test_power_rankings
"""
import unittest

from newsroom.power_rankings import fix_prompt, parse_power_rankings, resolve_team, split_kicker

NICKNAMES = ["Cowboys", "Seahawks", "Giants", "Eagles", "Buccaneers", "Packers", "Panthers", "Vikings", "Titans",
             "Browns", "Steelers", "Ravens", "Patriots", "Jets", "Dolphins", "Bills", "Rams", "Commanders",
             "Cardinals", "Bengals", "Falcons", "Jaguars", "49ers", "Texans", "Saints", "Colts", "Broncos", "Chiefs",
             "Bears", "Lions", "Raiders", "Chargers"]
TEAMS = [(i + 1, nick, "City", nick[:3].upper()) for i, nick in enumerate(NICKNAMES)]
TEAMS[2] = (3, "Giants", "New York", "NYG")
TEAMS[13] = (14, "Jets", "New York", "NYJ")


def article(order=None, extra=""):
    order = order or NICKNAMES
    parts = ["# NFL Power Rankings Week 9: The Theme", "> **EXCERPT:** Teaser.", "", "## 1. The Macro Overview", "Intro."]
    for rank, nick in enumerate(order, start=1):
        parts += [f"## {rank}. City {nick} (5-3) (Previous: — | New)",
                  f"**Kicker for {nick}:** Line one.", f"Blurb sentence one for {nick}. Sentence two.", ""]
    parts += ["***", "", "The walk-off paragraph about the gap.", extra]
    return "\n".join(parts)


class ResolveTeamTests(unittest.TestCase):
    def test_nickname_city_and_abbreviation(self):
        self.assertEqual(resolve_team("New York Giants (11-5) (Previous: 6 | Change: -1)", TEAMS), 3)
        self.assertEqual(resolve_team("**San Francisco 49ers**", TEAMS), 23)
        self.assertEqual(resolve_team("NYJ", TEAMS), 14)
        self.assertIsNone(resolve_team("New York (11-5)", TEAMS))     # ambiguous city only


class ParseTests(unittest.TestCase):
    def test_valid_article(self):
        entries, errors = parse_power_rankings(article(), TEAMS)
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 32)
        self.assertEqual(entries[0]["kicker"], "Kicker for Cowboys:")
        self.assertEqual(entries[0]["blurb"], "Line one. Blurb sentence one for Cowboys. Sentence two.")
        self.assertNotIn("walk-off", entries[-1]["blurb"])            # the closing paragraph isn't swallowed

    def test_numbered_section_header_is_not_a_team(self):
        _, errors = parse_power_rankings(article(extra="## 2. The Drop-Off\nMore text."), TEAMS)
        self.assertEqual(errors, [])

    def test_missing_and_duplicate_teams(self):
        order = NICKNAMES[:31] + ["Cowboys"]                            # Chargers missing, Cowboys twice
        _, errors = parse_power_rankings(article(order), TEAMS)
        self.assertTrue(any("ranked more than once" in e and "Cowboys" in e for e in errors))
        self.assertTrue(any("missing" in e and "Chargers" in e for e in errors))

    def test_truncated_list(self):
        _, errors = parse_power_rankings(article(NICKNAMES[:10]), TEAMS)
        self.assertTrue(any("Missing ranks" in e for e in errors))

    def test_unmatched_team_with_record(self):
        text = article().replace("## 7. City Panthers", "## 7. Carolina Cats")
        _, errors = parse_power_rankings(text, TEAMS)
        self.assertTrue(any("Carolina Cats" in e for e in errors))

    def test_kicker_without_bold(self):
        self.assertEqual(split_kicker("Just a blurb.\n\nNext paragraph."), (None, "Just a blurb."))

    def test_fix_prompt_lists_errors(self):
        self.assertIn("- Missing ranks: [32]", fix_prompt(["Missing ranks: [32]"]))


if __name__ == "__main__":
    unittest.main()
