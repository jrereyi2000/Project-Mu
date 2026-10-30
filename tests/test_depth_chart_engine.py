"""engines/depth_chart_engine.py: "Let Staff Set" on synthetic rosters (injuries, scheme fit, eligibility, specialists,
one base start per player, coach terms at 0) and store/load against a fake cursor (no database)."""
import random
import unittest

from engines import depth_chart_engine as dce

RATINGS = [
    "Acceleration", "Agility", "Awareness", "BCVision", "BlockShedding", "BreakSack", "BreakTackle", "Carrying",
    "CatchInTraffic", "Catching", "ChangeOfDirection", "DeepRouteRunning", "FinesseMoves", "HitPower",
    "ImpactBlocking", "Injury", "JukeMove", "Jumping", "KickAccuracy", "KickPower", "KickReturn", "LeadBlock",
    "LongSnap", "ManCoverage", "MediumRouteRunning", "PassBlock", "PassBlockFinesse", "PassBlockPower", "PlayAction",
    "PlayRecognition", "PowerMoves", "Press", "Pursuit", "Release", "RunBlock", "RunBlockFinesse", "RunBlockPower",
    "ShortRouteRunning", "SpectacularCatch", "Speed", "SpinMove", "Stamina", "StiffArm", "Strength", "Tackle",
    "ThrowAccuracy", "ThrowAccuracyDeep", "ThrowAccuracyMid", "ThrowAccuracyShort", "ThrowOnTheRun", "ThrowPower",
    "ThrowUnderPressure", "Toughness", "Trucking", "ZoneCoverage",
]
OL = ["PassBlock", "PassBlockFinesse", "PassBlockPower", "RunBlock", "RunBlockFinesse", "RunBlockPower",
      "ImpactBlocking", "Strength", "Awareness"]
DL = ["BlockShedding", "FinesseMoves", "PowerMoves", "Pursuit", "Tackle", "Strength", "PlayRecognition"]
COVER = ["ManCoverage", "ZoneCoverage", "Press", "Speed", "Acceleration", "Agility", "PlayRecognition"]
KEYS = {
    "QB": ["ThrowPower", "ThrowAccuracy", "ThrowAccuracyShort", "ThrowAccuracyMid", "ThrowAccuracyDeep",
           "ThrowOnTheRun", "ThrowUnderPressure", "PlayAction", "Awareness"],
    "HB": ["Speed", "Acceleration", "Agility", "BCVision", "Carrying", "BreakTackle", "JukeMove", "Trucking",
           "ChangeOfDirection", "KickReturn"],
    "FB": ["LeadBlock", "ImpactBlocking", "RunBlock", "Strength", "Carrying", "Trucking"],
    "WR": ["Speed", "Acceleration", "Catching", "ShortRouteRunning", "MediumRouteRunning", "DeepRouteRunning",
           "Release", "CatchInTraffic", "SpectacularCatch", "Agility", "KickReturn"],
    "TE": ["Catching", "RunBlock", "PassBlock", "ShortRouteRunning", "Strength", "CatchInTraffic", "LongSnap"],
    "OL": OL, "C": OL + ["LongSnap"],
    "DE": DL + ["Speed", "Acceleration"], "DT": DL,
    "OLB": ["Tackle", "Pursuit", "PlayRecognition", "ZoneCoverage", "BlockShedding", "Speed", "HitPower"],
    "MLB": ["Tackle", "Pursuit", "PlayRecognition", "ZoneCoverage", "HitPower", "Awareness"],
    "CB": COVER + ["KickReturn", "Catching"], "S": COVER + ["Tackle", "HitPower"],
    "K": ["KickPower", "KickAccuracy"], "P": ["KickPower", "KickAccuracy"], "LS": ["LongSnap", "Strength"],
}
GROUP = {"LT": "OL", "LG": "OL", "RG": "OL", "RT": "OL", "LE": "DE", "RE": "DE", "LOLB": "OLB", "ROLB": "OLB",
         "FS": "S", "SS": "S"}
ARCHETYPES = {
    "QB": ["QB_FieldGeneral", "QB_StrongArm", "QB_Improviser", "QB_Scrambler"],
    "HB": ["HB_PowerBack", "HB_ElusiveBack", "HB_ReceivingBack"], "FB": ["FB_Blocking", "FB_Utility"],
    "WR": ["WR_DeepThreat", "WR_Playmaker", "WR_Physical", "WR_Slot"],
    "TE": ["TE_Blocking", "TE_VerticalThreat", "TE_Possession"],
    "OL": ["OT_PassProtector", "OT_Power", "OT_Agile"], "C": ["C_PassProtector", "C_Power", "C_Agile"],
    "DE": ["DE_SmallerSpeedRusher", "DE_PowerRusher", "DE_RunStopper"],
    "DT": ["DT_NoseTackle", "DT_SpeedRusher", "DT_PowerRusher"], "OLB": ["OLB_PassCoverage", "OLB_RunStopper"],
    "MLB": ["MLB_FieldGeneral", "MLB_PassCoverage", "MLB_RunStopper"], "CB": ["CB_MantoMan", "CB_Slot", "CB_Zone"],
    "S": ["S_Zone", "S_Hybrid", "S_RunSupport"], "K": ["KP_Accurate", "KP_Power"], "P": ["KP_Accurate", "KP_Power"],
    "LS": ["LS_Power", "LS_Accurate"],
}
ROSTER_53 = {"QB": 3, "HB": 4, "FB": 1, "WR": 6, "TE": 3, "LT": 2, "LG": 2, "C": 2, "RG": 2, "RT": 2, "LE": 2,
             "RE": 2, "DT": 4, "LOLB": 2, "MLB": 2, "ROLB": 2, "CB": 5, "FS": 2, "SS": 2, "K": 1, "P": 1, "LS": 1}
EXTRA_65 = {"QB": 1, "HB": 1, "WR": 2, "TE": 1, "LG": 1, "RT": 1, "LE": 1, "DT": 1, "MLB": 1, "CB": 2}


def make_player(pid, pos, rng, overall=None, archetype=None, age=None, **ratings):
    group = GROUP.get(pos, pos)
    ovr = overall if overall is not None else rng.randint(58, 92)
    r = {k: rng.randint(25, 55) for k in RATINGS}
    for k in KEYS[group]:
        r[k] = max(30, min(99, ovr + rng.randint(-6, 6)))
    if pos not in ("LS", "C", "TE"):
        r["LongSnap"] = rng.choice([0, 5, 10])
    r.update(ratings)
    if pos == "LS":
        r["LongSnap"] = max(r["LongSnap"], 80)
    return {"player_id": pid, "position": pos, "archetype": archetype or rng.choice(ARCHETYPES[group]),
            "overall": ovr, "ratings": r, "age": age if age is not None else rng.randint(21, 34),
            "stamina": r["Stamina"]}


def make_roster(counts=ROSTER_53, extra=None, seed=11):
    rng = random.Random(seed)
    roster, pid = [], 1000
    for table in (counts, extra or {}):
        for pos, n in table.items():
            for _ in range(n):
                pid += 1
                roster.append(make_player(pid, pos, rng))
    return roster


def by_id(roster):
    return {p["player_id"]: p for p in roster}


SCHEME = {"offense": "Wide Zone West Coast", "defense": "4-3", "man_coverage": "High"}


class ChartShapeTest(unittest.TestCase):
    def check_chart(self, roster, injuries, scheme):
        chart = dce.staff_depth_chart(roster, scheme, injuries)
        players = by_id(roster)
        healthy = [p for p in roster if not dce.is_out(p["player_id"], injuries)]
        self.assertEqual(list(chart), dce.POSITION_SLOTS)
        for slot, ids in chart.items():
            self.assertTrue(ids, slot)
            self.assertLessEqual(len(ids), 6, slot)
            self.assertEqual(len(ids), len(set(ids)), slot)
            for pid in ids:
                self.assertIsNotNone(dce.eligibility_penalty(slot, players[pid]["position"]),
                                     f"{slot}: {players[pid]['position']} not eligible")
            pool = sum(1 for p in healthy if dce.eligibility_penalty(slot, p["position"]) is not None)
            self.assertEqual(len(ids), min(6, pool), slot)
        return chart

    def test_position_slots_are_maddens_35(self):
        self.assertEqual(len(dce.POSITION_SLOTS), 35)
        self.assertEqual(len(set(dce.POSITION_SLOTS)), 35)
        for slot in ("QB", "KR", "PR", "3DRB", "SLWR", "SLCB", "SUBLB", "RLE", "RRE", "RDT", "LS", "KOS", "PWHB",
                     "GAD", "NT"):
            self.assertIn(slot, dce.POSITION_SLOTS)
        self.assertEqual(set(dce.ELIGIBLE), set(dce.POSITION_SLOTS))
        self.assertEqual(set(dce.BASE_SLOTS) | set(dce.SITUATIONAL_SLOTS), set(dce.POSITION_SLOTS))

    def test_full_chart_53_man_with_injuries_and_scheme(self):
        roster = make_roster()
        qbs = sorted((p for p in roster if p["position"] == "QB"), key=lambda p: -p["overall"])
        injuries = {qbs[0]["player_id"]: {"out": True, "weeks": 3}}
        self.check_chart(roster, injuries, SCHEME)

    def test_full_chart_65_man(self):
        roster = make_roster(extra=EXTRA_65, seed=5)
        self.assertEqual(len(roster), 65)
        chart = self.check_chart(roster, {}, {"offense": "Spread", "defense": "3-4"})
        self.assertEqual(len(chart["WR"]), 6)
        self.assertEqual(len(chart["CB"]), 6)

    def test_empty_scheme_and_no_weights(self):
        roster = make_roster(seed=3)
        chart = dce.staff_depth_chart(roster, {}, {}, weights=None)
        self.assertTrue(all(chart[s] for s in dce.POSITION_SLOTS))


class InjuryTest(unittest.TestCase):
    def test_injured_players_never_appear(self):
        roster = make_roster(seed=21)
        out = [p["player_id"] for p in roster if p["position"] in ("QB", "WR", "LS")][:4]
        injuries = {pid: {"out": True, "weeks": 2} for pid in out}
        playing_hurt = next(p["player_id"] for p in roster if p["position"] == "CB")
        injuries[playing_hurt] = {"out": False, "weeks": 1}
        chart = dce.staff_depth_chart(roster, SCHEME, injuries)
        listed = {pid for ids in chart.values() for pid in ids}
        self.assertFalse(listed & set(out))
        self.assertIn(playing_hurt, listed)

    def test_string_keys_in_injuries(self):
        roster = make_roster(seed=21)
        qb = max((p for p in roster if p["position"] == "QB"), key=lambda p: p["overall"])
        chart = dce.staff_depth_chart(roster, {}, {str(qb["player_id"]): {"out": True, "weeks": 1}})
        self.assertNotIn(qb["player_id"], chart["QB"])


class OrderingTest(unittest.TestCase):
    def test_best_first_by_ovr(self):
        roster = make_roster(seed=8)
        chart = dce.staff_depth_chart(roster, {}, {})
        players = by_id(roster)
        qb = [players[pid]["overall"] for pid in chart["QB"]]
        self.assertEqual(qb, sorted(qb, reverse=True))
        self.assertTrue(all(players[pid]["position"] == "QB" for pid in chart["QB"]))
        hb = [players[pid] for pid in chart["HB"] if players[pid]["position"] == "HB"]
        self.assertEqual([p["overall"] for p in hb], sorted((p["overall"] for p in hb), reverse=True))

    def test_scheme_fit_breaks_close_calls(self):
        rng = random.Random(1)
        base = make_roster(seed=9)
        base = [p for p in base if p["position"] != "QB"]
        general = make_player(1, "QB", rng, overall=80, archetype="QB_FieldGeneral")
        scrambler = make_player(2, "QB", rng, overall=80, archetype="QB_Scrambler")
        roster = base + [general, scrambler]
        self.assertEqual(dce.staff_depth_chart(roster, {"offense": "West Coast"}, {})["QB"][:2], [1, 2])
        self.assertEqual(dce.staff_depth_chart(roster, {"offense": "Spread"}, {})["QB"][:2], [2, 1])

    def test_scheme_fit_is_small(self):
        rng = random.Random(1)
        base = [p for p in make_roster(seed=9) if p["position"] != "QB"]
        better = make_player(1, "QB", rng, overall=84, archetype="QB_Scrambler")
        fit = make_player(2, "QB", rng, overall=80, archetype="QB_FieldGeneral")
        self.assertEqual(dce.staff_depth_chart(base + [better, fit], {"offense": "West Coast"}, {})["QB"][0], 1)

    def test_natural_position_beats_out_of_position(self):
        rng = random.Random(4)
        roster = [p for p in make_roster(seed=4) if p["position"] not in ("FS", "SS")]
        fs = make_player(1, "FS", rng, overall=70)
        roster.append(fs)
        chart = dce.staff_depth_chart(roster, {}, {}, weights=None)
        self.assertEqual(chart["FS"][0], 1)          # CBs pay the out-of-position penalty
        self.assertTrue(all(by_id(roster)[pid]["position"] in ("FS", "SS", "CB") for pid in chart["FS"]))

    def test_slot_archetype_and_3drb(self):
        rng = random.Random(6)
        base = [p for p in make_roster(seed=6) if p["position"] not in ("HB", "WR")]
        power = make_player(1, "HB", rng, overall=85, archetype="HB_PowerBack", Catching=40, PassBlock=35,
                            ShortRouteRunning=40)
        receiver = make_player(2, "HB", rng, overall=78, archetype="HB_ReceivingBack", Catching=90, PassBlock=80,
                               ShortRouteRunning=88)
        wrs = [make_player(10 + i, "WR", rng) for i in range(5)]
        chart = dce.staff_depth_chart(base + [power, receiver] + wrs, {}, {})
        self.assertEqual(chart["HB"][0], 1)
        self.assertEqual(chart["3DRB"][0], 2)
        self.assertTrue(set(chart["3DRB"]) <= {1, 2} | {w["player_id"] for w in wrs})


class SpecialistTest(unittest.TestCase):
    def test_specialists_filled(self):
        roster = make_roster(seed=13)
        players = by_id(roster)
        chart = dce.staff_depth_chart(roster, SCHEME, {})
        self.assertEqual(players[chart["K"][0]]["position"], "K")
        self.assertEqual(players[chart["P"][0]]["position"], "P")
        self.assertEqual(players[chart["LS"][0]]["position"], "LS")
        self.assertIn(players[chart["KOS"][0]]["position"], ("K", "P"))
        for slot in ("KR", "PR"):
            self.assertEqual(len(chart[slot]), 6)
            self.assertTrue(all(players[pid]["position"] in ("HB", "WR", "CB", "FS", "SS", "FB", "TE")
                                for pid in chart[slot]))

    def test_fastest_returner_first(self):
        rng = random.Random(2)
        base = make_roster(seed=2)
        burner = make_player(1, "WR", rng, overall=62, KickReturn=95, Speed=98, Acceleration=97, Agility=94,
                             BCVision=80, Carrying=80, ChangeOfDirection=93)
        chart = dce.staff_depth_chart(base + [burner], {}, {})
        self.assertEqual(chart["KR"][0], 1)
        self.assertEqual(chart["PR"][0], 1)

    def test_long_snapper_falls_back_to_te_or_c(self):
        roster = [p for p in make_roster(seed=17) if p["position"] != "LS"]
        players = by_id(roster)
        chart = dce.staff_depth_chart(roster, {}, {})
        self.assertIn(players[chart["LS"][0]]["position"], ("TE", "C"))

    def test_rush_slots_from_pass_rushers(self):
        roster = make_roster(seed=19)
        players = by_id(roster)
        chart = dce.staff_depth_chart(roster, SCHEME, {})
        for slot in ("RLE", "RRE"):
            self.assertIn(players[chart[slot][0]]["position"], ("LE", "RE", "LOLB", "ROLB"))
        self.assertEqual(players[chart["RDT"][0]]["position"], "DT")


class StartingConflictTest(unittest.TestCase):
    def assert_one_start_each(self, chart, scheme):
        starts = [pid for ids in dce.starters_by_base_slot(chart, scheme).values() for pid in ids]
        self.assertEqual(len(starts), len(set(starts)))

    def test_no_player_starts_twice(self):
        for seed in range(6):
            roster = make_roster(seed=seed)
            for scheme in (SCHEME, {"offense": "Spread", "defense": "3-4"}, {}):
                self.assert_one_start_each(dce.staff_depth_chart(roster, scheme, {}), scheme)

    def test_thin_line_does_not_double_start(self):
        # Only three healthy guards/centers for LG, C, RG plus two tackles: everyone starts once, backups may repeat.
        roster = make_roster(seed=23)
        line = [p["player_id"] for p in roster if p["position"] in ("LG", "RG", "C")]
        injuries = {pid: {"out": True, "weeks": 4} for pid in line[:3]}
        chart = dce.staff_depth_chart(roster, SCHEME, injuries)
        self.assert_one_start_each(chart, SCHEME)
        for slot in ("LG", "C", "RG", "LT", "RT"):
            self.assertTrue(chart[slot])

    def test_starter_counts_follow_front(self):
        self.assertEqual(dce.starter_counts({"defense": "4-3"})["DT"], 2)
        self.assertEqual(dce.starter_counts({"defense": "3-4"})["MLB"], 2)
        self.assertEqual(dce.starter_counts({})["WR"], 3)

    def test_slot_with_no_eligible_player_still_gets_a_starter(self):
        roster = [p for p in make_roster(seed=29) if p["position"] != "QB"]
        chart = dce.staff_depth_chart(roster, {}, {})
        self.assertEqual(len(chart["QB"]), 1)
        self.assert_one_start_each(chart, {})


class CoachTermTest(unittest.TestCase):
    def test_coach_terms_change_nothing(self):
        roster = make_roster(seed=31)
        base = dce.staff_depth_chart(roster, SCHEME, {})
        for coach in ({"youth_trust": 1.0, "veteran_trust": -1.0}, {"youth_trust": -1.0, "veteran_trust": 1.0}):
            self.assertEqual(dce.staff_depth_chart(roster, SCHEME, {}, coach=coach), base)
            for p in roster:
                self.assertEqual(dce.coach_trust(p, coach), 0.0)
        self.assertEqual(dce.COACH_TRUST_SCALE, 0.0)


class FakeCursor:
    """Records every statement and keeps depth_charts rows in memory."""

    def __init__(self):
        self.calls = []
        self.rows = []
        self._result = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if sql.startswith("DELETE FROM depth_charts"):
            key = tuple(params)
            self.rows = [r for r in self.rows if (r[0], r[1], r[2], r[6]) != key]
        elif sql.startswith("INSERT INTO depth_charts"):
            self.rows.append(tuple(params))
        elif sql.startswith("SELECT position_slot, depth, player_id FROM depth_charts"):
            t, s, w, src = params
            self._result = sorted((r[3], r[4], r[5]) for r in self.rows if (r[0], r[1], r[2], r[6]) == (t, s, w, src))
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def fetchall(self):
        return list(self._result)


class StoreLoadTest(unittest.TestCase):
    def test_round_trip(self):
        roster = make_roster(seed=37)
        chart = dce.staff_depth_chart(roster, SCHEME, {})
        cur = FakeCursor()
        dce.store(cur, 3, 2034, 5, chart, "MU_COACH")
        self.assertTrue(cur.calls[0][0].startswith("DELETE FROM depth_charts"))
        self.assertEqual(cur.calls[0][1], (3, 2034, 5, "MU_COACH"))
        inserts = [p for sql, p in cur.calls if sql.startswith("INSERT")]
        self.assertEqual(len(inserts), sum(len(v) for v in chart.values()))
        self.assertTrue(all(1 <= p[4] <= 6 and p[6] == "MU_COACH" for p in inserts))
        self.assertEqual(dce.load(cur, 3, 2034, 5, "MU_COACH"), chart)

    def test_store_replaces_only_its_source(self):
        cur = FakeCursor()
        dce.store(cur, 3, 2034, 5, {"QB": [1, 2, 3]}, "MADDEN")
        dce.store(cur, 3, 2034, 5, {"QB": [9, 8]}, "JR")
        dce.store(cur, 3, 2034, 5, {"QB": [2, 1]}, "MADDEN")
        self.assertEqual(dce.load(cur, 3, 2034, 5, "MADDEN")["QB"], [2, 1])
        self.assertEqual(dce.load(cur, 3, 2034, 5, "JR")["QB"], [9, 8])
        self.assertEqual(dce.load(cur, 3, 2034, 6, "JR"), {})
        loaded = dce.load(cur, 3, 2034, 5, "JR")
        self.assertEqual(list(loaded), dce.POSITION_SLOTS)
        self.assertEqual(loaded["KR"], [])

    def test_store_validates(self):
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            dce.store(cur, 3, 2034, 5, {"QB": [1]}, "CPU")
        with self.assertRaises(ValueError):
            dce.store(cur, 3, 2034, 5, {"WR1": [1]}, "JR")
        with self.assertRaises(ValueError):
            dce.store(cur, 3, 2034, 5, {"WR": list(range(7))}, "JR")
        with self.assertRaises(ValueError):
            dce.load(cur, 3, 2034, 5, "CPU")
        self.assertEqual(cur.calls, [])


if __name__ == "__main__":
    unittest.main()
