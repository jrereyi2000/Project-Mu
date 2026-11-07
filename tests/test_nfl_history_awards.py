"""engines/nfl_history/awards (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.5, §4; Wikipedia fallback)."""
import os

import pytest

from engines.nfl_history import awards as A

ROOT = os.path.join(os.path.dirname(__file__), "..")

AWARD_PAGE = """
<table class="wikitable"><caption>Legend</caption><tr><th>x</th></tr></table>
<table class="wikitable sortable plainrowheaders"><caption>List of AP NFL Most Valuable Player winners</caption>
<tbody><tr><th scope="col">Season</th><th scope="col">Player</th><th>Position</th><th>Team</th><th>Votes<sup>[1]</sup></th></tr>
<tr><td><a href="/wiki/1998_NFL_season">1998</a></td><th scope="row"><span class="sortkey" style="display:none">Davis</span>Terrell Davis</th><td>Running back</td><td>Denver Broncos</td><td>1</td></tr>
<tr><td><a href="/wiki/1999_NFL_season">1999</a></td><th scope="row">Kurt Warner<sup>[2]</sup></th><td>Quarterback</td><td><a title="St. Louis Rams">St. Louis Rams</a></td><td>2</td></tr>
<tr><td rowspan="1">2003</td><th scope="row">Peyton Manning <span>(2)</span><br/>Steve McNair</th><td>Quarterback</td><td>Indianapolis Colts<br/>Tennessee Titans</td><td>3</td></tr>
<tr><td rowspan="2">2010</td><th scope="row">Tom Brady&nbsp;*</th><td>Quarterback</td><td>New England Patriots</td><td>4</td></tr>
<tr><th scope="row">Second Row Same Season</th><td>Kicker</td><td>Oakland Raiders</td><td>5</td></tr>
</tbody></table>
"""

SB_PAGE = """
<table class="wikitable"><caption>Super Bowl Most Valuable Players</caption>
<tr><th>Year [a]</th><th>Super Bowl</th><th>Winner</th><th>Team</th><th>Position</th><th>College</th></tr>
<tr><td>2000</td><td>XXXIV</td><td>Kurt Warner</td><td>St. Louis Rams</td><td>QB</td><td>Northern Iowa</td></tr>
<tr><td>2026</td><td>LX</td><td>Kenneth Walker III</td><td>Seattle Seahawks</td><td>RB</td><td>Michigan State</td></tr>
</table>
"""

COTY_PAGE = """
<table class="wikitable"><caption>List of AP NFL Coach of the Year winners</caption>
<tr><th>Season</th><th>Coach</th><th>Team</th><th>Record</th></tr>
<tr><td>2002</td><td>Andy Reid ^</td><td>Philadelphia Eagles</td><td>12-4</td></tr>
</table>
"""

ALLPRO_PAGE = """
<p>intro</p>
<!--
<table class="wikitable"><tr><th colspan="3">Offense</th></tr>
<tr><th>Position</th><th>First team</th><th>Second team</th></tr>
<tr><td>Quarterback</td><td>Lamar Jackson, Baltimore (AP, PFWA, TSN)</td><td>Josh Allen, Buffalo (AP-2)</td></tr>
<tr><td>Wide receiver</td><td>Ja'Marr Chase, Cincinnati (AP, PFWA)<br/>Puka Nacua, Los Angeles Rams (TSN)<br/>Justin Jefferson, Minnesota Vikings (AP),</td><td>Puka Nacua, Los Angeles Rams (AP-2t)<br/>Someone Else, Detroit (PFWA-2)</td></tr>
<tr><td>Left tackle</td><td>Tristan Wirfs, Tampa Bay (AP)</td><td>Jordan Mailata, Philadelphia (AP-2)</td></tr>
</table>
-->
<table class="wikitable"><tr><td>Baltimore Ravens</td><td>6</td></tr></table>
"""


def test_parse_tables_rowspan_and_hidden_sortkeys():
    [_, t] = A.parse_tables(AWARD_PAGE)
    rows = [[c["text"] for c in r] for r in t["grid"]]
    assert rows[1][1] == "Terrell Davis"           # display:none sortkey dropped
    assert rows[2][1] == "Kurt Warner"              # <sup> dropped
    assert rows[5][0] == "2010"                     # rowspan carried down


def test_parse_award_page_seasons_cowinners_and_teams():
    out = A.parse_award_page(AWARD_PAGE, "MVP", "Most Valuable Player winners")
    assert [(r["season"], r["player_name"], r["team_code"], r["position"]) for r in out] == [
        (1999, "Kurt Warner", "STL", "QB"),
        (2003, "Peyton Manning", "IND", "QB"), (2003, "Steve McNair", "TEN", "QB"),
        (2010, "Tom Brady", "NE", "QB"), (2010, "Second Row Same Season", "OAK", "K")]


def test_parse_award_page_super_bowl_year_is_next_calendar_year_and_coach():
    sb = A.parse_award_page(SB_PAGE, "SBMVP", "Super Bowl Most Valuable Players")
    assert [(r["season"], r["player_name"], r["team_code"]) for r in sb] == [
        (1999, "Kurt Warner", "STL"), (2025, "Kenneth Walker III", "SEA")]
    [c] = A.parse_award_page(COTY_PAGE, "COTY", "Coach of the Year")
    assert c == {"season": 2002, "award_type": "COTY", "team_code": "PHI", "position": None,
                 "coach_name": "Andy Reid"}
    with pytest.raises(ValueError):
        A.parse_award_page(COTY_PAGE, "Pro Bowl")


def test_parse_allpro_reads_commented_table_and_keeps_ap_only():
    out = A.parse_allpro(ALLPRO_PAGE, 2024)
    got = {(r["award_type"], r["position"], r["player_name"], r["team_code"]) for r in out}
    assert got == {
        ("First Team All-Pro", "QB", "Lamar Jackson", "BAL"), ("Second Team All-Pro", "QB", "Josh Allen", "BUF"),
        ("First Team All-Pro", "WR", "Ja'Marr Chase", "CIN"),
        ("First Team All-Pro", "WR", "Justin Jefferson", "MIN"),
        ("Second Team All-Pro", "WR", "Puka Nacua", "LA"),
        ("First Team All-Pro", "LT", "Tristan Wirfs", "TB"),
        ("Second Team All-Pro", "LT", "Jordan Mailata", "PHI")}
    assert all(r["season"] == 2024 for r in out)


def test_team_code_forms():
    assert A.team_code("Los Angeles Rams") == "LA"
    assert A.team_code("Los Angeles Chargers") == "LAC"
    assert A.team_code("Washington Redskins") == "WAS"
    assert A.team_code("New England") == "NE"
    assert A.team_code("Atlanta", ["2025 Atlanta Falcons season"]) == "ATL"
    assert A.team_code("Nowhere") is None


def test_resolver_by_name_team_and_season():
    players = [{"gsis_id": "g1", "display_name": "Josh Allen", "first_name": "Josh", "last_name": "Allen"},
               {"gsis_id": "g2", "display_name": "Josh Allen", "first_name": "Josh", "last_name": "Allen"},
               {"gsis_id": "g3", "display_name": "Ja'Marr Chase", "first_name": "Ja'Marr", "last_name": "Chase"}]
    lines = [{"gsis_id": "g1", "season": 2024, "team_id": "BUF"}, {"gsis_id": "g2", "season": 2024, "team_id": "JAX"},
             {"gsis_id": "g3", "season": 2024, "team_id": "CIN"}]
    r = A.Resolver(players, lines)
    assert r.resolve("Josh Allen", 2024, "BUF") == "g1"
    assert r.resolve("Josh Allen", 2024, None) is None          # two people, no team: unresolved
    assert r.resolve("JaMarr Chase", 2024, "XXX") == "g3"
    assert r.resolve("Nobody", 2024, "BUF") is None


def test_csv_round_trip(tmp_path):
    raw = A.parse_allpro(ALLPRO_PAGE, 2024) + A.parse_award_page(COTY_PAGE, "COTY")
    rows = A.to_csv_rows(raw)
    path = str(tmp_path / "a.csv")
    A.write_csv(rows, path)
    back = A.read_csv(path)
    assert len(back) == len(rows) and back[0]["season"] == 2002 and back[0]["coach_name"] == "Andy Reid"
    assert back[0]["gsis_id"] is None and set(back[0]) == set(A.CSV_COLUMNS)


def test_award_types_match_league_strings():
    from engines.player_popularity_engine import AWARD_POINTS
    for award in AWARD_POINTS:
        assert award in A.NFL_AWARD_TYPES
    assert set(A.SINGLE_AWARDS) <= set(A.NFL_AWARD_TYPES)


def test_committed_csv_has_one_mvp_per_season_except_2003():
    rows = A.read_csv(A.AWARDS_CSV)
    mvps = {}
    for r in rows:
        assert r["award_type"] in A.NFL_AWARD_TYPES
        if r["award_type"] == "MVP":
            mvps.setdefault(r["season"], []).append(r["player_name"])
    assert sorted(mvps) == list(range(1999, 2026))
    assert all(len(v) == 1 for s, v in mvps.items() if s != 2003) and len(mvps[2003]) == 2


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as e:
        A.main(["--help"])
    assert e.value.code == 0
    assert "build" in capsys.readouterr().out
