"""pipeline/transactions_parse.py: League Transactions rows, headings, keys (no database, no Vision)."""
import unittest

from pipeline import transactions_parse as T


class HeadingTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(T.clean_heading("2036 SEASON - WEEK 4"), "2036 SEASON - WEEK 4")
        self.assertEqual(T.clean_heading("2036 SEASON - WEEKT"), "2036 SEASON - WEEK 1")
        self.assertEqual(T.clean_heading("2036 SEASON-WEEK 1T"), "2036 SEASON - WEEK 11")
        self.assertEqual(T.clean_heading("2O36  season – week IO"), "2036 SEASON - WEEK 10")
        self.assertEqual(T.clean_heading("2036 POSTSEASON - WILDCARD"), "2036 POST SEASON - WILD CARD")
        self.assertEqual(T.clean_heading("2036 PRE SEASON - WEEK 2"), "2036 PRESEASON - WEEK 2")

    def test_week(self):
        self.assertEqual(T.heading_week("2036 SEASON - WEEK 4"), 4)
        self.assertEqual(T.heading_week("2036 SEASON - WEEKT"), 1)
        self.assertEqual(T.heading_week("2036 POST SEASON - WILD CARD"), 19)
        self.assertEqual(T.heading_week("2036 POST SEASON - SUPER BOWL"), 23)
        self.assertIsNone(T.heading_week("2036 PRESEASON - WEEK 2"))
        self.assertIsNone(T.heading_week("garbage"))

    def test_ordinal_order(self):
        order = ["2036 PRESEASON - WEEK 1", "2036 PRESEASON - WEEK 3", "2036 SEASON - WEEK 1", "2036 SEASON - WEEK 2",
                 "2036 SEASON - WEEK 11", "2036 SEASON - WEEK 18", "2036 POST SEASON - WILD CARD",
                 "2036 POST SEASON - SUPER BOWL", "2036 OFFSEASON - FREE AGENCY", "2037 PRESEASON - WEEK 1"]
        ords = [T.heading_ordinal(h) for h in order]
        self.assertEqual(ords, sorted(ords))
        self.assertEqual(len(set(ords)), len(ords))
        self.assertIsNone(T.heading_ordinal("NO HEADING HERE"))


class RowTest(unittest.TestCase):
    def test_signing(self):
        d = T.parse_row("JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT")
        self.assertTrue(d["ok"])
        self.assertEqual((d["type"], d["team"], d["to_team"], d["pos"], d["name"], d["years"], d["amount_text"]),
                         ("SIGNED", "JAGUARS", "JAGUARS", "WR", "DEVONTA TAYLOR", 1, "3.93M"))
        self.assertEqual(d["flags"], [])

    def test_release(self):
        d = T.parse_row("JAGUARS RELEASE (WR) JULIUS SLATON")
        self.assertTrue(d["ok"])
        self.assertEqual((d["type"], d["team"], d["pos"], d["name"]), ("RELEASED", "JAGUARS", "WR", "JULIUS SLATON"))
        self.assertEqual(T.legs(d)[0]["from"], "JAGUARS")
        self.assertIsNone(T.legs(d)[0]["to"])

    def test_missing_comma_years_and_ocr(self):
        d = T.parse_row("bears signed free agent ( CB ) JO  SMITH JR. TO T YEARS $850K CONTRACT")
        self.assertTrue(d["ok"])
        self.assertEqual((d["name"], d["years"], d["amount_text"]), ("JO SMITH JR", 1, "850K"))
        d = T.parse_row("BEARS SIGNED FREE AGENT (CB) JO SMITH TO 2 YEARS, S1.2 M CONTRACT")
        self.assertEqual((d["years"], d["amount_text"]), (2, "1.2M"))

    def test_signing_without_terms_is_flagged(self):
        d = T.parse_row("BEARS SIGNED FREE AGENT (CB) JO SMITH")
        self.assertTrue(d["ok"])
        self.assertIn("no_terms", d["flags"])
        self.assertTrue(T.needs_reread(d))

    def test_unknown_team(self):
        d = T.parse_row("JAGUAR5 RELEASE (WR) JULIUS SLATON")
        self.assertFalse(d["ok"])
        self.assertIn("unknown_team", d["flags"])

    def test_resign_and_extension(self):
        d = T.parse_row("SAINTS RE-SIGNED (QB) TOM BRADY TO 4 YEARS, $120M CONTRACT")
        self.assertEqual((d["type"], d["team"], d["name"], d["years"], d["amount_text"]),
                         ("RE_SIGNED", "SAINTS", "TOM BRADY", 4, "120M"))
        d = T.parse_row("SAINTS SIGNED (QB) TOM BRADY TO A 4 YEAR, $120M EXTENSION")
        self.assertEqual((d["type"], d["team"], d["pos"], d["name"], d["years"]), ("EXTENDED", "SAINTS", "QB", "TOM BRADY", 4))

    def test_draft_class_notice_is_other(self):
        d = T.parse_row("THE DRAFT CLASS HAS BEEN EDITED BY THE COMMISSIONER")
        self.assertEqual(d["type"], "OTHER")
        self.assertFalse(d["ok"])
        self.assertIn("unparsed", d["flags"])
        self.assertTrue(T.row_key(d).startswith("raw:"))

    def test_trade_shapes(self):
        d = T.parse_row("BEARS TRADED (WR) JOHN DOE TO PACKERS")
        self.assertTrue(d["ok"], d)
        self.assertEqual([(l["name"], l["from"], l["to"]) for l in d["legs"]], [("JOHN DOE", "BEARS", "PACKERS")])
        d = T.parse_row("BEARS TRADED (WR) JOHN DOE TO PACKERS FOR (CB) AL ROE AND (LT) BO POE")
        self.assertTrue(d["ok"], d)
        self.assertEqual([(l["name"], l["from"], l["to"]) for l in d["legs"]],
                         [("JOHN DOE", "BEARS", "PACKERS"), ("AL ROE", "PACKERS", "BEARS"), ("BO POE", "PACKERS", "BEARS")])
        d = T.parse_row("BEARS TRADED (WR) JOHN DOE TO PACKERS FOR 2037 2ND ROUND PICK")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["assets"], [{"asset": "2037 2ND ROUND PICK", "from": "PACKERS", "to": "BEARS"}])
        self.assertEqual([l["suffix"] for l in T.legs(d)], ["#P1", "#A1"])
        d = T.parse_row("PACKERS ACQUIRED (WR) JOHN DOE FROM BEARS")
        self.assertEqual([(l["name"], l["from"], l["to"]) for l in d["legs"]], [("JOHN DOE", "BEARS", "PACKERS")])
        for weird in ("TRADE COMPLETED", "BEARS TRADE", "BEARS TRADED (WR) TO"):
            d = T.parse_row(weird)                 # never raises
            self.assertEqual(d["type"], "TRADED")
            self.assertFalse(d["ok"])


class KeyTest(unittest.TestCase):
    def test_ocr_variants_share_a_key(self):
        a = T.parse_row("JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT")
        b = T.parse_row("JAGUARS SIGNED FREE AGENT ( WR) DEVONTA TAY-LOR TO T YEAR $3.93M CONTRACT")
        self.assertEqual(T.row_key(a), T.row_key(b))
        c = T.parse_row("JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.94M CONTRACT")
        self.assertNotEqual(T.row_key(a), T.row_key(c))

    def test_seq_stable_when_rows_prepended(self):
        def rows(texts):
            return [{"heading": h, "row_key": T.row_key(T.parse_row(t), t)} for h, t in texts]
        w4, w3 = "2036 SEASON - WEEK 4", "2036 SEASON - WEEK 3"
        rel = "BEARS RELEASE (WR) JO SMITH"
        before = rows([(w4, rel), (w4, "BEARS RELEASE (CB) AL ROE"), (w4, rel), (w3, rel)])
        after = rows([(w4, rel), (w4, "LIONS RELEASE (K) X Y")]) + before
        s_before, s_after = T.assign_seq(before), T.assign_seq(after)
        self.assertEqual(s_before, [2, 1, 1, 1])
        self.assertEqual(s_after[2:], s_before)
        self.assertEqual(s_after[0], 3)


class HeadingOrderTest(unittest.TestCase):
    def test_fix_and_flag(self):
        rows = [{"heading": h, "flags": []} for h in
                ["2036 SEASON - WEEK 4", "2036 SEASON - WEEK 7", "2036 SEASON - WEEK 4", "2036 SEASON - WEEK 3",
                 "BLURRED", "2036 SEASON - WEEK 3"]]
        T.fix_heading_order(rows)
        self.assertEqual(rows[1]["heading"], "2036 SEASON - WEEK 4")
        self.assertIn("heading_fixed", rows[1]["flags"])
        self.assertEqual(rows[4]["heading"], "2036 SEASON - WEEK 3")
        self.assertEqual(rows[0]["flags"], [])

    def test_unfixable_is_flagged(self):
        rows = [{"heading": h, "flags": []} for h in ["2036 SEASON - WEEK 4", "2036 SEASON - WEEK 9", "2036 SEASON - WEEK 2"]]
        T.fix_heading_order(rows)
        self.assertIn("heading_order", rows[1]["flags"])


if __name__ == "__main__":
    unittest.main()


class ConsoleWordingTest(unittest.TestCase):
    """JR's console answers (2026-10-19)."""
    def test_resign_extension_wording(self):
        d = T.parse_row("SAINTS RE-SIGN (QB) TOM BRADY TO A 4-YEAR, $120M EXTENSION")
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["type"], d["team"], d["pos"], d["name"], d["years"], d["amount_text"]),
                         ("RE_SIGNED", "SAINTS", "QB", "TOM BRADY", 4, "120M"))

    def test_non_moves_are_skipped(self):
        self.assertFalse(T.is_move("THE DRAFT CLASS HAS BEEN EDITED BY THE COMMISSIONER"))
        self.assertTrue(T.is_move("JAGUARS RELEASE (WR) JULIUS SLATON"))
        self.assertTrue(T.is_move("JAGUARS SlGNED FREE AGENT (WR) DEVONTA TAYLOR"))   # OCR-mangled verb, player group
        self.assertFalse(T.needs_reread(T.parse_row("THE DRAFT CLASS HAS BEEN EDITED")))

    def test_offseason_heading_is_the_finished_season(self):
        self.assertEqual(T.heading_year("2036 OFFSEASON - RE-SIGN PLAYERS"), 2036)
        self.assertGreater(T.heading_ordinal("2036 OFFSEASON - RE-SIGN PLAYERS"), T.heading_ordinal("2036 POST SEASON - SUPER BOWL"))
