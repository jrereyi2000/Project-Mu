"""Unit tests for evolving agent memory (newsroom/agent_memory.py). No database required.

Run: python3 -m unittest tests.test_agent_memory
"""
import unittest

from newsroom.agent_memory import (
    fetch_predictions, format_memory_block, grade_prediction, parse_score_predictions, parse_takes_json,
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


if __name__ == "__main__":
    unittest.main()
