"""engines/napper_waiver_engine planning: AI pickups and claim rounds (no database)."""
import unittest

from engines.napper_waiver_engine import (
    ACTIVITY, PERSONA_ACTIVITY, REACTIVITY, ai_next_claim, lowest_spare, manager_traits, player_value,
    resolve_waivers, week_plan,
)

# 9 starters + 3 BN = 12 active players.
FMT = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 3, "IR": 1}
OUT_1 = {"status": "O", "weeks_remaining": 1, "is_ir": False}


def p(pid, pos, proj, ros=None, slot="BN", bye=False, injury=None):
    return {"pid": pid, "name": f"P{pid}", "pos": pos, "proj": proj, "ros": ros if ros is not None else proj * 10,
            "slot": slot, "bye": bye, "injury": injury}


def ai(adds, stream=True, reactivity=0.0):
    return {"adds": adds, "stream": stream, "reactivity": reactivity}


def roster(full=True):
    players = [
        p(1, "QB", 20, slot="QB"), p(2, "RB", 15, slot="RB"), p(3, "RB", 12, slot="RB"),
        p(4, "WR", 14, slot="WR"), p(5, "WR", 11, slot="WR"), p(6, "TE", 8, slot="TE"),
        p(7, "WR", 9, slot="FLEX"), p(8, "K", 8, slot="K"), p(9, "DEF", 6, slot="DEF"),
        p(10, "RB", 7), p(11, "QB", 10),
    ]
    return players + ([p(12, "WR", 2)] if full else [])


class AiNextClaimTest(unittest.TestCase):
    def test_need_signs_best_healthy_qb_and_drops_lowest_spare(self):
        team = [x for x in roster() if x["pos"] != "QB"] + [p(13, "RB", 1), p(14, "RB", 3)]  # no QB, 12 players
        pool = [p(100, "QB", 18, ros=150, injury=OUT_1), p(101, "QB", 12, ros=120), p(102, "QB", 14, ros=110)]
        claim = ai_next_claim(team, pool, FMT, adds_left=0)       # NEED ignores the weekly cap
        self.assertEqual(claim, {"add": 101, "drop": 13, "reason": "NEED"})

    def test_need_without_drop_when_there_is_room(self):
        team = [x for x in roster(full=False) if x["pos"] != "QB"]
        claim = ai_next_claim(team, [p(101, "QB", 12)], FMT, adds_left=2)
        self.assertEqual(claim, {"add": 101, "drop": None, "reason": "NEED"})

    def test_stream_a_kicker_when_the_only_one_is_on_bye(self):
        team = roster()
        team[7]["bye"] = True
        pool = [p(200, "K", 7, ros=60), p(201, "K", 9, ros=50, bye=True)]
        claim = ai_next_claim(team, pool, FMT, adds_left=2)
        self.assertEqual(claim, {"add": 200, "drop": 12, "reason": "STREAM"})

    def test_no_stream_when_the_drop_is_worth_more(self):
        team = roster()
        team[7]["bye"] = True
        team[11]["ros"] = 500                                     # the spare is worth more than any kicker
        self.assertIsNone(ai_next_claim(team, [p(200, "K", 7, ros=60)], FMT, adds_left=2))

    def test_upgrade_fills_an_open_spot(self):
        claim = ai_next_claim(roster(full=False), [p(300, "WR", 6, ros=40), p(301, "RB", 5, ros=70)], FMT, 2)
        self.assertEqual(claim, {"add": 301, "drop": None, "reason": "UPGRADE"})

    def test_upgrade_swap_needs_the_margin(self):
        team = roster()                                           # spare P12: ros 20 -> needs > 24
        self.assertIsNone(ai_next_claim(team, [p(300, "WR", 3, ros=24)], FMT, 2))
        self.assertEqual(ai_next_claim(team, [p(300, "WR", 3, ros=25)], FMT, 2),
                         {"add": 300, "drop": 12, "reason": "UPGRADE"})

    def test_upgrade_respects_position_cap_and_weekly_cap(self):
        team = roster(full=False)
        self.assertIsNone(ai_next_claim(team, [p(400, "K", 9, ros=200)], FMT, 2))   # already has a K
        self.assertIsNone(ai_next_claim(team, [p(301, "RB", 5, ros=70)], FMT, 0))  # cap used up

    def test_no_backup_qb_upgrades_in_one_qb_leagues(self):
        team = [x for x in roster(full=False) if x["pid"] != 11]   # one QB, two open spots
        self.assertEqual(ai_next_claim(team, [p(500, "QB", 15, ros=250), p(301, "RB", 5, ros=70)], FMT, 2)["add"], 301)
        superflex = dict(FMT, SUPERFLEX=1, BN=2)
        self.assertEqual(ai_next_claim(team, [p(500, "QB", 15, ros=250)], superflex, 2)["add"], 500)

    def test_injured_free_agents_are_not_upgrades(self):
        self.assertIsNone(ai_next_claim(roster(full=False), [p(301, "RB", 0, ros=90, injury=OUT_1)], FMT, 2))

    def test_stream_skipped_in_weeks_the_manager_does_not_stream(self):
        team = roster()
        team[7]["bye"] = True
        pool = [p(200, "K", 7, ros=60)]
        self.assertIsNone(ai_next_claim(team, pool, FMT, adds_left=2, stream=False))
        self.assertEqual(ai_next_claim(team, pool, FMT, adds_left=0)["reason"], "STREAM")   # not capped

    def test_values_use_scaled_ros_and_reactivity(self):
        cold = p(300, "WR", 3, ros=100)
        cold.update(base=40, hot=-0.5)
        hot = p(301, "WR", 3, ros=60)
        hot.update(base=30, hot=1.0)
        self.assertEqual(player_value(cold), 40)
        self.assertEqual(player_value(hot, 0.5), 45)
        team = roster(full=False)
        self.assertEqual(ai_next_claim(team, [cold, hot], FMT, 2)["add"], 300)
        self.assertEqual(ai_next_claim(team, [cold, hot], FMT, 2, reactivity=0.5)["add"], 301)

    def test_reactivity_changes_who_is_dropped(self):
        team = roster()
        team[10].update(base=30, hot=-0.5)    # P11, backup QB
        team[11].update(base=20, hot=1.0)     # P12
        team[9].update(base=50, hot=0.0)      # P10
        self.assertEqual(lowest_spare(team, FMT)["pid"], 12)
        self.assertEqual(lowest_spare(team, FMT, reactivity=0.5)["pid"], 11)

    def test_lowest_spare_keeps_starters_and_this_runs_adds(self):
        team = roster()
        self.assertEqual(lowest_spare(team, FMT)["pid"], 12)
        self.assertEqual(lowest_spare(team, FMT, keep={12})["pid"], 10)


class ResolveWaiversTest(unittest.TestCase):
    def test_priority_order_decides_a_contested_player(self):
        rosters = {1: roster(full=False), 2: roster(full=False)}
        pool = {301: p(301, "RB", 5, ros=70)}
        r = resolve_waivers([2, 1], rosters, pool, FMT, {1: ai(2)}, {2: [{"id": 9, "add": 301, "drop": None}]})
        self.assertEqual(r["outcomes"], {9: ("WON", None)})
        self.assertEqual([(t["team"], t["add"], t["reason"]) for t in r["transactions"]], [(2, 301, "CLAIM")])
        self.assertIn(301, {x["pid"] for x in rosters[2]})

    def test_lost_claim_falls_through_to_the_next_one(self):
        rosters = {1: roster(full=False), 2: roster(full=False)}
        pool = {301: p(301, "RB", 5, ros=70), 302: p(302, "WR", 4, ros=30)}
        claims = {2: [{"id": 9, "add": 301, "drop": None}, {"id": 10, "add": 302, "drop": None}]}
        r = resolve_waivers([1, 2], rosters, pool, FMT, {1: ai(1)}, claims)
        self.assertEqual(r["outcomes"][9], ("LOST", "claimed by another team"))
        self.assertEqual(r["outcomes"][10], ("WON", None))

    def test_claim_on_a_full_roster_without_a_drop_is_invalid(self):
        rosters = {2: roster()}
        r = resolve_waivers([2], rosters, {301: p(301, "RB", 5)}, FMT, {},
                            {2: [{"id": 9, "add": 301, "drop": None}]})
        self.assertEqual(r["outcomes"][9][0], "INVALID")
        self.assertEqual(r["transactions"], [])

    def test_claim_with_a_drop_swaps_players(self):
        rosters = {2: roster()}
        r = resolve_waivers([2], rosters, {301: p(301, "RB", 5)}, FMT, {},
                            {2: [{"id": 9, "add": 301, "drop": 12}]})
        self.assertEqual(r["outcomes"][9], ("WON", None))
        self.assertEqual({x["pid"] for x in rosters[2]} - {x["pid"] for x in roster()}, {301})
        self.assertNotIn(12, {x["pid"] for x in rosters[2]})

    def test_one_add_per_turn_and_ai_weekly_cap(self):
        rosters = {1: roster(full=False)}
        rosters[1] = rosters[1][:-1]                              # two open spots
        pool = {300 + i: p(300 + i, "RB", 5, ros=70 - i) for i in range(4)}
        r = resolve_waivers([1], rosters, pool, FMT, {1: ai(2)}, {})
        self.assertEqual([t["add"] for t in r["transactions"]], [300, 301])

    def test_dropped_players_are_not_claimable_in_the_same_run(self):
        rosters = {1: roster(), 2: roster(full=False)}
        pool = {301: p(301, "RB", 5, ros=70)}
        r = resolve_waivers([1, 2], rosters, pool, FMT, {1: ai(1), 2: ai(2)}, {})
        # team 1 swaps P12 for 301; team 2 cannot pick up the dropped P12
        self.assertEqual([(t["team"], t["add"], t["drop"]) for t in r["transactions"]], [(1, 301, 12)])


class ManagerTest(unittest.TestCase):
    def test_traits_are_fixed_per_team_and_in_range(self):
        self.assertEqual(manager_traits(42, "BPA"), manager_traits(42, "BPA"))
        for tid in range(1, 200):
            t = manager_traits(tid, None)
            self.assertTrue(ACTIVITY[0] <= t["activity"] <= ACTIVITY[1])
            self.assertTrue(REACTIVITY[0] <= t["reactivity"] <= REACTIVITY[1])
            lo, hi = PERSONA_ACTIVITY["WAIVER_HAWK"]
            self.assertTrue(lo <= manager_traits(tid, "WAIVER_HAWK")["activity"] <= hi)

    def test_week_plan_budget_follows_activity(self):
        plans = [week_plan(1, w, 7, None) for w in range(2, 200)]
        self.assertEqual(week_plan(1, 5, 7, None), week_plan(1, 5, 7, None))
        self.assertTrue(all(0 <= x["adds"] <= 2 for x in plans))
        hawk = [week_plan(1, w, 7, "WAIVER_HAWK")["adds"] for w in range(2, 200)]
        self.assertEqual(max(hawk), 3)
        lazy = min(range(1, 400), key=lambda t: manager_traits(t, None)["activity"])
        busy = max(range(1, 400), key=lambda t: manager_traits(t, None)["activity"])
        avg = lambda t: sum(week_plan(1, w, t, None)["adds"] for w in range(2, 200)) / 198
        self.assertLess(avg(lazy), avg(busy))


if __name__ == "__main__":
    unittest.main()
