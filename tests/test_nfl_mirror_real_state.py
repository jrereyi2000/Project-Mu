"""engines/nfl_mirror real_state and fetch (NFL_WEEKLY_MIRROR_PLAN.md §2.1-2.2, §4). Inline fixtures, no network."""
import gzip
import os

import pytest

from engines.nfl_mirror import fetch, real_state as rs


def roster(team, week, gsis, name, status="ACT", abbr="A01", pos="QB", season=2026, **kw):
    r = {"season": str(season), "team": team, "week": str(week), "gsis_id": gsis, "full_name": name,
         "position": pos, "status": status, "status_description_abbr": abbr, "game_type": "REG",
         "height": "74", "weight": "220", "jersey_number": "12", "birth_date": "1999-01-02", "college": "Utah",
         "entry_year": "2021", "draft_number": "NA"}
    r.update(kw)
    return r


def game(week, home, away, hs="21", as_="17", day="2026-09-13", hc="Home Coach", ac="Away Coach"):
    return {"season": "2026", "week": str(week), "game_type": "REG", "gameday": day, "home_team": home,
            "away_team": away, "home_score": hs, "away_score": as_, "home_coach": hc, "away_coach": ac}


def depth(team, dt, gsis, pos="QB", rank="1", grp="Offense", slot="1"):
    return {"dt": dt, "team": team, "player_name": gsis, "gsis_id": gsis, "pos_grp": grp, "pos_name": pos,
            "pos_abb": pos, "pos_slot": slot, "pos_rank": rank}


def injury(team, week, gsis, status="Out", part="Knee"):
    return {"season": "2026", "team": team, "week": str(week), "gsis_id": gsis, "position": "QB",
            "full_name": gsis, "report_primary_injury": part, "report_status": status, "practice_status": "DNP"}


# ---------- status map ----------

@pytest.mark.parametrize("status,abbr,want", [
    ("ACT", "A01", "ACTIVE"), ("ACT", "I01", "INACTIVE"), ("INA", "I02", "INACTIVE"), ("DEV", "P01", "PS"),
    ("DEV", "P07", "PS"), ("RES", "R01", "IR"), ("RES", "R48", "IR"), ("RES", "R04", "PUP"), ("RES", "R05", "NFI"),
    ("RES", "R99", "IR"), ("CUT", "W03", None), ("RET", "", None), ("EXE", "E01", None),
])
def test_status_map(status, abbr, want):
    assert rs.map_status(status, abbr) == want


def test_unknown_reserve_code_is_logged(caplog):
    with caplog.at_level("WARNING"):
        assert rs.map_status("RES", "R77") == "IR"
    assert "R77" in caplog.text


# ---------- team codes, playoff weeks ----------

def test_team_codes_map_to_save_codes():
    assert rs.team_code("ARI") == "AZ"
    assert rs.team_code("LA") == "LAR"
    assert rs.team_code("WAS") == "WAS"
    assert rs.team_code("LV") == "LV"
    assert rs.team_code("LAC") == "LAC"
    assert rs.team_code("") is None
    assert rs.team_code("XYZ") is None


def test_build_week_uses_save_codes():
    w = rs.build_week([roster("ARI", 1, "g1", "A"), roster("LA", 1, "g2", "B")], [], [],
                      [game(1, "LA", "ARI")], 2026, 1)
    assert set(w.rosters) == {"AZ", "LAR"}
    assert w.head_coach == {"LAR": "Home Coach", "AZ": "Away Coach"}


@pytest.mark.parametrize("mu,real", [(1, 1), (18, 18), (19, 19), (20, 20), (21, 21), (23, 22)])
def test_real_week_for(mu, real):
    assert rs.real_week_for(mu) == real


@pytest.mark.parametrize("mu", [0, 22, 24])
def test_real_week_for_rejects(mu):
    with pytest.raises(ValueError):
        rs.real_week_for(mu)


# ---------- players ----------

def test_player_fields_and_left_off():
    rows = [roster("KC", 1, "g1", "Pat Q", height="6-3", draft_number="10"),
            roster("KC", 1, "g2", "Cut Guy", status="CUT", abbr="W03"),
            roster("KC", 1, "g3", "PS Guy", status="DEV", abbr="P02"),
            roster("KC", 1, "g4", "IR Guy", status="RES", abbr="R48")]
    w = rs.build_week(rows, [], [], [game(1, "KC", "BUF")], 2026, 1)
    by = {p.gsis_id: p for p in w.rosters["KC"]}
    assert set(by) == {"g1", "g3", "g4"}
    p = by["g1"]
    assert (p.name, p.team, p.status, p.height_in, p.weight_lbs, p.draft_number, p.jersey, p.entry_year) == \
        ("Pat Q", "KC", "ACTIVE", 75, 220, 10, 12, 2021)
    assert by["g3"].status == "PS"
    assert (by["g4"].status, by["g4"].designation) == ("IR", "R48")


def test_other_seasons_ignored():
    w = rs.build_week([roster("KC", 1, "g1", "A"), roster("KC", 1, "old", "B", season=2025)], [], [], [], 2026, 1)
    assert [p.gsis_id for p in w.rosters["KC"]] == ["g1"]


# ---------- bye carry forward ----------

def test_bye_team_carries_forward():
    rows = [roster("KC", 1, "k1", "K1"), roster("KC", 2, "k1", "K1"), roster("KC", 2, "k2", "K2"),
            roster("BUF", 1, "b1", "B1"), roster("BUF", 3, "b9", "B9"),
            roster("NYJ", 2, "j1", "J1"), roster("NYJ", 3, "j1", "J1"), roster("NYJ", 3, "k2", "K2")]
    inj = [injury("KC", 2, "k1", "Questionable"), injury("KC", 3, "zz", "Out")]
    games = [game(1, "KC", "BUF", day="2026-09-10"), game(2, "KC", "NYJ", day="2026-09-17", hc="Reid"),
             game(3, "BUF", "NYJ", day="2026-09-24")]
    w = rs.build_week(rows, inj, [], games, 2026, 3)
    assert w.carried_forward == ("KC",)
    # k2 moved to NYJ this week, so he leaves KC's carried roster
    assert [p.gsis_id for p in w.rosters["KC"]] == ["k1"]
    assert sorted(p.gsis_id for p in w.rosters["NYJ"]) == ["j1", "k2"]
    assert [p.gsis_id for p in w.rosters["BUF"]] == ["b9"]
    assert w.injuries["k1"].report_status == "Questionable"   # carried from KC's week 2
    assert "zz" not in w.injuries                               # KC has no week-3 roster, so week 3 is not its source
    # head coach on a bye: nearest earlier game
    assert w.head_coach["KC"] == "Reid"


def test_source_weeks():
    rows = [roster("KC", 1, "k1", "K1"), roster("KC", 3, "k1", "K1"), roster("BUF", 2, "b1", "B1")]
    assert rs.source_weeks(rows, 2026, 3) == {"KC": 3, "BUF": 2}
    assert rs.source_weeks(rows, 2026, 2) == {"KC": 1, "BUF": 2}


# ---------- modes ----------

def test_catch_up_vs_live():
    games = [game(1, "KC", "BUF"), game(1, "NYJ", "MIA"),
             game(2, "KC", "NYJ"), game(2, "BUF", "MIA", hs="", as_="")]
    assert rs.week_mode(games, 2026, 1) == rs.CATCH_UP
    assert rs.week_mode(games, 2026, 2) == rs.LIVE
    assert rs.week_mode(games, 2026, 3) == rs.LIVE   # no games yet
    games.append({**game(2, "BUF", "MIA"), "home_score": "NA", "away_score": "NA"})
    assert rs.week_mode(games, 2026, 2) == rs.LIVE
    w = rs.build_week([roster("KC", 1, "k1", "K1")], [], [], games, 2026, 1)
    assert w.mode == rs.CATCH_UP


# ---------- depth chart snapshot ----------

def test_chart_snapshot_choice_catch_up_and_live():
    rows_depth = [
        depth("KC", "2026-09-01T12:00:00Z", "old_qb"),
        depth("KC", "2026-09-09T12:00:00Z", "wk1_qb"),
        depth("KC", "2026-09-09T12:00:00Z", "wk1_qb2", rank="2"),
        depth("KC", "2026-09-16T12:00:00Z", "new_qb"),
        depth("KC", "2026-09-09T12:00:00Z", "returner", pos="KR", grp="Special Teams"),
        depth("KC", "2026-09-09T12:00:00Z", "kicker", pos="PK", grp="Special Teams"),
        depth("KC", "2026-09-09T12:00:00Z", "punter", pos="P", grp="Special Teams"),
        depth("BUF", "2026-09-20T12:00:00Z", "late_qb"),
    ]
    rows = [roster("KC", 1, "k1", "K1")]
    final = [game(1, "KC", "BUF", day="2026-09-10")]
    w = rs.build_week(rows, [], rows_depth, final, 2026, 1)
    assert w.mode == rs.CATCH_UP
    assert w.depth["KC"] == {"QB": ["wk1_qb", "wk1_qb2"], "PK": ["kicker"], "P": ["punter"]}
    assert w.depth["BUF"] == {"QB": ["late_qb"]}   # nothing on or before game day: oldest snapshot
    live = [game(1, "KC", "BUF", day="2026-09-10", hs="", as_="")]
    w = rs.build_week(rows, [], rows_depth, live, 2026, 1)
    assert w.mode == rs.LIVE
    assert w.depth["KC"] == {"QB": ["new_qb"]}


def test_chart_order_by_rank():
    rows_depth = [depth("KC", "2026-09-09", "b", pos="WR", rank="2"), depth("KC", "2026-09-09", "a", pos="WR"),
                  depth("KC", "2026-09-09", "a", pos="WR", slot="2")]
    assert rs.build_depth(rows_depth, 2026)["KC"]["WR"] == ["a", "b"]


# ---------- head coach ----------

def test_head_coach_changes_by_week():
    games = [game(1, "KC", "BUF", hc="Old"), game(2, "BUF", "KC", ac="Interim")]
    assert rs.head_coaches(games, 2026, 1)["KC"] == "Old"
    assert rs.head_coaches(games, 2026, 2)["KC"] == "Interim"
    assert rs.head_coaches(games, 2026, 5)["KC"] == "Interim"


# ---------- later weeks ----------

def test_later_weeks():
    rows = [roster("KC", 1, "k1", "K1", status="RES", abbr="R01"), roster("KC", 2, "k1", "K1", status="RES", abbr="R01"),
            roster("KC", 3, "k1", "K1")]
    inj = [injury("KC", 3, "k1", "Questionable"), injury("KC", 3, "x", "Out")]
    out = rs.later_weeks(rows, inj, 2026, 1)
    assert sorted(out) == [2, 3]
    assert out[2]["k1"] == {"status": "IR", "report_status": None}
    assert out[3]["k1"] == {"status": "ACTIVE", "report_status": "Questionable"}
    assert out[3]["x"] == {"status": None, "report_status": "Out"}


def test_sources_passed_through():
    w = rs.build_week([], [], [], [], 2026, 1, sources={"rosters": "abc"})
    assert w.sources == {"rosters": "abc"} and w.mode == rs.LIVE and w.rosters == {}


# ---------- fetch ----------

def test_fetch_week_uses_history_get(monkeypatch):
    calls = []

    def fake_get(url, cache_name, refresh=False, delay=0):
        calls.append((url, cache_name, refresh))
        return fetch.FetchedFile("/x/" + cache_name, url, cache_name, "sha-" + cache_name, 1)

    monkeypatch.setattr(fetch.history_fetch, "get", fake_get)
    files = fetch.fetch_week(2026, refresh=True)
    assert set(files) == set(fetch.NAMES)
    urls = {c[0].split("/download/", 1)[1] for c in calls}
    assert urls == {"weekly_rosters/roster_weekly_2026.csv.gz", "injuries/injuries_2026.csv.gz",
                    "depth_charts/depth_charts_2026.csv.gz", "schedules/games.csv",
                    "contracts/historical_contracts.parquet"}
    assert all(c[2] is True for c in calls)
    assert files["games"].cache_name == "nflverse_games.csv"   # shared with engines.nfl_history
    assert fetch.sources(files)["rosters"] == "sha-nflverse_roster_weekly_2026.csv.gz"


def test_read_rows_gz_and_plain(tmp_path):
    gz = tmp_path / "a.csv.gz"
    with gzip.open(gz, "wt", encoding="utf-8", newline="") as f:
        f.write("season,team\n2026,KC\n2026,ARI\n")
    plain = tmp_path / "b.csv"
    plain.write_text("x,y\n1,2\n")
    assert list(fetch.read_rows(fetch.FetchedFile(str(gz), "", "a", "", 0))) == \
        [{"season": "2026", "team": "KC"}, {"season": "2026", "team": "ARI"}]
    assert list(fetch.read_rows(str(plain))) == [{"x": "1", "y": "2"}]
    with pytest.raises(ValueError):
        list(fetch.read_rows(os.path.join(str(tmp_path), "c.parquet")))
