"""engines/nfl_history CLI and record check (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.9, §2.10, §4)."""
import pytest

import engines.nfl_history.__main__ as cli
from engines.nfl_history import check, store

COMMANDS = ("fetch", "load", "link", "coach-baseline", "init", "check", "status")


def test_help_lists_seven_commands(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for c in COMMANDS:
        assert c in out


@pytest.mark.parametrize("cmd", COMMANDS)
def test_each_command_has_help(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([cmd, "--help"])
    assert e.value.code == 0


@pytest.mark.parametrize("cmd", [c for c in COMMANDS if c != "fetch"])
def test_db_required(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([cmd])
    assert e.value.code == 2
    assert "--db" in capsys.readouterr().err


def test_refuses_live_databases(capsys):
    assert cli.main(["status", "--db", "madden"]) == 2
    assert cli.main(["load", "--db", "madden_2036", "--apply"]) == 2


def test_seasons_arg():
    assert list(cli.seasons_arg("1999-2001")) == [1999, 2000, 2001]
    assert list(cli.seasons_arg("2010")) == [2010]


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.cursors = 0

    def cursor(self):
        self.cursors += 1
        raise AssertionError("dry run must not open a cursor")

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _parsed():
    return ({"players": [{"gsis_id": "g"}], "stats": [{"season": 2000}]}, [], {})


def test_load_dry_run_writes_nothing(monkeypatch, capsys):
    conn = FakeConn()
    monkeypatch.setattr(store, "connect", lambda db: conn)
    monkeypatch.setattr(cli, "parse_all", lambda seasons, only, fetch_=False: _parsed())
    assert cli.main(["load", "--db", "mu_scratch_x", "--seasons", "2000"]) == 0
    assert conn.commits == 0 and conn.cursors == 0
    assert "dry run" in capsys.readouterr().out


def test_init_runs_load_link_baseline_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "connect", lambda db: FakeConn())
    monkeypatch.setattr(cli, "do_load", lambda conn, args: calls.append(("load", args.apply)))
    monkeypatch.setattr(cli, "do_link", lambda conn, args: calls.append(("link", args.snapshot)))
    monkeypatch.setattr(cli, "do_coach_baseline", lambda conn, args: calls.append(("baseline", args.apply)))
    assert cli.main(["init", "--db", "mu_scratch_x", "--snapshot", "s.json", "--apply"]) == 0
    assert calls == [("load", True), ("link", "s.json"), ("baseline", True)]


def test_counts_of():
    assert cli.counts_of({"stats": [1, 2], "teams": {"team_seasons": [1], "coach_seasons": []}}) == {
        "stats": 2, "teams": {"team_seasons": 1, "coach_seasons": 0}}


# ---------- record check ----------

def test_check_compare_mismatch_and_not_found():
    book = [
        {"category": "season_pass_yards", "value": 5477, "holder": "Peyton Manning", "season": 2013, "team_id": 9},
        {"category": "season_pass_yards", "value": 5084, "holder": "Patrick Mahomes", "season": 2018, "team_id": 16},
        {"category": "season_sacks", "value": 22.5, "holder": "Michael Strahan", "season": 2001, "team_id": 24},
        {"category": "season_rush_yards", "value": 2105, "holder": "Eric Dickerson", "season": 1984, "team_id": 19},
        {"category": "season_team_wins", "value": 16, "holder": "2007 New England Patriots", "season": 2007,
         "team_id": 22},
        {"category": "season_rec_yards", "value": 1964, "holder": "Calvin Johnson", "season": 2012, "team_id": 11},
        {"category": "game_pass_yards", "value": 554, "holder": "Norm Van Brocklin", "season": 2001, "team_id": 1},
    ]
    players = {(2013, "peyton manning"): {"season_pass_yards": 5477},
               (2018, "patrick mahomes"): {"season_pass_yards": 5097},
               (2001, "michael strahan"): {"season_sacks": 22.0}}
    teams = {(2007, 22): {"season_team_wins": 16}}
    out = check.compare(book, players, teams)
    got = [(m.holder, m.status, m.nfl) for m in out]
    assert got == [("Patrick Mahomes", "MISMATCH", 5097.0), ("Calvin Johnson", "NOT_FOUND", None)]


def test_check_report(tmp_path):
    out = [check.Mismatch("season_pass_yards", 2018, "Patrick Mahomes", 16, 5084, 5097, "MISMATCH")]
    path = check.write_report(out, 10, "mu_scratch_x", str(tmp_path))
    text = open(path).read()
    assert "Patrick Mahomes" in text and "checked" in text and "authority" in text
