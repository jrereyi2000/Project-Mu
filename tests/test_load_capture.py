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


class HeaderTest(unittest.TestCase):
    def test_sort_mark_stripped(self):
        self.assertEqual(L._header("▼YARDS"), "YARDS")
        self.assertEqual(L._header("*TAK"), "TAK")
        self.assertEqual(L._header("FG%"), "FG%")

    def test_missing_column_is_an_error(self):
        with self.assertRaises(L.LoadError):
            L.parse_row({"NAME": "X", "REC": "3"}, {"REC": ("receptions", L.num), "YARDS": ("rec_yards", L.num)}, "t")


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
