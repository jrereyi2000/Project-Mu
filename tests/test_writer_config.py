"""Consistency checks between the Editorial Desk's writer names and the persona/curatorial configs. No database required.

Run: python3 -m unittest tests.test_writer_config
"""
import ast
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "newsroom", "config")


def load(name, key=None):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        data = json.load(f)
    return data[key] if key else data


def dispatcher_literal(name):
    """Reads a module-level literal from swarm_dispatcher.py without importing it (no psycopg needed)."""
    with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


class WriterConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.personas = load("agent_personas.json", "archetypes")
        cls.curatorial = load("curatorial_autonomy_directives.json")
        cls.writer_structures = load("article_structure_directives.json")
        cls.article_types = load("article_types.json", "article_types")
        cls.pools = dispatcher_literal("REGULAR_SEASON_FEATURE_POOLS")
        cls.aliases = dispatcher_literal("WRITER_ARCHETYPE_ALIASES")

    def columnists(self):
        return {c for phase in self.pools.values() for c in phase["columnists"]}

    def test_every_article_writer_has_a_persona(self):
        for at_id, info in self.article_types.items():
            for writer in info.get("allowed_writers", []):
                self.assertIn(writer, self.personas, f"{at_id} allows {writer}, which has no persona")

    def test_tweet_personas_have_an_article_voice(self):
        """A national writer whose persona is written for tweets needs an article_personas entry
        (FANTASY_* articles use fantasy_writers.json instead)."""
        article_personas = load("agent_personas.json", "article_personas")
        writers = {w for at_id, info in self.article_types.items() if not at_id.startswith("FANTASY_")
                   for w in info.get("allowed_writers", [])} | self.columnists()
        for w in sorted(writers):
            if "Your tweets" in self.personas.get(w, ""):
                self.assertIn(w, article_personas, f"{w} writes articles but only has a tweet persona")

    def test_every_rotating_columnist_has_a_persona_and_curatorial_directive(self):
        for writer in self.columnists():
            self.assertIn(writer, self.personas, f"columnist {writer} has no persona")
            self.assertIn(writer, self.curatorial, f"columnist {writer} has no curatorial directive")

    def test_rotating_columnists_can_write_opinion_columns(self):
        allowed = set(self.article_types["NATIONAL_OPINION_COLUMN"]["allowed_writers"])
        self.assertLessEqual(self.columnists(), allowed)

    def test_no_aliases_in_config(self):
        used = self.columnists() | {w for i in self.article_types.values() for w in i.get("allowed_writers", [])}
        self.assertFalse(used & set(self.aliases), f"use canonical archetypes, not aliases: {used & set(self.aliases)}")

    def test_aliases_point_at_real_personas(self):
        for alias, canonical in self.aliases.items():
            self.assertIn(canonical, self.personas, alias)

    def test_types_without_their_own_structure_have_one_per_writer(self):
        # Article types with no structure_directive fall back to the writer's column format.
        for at_id, info in self.article_types.items():
            if info.get("structure_directive"):
                continue
            for writer in info.get("allowed_writers", []):
                self.assertTrue(self.writer_structures.get(writer, "").strip(),
                                f"{at_id} has no structure of its own and {writer} has no column format")

    def test_no_injury_asks_in_beat_or_preview_configs(self):
        # The league has no injury data; these prompts must not ask for it.
        for at_id in ("LOCAL_POSTGAME_REPORT_CARD", "LOCAL_MATCHUP_PRIMER", "LOCAL_PLAYER_SPOTLIGHT",
                      "LOCAL_SEASON_PROGRESS_REPORT", "SLATE_LOOKAHEAD_PREDICTIONS"):
            info = self.article_types[at_id]
            text = " ".join(str(info.get(f) or "") for f in ("directive", "curatorial_autonomy", "structure_directive",
                                                              "example_prompt_hook", "data_queried"))
            self.assertNotIn("injur", text.lower(), at_id)

    def test_power_rankings_header_matches_continuity_block(self):
        st = self.article_types["POWER_RANKINGS"]["structure_directive"]
        self.assertIn("## [Rank]. [City] [Team] ([W-L]) (Previous: N | Change: ±N)", st)
        self.assertNotIn("Rise:", st)

    def test_awards_cover_offensive_and_defensive_races(self):
        """MVP, OPOY, OROY, DPOY and DROY in the weekly tracker and both postseason overrides (JR, Oct 14)."""
        info = self.article_types["AWARDS_TRACKER"]
        overrides = {o["override_name"]: o for o in load("playoff_overrides.json", "playoff_overrides")["AWARDS_TRACKER"]}
        texts = {"TRACKER": info["structure_directive"] + info["curatorial_autonomy"],
                 "FINALISTS": overrides["AWARDS_FINALISTS"]["structure_directive_override"]
                              + overrides["AWARDS_FINALISTS"]["curatorial_autonomy_override"],
                 "WINNERS": overrides["AWARDS_WINNERS"]["structure_directive_override"]
                            + overrides["AWARDS_WINNERS"]["curatorial_autonomy_override"]}
        for name, text in texts.items():
            for phrase in ("Offensive Rookie", "Defensive Player of the Year", "Defensive Rookie",
                           "get_defense_special_teams_leaders", "rookies_only=True", "not zeros"):
                self.assertIn(phrase, text, f"{name}: {phrase}")
        self.assertNotIn("after the defensive breakdown", info["structure_directive"])  # the old contradiction
        self.assertIn("six winners", overrides["AWARDS_WINNERS"]["structure_directive_override"])  # + Coach of the Year

    def test_awards_tracker_covers_coach_of_the_year(self):
        info = self.article_types["AWARDS_TRACKER"]
        for key in ("focus", "structure_directive", "curatorial_autonomy", "directive", "example_prompt_hook"):
            self.assertIn("Coach of the Year", info[key], key)
        self.assertIn("get_coach_of_the_year_race", info["structure_directive"])
        overrides = {o["override_name"]: o for o in load("playoff_overrides.json", "playoff_overrides")["AWARDS_TRACKER"]}
        for name in ("AWARDS_FINALISTS", "AWARDS_WINNERS"):
            o = overrides[name]
            for key in ("directive_override", "curatorial_autonomy_override", "structure_directive_override"):
                self.assertIn("Coach of the Year", o[key], f"{name} {key}")
            self.assertIn("get_coach_of_the_year_race", o["curatorial_autonomy_override"], name)

    def test_coach_profile_is_registered_and_hooked_up(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertGreaterEqual(src.count("get_coach_profile"), 5)  # import, tool list, both research blocks
        self.assertEqual(src.count("COACHING TENDENCIES:"), 2)       # national and beat research blocks
        self.assertIn("Never turn a tier into a percentage", src)
        self.assertEqual(src.count("Man coverage is the staff's real-world philosophy"), 2)
        self.assertEqual(src.count("There is no blitz data"), 2)
        self.assertIn("measured from this league's results", src)
        self.assertNotIn("Scheme talk (coverage shells, fronts, blitz looks) is your read of the numbers", src)
        for name in ("PLAYOFF_AUTOPSY", "FRANCHISE_STATE_OF_THE_UNION", "LOCAL_SEASON_PROGRESS_REPORT",
                     "SINGLE_GAME_SCHEMATIC_AUTOPSY", "QUARTERBACK_DUEL_TAPE_BREAKDOWN",
                     "SUPER_BOWL_COMPREHENSIVE_PREVIEW", "SLATE_LOOKAHEAD_PREDICTIONS", "LOCAL_MATCHUP_PRIMER"):
            text = self.article_types[name]["curatorial_autonomy"]
            self.assertIn("COACHING DATA:", text, name)
            self.assertIn("never turn them into percentages", text, name)
        overrides = load("playoff_overrides.json", "playoff_overrides")
        for name in ("SINGLE_GAME_SCHEMATIC_AUTOPSY", "SLATE_LOOKAHEAD_PREDICTIONS"):
            for o in overrides[name]:
                self.assertIn("coaching_matchup", o["curatorial_autonomy_override"], o["override_name"])

    def test_playoff_autopsy_audits_the_coaching_staff(self):
        """Section 4 is a coaching-staff accountability audit with a concrete verdict (Agent 2)."""
        entry = self.article_types["PLAYOFF_AUTOPSY"]
        autonomy = entry["curatorial_autonomy"]
        for phrase in ("get_coach_profile", "Tenure:", "Playoff record with the team:", "Game management:"):
            self.assertIn(phrase, autonomy)
        structure = entry["structure_directive"]
        self.assertIn("4. Coaching Staff Accountability & Seat Temperature:\n"
                      "Use a ## header (e.g., ## The Hot Seat & Coordinator Cross-Examination).", structure)
        section4 = structure.split("4. Coaching Staff Accountability")[1].split("5. The Spring Mandate")[0]
        for phrase in ("ultimatum", "red-zone", "fourth-down", "third-down", "get_team_efficiency_profile",
                       "dismissal, coordinator overhaul, or ascending foundation patience"):
            self.assertIn(phrase, section4)
        self.assertNotIn("Front Office & Coaching Seat Temperature", structure)

    def test_player_splits_are_registered_and_hooked_up(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertGreaterEqual(src.count("get_player_scheme_splits"), 4)  # import, tool list, both research blocks
        self.assertEqual(src.count("don't cite splits for that player"), 2)
        for name in ("LOCAL_PLAYER_SPOTLIGHT", "PLAYER_PROGRESS_REPORT", "QUARTERBACK_DUEL_TAPE_BREAKDOWN",
                     "LOCAL_MATCHUP_PRIMER", "FANTASY_START_SIT", "FANTASY_MATCHUP_EXPLOITS", "FANTASY_PLAYOFF_STREAMERS"):
            text = self.article_types[name]["curatorial_autonomy"]
            self.assertIn("SPLITS DATA:", text, name)
            self.assertIn("get_player_scheme_splits", text, name)

    def test_coach_of_the_year_tool_is_registered(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            self.assertGreaterEqual(f.read().count("get_coach_of_the_year_race"), 3)  # import, tool list, guideline

    def test_roundtable_is_always_the_panel(self):
        self.assertEqual(self.article_types["SUPER_BOWL_ROUNDTABLE_CONSENSUS"]["allowed_writers"], ["ROUNDTABLE_PANEL"])

    def test_tweet_pool_excludes_article_only_voices(self):
        excluded = set(dispatcher_literal("TWEET_EXCLUDED_ARCHETYPES"))
        self.assertTrue({"ROUNDTABLE_PANEL", "SKIP_BAYLESS", "LOCAL_BEAT_REPORTER"} <= excluded)
        self.assertNotIn("SKIP_TWEET", excluded)  # Skip still tweets, once, through his tweet account

    def test_no_contract_asks(self):
        # Contracts aren't modelled; prompts may forbid citing them but must not ask for them.
        asks = ("contract-year", "analyzing the salary cap", "cap space", "cap investment", "extension value", "to their contract")
        texts = {f"persona {k}": v for k, v in self.personas.items()}
        for at_id, info in self.article_types.items():
            texts[at_id] = " ".join(str(v) for v in info.values())
        for name, text in texts.items():
            for phrase in asks:
                self.assertNotIn(phrase, text.lower(), f"{name} asks about '{phrase}'")

    def test_historical_comparisons_are_optional(self):
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertIn("HISTORICAL COMPARISONS (OPTIONAL)", src)
        self.assertNotIn("State the historical comparison explicitly", src)

    def test_top_100_counts_down_and_ends_on_number_one(self):
        info = self.article_types["NFL_TOP_100_HIERARCHY"]
        text = " ".join(str(info[f]) for f in ("directive", "structure_directive", "example_prompt_hook")).lower()
        self.assertIn("no. 100 to no. 1", text)
        self.assertIn("end the article with the no. 1 entry", text)
        self.assertNotIn("### the snub", text)
        self.assertNotIn("dissect controversial snubs", text)

    def test_top_100_pool_is_rated_not_popular(self):
        cur = self.article_types["NFL_TOP_100_HIERARCHY"]["curatorial_autonomy"]
        self.assertIn("get_top_rated_players", cur)
        self.assertNotIn("get_top_popular_players", cur)
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            self.assertIn("            get_top_rated_players,", f.read())  # registered as a writer tool

    def test_week19_directives_use_defense_data_not_untracked_metrics(self):
        """Defense and special-teams stats are captured, but pressures, snaps and targets aren't (JR, Oct 14)."""
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertIn("            get_defense_special_teams_leaders,", src)  # registered as a writer tool
        self.assertIn("DEFENSIVE DATA", src)
        overrides = load("playoff_overrides.json", "playoff_overrides")
        texts = {
            "ALL_PRO": self.article_types["OFFICIAL_AP_ALL_PRO_ROSTER"]["curatorial_autonomy"],
            "PRIMER": self.article_types["LOCAL_MATCHUP_PRIMER"]["curatorial_autonomy"],
            "LOOKAHEAD": overrides["SLATE_LOOKAHEAD_PREDICTIONS"][0]["curatorial_autonomy_override"],
        }
        for name, text in texts.items():
            self.assertIn("get_defense_special_teams_leaders", text, name)
        self.assertNotIn("Third-down stop percentage", self.article_types["LOCAL_MATCHUP_PRIMER"]["structure_directive"])
        self.assertNotIn("2026 AP All-Pro", self.article_types["OFFICIAL_AP_ALL_PRO_ROSTER"]["structure_directive"])

    def test_situational_directives_use_recorded_team_stats(self):
        """Third downs, red zone and penalties come from the team stats; no down-and-distance or blitz splits."""
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            self.assertIn("team_situational", f.read())
        overrides = load("playoff_overrides.json", "playoff_overrides")
        texts = {k: self.article_types[k]["structure_directive"] for k in
                 ("SLATE_LOOKAHEAD_PREDICTIONS", "LOCAL_SEASON_PROGRESS_REPORT", "SUPER_BOWL_CONTENDER_BLUEPRINT",
                  "PLAYOFF_AUTOPSY", "QUARTERBACK_DUEL_TAPE_BREAKDOWN", "SUPER_BOWL_COMPREHENSIVE_PREVIEW")}
        texts["POSTGAME"] = self.article_types["LOCAL_POSTGAME_REPORT_CARD"]["curatorial_autonomy"]
        texts["AUTOPSY"] = self.article_types["SINGLE_GAME_SCHEMATIC_AUTOPSY"]["curatorial_autonomy"]
        texts["PRIMER"] = self.article_types["LOCAL_MATCHUP_PRIMER"]["curatorial_autonomy"]
        for key, i in (("SINGLE_GAME_SCHEMATIC_AUTOPSY", 0), ("SINGLE_GAME_SCHEMATIC_AUTOPSY", 1),
                       ("SLATE_LOOKAHEAD_PREDICTIONS", 0), ("SLATE_LOOKAHEAD_PREDICTIONS", 1)):
            texts[f"{key}[{i}]"] = overrides[key][i]["structure_directive_override"]
        for name, text in texts.items():
            self.assertIn("get_team_efficiency_profile", text, name)
        self.assertNotIn("conversion rates against blitz packages",
                         self.article_types["QUARTERBACK_DUEL_TAPE_BREAKDOWN"]["structure_directive"])

    def test_research_blocks_keep_playoff_stats_out_of_season_totals(self):
        """Season Stats are regular season only (JR, Oct 14): playoff numbers come from playoff box scores."""
        with open(os.path.join(ROOT, "newsroom", "swarm_dispatcher.py")) as f:
            src = f.read()
        self.assertEqual(src.count("cover the regular season only; playoff numbers come only from playoff box scores"), 2)
        self.assertIn("postseason_stats", src)

    def test_player_selection_directives_include_defense(self):
        """Spotlight, progress report and X-Factor can pick defenders and specialists from recorded stats (JR, Oct 14)."""
        for key in ("LOCAL_PLAYER_SPOTLIGHT", "PLAYER_PROGRESS_REPORT", "X_FACTOR_VOLATILITY_SPOTLIGHT"):
            info = self.article_types[key]
            text = info["curatorial_autonomy"] + info["structure_directive"]
            self.assertIn("get_defense_special_teams_leaders", info["curatorial_autonomy"], key)
            self.assertIn("not zeros", info["curatorial_autonomy"], key)
            for untracked in ("pressure rate creates", "target volume", "snap rate", "situational snap counts"):
                self.assertNotIn(untracked, text, f"{key}: {untracked}")
        curatorial = load("curatorial_autonomy_directives.json")
        structure = load("article_structure_directives.json")
        for writer in ("KIMES", "ACHO", "JOYNER"):
            self.assertIn("get_defense_special_teams_leaders", curatorial[writer], writer)
        self.assertIn("get_defense_special_teams_leaders", structure["ACHO"])

    def test_bayless_columns_use_his_article_persona(self):
        self.assertEqual(self.aliases.get("BAYLESS"), "SKIP_BAYLESS")
        self.assertIn("SKIP_BAYLESS", self.article_types["NATIONAL_OPINION_COLUMN"]["allowed_writers"])

    def test_mel_kiper_mock_draft_config(self):
        """Mel Kiper Jr. is configured for OFFSEASON_MOCK_DRAFT with archetype and article persona."""
        self.assertIn("MEL_KIPER", self.personas)
        article_personas = load("agent_personas.json", "article_personas")
        self.assertIn("MEL_KIPER", article_personas)
        mock_config = self.article_types["OFFSEASON_MOCK_DRAFT"]
        self.assertIn("MEL_KIPER", mock_config["allowed_writers"])
        self.assertIn("Round 1", mock_config["directive"])
        self.assertIn("get_mock_draft", mock_config["directive"])

    def test_mock_draft_is_picks_1_to_24_on_the_scouted_board(self):
        """Week 20's mock draft covers the 24 picks locked after the Wild Card round (handoff, Agent 3)."""
        mock_config = self.article_types["OFFSEASON_MOCK_DRAFT"]
        queried = " ".join(mock_config["data_queried"])
        for tool in ("get_draft_order", "get_mock_draft", "get_scouted_draft_board", "get_team_draft_needs"):
            self.assertIn(tool, queried)
            self.assertIn(tool, mock_config["curatorial_autonomy"])
        self.assertIn("1\u201324", mock_config["name"])
        self.assertIn("Picks 1\u201324", mock_config["structure_directive"])
        self.assertNotIn("1\u201332", mock_config["structure_directive"])
        self.assertIn("## [Pick Number]. [Team Name]: [Prospect Name], [Position], [School]", mock_config["structure_directive"])
        self.assertNotIn("get_draft_prospects", json.dumps(self.article_types))

    def test_draft_blueprint_uses_locked_picks_needs_and_the_scouted_board(self):
        """Week 21's roster audit cites locked picks 1-28, top needs and only real prospects (handoff, Agent 4)."""
        blueprint = self.article_types["DRAFT_BLUEPRINT_ROSTER_AUDIT"]
        queried = " ".join(blueprint["data_queried"])
        autonomy = blueprint["curatorial_autonomy"]
        for tool in ("get_draft_order", "get_team_draft_needs", "get_scouted_draft_board"):
            self.assertIn(tool, queried)
            self.assertIn(tool, autonomy)
        self.assertIn("picks 1–28", autonomy)
        for field in ("`locked`", "`selection_status`", "`position` filter"):
            self.assertIn(field, autonomy)
        self.assertIn("0.35", autonomy)
        self.assertIn("Purgatory Penalty", autonomy)
        self.assertIn("picks 12–20", autonomy)
        self.assertIn("Never invent a prospect", autonomy)
        self.assertIn("null", autonomy)
        structure = blueprint["structure_directive"]
        self.assertIn("exact locked pick number", structure)
        self.assertIn("top 3 positional needs", structure)
        self.assertIn("3–4 Featured Franchises", structure)

    def test_super_bowl_media_interview_audit_coach_milestones(self):
        """SUPER_BOWL_MEDIA_INTERVIEW_AUDIT queries get_coach_profile, mandates it in curatorial autonomy,
        and contrasts dynasty validation vs first-time participant pressure in structure Sections 1 & 2."""
        info = self.article_types["SUPER_BOWL_MEDIA_INTERVIEW_AUDIT"]
        queried = " ".join(info.get("data_queried", []))
        self.assertIn("Career Postseason & Super Bowl Coaching Records (get_coach_profile)", queried)
        self.assertIn("get_coach_profile", queried)

        curatorial = info.get("curatorial_autonomy", "")
        self.assertIn("get_coach_profile", curatorial)
        for phrase in ("both head coaches", "career postseason records", "Super Bowl wins/losses", "tenure length"):
            self.assertIn(phrase.lower(), curatorial.lower())

        structure = info.get("structure_directive", "")
        sections = structure.split("\n\n")
        sec1 = [s for s in sections if s.strip().startswith("1.")][0]
        sec2 = [s for s in sections if s.strip().startswith("2.")][0]

        for sec in (sec1, sec2):
            self.assertIn("dynasty validation", sec)
            self.assertIn("first-time participant", sec)
            self.assertIn("Super Bowl media circus", sec)

    def test_franchise_state_of_the_union_rookie_coach_alignment(self):
        """FRANCHISE_STATE_OF_THE_UNION includes draft order and mock draft tools in data_queried and Coach-Rookie Alignment Test."""
        config = self.article_types["FRANCHISE_STATE_OF_THE_UNION"]
        queried = " ".join(config["data_queried"])
        self.assertIn("Round 1 Draft Order (get_draft_order)", config["data_queried"])
        self.assertIn("Round 1 Mock Draft Simulation (get_mock_draft)", config["data_queried"])
        self.assertIn("get_draft_order", queried)
        self.assertIn("get_mock_draft", queried)

        curatorial = config["curatorial_autonomy"]
        self.assertIn("Coach-Rookie Alignment Test", curatorial)
        self.assertIn("get_draft_order", curatorial)
        self.assertIn("get_mock_draft", curatorial)
        self.assertIn("get_team_draft_needs", curatorial)
        self.assertIn("get_coach_profile", curatorial)
        self.assertIn("When analyzing early-lottery teams (Picks 1–5)", curatorial)
        self.assertIn("clean coaching break", curatorial)

    def test_super_bowl_comprehensive_preview_coaching_and_efficiency(self):
        """SUPER_BOWL_COMPREHENSIVE_PREVIEW mandates coaching matchup tendencies and situational efficiency."""
        sb_config = self.article_types["SUPER_BOWL_COMPREHENSIVE_PREVIEW"]
        queried = " ".join(sb_config["data_queried"])
        self.assertIn("get_matchup_edges", queried)
        self.assertIn("get_team_efficiency_profile", queried)

        curatorial = sb_config["curatorial_autonomy"]
        self.assertIn("coaching_matchup", curatorial)
        self.assertIn("get_matchup_edges", curatorial)
        self.assertIn("pass lean", curatorial)
        self.assertIn("fourth-down aggressiveness", curatorial)
        self.assertIn("defensive front", curatorial)
        self.assertIn("coverage tendency", curatorial)
        self.assertIn("get_team_efficiency_profile", curatorial)

        structure = sb_config["structure_directive"]
        self.assertIn("4. The Chess Match: Coordinators and Scheme Exploitation:", structure)
        self.assertIn("pass lean", structure)
        self.assertIn("fourth-down aggressiveness", structure)
        self.assertIn("defensive front", structure)
        self.assertIn("coverage philosophy", structure)
        self.assertIn("red-zone touchdown conversion rates", structure)
        self.assertIn("third-down ranks", structure)
        self.assertIn("get_team_efficiency_profile", structure)


if __name__ == "__main__":
    unittest.main()
