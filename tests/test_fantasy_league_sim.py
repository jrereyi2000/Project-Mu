"""Unit tests for the simulated fantasy leagues (engines/fantasy_league_sim.py). No database required.

Run: python3 -m unittest tests.test_fantasy_league_sim
"""
import random
import unittest
from collections import Counter, defaultdict

from engines import fantasy_league_sim as sim


def synthetic_board(n_teams=32):
    """A board shaped like the real one: many WR/HB, fewer TE/QB, scores falling with depth."""
    board, pid = [], 1
    for pos, count, top, step in (("WR", 150, 136, 0.45), ("HB", 100, 134, 0.5), ("TE", 90, 113, 0.6),
                                  ("QB", 80, 133, 0.8), ("K", 32, 60, 0.3), ("DEF", 32, 65, 0.3)):
        for i in range(count):
            board.append({"player_id": pid, "position": pos, "team_id": 1 + pid % n_teams,
                          "draft_score": round(top - step * i - (20 if pos == "QB" and i > 0 else 0), 2)})
            pid += 1
    return sorted(board, key=lambda p: -p["draft_score"])


class StrategyMixTests(unittest.TestCase):
    def test_zero_rb_example_moves_partway(self):
        prev = dict(sim.DEFAULT_MIX)
        titles = {"BPA": 10, "HERO_RB": 5, "ZERO_RB": 60, "RB_HEAVY": 10, "EARLY_QB": 10, "EARLY_TE": 5}
        new = sim.adapt_mix(prev, titles)
        self.assertAlmostEqual(sum(new.values()), 1.0, places=9)
        self.assertGreater(new["ZERO_RB"], prev["ZERO_RB"])
        self.assertLess(new["ZERO_RB"], 0.30)          # partial move, nowhere near 60%
        self.assertAlmostEqual(new["ZERO_RB"], 0.248, places=2)

    def test_average_results_keep_the_mix(self):
        prev = dict(sim.DEFAULT_MIX)
        titles = {s: round(100 * v) for s, v in prev.items()}
        new = sim.adapt_mix(prev, titles)
        for s in sim.STRATEGIES:
            self.assertAlmostEqual(new[s], prev[s], places=6)

    def test_floor_and_no_titles(self):
        prev = dict(sim.DEFAULT_MIX)
        new = sim.adapt_mix(prev, {"BPA": 100})
        self.assertTrue(all(v >= sim.MIX_FLOOR - 1e-9 for v in new.values()))
        self.assertAlmostEqual(sum(new.values()), 1.0, places=9)
        unchanged = sim.adapt_mix(prev, {})
        for s in sim.STRATEGIES:
            self.assertAlmostEqual(unchanged[s], prev[s], places=9)

    def test_seats_drawn_independently(self):
        rng = random.Random(1)
        draws = Counter(s for _ in range(2000) for s in sim.assign_strategies(rng, sim.DEFAULT_MIX))
        self.assertAlmostEqual(draws["BPA"] / sum(draws.values()), 0.35, delta=0.02)


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = synthetic_board()
        cls.group = {p["player_id"]: sim.GROUP[p["position"]] for p in cls.board}
        cls.levels = sim.replacement_levels(cls.board)

    def draft(self, strategies, seed=7):
        return sim.draft_league(random.Random(seed), self.board, strategies, self.levels)

    def test_rosters_are_complete_and_legal(self):
        picks = self.draft(["BPA"] * 6 + ["ZERO_RB", "HERO_RB", "RB_HEAVY", "EARLY_QB", "EARLY_TE", "BPA"])
        self.assertEqual(len(picks), sim.TEAMS * sim.ROUNDS)
        self.assertEqual(len({p[3] for p in picks}), len(picks))       # nobody drafted twice
        rosters = defaultdict(Counter)
        for _, _, team, pid in picks:
            rosters[team][self.group[pid]] += 1
        for counts in rosters.values():
            self.assertEqual(sum(counts.values()), sim.ROUNDS)
            for g, n in sim.LINEUP.items():
                self.assertGreaterEqual(counts[g], n)
                self.assertLessEqual(counts[g], sim.DRAFT_CAP[g])

    def test_snake_order(self):
        picks = self.draft(["BPA"] * 12)
        self.assertEqual([p[2] for p in picks[:12]], list(range(1, 13)))
        self.assertEqual([p[2] for p in picks[12:24]], list(range(12, 0, -1)))

    def test_strategies_shape_early_rounds(self):
        strategies = ["ZERO_RB", "HERO_RB", "RB_HEAVY", "EARLY_QB", "EARLY_TE", "BPA"] * 2
        picks = self.draft(strategies)
        early = defaultdict(Counter)
        for _, rnd, team, pid in picks:
            if rnd <= 3:
                early[(team, "r3")][self.group[pid]] += 1
            if rnd <= 4:
                early[(team, "r4")][self.group[pid]] += 1
            if rnd <= 5:
                early[(team, "r5")][self.group[pid]] += 1
        for team, s in enumerate(strategies, start=1):
            if s == "ZERO_RB":
                self.assertEqual(early[(team, "r4")]["RB"], 0)
            if s == "HERO_RB":
                self.assertLessEqual(early[(team, "r5")]["RB"], 1)
            if s == "RB_HEAVY":
                self.assertGreaterEqual(early[(team, "r4")]["RB"], 2)
            if s == "EARLY_QB":
                self.assertGreaterEqual(early[(team, "r3")]["QB"], 1)
            if s == "EARLY_TE":
                self.assertGreaterEqual(early[(team, "r3")]["TE"], 1)

    def test_drafts_are_reproducible(self):
        a, _ = sim.run_drafts(2035, self.board, sim.DEFAULT_MIX, leagues=3)
        b, _ = sim.run_drafts(2035, self.board, sim.DEFAULT_MIX, leagues=3)
        self.assertEqual(a, b)


class AdpTests(unittest.TestCase):
    def test_undrafted_counts_as_pick_after_last_round(self):
        picks = [(1, 5, 1, 5, 10), (2, 15, 2, 4, 10)]   # player 10 drafted in 2 of 4 leagues
        adp = sim.compute_adp(picks, [10, 11], leagues=4)
        self.assertEqual(adp[10]["adp"], round((5 + 15 + 2 * sim.UNDRAFTED_PICK) / 4, 1))
        self.assertEqual((adp[10]["min_pick"], adp[10]["max_pick"], adp[10]["draft_pct"]), (5, 15, 50.0))
        self.assertEqual(adp[11], {"adp": float(sim.UNDRAFTED_PICK), "min_pick": None, "max_pick": None,
                                   "draft_pct": 0.0})


class ScheduleAndLineupTests(unittest.TestCase):
    def test_round_robin(self):
        weeks = sim.round_robin(12, 14, random.Random(3))
        for pairs in weeks:
            self.assertEqual(sorted(t for p in pairs for t in p), list(range(1, 13)))
        met = Counter(frozenset(p) for pairs in weeks[:11] for p in pairs)
        self.assertEqual(len(met), 66)                   # everyone meets everyone once in 11 weeks
        self.assertEqual(weeks[11], weeks[0])

    def test_lineup_fills_slots_and_flex(self):
        group = {1: "QB", 2: "QB", 3: "RB", 4: "RB", 5: "RB", 6: "WR", 7: "WR", 8: "WR", 9: "TE"}
        value = {1: 20, 2: 25, 3: 10, 4: 12, 5: 11, 6: 9, 7: 15, 8: 14, 9: 5}
        lineup = sim.set_lineup(list(group), value, group)
        self.assertEqual(lineup[:6], [2, 4, 5, 7, 8, 9])
        self.assertEqual(lineup[6], 3)                   # FLEX: best remaining RB/WR/TE

    def test_playoff_tie_goes_to_higher_seed(self):
        winners, losers = sim.playoff_round([(3, 6)], {3: 100.0, 6: 100.0}, {3: 3, 6: 6})
        self.assertEqual((winners, losers), ([3], [6]))


class SeasonTests(unittest.TestCase):
    def test_full_season_bracket_and_usage(self):
        board = synthetic_board()
        picks, teams = sim.run_drafts(2035, board, sim.DEFAULT_MIX, leagues=2)
        group_of = {p["player_id"]: sim.GROUP[p["position"]] for p in board}
        team_of = {p["player_id"]: p["team_id"] for p in board}
        rng = random.Random(11)
        opponent = {}
        for week in range(1, 19):
            order = list(range(1, 33))
            rng.shuffle(order)
            bye = set(order[:4]) if 6 <= week <= 14 else set()
            playing = [t for t in order if t not in bye]
            for a, b in zip(playing[::2], playing[1::2]):
                opponent[(a, week)] = b
                opponent[(b, week)] = a
        points = {pid: {w: max(0.0, p["draft_score"] / 6 + rng.gauss(0, 5)) for w in range(1, 18)
                        if (p["team_id"], w) in opponent} for pid, p in zip(group_of, board)}
        proj = {w: {pid: (p["draft_score"] / 6 if (team_of[pid], w) in opponent else 0.0,
                          p["draft_score"] / 6 * (19 - w), p["draft_score"] / 6) for pid, p in zip(group_of, board)}
                for w in range(2, 19)}
        adp = sim.compute_adp(picks, list(group_of), leagues=2)
        data = sim.WeekData(group_of, team_of, points, opponent, proj,
                            {pid: a["adp"] for pid, a in adp.items()}, last_complete=17)
        leagues = {}
        for t in teams:
            st = leagues.setdefault(t["league"], sim.LeagueState(rosters={}, managers={}))
            st.managers[t["team"]] = t
            st.rosters[t["team"]] = []
        for league, _, _, team, pid in picks:
            leagues[league].rosters[team].append(pid)

        usage = defaultdict(lambda: [0, 0])
        for league, state in leagues.items():
            sim.simulate_league(league, 2035, state, data, 17, usage)
            finishes = Counter(state.finish.values())
            self.assertEqual(finishes, Counter({"CHAMPION": 1, "RUNNER_UP": 1, "SEMIFINAL": 2,
                                                "QUARTERFINAL": 2, "MISSED": 6}))
            self.assertEqual(sorted(state.seeds.values()), list(range(1, 13)))
            self.assertEqual(sorted(sim.final_ranks(state).values()), list(range(1, 13)))
            for team, r in state.records.items():
                self.assertEqual(r["w"] + r["l"] + r["t"], sim.REGULAR_WEEKS)
                self.assertEqual(len(state.rosters[team]), sim.ROUNDS)
            champ = next(t for t, f in state.finish.items() if f == "CHAMPION")
            self.assertLessEqual(state.seeds[champ], 6)
        for week in range(1, 18):
            self.assertEqual(sum(r for (w, _), (r, _) in usage.items() if w == week), 2 * 12 * sim.ROUNDS)
            starters = sum(sim.LINEUP.values()) + 1  # 9 starters: 1 QB, 2 RB, 2 WR, 1 TE, 1 K, 1 DEF + 1 FLEX
            self.assertEqual(sum(s for (w, _), (_, s) in usage.items() if w == week), 2 * 12 * starters)


if __name__ == "__main__":
    unittest.main()
