"""Player statements on the Agent Wire (COACH_PLAYER_AGENT_LOOPS_PLAN U3b, §2.5). No database.

A public `LEAK` decision for the publication week's news window becomes at most `LEAK_MAX_PER_WEEK`
`PLAYER_STATEMENT` assignments grounded only in the recorded statement ("a statement, not a signing"); a week
with no leaks plans none and changes nothing else.
"""
import datetime as dt
import unittest

from engines.agent_loops.player import LEAK_MAX_PER_WEEK
from newsroom import social_desk as SD
from newsroom.social_publish import _post_time, week_thursday
from newsroom.social_writer import KIND_INSTRUCTIONS, KIND_SHAPES, build_post_prompt
from tests.test_social_desk import by_kind, league

SEASON = 2035


class StatementCursor:
    """Answers the loader's two reads: the to_regclass guard, then the agent_loop_decisions select."""

    def __init__(self, rows=(), table=True):
        self.rows, self.table, self.log, self._out = list(rows), table, [], []

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))
        self._out = [(self.table,)] if "to_regclass" in sql else list(self.rows)

    def fetchone(self):
        return self._out[0] if self._out else None

    def fetchall(self):
        return self._out


def leak_row(pid=4411, name="Jalen Carter", position="DT", team="City1 Team1", week=9, season=SEASON,
             text=None, offered=None, asked=None):
    facts = {"player_id": pid, "player": name, "position": position, "team": team, "wants": "a new deal"}
    if offered:
        facts["offered"] = offered
    if asked:
        facts["asked"] = asked
    return (season, week, f"player:{pid}:leak", text, facts)


def statement(i=1, team="City1 Team1", **kw):
    """A ctx["player_statements"] item (what load_player_statements returns)."""
    return {"player_id": 4400 + i, "name": f"Player {i}", "position": "WR", "team": team,
            "statement": f"Player {i} says he wants a deal done with {team}, and he has made his ask.",
            "season": SEASON, "tick": 9, **kw}


class LoadPlayerStatementsTests(unittest.TestCase):
    def test_missing_table_means_no_statements(self):
        self.assertEqual(SD.load_player_statements(StatementCursor([leak_row()], table=False), SEASON, 9), [])

    def test_reads_only_public_leaks_for_the_news_window(self):
        cur = StatementCursor([leak_row()])
        SD.load_player_statements(cur, SEASON, 9)
        guard_sql, select_sql = cur.log[0][0], cur.log[1][0]
        self.assertIn("to_regclass('agent_loop_decisions')", guard_sql)
        self.assertIn("kind = 'LEAK'", select_sql)
        self.assertIn("visibility = 'public'", select_sql)
        self.assertIn("season = %s", select_sql)
        self.assertIn("tick = ANY(%s)", select_sql)
        self.assertIn("ORDER BY tick, subject_key", select_sql)
        self.assertEqual(cur.log[1][1][0], SEASON)
        self.assertEqual(cur.log[1][1][1], [8, 9])          # recap week N-1 and publication week N

    def test_week_one_looks_back_at_the_offseason_tick(self):
        cur = StatementCursor([])
        SD.load_player_statements(cur, SEASON, 1)
        self.assertEqual(cur.log[1][1][1], [1, 24])

    def test_cap_at_leak_max_per_week(self):
        rows = [leak_row(pid=4400 + i, name=f"Player {i}", week=9) for i in range(LEAK_MAX_PER_WEEK + 2)]
        items = SD.load_player_statements(StatementCursor(rows), SEASON, 9)
        self.assertEqual(len(items), LEAK_MAX_PER_WEEK)
        self.assertEqual([i["name"] for i in items], [f"Player {i}" for i in range(LEAK_MAX_PER_WEEK)])

    def test_item_carries_only_the_recorded_row(self):
        item = SD.load_player_statements(
            StatementCursor([leak_row(text="I want a new deal here.",
                                      offered={"years": 4, "total_value": 80_000_000})]), SEASON, 9)[0]
        self.assertEqual(item["player_id"], 4411)
        self.assertEqual(item["name"], "Jalen Carter")
        self.assertEqual(item["position"], "DT")
        self.assertEqual(item["team"], "City1 Team1")
        self.assertEqual(item["statement"], "I want a new deal here.")
        self.assertEqual((item["season"], item["tick"]), (SEASON, 9))
        self.assertEqual(item["offered"], {"years": 4, "total_value": 80_000_000})
        self.assertNotIn("asked", item)

    def test_no_model_text_uses_the_loops_own_template(self):
        item = SD.load_player_statements(StatementCursor([leak_row(text=None)]), SEASON, 9)[0]
        self.assertIn("says he wants a deal done with City1 Team1", item["statement"])
        self.assertNotIn("$", item["statement"])

    def test_malformed_rows_are_skipped(self):
        bare = (SEASON, 9, "player:1:leak", "hello", {"player_id": 1})
        self.assertEqual(SD.load_player_statements(StatementCursor([bare]), SEASON, 9), [])


class PlannerTests(unittest.TestCase):
    def test_a_statement_becomes_exactly_one_assignment(self):
        ctx = league(week=9)
        ctx["accounts"] = ctx["accounts"] + [
            {"agent_id": "schefter", "archetype": "SCHEFTER_TWEET", "team_id": None, "handle": "@schefter",
             "display_name": "Schefter"}]
        ctx["player_statements"] = [statement(1, offered={"years": 4, "total_value": 80_000_000})]
        out = by_kind(SD.plan_social_week(ctx))["PLAYER_STATEMENT"]
        self.assertEqual(len(out), 1)
        a = out[0]
        self.assertEqual(a["subject"], {"type": "player", "id": 4401, "team_id": 1,
                                        "label": "Player 1 (WR, City1 Team1)"})
        self.assertEqual(a["team_id"], 1)                                    # filed under his team
        self.assertIn(a["archetype"], SD.INSIDER_ARCHETYPES)                  # an insider reports it
        self.assertEqual(a["facts"]["statement"], statement(1)["statement"])
        self.assertEqual((a["facts"]["player"], a["facts"]["position"], a["facts"]["team"]),
                         ("Player 1", "WR", "City1 Team1"))
        self.assertIn("A STATEMENT, not a signing", a["facts"]["grounding"])
        self.assertEqual(a["facts"]["offered"], {"years": 4, "total_value": 80_000_000})
        self.assertNotIn("asked", a["facts"])
        self.assertEqual(SD.WINDOWS["PLAYER_STATEMENT"], "Mon – Wed")

    def test_five_leaks_plan_only_the_cap_and_count_inside_the_total(self):
        plain = SD.plan_social_week(league(week=9))
        ctx = league(week=9)
        ctx["player_statements"] = [statement(i) for i in range(5)]
        plan = SD.plan_social_week(ctx)
        self.assertEqual(len(by_kind(plan)["PLAYER_STATEMENT"]), LEAK_MAX_PER_WEEK)
        self.assertEqual(plan["mix"]["PLAYER_STATEMENT"], LEAK_MAX_PER_WEEK)
        # inside the week's total, not added on top of it
        self.assertEqual(sum(plan["mix"].values()), sum(plain["mix"].values()))
        self.assertLessEqual(sum(plan["counts"].values()), sum(plan["mix"].values()))

    def test_a_week_with_no_leaks_plans_none_and_changes_nothing(self):
        plain = SD.plan_social_week(league(week=9))
        empty = SD.plan_social_week({**league(week=9), "player_statements": []})
        self.assertNotIn("PLAYER_STATEMENT", plain["counts"])
        self.assertNotIn("PLAYER_STATEMENT", empty["counts"])
        self.assertNotIn("PLAYER_STATEMENT", plain["mix"])
        self.assertEqual(plain["counts"], empty["counts"])
        self.assertEqual(plain["mix"], empty["mix"])


class WriterAndPublishTests(unittest.TestCase):
    def test_prompt_says_a_statement_not_a_signing(self):
        a = {"kind": "PLAYER_STATEMENT", "subject": {"type": "player", "id": 4401, "label": "Player 1"},
             "facts": {"player": "Player 1", "position": "WR", "team": "City1 Team1",
                       "statement": "Player 1 says he wants a deal done with City1 Team1, and he has made his ask.",
                       "grounding": "A STATEMENT, not a signing."}}
        account = {"agent_id": "schefter", "archetype": "SCHEFTER_TWEET", "handle": "@schefter",
                   "display_name": "Schefter", "persistent_memory": ""}
        _, prompt = build_post_prompt(a, account, "You break news.")
        low = prompt.lower()
        self.assertIn("a statement, not a signing", low)
        self.assertIn("add no new figures", low)
        self.assertIn("never claim a trade or a deal", low)
        self.assertIn("POST TYPE: PLAYER_STATEMENT", prompt)
        self.assertGreaterEqual(len(KIND_SHAPES["PLAYER_STATEMENT"]), 4)

    def test_publishes_monday_to_wednesday(self):
        ctx = league(week=9)
        a = {"kind": "PLAYER_STATEMENT", "agent_id": "schefter", "team_id": 1,
             "subject": {"type": "player", "id": 4401, "team_id": 1, "label": "Player 1"}}
        t = _post_time(ctx, a)
        tue = week_thursday(SEASON, 9) - dt.timedelta(days=2)
        self.assertGreaterEqual(t, tue - dt.timedelta(hours=15))
        self.assertLessEqual(t, tue + dt.timedelta(days=1, hours=20))


if __name__ == "__main__":
    unittest.main()
