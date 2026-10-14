"""Consistency checks between the Editorial Desk's writer names and the persona/curatorial configs. No database required.

Run: python3 -m unittest tests.test_writer_config
"""
import ast
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "newsroom", "config")


def load(name, key=None):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        data = json.load(f)
    return data[key] if key else data


def dispatcher_literal(name):
    """Reads a module-level literal from swarm_dispatcher.py without importing it (no psycopg needed)."""
    with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


class WriterConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.personas = load("agent_personas.json", "archetypes")
        cls.curatorial = load("curatorial_autonomy_directives.json")
        cls.writer_structures = load("article_structure_directives.json")
        cls.article_types = load("article_types.json", "article_types")
        cls.pools = dispatcher_literal("REGULAR_SEASON_FEATURE_POOLS")
        cls.aliases = dispatcher_literal("WRITER_ARCHETYPE_ALIASES")

    def columnists(self):
        return {c for phase in self.pools.values() for c in phase["columnists"]}

    def test_every_article_writer_has_a_persona(self):
        for at_id, info in self.article_types.items():
            for writer in info.get("allowed_writers", []):
                self.assertIn(writer, self.personas, f"{at_id} allows {writer}, which has no persona")

    def test_every_rotating_columnist_has_a_persona_and_curatorial_directive(self):
        for writer in self.columnists():
            self.assertIn(writer, self.personas, f"columnist {writer} has no persona")
            self.assertIn(writer, self.curatorial, f"columnist {writer} has no curatorial directive")

    def test_rotating_columnists_can_write_opinion_columns(self):
        allowed = set(self.article_types["NATIONAL_OPINION_COLUMN"]["allowed_writers"])
        self.assertLessEqual(self.columnists(), allowed)

    def test_no_aliases_in_config(self):
        used = self.columnists() | {w for i in self.article_types.values() for w in i.get("allowed_writers", [])}
        self.assertFalse(used & set(self.aliases), f"use canonical archetypes, not aliases: {used & set(self.aliases)}")

    def test_aliases_point_at_real_personas(self):
        for alias, canonical in self.aliases.items():
            self.assertIn(canonical, self.personas, alias)

    def test_types_without_their_own_structure_have_one_per_writer(self):
        # Article types with no structure_directive fall back to the writer's column format.
        for at_id, info in self.article_types.items():
            if info.get("structure_directive"):
                continue
            for writer in info.get("allowed_writers", []):
                self.assertTrue(self.writer_structures.get(writer, "").strip(),
                                f"{at_id} has no structure of its own and {writer} has no column format")

    def test_no_injury_asks_in_beat_or_preview_configs(self):
        # The league has no injury data; these prompts must not ask for it.
        for at_id in ("LOCAL_POSTGAME_REPORT_CARD", "LOCAL_MATCHUP_PRIMER", "LOCAL_PLAYER_SPOTLIGHT",
                      "LOCAL_SEASON_PROGRESS_REPORT", "SLATE_LOOKAHEAD_PREDICTIONS"):
            info = self.article_types[at_id]
            text = " ".join(str(info.get(f) or "") for f in ("directive", "curatorial_autonomy", "structure_directive",
                                                              "example_prompt_hook", "data_queried"))
            self.assertNotIn("injur", text.lower(), at_id)

    def test_power_rankings_header_matches_continuity_block(self):
        st = self.article_types["POWER_RANKINGS"]["structure_directive"]
        self.assertIn("## [Rank]. [City] [Team] ([W-L]) (Previous: N | Change: ±N)", st)
        self.assertNotIn("Rise:", st)

    def test_awards_tracker_is_oroy_not_defense(self):
        info = self.article_types["AWARDS_TRACKER"]
        text = (info["structure_directive"] + info["focus"] + info["example_prompt_hook"]).lower()
        self.assertIn("rookie", text)
        self.assertNotIn("defensive", text)

    def test_roundtable_is_always_the_panel(self):
        self.assertEqual(self.article_types["SUPER_BOWL_ROUNDTABLE_CONSENSUS"]["allowed_writers"], ["ROUNDTABLE_PANEL"])

    def test_tweet_pool_excludes_article_only_voices(self):
        excluded = set(dispatcher_literal("TWEET_EXCLUDED_ARCHETYPES"))
        self.assertTrue({"ROUNDTABLE_PANEL", "SKIP_BAYLESS", "LOCAL_BEAT_REPORTER"} <= excluded)
        self.assertNotIn("SKIP_TWEET", excluded)  # Skip still tweets, once, through his tweet account

    def test_no_contract_asks(self):
        # Contracts aren't modelled; prompts may forbid citing them but must not ask for them.
        asks = ("contract-year", "analyzing the salary cap", "cap space", "cap investment", "extension value", "to their contract")
        texts = {f"persona {k}": v for k, v in self.personas.items()}
        for at_id, info in self.article_types.items():
            texts[at_id] = " ".join(str(v) for v in info.values())
        for name, text in texts.items():
            for phrase in asks:
                self.assertNotIn(phrase, text.lower(), f"{name} asks about '{phrase}'")

    def test_historical_comparisons_are_optional(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertIn("HISTORICAL COMPARISONS (OPTIONAL)", src)
        self.assertNotIn("State the historical comparison explicitly", src)

    def test_top_100_counts_down_and_ends_on_number_one(self):
        info = self.article_types["NFL_TOP_100_HIERARCHY"]
        text = " ".join(str(info[f]) for f in ("directive", "structure_directive", "example_prompt_hook")).lower()
        self.assertIn("no. 100 to no. 1", text)
        self.assertIn("end the article with the no. 1 entry", text)
        self.assertNotIn("### the snub", text)
        self.assertNotIn("dissect controversial snubs", text)

    def test_top_100_pool_is_rated_not_popular(self):
        cur = self.article_types["NFL_TOP_100_HIERARCHY"]["curatorial_autonomy"]
        self.assertIn("get_top_rated_players", cur)
        self.assertNotIn("get_top_popular_players", cur)
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            self.assertIn("            get_top_rated_players,", f.read())  # registered as a writer tool

    def test_bayless_columns_use_his_article_persona(self):
        self.assertEqual(self.aliases.get("BAYLESS"), "SKIP_BAYLESS")
        self.assertIn("SKIP_BAYLESS", self.article_types["NATIONAL_OPINION_COLUMN"]["allowed_writers"])


if __name__ == "__main__":
    unittest.main()
