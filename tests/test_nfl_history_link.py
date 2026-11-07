"""engines/nfl_history/link (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.6, §4)."""
import csv
import os

import pytest

from engines.nfl_history import link as L

ROOT = os.path.join(os.path.dirname(__file__), "..")


def mu(pid, first, last, pos="QB", team="KC", exp=3, **kw):
    return L.MuPlayer(player_id=pid, pgid=1000 + pid, first_name=first, last_name=last, position=pos, team=team,
                      years_of_experience=exp, overall=kw.pop("overall", 70), **kw)


def person(gsis, name, pos="QB", rookie=2023, team="KC", **kw):
    first, _, last = name.partition(" ")
    return L.RealPerson(gsis_id=gsis, display_name=name, first_name=first, last_name=last, position=pos,
                        rookie_season=rookie, last_season=kw.pop("last", 2025), team_2026=team, **kw)


def test_normalize_name():
    assert L.normalize_name("José Núñez-Smith Jr.") == "jose nunez smith"
    assert L.normalize_name("D'Andre O'Neal III") == "dandre oneal"
    assert L.normalize_name("T.J. Watt") == L.normalize_name("TJ Watt") == "tj watt"
    assert L.normalize_name("  Al   Bo  ") == "al bo"


def test_exact_unique_match():
    r = L.match([mu(1, "Patrick", "Mahomes")], [person("g1", "Patrick Mahomes"), person("g2", "Josh Allen")])
    assert [(l["player_id"], l["gsis_id"], l["method"]) for l in r.links] == [(1, "g1", "EXACT")]
    assert r.links[0]["evidence"]["entry"] is True and r.links[0]["score"] > 0


def test_alias_match():
    m = mu(1, "Kenny", "Gainwell", pos="HB", aliases=("Kenneth Gainwell",))
    r = L.match([m], [person("g1", "Kenneth Gainwell", pos="RB")])
    assert r.links[0]["method"] == "ALIAS"


def test_suffix_and_accents():
    r = L.match([mu(1, "Michael", "Penix Jr", exp=2, team="ATL")],
                [person("g1", "Michael Penix", rookie=2024, team="ATL")])
    assert r.links[0]["gsis_id"] == "g1"
    r = L.match([mu(1, "Jose", "Nunez", pos="WR")], [person("g9", "José Núñez", pos="WR")])
    assert r.links[0]["gsis_id"] == "g9"


def test_same_name_separated_by_entry_season():
    people = [person("old", "Josh Allen", pos="QB", rookie=2010, team=None),
              person("new", "Josh Allen", pos="QB", rookie=2018, team="BUF")]
    r = L.match([mu(1, "Josh", "Allen", team="BUF", exp=8)], people)
    assert r.links[0]["gsis_id"] == "new"


def test_tie_is_ambiguous():
    people = [person("a", "Chris Jones", pos="DT", rookie=2016, team=None),
              person("b", "Chris Jones", pos="DT", rookie=2016, team=None)]
    r = L.match([mu(1, "Chris", "Jones", pos="DT", team="KC", exp=10)], people)
    assert r.links == [] and len(r.ambiguous) == 1
    assert {c["gsis_id"] for c in r.ambiguous[0]["candidates"]} == {"a", "b"}


def test_fuzzy_one_letter_slip_linked_and_listed():
    r = L.match([mu(1, "Olu", "Fashanu", pos="LT", team="NYJ", exp=2)],
                [person("g1", "Olumuyiwa Fashanu", pos="T", rookie=2024, team="NYJ")])
    assert r.links[0]["method"] == "FUZZY" and r.fuzzy == r.links
    r = L.match([mu(1, "Dru", "Phillips", pos="CB", team="NYG", exp=2)],
                [person("g2", "Andru Phillips", pos="CB", rookie=2024, team="NYG")])
    assert r.links[0]["method"] == "FUZZY"
    r = L.match([mu(1, "Matt", "Orzeck", pos="LS", team="GB", exp=7)],
                [person("g3", "Matthew Orzech", pos="LS", rookie=2019, team="GB")])
    assert r.links[0]["method"] == "FUZZY"


def test_fuzzy_needs_team_and_entry():
    r = L.match([mu(1, "Olu", "Fashanu", pos="LT", team="NYJ", exp=2)],
                [person("g1", "Olumuyiwa Fashanu", pos="T", rookie=2024, team="KC")])
    assert r.links == [] and len(r.unmatched) == 1


def test_person_claimed_twice_both_ambiguous():
    p = person("g1", "Sam Smith", pos="WR", team="KC")
    r = L.match([mu(1, "Sam", "Smith", pos="WR"), mu(2, "Sam", "Smith", pos="WR")], [p])
    assert r.links == [] and [a["player"].player_id for a in r.ambiguous] == [1, 2]


def test_edge_rushers_match_nflverse_lb():
    r = L.match([mu(1, "Myles", "Garrett", pos="RE", team="CLE", exp=9)],
                [person("g1", "Myles Garrett", pos="LB", rookie=2017, team="CLE")])
    assert r.links[0]["gsis_id"] == "g1"


def test_override_applied_none_and_refused():
    players = [mu(1, "Jim", "Real", pos="QB"), mu(2, "Not", "Real", pos="WR"), mu(3, "Pat", "Moved", pos="QB")]
    people = [person("g7", "James Different", pos="QB"), person("g3", "Pat Moved")]
    ov = [{"madden_pgid": "1001", "full_name": "Jim Real", "position": "QB", "gsis_id": "g7", "note": "JR"},
          {"madden_pgid": "1002", "full_name": "Not Real", "position": "WR", "gsis_id": "none", "note": ""},
          {"madden_pgid": "1003", "full_name": "Someone Else", "position": "QB", "gsis_id": "g7", "note": ""},
          {"madden_pgid": "9999", "full_name": "Ghost", "position": "QB", "gsis_id": "g7", "note": ""}]
    r = L.match(players, people, ov)
    by = {l["player_id"]: l for l in r.links}
    assert by[1]["method"] == "OVERRIDE" and by[1]["gsis_id"] == "g7"
    assert r.no_person == [2] and 2 not in by
    assert by[3]["method"] == "EXACT" and by[3]["gsis_id"] == "g3"        # refused override falls through
    reasons = [x["reason"] for x in r.refused]
    assert any("Pat Moved" in x for x in reasons) and any("not in this database" in x for x in reasons)


def test_unmatched_veteran_and_rookie_lists_and_report(tmp_path):
    players = [mu(1, "Vet", "Nobody", exp=5, overall=80), mu(2, "Rook", "Nobody", exp=0, overall=60),
               mu(3, "Patrick", "Mahomes")]
    people = [person("g1", "Patrick Mahomes"), person("g2", "Left League", last=2025, team=None)]
    r = L.match(players, people)
    assert [m.player_id for m in r.unmatched] == [1, 2]
    md, csv_path = L.write_report(r, players, people, "mu_scratch_x", 2026, str(tmp_path))
    text = open(md).read()
    assert "Unmatched veterans" in text and "Vet Nobody" in text and "Rook Nobody" in text
    assert "Left League" in text and "| EXACT | 1 |" in text
    rows = list(csv.DictReader(open(csv_path)))
    assert {r["status"] for r in rows} == {"linked", "unmatched", "real_unlinked"}


def test_deterministic_rerun():
    players = [mu(i, "Sam", f"Player{i}", pos="WR") for i in range(5)]
    people = [person(f"g{i}", f"Sam Player{i}", pos="WR") for i in range(5)]
    a = L.match(list(reversed(players)), people)
    b = L.match(players, list(reversed(people)))
    assert a.links == b.links


def test_real_side_filters_and_roster_team():
    rows = [{"gsis_id": "a", "display_name": "Active Guy", "rookie_season": 2020, "last_season": 2025, "espn_id": "1"},
            {"gsis_id": "b", "display_name": "Retired Guy", "rookie_season": 2005, "last_season": 2015},
            {"gsis_id": "c", "display_name": "Rookie Guy", "rookie_season": None, "last_season": None,
             "draft_year": 2026},
            {"gsis_id": "d", "display_name": "Old Id Clash", "rookie_season": 1990, "last_season": 1991,
             "espn_id": "9"}]
    people = L.real_side(rows, [{"espn_id": "1", "team": "KC"}, {"espn_id": "9", "team": "NE"}], 2026)
    assert [(p.gsis_id, p.team_2026) for p in people] == [("a", "KC"), ("c", None)]


def test_snapshot_extras_draft_year():
    ex = L.snapshot_extras({"players": [{"pgid": 1, "year_drafted": -4, "college": None},
                                        {"pgid": 2, "year_drafted": 2019}]})
    assert ex[1]["draft_year"] == 2023 and ex[2]["draft_year"] == 2019


def test_overrides_file_header():
    with open(os.path.join(ROOT, "data", "history", "nfl_link_overrides.csv")) as f:
        assert f.readline().strip().split(",") == list(L.OVERRIDE_COLUMNS)


class FakeCur:
    def __init__(self, results):
        self.results, self.sql, self.many, self.description = list(results), [], [], None
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        rows, names = self.results.pop(0) if self.results else ([], [])
        self._rows, self.description = rows, [(n,) for n in names]

    def executemany(self, sql, seq):
        self.many.append((sql, list(seq)))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _cur():
    people = ([("g1", "Patrick Mahomes", "Patrick", "Mahomes", "QB", None, 2017, 2025, 2017, None, 75, 225)],
              ["gsis_id", "display_name", "first_name", "last_name", "position", "espn_id", "rookie_season",
               "last_season", "draft_year", "college", "height_in", "weight"])
    return FakeCur([people, ([(False,)], ["x"]),
                    ([(5, 77, "P. Mahomes", "Patrick Mahomes", "6'2\"", 225, None, "QB", "KC", 9, 30, 99)], []),
                    ([], []), ([(9,)], ["id"])])


def test_run_link_dry_run_writes_report_only(tmp_path):
    cur = _cur()
    r = L.run_link(cur, 2026, apply=False, db_label="t", report_dir=str(tmp_path),
                   overrides_path=str(tmp_path / "none.csv"), rosters_path=str(tmp_path / "none.csv"))
    assert [l["gsis_id"] for l in r.links] == ["g1"]
    assert not any("nfl_player_links" in s for s in cur.sql) and not cur.many
    assert (tmp_path / "link_t.md").exists()


def test_run_link_apply_writes_links_and_load(tmp_path):
    cur = _cur()
    L.run_link(cur, 2026, apply=True, db_label="t", report_dir=str(tmp_path),
               overrides_path=str(tmp_path / "none.csv"), rosters_path=str(tmp_path / "none.csv"))
    assert "DELETE FROM nfl_player_links" in cur.sql
    assert any(s.startswith("INSERT INTO nfl_history_loads") for s in cur.sql)
    assert cur.many[0][1][0][:3] == (5, "g1", "EXACT")
