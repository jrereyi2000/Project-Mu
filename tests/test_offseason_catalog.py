"""Tests for Offseason Article Types in Newsroom Catalog.

Validates that all six offseason article types are defined in article_types.json,
comply with the required catalog schema, have allowed writers matching agent_personas.json,
and strictly embed the editorial rules and structure directives specified in the implementation plan.

Run with:
    python3 -m pytest -q tests/test_offseason_catalog.py
    python3 tests/test_offseason_catalog.py
"""

import json
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTICLE_TYPES_PATH = REPO_ROOT / "newsroom" / "config" / "article_types.json"
AGENT_PERSONAS_PATH = REPO_ROOT / "newsroom" / "config" / "agent_personas.json"

EXPECTED_OFFSEASON_TYPES = {
    "SUPER_BOWL_GAME_RECAP": {
        "writers": ["BARNWELL", "ROSENTHAL", "CHADIHA"],
        "expected_hook": "Explain how the Super Bowl was decided, identifying the pivotal stretch and the performances that changed the game.",
    },
    "SUPER_BOWL_CHAMPION_RETROSPECTIVE": {
        "writers": ["CHADIHA", "SANDO", "ROSENTHAL"],
        "expected_hook": "Trace the champion's path from its regular-season identity to the Super Bowl, and explain what the title validates.",
    },
    "SUPER_BOWL_RUNNER_UP_POSTMORTEM": {
        "writers": ["SANDO", "BARNWELL"],
        "expected_hook": "Explain what kept the runner-up from winning the championship and identify the most urgent step toward another chance.",
    },
    "COACHING_CAROUSEL_AUDIT": {
        "writers": ["SANDO", "BARNWELL"],
        "expected_hook": "Grade the coaching changes entered for this offseason, explaining which teams addressed their needs and which accepted the greatest risk.",
    },
    "FREE_AGENCY_SIGNING_GRADES": {
        "writers": ["YATES", "BARNWELL", "SANDO"],
        "expected_hook": "Grade the marquee signings entered for this offseason, weighing player fit and team need against the risks supported by available information.",
    },
    "NFL_DRAFT_CLASS_RECAP": {
        "writers": ["MEL_KIPER", "YATES", "BARNWELL"],
        "expected_hook": "Review the captured first-round results, grade the most consequential selections, and identify the picks that most changed a team's outlook.",
    },
}

REQUIRED_SCHEMA_KEYS = {
    "name",
    "allowed_writers",
    "writer_display_names",
    "writing_archetype_category",
    "granularity",
    "focus",
    "data_queried",
    "example_prompt_hook",
    "directive",
    "curatorial_autonomy",
    "structure_directive",
    "structure_source",
}


class TestOffseasonCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ARTICLE_TYPES_PATH, "r", encoding="utf-8") as f:
            cls.catalog_data = json.load(f)
        cls.article_types = cls.catalog_data.get("article_types", {})

        with open(AGENT_PERSONAS_PATH, "r", encoding="utf-8") as f:
            cls.personas_data = json.load(f)
        cls.archetypes = set(cls.personas_data.get("archetypes", {}).keys())
        cls.article_personas = set(cls.personas_data.get("article_personas", {}).keys())
        cls.valid_writers = cls.archetypes | cls.article_personas

    def test_all_six_offseason_types_present(self):
        """Assert that all six offseason article types exist in the catalog."""
        for type_key in EXPECTED_OFFSEASON_TYPES:
            self.assertIn(
                type_key,
                self.article_types,
                f"Missing offseason article type '{type_key}' in article_types.json",
            )

    def test_schema_conformance(self):
        """Assert that every offseason article type contains all required schema keys."""
        for type_key in EXPECTED_OFFSEASON_TYPES:
            entry = self.article_types[type_key]
            missing_keys = REQUIRED_SCHEMA_KEYS - set(entry.keys())
            self.assertEqual(
                missing_keys,
                set(),
                f"Type '{type_key}' is missing required schema keys: {missing_keys}",
            )
            self.assertEqual(
                entry.get("structure_source"),
                "article",
                f"Type '{type_key}' structure_source should be 'article'",
            )
            self.assertIsInstance(
                entry.get("structure_directive"),
                str,
                f"Type '{type_key}' structure_directive must be a non-empty string",
            )
            self.assertTrue(
                len(entry["structure_directive"].strip()) > 0,
                f"Type '{type_key}' structure_directive cannot be blank",
            )
            self.assertIsInstance(
                entry.get("example_prompt_hook"),
                str,
                f"Type '{type_key}' example_prompt_hook must be a non-empty string",
            )
            self.assertTrue(
                len(entry["example_prompt_hook"].strip()) > 0,
                f"Type '{type_key}' example_prompt_hook cannot be blank",
            )
            self.assertIsInstance(entry.get("data_queried"), list)
            self.assertTrue(len(entry["data_queried"]) > 0)

    def test_allowed_writers_and_display_names(self):
        """Validate allowed_writers against agent personas and verify display name counts."""
        for type_key, spec in EXPECTED_OFFSEASON_TYPES.items():
            entry = self.article_types[type_key]
            actual_writers = entry.get("allowed_writers", [])
            expected_writers = spec["writers"]

            self.assertEqual(
                actual_writers,
                expected_writers,
                f"Allowed writers mismatch for '{type_key}'. Expected {expected_writers}, got {actual_writers}",
            )

            for writer in actual_writers:
                self.assertIn(
                    writer,
                    self.valid_writers,
                    f"Writer '{writer}' in '{type_key}' does not exist in agent_personas.json",
                )

            display_names = entry.get("writer_display_names", [])
            self.assertEqual(
                len(display_names),
                len(actual_writers),
                f"Writer display names count mismatch for '{type_key}'.",
            )

    def test_prompt_hooks(self):
        """Verify prompt hooks match specification."""
        for type_key, spec in EXPECTED_OFFSEASON_TYPES.items():
            entry = self.article_types[type_key]
            self.assertEqual(
                entry.get("example_prompt_hook"),
                spec["expected_hook"],
                f"Prompt hook mismatch for '{type_key}'",
            )

    def test_free_agency_signing_grades_rules(self):
        """Strictly test FREE_AGENCY_SIGNING_GRADES rules:
        - Ban on numeric OVR
        - Qualitative tiers defined (95-99, 90-94)
        - Prohibit calling players All-Pro without verification in awards table
        - 90+ OVR scope only
        """
        entry = self.article_types["FREE_AGENCY_SIGNING_GRADES"]
        combined_text = (
            entry["directive"]
            + " "
            + entry["curatorial_autonomy"]
            + " "
            + entry["structure_directive"]
            + " "
            + entry["focus"]
        )

        self.assertIn("STRICT NUMERIC OVR BAN", combined_text)
        self.assertIn("Tier 1 superstar", combined_text)
        self.assertIn("High-impact marquee starter", combined_text)
        self.assertIn("awards", combined_text.lower())
        self.assertIn("All-Pro", combined_text)
        self.assertIn("90+", combined_text)

    def test_nfl_draft_class_recap_rules(self):
        """Strictly test NFL_DRAFT_CLASS_RECAP rules:
        - Round 1 only (picks 1-32)
        - Never imply coverage of rounds 2-7
        - Actual results only, never mocks
        """
        entry = self.article_types["NFL_DRAFT_CLASS_RECAP"]
        combined_text = (
            entry["directive"]
            + " "
            + entry["curatorial_autonomy"]
            + " "
            + entry["structure_directive"]
            + " "
            + entry["focus"]
        )

        self.assertIn("Round 1", combined_text)
        self.assertIn("1–32", combined_text)
        self.assertIn("Rounds 2–7", combined_text)
        self.assertIn("draft_selections", combined_text)
        self.assertIn("mock_draft_picks", combined_text)

    def test_coaching_carousel_audit_rules(self):
        """Strictly test COACHING_CAROUSEL_AUDIT rules:
        - Coordinator origins
        - Head coach slants (OFFENSIVE, DEFENSIVE, CEO)
        - Scheme inheritance rules
        """
        entry = self.article_types["COACHING_CAROUSEL_AUDIT"]
        combined_text = (
            entry["directive"]
            + " "
            + entry["curatorial_autonomy"]
            + " "
            + entry["structure_directive"]
        )

        self.assertIn("coordinator origin", combined_text.lower())
        self.assertIn("OFFENSIVE", combined_text)
        self.assertIn("DEFENSIVE", combined_text)
        self.assertIn("CEO", combined_text)
        self.assertIn("scheme inheritance", combined_text.lower())


if __name__ == "__main__":
    unittest.main()
