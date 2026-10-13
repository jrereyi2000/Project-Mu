"""Unit tests for playoff overrides (newsroom/playoff_overrides.py + config/playoff_overrides.json). No database required.

Run: python3 -m unittest tests.test_playoff_overrides
"""
import json
import os
import unittest

from newsroom.playoff_overrides import find_active_override, normalize_temporal_mode, validate_playoff_overrides

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "newsroom", "config")


def load(name, key):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return json.load(f)[key]


def override(name="X", weeks=(20,), mode="RETROSPECTIVE", **extra):
    return {"override_name": name, "target_weeks": list(weeks), "temporal_mode": mode, "directive_override": "d", **extra}


class NormalizeTests(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(normalize_temporal_mode("retrospective"), "RETROSPECTIVE")
        self.assertEqual(normalize_temporal_mode(" Prospective "), "PROSPECTIVE")

    def test_unknown_or_missing_is_none(self):
        self.assertIsNone(normalize_temporal_mode("anticipatory"))
        self.assertIsNone(normalize_temporal_mode(None))


class ValidateTests(unittest.TestCase):
    def test_flags_unknown_mode(self):
        problems = validate_playoff_overrides({"T": [override(mode="anticipatory")]}, ["T"])
        self.assertTrue(any("temporal_mode" in p for p in problems))

    def test_flags_regular_season_week(self):
        problems = validate_playoff_overrides({"T": [override(weeks=(18, 19))]}, ["T"])
        self.assertTrue(any("week 18" in p for p in problems))

    def test_flags_week_claimed_twice(self):
        problems = validate_playoff_overrides({"T": [override("A", (20,)), override("B", (20,))]}, ["T"])
        self.assertTrue(any("also claimed" in p for p in problems))

    def test_flags_unknown_article_type(self):
        self.assertTrue(validate_playoff_overrides({"NOPE": [override()]}, ["T"]))

    def test_preview_week_requires_prospective(self):
        problems = validate_playoff_overrides({"T": [override(preview_week=23)]}, ["T"])
        self.assertTrue(any("preview_week" in p for p in problems))


class ShippedConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overrides = load("playoff_overrides.json", "playoff_overrides")
        cls.article_types = load("article_types.json", "article_types")

    def test_config_is_valid(self):
        self.assertEqual(validate_playoff_overrides(self.overrides, self.article_types.keys()), [])

    def test_week_19_autopsy_reviews_regular_season_week_18(self):
        # Week 19 looks back at Week 18, which is not a playoff round, so the base article type applies.
        self.assertIsNone(find_active_override(self.overrides, "SINGLE_GAME_SCHEMATIC_AUTOPSY", 19))

    def test_playoff_round_autopsies(self):
        for week in (20, 21, 22):
            ov = find_active_override(self.overrides, "SINGLE_GAME_SCHEMATIC_AUTOPSY", week)
            self.assertEqual(ov["override_name"], "PLAYOFF_ELIMINATION_ROUND_AUTOPSY")
            self.assertEqual(normalize_temporal_mode(ov["temporal_mode"]), "RETROSPECTIVE")

    def test_super_bowl_bye_tale_of_the_tape_previews_week_23(self):
        ov = find_active_override(self.overrides, "SLATE_LOOKAHEAD_PREDICTIONS", 22)
        self.assertEqual(ov["override_name"], "SUPER_BOWL_EARLY_TALE_OF_THE_TAPE")
        self.assertEqual(normalize_temporal_mode(ov["temporal_mode"]), "PROSPECTIVE")
        self.assertEqual(ov["preview_week"], 23)

    def test_awards_tracker_finalists_then_winners(self):
        self.assertEqual(find_active_override(self.overrides, "AWARDS_TRACKER", 21)["override_name"], "AWARDS_FINALISTS")
        self.assertEqual(find_active_override(self.overrides, "AWARDS_TRACKER", 22)["override_name"], "AWARDS_WINNERS")
        for week in (21, 22):
            ov = find_active_override(self.overrides, "AWARDS_TRACKER", week)
            self.assertIn("regular season only", ov["curatorial_autonomy_override"])

    def test_contender_blueprint_covers_remaining_teams(self):
        self.assertEqual(find_active_override(self.overrides, "SUPER_BOWL_CONTENDER_BLUEPRINT", 20)["override_name"],
                         "DIVISIONAL_ROUND_CONTENDER_AUDIT")
        self.assertEqual(find_active_override(self.overrides, "SUPER_BOWL_CONTENDER_BLUEPRINT", 21)["override_name"],
                         "FINAL_FOUR_CONTENDER_AUDIT")

    def test_no_injury_asks_in_overrides(self):
        for entries in self.overrides.values():
            for ov in entries:
                for field in ("directive_override", "curatorial_autonomy_override", "structure_directive_override"):
                    self.assertNotIn("injur", (ov.get(field) or "").lower(), ov["override_name"])

    def test_previews_are_prospective(self):
        for at_id, week in (("SLATE_LOOKAHEAD_PREDICTIONS", 19), ("SLATE_LOOKAHEAD_PREDICTIONS", 21),
                            ("SINGLE_GAME_SCHEMATIC_AUTOPSY", 23)):
            ov = find_active_override(self.overrides, at_id, week)
            self.assertEqual(normalize_temporal_mode(ov["temporal_mode"]), "PROSPECTIVE", (at_id, week))


if __name__ == "__main__":
    unittest.main()
