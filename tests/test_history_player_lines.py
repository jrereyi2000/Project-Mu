"""Pure tests for 2026-2034 player lines (docs/HISTORY_BACKFILL_PLAN.md phase 3): the cast, team totals, the
allocation helpers, a small generate() and fill-ins. No DB."""
import unittest

from engines.history_backfill import cast, fillins, player_lines as pl, team_totals as tt


class CastTest(unittest.TestCase):
    def test_team_path_moves_halfway(self):
        self.assertEqual(cast.team_path({2026: 1, 2035: 2}, range(2026, 2035)),
                         {2026: 1, 2027: 1, 2028: 1, 2029: 1, 2030: 1, 2031: 2, 2032: 2, 2033: 2, 2034: 2})

    def test_build_pool_vet_and_facts(self):
        pool = [dict(player_id=7, name="A. Pool", full_name="Al Pool", position="WR", team_2035=3, age_2035=29,
                     exp_2035=6, overall=80, dev="Star", team_2026=None)]
        vets = [dict(espn_id="9", full_name="Vic Vet", position="QB", team_2026=4, birth_date="1990-01-01",
                     exp_2026=13, production=1.0)]
        facts = [dict(name="Vic Vet", season=2030, team=5, kind="award:MVP"),
                 dict(name="Al Pool", season=2031, team=3, kind="record:season_sacks")]    # a namesake: no WR sacks
        out = cast.build(pool, vets, facts, seed=2)
        pool_m = next(m for m in out["members"] if m["kind"] == "pool")
        vet_m = next(m for m in out["members"] if m["kind"] == "vet")
        self.assertEqual(sorted(pool_m["seasons"]), list(range(2029, 2035)))    # first season 2035 - 6
        self.assertGreaterEqual(vet_m["last"], 2030)                            # never retires before his award
        self.assertEqual(vet_m["seasons"][2030], 5)
        self.assertEqual([f["name"] for f in out["unknown"]], ["Al Pool"])
        self.assertEqual(out["unknown"][0]["position"], "REDG")

    def test_record_groups(self):
        self.assertEqual(cast.record_groups("record:rookie_game_receptions"), {"WR", "TE", "RB"})
        self.assertIsNone(cast.record_groups("award:MVP"))


class TeamTotalsTest(unittest.TestCase):
    def test_fit_recovers_a_line_and_draw_keeps_identities(self):
        samples = []
        for pf, pa in ((300, 400), (350, 380), (400, 350), (450, 320), (500, 300), (420, 420)):
            row = {s: 100 for s in tt.STATS}
            row["pass_yards"] = 1000 + 7 * pf - 1 * pa
            samples.append((pf, pa, row))
        model = tt.fit(samples)
        a, b1, b2, sd = model["pass_yards"]
        self.assertAlmostEqual(b1, 7, places=6)
        self.assertAlmostEqual(b2, -1, places=6)
        out = tt.draw(model, {(1, 2026): (400, 350), (2, 2026): (350, 400)}, seed=1)
        for row in out.values():
            self.assertEqual(row["rec_yards"], row["pass_yards"])
            self.assertEqual(row["blk_sacks_allowed"], row["pass_sacks"])
        self.assertAlmostEqual(sum(r["def_ints"] for r in out.values()),
                               sum(r["interceptions"] for r in out.values()), delta=1)


class AllocationTest(unittest.TestCase):
    def test_split_exact(self):
        got = pl.split_exact(10, {"a": 1, "b": 1, "c": 1})
        self.assertEqual(sum(got.values()), 10)
        self.assertEqual(pl.split_exact(4, {"a": 0, "b": 0}), {"a": 2, "b": 2})

    def test_split_capped_and_allocate(self):
        got, left = pl.split_capped(10, {"a": 9, "b": 1}, {"a": 3})
        self.assertEqual((got, left), ({"a": 3, "b": 7}, 0))
        got, err = pl.allocate(100, {2026: 1, 2027: 1, 2028: 1}, {2027: 50}, {2028: 40}, {})
        self.assertEqual(got[2027], 50)
        self.assertEqual(got[2028], 40)                       # the floor became a pin
        self.assertEqual(sum(got.values()), 100)
        self.assertIsNone(err)
        _, err = pl.allocate(10, {2026: 1}, {2026: 20}, {}, {})
        self.assertIn("above the remainder", err)

    def test_consistent(self):
        row = {"completions": 30, "pass_attempts": 20, "rush_yac": 90, "rush_yards": 50}
        pl.consistent(row)
        self.assertEqual((row["pass_attempts"], row["rush_yac"]), (30, 50))


def member(key, kind, group, pos, seasons, pid=None, overall=75, production=0.5):
    return {"key": key, "kind": kind, "group": group, "position": pos, "player_id": pid, "full_name": key,
            "name": key, "overall": overall, "dev": None, "production": production, "seasons": seasons,
            "ages": {y: 27 for y in seasons}, "exp": {y: 4 for y in seasons}}


class GenerateTest(unittest.TestCase):
    def test_remainders_exact_and_vets_fill(self):
        seasons = {2026: 1, 2027: 1}
        qb = member("p1", "pool", "QB", "QB", dict(seasons), pid=1)
        vet = member("e9", "vet", "QB", "QB", dict(seasons))
        totals = {(1, y): {"pass_yards": 4000, "pass_attempts": 550, "completions": 350} for y in seasons}
        rem = {1: {"pass_yards": 6000, "pass_attempts": 800, "completions": 520}}
        lines, left, problems = pl.generate([qb, vet], totals, rem, {}, {}, {}, lambda m, y, s: None, [])
        self.assertEqual(sum(lines[("p1", y)]["pass_yards"] for y in seasons), 6000)
        self.assertEqual(sum(lines[("p1", y)]["completions"] for y in seasons), 520)
        for y in seasons:
            self.assertLessEqual(lines[("p1", y)]["completions"], lines[("p1", y)]["pass_attempts"])
            self.assertEqual(lines[("p1", y)]["pass_yards"] + lines.get(("e9", y), {}).get("pass_yards", 0)
                             + left[(1, y)]["pass_yards"], 4000)
        self.assertEqual(problems, [])

    def test_pins_and_ceilings(self):
        seasons = {2026: 1, 2027: 1, 2028: 1}
        wr = member("p2", "pool", "WR", "WR", dict(seasons), pid=2)
        totals = {(1, y): {"rec_yards": 4000, "receptions": 300} for y in seasons}
        rem = {2: {"rec_yards": 3600, "receptions": 240}}
        pins = {("p2", 2027): {"rec_yards": 1800}}
        ceil = lambda m, y, s: 1500 if s == "rec_yards" else None
        lines, _, _ = pl.generate([wr], totals, rem, {}, pins, {}, ceil, [])
        self.assertEqual(lines[("p2", 2027)]["rec_yards"], 1800)            # the record holder's season
        self.assertLessEqual(lines[("p2", 2026)]["rec_yards"], 1500)
        self.assertLessEqual(lines[("p2", 2028)]["rec_yards"], 1500)
        self.assertEqual(sum(lines[("p2", y)]["rec_yards"] for y in seasons), 3600)


class FillinTest(unittest.TestCase):
    def test_starter_gaps_become_capped_fillins(self):
        left = {(1, 2033): {"pass_yards": 3000, "pass_attempts": 400, "rush_yards": 200},
                (1, 2034): {"pass_yards": 9000, "pass_attempts": 1200},
                (2, 2034): {"pass_yards": 1000}}
        names = [("Abe", "Lincoln"), ("Bo", "Jackson"), ("Cal", "Ripken")]
        out, unassigned = fillins.build(left, names, taken_names=["Abe Jackson"], seed=3)
        qbs = [f for f in out if f["position"] == "QB"]
        self.assertEqual(len(qbs), 1)                                  # 2033-2034 is one career
        self.assertEqual(sorted(qbs[0]["seasons"]), [2033, 2034])
        self.assertEqual(qbs[0]["lines"][2034]["pass_yards"], int(round(fillins.FILL_CAP * 3900)))
        self.assertEqual(unassigned[(2, 2034)], {"pass_yards": 1000})   # not starter-sized
        self.assertNotIn(qbs[0]["full_name"], ("Abe Lincoln", "Bo Jackson", "Cal Ripken", "Abe Jackson"))


class ApplyTest(unittest.TestCase):
    def test_derived_rates(self):
        from engines.history_backfill import players
        r = players.derived("season_stats", {"completions": 300, "pass_attempts": 450, "pass_yards": 4500,
                                             "games_played": 17, "rush_yards": 0, "rush_attempts": 0})
        self.assertEqual((r["pass_comp_pct"], r["pass_ypa"], r["rush_avg"]), (66.7, 10.0, None))
        self.assertEqual(players.derived("season_stat_kicking", {"fgm": 27, "fga": 30})["fg_pct"], 90.0)
        self.assertEqual(players.SIDE["def_sacks"], ("season_stat_defense", "sacks"))


if __name__ == "__main__":
    unittest.main()
