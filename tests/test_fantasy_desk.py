"""Unit tests for the Mu Fantasy desk: weekly slates, config consistency and fantasy prompts.

Run: python3 -m unittest tests.test_fantasy_desk
"""
import json
import os
import re
import unittest

from newsroom import fantasy_desk as fd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "newsroom", "config")


def types_of(week):
    return [s["article_type"] for s in fd.fantasy_slate(week)]


class SlateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CONFIG, "article_types.json")) as f:
            cls.article_types = json.load(f)["article_types"]
        with open(os.path.join(CONFIG, "fantasy_writers.json")) as f:
            cls.writers = json.load(f)["writers"]

    def test_week_1_is_positional_draft_tiers(self):
        slate = fd.fantasy_slate(1)
        self.assertEqual([s["slot"] for s in slate[:4]], [fd.HERO, fd.CARD1, fd.CARD2, fd.CARD3])
        self.assertTrue(all(s["article_type"] == "FANTASY_DRAFT_TIERS" for s in slate[:4]))
        self.assertEqual([re.search(r"Position: (\w+)", s["note"]).group(1) for s in slate[:4]], ["WR", "RB", "QB", "TE"])
        self.assertEqual(types_of(1)[4:], ["FANTASY_SLEEPERS_BUSTS", "FANTASY_MOCK_DRAFT", "FANTASY_ROOKIE_RANKINGS"])

    def test_regular_season_front_page_and_card3(self):
        for week in range(2, 15):
            t = types_of(week)
            self.assertEqual(t[:3], ["FANTASY_START_SIT", "FANTASY_WAIVER_WIRE", "FANTASY_WEEKLY_REACTIONS"])
            self.assertEqual(t[3], fd.CARD3_SCHEDULE[week])
            self.assertIn("FANTASY_RANKINGS_TIERS", t)
            self.assertEqual("FANTASY_MATCHUP_EXPLOITS" in t, week >= 3)
        self.assertEqual(fd.CARD3_SCHEDULE[12], "FANTASY_TRADE_DEADLINE_SPECIAL")
        self.assertEqual(fd.CARD3_SCHEDULE[14], "FANTASY_MUST_WIN_WEEK")
        self.assertNotEqual(fd.CARD3_SCHEDULE[2], "FANTASY_DVP_REPORT")   # DvP needs games first

    def test_playoffs_review_and_offseason(self):
        for week in (15, 16, 17):
            self.assertEqual(types_of(week)[0], "FANTASY_START_SIT")
        self.assertEqual(types_of(17)[1], "FANTASY_CEILING_PLAYS")
        self.assertEqual(types_of(18)[:4], ["FANTASY_SEASON_IN_REVIEW", "FANTASY_AWARDS", "FANTASY_DRAFT_RETRO",
                                            "FANTASY_PROJECTION_REPORT_CARD"])
        for week in range(19, 24):
            self.assertEqual(fd.fantasy_slate(week), [])
            self.assertIsNone(fd.fantasy_phase(week))

    def test_kdst_gated_until_phase_3(self):
        self.assertFalse(any("DST" in t for w in range(1, 19) for t in types_of(w)))

    def test_every_slotted_type_is_configured_with_fantasy_writers(self):
        for week in range(1, 19):
            slots = fd.fantasy_slate(week)
            self.assertEqual(len({s["slot"] for s in slots}), len(slots))
            for s in slots:
                at = self.article_types.get(s["article_type"])
                self.assertIsNotNone(at, s["article_type"])
                for w in at["allowed_writers"]:
                    self.assertIn(w, self.writers, f"{s['article_type']}: {w} has no fantasy voice")

    def test_fantasy_slots_are_not_national_slots(self):
        for week in range(1, 19):
            for s in fd.fantasy_slate(week):
                name = s["slot"]
                self.assertTrue(name.startswith("Fantasy "))
                self.assertFalse(name in ("Hero Slot", "Standalone") or name.startswith("Slot "))
                self.assertLessEqual(len(name), 32)      # news_feed.slot is VARCHAR(32)

    def test_fantasy_types_have_full_config(self):
        keys = {"name", "allowed_writers", "writer_display_names", "writing_archetype_category", "granularity",
                "focus", "data_queried", "curatorial_autonomy", "structure_directive", "directive",
                "example_prompt_hook", "structure_source"}
        fantasy = {k: v for k, v in self.article_types.items() if k.startswith("FANTASY_")}
        self.assertEqual(len(fantasy), 26)
        for k, v in fantasy.items():
            self.assertEqual(set(v), keys, k)
            self.assertEqual(len(v["allowed_writers"]), len(v["writer_display_names"]), k)
            self.assertTrue(all(t.startswith("get_") for t in v["data_queried"]), k)


class PromptTests(unittest.TestCase):
    def test_fantasy_prompt_uses_fantasy_voice_desk_rules_and_tools(self):
        from newsroom.swarm_dispatcher import SwarmDispatcher
        d = SwarmDispatcher(api_key="test", debug_mode=False)
        brief = {"desk": "Mu Fantasy", "publication_week": 10}
        p = d.compile_agent_prompt("Field_Yates", "YATES", "", "@FieldYates", "Field Yates", None, brief, 2035, 10,
                                   "ARTICLE", article_type="FANTASY_WAIVER_WIRE", desk_note="Test note.")
        self.assertIn("fantasy football column in the style of Field Yates", p["system_instruction"])
        self.assertIn("get_fantasy_waiver_wire", p["system_instruction"])
        self.assertIn("FANTASY DESK RULES", p["task_instruction"])
        self.assertIn("YOUR SELECTION FILTER (YATES)", p["task_instruction"])
        self.assertIn("EDITORIAL DESK ASSIGNMENT (MANDATORY):\n- Test note.", p["task_instruction"])
        self.assertIn("FANTASY REGULAR SEASON, PUBLISHED BEFORE WEEK 10", p["task_instruction"])
        self.assertNotIn("CRITICAL WRITER RESEARCH TOOLS", p["task_instruction"])

        national = d.compile_agent_prompt("Field_Yates", "YATES", "", "@FieldYates", "Field Yates", None, brief,
                                          2035, 10, "ARTICLE", article_type="POSITIONAL_PLAYER_TIERS")
        self.assertNotIn("get_fantasy_", national["system_instruction"])
        self.assertNotIn("FANTASY DESK RULES", national["task_instruction"])
        self.assertIn("Your tweets are incredibly structured", national["system_instruction"])


if __name__ == "__main__":
    unittest.main()
