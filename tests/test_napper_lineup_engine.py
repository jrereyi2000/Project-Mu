"""engines/napper_lineup_engine planning functions (no database)."""
import unittest

from engines.napper_lineup_engine import plan_ir, plan_lineup, plan_starters

FMT = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 3, "IR": 1}
OUT_3 = {"status": "O", "weeks_remaining": 3, "is_ir": False}
OUT_1 = {"status": "O", "weeks_remaining": 1, "is_ir": False}
ON_IR = {"status": "IR", "weeks_remaining": 0, "is_ir": True}


def p(pid, pos, proj, ros=None, slot="BN", bye=False, injury=None):
    return {"pid": pid, "name": f"P{pid}", "pos": pos, "proj": proj, "ros": ros if ros is not None else proj * 10,
            "slot": slot, "bye": bye, "injury": injury}


def roster():
    return [
        p(1, "QB", 20, slot="QB"), p(2, "RB", 15, slot="RB"), p(3, "RB", 12, slot="RB"),
        p(4, "WR", 14, slot="WR"), p(5, "WR", 11, slot="WR"), p(6, "TE", 8, slot="TE"),
        p(7, "WR", 9, slot="FLEX"), p(8, "K", 8, slot="K"), p(9, "DEF", 6, slot="DEF"),
        p(10, "RB", 7), p(11, "QB", 10), p(12, "WR", 5),
    ]


class PlanStartersTest(unittest.TestCase):
    def test_healthy_lineup_is_kept(self):
        slots = plan_starters(roster(), FMT)
        self.assertEqual([slots[i] for i in range(1, 10)], ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"])
        self.assertEqual({slots[10], slots[11], slots[12]}, {"BN"})

    def test_injured_and_bye_starters_are_benched_for_available_players(self):
        players = roster()
        players[1]["injury"] = OUT_1           # RB 2 out this week (projection already 0 upstream)
        players[1]["proj"] = 0
        players[3]["bye"] = True               # WR 4 on bye
        players[3]["proj"] = 0
        slots = plan_starters(players, FMT)
        self.assertEqual(slots[2], "BN")
        self.assertEqual(slots[4], "BN")
        self.assertEqual(slots[10], "RB")      # bench RB starts
        self.assertEqual(slots[7], "WR")       # FLEX WR moves into WR
        self.assertEqual(slots[12], "FLEX")    # last WR fills FLEX

    def test_slot_nobody_available_can_fill_takes_best_unavailable_player(self):
        players = roster()
        players[7]["bye"] = True               # only kicker is on bye
        self.assertEqual(plan_starters(players, FMT)[8], "K")


class PlanIrTest(unittest.TestCase):
    def test_eligible_player_goes_to_ir_and_one_week_injury_does_not(self):
        players = roster()
        players[1]["injury"] = OUT_3
        players[2]["injury"] = OUT_1
        to_ir, off_ir, drops = plan_ir(players, FMT)
        self.assertEqual((to_ir, off_ir, drops), ([2], [], []))

    def test_ir_slot_goes_to_the_higher_ros_player(self):
        players = roster()
        players[1]["injury"] = OUT_3           # ros 150
        players[5]["injury"] = ON_IR           # ros 80
        self.assertEqual(plan_ir(players, FMT)[0], [2])

    def test_healed_player_comes_off_ir_and_lowest_ros_bench_player_is_dropped(self):
        players = roster() + [p(13, "RB", 10, slot="IR")]   # healthy again; roster now 13 active > 12
        to_ir, off_ir, drops = plan_ir(players, FMT)
        self.assertEqual(off_ir, [13])
        self.assertEqual(drops, [12])                       # WR 5 proj, lowest ROS bench player

    def test_drop_never_removes_a_needed_starter(self):
        players = roster() + [p(13, "RB", 10, slot="IR"), p(14, "K", 1, ros=1, slot="BN")]
        players[7]["ros"] = 0                               # K 8: lowest ROS, but projects below K 14
        players[7]["proj"] = 0.5
        drops = plan_ir(players, FMT)[2]
        self.assertEqual(sorted(drops), [8, 12])            # K 14 starts, so K 8 can go; one kicker is kept


class PlanLineupTest(unittest.TestCase):
    def test_full_plan_moves_injured_starter_to_ir_and_fills_his_slot(self):
        players = roster()
        players[0]["injury"] = ON_IR           # starting QB on IR
        players[0]["proj"] = 0
        plan = plan_lineup(players, FMT)
        self.assertEqual(plan["to_ir"], [1])
        self.assertEqual(plan["slots"][1], "IR")
        self.assertEqual(plan["slots"][11], "QB")
        self.assertEqual(plan["drops"], [])


if __name__ == "__main__":
    unittest.main()
