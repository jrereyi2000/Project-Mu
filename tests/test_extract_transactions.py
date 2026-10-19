"""pipeline/extract_transactions_ocr.py: row assembly, page merging and the Gemini re-read (no Vision, no network)."""
import csv, os, tempfile, unittest

from pipeline import extract_transactions_ocr as X

SIZE = (3024, 1888)
W4 = "2036 SEASON - WEEK 4"
SIGN = "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT"
REL = "JAGUARS RELEASE (WR) JULIUS SLATON"


def items(*rows, top=400, step=200):
    """Synthetic Vision items: each row is (heading, body line, [continuation line])."""
    out, y = [("LEAGUE TRANSACTIONS", (100, 150, 600, 190)), ("NFL", (100, 300, 160, 330))], top
    for row in rows:
        out.append((row[0], (400, y, 900, y + 40)))
        for j, line in enumerate(row[1:]):
            out.append((line, (400, y + 50 + 45 * j, 1900, y + 90 + 45 * j)))
        y += step
    out.append(("BACK", (2800, 1800, 2900, 1840)))
    return out


def page(*texts, n=0):
    return [{"heading": W4, "text": t, "page": n, "box": (0, 0, 1, 1)} for t in texts]


class AssembleTest(unittest.TestCase):
    def test_rows_and_wrapped_body(self):
        rows = X.assemble_rows(items((W4, SIGN), (W4, "BEARS TRADED (WR) JOHN DOE TO PACKERS FOR", "(CB) AL ROE"),
                                     ("2036 SEASON - WEEKT", REL)), SIZE, page=2)
        self.assertEqual([r["heading"] for r in rows], [W4, W4, "2036 SEASON - WEEK 1"])
        self.assertEqual(rows[0]["text"], SIGN)
        self.assertEqual(rows[1]["text"], "BEARS TRADED (WR) JOHN DOE TO PACKERS FOR (CB) AL ROE")
        self.assertEqual(rows[2]["page"], 2)

    def test_cut_off_heading_left_out(self):
        its = items((W4, SIGN))
        its.append(("2036 SEASON - WEEK 3", (400, 1700, 900, 1740)))      # body below the visible list
        self.assertEqual(len(X.assemble_rows(its, SIZE)), 1)


class MergeTest(unittest.TestCase):
    def test_one_row_overlap(self):
        rows = X.merge_pages([page("A1 RELEASE (K) A B", REL, SIGN), page(SIGN, "BEARS RELEASE (P) C D", n=1)])
        self.assertEqual([r["text"] for r in rows], ["A1 RELEASE (K) A B", REL, SIGN, "BEARS RELEASE (P) C D"])
        self.assertFalse(any(r["flags"] for r in rows))

    def test_no_overlap_flags_a_gap(self):
        rows = X.merge_pages([page(REL), page(SIGN, n=1)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["flags"], ["gap?"])

    def test_identical_rows_survive(self):
        # page 0: REL, REL ; page 1: REL, SIGN. The expected 1-row overlap is used, so the merged list keeps
        # two RELs (a 2-row overlap would also need page 1 to start REL, REL, which it doesn't).
        rows = X.merge_pages([page(REL, REL), page(REL, SIGN, n=1)])
        self.assertEqual([r["text"] for r in rows], [REL, REL, SIGN])
        rows = X.merge_pages([page(SIGN, REL, REL), page(REL, REL, n=1)])
        self.assertEqual([r["text"] for r in rows], [SIGN, REL, REL, REL])

    def test_ocr_variant_still_overlaps(self):
        rows = X.merge_pages([page(REL, SIGN), page(SIGN.replace("1 YEAR", "T YEAR"), "BEARS RELEASE (P) C D", n=1)])
        self.assertEqual(len(rows), 3)


class ExtractTest(unittest.TestCase):
    def test_csv_with_stubbed_reread(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "transactions"))
            from PIL import Image
            for n in range(2):
                Image.new("RGB", SIZE).save(os.path.join(root, "transactions", f"page_{n:03d}.png"))
            pages = {0: items((W4, SIGN), (W4, "JAGUARS SIGNED FREE AGENT (WR) BO POE TO 1 YEAR $")),
                     1: items((W4, "JAGUARS SIGNED FREE AGENT (WR) BO POE TO 1 YEAR $"), (W4, "SOMETHING ODD"))}
            calls = []

            def reader(row):
                calls.append(row["text"])
                if "BO POE" in row["text"]:
                    return W4, "JAGUARS SIGNED FREE AGENT (WR) BO POE TO 1 YEAR, $1.10M CONTRACT"
                return W4, "SOMETHING ODD"

            n = X.extract_transactions_ocr(root, reader=reader,
                                           ocr=lambda p: pages[int(p[-7:-4])])
            self.assertEqual(n, 3)
            with open(os.path.join(root, "transactions.csv")) as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([r["ORDER"] for r in rows], ["0", "1", "2"])
            self.assertEqual(rows[1]["AMOUNT"], "1.10M")
            self.assertIn("from_gemini", rows[1]["FLAGS"])
            self.assertEqual((rows[2]["TYPE"], rows[2]["PARSE_OK"]), ("OTHER", "0"))
            self.assertEqual(rows[0]["WEEK"], "4")
            self.assertEqual(rows[0]["SEASON"], "2036")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
