"""engines/nfl_mirror/trades (NFL_WEEKLY_MIRROR_PLAN.md §2.3, §4). Inline fixtures, no network."""
from engines.nfl_mirror import trades
from engines.nfl_mirror.real_state import RealPlayer, RealWeek


def p(gid, team, status="ACTIVE"):
    return RealPlayer(gsis_id=gid, name=gid.upper(), position="WR", team=team, status=status, designation="")


def week(n, rosters):
    return RealWeek(season=2026, week=n, mode="CATCH_UP", rosters=rosters)


def test_committed_csv_is_header_only():
    assert trades.read_trades() == []
    with open(trades.DEFAULT_PATH) as f:
        assert f.readline().strip().split(",") == list(trades.COLUMNS)


def test_read_trades(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text(
        "trade_id,date,week,from_team,to_team,gsis_id,player_name,note\n"
        "# a comment line\n"
        "T1,2026-10-28,9,ARI,LA,00-001,Ann A,\n"
        "T1,2026-10-28,9,LA,ARI,,,2027 4th round pick\n"
        "\n"
        "T2,2026-11-01,,NYJ,KC,00-002,Bo B,claimed? no: trade\n")
    legs = trades.read_trades(str(path))
    assert len(legs) == 3
    a = legs[0]
    assert (a.trade_id, a.date, a.week, a.from_team, a.to_team, a.gsis_id, a.player_name) == \
        ("T1", "2026-10-28", 9, "AZ", "LAR", "00-001", "Ann A")
    assert legs[1].gsis_id is None and legs[1].note == "2027 4th round pick"
    assert legs[2].week is None


def test_read_trades_missing_file(tmp_path):
    assert trades.read_trades(str(tmp_path / "none.csv")) == []


def test_infer_switches():
    prev = week(8, {"KC": [p("a", "KC"), p("b", "KC")], "BUF": [p("c", "BUF")], "NYJ": [p("d", "NYJ")]})
    cur = week(9, {"KC": [p("b", "KC")], "BUF": [p("c", "BUF"), p("a", "BUF", "PS")], "NYJ": [],
                   "MIA": [p("new", "MIA")]})
    sw = trades.infer_switches(prev, cur)
    assert sw == [trades.Switch("a", "A", "KC", "BUF", 9, "PS")]   # d was cut, new was signed: not switches


def test_unconfirmed():
    s1 = trades.Switch("a", "A", "KC", "BUF", 9, "ACTIVE")
    s2 = trades.Switch("b", "B", "NYJ", "MIA", 9, "ACTIVE")
    s3 = trades.Switch("c", "C", "LV", "DEN", 9, "ACTIVE")
    legs = [trades.TradeLeg("T1", None, 9, "KC", "BUF", "a", "A", None),
            trades.TradeLeg("T2", None, 8, "NYJ", "MIA", "b", "B", None),     # wrong week
            trades.TradeLeg("T3", None, None, "LV", "DEN", "c", "C", None),   # no week: any week
            trades.TradeLeg("T3", None, None, "DEN", "LV", None, None, "pick")]
    assert trades.unconfirmed([s1, s2, s3], legs) == [s2]
    assert trades.unconfirmed([s1], []) == [s1]
