"""Comprehensive unit and integration tests for Newsroom & Social Desk injury integration."""
import json
import unittest
import psycopg

from db.db_handler import DatabaseHandler
from newsroom.db_tools import get_team_injury_report
from newsroom.fantasy_tools import get_fantasy_injuries, FANTASY_TOOLS
from newsroom.fantasy_desk import FANTASY_DESK_GUIDELINES
from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
from newsroom.social_desk import load_week_context, plan_social_week, plan_team_wakeup
from newsroom.social_writer import build_post_prompt, KIND_INSTRUCTIONS


import pytest


@pytest.mark.integration
class NewsroomInjuriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_conn_info = DatabaseHandler().db_conn_info
        cls.dispatcher = SwarmDispatcher()

    def test_get_team_injury_report(self):
        res_str = get_team_injury_report.invoke({"team_identifier": "LV", "week": 18, "season": 2035})
        data = json.loads(res_str)
        self.assertIn("team", data)
        self.assertEqual(data["season"], 2035)
        self.assertIn("injuries", data)
        self.assertIn("key_injuries_count", data)
        for inj in data["injuries"]:
            self.assertIn("player_name", inj)
            self.assertIn("position", inj)
            self.assertIn("overall_rating", inj)
            self.assertIn("popularity_index", inj)
            self.assertIn("is_starter", inj)
            self.assertIn("is_key_injury", inj)
            self.assertIn("injury_type", inj)
            self.assertIn("duration", inj)
            self.assertIn("weeks_remaining", inj)
            self.assertIn("is_ir", inj)
            # Threshold rule check: if OVR >= 88 or Pop >= 50, must be flagged is_key_injury
            if inj["overall_rating"] >= 88 or inj["popularity_index"] >= 50.0:
                self.assertTrue(inj["is_key_injury"], f"{inj['player_name']} must be marked as key injury")

    def test_get_fantasy_injuries(self):
        self.assertIn(get_fantasy_injuries, FANTASY_TOOLS)
        res_str = get_fantasy_injuries.invoke({"week": 18, "position": "ALL", "season": 2035})
        data = json.loads(res_str)
        self.assertEqual(data["season"], 2035)
        self.assertIn("injuries", data)
        self.assertGreater(data["count"], 0)
        for inj in data["injuries"]:
            self.assertIn(inj["fantasy_group"], ("QB", "RB", "WR", "TE"))
            self.assertIn("depth_role", inj)
            self.assertIn("fantasy_impact", inj)

        # Test position filter
        res_rb = json.loads(get_fantasy_injuries.invoke({"week": 18, "position": "RB", "season": 2035}))
        for inj in res_rb["injuries"]:
            self.assertEqual(inj["fantasy_group"], "RB")

    def test_preview_slate_key_injuries_injection(self):
        needs_temp_status = False
        target_week = 18
        with psycopg.connect(self.db_conn_info) as conn, conn.cursor() as cur:
            cur.execute("SELECT MIN(week) FROM games WHERE season = 2035 AND status = 'SCHEDULED'")
            row = cur.fetchone()
            if row and row[0] is not None:
                target_week = row[0]
            else:
                needs_temp_status = True
                cur.execute("UPDATE games SET status = 'SCHEDULED' WHERE season = 2035 AND week = 18")
                conn.commit()

        try:
            brief = build_weekly_assignment_brief(self.db_conn_info, season=2035, week=target_week)
            self.assertIn("preview_slate", brief)
            games = brief["preview_slate"]["all_games"]
            self.assertGreater(len(games), 0)
            # Check if games have key_injuries dictionary
            games_with_injuries = [g for g in games if "key_injuries" in g]
            self.assertGreater(len(games_with_injuries), 0)
            for g in games_with_injuries:
                self.assertIn("away", g["key_injuries"])
                self.assertIn("home", g["key_injuries"])
                for inj in g["key_injuries"]["away"] + g["key_injuries"]["home"]:
                    self.assertTrue(
                        inj["overall_rating"] >= 88 or inj["popularity_index"] >= 50.0 or inj.get("is_starting_qb"),
                        f"Injury {inj} does not meet national threshold"
                    )
        finally:
            if needs_temp_status:
                with psycopg.connect(self.db_conn_info) as conn, conn.cursor() as cur:
                    cur.execute("UPDATE games SET status = 'COMPLETED' WHERE season = 2035 AND week = 18")
                    conn.commit()

    def test_beat_brief_team_injuries_injection(self):
        brief = build_weekly_assignment_brief(self.db_conn_info, season=2035, week=18)
        # Compile prompt for a Rams (team 19) beat reporter
        prompt = self.dispatcher.compile_agent_prompt(
            agent_id="beat_lar_1",
            archetype="LOCAL_BEAT_REPORTER",
            persistent_memory="",
            handle="@RamsBeat",
            display_name="Rams Insider",
            assigned_team_id=19,
            brief=brief,
            season=2035,
            week=18,
            content_type="ARTICLE",
            article_type="LOCAL_MATCHUP_PRIMER",
        )
        task_instruction = prompt["task_instruction"]
        self.assertIn("team_injury_report", task_instruction)
        self.assertIn("starter_injuries", task_instruction)

    def test_directives_no_legacy_prohibitions(self):
        brief = build_weekly_assignment_brief(self.db_conn_info, season=2035, week=18)
        # Prospective national writer prompt
        prompt = self.dispatcher.compile_agent_prompt(
            agent_id="writer_barnwell",
            archetype="BARNWELL",
            persistent_memory="",
            handle="@BillBarnwell",
            display_name="Bill Barnwell",
            assigned_team_id=None,
            brief=brief,
            season=2035,
            week=18,
            content_type="ARTICLE",
            article_type="SLATE_LOOKAHEAD_PREDICTIONS",
        )
        task_inst = prompt["task_instruction"]
        self.assertNotIn("There is no injury data in this league: do not mention injuries", task_inst)
        self.assertIn("INJURY REPORTING RULE (THRESHOLD 88 OVR / 50 POP / STARTING QB)", task_inst)
        self.assertIn("get_team_injury_report", task_inst)

        # Beat reporter prompt
        beat_prompt = self.dispatcher.compile_agent_prompt(
            agent_id="beat_lar_1",
            archetype="LOCAL_BEAT_REPORTER",
            persistent_memory="",
            handle="@RamsBeat",
            display_name="Rams Beat",
            assigned_team_id=19,
            brief=brief,
            season=2035,
            week=18,
            content_type="ARTICLE",
            article_type="LOCAL_MATCHUP_PRIMER",
        )
        beat_task = beat_prompt["task_instruction"]
        self.assertNotIn("There is no injury data in this league: do not mention injuries", beat_task)
        self.assertIn("BEAT INJURY REPORTING RULE", beat_task)

        # Fantasy Desk Guidelines
        self.assertNotIn("NO INJURIES: This league has no injury data", FANTASY_DESK_GUIDELINES)
        self.assertIn("FANTASY INJURY & OPPORTUNITY RULE", FANTASY_DESK_GUIDELINES)

    def test_social_desk_injury_integration(self):
        with psycopg.connect(self.db_conn_info) as conn, conn.cursor() as cur:
            ctx = load_week_context(cur, 2035, 18)
        self.assertIn("injuries", ctx)
        self.assertGreater(len(ctx["injuries"]), 0)

        # Weekly plan
        plan = plan_social_week(ctx, use_expected_articles=True)
        assignments = plan["assignments"]
        self.assertEqual(len(assignments), 100)

        # Check BEAT_INJURY assignments
        beat_injury_posts = [a for a in assignments if a["kind"] == "BEAT_INJURY"]
        self.assertGreater(len(beat_injury_posts), 0)
        for post in beat_injury_posts:
            self.assertEqual(post["archetype"], "LOCAL_BEAT_REPORTER")
            self.assertIn("status_update", post["facts"])
            self.assertIn("injury_type", post["facts"])

        # Check INJURY_SHOCK reactions
        shock_posts = [a for a in assignments if a["kind"] == "INJURY_SHOCK"]
        self.assertGreater(len(shock_posts), 0)
        for post in shock_posts:
            self.assertIn("superstar_name", post["facts"])
            self.assertIn("injury_type", post["facts"])

        # Team wakeup check
        wakeup = plan_team_wakeup(ctx, team_id=19)
        wakeup_kinds = [a["kind"] for a in wakeup["assignments"]]
        self.assertIn("BEAT_INJURY", wakeup_kinds)
        self.assertIn("INJURY_SHOCK", wakeup_kinds)

        # Verify social prompt builder handles new kinds
        self.assertIn("BEAT_INJURY", KIND_INSTRUCTIONS)
        self.assertIn("INJURY_SHOCK", KIND_INSTRUCTIONS)
        for a in wakeup["assignments"]:
            if a["kind"] in ("BEAT_INJURY", "INJURY_SHOCK"):
                sys_inst, user_prompt = build_post_prompt(a, a, "You are a football account.")
                self.assertIn(f"POST TYPE: {a['kind']}", user_prompt)
                self.assertIn("FACTS:", user_prompt)


if __name__ == "__main__":
    unittest.main()
