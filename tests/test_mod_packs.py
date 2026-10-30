"""
tests/test_mod_packs.py: Unit tests for pipeline/mod_packs.py (per-game MMC mod packs, docs/MOD_INSTALL.md §4).
Covers broadcast windows from kickoff, October BCA dates, crowd score and tiers, pack string parsing, which
mods a game switches (presentation, music, scorebug, logos, crowd, ball) and the config write. No database.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from pipeline import mod_packs as mp

BASE = [
    ("MUTRemover1.6.fbmod", True),
    ("BCA Pink Oct.fbmod", False),
    ("Mudded Wilson NFL BALL.fbmod", True),
    ("No VRAM Warning.fbmod", True),
    ("m27_SNF_presentation_mod_2.3.fbmod", False),
    ("m27_MNF_Presentation_mod_2.3.fbmod", False),
    ("m27_TNF_Presentation_mod_2.3.fbmod", True),
    ("m27_FOX_Presentation_mod_2.3.fbmod", False),
    ("m27_CBS_presentation_mod_2.3.1.fbmod", False),
    ("RealBroadcastMusicFOXPrimetime1.4.fbmod", False),
    ("Fox NFL Team Logos 1.4.fbmod", False),
    ("M27_NFLScorebugs_FOX_v3.fbmod", True),
    ("RealBroadcastMusicCBSPrimetime1.4.fbmod", False),
    ("Cbs NFL Team Logos 1.4.fbmod", False),
    ("M27_NFLScorebugs_CBS_v3.fbmod", True),
    ("Madden 27 Stadium Experience Mod (2.3).fbmod", True),
    ("Dynamic Crowd Density - Tier 1 (80%).fbmod", False),
    ("Dynamic Crowd Density - Tier 2 (70%).fbmod", True),
    ("Dynamic Crowd Density - Tier 3 (60%).fbmod", False),
    ("Dynamic Crowd Density - Tier 4 (40%).fbmod", False),
    ("Special Teams Formations.fbmod", True),
]


def game(**kw):
    g = dict(id=1, season=2036, week=2, game_time="Sun 1:00 PM", game_type="REGULAR_SEASON", home="NYG",
             away="PHI", away_conference="NFC", division_game=False, home_pop_pct=0.5, away_pop_pct=0.5,
             home_form=0.5)
    g.update(kw)
    return mp.GameInfo(**g)


def on(entries):
    return {n for n, e in entries if e}


class WindowTest(unittest.TestCase):
    def test_primetime_slots(self):
        self.assertEqual(mp.broadcast_window("Thu 8:20 PM", "AFC"), "TNF")
        self.assertEqual(mp.broadcast_window("Mon 8:30 PM", "AFC"), "MNF")
        self.assertEqual(mp.broadcast_window("Mon 10:15 PM", "NFC"), "MNF")
        self.assertEqual(mp.broadcast_window("Sun 8:20 PM", "NFC"), "SNF")
        self.assertEqual(mp.broadcast_window("Sun 6:40 PM", "AFC"), "SNF")   # playoff Sunday evening
        self.assertEqual(mp.broadcast_window("Sat 8:15 PM", "AFC"), "SNF")

    def test_afternoon_follows_road_conference(self):
        self.assertEqual(mp.broadcast_window("Sun 1:00 PM", "AFC"), "CBS")
        self.assertEqual(mp.broadcast_window("Sun 4:25 PM", "NFC"), "FOX")
        self.assertEqual(mp.broadcast_window("Thu 4:15 PM", "AFC"), "CBS")   # Thanksgiving afternoon
        self.assertEqual(mp.broadcast_window("Sat 4:40 PM", "NFC"), "FOX")

    def test_unknown_time_is_sunday_afternoon(self):
        self.assertEqual(mp.broadcast_window("", "AFC"), "CBS")
        self.assertEqual(mp.broadcast_window(None, "NFC"), "FOX")

    def test_october(self):
        # 2036 week 1 opens Thu Sep 4: week 5 Sunday is Oct 5, week 4 Sunday is Sep 28, week 9 Sunday Nov 2
        self.assertTrue(mp.is_october(2036, 5, "Sun 1:00 PM"))
        self.assertFalse(mp.is_october(2036, 4, "Sun 1:00 PM"))
        self.assertFalse(mp.is_october(2036, 9, "Sun 1:00 PM"))
        self.assertTrue(mp.is_october(2036, 9, "Thu 8:20 PM"))               # Thu Oct 30


class CrowdTest(unittest.TestCase):
    def test_score_weights_and_bonuses(self):
        g = game(home_pop_pct=1.0, home_form=1.0, away_pop_pct=1.0)
        self.assertEqual(mp.crowd_score(g, "FOX"), 100)
        self.assertEqual(mp.crowd_score(game(division_game=True), "FOX"), 50 + mp.DIVISION_BONUS)
        self.assertEqual(mp.crowd_score(game(), "SNF"), 50 + mp.PRIMETIME_BONUS)
        self.assertEqual(mp.crowd_score(game(week=16), "FOX"), 50 + mp.RACE_BONUS)
        self.assertEqual(mp.crowd_score(game(week=16, home_form=0.4), "FOX"), 47)   # no race bonus under .500

    def test_tiers(self):
        self.assertEqual(mp.crowd_tier(game(game_type="WILD_CARD"), "FOX"), ("FULL", None))
        self.assertEqual(mp.crowd_tier(game(home_pop_pct=1, home_form=1, away_pop_pct=1), "FOX")[0], "FULL")
        self.assertEqual(mp.crowd_tier(game(), "FOX")[0], 2)                         # 50
        self.assertEqual(mp.crowd_tier(game(home_pop_pct=0, home_form=0, away_pop_pct=0), "FOX")[0], 4)

    def test_form_blends_last_season_early(self):
        now, last = {7: (1.0, 1)}, {7: (4.0, 17)}
        self.assertAlmostEqual(mp.form(7, now, last), (1 + 3 * 4 / 17) / 4)
        self.assertAlmostEqual(mp.form(7, {7: (3.0, 4)}, last), 0.75)
        self.assertAlmostEqual(mp.form(8, {}, {}), 0.5)


class PackTest(unittest.TestCase):
    def test_parse_and_format_round_trip(self):
        text = "a.fbmod:True|b c (1).fbmod:False"
        self.assertEqual(mp.parse_pack(text), [("a.fbmod", True), ("b c (1).fbmod", False)])
        self.assertEqual(mp.format_pack(mp.parse_pack(text)), text)
        self.assertEqual(mp.parse_pack(""), [])

    def test_cbs_game(self):
        c = mp.Choice("CBS", 1, 70.0, False)
        got = on(mp.build_pack(BASE, c))
        self.assertTrue({"m27_CBS_presentation_mod_2.3.1.fbmod", "RealBroadcastMusicCBSPrimetime1.4.fbmod",
                         "M27_NFLScorebugs_CBS_v3.fbmod", "Cbs NFL Team Logos 1.4.fbmod",
                         "Dynamic Crowd Density - Tier 1 (80%).fbmod", "Mudded Wilson NFL BALL.fbmod"} <= got)
        self.assertFalse(got & {"m27_TNF_Presentation_mod_2.3.fbmod", "M27_NFLScorebugs_FOX_v3.fbmod",
                                "Dynamic Crowd Density - Tier 2 (70%).fbmod", "BCA Pink Oct.fbmod"})

    def test_primetime_uses_fox_bug_and_no_network_music(self):
        got = on(mp.build_pack(BASE, mp.Choice("SNF", "FULL", None, True)))
        self.assertTrue({"m27_SNF_presentation_mod_2.3.fbmod", "M27_NFLScorebugs_FOX_v3.fbmod",
                         "Fox NFL Team Logos 1.4.fbmod", "BCA Pink Oct.fbmod"} <= got)
        self.assertFalse(got & {"RealBroadcastMusicFOXPrimetime1.4.fbmod", "RealBroadcastMusicCBSPrimetime1.4.fbmod",
                                "M27_NFLScorebugs_CBS_v3.fbmod", "Mudded Wilson NFL BALL.fbmod"})
        self.assertFalse(any("Crowd Density" in n for n in got))                       # FULL: no crowd mod

    def test_other_mods_keep_base_state_and_order(self):
        built = mp.build_pack(BASE, mp.Choice("FOX", 3, 30.0, False))
        self.assertEqual([n for n, _ in built], [n for n, _ in BASE])
        keep = dict(BASE)
        for name in ("MUTRemover1.6.fbmod", "No VRAM Warning.fbmod", "Special Teams Formations.fbmod",
                     "Madden 27 Stadium Experience Mod (2.3).fbmod"):
            self.assertEqual(dict(built)[name], keep[name])

    def test_pack_names(self):
        self.assertEqual(mp.Choice("FOX", 2, 60.0, False).pack_name, "Mu FOX Tier2 Mudded")
        self.assertEqual(mp.Choice("TNF", "FULL", None, True).pack_name, "Mu TNF Full BCA")

    def test_choose(self):
        c = mp.choose(game(week=6, game_time="Mon 8:30 PM", home_pop_pct=0.9, home_form=0.8))
        self.assertEqual((c.window, c.bca), ("MNF", True))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(self.path, "w") as f:
            json.dump({"Games": {"Madden27": {"Options": {"SelectedPack": "Default"},
                                              "Packs": {"Default": "a.fbmod:True"}}}}, f)

    def tearDown(self):
        os.remove(self.path)

    def test_write_selects_and_keeps_other_packs(self):
        mp.write_pack(self.path, "Mu FOX Tier1 Mudded", [("a.fbmod", False), ("b.fbmod", True)])
        cfg = json.load(open(self.path))["Games"]["Madden27"]
        self.assertEqual(cfg["Packs"]["Default"], "a.fbmod:True")
        self.assertEqual(cfg["Packs"]["Mu FOX Tier1 Mudded"], "a.fbmod:False|b.fbmod:True")
        self.assertEqual(cfg["Options"]["SelectedPack"], "Mu FOX Tier1 Mudded")

    def test_missing_base_pack(self):
        with self.assertRaises(SystemExit):
            mp.base_pack(self.path)

    def test_seed_base_refuses_while_manager_open(self):
        with mock.patch.dict(os.environ, {"MMC_CONFIG": self.path}), \
                mock.patch.object(mp, "manager_running", return_value=True):
            with self.assertRaises(SystemExit):
                mp.main(["seed-base", "--from", "Default"])
        self.assertNotIn(mp.BASE_PACK, json.load(open(self.path))["Games"]["Madden27"]["Packs"])

    def test_seed_base_copies(self):
        with mock.patch.dict(os.environ, {"MMC_CONFIG": self.path}), \
                mock.patch.object(mp, "manager_running", return_value=False):
            mp.main(["seed-base", "--from", "Default"])
            with self.assertRaises(SystemExit):                     # never overwrites
                mp.main(["seed-base", "--from", "Default"])
        cfg = json.load(open(self.path))["Games"]["Madden27"]
        self.assertEqual(cfg["Packs"][mp.BASE_PACK], "a.fbmod:True")
        self.assertEqual(cfg["Options"]["SelectedPack"], "Default")


if __name__ == "__main__":
    unittest.main()
