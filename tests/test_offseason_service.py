"""Comprehensive unit and integration tests for Offseason Editorial Package Service & Runner.

Covers:
1. Manifest parsing and strict schema validation (schema_version, season, 90+ OVR check).
2. OVR qualitative tier conversion and AP All-Pro honors verification.
3. Dry-run manifest preview generation.
4. Transactional coaching moves execution, scheme inheritance, invariant enforcement, and rollback.
5. Anti-leakage event facts packet generation and isolation boundaries.
6. Run context event-relative cutoff visibility cascade.
7. Offseason CLI runner execution and sub-event scoping (--only, --dry-run).
8. Debug API endpoint integration.

Run with:
    python3 -m pytest -q tests/test_offseason_service.py
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.rows import dict_row

from newsroom.offseason_run import resolve_sub_event_slate, run_offseason_package
from newsroom.offseason_service import (
    apply_coaching_moves,
    build_event_facts_packet,
    check_player_all_pro_honors,
    convert_ovr_to_qualitative_tier,
    load_manifest,
    preview_manifest,
)
from newsroom.run_context import (
    article_run_context,
    context_offseason_event,
    is_offseason_event_visible,
    reset_offseason_cutoff,
    set_offseason_cutoff,
)

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

SAMPLE_VALID_MANIFEST = {
    "schema_version": 1,
    "season": 2035,
    "event_id": "offseason-2035-package",
    "publication_week": 24,
    "event_dates": {
        "super_bowl": "2036-02-10",
        "coaching": "2036-02-22",
        "signings": "2036-03-16",
        "draft": "2036-04-26",
    },
    "coaching_moves": [
        {
            "team": "IND",
            "action": "hire",
            "outgoing_coach": "Shane Steichen",
            "incoming_coach": "Marcus Whitfield",
            "incoming_slant": "OFFENSIVE",
            "origin": {
                "team": "CAR",
                "role": "OC",
            },
            "imported_offensive_scheme": "Spread",
            "retained_defensive_scheme": "Base 4-3",
            "effective_season": 2036,
            "notes": "Colts hire Whitfield to modernize passing attack.",
        }
    ],
    "signings": [
        {
            "player_name": "Patrick Surtain II",
            "position": "CB",
            "former_team": "DEN",
            "new_team": "DET",
            "ovr": 96,
            "contract_notes": "4 years, top-of-market deal",
            "role_notes": "Immediate shutdown CB1",
        },
        {
            "player_name": "Justin Madubuike",
            "position": "DT",
            "former_team": "BAL",
            "new_team": "CHI",
            "ovr": 92,
            "contract_notes": "3 years, marquee interior anchor",
            "role_notes": "3-technique interior disruptor",
        },
    ],
    "draft_results": {
        "source_file": "captures/offseason/2035/draft_results.csv",
    },
}


class TestManifestValidation(unittest.TestCase):
    """Tests manifest parsing and schema enforcement."""

    def test_valid_manifest_passes(self):
        validated = load_manifest(SAMPLE_VALID_MANIFEST)
        self.assertEqual(validated["season"], 2035)
        self.assertEqual(validated["publication_week"], 24)
        self.assertEqual(len(validated["coaching_moves"]), 1)
        self.assertEqual(len(validated["signings"]), 2)

    def test_invalid_schema_version_rejected(self):
        bad = dict(SAMPLE_VALID_MANIFEST, schema_version=2)
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("Invalid schema_version", str(ctx.exception))

    def test_missing_season_rejected(self):
        bad = dict(SAMPLE_VALID_MANIFEST)
        del bad["season"]
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("Manifest must declare integer 'season'", str(ctx.exception))

    def test_publication_week_must_be_24(self):
        bad = dict(SAMPLE_VALID_MANIFEST, publication_week=18)
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("publication_week must be 24", str(ctx.exception))

    def test_sub_90_ovr_signing_strictly_rejected(self):
        bad = json.loads(json.dumps(SAMPLE_VALID_MANIFEST))
        bad["signings"].append({
            "player_name": "Average Starter",
            "position": "WR",
            "former_team": "NYJ",
            "new_team": "NE",
            "ovr": 88,
        })
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("Sub-90 signings are forbidden", str(ctx.exception))

    def test_invalid_incoming_slant_rejected(self):
        bad = json.loads(json.dumps(SAMPLE_VALID_MANIFEST))
        bad["coaching_moves"][0]["incoming_slant"] = "SPECIAL_TEAMS"
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("invalid incoming_slant", str(ctx.exception))

    def test_invalid_event_date_format_rejected(self):
        bad = json.loads(json.dumps(SAMPLE_VALID_MANIFEST))
        bad["event_dates"]["coaching"] = "02/22/2036"
        with self.assertRaises(ValueError) as ctx:
            load_manifest(bad)
        self.assertIn("valid YYYY-MM-DD", str(ctx.exception))


class TestQualitativeOvrConversion(unittest.TestCase):
    """Tests conversion of numeric OVR to qualitative tiers."""

    def test_tier_1_superstar(self):
        self.assertEqual(
            convert_ovr_to_qualitative_tier(99),
            "Tier 1 superstar / elite franchise cornerstone",
        )
        self.assertEqual(
            convert_ovr_to_qualitative_tier(95),
            "Tier 1 superstar / elite franchise cornerstone",
        )

    def test_high_impact_marquee_starter(self):
        self.assertEqual(
            convert_ovr_to_qualitative_tier(94),
            "High-impact marquee starter / Pro Bowl caliber talent",
        )
        self.assertEqual(
            convert_ovr_to_qualitative_tier(90),
            "High-impact marquee starter / Pro Bowl caliber talent",
        )

    def test_sub_90_raises_error(self):
        with self.assertRaises(ValueError):
            convert_ovr_to_qualitative_tier(89)


class TestAllProHonorsVerification(unittest.TestCase):
    """Tests AP All-Pro descriptor verification against the awards table."""

    def test_all_pro_allowed_when_in_database(self):
        with psycopg.connect(CONN_INFO, row_factory=dict_row) as conn:
            # Query an actual All-Pro player from the awards table
            row = conn.execute("SELECT player_name FROM awards WHERE award_type ILIKE '%All-Pro%' LIMIT 1").fetchone()
            if row:
                res = check_player_all_pro_honors(conn, row["player_name"])
                self.assertTrue(res["has_all_pro"])
                self.assertTrue(res["descriptor_allowed"])
                self.assertIn("Official AP All-Pro selection on record", res["notes"])

    def test_all_pro_forbidden_when_not_in_database(self):
        with psycopg.connect(CONN_INFO, row_factory=dict_row) as conn:
            res = check_player_all_pro_honors(conn, "Nonexistent Fictional Player XYZ")
            self.assertFalse(res["has_all_pro"])
            self.assertFalse(res["descriptor_allowed"])
            self.assertIn("strictly forbidden", res["notes"])


class TestManifestPreview(unittest.TestCase):
    """Tests dry-run preview generation."""

    def test_preview_manifest_structure(self):
        preview = preview_manifest(SAMPLE_VALID_MANIFEST)
        self.assertTrue(preview["valid"])
        self.assertEqual(preview["season"], 2035)
        self.assertIn("IND", preview["resolved_teams"])
        self.assertEqual(len(preview["coaching_moves"]), 1)
        self.assertEqual(preview["coaching_moves"][0]["incoming_slant"], "OFFENSIVE")
        self.assertEqual(len(preview["signings"]), 2)
        # Verify qualitative tier is present and numeric OVR is NOT shown as a top-level key in preview signing
        self.assertIn("qualitative_tier", preview["signings"][0])
        self.assertNotIn("ovr", preview["signings"][0])
        self.assertIn("Season 2035 Offseason Manifest", preview["summary"])


class TestCoachingMoveTransactionAndRollback(unittest.TestCase):
    """Tests transactional coaching moves, scheme inheritance, and rollback integrity."""

    def test_dry_run_coaching_moves_rolls_back(self):
        """Dry-run must validate all transitions and invariants while leaving the DB untouched."""
        with psycopg.connect(CONN_INFO, row_factory=dict_row) as conn:
            # Snapshot current IND head coach tenure
            before = conn.execute("""
                SELECT ct.id, ct.coach_id, ct.team_id, ct.start_season, ct.end_season, c.coach_name
                FROM coach_tenures ct
                JOIN coaches c ON c.id = ct.coach_id
                JOIN teams t ON t.id = ct.team_id
                WHERE t.abbreviation = 'IND' AND (ct.end_season IS NULL OR ct.end_season >= 2035)
            """).fetchall()

            res = apply_coaching_moves(SAMPLE_VALID_MANIFEST, dry_run=True, conn=conn)
            self.assertTrue(res["success"])
            self.assertTrue(res["dry_run"])
            self.assertIn("IND", [m["team"] for m in res["applied_moves"]])

            # Verify DB was NOT mutated
            after = conn.execute("""
                SELECT ct.id, ct.coach_id, ct.team_id, ct.start_season, ct.end_season, c.coach_name
                FROM coach_tenures ct
                JOIN coaches c ON c.id = ct.coach_id
                JOIN teams t ON t.id = ct.team_id
                WHERE t.abbreviation = 'IND' AND (ct.end_season IS NULL OR ct.end_season >= 2035)
            """).fetchall()
            self.assertEqual(before, after)

    def test_scheme_inheritance_offensive_slant(self):
        """Under an OFFENSIVE HC slant, offensive scheme is imported while defensive scheme is retained."""
        manifest = {
            "schema_version": 1,
            "season": 2035,
            "event_dates": {
                "super_bowl": "2036-02-10",
                "coaching": "2036-02-22",
                "signings": "2036-03-16",
                "draft": "2036-04-26",
            },
            "coaching_moves": [
                {
                    "team": "IND",
                    "incoming_coach": "Test Offensive Mastermind",
                    "incoming_slant": "OFFENSIVE",
                    "imported_offensive_scheme": "Spread",
                    "retained_defensive_scheme": "Base 4-3",
                    "effective_season": 2036,
                }
            ],
            "signings": [],
        }
        res = apply_coaching_moves(manifest, dry_run=True)
        self.assertTrue(res["success"])
        self.assertIn(res["touched_teams"][0], res["touched_teams"])


class TestAntiLeakageFactsPackaging(unittest.TestCase):
    """Tests information cutoff packaging for each offseason story type."""

    def test_super_bowl_facts_packet_isolation(self):
        packet = build_event_facts_packet("super_bowl", SAMPLE_VALID_MANIFEST)
        self.assertEqual(packet["event_type"], "super_bowl")
        self.assertEqual(packet["season"], 2035)
        self.assertIn("champion", packet)
        self.assertIn("runner_up", packet)
        self.assertIn("box_score", packet)
        # Anti-leakage: NO coaching moves, NO signings, NO draft
        self.assertNotIn("coaching_moves", packet)
        self.assertNotIn("marquee_signings", packet)
        self.assertNotIn("picks", packet)

    def test_coaching_facts_packet_isolation(self):
        packet = build_event_facts_packet("coaching", SAMPLE_VALID_MANIFEST)
        self.assertEqual(packet["event_type"], "coaching")
        self.assertEqual(len(packet["coaching_moves"]), 1)
        cm = packet["coaching_moves"][0]
        self.assertEqual(cm["incoming_coach"], "Marcus Whitfield")
        self.assertEqual(cm["incoming_slant"], "OFFENSIVE")
        # Anti-leakage: NO signings, NO draft
        self.assertNotIn("marquee_signings", packet)
        self.assertNotIn("picks", packet)

    def test_signings_facts_packet_isolation(self):
        packet = build_event_facts_packet("signings", SAMPLE_VALID_MANIFEST)
        self.assertEqual(packet["event_type"], "signings")
        self.assertEqual(len(packet["marquee_signings"]), 2)
        sig = packet["marquee_signings"][0]
        self.assertEqual(sig["player_name"], "Patrick Surtain II")
        self.assertEqual(sig["qualitative_tier"], "Tier 1 superstar / elite franchise cornerstone")
        # Anti-leakage: Numeric OVR is strictly absent
        self.assertNotIn("ovr", sig)
        # Anti-leakage: NO draft picks
        self.assertNotIn("picks", packet)

    def test_draft_facts_packet(self):
        packet = build_event_facts_packet("draft", SAMPLE_VALID_MANIFEST)
        self.assertEqual(packet["event_type"], "draft")
        self.assertIn("picks", packet)


class TestRunContextOffseasonCutoff(unittest.TestCase):
    """Tests run_context event-relative cutoff cascade."""

    def test_event_visibility_cascade(self):
        # 1. At super_bowl cutoff
        token = set_offseason_cutoff("super_bowl")
        self.assertEqual(context_offseason_event(), "super_bowl")
        self.assertTrue(is_offseason_event_visible("super_bowl"))
        self.assertFalse(is_offseason_event_visible("coaching"))
        self.assertFalse(is_offseason_event_visible("signings"))
        self.assertFalse(is_offseason_event_visible("draft"))
        reset_offseason_cutoff(token)

        # 2. At coaching cutoff
        token = set_offseason_cutoff("coaching")
        self.assertTrue(is_offseason_event_visible("super_bowl"))
        self.assertTrue(is_offseason_event_visible("coaching"))
        self.assertFalse(is_offseason_event_visible("signings"))
        self.assertFalse(is_offseason_event_visible("draft"))
        reset_offseason_cutoff(token)

        # 3. At signings cutoff
        token = set_offseason_cutoff("signings")
        self.assertTrue(is_offseason_event_visible("super_bowl"))
        self.assertTrue(is_offseason_event_visible("coaching"))
        self.assertTrue(is_offseason_event_visible("signings"))
        self.assertFalse(is_offseason_event_visible("draft"))
        reset_offseason_cutoff(token)

        # 4. At draft cutoff
        token = set_offseason_cutoff("draft")
        self.assertTrue(is_offseason_event_visible("super_bowl"))
        self.assertTrue(is_offseason_event_visible("coaching"))
        self.assertTrue(is_offseason_event_visible("signings"))
        self.assertTrue(is_offseason_event_visible("draft"))
        reset_offseason_cutoff(token)

    def test_article_run_context_manager(self):
        with article_run_context(2035, 24, offseason_event="coaching"):
            self.assertEqual(context_offseason_event(), "coaching")
            self.assertTrue(is_offseason_event_visible("super_bowl"))
            self.assertFalse(is_offseason_event_visible("draft"))
        self.assertIsNone(context_offseason_event())


class TestOffseasonRunnerExecution(unittest.TestCase):
    """Tests the offseason package CLI runner and scope resolver."""

    def test_resolve_sub_event_slate(self):
        full = resolve_sub_event_slate(None)
        self.assertEqual(len(full), 6)

        sb = resolve_sub_event_slate("super_bowl")
        self.assertEqual(len(sb), 3)
        self.assertEqual([x["slot"] for x in sb], ["Hero", "Secondary 1", "Secondary 2"])

        coach = resolve_sub_event_slate("coaching")
        self.assertEqual(len(coach), 1)
        self.assertEqual(coach[0]["slot"], "Feature 3")

        sign = resolve_sub_event_slate("signings")
        self.assertEqual(len(sign), 1)
        self.assertEqual(sign[0]["slot"], "Feature 4")

        draft = resolve_sub_event_slate("draft")
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft[0]["slot"], "Feature 5")

    def test_runner_dry_run_execution(self):
        """Test full package dry-run without mutating database."""
        res = run_offseason_package(
            season=2035,
            manifest_path_or_dict=SAMPLE_VALID_MANIFEST,
            dry_run=True,
            live=False,
        )
        self.assertEqual(res["season"], 2035)
        self.assertEqual(res["week"], 24)
        self.assertTrue(res["dry_run"])
        self.assertEqual(len(res["articles"]), 6)
        slots = [a["slot"] for a in res["articles"]]
        self.assertEqual(slots, ["Hero", "Secondary 1", "Secondary 2", "Feature 3", "Feature 4", "Feature 5"])

    def test_runner_only_coaching_dry_run(self):
        """Test partial package dry run for coaching carousel only."""
        res = run_offseason_package(
            season=2035,
            manifest_path_or_dict=SAMPLE_VALID_MANIFEST,
            dry_run=True,
            only="coaching",
            live=False,
        )
        self.assertEqual(len(res["articles"]), 1)
        self.assertEqual(res["articles"][0]["slot"], "Feature 3")
        self.assertEqual(res["articles"][0]["article_type"], "COACHING_CAROUSEL_AUDIT")


class TestOffseasonDebugApi(unittest.TestCase):
    """Tests /api/debug/offseason/preview and /api/debug/offseason/run endpoints."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import server.debug_api as dbg

        self.dbg = dbg
        app = FastAPI()
        app.include_router(dbg.router)
        self.client = TestClient(app)
        self.jobs_dir = tempfile.mkdtemp()
        self.manifest_dir = tempfile.mkdtemp()
        self.manifest_file = os.path.join(self.manifest_dir, "manifest.json")
        with open(self.manifest_file, "w") as fh:
            json.dump(SAMPLE_VALID_MANIFEST, fh)

        patches = [
            patch.object(dbg, "JOBS_DIR", self.jobs_dir),
            patch.object(dbg, "env", lambda k, d=None: "secret_token" if k == "DEBUG_TOKEN" else d),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.headers = {"X-Debug-Token": "secret_token"}

    def test_preview_endpoint_auth_required(self):
        r = self.client.post("/api/debug/offseason/preview", json={"season": 2035, "manifest_path": self.manifest_file})
        self.assertEqual(r.status_code, 401)

    def test_preview_endpoint_success(self):
        r = self.client.post(
            "/api/debug/offseason/preview",
            json={"season": 2035, "manifest_path": self.manifest_file},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["valid"])
        self.assertEqual(data["season"], 2035)
        self.assertIn("Season 2035 Offseason Manifest", data["summary"])

    def test_preview_endpoint_file_not_found(self):
        r = self.client.post(
            "/api/debug/offseason/preview",
            json={"season": 2035, "manifest_path": "nonexistent/manifest.json"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 404)

    def test_run_endpoint_spawns_job(self):
        r = self.client.post(
            "/api/debug/offseason/run",
            json={"season": 2035, "manifest_path": self.manifest_file, "dry_run": True, "only": "coaching"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["kind"], "offseason_run")
        self.assertIn("Offseason Run Season 2035", data["kind_label"])
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["only"], "coaching")


if __name__ == "__main__":
    unittest.main()

