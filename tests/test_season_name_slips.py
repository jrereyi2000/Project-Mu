"""Season Stats names read one letter off (load_capture.slip_candidate, 2026-10-20): Gemini reads M.Feralta as
M.Peralta and O.Jones as D.Jones every week. Fake cursor: no database."""
import unittest
from unittest.mock import MagicMock, patch

from pipeline import load_capture as L

CMAP = {"REC": ("receptions", L.num), "YARDS": ("rec_yards", L.num)}
O_JONES = (8078, "OJONES", 5, "WR", "O. Jones")


def run(rows, players, prev=(4, 72)):
    cur = MagicMock()
    cur.fetchone.return_value = prev
    problems, unmatched = [], []
    L.NAME_SLIPS.clear()
    with patch("sys.stdin.isatty", return_value=False):
        out = L.resolve_season_rows(cur=cur, season=2036, cat="receiving", rows=rows, cmap=CMAP,
                                    table="season_stats", players=players, problems=problems, unmatched=unmatched)
    return out, problems


def row(name, pos="WR", rec="7", yds="134"):
    return {"NAME": name, "POS": pos, "REC": rec, "YARDS": yds, "GP": "2", "DP": "61"}


class NameSlipTests(unittest.TestCase):
    def test_one_letter_off_with_fitting_totals_matches(self):
        out, problems = run([row("D.Jones")], [O_JONES])
        self.assertEqual(list(out), [8078])
        self.assertEqual(out[8078][2], 5)
        self.assertEqual(problems, [])
        self.assertEqual(len(L.NAME_SLIPS), 1)
        self.assertIn("D.Jones WR read as player 8078", L.NAME_SLIPS[0])

    def test_no_stored_row_still_matches(self):
        out, _ = run([row("D.Jones")], [O_JONES], prev=None)
        self.assertEqual(list(out), [8078])

    def test_a_total_going_down_is_not_him(self):
        with self.assertRaises(L.LoadError):
            run([row("D.Jones", rec="3")], [O_JONES])

    def test_other_position_is_not_him(self):
        with self.assertRaises(L.LoadError):
            run([row("D.Jones", pos="TE")], [O_JONES])

    def test_two_near_names_stop_the_load(self):
        with self.assertRaises(L.LoadError):
            run([row("D.Jones")], [O_JONES, (9001, "DJONAS", 7, "WR", "D. Jonas")])

    def test_his_own_name_on_the_list_means_someone_else(self):
        with self.assertRaises(L.LoadError):
            run([row("D.Jones"), row("O.Jones", rec="8", yds="140")], [O_JONES])

    def test_exact_name_never_uses_the_slip(self):
        out, _ = run([row("O.Jones")], [O_JONES])
        self.assertEqual(list(out), [8078])
        self.assertEqual(L.NAME_SLIPS, [])


if __name__ == "__main__":
    unittest.main()


GIANTS = [("DWILBON", (749, "TE", "D. Wilbon")), ("DHOBBS", (1431, "WR", "D. Hobbs"))]


def game_row(name, rec="2", yds="22"):
    return {"GAME": "02_giants_at_cardinals", "TEAM": "NYG", "NAME": name, "REC": rec, "YARDS": yds}


class GameNameSlipTests(unittest.TestCase):
    """Game stats names one letter off (load_capture.game_slip, 2026-10-27): the Wild Card load stopped on
    Texans M.Peralta, Giants D.Wilson and Chargers D.Millroe."""

    def test_one_letter_off_matches_the_teammate(self):
        r = game_row("D.Wilson")
        self.assertEqual(L.game_slip(r, "DWILSON", GIANTS, {"DWILSON": r}), ("match", GIANTS[0][1]))

    def test_same_line_read_twice_is_a_twin(self):
        r, twin = game_row("D.Wilson"), game_row("D.Wilbon")
        self.assertEqual(L.game_slip(r, "DWILSON", GIANTS, {"DWILSON": r, "DWILBON": twin}), ("twin", "D.Wilbon"))

    def test_his_own_line_with_other_numbers_is_someone_else(self):
        r, own = game_row("D.Wilson"), game_row("D.Wilbon", rec="4", yds="51")
        self.assertIsNone(L.game_slip(r, "DWILSON", GIANTS, {"DWILSON": r, "DWILBON": own}))

    def test_two_near_teammates_match_nobody(self):
        team = GIANTS + [("DWILLON", (9001, "WR", "D. Willon"))]
        self.assertIsNone(L.game_slip(game_row("D.Wilson"), "DWILSON", team, {}))

    def test_category_positions_narrow_the_teammates(self):
        self.assertIsNone(L.game_slip(game_row("D.Wilson"), "DWILSON", GIANTS, {}, {"QB", "HB"}))

    def test_two_letters_off_is_nobody(self):
        self.assertIsNone(L.game_slip(game_row("D.Watson"), "DWATSON", GIANTS, {}))
