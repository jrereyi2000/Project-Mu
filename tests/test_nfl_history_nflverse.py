"""engines/nfl_history fetch, teams and nflverse (docs/done/REAL_NFL_HISTORY_PLAN.md §2.3, §4)."""
import io

import pytest

from engines.nfl_history import fetch, nflverse as N, teams


# ---------- teams ----------

@pytest.mark.parametrize("code,season,mu", [
    ("OAK", 2010, "LV"), ("LV", 2021, "LV"), ("SD", 2010, "LAC"), ("LAC", 2018, "LAC"), ("STL", 2010, "LAR"),
    ("LA", 2017, "LAR"), ("LAR", 2020, "LAR"), ("ARI", 2005, "AZ"), ("WSH", 2023, "WAS"), ("WAS", 2000, "WAS"),
    ("HOU", 2002, "HOU"), ("GNB", 2001, "GB"), ("KAN", 2001, "KC"), ("SDG", 2001, "LAC"), ("TAM", 2001, "TB"),
])
def test_mu_abbr(code, season, mu):
    assert teams.mu_abbr(code, season) == mu


def test_mu_abbr_raises_for_houston_before_2002_and_unknown():
    with pytest.raises(teams.UnknownTeam):
        teams.mu_abbr("HOU", 2001)
    with pytest.raises(teams.UnknownTeam):
        teams.mu_abbr("XXX", 2010)


def test_division_of_alignments():
    assert teams.division_of("JAX", 2000) == ("AFC", "AFC Central")
    assert teams.division_of("SEA", 2001) == ("AFC", "AFC West")
    assert teams.division_of("SEA", 2002) == ("NFC", "NFC West")
    assert teams.division_of("STL", 1999) == ("NFC", "NFC West")
    assert len(teams.teams_in(2001)) == 31 and len(teams.teams_in(2002)) == 32


# ---------- players ----------

def test_parse_players_heights_and_blanks():
    rows = N.parse_players([
        {"gsis_id": "00-1", "display_name": "A Real", "height": "6-2", "weight": "200", "rookie_season": "2010",
         "draft_year": "", "birth_date": "1990-01-02", "college_name": "Utah", "draft_team": "KAN"},
        {"gsis_id": "00-2", "display_name": "B Real", "height": "74"},
        {"gsis_id": "", "display_name": "No Id"},
    ])
    assert [r["height_in"] for r in rows] == [74, 74]
    assert rows[0]["draft_year"] is None and rows[0]["rookie_season"] == 2010
    assert rows[0]["birth_date"].isoformat() == "1990-01-02" and rows[0]["college"] == "Utah"
    assert N.players_from_stats([{"player_id": "g", "player_display_name": "G Man", "position": "QB"},
                                 {"player_id": "g", "player_display_name": "G Man"}]) == [
        {"gsis_id": "g", "display_name": "G Man", "position": "QB"}]


# ---------- player seasons ----------

def _stat_row(**kw):
    base = {"player_id": "00-9", "player_display_name": "K Kicker", "position": "K", "recent_team": "OAK",
            "games": "16"}
    base.update(kw)
    return base


def test_parse_player_seasons_mapping_blank_vs_zero_tackles_fg_ranges():
    [r] = N.parse_player_seasons([_stat_row(
        passing_yards="0", rushing_yards="", def_tackles_solo="3", def_tackle_assists="2", def_sacks="1.5",
        fg_made_0_19="1", fg_made_20_29="2", fg_missed_20_29="1", fg_made_50_59="1", fg_made_60_="1",
        fg_missed_50_59="2", fg_long="61", targets="7", special_teams_tds="1")], 2015, "REG")
    assert r["team_id"] == "LV" and r["team_abbr"] == "OAK" and r["games_played"] == 16
    assert r["pass_yards"] == 0 and r["rush_yards"] is None
    assert r["def_tackles"] == 5 and r["def_sacks"] == 1.5
    assert (r["kick_fgm_29"], r["kick_fga_29"]) == (3, 4)
    assert (r["kick_fgm_50"], r["kick_fga_50"]) == (2, 4)
    assert r["kick_fgm_39"] is None and r["kick_fga_39"] is None
    assert r["kick_fg_long"] == 61 and r["rec_targets"] == 7 and r["st_tds"] == 1
    assert r["kr_tds"] is None and r["pr_tds"] is None


def test_parse_player_seasons_columns_are_store_columns():
    from engines.nfl_history.store import COLUMNS
    [r] = N.parse_player_seasons([_stat_row()], 2015, "POST")
    assert set(r) == set(COLUMNS["nfl_player_seasons"])
    assert r["def_tackles"] is None


def test_parse_player_seasons_missing_team():
    with pytest.raises(N.ParseError):
        N.parse_player_seasons([_stat_row(recent_team="")], 1999, "REG")
    skipped = []
    assert N.parse_player_seasons([_stat_row(recent_team="")], 1999, "REG", skipped) == []
    assert skipped[0]["gsis_id"] == "00-9"


def test_coverage_counts_tacklers():
    rows = N.parse_player_seasons([_stat_row(def_tackles_solo="1"), _stat_row(player_id="x")], 2000, "REG")
    assert N.coverage(rows) == {2000: 1}


# ---------- games and derivations ----------

def _g(season, gt, home, away, hs, as_, hc="H Coach", ac="A Coach", week=1, gid=None):
    return {"game_id": gid or f"{season}_{week}_{away}_{home}_{gt}", "season": str(season), "game_type": gt,
            "week": str(week), "gameday": f"{season}-09-10", "home_team": home, "away_team": away,
            "home_score": str(hs), "away_score": str(as_), "overtime": "0", "home_coach": hc, "away_coach": ac}


def test_parse_games_types_and_filters():
    rows = [_g(2019, "REG", "KC", "OAK", 28, 10), _g(2019, "SB", "KC", "SF", 31, 20),
            _g(2026, "REG", "KC", "LV", 1, 0), dict(_g(2019, "REG", "KC", "DEN", 0, 0), home_score="")]
    games = N.parse_games(rows, range(1999, 2026))
    assert [g["game_type"] for g in games] == ["REG", "SB"]
    assert games[0]["away_team_id"] == "LV" and games[0]["away_abbr"] == "OAK" and games[0]["overtime"] is False
    with pytest.raises(N.ParseError):
        N.parse_games([_g(2019, "XX", "KC", "SF", 1, 0)], [2019])


def test_team_seasons_record_tie_points_and_playoff_results():
    games = N.parse_games([
        _g(2010, "REG", "KC", "DEN", 20, 10, week=1), _g(2010, "REG", "DEN", "KC", 17, 17, week=2),
        _g(2010, "REG", "KC", "LV", 3, 7, week=3),
        _g(2010, "WC", "KC", "NE", 24, 21, week=18), _g(2010, "DIV", "PIT", "KC", 30, 10, week=19),
        _g(2010, "WC", "BUF", "LV", 10, 13, week=18), _g(2010, "DIV", "LV", "IND", 20, 14, week=19),
        _g(2010, "CON", "LV", "TEN", 20, 14, week=20), _g(2010, "SB", "LV", "GB", 20, 21, week=21),
    ], [2010])
    rows = {r["team_id"]: r for r in N.team_seasons(games)}
    kc = rows["KC"]
    assert (kc["wins"], kc["losses"], kc["ties"]) == (1, 1, 1)
    assert (kc["points_for"], kc["points_against"]) == (40, 34)
    assert kc["playoff_result"] == "DIVISIONAL" and (kc["playoff_wins"], kc["playoff_losses"]) == (1, 1)
    assert rows["LV"]["playoff_result"] == "SUPER_BOWL_LOSS"
    assert rows["DEN"]["playoff_result"] == "MISSED"
    assert kc["standings_source"] == "COMPUTED" and kc["conference"] == "AFC" and kc["division"] == "AFC West"
    assert kc["head_coach"] == "H Coach"
    # teams with no regular-season game (here: playoff-only opponents) have no row
    assert "GB" not in rows


def test_playoff_result_champion_and_each_round():
    assert N.playoff_result([]) == "MISSED"
    assert N.playoff_result([(0, False)]) == "WILD_CARD"
    assert N.playoff_result([(0, True), (1, False)]) == "DIVISIONAL"
    assert N.playoff_result([(1, True), (2, False)]) == "CONFERENCE"
    assert N.playoff_result([(2, True), (3, False)]) == "SUPER_BOWL_LOSS"
    assert N.playoff_result([(2, True), (3, True)]) == "CHAMPION"


def test_team_seasons_nfldata_standings_used_when_present():
    games = N.parse_games([_g(2016, "REG", "LA", "SF", 28, 0)], [2016])
    std = [{"season": "2016", "team": "LA", "div_rank": "3", "seed": ""},
           {"season": "2016", "team": "SF", "div_rank": "4", "seed": ""}]
    rows = {r["team_id"]: r for r in N.team_seasons(games, std)}
    assert rows["LAR"]["division_place"] == 3 and rows["LAR"]["conference_seed"] is None
    assert rows["LAR"]["standings_source"] == "NFLDATA" and rows["LAR"]["team_abbr"] == "LA"


def _bracket(season, conf_teams, bye_teams, extra_reg=()):
    """A season where every listed team plays: regular-season games from `extra_reg`, WC games among the non-bye
    teams, DIV games for the bye teams."""
    rows = list(extra_reg)
    wc = [t for t in conf_teams if t not in bye_teams]
    for i in range(0, len(wc), 2):
        rows.append(_g(season, "WC", wc[i], wc[i + 1], 20, 10, week=18, gid=f"wc{season}{i}"))
    for i, t in enumerate(bye_teams):
        rows.append(_g(season, "DIV", t, wc[2 * i], 30, 0, week=19, gid=f"dv{season}{i}"))
    return rows


def test_place_and_seed_six_and_seven_seed_brackets():
    afc = ["NE", "PIT", "LV", "IND", "KC", "BUF", "TEN"]
    reg = []
    wk = 1
    for i, t in enumerate(afc):          # NE best ... TEN worst by wins over the NFC filler teams
        for k in range(len(afc) - i):
            reg.append(_g(2010, "REG", t, "SF", 10, 0, week=wk, gid=f"r{t}{k}"))
            wk += 1
        reg.append(_g(2010, "REG", "SF", t, 10, 0, week=wk, gid=f"l{t}"))
        wk += 1
    six = N.parse_games(_bracket(2010, afc[:6], ["NE", "PIT"], reg), [2010])
    seeds = {t: s for t, (_p, s) in N.place_and_seed(six, 2010).items() if s}
    assert seeds["NE"] == 1 and seeds["PIT"] == 2 and len([t for t in seeds if t in afc]) == 6
    reg20 = [dict(r, season="2020", game_id=r["game_id"] + "x") for r in reg]
    seven = N.parse_games(_bracket(2020, afc, ["NE"], reg20), [2020])
    seeds = {t: s for t, (_p, s) in N.place_and_seed(seven, 2020).items() if s and t != "SF"}
    assert seeds["NE"] == 1 and max(seeds.values()) == 7


def test_place_and_seed_division_tiebreak_head_to_head_then_net_points():
    games = N.parse_games([
        _g(2005, "REG", "KC", "DEN", 10, 7, week=1), _g(2005, "REG", "DEN", "LV", 50, 0, week=2),
        _g(2005, "REG", "LV", "KC", 3, 0, week=3), _g(2005, "REG", "LAC", "SF", 0, 1, week=4),
    ], [2005])
    places = {t: p for t, (p, _s) in N.place_and_seed(games, 2005).items()}
    # KC, DEN, LV all 1-1; head-to-head is 1-1 each, so net points: DEN +47, KC 0, LV -47; LAC 0-1 last
    assert [t for t, _ in sorted(places.items(), key=lambda kv: kv[1]) if t != "SF"] == ["DEN", "KC", "LV", "LAC"]


def test_coach_seasons_interim_and_full_season():
    games = N.parse_games([
        _g(2008, "REG", "KC", "DEN", 10, 7, hc="First Coach", week=1),
        _g(2008, "REG", "KC", "LV", 7, 10, hc="Interim Guy", week=2),
        _g(2008, "REG", "DEN", "KC", 7, 7, ac="Interim Guy", week=3),
        _g(2008, "WC", "KC", "NE", 20, 10, hc="Interim Guy", week=18),
        _g(2008, "DIV", "KC", "PIT", 20, 10, hc="Interim Guy", week=19),
        _g(2008, "CON", "KC", "TEN", 20, 10, hc="Interim Guy", week=20),
        _g(2008, "SB", "KC", "GB", 20, 10, hc="Interim Guy", week=21),
    ], [2008])
    rows = {(r["coach_name"], r["team_id"]): r for r in N.coach_seasons(games)}
    first, interim = rows[("First Coach", "KC")], rows[("Interim Guy", "KC")]
    assert (first["wins"], first["losses"], first["games"], first["full_season"]) == (1, 0, 1, False)
    assert (interim["wins"], interim["losses"], interim["ties"], interim["games"]) == (0, 1, 1, 2)
    assert interim["playoff_result"] == "CHAMPION" and interim["super_bowl_wins"] == 1
    assert first["playoff_result"] == "MISSED"


def test_parse_draft():
    rows = [{"season": "2001", "round": "1", "pick": "1", "team": "SDG", "gsis_id": "", "pfr_player_id": "VickMi00",
             "pfr_player_name": "Michael Vick", "position": "QB", "college": "Virginia Tech"},
            {"season": "2001", "round": "2", "pick": "32", "team": "GNB", "gsis_id": "00-5",
             "pfr_player_name": "X", "position": "WR"},
            {"season": "2001", "round": "2", "pick": "33", "team": "KAN", "pfr_player_name": "Y"},
            {"season": "1998", "round": "1", "pick": "1", "team": "IND", "pfr_player_name": "Old"}]
    out = N.parse_draft(rows, range(1999, 2027))
    assert [(p["overall"], p["round"], p["pick"], p["team_id"]) for p in out] == [
        (1, 1, 1, "LAC"), (32, 2, 1, "GB"), (33, 2, 2, "KC")]
    assert out[0]["gsis_id"] is None and out[0]["pfr_id"] == "VickMi00"


def test_resolve_team_ids():
    rows = [{"team_id": "KC"}, {"home_team_id": "LV", "away_team_id": "LAC", "x": "KC"}]
    N.resolve_team_ids(rows, {"KC": 16, "LV": 17, "LAC": 18})
    assert rows == [{"team_id": 16}, {"home_team_id": 17, "away_team_id": 18, "x": "KC"}]


# ---------- fetch ----------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_get_caches_atomically_and_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))
    calls = []

    def fake_open(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent")))
        return _Resp(b"a,b\n1,2\n")
    monkeypatch.setattr(fetch, "_urlopen", fake_open)
    f1 = fetch.get("https://x/y.csv", "nflverse_y.csv", delay=0)
    f2 = fetch.get("https://x/y.csv", "nflverse_y.csv", delay=0)
    assert len(calls) == 1 and calls[0][1] == fetch.USER_AGENT
    assert f1.sha256 == f2.sha256 and len(f1.sha256) == 64 and f1.bytes == 8
    assert (tmp_path / "nflverse_y.csv").read_bytes() == b"a,b\n1,2\n"
    fetch.get("https://x/y.csv", "nflverse_y.csv", refresh=True, delay=0)
    assert len(calls) == 2


def test_fetch_cut_download_never_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))

    def broken(req, timeout):
        raise OSError("cut")
    monkeypatch.setattr(fetch, "_urlopen", broken)
    with pytest.raises(OSError):
        fetch.get("https://x/z.csv", "z.csv", delay=0)
    assert not (tmp_path / "z.csv").exists()


def test_file_list_and_cached(tmp_path, monkeypatch):
    names = [n for _, n in fetch.file_list(range(1999, 2001))]
    assert names[0] == "nflverse_players.csv" and "nflverse_stats_player_post_2000.csv" in names
    assert names[-1] == "nfldata_standings.csv" and "nflverse_draft_picks.csv" in names
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        fetch.cached(range(1999, 2001))
