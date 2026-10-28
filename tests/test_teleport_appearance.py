"""pipeline/teleport_appearance.py: headshot traits -> generic head / body type (unit, no database), the teleporter
hook, and the sample save teleported with appearance and applied to a copy (integration)."""
import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter

import psycopg
import pytest

from engines import ovr_weights as ow
from headshots import headshot_appearance_generator as hag
from pipeline import teleport as tp
from pipeline import teleport_appearance as ta
from tests.test_teleport import QB_RATINGS, READER, SAMPLE_SAVE, WRITER, _sample_ready

HEADS = ["gen_1_B_N_01", "gen_2_H_S_005", "gen_2_B_B_004", "gen_3_B_N_004", "gen_4_M_G_01", "gen_5_T_BD_02",
         "gen_6_BMH_N_003", "gen_7_T_N_009", "gen_7_B_G_004", "gen_7_M_S_011", "gen_1_MorphHead"]
SNAPSHOT = {"players": [{"appearance": {"generic_head": h}} for h in HEADS] + [{"appearance": {"generic_head": ""}}]}
SEED = {"name": "A. Manning", "team": "Dolphins", "position": "QB", "source": "batch"}


class TraitsTest(unittest.TestCase):
    def test_traits_are_the_generators(self):
        _, meta = hag.appearance("A. Manning", "Dolphins", "QB", 70, 300, 25)     # age only adds a hairline note
        t = ta.traits(SEED)
        self.assertEqual((t["tone"], t["hair"], t["facial"]), (meta["tone"], meta["hair"], meta["facial"]))
        self.assertEqual(ta.traits(SEED), t)

    def test_hair_kind(self):
        self.assertEqual(ta.hair_kind("shaved bald head"), "bald")
        self.assertEqual(ta.hair_kind("shoulder-length locs (black hair)"), "locs")
        self.assertEqual(ta.hair_kind("cornrows (black hair), slightly receding hairline"), "locs")
        self.assertEqual(ta.hair_kind("shoulder-length wavy hair (blond hair)"), "long")
        self.assertEqual(ta.hair_kind("buzz cut (dark brown hair)"), "short")

    def test_body_type_follows_the_build_line(self):
        self.assertEqual(ta.body_type(74, 185), "Thin")         # BMI 23.8
        self.assertEqual(ta.body_type(74, 215), "Standard")     # 27.6
        self.assertEqual(ta.body_type(74, 245), "Muscular")     # 31.5
        self.assertEqual(ta.body_type(77, 320), "Heavy")        # 37.9
        self.assertIsNone(ta.body_type(None, 200))

    def test_every_tone_and_facial_is_mapped(self):
        self.assertEqual(set(ta.TONE_DIGITS), set(hag.TONES))
        self.assertEqual(set(ta.FACIAL_CODES), {f for f, _ in hag.FACIAL})


class HeadTest(unittest.TestCase):
    pool = ta.head_pool(SNAPSHOT)

    def test_pool_parses_heads_and_skips_others(self):
        self.assertEqual(len(self.pool), 10)
        self.assertIn(("gen_6_BMH_N_003", "6", frozenset("BMH"), "N"), self.pool)

    def test_the_head_matches_tone_facial_and_hair(self):
        t = {"tone": "deep brown", "facial": "clean-shaven", "hair": "short twists (black hair)"}
        self.assertEqual(ta.pick_head(self.pool, t, SEED), ("gen_7_T_N_009", 3))
        t = {"tone": "deep brown", "facial": "light stubble", "hair": "short twists (black hair)"}
        self.assertEqual(ta.pick_head(self.pool, t, SEED), ("gen_7_M_S_011", 2))       # hair filter dropped
        t = {"tone": "light-olive", "facial": "full beard", "hair": "buzz cut (black hair)"}
        self.assertEqual(ta.pick_head(self.pool, t, SEED), ("gen_3_B_N_004", 1))       # tone only
        self.assertEqual(ta.pick_head([], t, SEED), (None, 0))

    def test_choice_is_deterministic_and_spread(self):
        t = {"tone": "light", "facial": "clean-shaven", "hair": "buzz cut (blond hair)"}
        pool = [(f"gen_1_B_N_{i:02d}", "1", frozenset("B"), "N") for i in range(20)]
        picks = [ta.pick_head(pool, t, {"name": f"P{i}", "team": "Bills"})[0] for i in range(60)]
        self.assertEqual(picks, [ta.pick_head(pool, t, {"name": f"P{i}", "team": "Bills"})[0] for i in range(60)])
        self.assertGreater(len(set(picks)), 10)

    def test_fields(self):
        set_, info = ta.appearance_fields(self.pool, SEED, "Alex", "Manning-Smith", 4242, 75, 250)
        self.assertEqual(set_["PLYR_GENERICHEAD"], set_["GenericHeadAssetName"][len("gen_"):])
        self.assertIn(set_["GenericHeadAssetName"][4], ta.TONE_DIGITS[info["tone"]])
        self.assertEqual(set_["PLYR_ASSETNAME"], "ManningSmithAlex_4242")
        self.assertEqual(set_["CharacterBodyType"], "Muscular")
        self.assertNotIn("CharacterBodyType", ta.appearance_fields(self.pool, SEED, "A", "B", 1, None, None)[0])


class TeleportHookTest(unittest.TestCase):
    def _run(self, seeds):
        snapshot = {"teams": [{"short_name": "CAR", "team_index": 0}], "players": [
            {"pgid": 500, "position": "QB", "archetype": "QB_StrongArm", "overall": 80, "ratings": dict(QB_RATINGS),
             "last_name": "Smith", "status": "Signed", "team_index": 0,
             "appearance": {"commentary_id": 0, "generic_head": h}} for h in HEADS[:1]] +
            [{"pgid": 900 + i, "status": "Retired", "team_index": 40, "appearance": {"generic_head": h}}
             for i, h in enumerate(HEADS[1:])]}
        roster = [{"player_id": 1, "player_name": "G. Crawford", "full_name": "George Crawford", "height": "6'3\"",
                   "weight": 229, "jersey": 10, "team": "CAR", "position": "QB", "archetype": "Strong Arm",
                   "overall": 82, "age": 23, "years_pro": 2, "dev_trait": "Normal"}]
        return tp.teleport(snapshot, roster, ow.load_weights(), seeds=seeds)

    def test_off_by_default(self):
        edits, report = self._run(None)
        self.assertNotIn("GenericHeadAssetName", edits["players"][0]["set"])
        self.assertNotIn("appearance", report)

    def test_seeds_add_the_fields(self):
        edits, report = self._run({1: {"name": "G. Crawford", "team": "Panthers", "position": "QB", "source": "batch"}})
        s = edits["players"][0]["set"]
        self.assertEqual(s["PLYR_ASSETNAME"], "CrawfordGeorge_500")
        self.assertIn(s["GenericHeadAssetName"], HEADS)
        self.assertEqual(report["appearance"]["players"], 1)
        self.assertEqual(report["appearance"]["seed_source"], {"batch": 1})


@pytest.mark.integration
@unittest.skipUnless(_sample_ready(), "needs the sample save, node and `npm install` in pipeline/save_reader")
class SampleAppearanceTest(unittest.TestCase):
    """madden_test's season onto the sample save with appearance, applied to a copy and read back."""

    def test_heads_land_and_follow_the_headshots(self):
        snapshot = json.loads(subprocess.run(["node", READER, SAMPLE_SAVE], capture_output=True, text=True,
                                             check=True, timeout=300).stdout)
        with psycopg.connect(tp.DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            season = cur.fetchone()[0]
            roster, seeds = tp.project_mu_roster(cur, season), ta.headshot_seeds(cur, season)
        edits, report = tp.teleport(snapshot, roster, ow.load_weights(), seeds=seeds)
        look = report["appearance"]
        self.assertEqual(look["players"], report["matched"])
        tones = Counter()
        for tone, n in look["headshot_tone"].items():
            tones[ta.TONE_DIGITS[tone]] += n
        digits = Counter()
        for d, n in look["head_tone_digit"].items():
            digits[next(k for k in ta.TONE_DIGITS.values() if d in k)] += n
        self.assertEqual(tones, digits)
        with tempfile.TemporaryDirectory() as d:
            path, out = os.path.join(d, "edits.json"), os.path.join(d, "CAREER-TELEPORTED")
            with open(path, "w") as fh:
                json.dump(edits, fh)
            subprocess.run(["node", WRITER, SAMPLE_SAVE, path, "--out", out], check=True, capture_output=True,
                           timeout=600)
            after = json.loads(subprocess.run(["node", READER, out], capture_output=True, text=True, check=True,
                                              timeout=300).stdout)
        now = {p["pgid"]: p["appearance"] for p in after["players"]}
        for e in edits["players"]:
            a = now[e["pgid"]]
            self.assertEqual(a["generic_head"], e["set"]["GenericHeadAssetName"])
            self.assertEqual(a["asset_name"], e["set"]["PLYR_ASSETNAME"])
            if "CharacterBodyType" in e["set"]:
                self.assertEqual(a["body_type"], e["set"]["CharacterBodyType"])
