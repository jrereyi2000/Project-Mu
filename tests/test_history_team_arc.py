"""Pure tests for the 2026-2034 team arc (docs/HISTORY_BACKFILL_PLAN.md phase 2): the bracket, the facts check and
a solve on a small synthetic league whose coach totals come from a known history. No DB."""
import random
import unittest

from engines.history_backfill import team_arc as ta

YEARS = [2026, 2027]


def league():
    """32 teams, 2 conferences of 4 divisions of 4; ids 1-32, abbreviations T1..T32."""
    teams = {}
    for i in range(32):
        conf = "AFC" if i < 16 else "NFC"
        teams[i + 1] = {"abbr": f"T{i + 1}", "conf": conf, "div": f"{conf} {i // 4 % 4}"}
    return teams


def true_history(teams, seed=3):
    """A valid history: complementary win pairs (272 a season), seeds by record, the higher seed always wins, the
    AFC wins the Super Bowl. Returns ({(team, season): row}, {season: (champion, runner-up)})."""
    rng = random.Random(seed)
    rows, sb = {}, {}
    ids = sorted(teams)
    for y in YEARS:
        order = ids[:]
        rng.shuffle(order)
        wins = {}
        for a, b in zip(order[:16], order[16:]):
            wins[a] = rng.randint(2, 15)
            wins[b] = 17 - wins[a]
        champs = {}
        for c in ("AFC", "NFC"):
            members = [t for t in ids if teams[t]["conf"] == c]
            divs = {}
            for t in members:
                divs.setdefault(teams[t]["div"], []).append(t)
            dw = [max(ts, key=lambda t: (wins[t], -t)) for ts in divs.values()]
            dw.sort(key=lambda t: (-wins[t], t))
            wc = sorted((t for t in members if t not in dw), key=lambda t: (-wins[t], t))[:3]
            seeds = dw + wc
            for t in members:
                rows[(t, y)] = {"wins": wins[t], "seed": seeds.index(t) + 1 if t in seeds else None, "pw": 0,
                                "pl": 0}
            for k in (5, 6, 7):                                  # higher seeds win: 2, 3, 4 advance
                rows[(seeds[k - 1], y)]["pl"] = 1
                rows[(seeds[9 - k - 1], y)]["pw"] += 1
            for w_, l_ in ((1, 4), (2, 3), (1, 2)):
                rows[(seeds[w_ - 1], y)]["pw"] += 1
                rows[(seeds[l_ - 1], y)]["pl"] = 1
            champs[c] = seeds[0]
        rows[(champs["AFC"], y)]["pw"] += 1
        rows[(champs["NFC"], y)]["pl"] = 1
        sb[y] = (champs["AFC"], champs["NFC"])
    return rows, sb


def facts_from(teams, rows, sb):
    snap = {}
    for t in teams:
        cid = 100 + t
        ys = [rows[(t, y)] for y in YEARS]
        snap[(cid, YEARS[0] - 1)] = dict.fromkeys(("wins", "losses", "winning_seasons", "playoff_wins",
                                                   "playoff_losses", "sb_wins", "sb_losses"), 0)
        snap[(cid, YEARS[-1])] = {
            "wins": sum(r["wins"] for r in ys), "losses": sum(17 - r["wins"] for r in ys),
            "winning_seasons": sum(1 for r in ys if r["wins"] > 8), "playoff_wins": sum(r["pw"] for r in ys),
            "playoff_losses": sum(r["pl"] for r in ys), "sb_wins": sum(1 for y in YEARS if sb[y][0] == t),
            "sb_losses": sum(1 for y in YEARS if sb[y][1] == t)}
    games = [{"id": 900 + y, "season": y, "type": "SUPER_BOWL", "home": w, "away": l, "hs": 30, "as": 20}
             for y, (w, l) in sb.items()]
    return {"teams": teams, "by_abbr": {v["abbr"]: k for k, v in teams.items()},
            "coach": {(t, y): 100 + t for t in teams for y in YEARS}, "snap": snap, "games": games, "awards": [],
            "start": {}, "end": {}, "gms": []}


class SmallSeasons(unittest.TestCase):
    """Run the module over two seasons instead of nine."""

    def setUp(self):
        self.saved = (ta.FIRST, ta.LAST, ta.SEASONS)
        ta.FIRST, ta.LAST, ta.SEASONS = YEARS[0], YEARS[-1], list(YEARS)

    def tearDown(self):
        ta.FIRST, ta.LAST, ta.SEASONS = self.saved


class BracketTest(unittest.TestCase):
    def test_sixty_four_outcomes_with_the_right_rounds(self):
        self.assertEqual(len(ta.OUTCOMES), 64)
        for out in ta.OUTCOMES:
            rounds = sorted(out["lost"].values())
            self.assertEqual(rounds, ["CONFERENCE"] + ["DIVISIONAL"] * 2 + ["WILD_CARD"] * 3)
            self.assertNotIn(out["champ"], out["lost"])
            self.assertNotIn(1, [l for r, w, l in out["games"] if r == "WILD_CARD"])     # the 1 seed has a bye

    def test_reseeding(self):
        chalk = next(o for o in ta.OUTCOMES if [g[1] for g in o["games"][:3]] == [2, 3, 4])
        div = sorted(tuple(sorted((w, l))) for r, w, l in chalk["games"] if r == "DIVISIONAL")
        self.assertEqual(div, [(1, 4), (2, 3)])
        upset = next(o for o in ta.OUTCOMES if [g[1] for g in o["games"][:3]] == [7, 3, 4])
        div = sorted(tuple(sorted((w, l))) for r, w, l in upset["games"] if r == "DIVISIONAL")
        self.assertEqual(div, [(1, 7), (3, 4)])               # the 7 seed won, so it visits the 1 seed

    def test_bracket_ok(self):
        ok = {1: "CHAMPION", 2: "CONFERENCE", 3: "DIVISIONAL", 4: "DIVISIONAL", 5: "WILD_CARD", 6: "WILD_CARD",
              7: "WILD_CARD"}
        self.assertTrue(ta.bracket_ok(ok))
        bad = {**ok, 2: "WILD_CARD", 5: "CONFERENCE"}           # 2 and 7 both lost in the Wild Card round
        self.assertFalse(ta.bracket_ok(bad))


class DeriveTest(SmallSeasons):
    def test_clean_facts_and_exact_groups(self):
        teams = league()
        rows, sb = true_history(teams)
        d, problems = ta.derive(facts_from(teams, rows, sb), canon_facts={}, adjust={})
        self.assertEqual(problems, [])
        self.assertEqual(len(d["groups"]), 32)
        self.assertEqual(d["excess"], {"AFC": 0, "NFC": 0})

    def test_bad_totals_are_reported(self):
        teams = league()
        rows, sb = true_history(teams)
        facts = facts_from(teams, rows, sb)
        facts["snap"][(101, YEARS[-1])]["wins"] += 1             # 35 games in two seasons
        _, problems = ta.derive(facts, canon_facts={}, adjust={})
        self.assertTrue(any("over 2 seasons" in p for p in problems))
        facts = facts_from(teams, rows, sb)
        facts["snap"][(101, YEARS[-1])]["playoff_losses"] += 1   # an eighth AFC playoff team, no real record to hold it
        _, problems = ta.derive(facts, canon_facts={}, adjust={})
        self.assertTrue(any("playoff trips" in p for p in problems))

    def test_adjustment_applies(self):
        teams = league()
        rows, sb = true_history(teams)
        facts = facts_from(teams, rows, sb)
        facts["snap"][(101, YEARS[-1])]["losses"] += 1
        _, problems = ta.derive(facts, canon_facts={}, adjust={101: {"losses": -1}})
        self.assertEqual(problems, [])


class SolveTest(SmallSeasons):
    def test_solution_meets_every_fact(self):
        teams = league()
        rows, sb = true_history(teams)
        facts = facts_from(teams, rows, sb)
        canon = {"records": {("T1", 2026): rows[(1, 2026)]["wins"]}}
        d, problems = ta.derive(facts, canon_facts=canon, adjust={})
        self.assertEqual(problems, [])
        targets = {(t, y): 8.5 for t in teams for y in YEARS}
        sol, status, _ = ta.solve(facts, d, targets, time_limit=60, seed=1)
        self.assertIsNotNone(sol, status)
        out = ta.finish(facts, d, sol, 1)
        self.assertEqual(ta.verify(facts, d, out, offset=sol["offset"]), [])
        self.assertEqual(out[(1, 2026)]["wins"], rows[(1, 2026)]["wins"])
        for y in YEARS:
            self.assertEqual(sum(r["points_for"] for (t, yy), r in out.items() if yy == y),
                             sum(r["points_against"] for (t, yy), r in out.items() if yy == y))

    def test_impossible_pin_is_infeasible(self):
        teams = league()
        rows, sb = true_history(teams)
        facts = facts_from(teams, rows, sb)
        d, _ = ta.derive(facts, canon_facts={}, adjust={})
        champ = sb[2026][0]
        pins = [{"team": champ, "season": 2026, "playoffs": False}]   # the Super Bowl winner can't miss the playoffs
        sol, status, _ = ta.solve(facts, d, {}, pins, time_limit=20)
        self.assertIsNone(sol)
        self.assertEqual(status, "INFEASIBLE")


if __name__ == "__main__":
    unittest.main()
