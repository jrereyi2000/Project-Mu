"""engines/tuning targets + compare (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4, §4). Offline: the nflverse
download is mocked with tiny CSVs and the snapshots are synthetic."""
import csv
import json

import pytest

from engines.tuning import compare as C
from engines.tuning import targets as T
from pipeline.load_save import passer_rating


# ---------- synthetic nflverse ----------

def _player(pos, **cols):
    return {"position": pos, "position_group": "", "season_type": "REG", **cols}


PLAYERS = {
    2023: [  # 17 games a team
        _player("QB", attempts=500, completions=300, passing_yards=4000, passing_tds=30, passing_interceptions=10),
        _player("QB", attempts=300, completions=180, passing_yards=2000, passing_tds=10, passing_interceptions=10),
        _player("QB", attempts=50, completions=45, passing_yards=700, passing_tds=8, passing_interceptions=0),
        _player("RB", carries=250, rushing_yards=1000, rushing_tds=8, receptions=40, receiving_yards=300),
        _player("DE", def_sacks=10, def_tackles_solo=30, def_tackle_assists=10),
        _player("LB", def_sacks=5.5, def_tackles_solo=40, def_tackle_assists=20),   # an edge listed LB: FRONT too
        _player("CB", def_interceptions=4, def_tackles_solo=50, def_pass_defended=12),
        _player("K", fg_made=30, fg_att=35, pat_made=40, pat_att=41),
        _player("P", pt_att=60, pt_yards=2700, pt_net_yards=2400),
        _player("OT", fumbles_total=1, receptions=1, receiving_yards=5),            # no group, still in league totals
    ],
    2015: [  # 16 games a team
        _player("QB", attempts=480, completions=300, passing_yards=4000, passing_tds=25, passing_interceptions=12),
        _player("WR", receptions=100, receiving_yards=1400, receiving_tds=10),
    ],
}
TEAMS = {
    2023: [{"season": 2023, "team": "AAA", "season_type": "REG", "games": 17, "passing_yards": 4000, "rushing_yards": 2000,
            "def_sacks": 40, "passing_interceptions": 10, "fumbles_lost_total": 5},
           {"season": 2023, "team": "BBB", "season_type": "REG", "games": 17, "passing_yards": 3000, "rushing_yards": 1500,
            "def_sacks": 30, "passing_interceptions": 15, "fumbles_lost_total": 10}],
    2015: [{"season": 2015, "team": "LV", "season_type": "REG", "games": 16, "passing_yards": 3200, "rushing_yards": 1600,
            "def_sacks": 32, "passing_interceptions": 8, "fumbles_lost_total": 8},
           {"season": 2015, "team": "AAA", "season_type": "REG", "games": 16, "passing_yards": 3200, "rushing_yards": 1600,
            "def_sacks": 32, "passing_interceptions": 8, "fumbles_lost_total": 8}],
}
SCHEDULE = [
    {"season": 2023, "game_type": "REG", "home_team": "AAA", "away_team": "BBB", "home_score": 30, "away_score": 20},
    {"season": 2023, "game_type": "REG", "home_team": "BBB", "away_team": "AAA", "home_score": 10, "away_score": 17},
    {"season": 2023, "game_type": "POST", "home_team": "AAA", "away_team": "BBB", "home_score": 50, "away_score": 0},
    {"season": 2015, "game_type": "REG", "home_team": "OAK", "away_team": "AAA", "home_score": 24, "away_score": 16},
    {"season": 2016, "game_type": "REG", "home_team": "OAK", "away_team": "AAA", "home_score": "", "away_score": ""},
]


def _write_csv(path, rows):
    fields = sorted({k for r in rows for k in r})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    return str(path)


for _y, _rows in PLAYERS.items():
    for _r in _rows:
        _r["season"] = _y


@pytest.fixture
def nflverse(tmp_path, monkeypatch):
    files = {T.SCHEDULE_URL: _write_csv(tmp_path / "games.csv", SCHEDULE)}
    for y in PLAYERS:
        urls = T._urls(y)
        files[urls["players"]] = _write_csv(tmp_path / f"p{y}.csv", PLAYERS[y])
        files[urls["teams"]] = _write_csv(tmp_path / f"t{y}.csv", TEAMS[y])
    calls = []

    def fake_get(url, cache_name, refresh=False):
        calls.append((url, cache_name, refresh))
        return files[url]
    monkeypatch.setattr(T, "_get", fake_get)
    return calls


def test_build_targets_from_synthetic_nflverse(nflverse):
    t = T.build_targets([2015, 2023])
    s = t["stats"]
    # 2015 is scaled to 17 games (4000 * 17/16 = 4250); band = min/max across seasons, mean = their average
    assert s["QB.pass_yards.rank1"]["by_season"] == {"2015": 4250.0, "2023": 4000.0}
    assert (s["QB.pass_yards.rank1"]["lo"], s["QB.pass_yards.rank1"]["hi"], s["QB.pass_yards.rank1"]["mean"]) == \
        (4000.0, 4250.0, 4125.0)
    # percentiles over qualifiers only (>= 224 attempts): 4000 and 2000 in 2023
    assert s["QB.pass_yards.p50"]["by_season"]["2023"] == 3000.0
    # rate leaders are qualifiers only: the 50-attempt QB's rating doesn't lead
    assert s["QB.pass_rating.rank1"]["by_season"]["2023"] == passer_rating(300, 500, 4000, 30, 10)
    assert "QB.pass_yards.rank5" not in s           # fewer than 5 QBs
    # league totals per team-game: every player (the 3 QBs), over 34 team-games; the OT counts too
    assert s["league.pass_attempts_per_team_game"]["by_season"]["2023"] == round(850 / 34, 3)
    assert s["league.receptions_per_team_game"]["by_season"]["2023"] == round(41 / 34, 3)
    # FRONT pools DE and LB; DEF pools FRONT and DB
    assert s["FRONT.def_sacks.rank1"]["by_season"]["2023"] == 10.0
    assert s["DEF.def_tackles.rank1"]["by_season"]["2023"] == 60.0     # LB: 40 solo + 20 assists
    assert s["DB.def_ints.rank1"]["by_season"]["2023"] == 4.0
    # team totals: points from the REG schedule (POST ignored), OAK's old abbreviation mapped to stats_team's LV
    assert s["team.points.max"]["by_season"]["2023"] == 47.0
    assert s["team.points.max"]["by_season"]["2015"] == round(24 * 17 / 16, 3)
    assert s["league.points_per_team_game"]["by_season"]["2023"] == round(77 / 34, 3)
    assert s["team.turnovers.min"]["by_season"]["2023"] == 15.0
    assert s["team.total_yards.max"]["by_season"]["2023"] == 6000.0
    assert t["meta"]["thresholds"]["QB"] == {"column": "pass_attempts", "min": 224}
    assert t["meta"]["per_season"]["2015"]["teams_without_points"] == []
    assert {u for u, _, _ in nflverse} >= {T.SCHEDULE_URL, T._urls(2015)["players"], T._urls(2023)["teams"]}


def test_targets_cli_writes_json(nflverse, tmp_path):
    out = tmp_path / "t.json"
    assert T.main(["--seasons", "2015,2023", "--refresh", "--out", str(out)]) == 0
    t = json.loads(out.read_text())
    assert t["meta"]["seasons"] == [2015, 2023]
    assert "QB.pass_yards.rank1" in t["stats"]
    assert all(refresh for _, _, refresh in nflverse)


def test_parse_seasons_and_default_path():
    assert list(T.parse_seasons("2015-2017")) == [2015, 2016, 2017]
    assert T.parse_seasons("2019,2021") == [2019, 2021]
    assert T.default_path().endswith("data/tuning/targets_2015_2025.json")


def test_percentile_linear():
    assert T.percentile([1, 2, 3, 4], 50) == 2.5
    assert T.percentile([5], 90) == 5
    assert T.percentile([], 50) is None


# ---------- synthetic snapshot ----------

def _game(year, week_type, status, home, away, hs, as_, hbox=None, abox=None):
    return {"franchise_year": year, "week_type": week_type, "week_index": 0, "status": status,
            "home_team_index": home, "away_team_index": away, "home_score": hs, "away_score": as_,
            "home_team_stats": hbox, "away_team_stats": abox}


def _box(pas, rush, sacks, giveaways):
    return {"OFFPASSYARDS": pas, "OFFRUSHYARDS": rush, "OFFYARDS": pas + rush, "SACKS": sacks, "GIVEAWAYS": giveaways}


def _row(kind, year, **stats):
    return {"kind": kind, "table": "x", "stats": {"SEAS_YEAR": year, **stats}}


def snapshot(games=None):
    return {
        "season": {"calendar_year": 2028, "franchise_year": 2, "base_calendar_year": 2026,
                   "week_type": "SuperBowl", "week_index": 22},
        "games": games if games is not None else [
            _game(2, "RegularSeason", "HomeWon", 0, 1, 24, 10, _box(250, 100, 3, 1), _box(200, 80, 2, 2)),
            _game(2, "RegularSeason", "AwayWon", 1, 0, 14, 21, _box(220, 90, 1, 0), _box(260, 110, 4, 1)),
            _game(2, "PreSeason", "HomeWon", 0, 1, 50, 0, _box(999, 999, 9, 9), _box(1, 1, 1, 1)),
            _game(1, "RegularSeason", "HomeWon", 0, 1, 3, 0, _box(10, 10, 0, 0), _box(10, 10, 0, 0)),
            _game(2, "RegularSeason", "Unplayed", 0, 1, 0, 0),
        ],
        "players": [
            {"position": "QB", "season_stats": [
                _row("offense", 2, PASSATTEMPTS=60, PASSCOMPLETED=40, PASSYARDS=510, PASSTDS=4, PASSINTS=1,
                     PASSSACKED=3, GAMESPLAYED=2),
                _row("offense", 1, PASSATTEMPTS=600, PASSYARDS=5000)]},
            {"position": "HB", "season_stats": [_row("offense", 2, RUSHATTEMPTS=30, RUSHYARDS=150, RUSHTDS=1)]},
            {"position": "LE", "season_stats": [_row("defense", 2, DEFTACKLES=5, ASSDEFTACKLES=2, DLINESACKS=1,
                                                     DLINEHALFSACK=1)]},
            {"position": "K", "season_stats": [_row("kicking", 2, KICKFGMADE=3, KICKFGATTEMPTS=4, KICKEPMADE=5,
                                                    KICKEPATTEMPTS=5)]},
            {"position": "LT", "season_stats": [_row("oline", 2, OLINESACKSALLOWED=1)]},
            {"position": "WR", "season_stats": []},
        ],
    }


def test_season_from_snapshot():
    sim = C.season_from_snapshot(snapshot())
    m, s = sim["meta"], sim["stats"]
    assert (m["season"], m["franchise_year"], m["games"], m["teams"], m["games_per_team"]) == (2028, 2, 2, 2, 2.0)
    assert m["players"] == 5 and not m["no_games"]
    # counting stats scaled to 17 games (x 8.5); only franchise year 2's rows count
    assert s["QB.pass_attempts.rank1"] == 60 * 8.5
    assert s["QB.pass_yards.rank1"] == 510 * 8.5
    assert s["RB.rush_yards.rank1"] == 150 * 8.5
    assert s["FRONT.def_sacks.rank1"] == 1.5 * 8.5      # DLINESACKS + half a sack per DLINEHALFSACK (load_save)
    assert s["DEF.def_tackles.rank1"] == 7 * 8.5         # solo + assists
    assert s["K.kick_fgm.rank1"] == 3 * 8.5
    # per team-game values aren't scaled: 4 regular-season team-games
    assert s["league.pass_attempts_per_team_game"] == 15.0
    assert s["league.pass_comp_pct"] == round(100 * 40 / 60, 3)
    assert s["league.points_per_team_game"] == round((24 + 10 + 14 + 21) / 4, 3)
    assert s["team.points.max"] == 45 * 8.5               # team 0: 24 + 21
    assert s["team.total_yards.max"] == (350 + 370) * 8.5
    assert s["team.sacks.min"] == 3 * 8.5                 # team 1: 2 + 1
    assert s["team.turnovers.max"] == 2 * 8.5


def test_season_from_snapshot_other_season():
    sim = C.season_from_snapshot(snapshot(), season=2027)
    assert sim["meta"]["franchise_year"] == 1 and sim["meta"]["games"] == 1
    assert sim["stats"]["QB.pass_yards.rank1"] == 5000 * 17


def test_no_games_played():
    snap = snapshot(games=[_game(2, "RegularSeason", "Unplayed", 0, 1, 0, 0)])
    sim = C.season_from_snapshot(snap)
    assert sim["meta"]["no_games"] and sim["stats"] == {}
    report = C.compare(sim, {"meta": {"seasons": [2015, 2025]},
                             "stats": {"QB.pass_yards.rank1": {"lo": 1, "mean": 2, "hi": 3}}})
    assert report.no_games and report.score is None and report.worst() == []
    text = C.format_report(report)
    assert "No games played in season 2028" in text


# ---------- scoring ----------

def test_band_score():
    assert C.band_score(5, 4, 5, 6) == 0.0
    assert C.band_score(4, 4, 5, 6) == 0.0                # the edges are inside
    assert C.band_score(8, 4, 5, 6) == 1.0                # 2 above, width 2
    assert C.band_score(1, 4, 5, 6) == 1.5                # 3 below
    # zero-width band: floored at 5% of |mean|, or the floor when that's smaller
    assert C.band_score(105, 100, 100, 100) == pytest.approx(1.0)
    assert C.band_score(1, 0, 0, 0, floor=1.0) == 1.0
    assert C.band_score(0.5, 0, 0, 0) == pytest.approx(0.5 / C.WIDTH_FLOOR)


def test_resolution():
    assert C.resolution("DEF.def_forced_fumbles.p50") == C.COUNT_FLOOR
    assert C.resolution("team.points.max") == C.COUNT_FLOOR
    assert C.resolution("QB.pass_rating.p50") == C.WIDTH_FLOOR
    assert C.resolution("league.pass_yards_per_team_game") == C.WIDTH_FLOOR


def test_compare_report():
    targets = {"meta": {"seasons": [2015, 2025]}, "stats": {
        "a.x.rank1": {"lo": 10, "mean": 12, "hi": 14},        # inside
        "a.y.rank1": {"lo": 10, "mean": 12, "hi": 14},        # 2 widths above (width 4)
        "league.z": {"lo": 1, "mean": 1, "hi": 1},            # zero width, 5% floor: 0.05
        "a.missing.p50": {"lo": 0, "mean": 1, "hi": 2},
    }}
    sim = {"meta": {"season": 2028, "games": 272, "teams": 32, "games_per_team": 17.0, "players": 1700},
           "stats": {"a.x.rank1": 12, "a.y.rank1": 22, "league.z": 1.1}}
    r = C.compare(sim, targets)
    by = {s.key: s for s in r.stats}
    assert by["a.x.rank1"].score == 0
    assert by["a.y.rank1"].score == 2.0
    assert by["league.z"].score == pytest.approx(2.0)
    assert (by["a.y.rank1"].sim, by["a.y.rank1"].lo, by["a.y.rank1"].mean, by["a.y.rank1"].hi) == (22, 10, 12, 14)
    assert r.score == pytest.approx(4 / 3, abs=1e-3)
    assert r.missing == ["a.missing.p50"]
    assert [s.key for s in r.worst(2)] == ["a.y.rank1", "league.z"]
    text = C.format_report(r)
    assert "**Score: 1.333**" in text and "## Worst 10" in text and "`a.missing.p50`" in text
    assert text.index("a.y.rank1") < text.index("## By section")


# ---------- CLI ----------

def test_compare_cli(tmp_path, capsys, nflverse):
    tpath, spath, out = tmp_path / "targets.json", tmp_path / "s.json", tmp_path / "report.md"
    tpath.write_text(json.dumps(T.build_targets([2015, 2023])))
    spath.write_text(json.dumps(snapshot()))
    assert C.main([str(spath), "--targets", str(tpath), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "# Tuning comparison: season 2028" in printed and "**Score:" in printed
    assert out.read_text() == printed


def test_compare_cli_no_games_and_missing_targets(tmp_path, capsys):
    tpath, spath = tmp_path / "targets.json", tmp_path / "s.json"
    tpath.write_text(json.dumps({"meta": {"seasons": [2015, 2025]}, "stats": {}}))
    spath.write_text(json.dumps(snapshot(games=[])))
    assert C.main([str(spath), "--targets", str(tpath)]) == 0
    assert "No games played" in capsys.readouterr().out
    assert C.main([str(spath), "--targets", str(tmp_path / "nope.json")]) == 2
    assert "engines.tuning.targets" in capsys.readouterr().err
