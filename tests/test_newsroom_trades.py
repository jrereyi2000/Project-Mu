"""Trades and rumors in the newsroom (docs/done/team_management/05_NEWSROOM_INTEGRATION_PLAN.md Phase 8). Pure helpers and
fake cursors: no database, no model calls."""
import re
import unittest
from unittest import mock

from newsroom import roster_moves as rm

NAMES = {"NYJ": "Jets", "NE": "Patriots", "BUF": "Bills", "MIA": "Dolphins"}


def trade(week=4, legs=None, pid=1):
    return {"proposal_id": pid, "week": week, "team_a": "NYJ", "team_b": "NE", "status": "EXECUTED", "legs": legs or [
        {"type": "PLAYER", "from": "NE", "to": "NYJ", "player_name": "Rich Hanson", "position": "WR", "ovr": 91,
         "cap_hit_now": 12_500_000},
        {"type": "PLAYER", "from": "NYJ", "to": "NE", "player_name": "Sauce Able", "position": "CB", "ovr": 80,
         "cap_hit_now": 9_000_000},
        {"type": "PICK", "from": "NYJ", "to": "NE", "label": "2037 R2 (NYJ)",
         "pick": {"draft_season": 2037, "round": 2, "original_team": "NYJ"}},
    ]}


def rumor(week=4, outcome="stalled", trigger="NEED", rid=7):
    return {"rumor_id": rid, "week": week, "teams": ["BUF", "MIA"], "initiator": "BUF", "trigger": trigger,
            "assets": [{"type": "PLAYER", "position": "TE", "from": "MIA", "to": "BUF"},
                       {"type": "PICK", "position": None, "from": "BUF", "to": "MIA"}],
            "status": "RUMOR", "outcome": outcome}


DONE_WORDS = re.compile(r"\b(traded|acquired|acquires|agreed|completed|executed|sent)\b", re.I)


class TradeLineTests(unittest.TestCase):
    def test_trade_line_is_fact_with_players_picks_and_cap(self):
        line = rm.trade_line(trade(), NAMES)
        self.assertTrue(line.startswith("Week 4 · TRADE (executed) · "))
        self.assertIn("Jets acquired WR Rich Hanson ($12.5M cap hit this season) from the Patriots", line)
        self.assertIn("Patriots acquired CB Sauce Able ($9M cap hit this season) and the Jets' 2037 2nd-round pick "
                      "from the Jets", line)
        self.assertIn("cap this season: Jets +$3.5M, Patriots -$3.5M", line)
        self.assertNotIn("91", line)                     # OVR is never printed

    def test_pick_held_from_another_team(self):
        legs = [{"type": "PLAYER", "from": "NE", "to": "NYJ", "player_name": "A B", "position": "QB", "cap_hit_now": None},
                {"type": "PICK", "from": "NYJ", "to": "NE", "pick": {"draft_season": 2038, "round": 1,
                                                                    "original_team": "BUF"}}]
        line = rm.trade_line(trade(legs=legs), NAMES)
        self.assertIn("the Bills' 2038 1st-round pick (held by the Jets)", line)
        self.assertNotIn("cap this season", line)        # no known cap hits, no cap clause
        self.assertNotIn("$0", line)

    def test_offseason_trade_label(self):
        self.assertTrue(rm.trade_line(trade(week=26), NAMES).startswith("Offseason · "))


class RumorTests(unittest.TestCase):
    def test_rumor_line_is_labelled_and_never_worded_as_done(self):
        for outcome in ("stalled", "open"):
            for trig in ("NEED", "INJURY", "SELL", None):
                line = rm.rumor_line(rumor(outcome=outcome, trigger=trig), NAMES)
                self.assertIn("RUMOR, not a done deal", line)
                self.assertIsNone(DONE_WORDS.search(line), line)
                self.assertNotIn("$", line)
        line = rm.rumor_line(rumor(), NAMES)
        self.assertIn("the Bills and the Dolphins discussed a trade (the Bills made the call, to fill a need)", line)
        self.assertIn("a TE to the Bills; a draft pick to the Dolphins", line)
        self.assertTrue(line.endswith("talks stalled, no deal"))
        self.assertTrue(rm.rumor_line(rumor(outcome="open"), NAMES).endswith("report it only as talks"))

    def test_rumor_visible_only_before_the_edition_and_through_the_deadline(self):
        self.assertTrue(rm.rumor_visible(4, 5))
        self.assertFalse(rm.rumor_visible(5, 5))         # nothing at the article's week
        self.assertFalse(rm.rumor_visible(6, 5))         # nor after it
        self.assertTrue(rm.rumor_visible(7, 8))
        self.assertFalse(rm.rumor_visible(7, 9))         # the Week 8 deadline has passed
        self.assertFalse(rm.rumor_visible(None, 5))
        self.assertTrue(rm.rumor_visible(3, None))       # no article context
        self.assertEqual(rm.TRADE_DEADLINE_WEEK, __import__("engines.gm_trade_engine", fromlist=["x"]).TRADE_DEADLINE_WEEK)

    def test_rumor_outcome(self):
        self.assertEqual(rm.rumor_outcome("REJECTED", 4, 5), "stalled")
        self.assertEqual(rm.rumor_outcome("EXPIRED", 4, 5), "stalled")
        self.assertEqual(rm.rumor_outcome("EXPIRED", 5, 5), "open")   # stalled only from the week after it expired
        self.assertEqual(rm.rumor_outcome("PROPOSED", 4, 5), "open")
        self.assertEqual(rm.rumor_outcome("APPROVED", 4, 5), "open")
        with self.assertRaises(ValueError):
            rm.rumor_outcome("EXECUTED", 4, 5)


class VisibilityTests(unittest.TestCase):
    def test_trade_visible(self):
        self.assertTrue(rm.trade_visible(2036, 4, 2036, 4))      # confirmed before Week 4's games
        self.assertTrue(rm.trade_visible(2036, 4, 2036, 5))
        self.assertFalse(rm.trade_visible(2036, 5, 2036, 4))
        self.assertFalse(rm.trade_visible(2036, 26, 2036, 18))   # offseason trade, in-season article
        self.assertTrue(rm.trade_visible(2036, 26, 2036, 24))
        self.assertFalse(rm.trade_visible(2036, 26, 2036, 24, offseason_event_ok=False))
        self.assertTrue(rm.trade_visible(2035, 6, 2036, 1))

    def test_new_trades_and_trade_grades(self):
        trades = [trade(week=3, pid=1), trade(week=4, pid=2), trade(week=26, pid=3)]
        self.assertEqual([t["proposal_id"] for t in rm.new_trades(trades, 5)], [2])
        self.assertEqual(rm.new_trades(trades, None), [])
        self.assertTrue(rm.trade_grades_due(trades, 5))
        self.assertTrue(rm.trade_grades_due(trades, 4))
        self.assertFalse(rm.trade_grades_due(trades, 6))
        self.assertFalse(rm.trade_grades_due([trade(week=26)], 24))
        self.assertFalse(rm.trade_grades_due([], 5))

    def test_trades_block(self):
        self.assertEqual(rm.trades_block([], []), {})
        block = rm.trades_block([trade()], [rumor()], NAMES, 5)
        self.assertEqual(len(block["executed_trades"]), 1)
        self.assertEqual(len(block["rumors"]), 1)
        self.assertIn("never as a trade that happened", block["rumor_note"])
        self.assertNotIn("rumors", rm.trades_block([trade()], [], NAMES, 5))

    def test_trade_legs_stay_out_of_move_blocks(self):
        leg = {"id": 1, "season": 2036, "week": 4, "type": "TRADED", "player_id": None, "team": "Jets",
               "from_team": "Patriots", "position": None}
        self.assertEqual(rm.brief_block([leg], 5), {})
        self.assertEqual(rm.team_block([leg])["moves_this_week"], [])
        self.assertTrue(rm.notability({"type": "TRADED", "ovr": 60})[0])     # every executed trade is news
        self.assertIn("traded a draft pick to the Jets (executed trade)", rm.move_line(leg))


class FakeCursor:
    def __init__(self, statuses):
        self.statuses, self.rows = statuses, []

    def execute(self, sql, params=None):
        self.rows = [(i, *self.statuses[i]) for i in params[0]] if "gm_trade_proposals" in sql else []

    def fetchall(self):
        return self.rows


class FetchRumorTests(unittest.TestCase):
    def run_fetch(self, ctx, rows, statuses):
        from newsroom import db_tools
        calls = []

        def fake_rumors(cur, season, week):
            calls.append(week)
            return [r for r in rows if r["week"] <= week]
        with mock.patch("engines.front_office.installed", return_value=True), \
                mock.patch("engines.front_office.trade_rumors", side_effect=fake_rumors):
            return db_tools.fetch_rumors(FakeCursor(statuses), 2036, ctx=ctx), calls

    def test_cutoff_deadline_and_outcomes(self):
        rows = [rumor(week=3, rid=1), rumor(week=4, rid=2), rumor(week=5, rid=3)]
        statuses = {1: ("REJECTED", 3), 2: ("EXPIRED", 4), 3: ("PROPOSED", 5)}
        out, calls = self.run_fetch((2036, 5, True), rows, statuses)
        self.assertEqual(calls, [4])                                   # proposals before the edition's week only
        self.assertEqual([(r["rumor_id"], r["outcome"]) for r in out], [(1, "stalled"), (2, "stalled")])
        out, calls = self.run_fetch((2036, 9, True), rows, statuses)   # past the deadline: none, no query
        self.assertEqual((out, calls), ([], []))
        out, _ = self.run_fetch((2037, 5, True), rows, statuses)       # another season's article: none
        self.assertEqual(out, [])

    def test_never_an_executed_proposal(self):
        rows = [rumor(week=3, rid=1)]
        out, _ = self.run_fetch((2036, 5, True), rows, {1: ("EXECUTED", 3)})
        self.assertEqual(out, [])


class PackageToolTests(unittest.TestCase):
    def test_parse_package_items(self):
        from newsroom.db_tools import parse_package_item as p
        self.assertEqual(p("2037 R2"), ("PICK", 2037, 2, ""))
        self.assertEqual(p(" 2037 2nd-round pick (Bills) "), ("PICK", 2037, 2, "Bills"))
        self.assertEqual(p("2038 round 1 Jets"), ("PICK", 2038, 1, "Jets"))
        self.assertEqual(p("Rich Hanson"), ("PLAYER", "Rich Hanson"))

    def test_check_week(self):
        from newsroom.db_tools import package_check_week as w
        self.assertEqual([w(5), w(8), w(9), w(19), w(23), w(24), w(None)], [5, 8, 24, 24, 24, 24, None])

    def test_check_lines_hide_numbers_and_booked_assets(self):
        from newsroom.db_tools import package_check_lines
        lines = package_check_lines({
            "cap": {"ok": False, "detail": "NYJ: $0.40M after (margin $1M); NE: $-2.00M after (margin $3M)"},
            "booking": {"ok": False, "detail": "WR Rich Hanson"},
            "fairness": {"ok": False, "detail": "league value $9.1M vs $2.0M (gap $7.1M, limit $4.0M)"},
            "ntc": {"ok": True, "detail": "none"},
        })
        text = " ".join(lines)
        self.assertNotIn("$", text)
        self.assertNotIn("Hanson", text)
        self.assertIn("FAIL · fits under both teams' caps: NE, NYJ would be over the cap or too close to it", text)
        self.assertIn("pass · no no-trade clause", text)


if __name__ == "__main__":
    unittest.main()


class TradeGradesTests(unittest.TestCase):
    def test_target_lists_every_trade_with_gm_thinking(self):
        t1 = {**trade(week=4, pid=1), "rationale_a": "We win now.", "rationale_b": "Picks for later."}
        t2 = {**trade(week=4, pid=2), "team_a": "BUF", "team_b": "MIA", "legs": [], "rationale_a": None}
        target = rm.trade_grades_target([t1, t2], NAMES, {"NYJ": 18, "NE": 17}, {"NYJ": {"gm_name": "Pierce Gallagher"}})
        self.assertEqual((target["team_id"], target["kind"], target["trades"]), (18, "TRADE_GRADES", [1, 2]))
        self.assertIn("grade every trade executed in Week 4", target["note"])
        self.assertIn("Trade 1: Week 4 · TRADE (executed) · Jets acquired WR Rich Hanson", target["note"])
        self.assertIn('Jets GM Pierce Gallagher on the deal: "We win now."', target["note"])
        self.assertIn('The Patriots front office on the deal: "Picks for later."', target["note"])
        self.assertEqual(target["team_name"], "Bills / Dolphins / Jets / Patriots")
        self.assertIsNone(rm.trade_grades_target([]))

    def _slate(self, target, week=5):
        from newsroom import swarm_dispatcher as sd
        d = sd.SwarmDispatcher(api_key="test", debug_mode=False)
        with mock.patch.object(d, "_trade_grades_target", return_value=target), \
                mock.patch.object(d, "_resolve_slot_targets", side_effect=lambda s, w, slots: [None] * len(slots)), \
                mock.patch.object(d, "get_agent_record", side_effect=lambda a: (a, a, "", "@x", a, None)), \
                mock.patch.object(d, "_get_distinct_beat_reporter",
                                  return_value=("b", "LOCAL_BEAT_REPORTER", "", "@b", "B", None)), \
                mock.patch.object(sd.psycopg, "connect", side_effect=RuntimeError("offline")):
            return d.commission_weekly_editorial_slate(2036, week)

    def test_trade_grades_takes_slot_5_only_after_a_trade(self):
        target = {"team_id": 18, "team_name": "Jets / Patriots", "kind": "TRADE_GRADES", "note": "grade it"}
        slate = self._slate(target)
        by_slot = {s["slot"]: s for s in slate}
        self.assertEqual(by_slot["Slot 5"]["article_type_id"], "TRADE_GRADES")
        self.assertIn(by_slot["Slot 5"]["archetype"], ("SANDO", "BARNWELL"))
        self.assertIs(by_slot["Slot 5"]["target"], target)
        self.assertEqual(sum(s["article_type_id"] == "TRADE_GRADES" for s in slate), 1)
        self.assertNotIn("TRADE_GRADES", {by_slot[k]["article_type_id"] for k in ("Slot 3", "Slot 4")})
        self.assertEqual(len(slate), len(self._slate(None)))          # same slate size, one slot swapped
        self.assertNotIn("TRADE_GRADES", [s["article_type_id"] for s in self._slate(None)])


def wire_league(trades=(), rumors=(), moves=()):
    from tests.test_social_desk import moves_league
    ctx = moves_league(list(moves))
    ctx["trades"], ctx["rumors"] = list(trades), list(rumors)
    return ctx


def wire_trade(tid, a, b):
    line = f"Week 8 · TRADE (executed) · Team{a} acquired WR X ($12.5M cap hit this season) from the Team{b}"
    return {"id": tid, "week": 8, "teams": [a, b], "line": line, "money": ["$12.5M"], "players": ["X"],
            "gm_said": {a: "We win now.", b: None}}


def wire_rumor(rid, a, b):
    return {"id": rid, "week": 8, "teams": [a, b], "outcome": "stalled",
            "line": f"Week 8 · RUMOR, not a done deal · the Team{a} and the Team{b} discussed a trade · talks stalled, no deal"}


class AgentWireTradeTests(unittest.TestCase):
    def kinds(self, plan):
        from tests.test_social_desk import by_kind
        return by_kind(plan)

    def test_trade_news_reactions_and_beat(self):
        from newsroom.social_desk import TARGET_POSTS, plan_social_week
        plan = plan_social_week(wire_league(trades=[wire_trade(1, 3, 4)]))
        k = self.kinds(plan)
        self.assertEqual(len(k["TRADE_NEWS"]), 1)
        self.assertIn(k["TRADE_NEWS"][0]["archetype"], ("SCHEFTER_TWEET", "RAPOPORT"))
        self.assertIn("$12.5M", k["TRADE_NEWS"][0]["facts"]["trade"])
        self.assertEqual(sorted((a["archetype"], a["team_id"]) for a in k["TRADE_REACTION"]),
                         [("DIE_HARD_FAN", 3), ("DIE_HARD_FAN", 4)])
        mine = next(a for a in k["TRADE_REACTION"] if a["team_id"] == 3)
        self.assertEqual(mine["facts"]["your_gm_said"], "We win now.")
        self.assertNotIn("your_gm_said", next(a for a in k["TRADE_REACTION"] if a["team_id"] == 4)["facts"])
        beat = {a["team_id"]: a for a in plan["assignments"] if a["kind"] == "BEAT_MOVE"}
        self.assertEqual(set(beat), {3, 4})
        self.assertIn("TRADE (executed)", beat[3]["facts"]["trades_this_week"][0])
        self.assertEqual(sum(plan["counts"].values()), TARGET_POSTS)

    def test_rumors_capped_never_worded_as_done(self):
        from newsroom.social_desk import TRADE_RUMOR_MAX, plan_social_week
        plan = plan_social_week(wire_league(rumors=[wire_rumor(i, i, i + 10) for i in range(1, 5)]))
        rumors = self.kinds(plan)["TRADE_RUMOR"]
        self.assertEqual(len(rumors), TRADE_RUMOR_MAX)
        for a in rumors:
            self.assertIn(a["archetype"], ("SCHEFTER_TWEET", "RAPOPORT"))
            self.assertIn("RUMOR, not a done deal", a["facts"]["rumor"])
            self.assertIn("no trade happened", a["facts"]["grounding"])
        self.assertFalse(self.kinds(plan)["TRADE_NEWS"])
        self.assertFalse(self.kinds(plan)["TRADE_REACTION"])          # nobody reacts to a rumor as a trade

    def test_no_trades_no_trade_posts(self):
        from newsroom.social_desk import plan_social_week
        k = self.kinds(plan_social_week(wire_league()))
        for kind in ("TRADE_NEWS", "TRADE_REACTION", "TRADE_RUMOR"):
            self.assertFalse(k[kind], kind)

    def test_trades_come_before_moves_for_the_insiders(self):
        from newsroom.social_desk import plan_social_week
        from tests.test_social_desk import move
        plan = plan_social_week(wire_league(trades=[wire_trade(i, i, i + 10) for i in range(1, 4)],
                                            rumors=[wire_rumor(9, 5, 6)], moves=[move(i, 20 + i) for i in range(1, 4)]))
        k = self.kinds(plan)
        self.assertEqual((len(k["TRADE_NEWS"]), len(k["TRADE_RUMOR"])), (3, 1))
        moves_bucket = [a["kind"] for a in plan["assignments"] if a["kind"] in ("TRADE_NEWS", "TRADE_RUMOR", "MOVE_NEWS")]
        self.assertEqual(moves_bucket[:4], ["TRADE_NEWS"] * 3 + ["TRADE_RUMOR"])

    def test_team_wakeup_beat_move_for_a_trade(self):
        from newsroom.social_desk import plan_team_wakeup
        plan = plan_team_wakeup(wire_league(trades=[wire_trade(1, 16, 4)]), 16)
        self.assertIn("BEAT_MOVE", [a["kind"] for a in plan["assignments"]])


class RumorGuardTests(unittest.TestCase):
    def test_rumor_guard(self):
        from newsroom.social_writer import KIND_INSTRUCTIONS, RUMOR_PROBLEM, allowed_money, clean_post
        allowed = allowed_money({"rumor": "Week 8 · RUMOR, not a done deal · ..."})
        ok = clean_post("Sources: Bills and Dolphins have discussed a TE deal. Not a done deal.", allowed=allowed, rumor=True)
        self.assertEqual(ok["problems"], [])
        for bad in ("Bills have acquired a TE from Miami", "Done deal: TE to Buffalo", "The Bills traded for a TE",
                    "Bills and Dolphins agreed to terms"):
            problems = clean_post(bad, allowed=allowed, rumor=True)["problems"]
            self.assertTrue(any(p.startswith(RUMOR_PROBLEM) for p in problems), bad)
        self.assertTrue(clean_post("TE talks, $10M ask", allowed=allowed, rumor=True)["problems"])   # no figures
        self.assertEqual(clean_post("Bills acquired a TE", allowed=allowed)["problems"], [])        # guard off
        for kind in ("TRADE_NEWS", "TRADE_REACTION", "TRADE_RUMOR"):
            self.assertIn("FACTS", KIND_INSTRUCTIONS[kind])

    def test_rumor_problem_blocks_publishing(self):
        from newsroom.social_publish import publishable
        from newsroom.social_writer import RUMOR_PROBLEM
        self.assertFalse(publishable({"text": "x", "problems": [f"{RUMOR_PROBLEM}: acquired"]}))
