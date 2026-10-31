"""tools/college_conferences.py (the wikitext parser and matcher) and the committed scouting college list it builds.
Pure: no database, no network.

Run: python3 -m unittest tests.test_college_conferences
"""
import collections
import json
import unittest

import tools.college_conferences as cc
from engines import scouting_value as sv

FBS_TABLE = """{| class="wikitable sortable"
|-
! style=width:45em | School
! style=width:10em | Nickname
! City
! style=width:20em | Current<br />conference{{efn|group=n|According to conferences in football.}}
|-
| [[United States Air Force Academy|Air Force]]
| [[Air Force Falcons football|Falcons]]
| [[Colorado Springs, Colorado|USAF Academy]]{{efn|group=n|a note}}
| [[Mountain West Conference|Mountain West]]<ref>a ref</ref>
|-
| [[University of Notre Dame|Notre Dame]]
| [[Notre Dame Fighting Irish football|Fighting Irish]]
| [[Notre Dame, Indiana|Notre Dame]]
| [[NCAA Division I FBS independent schools|Independent]]
|}"""


class ParserTest(unittest.TestCase):
    def test_table_rows_become_programs(self):
        (_, lines), = cc.tables(cc.clean(FBS_TABLE))
        progs = cc.table_programs(lines, "FBS", ["school"], "nickname", "current")
        self.assertEqual([(p["names"], p["nickname"], p["label"]) for p in progs],
                         [(["Air Force", "United States Air Force Academy"], "Falcons", "Mountain West"),
                          (["Notre Dame", "University of Notre Dame"], "Fighting Irish", "Independent")])

    def test_madden_abbreviations_expand(self):
        self.assertIn("Western Kentucky", cc.variants("W. Kentucky"))
        self.assertIn("Southeastern Louisiana", cc.variants("SE Louisiana"))
        self.assertIn("North Carolina A&T", cc.variants("N.C. A&T"))

    def test_match_keeps_college_and_university_apart(self):
        # a "<State> College" is not the University of <State> (Mississippi College once matched Ole Miss)
        progs = [{"division": "FBS", "names": ["Kentucky", "University of Kentucky"]},
                 {"division": "D2", "names": ["Western Oregon"]}]
        found, unmatched, _ = cc.match(["Kentucky College", "W. Oregon", "N/A"], progs)
        self.assertEqual(list(found), ["W. Oregon"])
        self.assertEqual(unmatched, ["Kentucky College"])                # N/A is a placeholder: never matched


class CommittedListTest(unittest.TestCase):
    """data/scouting/colleges.json as built from the 2026 lists."""

    @classmethod
    def setUpClass(cls):
        cls.schools = sv.colleges()["schools"]
        with open(cc.MADDEN) as fh:
            doc = json.load(fh)
        cls.madden = sorted({c["name"] for c in doc["colleges"]} | set(doc["overrides"].values()))

    def test_every_madden_school_with_football_has_a_conference(self):
        no_program = cc.NO_PROGRAM | {n for n, v in cc.MANUAL.items() if v is None}
        missing = [n for n in self.madden if n not in no_program and sv.school(n) is None]
        self.assertEqual(missing, [])
        for n in self.madden:
            if n not in no_program:
                s = sv.school(n)
                self.assertTrue(s["conference"] and s["nickname"] and s["level"], n)

    def test_power_four_sizes_and_levels(self):
        by_conf = collections.Counter(s["conference"] for s in self.schools.values() if s["level"] == "P4")
        self.assertEqual({c: by_conf[c] for c in ("SEC", "Big Ten", "Big 12", "ACC")},
                         {"SEC": 16, "Big Ten": 18, "Big 12": 16, "ACC": 17})
        self.assertEqual(self.schools["NOTRE DAME"]["level"], "P4")
        self.assertEqual(self.schools["MISSISSIPPI STATE"]["conference"], "SEC")
        self.assertNotIn("MISSISSIPPI COLLEGE", self.schools)          # football dropped (2013)

    def test_one_level_per_conference(self):
        levels = collections.defaultdict(set)
        for s in self.schools.values():
            levels[s["conference"]].add(s["level"])
        self.assertEqual({c: l for c, l in levels.items() if len(l) > 1}, {"Independent": {"FBS", "P4"}})

    def test_the_twelve_from_the_save(self):
        for name, conf in (("BYU", "Big 12"), ("AIR FORCE", "Mountain West"), ("HAWAII", "Mountain West"),
                           ("LIBERTY", "Conference USA"), ("PENN", "Ivy League"), ("PRINCETON", "Ivy League"),
                           ("SAN JOSE STATE", "Mountain West"), ("DAYTON", "Pioneer League"), ("FERRIS STATE", "GLIAC"),
                           ("SOUTHERN ARKANSAS", "GAC"), ("VILLANOVA", "Patriot League"), ("DELAWARE STATE", "MEAC")):
            self.assertEqual(sv.school(name)["conference"], conf, name)

    def test_independents_have_no_conference_mates(self):
        self.assertEqual(sv.conference_members("Independent"), [])
        self.assertEqual(sv.conference_members("FCS Independent"), [])
        self.assertIn("ALABAMA", sv.conference_members("SEC"))


if __name__ == "__main__":
    unittest.main()
