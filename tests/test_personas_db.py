"""Unit tests for Phase 2 and 3 Persona Systems (tests/test_personas_db.py).

Covers:
1. Trait catalogue validation (budgets, conflicts, roles).
2. Reproducible seeding (deterministic sliders and trait allocation).
3. Persona prompt rendering from DB tables vs JSON.
4. Debug API persona read and update endpoints (/api/debug/personas).
5. GM persona prompt and dialogue enhancement without altering trade engine math.
"""
import copy
import os
import unittest
from unittest import mock

import pytest
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engines import gm_negotiation as gn
from newsroom import personas as P
import server.debug_api as dbg

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden_test user=postgres password=postgres")


class TraitCatalogueValidationTests(unittest.TestCase):
    def setUp(self):
        self.traits = P.load_traits()

    def test_default_catalogue_validates(self):
        self.assertTrue(P.validate_trait_catalogue(self.traits))

    def test_invalid_category_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["category"] = "unsupported_cat"
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("invalid category", str(ctx.exception))

    def test_invalid_role_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["roles"]["alien_role"] = 3
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("unknown role", str(ctx.exception))

    def test_negative_role_weight_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["roles"]["gm"] = -1
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("weight must be non-negative", str(ctx.exception))

    def test_prompt_not_tends_to_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["prompt"] = "Always argues with coaches"
        bad["FILM_JUNKIE"]["prompt_text"] = "Always argues with coaches"
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("must start with 'tends to'", str(ctx.exception))

    def test_asymmetric_conflict_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["conflicts"] = ["SCHEME_NERD"]
        # SCHEME_NERD does not list FILM_JUNKIE
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("Conflict asymmetry", str(ctx.exception))

    def test_self_conflict_raises(self):
        bad = copy.deepcopy(self.traits)
        bad["FILM_JUNKIE"]["conflicts"] = ["FILM_JUNKIE"]
        with self.assertRaises(ValueError) as ctx:
            P.validate_trait_catalogue(bad)
        self.assertIn("cannot conflict with itself", str(ctx.exception))


class SliderBudgetValidationTests(unittest.TestCase):
    def test_valid_slider_budget(self):
        sliders = {"temperament": 5, "sociability": 5, "confidence": 5, "openness": 5, "warmth": 5, "drive": 5}
        self.assertTrue(P.validate_slider_budget(sliders))

    def test_invalid_sum_raises(self):
        sliders = {"temperament": 6, "sociability": 5, "confidence": 5, "openness": 5, "warmth": 5, "drive": 5}
        with self.assertRaises(ValueError) as ctx:
            P.validate_slider_budget(sliders)
        self.assertIn("does not match budget 30", str(ctx.exception))

    def test_out_of_bounds_value_raises(self):
        sliders = {"temperament": 11, "sociability": -1, "confidence": 5, "openness": 5, "warmth": 5, "drive": 5}
        with self.assertRaises(ValueError) as ctx:
            P.validate_slider_budget(sliders)
        self.assertIn("out of bounds", str(ctx.exception))

    def test_missing_slider_raises(self):
        sliders = {"temperament": 10, "sociability": 10, "confidence": 10}
        with self.assertRaises(ValueError) as ctx:
            P.validate_slider_budget(sliders)
        self.assertIn("Missing sliders", str(ctx.exception))


class PersonaTraitsValidationTests(unittest.TestCase):
    def setUp(self):
        self.traits = P.load_traits()

    def test_valid_traits_pass(self):
        chosen = ["ANALYTICS_BELIEVER", "SCHEME_NERD", "FILM_ROOM_RAT", "QUOTABLE"]
        self.assertTrue(P.validate_persona_traits(chosen, self.traits))

    def test_too_few_or_too_many_traits_raises(self):
        with self.assertRaises(ValueError) as ctx:
            P.validate_persona_traits(["ANALYTICS_BELIEVER", "SCHEME_NERD"], self.traits)
        self.assertIn("between 3 and 5 traits", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            P.validate_persona_traits(["ANALYTICS_BELIEVER", "SCHEME_NERD", "FILM_ROOM_RAT", "QUOTABLE", "LOYAL", "FOODIE"], self.traits)
        self.assertIn("between 3 and 5 traits", str(ctx.exception))

    def test_duplicate_traits_raises(self):
        with self.assertRaises(ValueError) as ctx:
            P.validate_persona_traits(["ANALYTICS_BELIEVER", "ANALYTICS_BELIEVER", "QUOTABLE"], self.traits)
        self.assertIn("Duplicate traits", str(ctx.exception))

    def test_more_than_two_in_category_raises(self):
        with self.assertRaises(ValueError) as ctx:
            P.validate_persona_traits(["ANALYTICS_BELIEVER", "SCHEME_NERD", "HISTORIAN", "QUOTABLE"], self.traits)
        self.assertIn("Too many traits from category", str(ctx.exception))

    def test_conflicting_traits_raise(self):
        with self.assertRaises(ValueError) as ctx:
            P.validate_persona_traits(["ANALYTICS_BELIEVER", "OLD_SCHOOL", "QUOTABLE"], self.traits)
        self.assertIn("conflicts with", str(ctx.exception))


class ReproducibleSeedingTests(unittest.TestCase):
    def test_slider_seeding_is_deterministic(self):
        s1 = P.seed_sliders("gm", "CAR")
        s2 = P.seed_sliders("gm", "CAR")
        self.assertEqual(s1, s2)
        self.assertTrue(P.validate_slider_budget(s1))

    def test_traits_seeding_is_deterministic(self):
        t1 = P.seed_traits("gm", "CAR", count=4)
        t2 = P.seed_traits("gm", "CAR", count=4)
        self.assertEqual(t1, t2)
        self.assertTrue(P.validate_persona_traits(t1))

    def test_different_agents_produce_spread(self):
        results = [P.seed_sliders("gm", str(i)) for i in range(16)]
        unique_tuples = {tuple(r[s] for s in P.SLIDERS) for r in results}
        self.assertGreater(len(unique_tuples), 5, "Seeding produced too many identical slider sets")

        traits_sets = [tuple(sorted(P.seed_traits("gm", str(i), count=4))) for i in range(16)]
        unique_traits = set(traits_sets)
        self.assertGreater(len(unique_traits), 5, "Seeding produced too many identical trait sets")


class MockPromptRenderingTests(unittest.TestCase):
    def test_render_persona_mock_db_structure(self):
        mock_persona = {
            "voice": "You are a sharp, numbers-driven columnist.",
            "sliders": {"temperament": 5, "sociability": 5, "confidence": 5, "openness": 5, "warmth": 5, "drive": 5},
            "traits": ["ANALYTICS_BELIEVER", "MEASURED"],
            "views": [{"text": "Point differential reflects true strength.", "when": ["GAME_REVIEW"]}],
            "signatures": [{"text": "a look at net EPA per play", "detect": "net EPA"}],
            "want": "to cut through public hype",
            "fear": "falling for small-sample noise",
            "formats": {"ARTICLE": "Argument-driven with clear analytical evidence."}
        }
        rendered = P.render_persona(mock_persona, fmt="ARTICLE", tags={"GAME_REVIEW"}, seed="test_seed_42")
        self.assertIn("You are a sharp, numbers-driven columnist.", rendered)
        self.assertIn("Analytics believer: tends to trust point differential", rendered)
        self.assertIn("Point differential reflects true strength.", rendered)
        self.assertIn("HOW YOU WRITE A COLUMN:", rendered)
        self.assertIn("IN THE BACKGROUND", rendered)

    def test_fallback_when_db_cursor_is_none(self):
        p_fallback = P.load_personas(cur=None)
        self.assertIn("BARNWELL", p_fallback)
        self.assertEqual(p_fallback["BARNWELL"]["sliders"]["openness"], 8)


@pytest.mark.integration
class PromptRenderingDbVsJsonIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            with psycopg.connect(CONN_INFO) as conn:
                with conn.cursor() as cur:
                    P.seed_writers_to_db(cur)
                    P.seed_gms_to_db(cur)
                    conn.commit()
        except Exception as e:
            self.skipTest(f"Database setup failed: {e}")

    def test_prompt_identical_db_vs_json(self):
        with psycopg.connect(CONN_INFO) as conn:
            with conn.cursor() as cur:
                db_personas = P.load_personas(cur=cur)

        json_personas = P.load_personas_json()
        self.assertEqual(set(db_personas.keys()), set(json_personas.keys()))

        for k in ("ESPN", "THE_ATHLETIC", "SCHEFTER", "BARNWELL", "KIMES"):
            p_json = json_personas[k]
            p_db = db_personas[k]
            prompt_json = P.render_persona(p_json, fmt="ARTICLE", seed="seed_check_42")
            prompt_db = P.render_persona(p_db, fmt="ARTICLE", seed="seed_check_42")
            self.assertEqual(prompt_json, prompt_db, f"Prompt mismatch for writer {k}")


class DebugApiUnitTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dbg.router)
        self.client = TestClient(app)
        self.token_patch = mock.patch.object(dbg, "env", lambda k, d=None: "debug_secret_tok" if k == "DEBUG_TOKEN" else d)
        self.token_patch.start()
        self.headers = {"X-Debug-Token": "debug_secret_tok"}

    def tearDown(self):
        self.token_patch.stop()

    def test_post_persona_token_auth(self):
        r_no_tok = self.client.post("/api/debug/personas/writer/ESPN", json={"notes": "test"})
        self.assertEqual(r_no_tok.status_code, 401)

        r_wrong_tok = self.client.post("/api/debug/personas/writer/ESPN", json={"notes": "test"},
                                       headers={"X-Debug-Token": "bad_token"})
        self.assertEqual(r_wrong_tok.status_code, 401)

    def test_post_persona_validates_budget(self):
        bad_sliders = {"temperament": 10, "sociability": 10, "confidence": 10,
                       "openness": 5, "warmth": 5, "drive": 5}
        r = self.client.post("/api/debug/personas/writer/ESPN", json={"sliders": bad_sliders}, headers=self.headers)
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not match budget 30", r.json()["detail"])

    def test_post_persona_validates_trait_conflicts(self):
        bad_traits = ["ANALYTICS_BELIEVER", "OLD_SCHOOL", "QUOTABLE"]
        r = self.client.post("/api/debug/personas/writer/ESPN", json={"traits": bad_traits}, headers=self.headers)
        self.assertEqual(r.status_code, 400)
        self.assertIn("conflicts with", r.json()["detail"])


@pytest.mark.integration
class DebugApiEndpointsIntegrationTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dbg.router)
        self.client = TestClient(app)
        self.token_patch = mock.patch.object(dbg, "env", lambda k, d=None: "debug_secret_tok" if k == "DEBUG_TOKEN" else d)
        self.token_patch.start()
        self.headers = {"X-Debug-Token": "debug_secret_tok"}
        # Ensure DB has seeded personas
        try:
            with psycopg.connect(CONN_INFO) as conn:
                with conn.cursor() as cur:
                    P.seed_writers_to_db(cur)
                    P.seed_gms_to_db(cur)
                    conn.commit()
        except Exception as e:
            self.skipTest(f"Database setup failed: {e}")

    def tearDown(self):
        self.token_patch.stop()

    def test_get_personas_endpoint(self):
        res = self.client.get("/api/debug/personas")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("personas", data)
        self.assertIn("count", data)

        res_writers = self.client.get("/api/debug/personas?type=writer")
        self.assertEqual(res_writers.status_code, 200)
        for p in res_writers.json()["personas"]:
            self.assertEqual(p["persona_type"], "writer")

    def test_post_and_get_persona_roundtrip(self):
        target_id = "THE_ATHLETIC"
        orig = self.client.get(f"/api/debug/personas?type=writer&id={target_id}").json()["personas"][0]

        new_sliders = {"temperament": 4, "sociability": 4, "confidence": 5,
                       "openness": 6, "warmth": 5, "drive": 6}
        r = self.client.post(f"/api/debug/personas/writer/{target_id}", json={"sliders": new_sliders}, headers=self.headers)
        self.assertEqual(r.status_code, 200)

        updated = self.client.get(f"/api/debug/personas?type=writer&id={target_id}").json()["personas"][0]
        self.assertEqual(updated["sliders"], new_sliders)

        r_restore = self.client.post(f"/api/debug/personas/writer/{target_id}", json={"sliders": orig["sliders"]}, headers=self.headers)
        self.assertEqual(r_restore.status_code, 200)


class GmPersonaPromptEnhancementTests(unittest.TestCase):
    def setUp(self):
        self.b = {
            "teams": {
                1: {"abbr": "CAR", "city": "Carolina", "name": "Panthers"},
                2: {"abbr": "KC", "city": "Kansas City", "name": "Chiefs"}
            },
            "gm": {
                2: {
                    "gm_id": 2,
                    "gm_name": "Brett Veach",
                    "voice": "decisive and aggressive",
                    "aggressiveness": 0.85,
                    "sliders": {
                        "temperament": 8, "sociability": 7, "confidence": 9,
                        "openness": 3, "warmth": 1, "drive": 2
                    },
                    "traits": ["CAP_MIND", "RING_CHASER"]
                }
            }
        }
        self.offer = []
        self.counter = []
        self.sheet = {}

    def test_gm_message_prompt_includes_sliders_and_traits(self):
        prompt = gn.message_prompt(self.b, "ACCEPT", 1, 2, self.offer, self.counter, None, (), None, self.sheet)
        self.assertIn("Disposition:", prompt)
        self.assertIn("volatile and quick to boil over", prompt)
        self.assertIn("TENDENCIES", prompt)
        self.assertIn("Asset manager", prompt)
        self.assertIn("Ring chaser", prompt)

    def test_gm_template_message_reflects_aggressiveness(self):
        msg = gn.template_message(self.b, "ACCEPT", 1, 2, self.offer, self.counter)
        self.assertIn("Deal. Send the paperwork over and let's get it filed right away", msg)

    def test_gm_template_message_reflects_loyalty(self):
        loyal_b = copy.deepcopy(self.b)
        loyal_b["gm"][2]["traits"] = ["LOYAL", "OLD_SCHOOL"]
        msg = gn.template_message(loyal_b, "ACCEPT", 1, 2, self.offer, self.counter)
        self.assertIn("we take care of our guys and this fits what we're building", msg)

    def test_gm_template_message_reflects_patience_or_walk_away(self):
        patient_b = copy.deepcopy(self.b)
        patient_b["gm"][2]["aggressiveness"] = 0.25
        patient_b["gm"][2]["traits"] = ["MEASURED", "GUARDED"]
        msg_walk = gn.template_message(patient_b, "WALK_AWAY", 1, 2, self.offer, self.counter)
        self.assertIn("We've been patient here, but we've gone back and forth enough", msg_walk)


class _RollbackTest(Exception):
    pass


@pytest.mark.integration
class PersonaTablesMissingTests(unittest.TestCase):
    """Verifies that missing persona tables do not abort transactions (R1 review fix)."""

    def test_missing_tables_fallback_and_no_transaction_abort(self):
        from engines import gm_trade_engine as g
        try:
            conn = psycopg.connect(CONN_INFO)
        except Exception:
            self.skipTest("madden_test DB unavailable")

        with conn.cursor() as cur:
            try:
                with cur.connection.transaction():
                    # Drop tables in a savepoint/transaction to simulate pre-migration database
                    cur.execute("""
                        DROP TABLE IF EXISTS persona_trait_history CASCADE;
                        DROP TABLE IF EXISTS persona_relationships CASCADE;
                        DROP TABLE IF EXISTS persona_views CASCADE;
                        DROP TABLE IF EXISTS persona_traits CASCADE;
                        DROP TABLE IF EXISTS persona_sliders CASCADE;
                    """)
                    self.assertFalse(P.persona_tables_exist(cur))
                    self.assertFalse(P.persona_tables_exist(cur, views=True))
                    self.assertFalse(P.persona_tables_exist(cur, views=True, relationships=True))

                    # read_books must succeed and return seeded personas without aborting transaction
                    b = g.read_books(cur, 2036, 22)
                    self.assertGreater(len(b["gm"]), 0)
                    first_gm = next(iter(b["gm"].values()))
                    self.assertIn("sliders", first_gm)
                    self.assertIn("traits", first_gm)
                    self.assertEqual(sum(first_gm["sliders"].values()), 30)

                    # Subsequent query in same transaction must succeed
                    cur.execute("SELECT 1")
                    val = cur.fetchone()
                    self.assertEqual(val[0] if isinstance(val, (tuple, list)) else next(iter(val.values())), 1)

                    # get_persona must fall back to seeded persona without error
                    gm_p = P.get_persona("gm", "1", cur=cur)
                    self.assertEqual(sum(gm_p["sliders"].values()), 30)
                    cur.execute("SELECT 1")
                    val = cur.fetchone()
                    self.assertEqual(val[0] if isinstance(val, (tuple, list)) else next(iter(val.values())), 1)

                    # load_personas must return JSON personas without error
                    all_writers = P.load_personas(cur=cur)
                    self.assertIn("LOCAL_BEAT_REPORTER", all_writers)
                    cur.execute("SELECT 1")
                    val = cur.fetchone()
                    self.assertEqual(val[0] if isinstance(val, (tuple, list)) else next(iter(val.values())), 1)

                    raise _RollbackTest()
            except _RollbackTest:
                pass

            # Verify tables exist again after rollback
            self.assertTrue(P.persona_tables_exist(cur))
        conn.close()

    def test_undefined_table_mock_cursor_recovers_without_abort(self):
        from engines import gm_trade_engine as g

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()
        mock_cur.connection = mock_conn

        # Set up savepoint context manager
        savepoint_cm = mock.MagicMock()
        mock_conn.transaction.return_value = savepoint_cm

        def fake_execute(query, params=None):
            if "persona_sliders" in query or "persona_traits" in query:
                raise psycopg.errors.UndefinedTable("relation does not exist")
            return None

        mock_cur.execute.side_effect = fake_execute

        # With persona_tables_exist returning True, query attempts inside savepoint and fails gracefully
        with mock.patch("newsroom.personas.persona_tables_exist", return_value=True):
            with mock.patch.object(g, "value_season", return_value=2036):
                with mock.patch.object(g, "read_cap", return_value={}):
                    with mock.patch.object(g, "read_proposals", return_value=[]):
                        with mock.patch("engines.gm_direction.read_injuries", return_value={}):
                            with mock.patch("engines.trade_value.read_players", return_value={}):
                                fetch_queue = [
                                    [(1, "CAR", "Carolina", "Panthers", "NFC", "South")],  # teams
                                    [(1, 10, "Scott Fitterer", 0.5, 0.5, 0.5, 0.5, {}, "standard", False, True)],  # gm_profiles
                                ]
                                mock_cur.fetchall.side_effect = lambda: fetch_queue.pop(0) if fetch_queue else []
                                mock_cur.fetchone.return_value = (0, 0, 20)
                                books = g.read_books(mock_cur, 2036, 22)
                                self.assertIn(1, books["gm"])
                                gm_entry = books["gm"][1]
                                self.assertIn("sliders", gm_entry)
                                self.assertEqual(sum(gm_entry["sliders"].values()), 30)
                                # Savepoint transaction context manager was entered
                                savepoint_cm.__enter__.assert_called()


if __name__ == "__main__":
    unittest.main()
