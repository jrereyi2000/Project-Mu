"""Console-free checks of madden_capture.py (key map, week labels)."""
import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("madden_capture.py needs macOS (Quartz, Vision)")

import madden_capture as mc


class KeyMapTest(unittest.TestCase):
    def test_chiaki_keyboard_map(self):
        # chiaki-ng "Keyboard as controller": Cross=S Circle=D L1=2 R1=3 L2=1 R2=4, Esc
        self.assertEqual(mc.KEYCODES["cross"], 1)
        self.assertEqual(mc.KEYCODES["circle"], 2)
        self.assertEqual(mc.KEYCODES["escape"], 53)


class WeekLabelTest(unittest.TestCase):
    def test_clean_week_fixes_ocr_digits(self):
        self.assertEqual(mc.clean_week("SEASON WEEKT"), "SEASON WEEK 1")
        self.assertEqual(mc.clean_week("season week 1O"), "SEASON WEEK 10")
        self.assertEqual(mc.clean_week("POST SEASON WILDCARD"), "POST SEASON WILD CARD")

    def test_week_order(self):
        self.assertLess(mc.week_index("PRESEASON WEEK 3"), mc.week_index("SEASON WEEK 1"))
        self.assertEqual(mc.week_index("SEASON WEEK 18"), 18)
        self.assertEqual(mc.week_index("POST SEASON PRO BOWL"), 22)       # Madden's order is games.week
        self.assertEqual(mc.week_index("POST SEASON SUPER BOWL"), 23)
        self.assertEqual(mc.clean_week("POST SEASON PROBOWL"), "POST SEASON PRO BOWL")

    def test_previous_week(self):
        self.assertEqual(mc.previous_week("SEASON WEEK 9"), "SEASON WEEK 8")
        self.assertEqual(mc.previous_week("POST SEASON WILD CARD"), "SEASON WEEK 18")
        with self.assertRaises(mc.Abort):
            mc.previous_week("SEASON WEEK 1")
        self.assertEqual(mc.previous_week("POST SEASON PRO BOWL"), "POST SEASON CONFERENCE")
        # Pro Bowl week opens to Preseason Week 1 because it has no games
        self.assertEqual(mc.previous_week("PRESEASON WEEK 1"), "POST SEASON CONFERENCE")

    def test_next_round(self):
        """The coming playoff round recorded before stepping back: the Super Bowl from Pro Bowl week."""
        self.assertEqual(mc.next_round("POST SEASON WILD CARD", "SEASON WEEK 18"), "POST SEASON WILD CARD")
        self.assertEqual(mc.next_round("POST SEASON PRO BOWL", "POST SEASON CONFERENCE"), "POST SEASON SUPER BOWL")
        self.assertIsNone(mc.next_round(None, "POST SEASON SUPER BOWL"))            # offseason
        self.assertIsNone(mc.next_round("SEASON WEEK 12", "SEASON WEEK 11"))
        self.assertIsNone(mc.next_round("POST SEASON DIVISIONAL", "POST SEASON DIVISIONAL"))

    def test_pro_bowl_week_schedule_resolution(self):
        """On Pro Bowl week Madden opens the schedule to Preseason Week 1; run_schedule recognizes this."""
        with tempfile.TemporaryDirectory() as out:
            f = mock.Mock(text="PRESEASON WEEK 1 | FORCE WIN | BACK", items=[])
            mc.CURRENT_WEEK = "PRESEASON WEEK 1"
            with mock.patch.object(mc, "open_league_schedule", return_value=f), \
                    mock.patch.object(mc, "go_to_week", return_value=f), \
                    mock.patch.object(mc, "capture_list", return_value=1), \
                    mock.patch.object(mc, "log"):
                mc.run_schedule(out)
            import json
            labels = json.load(open(os.path.join(out, "week.json")))
            self.assertEqual(labels["current"], "POST SEASON PRO BOWL")
            self.assertEqual(labels["captured"], "POST SEASON CONFERENCE")
            self.assertEqual(labels["next"], "POST SEASON SUPER BOWL")

    def test_menu_index(self):
        """Dropdown physical order: Season Week 1-18, Wild Card, Divisional, Conference, Super Bowl, Preseason Week 1-4.
        Super Bowl sits directly between Conference and Preseason Week 1."""
        self.assertEqual(mc.menu_index("SEASON WEEK 18"), 18)
        self.assertEqual(mc.menu_index("POST SEASON WILD CARD"), 19)
        self.assertEqual(mc.menu_index("POST SEASON DIVISIONAL"), 20)
        self.assertEqual(mc.menu_index("POST SEASON CONFERENCE"), 21)
        self.assertEqual(mc.menu_index("POST SEASON SUPER BOWL"), 22)
        self.assertEqual(mc.menu_index("PRESEASON WEEK 1"), 23)
        self.assertEqual(mc.menu_index("PRESEASON WEEK 4"), 26)

        # Preseason Week 1 -> Super Bowl is UP (23 > 22)
        self.assertGreater(mc.menu_index("PRESEASON WEEK 1"), mc.menu_index("POST SEASON SUPER BOWL"))
        # Super Bowl -> Conference is UP (22 > 21)
        self.assertGreater(mc.menu_index("POST SEASON SUPER BOWL"), mc.menu_index("POST SEASON CONFERENCE"))
        # Conference -> Super Bowl is DOWN (21 < 22)
        self.assertLess(mc.menu_index("POST SEASON CONFERENCE"), mc.menu_index("POST SEASON SUPER BOWL"))


class OcrTextTest(unittest.TestCase):
    def test_cyrillic_lookalikes_become_latin(self):
        self.assertEqual("MCPH\u0415\u0415".translate(mc.HOMOGLYPHS), "MCPHEE")


class _Frame:
    """classify() only needs .text and .find(); boxes are irrelevant here."""
    def __init__(self, *labels):
        self.items = [(t, (0, 0, 1, 1)) for t in labels]
        self.text = " | ".join(labels)

    find = mc.Frame.find


class ClassifyTest(unittest.TestCase):
    def test_roster_list_is_not_player_stats(self):
        # The roster list also shows PLAYER NAME (sort chip) and VIEW PLAYER CARD.
        f = _Frame("PANTHERS", "ROSTER", "NAME", "PLAYER NAME", "MOR", "SAVINGS", "PENALTY", "VIEW PLAYER CARD")
        self.assertEqual(mc.classify(f), "ROSTER_LIST")

    def test_roster_list_while_button_bar_redraws(self):
        f = _Frame("PANTHERS", "ROSTER", "NAME", "PLAYER NAME", "MOR", "SAVINGS", "PENALTY")
        self.assertEqual(mc.classify(f), "ROSTER_LIST")

    def test_player_stats_still_recognised(self):
        f = _Frame("NFL", "R2", "PASSING", "PLAYER NAME", "VIEW PLAYER CARD", "YARDS")
        self.assertEqual(mc.classify(f), "PLAYER_STATS")

    def test_player_stats_scrolled_right(self):
        # Oct 14: after a stream restart mid-Defense the table was at its right edge, PLAYER NAME scrolled away
        f = _Frame("NFLPA", "SS #21", "NFL", "TFL", "SACK", "VIEW PLAYER CARD", "SORT", "R2", "INT", "BACK",
                   "DEFENSE", "INTYDS", "FFUMB", "FMBREC", "GP", "DOWNS PLAYED", "DP")
        self.assertEqual(mc.classify(f), "PLAYER_STATS")

    def test_locked_menu_item_still_found(self):
        f = _Frame("TRADE Q", "TEAM STATS")
        self.assertIsNotNone(f.find("TRADE"))
        self.assertIsNone(f.find("TEAM"))            # a real different label isn't a match

    def test_team_chip_must_be_a_team(self):
        self.assertEqual(len(mc.TEAMS), 32)
        f = _Frame("NAME")
        f.size = (3024, 1888)
        f.items = [("NAME", (100, 400, 200, 430))]
        with mock.patch.object(mc, "Frame", return_value=f), mock.patch.object(mc.time, "sleep"):
            self.assertIsNone(mc.roster_team(f))

    def test_box_score(self):
        f = _Frame("L1", "BOX SCORE", "PLAYER STATS", "BACK", "DET", "OFF YARDS GAINED", "OFF RUSH YARDS")
        self.assertEqual(mc.classify(f), "BOX_SCORE")

    def test_box_score_scrolled_to_bottom(self):
        f = _Frame("L1", "BOX SCORE", "PLAYER STATS", "R1", "TOTAL YARDS GAINED", "TIME OF POSSESSION")
        self.assertEqual(mc.classify(f), "BOX_SCORE")

    def test_game_player_stats(self):
        # also shows TEAM NAME / SORT / R2 like Team Stats; the BOX SCORE | PLAYER STATS tabs decide
        f = _Frame("L1", "BOX SCORE", "PLAYER STATS", "R1", "BOTH", "PASSING", "TEAM", "NAME", "TEAM NAME", "SORT")
        self.assertEqual(mc.classify(f), "GAME_STATS")

    def test_postgame_menu(self):
        f = _Frame("FINISH GAME", "VIEW HIGHLIGHTS", "PLAYER STATS", "TEAM STATS", "SCORE SUMMARY")
        self.assertEqual(mc.classify(f), "POSTGAME_MENU")

    def test_postgame_team_stats_box_score(self):
        f = _Frame("L1", "TEAM STATS", "PLAYER STATS", "SCORING SUMMARY", "R1", "ATL", "CAR", "FIRST DOWNS", "TOTAL OFFENSE")
        self.assertEqual(mc.classify(f), "BOX_SCORE")

    def test_postgame_team_stats_scrolled_to_bottom(self):
        f = _Frame("L1", "TEAM STATS", "PLAYER STATS", "SCORING SUMMARY", "R1", "ATL", "CAR", "PENALTIES", "POSSESSION TIME")
        self.assertEqual(mc.classify(f), "BOX_SCORE")

    def test_postgame_player_stats(self):
        f = _Frame("L1", "TEAM STATS", "PLAYER STATS", "SCORING SUMMARY", "R1", "BOTH", "R2", "PASSING", "TEAM", "PLAYER")
        self.assertEqual(mc.classify(f), "GAME_STATS")

    def test_highlights_screen(self):
        f = _Frame("TIME", "RATING", "PLAY", "VIEW PLAY", "CHANGE QUARTER", "BACK", "4TH", "OT", "TOTAL")
        self.assertEqual(mc.classify(f), "HIGHLIGHTS")

    def test_manage_roster_hub(self):
        f = _Frame("GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "LEAGUE", "COACH CENTRAL", "UPGRADE PLAYERS",
                   "ADJUST LINEUP", "ROSTER", "DEPTH CHART")
        self.assertEqual(mc.classify(f), "HUB_ROSTER")

    def test_sched_tiles_regular(self):
        f = _Frame("TEAM SCHEDULE", "LEAGUE SCHEDULE", "PLAYOFF PICTURE")
        self.assertEqual(mc.classify(f), "SCHED_TILES")

    def test_sched_tiles_playoff(self):
        f = _Frame("PLAYOFF SCHEDULE", "TEAM SCHEDULE", "PLAYOFF PICTURE")
        self.assertEqual(mc.classify(f), "SCHED_TILES")

    def test_sched_tiles_stop_debug_frame(self):
        path = os.path.join(os.path.dirname(__file__), "..", "captures", "2026-10-15_2009", "stop_debug",
                            "202855_expected_SCHED_TILES_got_UNKNOWN.png")
        if os.path.exists(path):
            class StopFrame:
                def __init__(self, p):
                    self.path = p
                    self.items = mc.ocr(p)
                    self.text = " | ".join(t for t, _ in self.items)
                find = mc.Frame.find
            f = StopFrame(path)
            self.assertEqual(mc.classify(f), "SCHED_TILES")


class DraftRouteTest(unittest.TestCase):
    """Manage Roster > Scouting > Prospects > L3 > Edit Draft Class > Draft Board (mapped Oct 15)."""
    SCOUTING = _Frame("HIRE SCOUTS", "PROSPECTS", "SCOUTS", "REGION BREAKDOWN", "MOCK DRAFTS",
                      "TOP 10 PROSPECTS", "DAVE VOGEL", "SELECT", "KEY INFO", "BACK")
    PROSPECTS = _Frame("NATIONAL", "WEST", "LEDG", "ATTRIBUTES", "FAV", "RANK", "NAME", "D. VOGEL", "PROJ",
                       "Round 1", "Top 5", "SCOUTED", "VIEW PLAYERCARD", "SORT", "SET FAVORITE", "EDIT PLAYERS", "BACK")
    PROMPT = _Frame("EDIT DRAFT CLASS", "DO YOU WANT TO PROCEED?", "DOWNLOAD FROM DOWNLOAD CENTER",
                    "EDIT DRAFT CLASS", "IMPORT LOCAL FILE", "SELECT", "BACK")
    BOARD = _Frame("LEDG #72", "OVR75", "ALL ROUNDS", "PICK -", "ROUND 1, PICK 1", "R2", "ROSTER", "NAME",
                   "D.VOGEL", "EDIT PLAYER", "SORT", "ADJUST ORDER", "BACK")

    def test_l3_key(self):
        self.assertEqual(mc.KEYCODES["l3"], 23)        # chiaki-ng: L3 = 5

    def test_screens(self):
        self.assertEqual(mc.classify(self.SCOUTING), "SCOUTING")
        self.assertEqual(mc.classify(self.PROMPT), "EDIT_CLASS_PROMPT")
        self.assertEqual(mc.classify(self.BOARD), "DRAFT_BOARD")

    def test_prospects_list_is_not_the_draft_board(self):
        # its PROJ column reads "Round 1", which the old DRAFT_BOARD check took for the board
        self.assertEqual(mc.classify(self.PROSPECTS), "SCOUT_PROSPECTS")

    def test_prompt_cross_only_on_edit_draft_class(self):
        with mock.patch.object(mc, "prompt_option", return_value="DOWNLOAD FROM DOWNLOAD CENTER"), \
                mock.patch.object(mc, "press") as press:
            with self.assertRaises(mc.Abort):
                mc.cross("EDIT_CLASS_PROMPT", self.PROMPT)
        press.assert_not_called()
        with mock.patch.object(mc, "prompt_option", return_value="EDIT DRAFT CLASS"), \
                mock.patch.object(mc, "press") as press:
            mc.cross("EDIT_CLASS_PROMPT", self.PROMPT)
        press.assert_called_once()

    def test_scouting_cross_only_on_prospects(self):
        f = mock.Mock()
        f.highlighted.return_value = "MOCK DRAFTS"
        with mock.patch.object(mc, "press") as press:
            with self.assertRaises(mc.Abort):
                mc.cross("SCOUTING", f)
        press.assert_not_called()

    def test_postgame_cross_allowed_only_on_stats(self):
        f = mock.Mock()
        for item, allowed in (("FINISH GAME", False), ("VIEW HIGHLIGHTS", True),
                              ("SCORE SUMMARY", False), ("TEAM STATS", True), ("PLAYER STATS", True)):
            f.highlighted.return_value = item
            with mock.patch.object(mc, "press") as press:
                if allowed:
                    mc.cross("POSTGAME_MENU", f)
                    press.assert_called_once_with("cross", wait=1.5)
                else:
                    with self.assertRaises(mc.Abort):
                        mc.cross("POSTGAME_MENU", f)
                    press.assert_not_called()

    def test_no_cross_on_the_board(self):
        # the board's Cross is EDIT PLAYER
        with mock.patch.object(mc, "press") as press:
            with self.assertRaises(mc.Abort):
                mc.cross("DRAFT_BOARD", self.BOARD)
        press.assert_not_called()

    def test_prompt_option_ignores_the_title(self):
        f = mock.Mock(size=(3024, 1888), path="unused.png",
                      items=[("EDIT DRAFT CLASS", (1266, 992, 1754, 1067)),          # title, mid-screen
                             ("DOWNLOAD FROM DOWNLOAD CENTER", (1270, 1383, 1754, 1423)),
                             ("EDIT DRAFT CLASS", (1385, 1471, 1639, 1510)),
                             ("IMPORT LOCAL FILE", (1389, 1559, 1631, 1598))])
        lit = {1471: 155, 992: 200}                 # title brighter than any button: still ignored
        with mock.patch.object(mc, "brightness", side_effect=lambda p, b: lit.get(b[1], 55)):
            self.assertEqual(mc.prompt_option(f), "EDIT DRAFT CLASS")

    # Back on the board: the prompt is drawn over the board, whose text still reads
    EXIT_PROMPT = _Frame(*BOARD.text.split(" | "), "DRAFT CLASS EDITED", "EXPORT LOCAL FILE",
                         "UPLOAD TO DOWNLOAD CENTER", "SAVE AND EXIT", "SELECT")

    def test_exit_prompt_over_the_board(self):
        self.assertEqual(mc.classify(self.EXIT_PROMPT), "EXIT_CLASS_PROMPT")

    def test_exit_cross_only_on_save_and_exit(self):
        for cur, allowed in (("EXPORT LOCAL FILE", False), ("UPLOAD TO DOWNLOAD CENTER", False), ("SAVE AND EXIT", True)):
            with mock.patch.object(mc, "prompt_option", return_value=cur), mock.patch.object(mc, "press") as press:
                if allowed:
                    mc.cross("EXIT_CLASS_PROMPT", self.EXIT_PROMPT)
                else:
                    with self.assertRaises(mc.Abort):
                        mc.cross("EXIT_CLASS_PROMPT", self.EXIT_PROMPT)
            self.assertEqual(press.called, allowed, cur)

    def test_close_draft_board(self):
        # Back opens the prompt on EXPORT LOCAL FILE; two Downs reach SAVE AND EXIT
        screens = iter([(self.BOARD, "DRAFT_BOARD"), (self.EXIT_PROMPT, "EXIT_CLASS_PROMPT"),
                        (self.EXIT_PROMPT, "EXIT_CLASS_PROMPT")])
        with mock.patch.object(mc, "look", side_effect=lambda: next(screens)), \
                mock.patch.object(mc, "wait_for", return_value=(self.EXIT_PROMPT, "EXIT_CLASS_PROMPT")), \
                mock.patch.object(mc, "prompt_option",
                                  side_effect=["EXPORT LOCAL FILE", "UPLOAD TO DOWNLOAD CENTER", "SAVE AND EXIT"]), \
                mock.patch.object(mc, "press") as press, mock.patch.object(mc, "log"), \
                mock.patch.object(mc, "cross_into", return_value=(self.SCOUTING, "SCOUTING")) as cross_into:
            self.assertEqual(mc.close_draft_board(), (self.SCOUTING, "SCOUTING"))
        self.assertEqual([c.args[0] for c in press.call_args_list], ["circle", "down", "down"])
        cross_into.assert_called_once_with("EXIT_CLASS_PROMPT", ["SCOUTING"], timeout=10)

    def test_go_hub_leaves_the_board_through_its_prompt(self):
        hub = _Frame("GAMEDAY", "COACH CENTRAL")
        screens = iter([(self.BOARD, "DRAFT_BOARD"), (hub, "HUB")])
        with mock.patch.object(mc, "look", side_effect=lambda: next(screens)), \
                mock.patch.object(mc, "close_draft_board") as close, \
                mock.patch.object(mc, "press") as press:
            self.assertEqual(mc.go_hub(), (hub, "HUB"))
        close.assert_called_once()
        press.assert_not_called()                  # no bare Back on the board

    def run_board(self, frames_moving, end_text, start="HUB_ROSTER"):
        """run_prospects with the console faked: the table moves for `frames_moving` rows, then stays still."""
        sigs = iter(list(range(frames_moving)) + ["still"] * 50)
        out = tempfile.mkdtemp()
        with mock.patch.object(mc, "look", return_value=(None, start)), \
                mock.patch.object(mc, "open_prospects") as open_, \
                mock.patch.object(mc, "close_draft_board") as close, \
                mock.patch.object(mc, "snap", side_effect=lambda p: open(p, "w").close()), \
                mock.patch.object(mc, "table_sig", side_effect=lambda p: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc, "Frame", return_value=mock.Mock(text=end_text)), \
                mock.patch.object(mc, "press"), mock.patch.object(mc, "log"):
            rows = mc.run_prospects(out, max_prospects=0)
        return rows, open_, close, sorted(os.listdir(os.path.join(out, "prospects")))

    def test_board_end_on_undrafted_rows(self):
        rows, _, close, files = self.run_board(5, "Undrafted | J.Cowart | QB")
        self.assertEqual(rows, 6)                  # 5 moving frames + the last still one
        self.assertEqual(len(files), 6)
        close.assert_called_once()

    def test_board_stall_before_undrafted_fails(self):
        # Oct 15: the board stopped moving at row 18 after chiaki-ng stopped drawing
        with self.assertRaisesRegex(mc.Abort, "stopped moving"):
            self.run_board(5, "Round 1, Pick 18 | S.Beatty")

    def test_starting_on_the_board_reopens_it(self):
        _, open_, close, _ = self.run_board(2, "Undrafted", start="DRAFT_BOARD")
        open_.assert_called_once()
        self.assertEqual(close.call_count, 2)      # once to reset the highlight, once at the end

    def test_open_prospects_from_the_prospects_list(self):
        # look(): the Prospects list, then the prompt again after the Down press
        screens = iter([(self.PROSPECTS, "SCOUT_PROSPECTS"), (self.PROMPT, "EDIT_CLASS_PROMPT")])
        with mock.patch.object(mc, "look", side_effect=lambda: next(screens)), \
                mock.patch.object(mc, "press_into", return_value=(self.PROMPT, "EDIT_CLASS_PROMPT")) as l3, \
                mock.patch.object(mc, "prompt_option", side_effect=["DOWNLOAD FROM DOWNLOAD CENTER", "EDIT DRAFT CLASS"]), \
                mock.patch.object(mc, "press") as press, \
                mock.patch.object(mc, "cross_into", return_value=(self.BOARD, "DRAFT_BOARD")) as cross_into:
            self.assertIs(mc.open_prospects(), self.BOARD)
        l3.assert_called_once()
        press.assert_called_once_with("down")
        cross_into.assert_called_once_with("EDIT_CLASS_PROMPT", ["DRAFT_BOARD"], timeout=10)


# Apple Vision output for real card crops (captures/2026-10-13_explore, Oct 13)
CRAWFORD = [("GEORGE", (46, 54, 132, 82)), ("CRAWFORD", (42, 86, 362, 156)),
            ("#10 @B I SCRAMBLER I FRANCHISE QB", (48, 166, 485, 187)),
            ("SUPERSTAR X-FACTOR | OMAHA", (222, 238, 563, 268)),
            ("AGE", (42, 312, 90, 342)), ("22", (44, 346, 86, 386)), ("HT", (178, 312, 212, 344)),
            ("6'3\"", (178, 344, 242, 386)), ("WT", (312, 314, 350, 344)), ("229", (310, 346, 371, 386)),
            ("EXP", (443, 312, 489, 342)), ("MORALE", (579, 312, 659, 342)), ("99%", (577, 344, 647, 386))]
HERRERA = [("DIEGO", (44, 54, 112, 86)), ("HERRERA", (44, 83, 294, 157)),
           ("#51C POWER | RISING STAR", (44, 168, 385, 194)), ("STAR", (118, 242, 172, 264)),
           ("AGE", (42, 312, 90, 344)), ("23", (44, 346, 88, 386)), ("HT", (178, 312, 212, 344)),
           ("6'6\"", (178, 340, 248, 386)), ("WT", (310, 312, 352, 344)), ("342", (310, 346, 369, 386)),
           ("EXP", (443, 312, 489, 344)), ("ROOKIE", (441, 346, 527, 386)), ("MORALE", (579, 312, 659, 342))]
# HT label not read; value still found by its column
DAVIS = [("TREY'MANE", (44, 53, 168, 87)), ("DAVIS", (43, 84, 217, 158)),
         ("#6 WR | PHYSICAL | ELITE PLAYER", (44, 168, 423, 194)),
         ("SUPERSTAR X-FACTOR | YAC 'EM UP", (222, 238, 623, 268)), ("AGE", (42, 312, 90, 342)),
         ("24", (44, 346, 86, 386)), ("6'4\"", (178, 344, 242, 386)), ("WT", (312, 314, 350, 344)),
         ("223", (310, 346, 371, 386)), ("EXP", (443, 312, 489, 342)), ("3", (445, 348, 467, 384))]


class CardParseTest(unittest.TestCase):
    def test_crawford(self):
        c = mc.parse_card(CRAWFORD)
        self.assertEqual((c["first"], c["last"], c["number"], c["position"]), ("George", "Crawford", 10, "QB"))
        self.assertEqual((c["archetype"], c["dev"], c["age"], c["height"], c["weight"]),
                         ("Scrambler", "Superstar X-Factor", 22, "6'3\"", 229))
        self.assertIsNone(c["experience"])
        self.assertEqual(mc.card_flags(c), ["missing_experience"])

    def test_rookie_and_glued_number(self):
        c = mc.parse_card(HERRERA)
        self.assertEqual((c["number"], c["position"], c["archetype"], c["dev"]), (51, "C", "Power", "Star"))
        self.assertEqual(c["experience"], 0)
        self.assertEqual(mc.card_flags(c), [])

    def test_hidden_rookie_dev(self):
        # Madden hides a rookie's dev until 500 snaps (JR, 2026-10-20): store it as Hidden, not blank
        items = [("ENRIQUE", (46, 54, 150, 82)), ("VALLES", (42, 86, 232, 156)),
                 ("#77 DT | NOSE TACKLE | PROJECT PLAYER", (48, 166, 515, 190)),
                 ("HIDDEN | 0/500 SNAPS TO REVEAL", (118, 238, 490, 266)),
                 ("AGE", (42, 312, 90, 342)), ("22", (44, 346, 86, 386)), ("HT", (178, 312, 212, 344)),
                 ("6'1\"", (178, 344, 242, 386)), ("WT", (312, 314, 350, 344)), ("332", (310, 346, 371, 386)),
                 ("EXP", (443, 312, 489, 342)), ("ROOKIE", (441, 346, 527, 386)), ("MORALE", (579, 312, 659, 342))]
        c = mc.parse_card(items)
        self.assertEqual((c["dev"], c["experience"]), ("Hidden", 0))
        self.assertNotIn("missing_dev", mc.card_flags(c))

    def test_value_without_its_label(self):
        c = mc.parse_card(DAVIS)
        self.assertEqual((c["height"], c["experience"], c["role"]), ("6'4\"", 3, "Elite Player"))

    def test_number_zero_read_as_letter_o(self):
        items = [("CHUCK", (44, 54, 120, 84)), ("BYRNE", (45, 84, 225, 158)),
                 ("#O LS | POWER | BORDERLINE STARTER", (42, 162, 482, 194)), ("NORMAL", (118, 241, 210, 265)),
                 ("AGE", (42, 312, 90, 342))]
        c = mc.parse_card(items)
        self.assertEqual((c["first"], c["last"], c["number"], c["position"], c["archetype"]),
                         ("Chuck", "Byrne", 0, "LS", "Power"))

    def test_repeated_list_name_settled_by_position(self):
        # Seahawks, Oct 13: S.Lewis LT and S.Lewis HB on adjacent rows
        lines = [[("S.LEWIS", (0, 0, 100, 30)), ("LT", (770, 0, 800, 30))],
                 [("S.LEWIS", (0, 50, 100, 80)), ("HB", (770, 50, 800, 80))]]
        W, x0 = 3024, 3024 * mc.LIST_BOX[0]
        self.assertEqual(mc.list_row_for(lines, {"first": "Shonn", "last": "Lewis", "position": "HB"}, W, x0)["i"], 1)
        self.assertEqual(mc.list_row_for(lines, {"first": "Shaq", "last": "Lewis", "position": "LT"}, W, x0)["i"], 0)
        self.assertTrue(mc.list_row_for(lines, {"first": "Shaq", "last": "Lewis", "position": "QB"}, W, x0)["ambiguous"])

    def test_step_check(self):
        W, x0 = 3024, 3024 * mc.LIST_BOX[0]
        row = lambda name, pos, age, y: [(name, (0, y, 100, y + 30)), (pos, (770, y, 800, y + 30)),
                                         (age, (955, y, 985, y + 30))]
        lines = [row("J.GREEN", "WR", "23", 0), row("C.CALLAWAY", "LT", "29", 50),
                 row("C.BYRNE", "LS", "28", 100), row("J.LEWIS", "RT", "24", 150)]
        green = {"first": "Josh", "last": "Green", "position": "WR", "age": 23}
        callaway = {"first": "Cole", "last": "Callaway", "position": "LT", "age": 29}
        byrne = {"first": "Chuck", "last": "Byrne", "position": "LS", "age": 28}
        lewis = {"first": "Jake", "last": "Lewis", "position": "RT", "age": 24}
        self.assertEqual(mc.check_step(lines, green, callaway, W, x0), ("ok", 0))
        self.assertEqual(mc.check_step(lines, callaway, lewis, W, x0), ("skipped", 1))
        # new card's name unread: the line under the previous player must match it by position
        self.assertEqual(mc.check_step(lines, callaway, dict(byrne, first=None, last=None), W, x0), ("ok", 0))
        self.assertEqual(mc.check_step(lines, callaway, dict(lewis, first=None, last=None), W, x0), ("unknown", 0))

    def test_step_check_with_misread_initial(self):
        # Steelers, Oct 13: "0.CAMPBELL" on the list, POS not read
        W, x0 = 3024, 3024 * mc.LIST_BOX[0]
        lines = [[("C.HANSEN", (0, 0, 100, 30)), ("WR", (770, 0, 800, 30))],
                 [("0.CAMPBELL", (0, 50, 100, 80)), ("28", (955, 50, 985, 80))],
                 [("E.ORTIZ", (0, 100, 100, 130)), ("RT", (770, 100, 800, 130))]]
        campbell = {"first": "Omar", "last": "Campbell", "position": "QB", "age": 28}
        ortiz = {"first": "Enrique", "last": "Ortiz", "position": "RT", "age": 24}
        self.assertEqual(mc.check_step(lines, campbell, ortiz, W, x0), ("ok", 0))

    def test_name_suffix_read_differently(self):
        # Cardinals, Oct 13: card "Nolen Iii", list "W.NOLEN IIL"
        W, x0 = 3024, 3024 * mc.LIST_BOX[0]
        lines = [[("W.NOLEN IIL", (0, 0, 100, 30)), ("31", (955, 0, 985, 30))],
                 [("F.MENDOZA", (0, 50, 100, 80)), ("QB", (770, 50, 800, 80))]]
        nolen = {"first": "Walter", "last": "Nolen Iii", "position": "DT", "age": 31}
        mendoza = {"first": "Fernando", "last": "Mendoza", "position": "QB", "age": 32}
        self.assertEqual(mc.check_step(lines, nolen, mendoza, W, x0), ("ok", 0))
        self.assertEqual(mc._strip_suffix("T.Zuhn III"), "T.Zuhn")
        self.assertEqual(mc._strip_suffix("Brian Thomas Jr"), "Brian Thomas")
        self.assertEqual(mc._strip_suffix("Ivy"), "Ivy")

    def test_height_checks(self):
        base = {k: 1 for k in mc.CARD_FIELDS}
        base.update(age=24, weight=200, experience=2)
        for ht, flag in (("6']\"", "bad_height"), ("5'71\"", "bad_height"), ("6'7\"", "suspect_height")):
            self.assertIn(flag, mc.card_flags(dict(base, height=ht)), ht)
        self.assertEqual(mc.card_flags(dict(base, height="6'7\"", weight=320)), [])


class ScheduleCrossRuleTest(unittest.TestCase):
    """Cross on the League Schedule list only opens a played game's box score."""

    def frame(self, week, bar, extra=()):
        f = _Frame(f"SEASON WEEK {week}", "MATCHUP", "FORCE WIN", "SEA 22 - 17 DAL", bar, *extra)
        f.size = (3024, 1888)
        f.path = "unused.png"
        f.items = [(t, (0, 1800 if t in (bar,) + tuple(extra) else 700, 10, 1810 if t in (bar,) + tuple(extra) else 710))
                   for t in f.text.split(" | ")]
        return f

    def test_played_week_allowed(self):
        with mock.patch.object(mc, "highlighted_schedule_row", return_value=["SEAHAWKS @ COWBOYS", "SEA 22 - 17 DAL"]):
            self.assertIsNone(mc.schedule_cross_ok(self.frame(17, "BOX SCORE"), "SEASON WEEK 18"))

    def test_refusals(self):
        played = ["SEAHAWKS @ COWBOYS", "SEA 22 - 17 DAL"]
        with mock.patch.object(mc, "highlighted_schedule_row", return_value=played):
            self.assertIn("not before", mc.schedule_cross_ok(self.frame(18, "BOX SCORE"), "SEASON WEEK 18"))
            self.assertIn("BOX SCORE", mc.schedule_cross_ok(self.frame(17, "SORT"), "SEASON WEEK 18"))
            self.assertIn("ACTIONS", mc.schedule_cross_ok(self.frame(17, "BOX SCORE", ("ACTIONS",)), "SEASON WEEK 18"))
            self.assertIn("FORCE WIN", mc.schedule_cross_ok(self.frame(17, "BOX SCORE", ("FORCE WIN",)), "SEASON WEEK 18"))
        with mock.patch.object(mc, "highlighted_schedule_row", return_value=["PACKERS @ LIONS", "0", "NONE"]):
            self.assertIn("final score", mc.schedule_cross_ok(self.frame(17, "BOX SCORE"), "SEASON WEEK 18"))

    def test_super_bowl_box_score_in_the_offseason(self):
        """After the Super Bowl there's no current week: only the Super Bowl week opens, with every other check."""
        played = ["CHIEFS @ EAGLES", "KC 24 - 21 PHI"]
        sb = self.frame(17, "BOX SCORE")
        sb.text = sb.text.replace("SEASON WEEK 17", "POST SEASON SUPER BOWL")
        with mock.patch.object(mc, "highlighted_schedule_row", return_value=played):
            self.assertIsNone(mc.schedule_cross_ok(sb, None))
            self.assertIn("not before", mc.schedule_cross_ok(self.frame(17, "BOX SCORE"), None))
            sb_actions = self.frame(17, "BOX SCORE", ("ACTIONS",))
            sb_actions.text = sb_actions.text.replace("SEASON WEEK 17", "POST SEASON SUPER BOWL")
            self.assertIn("ACTIONS", mc.schedule_cross_ok(sb_actions, None))

    def test_result_pattern(self):
        self.assertTrue(mc.RESULT_RE.match("LAC 34 - 9LV"))
        self.assertFalse(mc.RESULT_RE.match("0"))


class RunModeTest(unittest.TestCase):
    """Every mode: a failure on a frozen stream restarts it, clears the partial output and
    runs the mode again; a failure on a live stream stops the run."""

    def setUp(self):
        mc.RESTARTS[0] = 0

    def test_frozen_restarts_and_reruns(self):
        calls, resets = [], []

        def mode():
            calls.append(1)
            if len(calls) == 1:
                raise mc.Abort("Category stayed on BLOCKING")
            return "ok"

        with mock.patch.object(mc, "stream_frozen", return_value=True), \
                mock.patch.object(mc, "restart_stream") as restart, mock.patch.object(mc, "log"):
            self.assertEqual(mc.run_mode("test", mode, lambda: resets.append(1)), "ok")
        self.assertEqual((len(calls), len(resets), restart.call_count), (2, 1, 1))

    def test_live_stream_failure_stops(self):
        with mock.patch.object(mc, "stream_frozen", return_value=False), \
                mock.patch.object(mc, "restart_stream") as restart, mock.patch.object(mc, "log"):
            with self.assertRaises(mc.Abort):
                mc.run_mode("test", mock.Mock(side_effect=mc.Abort("real problem")))
        restart.assert_not_called()

    def test_restart_budget_is_shared(self):
        mc.RESTARTS[0] = mc.STREAM_RESTARTS
        with mock.patch.object(mc, "stream_frozen", return_value=True), \
                mock.patch.object(mc, "restart_stream") as restart, mock.patch.object(mc, "log"):
            with self.assertRaises(mc.Abort):
                mc.run_mode("test", mock.Mock(side_effect=mc.Abort("frozen again")))
        restart.assert_not_called()


class RosterRecoveryTest(unittest.TestCase):
    """A frozen stream mid-team: restart, redo that team, nothing partial in cards.csv."""

    def test_frozen_stream_restarts_and_redoes_the_team(self):
        mc.RESTARTS[0] = 0
        calls = {"capture": 0, "restart": 0}

        def capture(out, idx, team, rows):
            calls["capture"] += 1
            os.makedirs(os.path.join(out, "roster", f"{idx:02d}_{team.lower()}"), exist_ok=True)
            rows.writerow([team, 0, "Half", "Done"] + [""] * 11)
            if calls["capture"] == 1:
                raise mc.Abort("the card stopped changing")
            rows.writerow([team, 1, "Second", "Row"] + [""] * 11)
            return 2

        def restart():
            calls["restart"] += 1
            return None, "ROSTER_LIST"

        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(mc, "capture_team_roster", side_effect=capture), \
                mock.patch.object(mc, "stream_frozen", return_value=True), \
                mock.patch.object(mc, "restart_stream", side_effect=restart), \
                mock.patch.object(mc, "open_roster", return_value=None), \
                mock.patch.object(mc, "roster_team", return_value="PANTHERS"), \
                mock.patch.object(mc, "log"):
            mc.run_roster(out, max_teams=1)
            rows = list(csv.reader(open(os.path.join(out, "roster", "cards.csv"))))[1:]
            done = open(os.path.join(out, "roster", "done.txt")).read().split()
        self.assertEqual(calls, {"capture": 2, "restart": 1})
        self.assertEqual([r[2] for r in rows], ["Half", "Second"])   # only the second attempt's rows
        self.assertEqual(done, ["1", "PANTHERS"])

    def test_not_frozen_means_stop(self):
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(mc, "capture_team_roster", side_effect=mc.Abort("real problem")), \
                mock.patch.object(mc, "stream_frozen", return_value=False), \
                mock.patch.object(mc, "restart_stream") as restart, \
                mock.patch.object(mc, "open_roster", return_value=None), \
                mock.patch.object(mc, "roster_team", return_value="PANTHERS"), \
                mock.patch.object(mc, "log"):
            with self.assertRaises(mc.Abort):
                mc.run_roster(out, max_teams=1)
    def test_stats_resume_skips_done_and_discards_partial(self):
        cats = ["PASSING", "RUSHING", "RECEIVING"]
        captured = []
        failed = [False]

        def capture(out, idx, cat, all_rows):
            folder = os.path.join(out, "stats")
            p = os.path.join(folder, f"{idx:02d}_{mc.norm(cat).lower()}_000.png")
            with open(p, "w") as f:
                f.write("test")
            if cat == "RUSHING" and not failed[0]:
                failed[0] = True
                raise mc.Abort("failure on rushing")
            captured.append(cat)

        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(mc, "open_player_stats"), \
                mock.patch.object(mc, "set_scope"), \
                mock.patch.object(mc, "look", return_value=(mock.MagicMock(), "PLAYER_STATS")), \
                mock.patch.object(mc, "chip_labels", return_value=(None, "PASSING")), \
                mock.patch.object(mc, "next_category", side_effect=lambda cat, screen: {"PASSING": "RUSHING", "RUSHING": "RECEIVING", "RECEIVING": "PASSING"}[cat]), \
                mock.patch.object(mc, "capture_category", side_effect=capture), \
                mock.patch.object(mc, "log"):
            # First attempt: PASSING succeeds, RUSHING aborts
            with self.assertRaises(mc.Abort):
                mc.run_stats(out, all_rows=False, resume=True)

            sdir = os.path.join(out, "stats")
            # 02_rushing_000.png should have been discarded on abort
            self.assertFalse(os.path.exists(os.path.join(sdir, "02_rushing_000.png")))
            # 01_passing_000.png kept
            self.assertTrue(os.path.exists(os.path.join(sdir, "01_passing_000.png")))
            with open(os.path.join(sdir, "done.txt")) as f:
                done_lines = f.read().splitlines()
            self.assertEqual(done_lines, ["1 PASSING"])

            # Second attempt: resumes, skips PASSING, captures RUSHING and RECEIVING
            mc.run_stats(out, all_rows=False, resume=True)
            self.assertEqual(captured, ["PASSING", "RUSHING", "RECEIVING"])
            with open(os.path.join(sdir, "done.txt")) as f:
                done_lines = f.read().splitlines()
            self.assertEqual(done_lines, ["1 PASSING", "2 RUSHING", "3 RECEIVING"])

    def test_active_highlight_quarter(self):
        import numpy as np
        from PIL import Image
        centers = {
            "1ST": 0.389,
            "2ND": 0.429,
            "3RD": 0.470,
            "4TH": 0.511,
            "OT": 0.551,
        }
        for H, W in [(1080, 1920), (1888, 3024)]:
            for q, norm_c in centers.items():
                arr = np.zeros((H, W), dtype=np.uint8)
                bar_w = int(W * 0.03)
                c_px = int(W * norm_c)
                y_px = int(H * 0.185)
                arr[y_px:y_px + 4, c_px - bar_w // 2 : c_px + bar_w // 2] = 255
                path = os.path.join(self.tmp if hasattr(self, "tmp") else "/tmp", f"test_q_{W}_{q}.png")
                Image.fromarray(arr).save(path)
                self.assertEqual(mc.active_highlight_quarter(path), q)
                if os.path.exists(path):
                    os.remove(path)


    def test_highlight_top_clock(self):
        class FakeFrame:
            def __init__(self, items):
                self.items = items
        # clock at (l=100, t=300) should be parsed to 18s
        f1 = FakeFrame([("0:18", (100, 300, 150, 330)), ("1:12", (100, 350, 150, 380))])
        self.assertEqual(mc.highlight_top_clock(f1), 18)
        # clock at (l=100, t=300) with 8:04 should be parsed to 484s
        f2 = FakeFrame([("8:04", (100, 300, 150, 330))])
        self.assertEqual(mc.highlight_top_clock(f2), 484)
        # no clocks
        f3 = FakeFrame([("TOUCHDOWN", (200, 300, 400, 330))])
        self.assertIsNone(mc.highlight_top_clock(f3))

    def test_highlight_visible_clocks(self):
        class FakeFrame:
            def __init__(self, items):
                self.items = items
        f = FakeFrame([("0:18", (100, 300, 150, 330)), ("1:12", (100, 350, 150, 380)), ("TOUCHDOWN", (200, 300, 400, 330))])
        self.assertEqual(mc.highlight_visible_clocks(f), {18, 72})

    def test_capture_highlights_stops_on_wrap(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path = path
                self.items = items

        f_start = MockFrame("/tmp/f0.png", [("0:18", (100, 300, 150, 330)), ("0:28", (100, 350, 150, 380))])
        # f_still_on_screen: 0:18 is still visible after a short scroll (should NOT trigger wrap)
        f_still_on_screen = MockFrame("/tmp/f1.png", [("0:18", (100, 350, 150, 380)), ("1:12", (100, 400, 150, 430))])
        # f_mid: 0:18 has completely disappeared from screen
        f_mid = MockFrame("/tmp/f2.png", [("7:30", (100, 300, 150, 330))])
        # f_wrap: 0:18 reappears after having disappeared -> triggers wrap!
        f_wrap = MockFrame("/tmp/f3.png", [("0:18", (100, 300, 150, 330))])

        # after the wrap, Up steps back to the quarter's bottom page (top clock jumps past the wrapped page's)
        f_bottom = MockFrame("/tmp/f4.png", [("9:00", (100, 300, 150, 330))])
        looks = [f_still_on_screen, f_mid, f_wrap, f_wrap, f_bottom]

        with tempfile.TemporaryDirectory() as hdir, \
                mock.patch.object(mc, "wait_for", side_effect=[(f_start, "HIGHLIGHTS"), (f_start, "HIGHLIGHTS"), (f_start, "POSTGAME_MENU")]), \
                mock.patch.object(mc, "active_highlight_quarter", return_value="1ST"), \
                mock.patch.object(mc, "look", side_effect=lambda: (looks.pop(0), "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: mock.MagicMock()), \
                mock.patch.object(mc, "same_table", return_value=False), \
                mock.patch.object(mc, "press"), \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            pages = mc.capture_highlights(hdir)
            # f_start, f_still_on_screen, f_mid, then f_wrap wrapped and the bottom page was added
            self.assertEqual(pages, 4)

    def test_capture_highlights_quarter_transition(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path = path
                self.items = items

        f4 = MockFrame("/tmp/f4.png", [("0:18", (100, 300, 150, 330))])
        f3 = MockFrame("/tmp/f3.png", [("1:20", (100, 300, 150, 330))])
        f1 = MockFrame("/tmp/f1.png", [("5:00", (100, 300, 150, 330))])
        f_postgame = MockFrame("/tmp/postgame.png", [])

        wait_fors = [
            (f4, "HIGHLIGHTS"),
            (f4, "HIGHLIGHTS"),
            (f3, "HIGHLIGHTS"),
            (f3, "HIGHLIGHTS"),
            (f1, "HIGHLIGHTS"),
            (f1, "HIGHLIGHTS"),
            (f_postgame, "POSTGAME_MENU"),
        ]

        def fake_active_quarter(p):
            if "f4" in p:
                return "4TH"
            elif "f3" in p:
                return "3RD"
            elif "f1" in p:
                return "1ST"
            return None

        with tempfile.TemporaryDirectory() as hdir, \
                mock.patch.object(mc, "wait_for", side_effect=wait_fors), \
                mock.patch.object(mc, "active_highlight_quarter", side_effect=fake_active_quarter), \
                mock.patch.object(mc, "look", side_effect=lambda: (f4, "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: mock.MagicMock()), \
                mock.patch.object(mc, "same_table", return_value=True), \
                mock.patch.object(mc, "press") as mock_press, \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            pages = mc.capture_highlights(hdir)
            self.assertEqual(pages, 3)
            left_calls = [c for c in mock_press.call_args_list if c[0][0] == "left"]
            self.assertEqual(len(left_calls), 2)

    def test_game_wide_categories(self):
        self.assertIn("DEFENSE", mc.GAME_WIDE_CATEGORIES)
        self.assertIn("DEFENSIVE", mc.GAME_WIDE_CATEGORIES)
        self.assertIn("KICKING", mc.GAME_WIDE_CATEGORIES)

    def test_traverse_game_left_edge_with_player_header(self):
        class MockFrame:
            def __init__(self, items):
                self.items = items
                self.size = (3024, 1888)
        # Table header with TEAM, PLAYER at y = 0.46
        f_left = MockFrame([("TEAM", (100, 860, 200, 890)), ("PLAYER", (300, 860, 500, 890)), ("Y FGM", (700, 860, 800, 890))])
        frames = [f_left, f_left]
        with mock.patch.object(mc, "Frame", side_effect=lambda: frames.pop(0)), \
                mock.patch.object(mc, "key_down"), \
                mock.patch.object(mc, "key_up"), \
                mock.patch.object(mc.time, "sleep"):
            with mock.patch.object(mc, "game_scroll_thumb", return_value=None):
                res = mc.traverse_game("left")
            self.assertIs(res, f_left)

    def test_traverse_game_right_edge(self):
        class MockFrame:
            def __init__(self, items):
                self.items = items
                self.size = (3024, 1888)
        # Mid frame (scrolling, TEAM still visible)
        f_mid = MockFrame([("TEAM", (100, 860, 200, 890)), ("FGM", (300, 860, 400, 890))])
        # Right frame: neither TEAM nor PLAYER visible, ending in SFTY, TD
        f_right = MockFrame([("BLOCK", (300, 860, 400, 890)), ("SFTY", (500, 860, 600, 890)), ("TD", (700, 860, 800, 890))])
        frames = [f_mid, f_right, f_right]
        with mock.patch.object(mc, "Frame", side_effect=lambda: frames.pop(0)), \
                mock.patch.object(mc, "key_down"), \
                mock.patch.object(mc, "key_up"), \
                mock.patch.object(mc.time, "sleep"):
            with mock.patch.object(mc, "game_scroll_thumb", return_value=None):
                res = mc.traverse_game("right")
            self.assertIs(res, f_right)

    def _scrollbar_frame(self, x0, x1, items=()):
        """A 3024x1888 frame with the game table's scrollbar thumb from x0 to x1 (fractions)."""
        from PIL import Image, ImageDraw
        path = os.path.join(tempfile.mkdtemp(), "f.png")
        img = Image.new("L", (3024, 1888), 12)
        d = ImageDraw.Draw(img)
        d.rectangle((122, 1598, 2895, 1602), fill=21)                       # faint track
        d.rectangle((int(x0 * 3024), 1598, int(x1 * 3024), 1602), fill=235)  # thumb
        img.save(path)

        class F:
            pass
        f = F()
        f.path, f.items, f.size = path, list(items), (3024, 1888)
        return f

    def test_game_scroll_thumb(self):
        thumb = mc.game_scroll_thumb(self._scrollbar_frame(0.2864, 0.9563))
        self.assertAlmostEqual(thumb[0], 0.2864, places=2)
        self.assertAlmostEqual(thumb[1], 0.9563, places=2)
        self.assertIsNone(mc.game_scroll_thumb(self._scrollbar_frame(0.5, 0.5)))

    def test_traverse_game_postgame_pinned_columns_use_scrollbar(self):
        # Postgame DEFENSIVE (Oct 16): TEAM and PLAYER stay pinned, so the header alone looked like
        # the left edge at once and never like the right edge. The scrollbar thumb decides.
        hdr = [("TEAM", (100, 860, 200, 890)), ("PLAYER", (300, 860, 500, 890)), ("SFTY", (2600, 860, 2700, 890))]
        mid = self._scrollbar_frame(0.2295, 0.8995, hdr)       # one column short of the right edge
        right = self._scrollbar_frame(0.2864, 0.9563, hdr)
        left = self._scrollbar_frame(0.0403, 0.7407, hdr)
        for direction, seq, want in (("right", [mid, mid, right], right), ("left", [mid, right, left], left)):
            frames = list(seq)
            with mock.patch.object(mc, "Frame", side_effect=lambda: frames.pop(0)), \
                    mock.patch.object(mc, "key_down") as kd, \
                    mock.patch.object(mc, "key_up"), \
                    mock.patch.object(mc.time, "sleep"):
                self.assertIs(mc.traverse_game(direction), want)
                self.assertEqual(kd.call_count, 2)

    def test_highlight_clocks_retina_frame(self):
        # 3024x1888 stream (Oct 16): rows run down to ~82% of the height; the old 1080p pixel box
        # (t < 950) only saw the top five
        class F:
            size = (3024, 1888)
            items = [("0:36", (157, 590, 230, 620)), ("3:11", (157, 1080, 230, 1110)), ("6:02", (157, 1580, 230, 1610)),
                     ("TIME", (120, 520, 230, 550))]
        self.assertEqual(mc.highlight_clocks(F()), [36, 191, 362])

    def test_capture_highlights_retries_dropped_scroll(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path, self.items = path, items
        f0 = MockFrame("/tmp/f0.png", [("0:18", (100, 300, 150, 330))])
        f1 = MockFrame("/tmp/f1.png", [("7:30", (100, 300, 150, 330))])   # one clock: Down has nowhere to go
        sigs = iter(["a", "a", "b", "b", "b", "b", "b"])     # 1st Down batch dropped, 2nd scrolls, then a one-play end
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(mc, "wait_for", side_effect=[(f0, "HIGHLIGHTS"), (f0, "HIGHLIGHTS"), (f0, "POSTGAME_MENU")]), \
                mock.patch.object(mc, "active_highlight_quarter", return_value="1ST"), \
                mock.patch.object(mc, "look", side_effect=lambda: (f1, "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc, "press"), \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            hdir = os.path.join(tmp, "highlights")
            self.assertEqual(mc.capture_highlights(hdir), 2)
            self.assertTrue(os.path.exists(os.path.join(hdir, "DONE")))

    def test_capture_highlights_aborts_when_quarter_wont_change(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path, self.items = path, items
        f4 = MockFrame("/tmp/f4.png", [("0:18", (100, 300, 150, 330))])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(mc, "wait_for", side_effect=lambda *a, **k: (f4, "HIGHLIGHTS")), \
                mock.patch.object(mc, "active_highlight_quarter", return_value="4TH"), \
                mock.patch.object(mc, "look", side_effect=lambda: (f4, "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: "same"), \
                mock.patch.object(mc, "same_table", return_value=True), \
                mock.patch.object(mc, "press"), \
                mock.patch.object(mc.time, "sleep"), \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            hdir = os.path.join(tmp, "highlights")
            with self.assertRaises(mc.Abort):
                mc.capture_highlights(hdir)
            self.assertFalse(os.path.exists(os.path.join(hdir, "DONE")))

    def test_capture_highlights_aborts_when_stream_stops_scrolling(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path, self.items = path, items
        f = MockFrame("/tmp/f0.png", [("0:18", (100, 300, 150, 330)), ("0:40", (100, 350, 150, 380))])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(mc, "wait_for", side_effect=lambda *a, **k: (f, "HIGHLIGHTS")), \
                mock.patch.object(mc, "active_highlight_quarter", return_value="2ND"), \
                mock.patch.object(mc, "look", side_effect=lambda: (f, "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: "same"), \
                mock.patch.object(mc, "same_table", return_value=True), \
                mock.patch.object(mc, "press") as press, \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            with self.assertRaises(mc.Abort):
                mc.capture_highlights(os.path.join(tmp, "highlights"))
            self.assertEqual(press.call_count, mc.HIGHLIGHT_SCROLL_TRIES)

    def test_capture_wide_list_confirm_end_first_page(self):
        # confirm_end once compared page 0 with itself and ended Defense/Kicking after 0 pages (Oct 16)
        # page 0: left (checked, kept), right; page 1: right (checked, kept), left; page 2: left repeats
        # page 1's left, gets one more Down batch, still repeats -> the end after 2 pages
        sigs = iter(["L0", "L0", "R0", "R1", "R1", "L1", "L1", "L1"])
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(mc, "wait_table_loaded"), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc, "press"), \
                mock.patch.object(mc.subprocess, "run", side_effect=lambda a: open(a[2], "w").close()), \
                mock.patch.object(mc, "log"):
            trav = mock.MagicMock(return_value=mock.MagicMock(path="/tmp/x.png"))
            pages = mc.capture_wide_list(folder, "05_defense", rows_per_page=6, traverse=trav, end_on_left=True,
                                         confirm_end=True)
            self.assertEqual(pages, 2)

    def test_capture_highlights_stops_when_top_clock_drops(self):
        class MockFrame:
            def __init__(self, path, items):
                self.path, self.items = path, items
        row = lambda c, y: (c, (100, y, 150, y + 30))
        f0 = MockFrame("/tmp/f0.png", [row("0:03", 300), row("0:22", 350)])
        f1 = MockFrame("/tmp/f1.png", [row("7:23", 300), row("11:00", 350)])
        f_wrap = MockFrame("/tmp/f2.png", [row("0:22", 300), row("0:27", 350)])   # 0:03 not on top after the wrap
        f_bottom = MockFrame("/tmp/f3.png", [row("10:30", 300), row("11:00", 350)])
        looks = [f1, f_wrap, f_bottom]
        sigs = iter(range(100))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(mc, "wait_for", side_effect=[(f0, "HIGHLIGHTS"), (f0, "HIGHLIGHTS"), (f0, "POSTGAME_MENU")]), \
                mock.patch.object(mc, "active_highlight_quarter", return_value="1ST"), \
                mock.patch.object(mc, "look", side_effect=lambda: (looks.pop(0), "HIGHLIGHTS")), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc, "press"), \
                mock.patch.object(mc.subprocess, "run"), \
                mock.patch.object(mc, "log"):
            self.assertEqual(mc.capture_highlights(os.path.join(tmp, "highlights")), 3)   # 2 pages + the bottom

    def test_clear_postgame_keeps_finished_parts(self):
        with tempfile.TemporaryDirectory() as out:
            g = os.path.join(out, "games", "01_super_bowl")
            os.makedirs(os.path.join(g, "stats"))
            os.makedirs(os.path.join(g, "highlights"))
            for p in ("box_top.png", "stats/01_passing_000.png", "highlights/q4th_000.png", "highlights/DONE"):
                open(os.path.join(g, p), "w").close()
            mc.clear_postgame(out)
            self.assertFalse(os.path.exists(os.path.join(g, "stats")))
            self.assertFalse(os.path.exists(os.path.join(g, "box_top.png")))
            self.assertTrue(os.path.exists(os.path.join(g, "highlights", "q4th_000.png")))

    def test_capture_list_confirm_end_retries_dropped_presses(self):
        sigs = iter(["p0", "p0", "p1", "p1", "p1"])    # page 1 repeats once (dropped Downs), then moves; then the end
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(mc, "wait_table_loaded"), \
                mock.patch.object(mc, "snap", side_effect=lambda p: open(p, "w").close()), \
                mock.patch.object(mc, "table_sig", side_effect=lambda *a: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc, "press") as press, \
                mock.patch.object(mc, "log"):
            pages = mc.capture_list(folder, "05_x", rows_per_page=6, stop_at_zeros=False, confirm_end=True)
            self.assertEqual(pages, 2)
            self.assertEqual(press.call_count, 4)


class SeasonResumeTest(unittest.TestCase):
    def test_run_season_schedule_skips_when_done_txt_exists(self):
        with tempfile.TemporaryDirectory() as out:
            sdir = os.path.join(out, "season_schedule")
            os.makedirs(sdir)
            with open(os.path.join(sdir, "done.txt"), "w") as f:
                f.write("weeks 1-18\n")
            with mock.patch.object(mc, "open_league_schedule") as mock_open:
                mc.run_season_schedule(out)
                mock_open.assert_not_called()

    def test_run_roster_skips_when_all_32_teams_in_done_txt(self):
        with tempfile.TemporaryDirectory() as out:
            rdir = os.path.join(out, "roster")
            os.makedirs(rdir)
            with open(os.path.join(rdir, "done.txt"), "w") as f:
                for i in range(32):
                    f.write(f"{i+1} TEAM{i+1}\n")
            with mock.patch.object(mc, "open_roster") as mock_open:
                res = mc.run_roster(out, max_teams=32, resume=True)
                mock_open.assert_not_called()
                self.assertEqual(res, 0)

    def test_run_team_overalls_skips_when_done_txt_exists(self):
        with tempfile.TemporaryDirectory() as out:
            tdir = os.path.join(out, "team_overalls")
            os.makedirs(tdir)
            with open(os.path.join(tdir, "done.txt"), "w") as f:
                f.write("32 teams\n")
            with mock.patch.object(mc, "open_team_overalls") as mock_open:
                mc.run_team_overalls(out)
                mock_open.assert_not_called()

    def test_run_injuries_skips_when_done_txt_exists(self):
        with tempfile.TemporaryDirectory() as out:
            idir = os.path.join(out, "injuries")
            os.makedirs(idir)
            with open(os.path.join(idir, "done.txt"), "w") as f:
                f.write("done\n")
            with mock.patch.object(mc, "open_injury_report") as mock_open:
                res = mc.run_injuries(out)
                mock_open.assert_not_called()
                self.assertEqual(res, 32)

    def test_run_prospects_skips_when_done_exists(self):
        with tempfile.TemporaryDirectory() as out:
            pdir = os.path.join(out, "prospects")
            os.makedirs(pdir)
            with open(os.path.join(out, ".prospects_done"), "w") as f:
                f.write("done\n")
            with mock.patch.object(mc, "open_prospects") as mock_open:
                res = mc.run_prospects(out)
                mock_open.assert_not_called()
                self.assertEqual(res, 0)


if __name__ == "__main__":
    unittest.main()



class LeagueTransactionsTest(unittest.TestCase):
    """League > League Transactions (team management step 2): classifier, the League-menu Cross guard (D7),
    and the weekly cutoff."""
    SCREEN = _Frame("LEAGUE TRANSACTIONS", "NFL", "R2", "ALL", "2036 SEASON - WEEK 4",
                    "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT",
                    "2036 SEASON - WEEK 4", "JAGUARS RELEASE (WR) JULIUS SLATON", "BACK")
    HUB = _Frame("GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "LEAGUE", "COACH CENTRAL", "OPTIONS", "NFL STANDINGS",
                 "SCHEDULE", "STATS & AWARDS", "LEAGUE TRANSACTIONS", "LEAGUE HISTORY", "RELOCATE OR REBUILD",
                 "2036 SEASON - WEEK 4", "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR")

    def test_screen_vs_hub(self):
        self.assertEqual(mc.classify(self.SCREEN), "LEAGUE_TRANSACTIONS")
        # no title on screen (JR, 2026-10-19): the chips and a heading are enough
        self.assertEqual(mc.classify(_Frame("NFL", "R2", "ALL", "2036 SEASON - WEEK 4", "SAINTS RE-SIGN (QB) TOM BRADY",
                                            "2036 SEASON - WEEK 3", "BACK")), "LEAGUE_TRANSACTIONS")
        self.assertEqual(mc.classify(self.HUB), "HUB_LEAGUE")

    def test_league_menu_cross_by_name(self):
        f = mock.Mock()
        for item, allowed in (("RELOCATE OR REBUILD", False), ("LEAGUE HISTORY", False), ("NFL STANDINGS", False),
                              (None, False), ("STATS & AWARDS", True), ("SCHEDULE", True), ("LEAGUE TRANSACTIONS", True)):
            f.highlighted.return_value = item
            with mock.patch.object(mc, "press") as press:
                if allowed:
                    mc.cross("HUB_LEAGUE", f)
                    press.assert_called_once_with("cross", wait=1.5)
                else:
                    with self.assertRaises(mc.Abort):
                        mc.cross("HUB_LEAGUE", f)
                    press.assert_not_called()

    def test_no_cross_on_the_list(self):
        with mock.patch.object(mc, "press") as press:
            with self.assertRaises(mc.Abort):
                mc.cross("LEAGUE_TRANSACTIONS", self.SCREEN)
        press.assert_not_called()

    def test_week_cutoff(self):
        since = "2036 SEASON - WEEK 4"
        self.assertTrue(mc.older_than(["2036 SEASON - WEEK 3", "2036 SEASON - WEEK 2"], since))
        self.assertFalse(mc.older_than(["2036 SEASON - WEEK 4", "2036 SEASON - WEEK 3"], since))   # mixed page
        self.assertFalse(mc.older_than(["2036 SEASON - WEEK 4"], since))                           # the since week is re-read
        self.assertFalse(mc.older_than([], since))
        self.assertFalse(mc.older_than(["BLUR", "2036 SEASON - WEEK 1"], since))

    def test_headings_read_top_to_bottom(self):
        f = mock.Mock(items=[("2036 SEASON - WEEKT", (0, 900, 1, 1)), ("JAGUARS RELEASE (WR) X Y", (0, 950, 1, 1)),
                             ("2036 SEASON - WEEK 2", (0, 500, 1, 1))])
        self.assertEqual(mc.transaction_headings(f), ["2036 SEASON - WEEK 2", "2036 SEASON - WEEK 1"])
