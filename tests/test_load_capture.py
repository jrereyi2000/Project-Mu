"""pipeline/load_capture.py helpers that need no database."""
import json, os, tempfile, unittest

from pipeline import load_capture as L


class ScopeTest(unittest.TestCase):
    cmap = {"FGM": ("fgm", L.num), "XPM": ("xpm", L.num)}

    def test_two_kickers_settled_by_their_teams_lists(self):
        rs = [{"NAME": "J.Delgado", "POS": "K", "FGM": "30", "XPM": "40"},
              {"NAME": "J.Delgado", "POS": "K", "FGM": "12", "XPM": "20"}]
        cands = [(747, 29), (1090, 5)]                    # (player id, team id)
        scoped = {5: [{"NAME": "J.Delgado", "POS": "K", "FGM": "30", "XPM": "40"}],
                  29: [{"NAME": "J.Delgado", "POS": "K", "FGM": "12", "XPM": "20"}]}
        self.assertEqual(L.assign_by_scope(rs, cands, scoped, self.cmap), {0: 1090, 1: 747})

    def test_one_team_settles_a_pair(self):
        rs = [{"NAME": "J.Delgado", "POS": "K", "FGM": "30", "XPM": "40"},
              {"NAME": "J.Delgado", "POS": "K", "FGM": "12", "XPM": "20"}]
        scoped = {5: [{"NAME": "J.Delgado", "POS": "K", "FGM": "30", "XPM": "40"}]}
        self.assertEqual(L.assign_by_scope(rs, [(747, 29), (1090, 5)], scoped, self.cmap), {0: 1090, 1: 747})

    def test_one_row_one_team_not_listing_it(self):
        rs = [{"NAME": "M.Peralta", "POS": "FB", "FGM": "3", "XPM": "0"}]
        scoped = {32: [{"NAME": "X.Other", "POS": "FB", "FGM": "1", "XPM": "0"}]}      # Chiefs' list: not him
        self.assertEqual(L.assign_by_scope(rs, [(504, 32), (8263, 18)], scoped, self.cmap), {0: 8263})

    def test_no_scoped_list_settles_nothing(self):
        rs = [{"NAME": "J.Delgado", "POS": "K", "FGM": "30", "XPM": "40"}]
        self.assertIsNone(L.assign_by_scope(rs, [(747, 29), (1090, 5)], {}, self.cmap))


class TeamLineTotalsTest(unittest.TestCase):
    def test_team_row_gets_attempts_and_tds_from_player_lines(self):
        """A captured box score has yards but no attempts or TDs; the team row sums its player lines."""
        class Cur:
            def execute(self, sql, params):
                self.sql, self.params = sql, params
            def fetchone(self):
                return (25, 38, 2, 31, 1)
        cur = Cur()
        totals = L.team_line_totals(cur, 7001, 12)
        self.assertEqual(totals, {"passing_completions": 25, "passing_attempts": 38, "passing_tds": 2,
                                  "rushing_attempts": 31, "rushing_tds": 1})
        self.assertIn("FROM player_stats", cur.sql)
        self.assertEqual(cur.params, (7001, 12))


class HeaderTest(unittest.TestCase):
    def test_sort_mark_stripped(self):
        self.assertEqual(L._header("▼YARDS"), "YARDS")
        self.assertEqual(L._header("*TAK"), "TAK")
        self.assertEqual(L._header("FG%"), "FG%")

    def test_missing_column_is_an_error(self):
        with self.assertRaises(L.LoadError):
            L.parse_row({"NAME": "X", "REC": "3"}, {"REC": ("receptions", L.num), "YARDS": ("rec_yards", L.num)}, "t")

    def test_general_ovr_fallback(self):
        row = {"NAME": "E.Roman", "OVR": "80"}
        dp_key = "DP" if "DP" in row else ("OVR" if "OVR" in row else "DP")
        vals = L.parse_row(row, {dp_key: ("downs_played", L.num)}, "test")
        self.assertEqual(vals, {"downs_played": 80})


class MisreadTest(unittest.TestCase):
    def test_one_row_read_twice(self):
        cmap = {"ATT": None, "YARDS": None, "TD": None}
        rows = [{"NAME": "B.Favors", "POS": "HB", "ATT": "38", "YARDS": "180", "TD": "1", "GP": "16", "DP": "242"},
                {"NAME": "B.Favors", "POS": "HB", "ATT": "38", "YARDS": "180", "TD": "7", "GP": "16", "DP": "242"}]
        self.assertEqual(L.misread_copies(rows, cmap), ["TD"])
        two = [dict(rows[0]), dict(rows[1], ATT="12", YARDS="50", TD="0", DP="90")]
        self.assertIsNone(L.misread_copies(two, cmap))

    def test_counters_exclude_yards_and_rates(self):
        self.assertEqual(L.counters_of(["rush_attempts", "rush_yards", "rush_avg", "rush_tds", "rush_long", "rec_rac"]),
                         ["rush_attempts", "rush_tds"])



class CurrentWeekTest(unittest.TestCase):
    def folder(self, labels=None):
        d = tempfile.mkdtemp()
        if labels:
            with open(os.path.join(d, "week.json"), "w") as fh:
                json.dump(labels, fh)
        return d

    def test_current_week_from_the_captured_label(self):
        d = self.folder({"current": "SEASON WEEK 18", "captured": "SEASON WEEK 17"})
        self.assertEqual(L.next_current_week(d, 17, 17)[0], 18)

    def test_postseason_label(self):
        d = self.folder({"current": "POST SEASON WILD CARD", "captured": "SEASON WEEK 18"})
        self.assertEqual(L.next_current_week(d, 18, 18)[0], 19)

    def test_no_week_json_takes_the_next_week(self):
        week, source = L.next_current_week(self.folder(), 17, 17)
        self.assertEqual(week, 18)
        self.assertIn("no week.json", source)

    def test_never_moves_back(self):
        self.assertEqual(L.next_current_week(self.folder(), 9, 18)[0], 18)

    def test_captured_week_must_be_the_loaded_week(self):
        d = self.folder({"current": "SEASON WEEK 18", "captured": "SEASON WEEK 16"})
        with self.assertRaises(L.LoadError):
            L.next_current_week(d, 17, 17)

    def test_current_week_must_follow_the_loaded_week(self):
        d = self.folder({"current": "SEASON WEEK 17", "captured": "SEASON WEEK 17"})
        with self.assertRaises(L.LoadError):
            L.next_current_week(d, 17, 17)
        d = self.folder({"current": "PRESEASON WEEK 1", "captured": "SEASON WEEK 17"})
        with self.assertRaises(L.LoadError):
            L.next_current_week(d, 17, 17)


if __name__ == "__main__":
    unittest.main()


class PlayoffGamesTest(unittest.TestCase):
    """add_playoff_games: playoff games come from the captured schedule (they aren't in the season schedule)."""

    class Cur:
        def __init__(self, existing=()):
            self.rows, self.existing, self._last = [], set(existing), None

        def execute(self, sql, params):
            if not sql.startswith("INSERT"):
                self._last = (len(self.rows),)
                return
            season, week, home, away, game_time, game_type = params
            new = (season, week, home, away) not in self.existing
            if new:
                self.existing.add((season, week, home, away))
                self.rows.append(params)
            self._last = (1,) if new else None

        def fetchone(self):
            return self._last

    sched = [{"WEEK": "Wild Card", "GAME TIME": "Sat 4:30 PM", "MATCHUP": "Steelers @ Ravens", "RESULT": "PIT 17 - 24 BAL"},
             {"WEEK": "Wild Card", "GAME TIME": "", "MATCHUP": "Packers @ Eagles", "RESULT": "GB 10 - 21 PHI"},
             {"WEEK": "Wild Card", "GAME TIME": "Sun 1:00 PM", "MATCHUP": "Nobody @ Eagles", "RESULT": "?"}]
    by_name = {"STEELERS": 1, "RAVENS": 2, "PACKERS": 3, "EAGLES": 4}

    def test_creates_typed_games_and_skips_unknown_teams(self):
        cur = self.Cur()
        self.assertEqual(L.add_playoff_games(cur, self.sched, 2035, 19, self.by_name), 2)
        self.assertEqual(cur.rows, [(2035, 19, 2, 1, "Sat 4:30 PM", "WILD_CARD"), (2035, 19, 4, 3, None, "WILD_CARD")])

    def test_existing_games_are_left_alone(self):
        cur = self.Cur(existing={(2035, 19, 2, 1)})
        self.assertEqual(L.add_playoff_games(cur, self.sched, 2035, 19, self.by_name), 1)

    def test_week_types(self):
        self.assertEqual(L.PLAYOFF_GAME_TYPES, {19: "WILD_CARD", 20: "DIVISIONAL", 21: "CONFERENCE", 23: "SUPER_BOWL"})


class NextRoundTest(unittest.TestCase):
    """load_next_round: schedule_next.csv (the coming playoff round) -> SCHEDULED games."""

    def folder(self, week_label):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "schedule_next.csv"), "w") as fh:
            fh.write("WEEK,GAME TIME,MATCHUP,RESULT,PLAYED,FORCE WIN\n")
            fh.write(f"{week_label},Sat 4:30 PM,Steelers @ Ravens,,0,None\n")
        return d

    def cur(self):
        return PlayoffGamesTest.Cur()

    def test_wild_card_round_after_week_18(self):
        problems = []
        got = L.load_next_round(self.cur(), self.folder("Wild Card"), 2035, 19, {"STEELERS": 1, "RAVENS": 2}, problems)
        self.assertEqual(problems, [])
        self.assertEqual(got, {"week": 19, "games": 1, "added": 1})

    def test_round_must_be_the_coming_week(self):
        problems = []
        L.load_next_round(self.cur(), self.folder("Divisional"), 2035, 19, {"STEELERS": 1, "RAVENS": 2}, problems)
        self.assertTrue(problems)

    def test_no_file_no_round(self):
        self.assertIsNone(L.load_next_round(self.cur(), tempfile.mkdtemp(), 2035, 19, {}, []))

    def test_playoff_folder_mode(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "game_scores.csv"), "w").close()
        self.assertEqual(L.detect_mode(d), "games")
        open(os.path.join(d, "schedule.csv"), "w").close()
        self.assertEqual(L.detect_mode(d), "playoff")


class SeasonEndTest(unittest.TestCase):
    def week_json(self, labels):
        d = tempfile.mkdtemp()
        json.dump(labels, open(os.path.join(d, "week.json"), "w"))
        return d

    def test_super_bowl_load_ends_the_season(self):
        """After the Super Bowl the schedule shows an offseason stage (no week): the league stays at 23."""
        d = self.week_json({"current": None, "captured": "POST SEASON SUPER BOWL"})
        self.assertEqual(L.next_current_week(d, 23, 22)[0], 23)

    def test_conference_load_moves_to_pro_bowl_week(self):
        d = self.week_json({"current": "POST SEASON PRO BOWL", "captured": "POST SEASON CONFERENCE",
                            "next": "POST SEASON SUPER BOWL"})
        self.assertEqual(L.next_current_week(d, 21, 21)[0], 22)

    def test_super_bowl_is_the_round_after_pro_bowl_week(self):
        d = NextRoundTest().folder("Super Bowl")
        problems = []
        got = L.load_next_round(PlayoffGamesTest.Cur(), d, 2035, 22, {"STEELERS": 1, "RAVENS": 2}, problems)
        self.assertEqual(problems, [])
        self.assertEqual(got["week"], 23)


class ProspectsLoadTest(unittest.TestCase):
    def test_detect_mode_prospects(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "prospects.csv"), "w").close()
        self.assertEqual(L.detect_mode(d), "prospects")

    def test_detect_mode_combined_roster_and_prospects(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "roster_cards.csv"), "w").close()
        open(os.path.join(d, "prospects.csv"), "w").close()
        self.assertEqual(L.detect_mode(d), "roster")

    class Cur:
        """Records the SQL the board load runs."""
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((" ".join(sql.split()), params))

        def executemany(self, sql, rows):
            self.calls.append((" ".join(sql.split()), list(rows)))

    def board(self, *rows):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "prospects.csv"), "w") as f:
            f.write("PROJECTED_PICK,PROJECTED_ROUND,ROUND_PICK,FULL_NAME,POSITION,COLLEGE,HEIGHT,WEIGHT,AGE,OVR\n")
            f.writelines(r + "\n" for r in rows)
        return d

    def load(self, d, dry_run=False):
        from unittest import mock
        cur, report = self.Cur(), {"dry_run": dry_run}
        export = os.path.join(tempfile.mkdtemp(), "2036_prospects.csv")
        orig_join = os.path.join
        with mock.patch("os.path.join", side_effect=lambda *a: export if "data" in a and "draft" in a else orig_join(*a)):
            count = L.load_prospects(cur, d, 2035, report)
        return count, cur, report, export

    def test_load_prospects_writes_board_and_export(self):
        d = self.board("1,1,1,Caleb Williams,QB,USC,6'1\",215,22,81", "2,1,2,Marvin Harrison Jr,WR,Ohio State,6'4\",205,21,80")
        count, cur, report, export = self.load(d)
        self.assertEqual((count, report["prospects_loaded"], report["draft_season"]), (2, 2, 2036))
        (delete, params), (insert, rows) = cur.calls
        self.assertTrue(delete.startswith("DELETE FROM draft_prospects") and params == (2036,))
        self.assertTrue(insert.startswith("INSERT INTO draft_prospects"))
        self.assertEqual(rows[0][:8], (2036, 1, "1", 1, "Caleb Williams", "QB", "USC", 73))
        self.assertEqual(rows[0][-1], os.path.basename(d))                  # source capture
        self.assertTrue(os.path.exists(export))

    def test_load_prospects_dry_run_writes_no_export(self):
        d = self.board("1,1,1,Caleb Williams,QB,USC,6'1\",215,22,81")
        _, _, _, export = self.load(d, dry_run=True)
        self.assertFalse(os.path.exists(export))           # the board rows roll back with the transaction

    def test_load_prospects_lenient(self):
        # JR, Oct 15: a blank or bad cell or two still loads; next season's roster scan fills them in
        d = self.board("1,1,1,Caleb Williams,UNKNOWN_POS,USC,6'1\",,22,81",
                       "2,1,2,,WR,Ohio State,6'4\",205,21,80",            # no name: skipped
                       "3,1,3,Rome Odunze,WR,,5'71\",212,22,79")
        count, cur, report, _ = self.load(d)
        self.assertEqual(count, 2)
        rows = cur.calls[1][1]
        self.assertEqual([(r[4], r[5], r[6], r[7], r[8]) for r in rows],
                         [("Caleb Williams", None, "USC", 73, None), ("Rome Odunze", "WR", None, None, 212)])
        self.assertEqual(len(report["prospect_warnings"]), 5)

    def test_load_prospects_empty_board_raises_load_error(self):
        d = self.board(",,,,QB,USC,6'1\",215,22,81")
        with self.assertRaises(L.LoadError):
            L.load_prospects(self.Cur(), d, 2035, {})


class LoadHighlightsTest(unittest.TestCase):
    def test_load_games_saves_highlights(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "game_scores.csv"), "w") as f:
            f.write("GAME,AWAY,HOME,A1,A2,A3,A4,AOT,ATOTAL,H1,H2,H3,H4,HOT,HTOTAL,QUARTERS_OK\n")
            f.write("01_super_bowl,BUF,NYJ,7,3,7,7,0,24,0,10,0,10,0,20,y\n")

        gdir = os.path.join(folder, "games", "01_super_bowl")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "box_score.csv"), "w") as f:
            f.write("LABEL,BUF,NYJ\n")
            f.write("TOTAL OFFENSE,350,300\n")

        hl_data = [{"quarter": "1ST", "clock": "10:00", "play_type": "TOUCHDOWN", "play": "Test TD"}]
        with open(os.path.join(gdir, "highlights.json"), "w") as f:
            json.dump(hl_data, f)

        class MockCur:
            def __init__(self):
                self.calls = []
            def execute(self, sql, params=None):
                self.calls.append((" ".join(sql.split()), params))
            def fetchall(self):
                last_sql = self.calls[-1][0] if self.calls else ""
                if "id, name, abbreviation" in last_sql:
                    return [(1, "Bills", "BUF"), (2, "Jets", "NYJ")]
                if "id, name FROM teams" in last_sql:
                    return [(1, "Bills"), (2, "Jets")]
                if "FROM players" in last_sql:
                    return []
                if "FROM games WHERE season" in last_sql:
                    return [(42, 23)]
                return []
            def fetchone(self):
                last_sql = self.calls[-1][0] if self.calls else ""
                if "COUNT(*)" in last_sql.upper() and "GAMES" in last_sql.upper():
                    return (1,)
                if "COUNT(*)" in last_sql.upper() and "PLAYER_STATS" in last_sql.upper():
                    return (0,)
                if "COALESCE(SUM" in last_sql:
                    return (0, 0, 0, 0, 0)
                return (1,)

        cur = MockCur()
        report = {}
        L.load_games(cur, folder, 2035, report)

        hl_calls = [c for c in cur.calls if "INSERT INTO game_highlights" in c[0]]
        self.assertEqual(len(hl_calls), 1)
        sql, params = hl_calls[0]
        self.assertEqual(params[:3], (42, 2035, 23))
        self.assertEqual(json.loads(params[3]), hl_data)

    def test_load_season_loads_injuries(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "season_schedule.csv"), "w") as f:
            f.write("WEEK,HOME,AWAY\n")
        with open(os.path.join(folder, "injuries.csv"), "w") as f:
            f.write("TEAM,PLAYER_NAME,POSITION,OVR,INJURY_TYPE,DURATION,WEEKS_REMAINING,IS_IR\n")
            f.write("BILLS,J.Allen,QB,99,Shoulder,3 Weeks,3,false\n")

        class MockCur:
            def __init__(self):
                self.calls = []
            def execute(self, sql, params=None):
                self.calls.append((" ".join(sql.split()), params))
            def fetchall(self):
                last_sql = self.calls[-1][0] if self.calls else ""
                if "id, name, abbreviation FROM teams" in last_sql:
                    return [(1, "Bills", "BUF")]
                if "FROM player_seasons" in last_sql:
                    return [(101, 1, "J.Allen", "Josh Allen", "QB", 99)]
                if "SELECT home_team_id" in last_sql:
                    return []
                return []
            def fetchone(self):
                return None

        cur = MockCur()
        report = {}
        with unittest.mock.patch.object(L, "load_season_schedule", return_value=0):
            L.load_season(cur, folder, 2036, report)

        self.assertEqual(report.get("injuries"), 1)
        injury_inserts = [c for c in cur.calls if "INSERT INTO injuries" in c[0]]
        self.assertEqual(len(injury_inserts), 1)
        sql, params = injury_inserts[0]
        self.assertEqual(params[:4], (2036, 1, 1, 101))
