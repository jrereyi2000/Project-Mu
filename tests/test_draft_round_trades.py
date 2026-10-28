"""Draft-day trades at the start of Rounds 2-7 (docs/done/team_management/06_DRAFT_ROUND_TRADES_PLAN.md): the pure parts of
engines/draft_room.start_round and engines/gm_trade_engine.run_draft_round, on synthetic data (no database)."""
import unittest

from engines import draft_room as dr
from engines import gm_trade_engine as ge
from engines.gm_rationale import template, validate_rationale

DS = 2037


def slots(n, owners=None):
    """Round n's 32 slots; slot i is owned by team i unless owners says otherwise."""
    owners = owners or {}
    return [{"pick": (n - 1) * 32 + i, "round_pick": i, "key": (DS, n, i), "original_team_id": i,
             "owner_team_id": owners.get(i, i)} for i in range(1, 33)]


def pool(n=60):
    return [f"p{i}" for i in range(1, n + 1)]          # Madden's board order: p1 goes first


class PoolTest(unittest.TestCase):
    def test_estimated_pool_drops_the_simmed_rounds_by_madden_board(self):
        available = {f"p{i}" for i in range(1, 101)} | {"unprojected"}
        projected = {f"p{i}": i for i in range(1, 101)}
        self.assertEqual(dr.estimated_pool(available, projected, 2)[:2], ["p1", "p2"])        # Round 1 as recorded
        self.assertEqual(dr.estimated_pool(available, projected, 3)[0], "p33")              # Round 2 assumed by board
        self.assertEqual(dr.estimated_pool(available, projected, 3)[-1], "unprojected")      # unprojected last

    def test_team_target_board_then_need_then_consensus(self):
        cons = {"a": {"rank": 1}, "b": {"rank": 2}, "c": {"rank": 3}}
        groups = {"a": "QB", "b": "CB", "c": "WR"}
        self.assertEqual(dr.team_target([("c", 80, 1)], cons, {"a", "b", "c"}, [], groups), "c")
        self.assertEqual(dr.team_target([("z", 80, 1)], cons, {"a", "b", "c"}, ["CB", "WR"], groups), "b")
        self.assertEqual(dr.team_target([], cons, {"a", "b", "c"}, ["K"], groups), "a")


class CandidatesTest(unittest.TestCase):
    def test_trade_up_when_the_target_goes_well_before_the_pick(self):
        targets = {t: f"p{t}" for t in range(1, 33)}           # everyone's target goes at its own slot
        targets[20] = "p10"                                     # team 20's target goes at 10
        cands = dr.round_candidates(slots(3), pool(), targets)
        up = [c for c in cands if c["move"] == "UP"]
        # it calls the owners of the SELLER_WINDOW slots just ahead of its target's, nearest first
        self.assertEqual([c["seller"] for c in up], [9, 8, 7, 6])
        c = up[0]
        self.assertEqual((c["buyer"], c["seller_pick"], c["overall"], c["gap"]), (20, (DS, 3, 9), 64 + 9, 10))

    def test_no_move_when_the_target_lasts(self):
        targets = {t: f"p{t + 1}" for t in range(1, 33)}       # goes one pick after: close enough to wait
        self.assertEqual(dr.round_candidates(slots(3), pool(), targets), [])

    def test_trade_down_to_a_later_team_that_needs_to_move_up(self):
        targets = {t: f"p{t}" for t in range(1, 33)}
        targets[5] = "p20"                                      # team 5's target lasts to 20: it can move back
        targets[12] = "p6"                                      # team 12's goes at 6: it needs pick 5
        cands = dr.round_candidates(slots(2), pool(), targets)
        down = [c for c in cands if c["move"] == "DOWN"]
        self.assertEqual(len(down), 1)
        self.assertEqual((down[0]["seller"], down[0]["buyer"], down[0]["buyer_pick"]), (5, 12, (DS, 2, 12)))

    def test_no_trade_down_without_a_partner(self):
        targets = {t: f"p{t}" for t in range(1, 33)}
        targets[5] = "p20"
        self.assertEqual([c for c in dr.round_candidates(slots(2), pool(), targets) if c["move"] == "DOWN"], [])

    def test_traded_pick_moves_with_its_owner(self):
        targets = {t: f"p{t}" for t in range(1, 33)}
        targets[20] = "p10"
        cands = dr.round_candidates(slots(3, owners={9: 25}), pool(), targets)
        self.assertEqual(cands[0]["seller"], 25)                # slot 9 now belongs to team 25

    def test_biggest_moves_first(self):
        targets = {t: f"p{t}" for t in range(1, 33)}
        targets[20], targets[30] = "p15", "p8"
        buyers = [c["buyer"] for c in dr.round_candidates(slots(3), pool(), targets)]
        self.assertEqual(buyers, [30] * 4 + [20] * 4)            # a team's calls stay together


class RoundFlowTest(unittest.TestCase):
    def test_rounds_start_in_order(self):
        self.assertEqual(dr.next_round({}), 2)
        self.assertEqual(dr.next_round({2: 3, 3: 0}), 4)
        self.assertIsNone(dr.next_round({7: 1}))
        self.assertIn("Start Round 4", dr.round_steps(3)[-1])
        self.assertIn("draft-results", dr.round_steps(7)[-1])

    def test_earlier_rounds_expire_when_a_round_starts(self):
        props = [{"id": 1, "status": "PROPOSED", "trade_window": "DRAFT_DAY", "flags": ["pick:12"]},          # Round 1
                 {"id": 2, "status": "APPROVED", "trade_window": "DRAFT_DAY", "flags": ["round:2", "pick:40"]},
                 {"id": 3, "status": "PROPOSED", "trade_window": "DRAFT_DAY", "flags": ["round:3", "pick:70"]}]
        got = dict(ge.expire_stale(props, ge.OFFSEASON_WEEK, "DRAFT_DAY", round_no=3))
        self.assertEqual(set(got), {1, 2})
        self.assertEqual(got[2], "Round 2 was simmed")
        self.assertEqual(set(dict(ge.expire_stale(props, ge.OFFSEASON_WEEK, "DRAFT_DAY", round_no=2))), {1})

    def test_payment_picks(self):
        self.assertTrue(ge.round_payment_ok((DS, 3, 1), DS, 3))
        self.assertFalse(ge.round_payment_ok((DS, 2, 1), DS, 3))         # already made
        self.assertTrue(ge.round_payment_ok((DS + 1, 4, 1), DS, 3))
        self.assertFalse(ge.round_payment_ok((DS + 1, 3, 1), DS, 3))     # next year: Rounds 4-7 only
        self.assertFalse(ge.round_payment_ok((DS + 2, 7, 1), DS, 3))

    def test_proposal_count_is_seeded_and_capped(self):
        self.assertEqual(ge.round_proposal_count(2036, 3, 0, 0), ge.round_proposal_count(2036, 3, 0, 0))
        counts = [ge.round_proposal_count(s, r, 0, 0) for s in range(1000) for r in range(2, 8)]
        self.assertLessEqual(max(counts), ge.ROUND_MAX_PROPOSALS)
        # at the prior approval rate (0.6), about 30 executed over Rounds 2-7 when candidates don't run out
        executed = sum(counts) / 1000 * ge.p_exec(0, 0)
        self.assertTrue(24 <= executed <= 36, executed)


class RoundDealsTest(unittest.TestCase):
    class FlatValuer:
        def value(self, x, tid):
            return 1000 - 10 * x["pick"][1]

    def books(self):
        return {"picks": {(DS, 3, 9): 9, (DS, 3, 20): 20, (DS, 5, 20): 20, (DS, 2, 20): 20, (DS + 1, 2, 20): 20,
                          (DS + 1, 6, 20): 20}, "booked_picks": set()}

    def test_trade_up_pays_only_with_payable_picks(self):
        cand = {"move": "UP", "buyer": 20, "seller": 9, "seller_pick": (DS, 3, 9), "buyer_pick": (DS, 3, 20)}
        deals = ge.round_deals(self.books(), self.FlatValuer(), cand, DS, 3)
        paid = {tuple(x["pick"]) for d in deals for x in d["assets"] if x["from"] == 20}
        self.assertEqual(paid, {(DS, 3, 20), (DS, 5, 20), (DS + 1, 6, 20)})
        self.assertTrue(all(d["a"] == 20 and d["draft_move"] == "UP" for d in deals))

    def test_trade_down_takes_the_buyers_pick_back(self):
        cand = {"move": "DOWN", "buyer": 20, "seller": 9, "seller_pick": (DS, 3, 9), "buyer_pick": (DS, 3, 20)}
        deals = ge.round_deals(self.books(), self.FlatValuer(), cand, DS, 3)
        self.assertTrue(deals)
        self.assertTrue(all((DS, 3, 20) in {tuple(x["pick"]) for x in d["assets"]} for d in deals))
        self.assertTrue(all(d["a"] == 9 for d in deals))         # the seller initiates a trade-down


class RationaleTest(unittest.TestCase):
    def sheet(self, move):
        team = lambda ab, nick: {"abbr": ab, "team": f"City {nick}", "nickname": nick, "needs": ["cornerback"],
                                 "gm": "GM", "direction": "RETOOL", "cap_before": "$1.00M", "cap_after": "$1.00M",
                                 "urgent": None}
        return {"trigger": "DRAFT", "draft_move": move, "draft_round": 3,
                "teams": {"a": team("AAA", "Alphas"), "b": team("BBB", "Betas")},
                "assets": [{"kind": "pick", "name": "2037 R3 (BBB)", "from": "BBB", "to": "AAA"},
                           {"kind": "pick", "name": "2037 R3 (AAA)", "from": "AAA", "to": "BBB"}]}

    def test_template_words_the_move(self):
        self.assertEqual(template(self.sheet("UP"))["headline"], "Alphas trade up with the Betas")
        down = template(self.sheet("DOWN"))
        self.assertEqual(down["headline"], "Alphas trade down with the Betas")
        self.assertIn("moved back", down["rationale_a"])

    def test_validator_rejects_a_prospect_name(self):
        league = {"players": {"JAMAR BODDEN"}, "teams": set()}
        ok, why = validate_rationale("We were sure Jamar Bodden wouldn't last.", self.sheet("UP"), league)
        self.assertFalse(ok)
        self.assertIn("Bodden", why)


if __name__ == "__main__":
    unittest.main()
