"""engines/ovr_weights.py: fitting synthetic players whose per-archetype grades come from known formulas, the slot map,
OVR as the top grade, moving ratings to a target, and the stored Madden 27 formulas."""
import os
import random
import unittest

from engines import ovr_weights as ow

# Two archetypes per position; WR_Elite is convex (squared terms), the rest linear. "Marker" is low for everyone.
FORMULAS = {
    "WR_Fast": ({"Speed": (0.5, 0.0), "Catching": (0.3, 0.0), "Awareness": (0.2, 0.0)}, -5.0),
    "WR_Elite": ({"Catching": (0.1, 0.3), "Awareness": (0.2, 0.2), "Marker": (0.4, 0.0)}, 0.0),
    "OL_Big": ({"Strength": (0.6, 0.0), "PassBlock": (0.4, 0.0)}, -3.0),
    "OL_Quick": ({"PassBlock": (0.5, 0.0), "Speed": (0.5, 0.0)}, -4.0),
}
SLOTS = {"WR": ["WR_Fast", "WR_Elite"], "LT": ["OL_Big", "OL_Quick"]}
RATINGS = ("Speed", "Catching", "Awareness", "Strength", "PassBlock")


def grade(arch, r):
    terms, b = FORMULAS[arch]
    return round(b + sum(w * r[k] + q * r[k] ** 2 / 100 for k, (w, q) in terms.items()))


def synthetic_snapshot(n=120, seed=7):
    rng = random.Random(seed)
    players = []
    for pos, archs in SLOTS.items():
        for _ in range(n):
            r = {k: rng.randint(40, 99) for k in RATINGS}
            r["Marker"] = rng.choice([0, 5, 10, 10, 10])
            g = [grade(a, r) for a in archs] + [0, 0, 0]
            top = max(g)
            players.append({"position": pos, "archetype": archs[g.index(top)], "overall": top,
                            "archetype_grades": g, "ratings": r})
    return {"meta": {"game_year": 27, "source": "/tmp/SYNTHETIC", "reader_version": 2}, "players": players}


class FitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = synthetic_snapshot()
        cls.doc = ow.fit_weights(cls.snap)

    def test_slot_map(self):
        self.assertEqual(self.doc["slots"]["WR"][:2], ["WR_Fast", "WR_Elite"])
        self.assertEqual(self.doc["slots"]["LT"][:2], ["OL_Big", "OL_Quick"])
        self.assertIsNone(self.doc["slots"]["WR"][2])

    def test_every_grade_is_a_sample(self):
        self.assertEqual(self.doc["archetypes"]["WR_Fast"]["n"], 120)
        self.assertEqual(self.doc["samples"], 480)

    def test_linear_and_squared_models_are_recovered(self):
        fast = self.doc["archetypes"]["WR_Fast"]
        self.assertAlmostEqual(fast["terms"]["Speed"][0] + fast["terms"]["Speed"][1], 0.5, delta=0.1)
        elite = self.doc["archetypes"]["WR_Elite"]
        self.assertEqual(elite["model"], "quadratic")
        self.assertGreater(elite["cv_within_1"], elite["linear_cv_within_1"])
        self.assertGreaterEqual(self.doc["cv_within_1"], 0.95)

    def test_ovr_is_the_top_grade(self):
        self.assertGreaterEqual(self.doc["ovr_within_1"], 0.95)
        r = {"Speed": 90, "Catching": 60, "Awareness": 60, "Strength": 50, "PassBlock": 50, "Marker": 10}
        ovr, arch = ow.predict_ovr(self.doc, "WR", r)
        grades = ow.predict_grades(self.doc, "WR", r)
        self.assertEqual((ovr, arch), (max(grades.values()), max(grades, key=grades.get)))

    def test_tie_keeps_the_current_archetype(self):
        doc = {"slots": {"X": ["A", "B"]}, "archetypes": {
            "A": {"terms": {"Speed": [1.0, 0.0]}, "intercept": 0.0},
            "B": {"terms": {"Speed": [1.0, 0.0]}, "intercept": 0.0}}}
        self.assertEqual(ow.predict_ovr(doc, "X", {"Speed": 70}), (70, "A"))
        self.assertEqual(ow.predict_ovr(doc, "X", {"Speed": 70}, current="B"), (70, "B"))

    def test_marker_ratings_are_not_skills(self):
        elite = self.doc["archetypes"]["WR_Elite"]
        self.assertNotIn("Marker", elite["skills"])
        self.assertIn("Catching", elite["skills"])

    def test_adjust_reaches_the_target_on_skills_only(self):
        r = {"Speed": 80, "Catching": 75, "Awareness": 70, "Strength": 50, "PassBlock": 50, "Marker": 10}
        for arch in ("WR_Fast", "WR_Elite"):
            start = ow.predict_grade(self.doc, arch, r)
            for target in (start + 4, start - 3):
                new, changes = ow.adjust_ratings(self.doc, arch, r, target)
                self.assertEqual(ow.predict_grade(self.doc, arch, new), target)
                self.assertNotIn("Marker", changes)
                self.assertTrue(set(changes) <= set(self.doc["archetypes"][arch]["skills"]))
        self.assertEqual(r["Speed"], 80, "the input ratings aren't changed")

    def test_adjust_spreads_respects_the_cap_and_fails_cleanly(self):
        r = {"Speed": 99, "Catching": 80, "Awareness": 99, "Marker": 10}
        target = ow.predict_grade(self.doc, "WR_Fast", r) + 1
        new, changes = ow.adjust_ratings(self.doc, "WR_Fast", r, target)
        self.assertEqual((new["Speed"], new["Awareness"]), (99, 99))
        self.assertGreater(changes["Catching"], 0)
        with self.assertRaises(ValueError):
            ow.adjust_ratings(self.doc, "WR_Fast", r, 150)


@unittest.skipUnless(os.path.exists(ow.DEFAULT_PATH), "no stored weights")
class StoredWeightsTest(unittest.TestCase):
    """The committed Madden 27 formulas (fitted from CAREER-GIANTS-AUTOSAVE)."""

    def setUp(self):
        self.doc = ow.load_weights()

    def test_shape_and_accuracy(self):
        self.assertEqual(self.doc["game_year"], 27)
        self.assertEqual(self.doc["slots"]["QB"][:4], ["QB_FieldGeneral", "QB_StrongArm", "QB_Improviser", "QB_Scrambler"])
        self.assertEqual(self.doc["slots"]["HB"][:3], ["HB_PowerBack", "HB_ElusiveBack", "HB_ReceivingBack"])
        self.assertGreaterEqual(self.doc["cv_within_1"], 0.97)
        self.assertGreaterEqual(self.doc["ovr_within_1"], 0.97)
        for arch, a in self.doc["archetypes"].items():
            self.assertTrue(a["terms"], arch)
            self.assertTrue(all(w >= 0 and q >= 0 for w, q in a["terms"].values()), arch)
            self.assertTrue(a["skills"], arch)

    def test_qb_formulas_beat_linear_and_never_move_long_snap(self):
        for arch in ("QB_FieldGeneral", "QB_StrongArm", "QB_Improviser", "QB_Scrambler"):
            a = self.doc["archetypes"][arch]
            self.assertEqual(a["model"], "quadratic", arch)
            self.assertGreater(a["cv_within_1"], a["linear_cv_within_1"] + 0.1, arch)
            self.assertNotIn("LongSnap", a["skills"], arch)
        self.assertIn("LongSnap", self.doc["archetypes"]["LS_Accurate"]["skills"])

    def test_a_qb_moves_to_its_target(self):
        r = {"Awareness": 85, "ThrowPower": 90, "ThrowAccuracyShort": 88, "ThrowAccuracyMid": 85,
             "ThrowAccuracyDeep": 82, "Acceleration": 75, "Injury": 85, "LongSnap": 10}
        start = ow.predict_grade(self.doc, "QB_StrongArm", r)
        new, changes = ow.adjust_ratings(self.doc, "QB_StrongArm", r, start + 3)
        self.assertEqual(ow.predict_grade(self.doc, "QB_StrongArm", new), start + 3)
        self.assertNotIn("LongSnap", changes)
