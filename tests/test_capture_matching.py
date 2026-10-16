"""pipeline/capture_matching.py: name keys, number parsing, roster matching, Season Stats history."""
import unittest

from pipeline.capture_matching import (all_caps, assign_by_history, db_player_name, db_week, dec, full_key, match_roster, name_key,
                                       name_key_from_full, num)


class NameTest(unittest.TestCase):
    def test_list_name_spellings_agree(self):
        for a, b in (("G.Crawford", "G. Crawford"), ("G.Crawford", "George Crawford"), ("T.Parker", "T.J. Parker"),
                     ("P.Van Buren", "P. Van Buren"), ("P.Van Buren", "Peter Van Buren"), ("A.Hill Jr.", "A. Hilljr."),
                     ("W.Nolen III", "Walter Nolen Iii"), ("D.Gore", "D.J. Gore")):
            self.assertEqual(name_key(a), name_key(b), (a, b))
        self.assertNotEqual(name_key("R.Keaton"), name_key("P.Keaton"))

    def test_full_name_belongs_to_list_name(self):
        self.assertTrue(name_key_from_full(full_key("George Crawford"), name_key("G. Crawford")))
        self.assertFalse(name_key_from_full(full_key("Armand Membou"), name_key("D. Moyer")))   # a corrupted row

    def test_name_case(self):
        from pipeline.capture_matching import name_case
        self.assertEqual(name_case("Nolen Iii"), "Nolen III")
        self.assertEqual(name_case("Cj"), "C.J.")
        self.assertEqual(name_case("Mccain"), "McCain")
        self.assertEqual(name_case("Thomas Jr"), "Thomas Jr.")
        self.assertEqual(name_case("Ivy"), "Ivy")

    def test_all_caps(self):
        for name in ("JERRY RAYFORD", "OMAR COOPER JR.", "C.J. STROUD"):
            self.assertTrue(all_caps(name), name)
        for name in ("Jerry Rayford", "J. Rayford", "C.J. Lee", "T. Ho", "", None):
            self.assertFalse(all_caps(name), name)

    def test_db_name(self):
        self.assertEqual(db_player_name("George", "Crawford"), "G. Crawford")


class NumberTest(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(num("5,121"), 5121)
        self.assertEqual(num("60%"), 60)
        self.assertEqual(num("7.0"), 7)
        self.assertIsNone(num(""))
        self.assertEqual(dec("97.7"), 97.7)
        with self.assertRaises(ValueError):
            num("5.5")
        with self.assertRaises(ValueError):
            num("abc")


def card(name, full, team, pos, number=None):
    return {"name_key": name_key(name), "full_key": full_key(full), "team_id": team, "position": pos, "number": number}


def row(pid, name, full, team, pos, number=0):
    return dict(card(name, full, team, pos, number), id=pid)


class RosterMatchTest(unittest.TestCase):
    def test_list_name_first_then_new(self):
        cards = [card("G.Crawford", "George Crawford", 11, "QB"), card("C.Newman", "Chris Newman", 11, "LT")]
        m, new, amb = match_roster(cards, [row(1, "G. Crawford", "George Crawford", 11, "QB")])
        self.assertEqual(m, {0: (1, "list name + team + position")})
        self.assertEqual((new, amb), ([1], []))

    def test_same_full_name_on_two_teams_with_one_row(self):
        # two Manny Santos (Chargers, Ravens); the database had one row (Ravens)
        cards = [card("M.Santos", "Manny Santos", 36, "WR"), card("M.Santos", "Manny Santos", 16, "TE")]
        m, new, amb = match_roster(cards, [row(106, "M. Santos", "Manny Santos", 16, "TE")])
        self.assertEqual(m, {1: (106, "list name + team + position")})
        self.assertEqual(new, [0])

    def test_corrupted_full_name_does_not_steal_a_match(self):
        # row 101 is D. Moyer's (his stats join on that name) but carries Armand Membou's full name
        cards = [card("D.Moyer", "Dom Moyer", 19, "LS"), card("A.Membou", "Armand Membou", 19, "RT")]
        m, new, _ = match_roster(cards, [row(101, "D. Moyer", "Armand Membou", 19, "LS")])
        self.assertEqual(m[0][0], 101)
        self.assertEqual(new, [1])

    def test_traded_player_matches_by_full_name(self):
        m, new, _ = match_roster([card("J.Fisher", "Jake Fisher", 7, "WR")], [row(5, "J. Fisher", "Jake Fisher", 9, "WR")])
        self.assertEqual(m, {0: (5, "full name + position")})


class HistoryTest(unittest.TestCase):
    def test_two_kickers_with_one_name(self):
        prev = {1: {"fgm": 20, "xpm": 30}, 2: {"fgm": 10, "xpm": 25}}
        self.assertEqual(assign_by_history([{"fgm": 11, "xpm": 27}, {"fgm": 22, "xpm": 31}], prev, ["fgm", "xpm"]),
                         {0: 2, 1: 1})

    def test_no_history_or_a_tie_is_refused(self):
        self.assertIsNone(assign_by_history([{"fgm": 1}], {1: {}, 2: {"fgm": 0}}, ["fgm"]))
        self.assertIsNone(assign_by_history([{"fgm": 5}, {"fgm": 5}], {1: {"fgm": 1}, 2: {"fgm": 1}}, ["fgm"]))



class WeekTest(unittest.TestCase):
    def test_regular_season_labels(self):
        self.assertEqual(db_week("SEASON WEEK 18"), 18)
        self.assertEqual(db_week("Week 17"), 17)            # schedule.csv
        self.assertEqual(db_week("season  week 1"), 1)

    def test_postseason_labels_follow_the_games_table(self):
        self.assertEqual(db_week("POST SEASON WILD CARD"), 19)
        self.assertEqual(db_week("Wild Card"), 19)
        self.assertEqual(db_week("POST SEASON DIVISIONAL"), 20)
        self.assertEqual(db_week("Div. Playoff"), 20)
        self.assertEqual(db_week("Divisional"), 20)
        self.assertEqual(db_week("POST SEASON CONFERENCE"), 21)
        self.assertEqual(db_week("Conf. Playoff"), 21)
        self.assertEqual(db_week("Conf. Championship"), 21)
        self.assertEqual(db_week("POST SEASON SUPER BOWL"), 23)
        self.assertEqual(db_week("Super Bowl"), 23)

    def test_preseason_and_junk_give_none(self):
        for label in ("PRESEASON WEEK 2", "", None, "WEEK", "SORT"):
            self.assertIsNone(db_week(label))


if __name__ == "__main__":
    unittest.main()
