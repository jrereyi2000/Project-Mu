"""Unit tests for evolving agent memory (newsroom/agent_memory.py). No database required.

Run: python3 -m unittest tests.test_agent_memory
"""
import unittest

from newsroom.agent_memory import (
    build_memory_block, fetch_predictions, format_memory_block, grade_call, grade_prediction, parse_score_predictions,
    parse_takes_json, summary_prompt,
)

TEAMS = [(1, "Bills", "Buffalo", "BUF"), (2, "Dolphins", "Miami", "MIA"), (3, "Chiefs", "Kansas City", "KC"),
         (4, "Eagles", "Philadelphia", "PHI")]


class PredictionParsingTests(unittest.TestCase):
    def test_projected_final_lines(self):
        text = "Some analysis.\n**Projected Final: Buffalo Bills 27, Miami Dolphins 24**\nProjected Final: Chiefs 31, Eagles 17"
        preds = parse_score_predictions(text, TEAMS)
        self.assertEqual([(p["team_a"], p["score_a"], p["team_b"], p["score_b"]) for p in preds],
                         [(1, 27, 2, 24), (3, 31, 4, 17)])

    def test_prose_scores_are_ignored(self):
        text = "Last week the Bills beat the Dolphins, Bills 27, Dolphins 24, in overtime."
        self.assertEqual(parse_score_predictions(text, TEAMS), [])

    def test_roundtable_panellist_lines(self):
        text = "## Official Super Bowl Picks\nMina Kimes: Chiefs 27, Eagles 24\n- Bill Simmons: Eagles 30, Chiefs 20\n**Panel Consensus: Chiefs 3-1**"
        preds = parse_score_predictions(text, TEAMS, panel=True)
        self.assertEqual([(p["label"], p["team_a"], p["score_a"]) for p in preds],
                         [("Mina Kimes", 3, 27), ("Bill Simmons", 4, 30)])

    def test_unresolvable_or_same_team_skipped(self):
        self.assertEqual(parse_score_predictions("Projected Final: Jets 20, Bills 17", TEAMS), [])
        self.assertEqual(parse_score_predictions("Projected Final: Bills 20, Bills 17", TEAMS), [])

    def test_duplicate_game_counted_once(self):
        text = "Projected Final: Bills 27, Dolphins 24\nProjected Final: Bills 27, Dolphins 24"
        self.assertEqual(len(parse_score_predictions(text, TEAMS)), 1)


class TakesAndGradingTests(unittest.TestCase):
    def test_takes_json_tolerates_fences(self):
        raw = '```json\n[{"take": "The Bills are frauds.", "team": "Bills", "player": ""}, {"take": " "}]\n```'
        self.assertEqual(parse_takes_json(raw), [{"take": "The Bills are frauds.", "team": "Bills", "player": ""}])

    def test_takes_json_garbage(self):
        self.assertEqual(parse_takes_json("no json here"), [])
        self.assertEqual(parse_takes_json("[not json"), [])

    def test_takes_capped(self):
        raw = "[" + ",".join('{"take": "t%d"}' % i for i in range(6)) + "]"
        self.assertEqual(len(parse_takes_json(raw)), 3)

    def test_grading(self):
        base = {"score_a": 27, "score_b": 24}
        self.assertEqual(grade_prediction({**base, "actual_a": 30, "actual_b": 10})["result"], "correct")
        self.assertEqual(grade_prediction({**base, "actual_a": 10, "actual_b": 30})["result"], "wrong")
        self.assertEqual(grade_prediction({**base, "actual_a": None, "actual_b": None})["result"], "pending")
        self.assertEqual(grade_prediction({**base, "actual_a": 20, "actual_b": 20})["result"], "push")


class MemoryBlockTests(unittest.TestCase):
    def test_empty_memory_is_empty(self):
        self.assertEqual(format_memory_block("", [], [], [], (0, 0)), "")

    def test_block_has_takes_and_owns_misses(self):
        pred = {"week": 6, "label": "", "name_a": "Buffalo Bills", "name_b": "Miami Dolphins",
                "score_a": 27, "score_b": 24, "actual_a": 17, "actual_b": 31, "result": "wrong"}
        block = format_memory_block("I've doubted Buffalo all year.", [{"week": 6, "text": "The Bills are frauds."}],
                                    [], [pred], (3, 1))
        self.assertIn("YOUR SEASON SO FAR", block)
        self.assertIn("Week 6: The Bills are frauds.", block)
        self.assertIn("3-1 picking winners", block)
        self.assertIn("you were WRONG", block)

    def test_pending_predictions_not_listed(self):
        pred = {"week": 9, "label": "", "name_a": "A", "name_b": "B", "score_a": 1, "score_b": 0,
                "actual_a": None, "actual_b": None, "result": "pending"}
        self.assertNotIn("TRACK RECORD", format_memory_block("", [], [], [pred], (0, 0)))


class FakeCursor:
    """Like a DB-API cursor, a new execute() replaces the pending result set."""
    def __init__(self, results):
        self.results, self._rows = results, []

    def execute(self, sql, params=None):
        key = "teams" if "FROM teams" in sql else "predictions"
        self._rows = list(self.results[key])

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class FetchPredictionsTests(unittest.TestCase):
    def test_predictions_survive_the_team_name_lookup(self):
        # Regression: the team-name query used to run before the predictions were read, so this returned [].
        cur = FakeCursor({
            "predictions": [(6, "", 1, 27, 2, 24, 17, 31)],
            "teams": [(1, "Buffalo Bills"), (2, "Miami Dolphins")],
        })
        preds = fetch_predictions(cur, "agent", 2035, 7)
        self.assertEqual(len(preds), 1)
        self.assertEqual((preds[0]["name_a"], preds[0]["name_b"], preds[0]["result"]),
                         ("Buffalo Bills", "Miami Dolphins", "wrong"))


class WinnerPickTests(unittest.TestCase):
    BASE = {"pick_type": "WINNER", "score_a": None, "score_b": None, "team_a_id": 1, "team_b_id": 2,
            "name_a": "Buffalo Bills", "name_b": "Miami Dolphins", "week": 4, "label": ""}

    def test_graded_by_winner_only(self):
        self.assertEqual(grade_prediction({**self.BASE, "winner_team_id": 1, "actual_a": 21, "actual_b": 20})["result"], "correct")
        self.assertEqual(grade_prediction({**self.BASE, "winner_team_id": 2, "actual_a": 21, "actual_b": 20})["result"], "wrong")
        self.assertEqual(grade_prediction({**self.BASE, "winner_team_id": 2, "actual_a": 20, "actual_b": 20})["result"], "push")
        self.assertEqual(grade_prediction({**self.BASE, "winner_team_id": 2, "actual_a": None, "actual_b": None})["result"], "pending")
        self.assertEqual(grade_prediction({**self.BASE, "winner_team_id": 9, "actual_a": 21, "actual_b": 20})["result"], "pending")

    def test_winner_pick_in_memory_block_and_summary(self):
        p = grade_prediction({**self.BASE, "winner_team_id": 2, "actual_a": 30, "actual_b": 10})
        block = format_memory_block("", [], [], [p], (0, 1))
        self.assertIn("you picked the Miami Dolphins to beat the Buffalo Bills", block)
        self.assertIn("you were WRONG", block)
        self.assertNotIn("None", block)
        self.assertIn("you picked the Miami Dolphins", summary_prompt("X", "", "", [], [p], 4))


class SeasonCallTests(unittest.TestCase):
    FACTS = {"awards": {"MVP": {17}, "ROY": {40, 41}}, "champion": 3, "playoff_teams": {1, 2, 3},
             "division_winners": {1}, "division_undecided": set(), "fired_teams": {8}, "fired_coaches": {80},
             "season_final": False}

    def call(self, ctype, stype, sid):
        return {"call_type": ctype, "subject_type": stype, "subject_id": sid}

    def test_awards(self):
        self.assertEqual(grade_call(self.call("MVP", "player", 17), self.FACTS), "RIGHT")
        self.assertEqual(grade_call(self.call("MVP", "player", 18), self.FACTS), "WRONG")
        self.assertEqual(grade_call(self.call("ROY", "player", 41), self.FACTS), "RIGHT")   # OROY or DROY
        self.assertIsNone(grade_call(self.call("DPOY", "player", 5), self.FACTS))          # not awarded yet
        self.assertEqual(grade_call(self.call("MVP", "team", 1), self.FACTS), "VOID")

    def test_super_bowl_and_standings(self):
        self.assertEqual(grade_call(self.call("SUPER_BOWL", "team", 3), self.FACTS), "RIGHT")
        self.assertEqual(grade_call(self.call("SUPER_BOWL", "team", 2), self.FACTS), "WRONG")
        self.assertEqual(grade_call(self.call("PLAYOFF_TEAM", "team", 2), self.FACTS), "RIGHT")
        self.assertEqual(grade_call(self.call("PLAYOFF_TEAM", "team", 9), self.FACTS), "WRONG")
        self.assertEqual(grade_call(self.call("MISSES_PLAYOFFS", "team", 9), self.FACTS), "RIGHT")
        self.assertEqual(grade_call(self.call("DIVISION_WINNER", "team", 1), self.FACTS), "RIGHT")
        self.assertIsNone(grade_call(self.call("DIVISION_WINNER", "team", 1), {**self.FACTS, "division_undecided": {1}}))
        self.assertIsNone(grade_call(self.call("PLAYOFF_TEAM", "team", 2), {"season_final": False}))

    def test_coach_fired(self):
        self.assertEqual(grade_call(self.call("COACH_FIRED", "team", 8), self.FACTS), "RIGHT")
        self.assertEqual(grade_call(self.call("COACH_FIRED", "coach", 80), self.FACTS), "RIGHT")
        self.assertIsNone(grade_call(self.call("COACH_FIRED", "team", 1), self.FACTS))
        self.assertEqual(grade_call(self.call("COACH_FIRED", "team", 1), {**self.FACTS, "season_final": True}), "WRONG")

    def test_open_and_graded_calls_in_memory_block(self):
        open_calls = [{"made_week": 1, "call_type": "MVP", "subject_name": "Josh Allen", "detail": "book it", "result": "PENDING"}]
        graded = [{"made_week": 1, "call_type": "COACH_FIRED", "subject_name": "Dallas Cowboys", "detail": None, "result": "RIGHT"},
                  {"made_week": 1, "call_type": "PLAYOFF_TEAM", "subject_name": "New York Jets", "detail": None, "result": "WRONG"}]
        block = format_memory_block("", [], [], [], (0, 0), open_calls, graded)
        self.assertIn("YOUR OPEN SEASON CALLS", block)
        self.assertIn("Week 1: MVP: Josh Allen (book it)", block)
        self.assertIn("YOUR SEASON CALL RECORD: 1 right, 1 wrong", block)
        self.assertIn("New York Jets: you were WRONG", block)


class PersonMemoryCursor:
    """Records every query; answers just enough for build_memory_block over a two-account person."""
    def __init__(self, press_rows=None, press_installed=True, take_rows=None):
        self.calls, self._rows = [], []
        self.press_rows = list(press_rows or [])
        self.press_installed = press_installed
        self.take_rows = take_rows

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.calls.append((s, params))
        if s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            self._rows = []
        elif "to_regclass('public.agent_memory_entries')" in s:
            self._rows = [(True,)]
        elif "to_regclass('public.agent_season_calls')" in s:
            self._rows = [(True,)]
        elif "to_regclass('public.press_conferences')" in s:
            self._rows = [(self.press_installed,)]
        elif "information_schema.columns" in s:
            self._rows = [(1,)]
        elif s.startswith("SELECT agent_id FROM agent_memories"):
            self._rows = [("Skip_Bayless_National",), ("Skip_Bayless",)]
        elif "FROM agent_memory_entries" in s and "kind = 'PRESS'" in s:
            self._rows = list(self.press_rows)
        elif "FROM agent_memory_entries" in s:
            if self.take_rows is not None:
                self._rows = list(self.take_rows)
            else:
                self._rows = [(2, "Skip's tweet take")] if "Skip_Bayless" in params[0] else []
        elif "FROM agent_predictions" in s:
            self._rows = []
        elif "FROM agent_season_calls" in s:
            self._rows = [(1, "MVP", None, "PENDING", None, "Josh Allen")]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class PersonMemoryTests(unittest.TestCase):
    def test_memory_reads_across_the_persons_accounts(self):
        cur = PersonMemoryCursor()
        block = build_memory_block(cur, "Skip_Bayless_National", 2036, 5)
        self.assertIn("Skip's tweet take", block)
        self.assertIn("YOUR OPEN SEASON CALLS", block)
        reads = [p for s, p in cur.calls if "ANY(%s)" in s]
        self.assertTrue(reads)
        for params in reads:
            self.assertIn(["Skip_Bayless_National", "Skip_Bayless"], list(params))


class PressMemoryTests(unittest.TestCase):
    """PRESS rows (press_room.close_effects) show in their own section, before the publication week, never as takes."""

    def test_press_section_lists_the_newest_rows_before_the_week(self):
        cur = PersonMemoryCursor(take_rows=[], press_rows=[
            (5, "Coach Whitfield's presser after the loss to Atlanta."),
            (4, "Coach Whitfield's presser after the win over Tampa."),
            (3, "Coach Whitfield's presser after the loss to New Orleans.")])
        block = build_memory_block(cur, "Skip_Bayless_National", 2036, 6)
        self.assertIn("YOUR RECENT PRESS CONFERENCES (you were in the room):", block)
        self.assertIn("Week 5: Coach Whitfield's presser after the loss to Atlanta.", block)
        self.assertNotIn("YOUR RECENT TAKES", block)          # PRESS rows are not takes
        press_sql = [s for s, _ in cur.calls if "kind = 'PRESS'" in s]
        self.assertTrue(press_sql)
        self.assertIn("week < %s", press_sql[0])
        self.assertIn("kind = 'TAKE'", " ".join(s for s, _ in cur.calls if "FROM agent_memory_entries" in s))

    def test_press_section_absent_without_the_press_tables(self):
        cur = PersonMemoryCursor(press_rows=[(5, "Week 5: Coach Whitfield's presser.")], press_installed=False)
        block = build_memory_block(cur, "Skip_Bayless_National", 2036, 6)
        self.assertNotIn("YOUR RECENT PRESS CONFERENCES", block)
        self.assertNotIn("kind = 'PRESS'", " ".join(s for s, _ in cur.calls))

    def test_press_rows_do_not_leak_into_the_takes(self):
        cur = PersonMemoryCursor(press_rows=[(5, "Week 5: Coach Whitfield's presser.")])
        cur.press_installed = True
        # the TAKE queries still carry kind = 'TAKE' and never the PRESS row's text
        block = build_memory_block(cur, "Skip_Bayless_National", 2036, 6)
        self.assertIn("Skip's tweet take", block)
        take_lines = [s for s, _ in cur.calls if "kind = 'TAKE'" in s]
        self.assertTrue(take_lines)
        self.assertTrue(all("PRESS" not in s for s in take_lines))


if __name__ == "__main__":
    unittest.main()
