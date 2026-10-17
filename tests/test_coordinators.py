"""Comprehensive unit and integration tests for coach slants and coordinator infrastructure.
(COACH_SLANTS_AND_COORDINATORS_PLAN.md §3, §4, §5, §6).

Tests cover:
1. Scheme inheritance logic across OFFENSIVE, DEFENSIVE, and CEO slants.
2. Slant check constraints and defaults on `coaches`.
3. Table check, foreign key, and unique constraints on `coordinators` and `coordinator_tenures`.
4. `fetch_team_staff` functionality, effective unit schemes, and backward compatibility.
5. Seeder baseline accuracy, procedural fallbacks, and idempotency.

Run with:
    python3 -m pytest -q tests/test_coordinators.py
or:
    python3 tests/test_coordinators.py
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import psycopg
from db.db_handler import DatabaseHandler
from engines.coach_profile import (
    fetch_team_staff,
    staff_matchup,
)
from engines.coordinator_seeder import (
    COACH_BASELINE_SLANTS,
    generate_procedural_name,
    seed_all_coordinators_and_slants,
    seed_coach_slants,
    seed_coordinators,
)


class TestSchemeInheritanceLogic(unittest.TestCase):
    """Unit tests for tactical scheme inheritance (§5.2, §6.1) with mock cursors."""

    def test_offensive_slant_scheme_inheritance(self):
        """Under an OFFENSIVE HC, HC dictates offense; DC runs defense."""
        cur = MagicMock()
        cur.fetchone.return_value = (10, "Kyle Shanahan", "OFFENSIVE", "Wide Zone West Coast", "4-3 Over", "High", "High", "Low", "Low", 2026)
        # Mock fetchall: first for fetch_observed ([]), second for coordinators
        cur.fetchall.side_effect = [
            [],  # fetch_observed
            [
                ("OC", "Klay Kubiak", "West Coast", "SF Offense", "High", None, None),
                ("DC", "Raheem Morris", "4-3", "SF Defense", None, "Low", "Low"),
            ],
        ]

        staff = fetch_team_staff(cur, team_id=1, season=2026)
        self.assertIsNotNone(staff)
        self.assertEqual(staff["effective_offense"], "Wide Zone West Coast")  # Dictated by OFFENSIVE HC
        self.assertEqual(staff["effective_defense"], "4-3")                    # Coordinated by DC
        self.assertEqual(staff["head_coach"]["slant"], "OFFENSIVE")
        self.assertEqual(staff["offensive_coordinator"]["name"], "Klay Kubiak")
        self.assertEqual(staff["defensive_coordinator"]["name"], "Raheem Morris")
        self.assertIn("OFFENSIVE slant", staff["staff_philosophy_summary"])

    def test_defensive_slant_scheme_inheritance(self):
        """Under a DEFENSIVE HC, HC dictates defense; OC runs offense."""
        cur = MagicMock()
        cur.fetchone.return_value = (20, "DeMeco Ryans", "DEFENSIVE", "West Coast", "Wide 9 4-3", "Medium", "Low", "Low", "Medium", 2026)
        cur.fetchall.side_effect = [
            [],  # fetch_observed
            [
                ("OC", "Nick Caley", "Spread", "HOU Offense", "High", None, None),
                ("DC", "Matt Burke", "Base 4-3", "HOU Defense", None, "Medium", "Medium"),
            ],
        ]

        staff = fetch_team_staff(cur, team_id=2, season=2026)
        self.assertIsNotNone(staff)
        self.assertEqual(staff["effective_offense"], "Spread")        # Dictated by OC
        self.assertEqual(staff["effective_defense"], "Wide 9 4-3")    # Dictated by DEFENSIVE HC
        self.assertEqual(staff["head_coach"]["slant"], "DEFENSIVE")

    def test_ceo_slant_scheme_inheritance(self):
        """Under a CEO HC, offense delegates to OC and defense delegates to DC."""
        cur = MagicMock()
        cur.fetchone.return_value = (30, "Dan Campbell", "CEO", "Power Run", "Base 4-3", "High", "High", "Medium", "High", 2026)
        cur.fetchall.side_effect = [
            [],  # fetch_observed
            [
                ("OC", "Drew Petzing", "West Coast Spread", "DET Offense", "High", None, None),
                ("DC", "Kelvin Sheppard", "Multiple 4-3", "DET Defense", None, "High", "High"),
            ],
        ]

        staff = fetch_team_staff(cur, team_id=3, season=2026)
        self.assertIsNotNone(staff)
        self.assertEqual(staff["effective_offense"], "West Coast Spread")  # Delegated to OC
        self.assertEqual(staff["effective_defense"], "Multiple 4-3")        # Delegated to DC
        self.assertEqual(staff["head_coach"]["slant"], "CEO")

    def test_missing_coordinator_fallbacks(self):
        """When coordinators are absent, unit schemes fall back gracefully to HC schemes."""
        cur = MagicMock()
        cur.fetchone.return_value = (40, "John Harbaugh", "CEO", "West Coast", "3-4", "High", "Low", "Low", "Low", 2026)
        cur.fetchall.side_effect = [
            [],  # fetch_observed
            [],  # No coordinators
        ]

        staff = fetch_team_staff(cur, team_id=4, season=2026)
        self.assertIsNotNone(staff)
        self.assertEqual(staff["effective_offense"], "West Coast")
        self.assertEqual(staff["effective_defense"], "3-4")
        self.assertIsNone(staff["offensive_coordinator"])
        self.assertIsNone(staff["defensive_coordinator"])

    def test_staff_matchup_integration(self):
        """staff_matchup works seamlessly with new staff dictionaries and legacy coach dicts."""
        off_staff = {
            "head_coach": {"coach_name": "Marcus Whitfield", "offensive_scheme": "Wide Zone West Coast", "pass_lean": "Low", "fourth_down_aggressiveness": "Medium"},
            "effective_offense": "Spread Option",
            "effective_defense": "3-4",
        }
        def_staff = {
            "head_coach": {"coach_name": "Robert Saleh", "defensive_scheme": "4-3 Over", "man_coverage": "Low"},
            "effective_offense": "Erhardt-Perkins",
            "effective_defense": "Cover 3 4-3",
        }

        matchup = staff_matchup(off_staff, def_staff)
        self.assertIsNotNone(matchup)
        self.assertIn("Marcus Whitfield: Spread Option", matchup["offense"])
        self.assertIn("Robert Saleh: Cover 3 4-3 front", matchup["defense"])


class TestDatabaseCoordinatorsAndSlants(unittest.TestCase):
    """Database-backed tests using PostgreSQL transaction rollback."""

    @classmethod
    def setUpClass(cls):
        cls.db_conn_info = DatabaseHandler().db_conn_info

    def test_coach_slant_constraint(self):
        """coaches.slant must be one of 'OFFENSIVE', 'DEFENSIVE', 'CEO'."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")

                # Valid slants succeed
                cur.execute("UPDATE coaches SET slant = 'OFFENSIVE' WHERE coach_name = 'Marcus Whitfield';")
                cur.execute("UPDATE coaches SET slant = 'DEFENSIVE' WHERE coach_name = 'Marcus Whitfield';")
                cur.execute("UPDATE coaches SET slant = 'CEO' WHERE coach_name = 'Marcus Whitfield';")

                # Invalid slant raises check constraint violation
                with self.assertRaises(psycopg.errors.CheckViolation):
                    cur.execute("UPDATE coaches SET slant = 'SPECIAL_TEAMS' WHERE coach_name = 'Marcus Whitfield';")

                conn.rollback()

    def test_coordinators_table_constraints(self):
        """Test side check constraint on coordinators table."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")

                # Invalid side
                with self.assertRaises(psycopg.errors.CheckViolation):
                    cur.execute("""
                        INSERT INTO coordinators (coordinator_name, side, scheme)
                        VALUES ('Invalid Side', 'SPECIAL_TEAMS', 'Spread');
                    """)

                conn.rollback()

    def test_coordinator_tenures_constraints(self):
        """Test tenure constraints: role, end_season >= start_season, and unique tenure start."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")

                # Insert test coordinator and team
                cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES ('Test OC', 'OFFENSIVE', 'Spread') RETURNING id;")
                coord_id = cur.fetchone()[0]
                cur.execute("SELECT id FROM teams LIMIT 1;")
                team_id = cur.fetchone()[0]

                # 1. Invalid role
                with self.assertRaises(psycopg.errors.CheckViolation):
                    cur.execute("""
                        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season)
                        VALUES (%s, %s, 'HC', 2026);
                    """, (coord_id, team_id))
                conn.rollback()

        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")
                cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES ('Test OC 2', 'OFFENSIVE', 'Spread') RETURNING id;")
                coord_id = cur.fetchone()[0]
                cur.execute("SELECT id FROM teams LIMIT 1;")
                team_id = cur.fetchone()[0]

                # 2. end_season < start_season check violation
                with self.assertRaises(psycopg.errors.CheckViolation):
                    cur.execute("""
                        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                        VALUES (%s, %s, 'OC', 2030, 2028);
                    """, (coord_id, team_id))
                conn.rollback()

        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")
                cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES ('Test OC 3', 'OFFENSIVE', 'Spread') RETURNING id;")
                coord_id = cur.fetchone()[0]
                cur.execute("SELECT id FROM teams LIMIT 1;")
                team_id = cur.fetchone()[0]

                # 3. Unique constraint on (coordinator_id, team_id, role, start_season)
                cur.execute("""
                    INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season)
                    VALUES (%s, %s, 'OC', 2026);
                """, (coord_id, team_id))

                with self.assertRaises(psycopg.errors.UniqueViolation):
                    cur.execute("""
                        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season)
                        VALUES (%s, %s, 'OC', 2026);
                    """, (coord_id, team_id))

                conn.rollback()

    def test_cascade_delete_coordinator(self):
        """Deleting a coordinator cascades to remove their coordinator_tenures."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("BEGIN;")
                cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES ('Temp Coord', 'DEFENSIVE', '4-3') RETURNING id;")
                coord_id = cur.fetchone()[0]
                cur.execute("SELECT id FROM teams LIMIT 1;")
                team_id = cur.fetchone()[0]
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) VALUES (%s, %s, 'DC', 2026);", (coord_id, team_id))

                # Delete coordinator
                cur.execute("DELETE FROM coordinators WHERE id = %s;", (coord_id,))
                cur.execute("SELECT count(*) FROM coordinator_tenures WHERE coordinator_id = %s;", (coord_id,))
                self.assertEqual(cur.fetchone()[0], 0)

                conn.rollback()

    def test_seeder_all_33_coaches_slants(self):
        """All 33 baseline coaches have populated slants matching plan taxonomy."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT coach_name, slant FROM coaches ORDER BY coach_name;")
                rows = cur.fetchall()
                self.assertEqual(len(rows), 33)

                slants = dict(rows)
                # Verify key anchors from §3.1
                self.assertEqual(slants["Marcus Whitfield"], "OFFENSIVE")
                self.assertEqual(slants["Dave Canales"], "OFFENSIVE")
                self.assertEqual(slants["Sean McVay"], "OFFENSIVE")
                self.assertEqual(slants["Kyle Shanahan"], "OFFENSIVE")
                self.assertEqual(slants["DeMeco Ryans"], "DEFENSIVE")
                self.assertEqual(slants["Robert Saleh"], "DEFENSIVE")
                self.assertEqual(slants["Todd Bowles"], "DEFENSIVE")
                self.assertEqual(slants["Dan Campbell"], "CEO")
                self.assertEqual(slants["John Harbaugh"], "CEO")

    def test_carolina_narrative_continuity(self):
        """Verifies Panthers staff continuity: Brad Idzik OC (2026-2033 under Canales), Charles Grant OC (2034+ under Whitfield), Evero DC (2026+)."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM teams WHERE abbreviation = 'CAR';")
                car_id = cur.fetchone()[0]

                # Season 2030 (Canales era)
                staff_2030 = fetch_team_staff(cur, car_id, 2030)
                self.assertIsNotNone(staff_2030)
                self.assertEqual(staff_2030["head_coach"]["coach_name"], "Dave Canales")
                self.assertEqual(staff_2030["offensive_coordinator"]["name"], "Brad Idzik")
                self.assertEqual(staff_2030["defensive_coordinator"]["name"], "Ejiro Evero")

                # Season 2035 (Whitfield era)
                staff_2035 = fetch_team_staff(cur, car_id, 2035)
                self.assertIsNotNone(staff_2035)
                self.assertEqual(staff_2035["head_coach"]["coach_name"], "Marcus Whitfield")
                self.assertEqual(staff_2035["head_coach"]["slant"], "OFFENSIVE")
                self.assertEqual(staff_2035["offensive_coordinator"]["name"], "Charles Grant")
                self.assertEqual(staff_2035["defensive_coordinator"]["name"], "Ejiro Evero")

    def test_chicago_narrative_continuity(self):
        """Verifies Bears staff continuity (§4.2): Thomas Brown OC (2026-2027), Whitfield OC (2028-2033), Brett Vance OC (2034+)."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM teams WHERE abbreviation = 'CHI';")
                chi_id = cur.fetchone()[0]

                # Season 2027 (Thomas Brown first stint)
                staff_2027 = fetch_team_staff(cur, chi_id, 2027)
                self.assertIsNotNone(staff_2027)
                self.assertEqual(staff_2027["head_coach"]["coach_name"], "Ben Johnson")
                self.assertEqual(staff_2027["offensive_coordinator"]["name"], "Thomas Brown")
                self.assertEqual(staff_2027["defensive_coordinator"]["name"], "Dennis Allen")

                # Season 2030 (Marcus Whitfield OC stint)
                staff_2030 = fetch_team_staff(cur, chi_id, 2030)
                self.assertIsNotNone(staff_2030)
                self.assertEqual(staff_2030["head_coach"]["coach_name"], "Ben Johnson")
                self.assertEqual(staff_2030["offensive_coordinator"]["name"], "Marcus Whitfield")
                self.assertEqual(staff_2030["defensive_coordinator"]["name"], "Dennis Allen")

                # Season 2035 (Brett Vance era)
                staff_2035 = fetch_team_staff(cur, chi_id, 2035)
                self.assertIsNotNone(staff_2035)
                self.assertEqual(staff_2035["head_coach"]["coach_name"], "Ben Johnson")
                self.assertEqual(staff_2035["offensive_coordinator"]["name"], "Brett Vance")
                self.assertEqual(staff_2035["defensive_coordinator"]["name"], "Dennis Allen")

    def test_every_active_team_has_full_staff(self):
        """Every active franchise has exactly one active HC, one active OC, and one active DC in 2035."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, abbreviation FROM teams;")
                teams = cur.fetchall()
                for team_id, abbr in teams:
                    staff = fetch_team_staff(cur, team_id, season=2035)
                    self.assertIsNotNone(staff, f"Team {abbr} missing staff in 2035")
                    self.assertIsNotNone(staff["head_coach"], f"Team {abbr} missing HC in 2035")
                    self.assertIsNotNone(staff["offensive_coordinator"], f"Team {abbr} missing OC in 2035")
                    self.assertIsNotNone(staff["defensive_coordinator"], f"Team {abbr} missing DC in 2035")
                    self.assertIsNotNone(staff["effective_offense"])
                    self.assertIsNotNone(staff["effective_defense"])

    def test_procedural_fallback_deterministic(self):
        """Procedural coordinator generator generates non-empty plausible strings deterministically."""
        name1 = generate_procedural_name("DET", "OC")
        name2 = generate_procedural_name("DET", "OC")
        name_dc = generate_procedural_name("DET", "DC")
        self.assertEqual(name1, name2)
        self.assertNotEqual(name1, name_dc)
        self.assertGreater(len(name1.split()), 1)

    def test_seeder_idempotency(self):
        """Calling seed_all_coordinators_and_slants multiple times preserves identical counts."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                res1 = seed_all_coordinators_and_slants(cur)
                res2 = seed_all_coordinators_and_slants(cur)
                self.assertEqual(res1["slants_updated"], res2["slants_updated"])
                self.assertEqual(res1["coordinators_seeded"], res2["coordinators_seeded"])
                self.assertEqual(res1["tenures_seeded"], res2["tenures_seeded"])


if __name__ == "__main__":
    unittest.main()
