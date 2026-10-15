"""Round 1 draft order with playoff-exit brackets (engines/draft_order.py), on a synthetic 32-team league."""
import unittest

from engines.draft_order import compute_draft_order

# Team t (1-32) wins t-1 of 32 regular-season games, so team 1 has the worst record and team 32 the best.
# Odd teams are AFC, even NFC.
TEAMS = {t: {"name": f"T{t}", "abbreviation": f"T{t}", "conference": "AFC" if t % 2 else "NFC"} for t in range(1, 33)}


def regular_season():
    games = []
    for t in range(1, 33):
        for i in range(32):
            won = i < t - 1
            games.append(dict(home_team_id=t, away_team_id=0, home_score=20 if won else 10,
                              away_score=10 if won else 20, status="COMPLETED", week=i % 18 + 1,
                              game_type="REGULAR_SEASON"))
    return games


def game(rnd, winner, loser, played=True):
    return dict(home_team_id=winner, away_team_id=loser, home_score=27 if played else None,
                away_score=17 if played else None, status="COMPLETED" if played else "SCHEDULED",
                week={"WILD_CARD": 19, "DIVISIONAL": 20, "CONFERENCE": 21, "SUPER_BOWL": 23}[rnd], game_type=rnd)


# The 14-team field: 7 per conference. Byes: 32 (NFC) and 31 (AFC).
WC = [(30, 19), (28, 21), (26, 23), (29, 20), (27, 22), (25, 24)]
DIV = [(32, 25), (30, 26), (31, 27), (29, 28)]
CONF = [(32, 30), (31, 29)]


def order(games):
    return compute_draft_order(TEAMS, games)


class DraftOrderTest(unittest.TestCase):
    def picks(self, result):
        return {p["pick"]: (p["team_id"], p["locked"]) for p in result["round_1_order"]}

    def test_regular_season_is_provisional(self):
        r = order(regular_season())
        self.assertFalse(r["playoff_field_known"])
        self.assertEqual(self.picks(r)[1], (1, False))
        self.assertEqual(self.picks(r)[32], (32, False))

    def test_wild_card_week_finds_the_byes(self):
        # Wild Card games scheduled, none played: the 18 non-playoff teams are locked at 1-18. The byes (31, 32)
        # are in no game yet but are counted in the field, so no playoff team lands in 1-18.
        r = order(regular_season() + [game("WILD_CARD", w, l, played=False) for w, l in WC])
        p = self.picks(r)
        self.assertEqual([p[i] for i in range(1, 19)], [(t, True) for t in range(1, 19)])
        self.assertEqual({p[i][0] for i in range(19, 33)}, set(range(19, 33)))
        self.assertTrue(all(not p[i][1] for i in range(19, 33)))

    def test_after_wild_card(self):
        r = order(regular_season() + [game("WILD_CARD", w, l) for w, l in WC])
        p = self.picks(r)
        self.assertEqual([p[i] for i in range(19, 25)], [(t, True) for t in (19, 20, 21, 22, 23, 24)])
        self.assertEqual({p[i][0] for i in range(25, 33)}, {25, 26, 27, 28, 29, 30, 31, 32})
        self.assertEqual(r["round_1_order"][24]["selection_status"], "Still Alive (provisional)")

    def test_round_in_progress_is_not_locked(self):
        played = [game("WILD_CARD", w, l) for w, l in WC[:3]] + [game("WILD_CARD", w, l, played=False) for w, l in WC[3:]]
        p = self.picks(order(regular_season() + played))
        self.assertEqual([p[i] for i in range(19, 22)], [(19, False), (21, False), (23, False)])

    def test_full_playoffs(self):
        games = (regular_season() + [game("WILD_CARD", w, l) for w, l in WC] + [game("DIVISIONAL", w, l) for w, l in DIV]
                 + [game("CONFERENCE", w, l) for w, l in CONF] + [game("SUPER_BOWL", 31, 32)])
        r = order(games)
        p = self.picks(r)
        self.assertEqual([p[i][0] for i in range(25, 29)], [25, 26, 27, 28])
        self.assertEqual([p[i][0] for i in (29, 30)], [29, 30])
        self.assertEqual((p[31], p[32]), ((32, True), (31, True)))     # better record, but lost the Super Bowl
        self.assertTrue(all(locked for _, locked in p.values()))
        self.assertEqual(r["rounds_complete"], ["WILD_CARD", "DIVISIONAL", "CONFERENCE", "SUPER_BOWL"])

    def test_past_season_with_only_its_super_bowl(self):
        p = self.picks(order([game("SUPER_BOWL", 5, 9)]))
        self.assertEqual((p[31], p[32]), ((9, True), (5, True)))
        self.assertTrue(all(locked for _, locked in p.values()))

    def test_ties_count_half_and_point_differential_breaks_ties(self):
        teams = {1: dict(name="X", conference="AFC"), 2: dict(name="Y", conference="AFC"), 3: dict(name="Z", conference="AFC")}
        g = lambda h, a, hs, as_: dict(home_team_id=h, away_team_id=a, home_score=hs, away_score=as_,
                                       status="COMPLETED", week=1, game_type="REGULAR_SEASON")
        # all three finish at .500: X 1-1 (-17), Y 0-0-2 (0), Z 1-1-2 (+17); the worst differential picks first
        games = [g(1, 3, 20, 10), g(2, 3, 10, 10), g(2, 3, 14, 14), g(1, 3, 3, 30)]
        r = compute_draft_order(teams, games)
        self.assertEqual([(p["team"], p["win_pct"]) for p in r["round_1_order"]], [("X", 0.5), ("Y", 0.5), ("Z", 0.5)])
        self.assertEqual(r["round_1_order"][1]["record"], "0-0-2")

if __name__ == "__main__":
    unittest.main()
