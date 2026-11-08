"""Wiring tests for press conferences (PRESS_CONFERENCE_PLAN §2.2, §2.8, §2.9, §4; U7).

Offline (no database, no model calls, nothing run): the router mount, the user wakeup hold, the beat brief block and
declined note, the head-coach quoting rule in the beat directive and the shared rule, the beat-only tool, the
dispatch_team_local presser guarantee, Play Game's decline + detached judgments/wakeup, and the watcher's detached
`run-week` for the week the database says was just completed.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import server.api as api
import newsroom.press_room as press_room
from newsroom import swarm_dispatcher
from pipeline import game_launch as gl
from pipeline import mod_packs
from pipeline import watch_save as ws
from pipeline.gpu_queue import process_check

ROUTER_PATH = "/api/franchise/{abbr}/press"
QUOTING_RULE = ("A head coach is quoted only from his press conference transcript, word for word "
                "(`press_conference` in your brief, or `get_press_conference`). If there is none, do not put words "
                "in his mouth. Coordinators, players and team captains may be quoted as before.")


# ----------------------------------------------------------------------------------------------- server wiring
class WakeupCursor:
    """Answers maybe_start_team_wakeup and its presser hold from scripted rows."""
    def __init__(self, installed=True, press=None, game_id=77, article_count=0):
        self.installed, self.press, self.game_id, self.article_count = installed, press, game_id, article_count
        self.rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM league_state" in s:
            self.rows = [{"current_season": 2036, "current_week": 6}]
        elif "to_regclass('press_conferences')" in s:
            self.rows = [{"press_installed": self.installed}]
        elif "q:wakeup_last_game" in s:
            self.rows = [{"id": self.game_id}] if self.game_id else []
        elif "q:wakeup_presser" in s:
            self.rows = [dict(self.press)] if self.press else []
        elif "COUNT(*) AS cnt" in s:
            self.rows = [{"cnt": self.article_count}]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None


class WakeupTests(unittest.TestCase):
    def setUp(self):
        self.auto = mock.patch.object(api, "AUTO_LOCAL_WAKEUP", True)
        self.auto.start()
        self.addCleanup(self.auto.stop)
        self.start = mock.patch.object(api, "start_local_wakeup", return_value=True)
        self.start_mock = self.start.start()
        self.addCleanup(self.start.stop)

    def test_a_user_presser_holds_the_wakeup_while_it_is_open(self):
        for status in ("SCHEDULED", "LIVE", "REVIEW"):
            with self.subTest(status=status):
                self.start_mock.reset_mock()
                cur = WakeupCursor(press={"status": status, "controller": "USER"})
                self.assertFalse(api.maybe_start_team_wakeup(cur, 5, 2036))
                self.start_mock.assert_not_called()

    def test_the_wakeup_starts_once_the_presser_is_closed_or_declined(self):
        for status in ("CLOSED", "DECLINED"):
            with self.subTest(status=status):
                self.start_mock.reset_mock()
                cur = WakeupCursor(press={"status": status, "controller": "USER"})
                self.assertTrue(api.maybe_start_team_wakeup(cur, 5, 2036))
                self.start_mock.assert_called_once_with(5, 2036, 6, label="")

    def test_an_ai_presser_never_holds_the_wakeup(self):
        cur = WakeupCursor(press={"status": "SCHEDULED", "controller": "AGENT"})
        self.assertTrue(api.maybe_start_team_wakeup(cur, 5, 2036))

    def test_old_behaviour_without_the_press_tables(self):
        cur = WakeupCursor(installed=False, press={"status": "SCHEDULED", "controller": "USER"})
        self.assertTrue(api.maybe_start_team_wakeup(cur, 5, 2036))

    def test_no_presser_or_no_game_never_holds(self):
        self.assertTrue(api.maybe_start_team_wakeup(WakeupCursor(press=None), 5, 2036))
        self.start_mock.reset_mock()
        self.assertTrue(api.maybe_start_team_wakeup(WakeupCursor(game_id=None), 5, 2036))

    def test_an_existing_local_article_still_stops_the_wakeup(self):
        cur = WakeupCursor(press={"status": "CLOSED", "controller": "USER"}, article_count=1)
        self.assertFalse(api.maybe_start_team_wakeup(cur, 5, 2036))


class RouterTests(unittest.TestCase):
    def test_press_router_is_mounted(self):
        # FastAPI 0.12x keeps an include_router() as one _IncludedRouter; its endpoints live on original_router.
        paths = set()
        for route in api.app.routes:
            if getattr(route, "path", None):
                paths.add(route.path)
            for child in getattr(getattr(route, "original_router", None), "routes", []) or []:
                if getattr(child, "path", None):
                    paths.add(child.path)
        self.assertIn(ROUTER_PATH, paths)


# ----------------------------------------------------------------------------------------------- newsroom wiring
class _FailingCursor:
    """Any real database read raises, so the per-block try/except in compile_agent_prompt takes its fallback."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): raise RuntimeError("no database in tests")
    def fetchone(self): raise RuntimeError("no database in tests")
    def fetchall(self): raise RuntimeError("no database in tests")


class _FailingConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _FailingCursor()


class CompiledPromptTests(unittest.TestCase):
    """compile_agent_prompt with every database call stubbed out (persona, memory, staff, press brief)."""

    def setUp(self):
        patches = [
            mock.patch.object(swarm_dispatcher.psycopg, "connect", lambda *a, **k: _FailingConn()),
            mock.patch.object(swarm_dispatcher.persona_lib, "build_persona_block", lambda *a, **k: "PERSONA"),
            mock.patch.object(swarm_dispatcher, "league_staff_directory", lambda *a, **k: []),
            mock.patch.object(swarm_dispatcher.agent_memory, "build_memory_block", lambda *a, **k: ""),
            mock.patch.object(swarm_dispatcher, "fetch_moves", lambda *a, **k: []),
            mock.patch.object(swarm_dispatcher, "fetch_cap_rows", lambda *a, **k: []),
            mock.patch.object(swarm_dispatcher, "new_week_trades", lambda *a, **k: ([], [])),
            mock.patch.object(swarm_dispatcher, "trades_block", lambda *a, **k: ""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.dispatcher = swarm_dispatcher.SwarmDispatcher(debug_mode=False)

    def compiled(self, archetype, *, content_type="ARTICLE", assigned_team_id=5):
        return self.dispatcher.compile_agent_prompt(
            agent_id="writer1", archetype=archetype, persistent_memory="", handle="@writer1",
            display_name="Writer One", assigned_team_id=assigned_team_id, brief={"recap_week": 5},
            season=2036, week=6, content_type=content_type, article_type=None)

    @staticmethod
    def brief_of(prompt):
        body = prompt["task_instruction"].split("ASSIGNMENT BRIEF:\n", 1)[1]
        return json.loads(body.split("\n\nTASK:", 1)[0])

    def test_beat_brief_carries_the_press_block(self):
        block = {"subject": "Marcus Whitfield", "game": "Week 5 vs Atlanta, lost 17-24",
                 "exchanges": [{"reporter": "@beat", "question": "Coach, what happened?", "answer": "We own it."}]}
        with mock.patch.object(press_room, "brief_block", return_value=block), \
                mock.patch.object(press_room, "declined_note", return_value=None):
            brief = self.brief_of(self.compiled("LOCAL_BEAT_REPORTER"))
        self.assertEqual(brief["press_conference"], block)
        self.assertNotIn("press_conference_note", brief)

    def test_beat_brief_carries_the_declined_note(self):
        note = "Coach Marcus Whitfield did not take questions after Week 5 vs Atlanta; do not quote him on it."
        with mock.patch.object(press_room, "brief_block", return_value=None), \
                mock.patch.object(press_room, "declined_note", return_value=note):
            brief = self.brief_of(self.compiled("LOCAL_BEAT_REPORTER"))
        self.assertEqual(brief["press_conference_note"], note)
        self.assertNotIn("press_conference", brief)

    def test_beat_brief_has_no_press_keys_without_the_tables(self):
        with mock.patch.object(press_room, "brief_block", return_value=None), \
                mock.patch.object(press_room, "declined_note", return_value=None):
            brief = self.brief_of(self.compiled("LOCAL_BEAT_REPORTER"))
        self.assertNotIn("press_conference", brief)
        self.assertNotIn("press_conference_note", brief)

    def test_head_coach_quoting_rule_is_in_the_beat_directive_and_the_shared_rule(self):
        beat = self.compiled("LOCAL_BEAT_REPORTER")
        national = self.compiled("BARNWELL")
        self.assertIn(QUOTING_RULE, beat["task_instruction"])
        self.assertIn(QUOTING_RULE, national["task_instruction"])
        self.assertNotIn("Quote coaching staff sentiment", beat["task_instruction"])

    def test_the_press_tool_is_registered_and_named_only_in_the_beat_directive(self):
        names = [tool.name for tool in self.dispatcher.tools_for("LOCAL_POSTGAME_REPORT_CARD")]
        self.assertIn("get_press_conference", names)
        beat = self.compiled("LOCAL_BEAT_REPORTER")["task_instruction"]
        national = self.compiled("BARNWELL")["task_instruction"]
        self.assertIn("Query your team's recent postgame head-coach press conferences", beat)
        self.assertNotIn("Query your team's recent postgame head-coach press conferences", national)

    def test_dispatch_team_local_ensures_the_presser_first(self):
        dispatcher = swarm_dispatcher.SwarmDispatcher(debug_mode=False)
        with mock.patch.object(press_room, "ensure_team_presser", return_value="CLOSED") as ensure, \
                mock.patch.object(swarm_dispatcher.psycopg, "connect", side_effect=RuntimeError("stop here")):
            with self.assertRaises(RuntimeError):
                dispatcher.dispatch_team_local(team_id=5, brief={}, season=2036, week=6)
        ensure.assert_called_once_with(dispatcher.db.db_conn_info, 5, 2036, 5)


# ----------------------------------------------------------------------------------------------- Play Game hook
GAME = mod_packs.GameInfo(id=77, season=2036, week=5, game_time="Sun 8:20 PM", game_type="REGULAR_SEASON",
                          home="CAR", away="ATL", away_conference="NFC", division_game=True, home_pop_pct=0.9,
                          away_pop_pct=0.8, home_form=0.75)
BASE = [("m27_snf_presentation.fbmod", False), ("m27_fox_presentation.fbmod", True), ("Mudded Ball.fbmod", True),
        ("Other.fbmod", True)]


class LaunchCursor:
    def __init__(self, pending=0, team_id=5):
        self.pending, self.team_id, self.executed, self.rows = pending, team_id, [], []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "pending_moves" in sql:
            self.rows = [(self.pending,)]
        elif "q:launch_team" in sql:
            self.rows = [(self.team_id,)]
        else:
            self.rows = [(0,)]

    def fetchone(self):
        return self.rows[0] if self.rows else None


class GameLaunchPressTests(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(mod_packs, "load_games", lambda *a, **k: [GAME]),
            mock.patch.object(mod_packs, "current_week", lambda cur: (2036, 5)),
            mock.patch.object(mod_packs, "config_path", lambda: "/mnt/c/fake/manager_config.json"),
            mock.patch.object(mod_packs, "base_pack", lambda path: list(BASE)),
            mock.patch.object(mod_packs, "manager_running", lambda: False),
            mock.patch.object(mod_packs, "write_pack", lambda *a, **k: None),
            mock.patch.object(gl.subprocess, "run"),
            mock.patch.object(gl.subprocess, "Popen"),
            mock.patch.dict("os.environ", {"MMC_MANAGER_DIR": "/mnt/c/Madden27Modding/MMC"}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        process_check.set_process_check_mock(lambda: False)
        self.addCleanup(process_check.set_process_check_mock, None)

    def test_a_real_launch_declines_and_spawns_the_judgments_and_wakeup(self):
        cur = LaunchCursor()
        with mock.patch.object(press_room, "decline_open_for_launch", return_value=[11]) as decline:
            out = gl.launch("CAR", 2036, 5, cur=cur)
        self.assertIsNone(out["refused"])
        self.assertTrue(out["started"])
        decline.assert_called_once_with(cur, 5, 2036)
        popen = gl.subprocess.Popen
        popen.assert_called_once()
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:3], [gl.sys.executable, "-c", gl.PRESS_WAKEUP_CODE])
        self.assertEqual(argv[3:], ["[11]", "5", "2036", "5"])
        self.assertTrue(popen.call_args[1]["start_new_session"])

    def test_a_dry_run_declines_nothing_and_spawns_nothing(self):
        with mock.patch.object(press_room, "decline_open_for_launch") as decline:
            gl.launch("CAR", 2036, 5, dry_run=True, cur=LaunchCursor())
        decline.assert_not_called()
        gl.subprocess.Popen.assert_not_called()

    def test_a_refused_launch_leaves_the_presser_alone(self):
        with mock.patch.object(press_room, "decline_open_for_launch") as decline:
            out = gl.launch("CAR", 2036, 5, cur=LaunchCursor(pending=2))
        self.assertIn("pending moves", out["refused"])
        decline.assert_not_called()
        gl.subprocess.Popen.assert_not_called()

    def test_no_open_presser_means_no_detached_process(self):
        with mock.patch.object(press_room, "decline_open_for_launch", return_value=[]):
            gl.launch("CAR", 2036, 5, cur=LaunchCursor())
        gl.subprocess.Popen.assert_not_called()


# ------------------------------------------------------------------------------------------------ watcher hook
class WatcherCursor:
    def __init__(self, week, installed=True): self.week, self.installed, self.params = week, installed, None

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return self
    def execute(self, sql, params=None): self.params = params
    def fetchone(self): return (self.installed, self.week)


class WatchSavePressTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.snapshot = os.path.join(self.dir, "save.json")
        self.result = os.path.join(self.dir, "ingest_result.json")

    def fake_run(self, cmd, **kwargs):
        with open(self.result, "w") as fh:
            json.dump({"status": "success"}, fh)
        return SimpleNamespace(returncode=0)

    def test_press_week_is_the_databases_latest_completed_week(self):
        with mock.patch("psycopg.connect", return_value=WatcherCursor(5)):
            self.assertEqual(ws.press_week("dsn", 2036), 5)
        with mock.patch("psycopg.connect", return_value=WatcherCursor(None)):
            self.assertIsNone(ws.press_week("dsn", 2036))
        with mock.patch("psycopg.connect", return_value=WatcherCursor(5, installed=False)):
            self.assertIsNone(ws.press_week("dsn", 2036))      # unmigrated database: no spawn, old behaviour

    def test_start_press_week_spawns_run_week_detached(self):
        log_fn = mock.MagicMock()
        with mock.patch.object(ws.subprocess, "Popen") as popen:
            ws.start_press_week("host=x dbname=madden_fake", 2036, 5, log_fn=log_fn)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1:], ["-m", "newsroom.press_room", "run-week",
                                    "--season", "2036", "--week", "5"])
        self.assertEqual(popen.call_args[1]["env"]["DATABASE_URL"], "host=x dbname=madden_fake")
        self.assertTrue(popen.call_args[1]["start_new_session"])
        log_fn.assert_called_once()

    def test_run_ingest_spawns_the_press_week_after_the_ingest_child(self):
        order = []

        def fake_run(cmd, **kwargs):
            order.append("ingest")
            return self.fake_run(cmd, **kwargs)

        def fake_start(dsn, season, week, log_fn=None):
            order.append(("press", dsn, season, week))

        with mock.patch.object(ws.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(ws, "press_week", return_value=5) as press, \
                mock.patch.object(ws, "start_press_week", side_effect=fake_start), \
                mock.patch.object(ws, "log"):
            out = ws.run_ingest(self.snapshot, "host=x dbname=madden_fake", 2036, 6)
        self.assertEqual(out["status"], "success")
        press.assert_called_once_with("host=x dbname=madden_fake", 2036)
        self.assertEqual(order, ["ingest", ("press", "host=x dbname=madden_fake", 2036, 5)])

    def test_the_press_week_comes_from_the_database_not_save_week_minus_one(self):
        # A re-ingest or a gap: the save says 9, but the season's latest completed week is 4.
        with mock.patch.object(ws.subprocess, "run", side_effect=self.fake_run), \
                mock.patch.object(ws, "press_week", return_value=4), \
                mock.patch.object(ws, "start_press_week") as start, \
                mock.patch.object(ws, "log"):
            ws.run_ingest(self.snapshot, "dsn", 2036, 9)
        self.assertEqual(start.call_args[0][1:3], (2036, 4))

    def test_no_spawn_on_a_dry_run_skipped_articles_engines_or_a_preseason_save(self):
        cases = [dict(dry_run=True), dict(no_articles=True), dict(no_engines=True), dict(save_week=None)]
        for case in cases:
            with self.subTest(**case):
                with mock.patch.object(ws.subprocess, "run", side_effect=self.fake_run), \
                        mock.patch.object(ws, "press_week", return_value=5), \
                        mock.patch.object(ws, "start_press_week") as start, \
                        mock.patch.object(ws, "log"):
                    ws.run_ingest(self.snapshot, "dsn", 2036, case.get("save_week", 6),
                                  dry_run=case.get("dry_run", False),
                                  no_articles=case.get("no_articles", False),
                                  no_engines=case.get("no_engines", False))
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
