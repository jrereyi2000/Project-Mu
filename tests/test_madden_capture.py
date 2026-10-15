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

    def test_next_round(self):
        """The coming playoff round recorded before stepping back: the Super Bowl from Pro Bowl week."""
        self.assertEqual(mc.next_round("POST SEASON WILD CARD", "SEASON WEEK 18"), "POST SEASON WILD CARD")
        self.assertEqual(mc.next_round("POST SEASON PRO BOWL", "POST SEASON CONFERENCE"), "POST SEASON SUPER BOWL")
        self.assertIsNone(mc.next_round(None, "POST SEASON SUPER BOWL"))            # offseason
        self.assertIsNone(mc.next_round("SEASON WEEK 12", "SEASON WEEK 11"))
        self.assertIsNone(mc.next_round("POST SEASON DIVISIONAL", "POST SEASON DIVISIONAL"))


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

    def test_manage_roster_hub(self):
        f = _Frame("GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "LEAGUE", "COACH CENTRAL", "UPGRADE PLAYERS",
                   "ADJUST LINEUP", "ROSTER", "DEPTH CHART")
        self.assertEqual(mc.classify(f), "HUB_ROSTER")


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
            restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
