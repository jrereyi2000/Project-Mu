"""Offline tests for the NFL Weekly Mirror's watcher gating and CLI (NFL_WEEKLY_MIRROR_PLAN.md §2.10, §2.11).

A mirrored week skips the GM trade proposals, the newsroom articles and the press conferences, and the week-done
notification carries ``mirror: N moves (M unconfirmed switches)``. A normal week keeps all three. The CLI's five
commands run through ``main(argv)`` against a fake cursor: no database and no network.
"""
import json
import os
import subprocess
import types
from unittest import mock

import pytest

from engines.nfl_mirror import __main__ as mirror_main
from engines.nfl_mirror import real_state
from pipeline import weekly_capture
from pipeline import watch_save as ws

SEASON = 2026


# -------------------------------------------------------------------------------------------- fakes ---
class ScriptedCursor:
    """Records SQL and returns the scripted rows, one execute at a time."""

    def __init__(self, returns=()):
        self.returns = list(returns)
        self.sql = []
        self._ret = None

    def execute(self, sql, args=None):
        self.sql.append((" ".join(sql.split()), args))
        self._ret = self.returns.pop(0) if self.returns else None

    def executemany(self, sql, rows):
        self.sql.append((" ".join(sql.split()), list(rows)))

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def statements(self):
        return [s for s, _ in self.sql]


class FakeConn:
    def __init__(self, cursor, dbname="madden_x"):
        self._cursor = cursor
        self.info = types.SimpleNamespace(dbname=dbname)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def signed(*runs, tables=True, league_state=None, links=()):
    """A records-free fake cursor whose to_regclass answers are `tables` and whose other queries return `runs`.

    `league_state` answers the preview's identity query and `links` the nfl_player_links join, so the CLI's new
    database-bound paths can run offline without consuming a scripted row."""
    class SignedCursor(ScriptedCursor):
        def execute(self, sql, args=None):
            norm = " ".join(sql.split())
            self.sql.append((norm, args))
            if norm.startswith("SELECT to_regclass"):
                self._ret = (tables,)
            elif "FROM league_state" in norm:
                self._ret = league_state
            elif "nfl_player_links l JOIN players" in norm:
                self._ret = None
                self._all = list(links)
            else:
                self._ret = self.returns.pop(0) if self.returns else None

        def fetchall(self):
            return getattr(self, "_all", [])

    return FakeConn(SignedCursor(runs))


def snapshot_path(tmp_path, week_index=5):
    path = tmp_path / "save.json"
    path.write_text(json.dumps({"season": {"calendar_year": SEASON, "week_type": "RegularSeason",
                                           "week_index": week_index}}))
    return str(path)


def patched_ingest(monkeypatch, tmp_path, mirror):
    """The ingest's DB and long-running steps are patched; `mirror` is (info, active)."""
    import pipeline.load_save as load_save

    logs = []
    mocks = {
        "load": mock.patch.object(load_save, "load",
                                  return_value={"league_state": "x", "players": 1, "games_completed": 16}),
        "bootstrap": mock.patch.object(ws, "bootstrap_new_players", return_value=0),
        "modes": mock.patch.object(ws, "personality_modes", return_value=None),
        "producers": mock.patch.object(ws, "step_relationship_producers", return_value=None),
        "promises": mock.patch.object(ws, "step_coach_promises", return_value=None),
        "evolution": mock.patch.object(ws, "step_personality_evolution", return_value=None),
        "focus": mock.patch.object(ws, "step_focus_refresh", return_value=None),
        "requests": mock.patch.object(ws, "step_trade_requests", return_value=None),
        "mirror": mock.patch.object(ws, "mirror_week_active", return_value=mirror),
        "engines": mock.patch.object(ws, "run_weekly_engines"),
        "articles": mock.patch.object(ws, "step_articles", return_value={"status": "published", "week": 6}),
        "notify": mock.patch.object(ws, "send_notify"),
        "queue": mock.patch.object(ws, "queue_nightly_jobs"),
        "pack": mock.patch.object(ws, "prepare_next_week_mod_pack"),
    }
    started = {name: m.start() for name, m in mocks.items()}
    path = snapshot_path(tmp_path)
    return path, logs, started, mocks


def stop(mocks):
    for m in mocks.values():
        m.stop()


# ------------------------------------------------------------------------------- watch_save gating ---
def test_a_mirrored_week_skips_gm_articles_and_press(monkeypatch, tmp_path):
    info = {"stage": "REGULAR_WEEK", "wave": None, "season": SEASON, "week": 6}
    path, logs, started, mocks = patched_ingest(monkeypatch, tmp_path, (info, True))
    notice = mock.patch.object(ws, "mirror_week_notice",
                               return_value="mirror: 12 moves (1 unconfirmed switches)")
    notice.start()
    try:
        res = ws.ingest_snapshot(path, SEASON, 6, save_name="CAREER-A", log_fn=logs.append)
    finally:
        stop(mocks)
        notice.stop()

    started["engines"].assert_called_once_with(SEASON, mode="weekly", log_fn=mock.ANY, gm=False)
    started["articles"].assert_not_called()
    assert "NFL mirror week: no articles" in logs
    assert res["nfl_mirror_week"] is True
    text = started["notify"].call_args[0][0]
    assert "mirror: 12 moves (1 unconfirmed switches)" in text
    assert started["notify"].call_args[1]["title"] == "Mu: week done"


def test_a_normal_week_runs_gm_articles_and_has_no_mirror_fragment(monkeypatch, tmp_path):
    info = {"stage": "REGULAR_WEEK", "wave": None, "season": SEASON, "week": 6}
    path, logs, started, mocks = patched_ingest(monkeypatch, tmp_path, (info, False))
    notice = mock.patch.object(ws, "mirror_week_notice")
    started_notice = notice.start()
    try:
        res = ws.ingest_snapshot(path, SEASON, 6, save_name="CAREER-A", log_fn=logs.append)
    finally:
        stop(mocks)
        notice.stop()

    started["engines"].assert_called_once_with(SEASON, mode="weekly", log_fn=mock.ANY)
    started["articles"].assert_called_once_with(SEASON, week=6)
    assert "NFL mirror week: no articles" not in logs
    assert res["nfl_mirror_week"] is False
    assert "mirror:" not in started["notify"].call_args[0][0]
    started_notice.assert_not_called()


def test_mirror_week_active_reads_the_stores_setting():
    snapshot = {"season": {"calendar_year": SEASON, "week_type": "RegularSeason", "week_index": 5},
                "players": [{"pgid": 1}]}
    conn = signed((True, SEASON, None, None, "ON", None))     # installed, then the settings row
    with mock.patch.object(ws, "mirror_connect", return_value=conn):
        info, active = ws.mirror_week_active("host=localhost dbname=madden_x", snapshot)
    assert info == {"stage": "REGULAR_WEEK", "wave": None, "season": SEASON, "week": 6}
    assert active is True


def test_mirror_week_active_is_false_when_the_tables_are_missing():
    snapshot = {"season": {"calendar_year": SEASON, "week_type": "RegularSeason", "week_index": 5},
                "players": [{"pgid": 1}]}
    conn = signed(tables=False)                                        # nfl_mirror_settings does not exist
    with mock.patch.object(ws, "mirror_connect", return_value=conn):
        info, active = ws.mirror_week_active("host=localhost dbname=madden_x", snapshot)
    assert info["stage"] == "REGULAR_WEEK"
    assert active is False


def test_mirror_week_active_never_fails_an_ingest():
    snapshot = {"season": {"calendar_year": SEASON, "week_type": "RegularSeason", "week_index": 5},
                "players": [{"pgid": 1}]}
    with mock.patch.object(ws, "mirror_connect", side_effect=OSError("no database")):
        info, active = ws.mirror_week_active("host=localhost dbname=madden_x", snapshot)
    assert info["stage"] == "REGULAR_WEEK"
    assert active is False


def test_the_notification_fragment_counts_written_moves_and_unconfirmed_switches():
    conn = signed((9,), (10,), (2,))
    with mock.patch.object(ws, "mirror_connect", return_value=conn), \
            mock.patch.object(ws, "mirror_unconfirmed_switches", return_value=["a", "b", "c"]):
        line = ws.mirror_week_notice("host=localhost dbname=madden_x", SEASON, 10)
    assert line == "mirror: 9 moves (3 unconfirmed switches)"


def test_the_notification_fragment_falls_back_to_planned_releases():
    conn = signed((9,), (10,), (2,))
    with mock.patch.object(ws, "mirror_connect", return_value=conn), \
            mock.patch.object(ws, "mirror_unconfirmed_switches", side_effect=FileNotFoundError("not cached")):
        line = ws.mirror_week_notice("host=localhost dbname=madden_x", SEASON, 10)
    assert line == "mirror: 9 moves (2 unconfirmed switches)"


def test_the_notification_fragment_never_fails_an_ingest():
    with mock.patch.object(ws, "mirror_connect", side_effect=OSError("no database")):
        assert ws.mirror_week_notice("host=localhost dbname=madden_x", SEASON, 10) == ""


# -------------------------------------------------------------------------------- press conferences ---
def _run_ingest_with_result(tmp_path, monkeypatch, result):
    path = snapshot_path(tmp_path)
    def fake_run(cmd, cwd, env):
        with open(tmp_path / "ingest_result.json", "w") as fh:
            json.dump(result, fh)
        return mock.Mock(returncode=0)
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    presses_patch = mock.patch.object(ws, "press_week", return_value=5)
    starts_patch = mock.patch.object(ws, "start_press_week")
    presses, starts = presses_patch.start(), starts_patch.start()
    try:
        logs = []
        ws.run_ingest(path, "host=localhost dbname=madden_x user=postgres", SEASON, 6, save_name="CAREER-A",
                      log_fn=logs.append)
    finally:
        presses_patch.stop()
        starts_patch.stop()
    return presses, starts, logs


def test_a_mirrored_week_skips_the_press_conferences(tmp_path, monkeypatch):
    presses, starts, logs = _run_ingest_with_result(tmp_path, monkeypatch,
                                                    {"status": "success", "nfl_mirror_week": True})
    presses.assert_not_called()
    starts.assert_not_called()
    assert "NFL mirror week: no press conferences" in logs


def test_a_normal_week_starts_the_press_conferences(tmp_path, monkeypatch):
    presses, starts, _ = _run_ingest_with_result(tmp_path, monkeypatch, {"status": "success"})
    presses.assert_called_once()
    starts.assert_called_once()


# -------------------------------------------------------------------------- weekly_capture gm=False ---
def test_run_weekly_engines_gm_false_skips_the_gm_step(monkeypatch):
    patchers, called = {}, {}
    for name in ("step_engines", "step_gm", "step_inbox", "step_scouting", "step_season_archive",
                 "step_gm_hot_seat", "step_coach_hot_seat"):
        patchers[name] = mock.patch.object(weekly_capture, name)
        called[name] = patchers[name].start()
    try:
        logs = []
        weekly_capture.run_weekly_engines(SEASON, log_fn=logs.append, gm=False)
        assert called["step_engines"].called
        called["step_gm"].assert_not_called()
        called["step_inbox"].assert_called_once_with(SEASON)
        called["step_scouting"].assert_called_once_with(SEASON)
        assert any("GM trade proposals skipped (NFL mirror week)" in line for line in logs)

        weekly_capture.run_weekly_engines(SEASON, log_fn=logs.append)
        called["step_gm"].assert_called_once_with(SEASON)
    finally:
        for patcher in patchers.values():
            patcher.stop()


# --------------------------------------------------------------------------------------------- CLI ---
def fake_real_week(week, rosters=None, mode=real_state.CATCH_UP):
    return real_state.RealWeek(season=SEASON, week=week, mode=mode, rosters=rosters or {}, depth={})


def linked_real_week():
    """Real week 6: the linked gsis 00-X is active for KC, which is team_index 0 in the capture fixtures."""
    return real_state.RealWeek(
        season=SEASON, week=6, mode=real_state.CATCH_UP,
        rosters={"KC": [real_state.RealPlayer("00-X", "Linked Man", "WR", "KC", "ACTIVE", "A01")]})


def fetched_files():
    return {name: types.SimpleNamespace(path=f"/fake/{name}", sha256="x")
            for name in ("rosters", "injuries", "depth", "games")}


def save_capture(root, folder, league_id, *, season=SEASON, week_type="RegularSeason", week_index=6,
                 player_team=0, mtime=1, identity=True):
    """A capture save.json with a franchise identity, season/stage and one linked player (pgid 100)."""
    path = root / folder / "save.json"
    path.parent.mkdir(parents=True)
    snapshot = {
        "season": {"calendar_year": season, "week_type": week_type, "week_index": week_index},
        "teams": [{"team_index": 0, "short_name": "KC"}, {"team_index": 1, "short_name": "BUF"}],
        "players": [{"pgid": 100, "first_name": "Linked", "last_name": "Man", "position": "WR",
                     "status": "Signed", "team_index": player_team, "years_pro": 0, "overall": 70, "age": 26,
                     "injury": {"injured_reserve": False}, "year_drafted": None, "college": None,
                     "height_in": None, "weight_lbs": None}],
    }
    if identity:
        snapshot["franchise"] = {"league_id": league_id}
    path.write_text(json.dumps(snapshot))
    os.utime(path, (mtime, mtime))
    return path


def preview_environment(monkeypatch, captures, *, db_league=9001, db_season=SEASON, db_week=6,
                        settings_season=SEASON, links=(("00-X", 100),)):
    """Patch the preview's DB identity, fetch and build; the real roster diff runs against the fixture links."""
    monkeypatch.setattr(mirror_main, "CAPTURES", str(captures))
    conn = signed((True, settings_season, None, None, "ON", None),
                  league_state=(db_league, db_season, db_week), links=links)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)
    built = []

    def build(*args, **kwargs):
        week = kwargs.get("week", args[5])
        built.append(week)
        return linked_real_week()

    fetch = mock.Mock(side_effect=lambda season, refresh=True: fetched_files())
    monkeypatch.setattr(mirror_main, "_build_week", build)
    monkeypatch.setattr(mirror_main, "_fetch_week", fetch)
    monkeypatch.setattr(mirror_main, "_read_rows", lambda fetched: [])
    monkeypatch.setattr(mirror_main, "_read_trades", lambda: [])
    monkeypatch.setattr(mirror_main, "league_cap", lambda snapshot: 255_400_000)
    return types.SimpleNamespace(conn=conn, built=built, fetch=fetch)


def run_preview(*, snapshot=None):
    argv = ["preview", "--week", "6", "--dsn", "host=localhost dbname=mu_scratch_u19_test"]
    if snapshot is not None:
        argv += ["--snapshot", str(snapshot)]
    return mirror_main.main(argv)


def test_cli_help_exits_zero_and_lists_all_five_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        mirror_main.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ("status", "preview", "trades", "enable", "disable"):
        assert command in out


def test_cli_status_prints_settings_last_week_and_next_mode(monkeypatch, capsys):
    conn = signed((True, SEASON, 10, None, "OFF", None),      # installed, settings row
                  (SEASON, 10, 10, "CATCH_UP", "WRITTEN"))              # newest nfl_mirror_weeks row
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)
    monkeypatch.setattr(mirror_main, "_cached_games_rows",
                        lambda season: [{"season": str(SEASON), "week": "11", "home_score": "20",
                                         "away_score": "17"}])

    assert mirror_main.main(["status", "--dsn", "host=localhost dbname=madden_x"]) == 0
    out = capsys.readouterr().out
    assert "enabled" in out and "season 2026" in out
    assert "last mirrored week: season 2026 week 10" in out
    assert "next real week: Mu week 11 -> nflverse week 11, mode CATCH_UP" in out


def test_cli_status_reports_a_missing_migration(monkeypatch, capsys):
    conn = signed(tables=False)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)
    assert mirror_main.main(["status", "--dsn", "host=localhost dbname=madden_x"]) == 1
    assert "not installed" in capsys.readouterr().out


def test_cli_preview_runs_fetch_build_and_diff_and_writes_nothing(monkeypatch, tmp_path, capsys):
    snap = tmp_path / "save.json"
    snap.write_text(json.dumps({"teams": [], "players": []}))
    files = {name: types.SimpleNamespace(path=f"/fake/{name}", sha256="x")
             for name in ("rosters", "injuries", "depth", "games")}
    roster = types.SimpleNamespace(
        ops=[{"kind": "sign", "gsis_id": "00-1", "pgid": 7, "to_index": 2, "why": "real active"}],
        creates=[], ps_fill=[], review=[], unlinked_new=[])
    built = []

    def build(*args, **kwargs):
        built.append(kwargs.get("week", args[5]))
        return fake_real_week(int(kwargs.get("week", args[5])))

    monkeypatch.setattr(mirror_main, "_snapshot_for_database",
                        lambda identity, explicit=None: (str(snap), {"teams": [], "players": []}))
    monkeypatch.setattr(mirror_main, "_fetch_week", lambda season, refresh=True: dict(files))
    monkeypatch.setattr(mirror_main, "_read_rows", lambda fetched: [])
    monkeypatch.setattr(mirror_main, "_build_week", build)
    monkeypatch.setattr(mirror_main, "_plan_rosters", lambda *a, **k: roster)
    monkeypatch.setattr(mirror_main, "_read_trades", lambda: [])
    monkeypatch.setattr(mirror_main, "team_codes", lambda snapshot, season: {})
    monkeypatch.setattr(mirror_main, "league_cap", lambda snapshot: 255_400_000)
    conn = signed((True, SEASON, None, None, "ON", None),          # read_settings in settings_season
                  league_state=(9001, SEASON, 6))
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)

    assert mirror_main.main(["preview", "--week", "6", "--dsn", "host=localhost dbname=madden_x"]) == 0
    out = capsys.readouterr().out
    assert "NFL mirror preview" in out
    assert "real week 6 (CATCH_UP)" in out
    assert "roster ops: 1" in out and "unconfirmed switches: 0" in out
    assert built == [6, 5, 6], "the preview builds the real week, then the diff builds it and the week before"
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in conn.cursor().statements())


def test_cli_trades_prints_the_unconfirmed_switches(monkeypatch, capsys):
    mover = real_state.RealPlayer(gsis_id="00-1", name="Moved Man", position="WR", team="GB", status="ACTIVE",
                                  designation="")
    before = {"CHI": [real_state.RealPlayer(gsis_id="00-1", name="Moved Man", position="WR", team="CHI",
                                            status="ACTIVE", designation="")]}
    after = {"GB": [mover]}

    def build(rows_rosters, rows_injuries, rows_depth, rows_games, season, week, sources=None):
        return fake_real_week(week, before if week == 5 else after)

    files = {name: types.SimpleNamespace(path=f"/fake/{name}") for name in
             ("rosters", "injuries", "depth", "games")}
    monkeypatch.setattr(mirror_main, "_fetch_week", lambda season, refresh=False: dict(files))
    monkeypatch.setattr(mirror_main, "_read_rows", lambda fetched: [])
    monkeypatch.setattr(mirror_main, "_build_week", build)
    monkeypatch.setattr(mirror_main, "_read_trades", lambda: [])
    monkeypatch.setattr(mirror_main, "_connect",
                        lambda dsn: signed((True, SEASON, None, None, "ON", None)))

    assert mirror_main.main(["trades", "--week", "6", "--dsn", "host=localhost dbname=madden_x"]) == 0
    out = capsys.readouterr().out
    assert "1 unconfirmed switch(es)" in out
    assert "Moved Man" in out and "CHI -> GB" in out


def test_cli_enable_needs_the_matching_database_and_writes_the_setting(monkeypatch, capsys):
    cur = ScriptedCursor()
    conn = FakeConn(cur)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)

    assert mirror_main.main(["enable", "--confirm", "wrong", "--dsn", "host=localhost dbname=madden_x"]) == 2
    assert "refused" in capsys.readouterr().err
    assert conn.committed is False and cur.sql == []

    assert mirror_main.main(["enable", "--last-week", "10", "--confirm", "madden_x",
                             "--dsn", "host=localhost dbname=madden_x"]) == 0
    assert conn.committed is True
    assert "NFL mirror enabled on madden_x" in capsys.readouterr().out
    update = next(args for sql, args in cur.sql if sql.startswith("UPDATE nfl_mirror_settings"))
    assert update[0] is True and update[2] == 10


def test_cli_disable_needs_the_matching_database_and_clears_the_switch(monkeypatch, capsys):
    cur = ScriptedCursor()
    conn = FakeConn(cur)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)

    with pytest.raises(SystemExit) as exc:
        mirror_main.main(["disable", "--dsn", "host=localhost dbname=madden_x"])
    assert exc.value.code == 2
    assert "--confirm" in capsys.readouterr().err

    assert mirror_main.main(["disable", "--confirm", "madden_x", "--dsn", "host=localhost dbname=madden_x"]) == 0
    assert conn.committed is True
    update = next(args for sql, args in cur.sql if sql.startswith("UPDATE nfl_mirror_settings"))
    assert update[0] is False and update[2] is None
    assert "NFL mirror disabled on madden_x" in capsys.readouterr().out


def test_cli_commands_are_wired_to_their_functions():
    parser = mirror_main.build_parser()
    args = parser.parse_args(["status", "--dsn", "x"])
    assert args.func is mirror_main.cmd_status
    args = parser.parse_args(["preview", "--week", "3", "--dsn", "x"])
    assert args.func is mirror_main.cmd_preview and args.week == 3
    args = parser.parse_args(["preview", "--week", "3", "--snapshot", "captures/x/save.json", "--dsn", "x"])
    assert args.func is mirror_main.cmd_preview and args.snapshot == "captures/x/save.json"
    args = parser.parse_args(["trades", "--week", "3", "--dsn", "x"])
    assert args.func is mirror_main.cmd_trades and args.week == 3
    args = parser.parse_args(["enable", "--last-week", "9", "--confirm", "db", "--dsn", "x"])
    assert args.func is mirror_main.cmd_enable and args.last_week == 9
    args = parser.parse_args(["disable", "--confirm", "db", "--dsn", "x"])
    assert args.func is mirror_main.cmd_disable


# ---------------------------------------------------------------------------- F1: --confirm identity ---
# Audit-5 U7 F1: the first hand-split dbname= must never authorize a write to a database libpq reads as the last.

def test_f1_enable_refuses_when_the_first_dbname_is_not_the_opened_database(monkeypatch, capsys):
    cur = ScriptedCursor()
    conn = FakeConn(cur, dbname="mu_scratch_u19_test")
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)
    dsn = ("host=localhost dbname=confirmed_name dbname=mu_scratch_u19_test "
           "user=postgres password=postgres")

    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", "confirmed_name"]) == 2
    assert "refused" in capsys.readouterr().err
    assert conn.committed is False and cur.sql == [], "a refused confirmation must write nothing"
    assert conn.closed is True
    assert mirror_main._db_name(dsn) == "mu_scratch_u19_test"      # the message helper is libpq-aware now


def test_f1_enable_accepts_the_last_repeated_dbname_the_connection_opened(monkeypatch, capsys):
    cur = ScriptedCursor()
    conn = FakeConn(cur, dbname="mu_scratch_u19_test")
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)
    dsn = ("host=localhost dbname=confirmed_name dbname=mu_scratch_u19_test "
           "user=postgres password=postgres")

    assert mirror_main.main(["enable", "--last-week", "10", "--dsn", dsn,
                             "--confirm", "mu_scratch_u19_test"]) == 0
    assert conn.committed is True
    assert "NFL mirror enabled on mu_scratch_u19_test" in capsys.readouterr().out
    update = next(args for sql, args in cur.sql if sql.startswith("UPDATE nfl_mirror_settings"))
    assert update[0] is True and update[2] == 10


def test_f1_disable_refuses_a_mismatch_and_writes_nothing(monkeypatch, capsys):
    cur = ScriptedCursor()
    conn = FakeConn(cur, dbname="mu_scratch_u19_test")
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)

    assert mirror_main.main(["disable", "--dsn", "host=localhost dbname=confirmed_name",
                             "--confirm", "confirmed_name"]) == 2
    assert "refused" in capsys.readouterr().err
    assert conn.committed is False and cur.sql == []


@pytest.mark.parametrize("dsn,opened", [
    ("postgresql://postgres:postgres@localhost/wrong?dbname=mu_scratch_u19_test", "mu_scratch_u19_test"),
    ("host=localhost dbname='mu_scratch_u19_test' user=postgres password=postgres", "mu_scratch_u19_test"),
    ("host=localhost user=postgres password=postgres dbname='mu_scratch_u19_test'", "mu_scratch_u19_test"),
    ("postgresql://postgres:postgres@localhost/mu_scratch_u19_test?dbname=wrong", "wrong"),
])
def test_f1_url_and_quoted_names_are_judged_by_the_opened_connection(monkeypatch, capsys, dsn, opened):
    cur = ScriptedCursor()
    conn = FakeConn(cur, dbname=opened)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn_: conn)

    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", "mu_scratch_u19_test"]) == \
        (0 if opened == "mu_scratch_u19_test" else 2)
    if opened == "mu_scratch_u19_test":
        assert conn.committed is True and "enabled on mu_scratch_u19_test" in capsys.readouterr().out
    else:
        assert conn.committed is False and cur.sql == [] and "refused" in capsys.readouterr().err


def test_f1_confirmation_falls_back_to_current_database(monkeypatch, capsys):
    cur = ScriptedCursor(returns=[("actual_db",)])
    conn = FakeConn(cur, dbname=None)                # a connection object that reports no .info.dbname
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: conn)

    assert mirror_main.main(["enable", "--confirm", "actual_db", "--dsn", "host=localhost"]) == 0
    assert conn.committed is True
    assert cur.statements()[0] == "SELECT current_database()"
    assert "enabled on actual_db" in capsys.readouterr().out

    refused_cur = ScriptedCursor(returns=[("actual_db",)])
    refused = FakeConn(refused_cur, dbname=None)
    monkeypatch.setattr(mirror_main, "_connect", lambda dsn: refused)
    assert mirror_main.main(["enable", "--confirm", "wrong", "--dsn", "host=localhost"]) == 2
    assert refused.committed is False
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in refused_cur.statements())


# ------------------------------------------------------------------- F2: preview snapshot identity ---
# Audit-5 U7 F2: the newest capture globally can belong to another franchise; preview must bind it to this DB.

def test_f2_preview_uses_the_databases_franchise_capture_not_the_newest(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    a = save_capture(root, "A", 9001, player_team=0, mtime=1)      # league A, 00-X on KC
    b = save_capture(root, "B", 2002, player_team=1, mtime=2)      # league B, newer, 00-X on BUF
    preview_environment(monkeypatch, root, db_league=9001)

    assert run_preview() == 0
    out = capsys.readouterr().out
    assert f"NFL mirror preview from {a}" in out
    assert "roster ops: 0" in out
    assert str(b) not in out


def test_f2_a_single_matching_capture_is_the_ordinary_control(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    a = save_capture(root, "A", 9001, player_team=0)
    preview_environment(monkeypatch, root, db_league=9001)

    assert run_preview() == 0
    out = capsys.readouterr().out
    assert f"NFL mirror preview from {a}" in out and "roster ops: 0" in out


def test_f2_preview_refuses_when_no_capture_matches_the_database(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    save_capture(root, "B", 2002, player_team=1)
    env = preview_environment(monkeypatch, root, db_league=9001)

    assert run_preview() == 2
    err = capsys.readouterr().err
    assert "refused" in err and "no capture snapshot matches league 9001" in err
    env.fetch.assert_not_called()
    assert env.built == []


def test_f2_preview_refuses_a_capture_without_a_franchise_identity(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    save_capture(root, "A", None, identity=False)
    env = preview_environment(monkeypatch, root, db_league=9001)

    assert run_preview() == 2
    assert "franchise.league_id" in capsys.readouterr().err
    env.fetch.assert_not_called()


def test_f2_preview_refuses_an_unset_database_league_id(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    save_capture(root, "A", 9001)
    env = preview_environment(monkeypatch, root, db_league=None)

    assert run_preview() == 2
    assert "madden_league_id is not set" in capsys.readouterr().err
    env.fetch.assert_not_called()


@pytest.mark.parametrize("capture_kwargs,reason", [
    ({"season": 2027}, "is season 2027"),
    ({"week_type": "WildcardPlayoff", "week_index": 1}, "PLAYOFF_WEEK"),
])
def test_f2_preview_refuses_a_season_or_stage_mismatch(monkeypatch, tmp_path, capsys, capture_kwargs, reason):
    root = tmp_path / "captures"
    save_capture(root, "A", 9001, **capture_kwargs)
    env = preview_environment(monkeypatch, root, db_league=9001, db_week=6)

    assert run_preview() == 2
    assert reason in capsys.readouterr().err
    env.fetch.assert_not_called()


def test_f2_preview_refuses_a_database_week_with_no_stage(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    save_capture(root, "A", 9001)
    env = preview_environment(monkeypatch, root, db_league=9001, db_week=0)

    assert run_preview() == 2
    assert "not a regular-season or playoff week" in capsys.readouterr().err
    env.fetch.assert_not_called()


def test_f2_explicit_snapshot_is_validated_against_the_database(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    a = save_capture(root, "A", 9001, player_team=0, mtime=1)
    b = save_capture(root, "B", 2002, player_team=1, mtime=2)
    env = preview_environment(monkeypatch, root, db_league=9001)
    env.fetch.side_effect = AssertionError("fetch must not run for a refused snapshot")

    assert run_preview(snapshot=b) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "league 2002" in err

    env.fetch.side_effect = lambda season, refresh=True: fetched_files()
    assert run_preview(snapshot=a) == 0
    assert f"NFL mirror preview from {a}" in capsys.readouterr().out


def test_f2_an_unreadable_explicit_snapshot_refuses(monkeypatch, tmp_path, capsys):
    root = tmp_path / "captures"
    save_capture(root, "A", 9001)
    env = preview_environment(monkeypatch, root, db_league=9001)
    env.fetch.side_effect = AssertionError("fetch must not run for an unreadable snapshot")

    assert run_preview(snapshot=tmp_path / "missing.json") == 2
    assert "cannot read snapshot" in capsys.readouterr().err


# ------------------------------------------------------------------------- F3: next supported game week ---
@pytest.mark.parametrize("last_week,expected", [
    (20, "next real week: Mu week 21 -> nflverse week 21, mode CATCH_UP"),
    (21, "next real week: Mu week 23 -> nflverse week 22, mode CATCH_UP"),
    (22, "next real week: Mu week 23 -> nflverse week 22, mode CATCH_UP"),
    (23, "next real week: none (past the Super Bowl; no real NFL game after Mu week 23)"),
    (10, "next real week: Mu week 11 -> nflverse week 11, mode CATCH_UP"),
])
def test_f3_status_skips_the_pro_bowl_internal_week(monkeypatch, last_week, expected):
    real_week = 22 if last_week == 22 else real_state.real_week_for(last_week)
    conn = signed((True, SEASON, None, None, "OFF", None),
                  (SEASON, last_week, real_week, "CATCH_UP", "WRITTEN"))
    monkeypatch.setattr(mirror_main, "_cached_games_rows",
                        lambda season: [{"season": str(SEASON), "week": str(week),
                                         "home_score": "20", "away_score": "17"} for week in (11, 21, 22)])
    lines, code = mirror_main.status_report(conn.cursor())
    assert code == 0
    assert lines[-1] == expected
    if last_week == 23:
        assert "Mu week 24" not in lines[-1]


def test_f3_a_pending_super_bowl_is_the_next_week_in_live_mode(monkeypatch):
    conn = signed((True, SEASON, None, None, "OFF", None),
                  (SEASON, 21, 21, "CATCH_UP", "WRITTEN"))
    monkeypatch.setattr(mirror_main, "_cached_games_rows",
                        lambda season: [{"season": str(SEASON), "week": "22", "home_score": "", "away_score": ""}])
    lines, _ = mirror_main.status_report(conn.cursor())
    assert lines[-1] == "next real week: Mu week 23 -> nflverse week 22, mode LIVE"


# ------------------------------------------------------- F1/F2 scratch PostgreSQL (mu_scratch_u19_test) ---
SCRATCH_DB = "mu_scratch_u19_test"
SCRATCH_ADMIN = ["docker", "exec", "madden-db", "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-c"]
SCRATCH_SCHEMA = """
CREATE TABLE league_state (
    id INT PRIMARY KEY DEFAULT 1,
    current_season INT NOT NULL DEFAULT 2024,
    current_week INT NOT NULL DEFAULT 1,
    madden_league_id BIGINT,
    shape_season INT
);
INSERT INTO league_state (id) VALUES (1);
CREATE TABLE nfl_mirror_settings (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    enabled BOOLEAN NOT NULL DEFAULT false,
    season INT NOT NULL DEFAULT 2026,
    last_week INT,
    madden_injuries_field JSONB,
    injuries_state VARCHAR(8) NOT NULL DEFAULT 'ON' CHECK (injuries_state IN ('ON', 'OFF')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO nfl_mirror_settings (id) VALUES (1);
CREATE TABLE players (id SERIAL PRIMARY KEY, player_name VARCHAR(255) NOT NULL, madden_pgid INT);
CREATE TABLE nfl_players (gsis_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, position TEXT, birth_date TEXT,
                          college TEXT);
CREATE TABLE nfl_player_links (
    player_id INT PRIMARY KEY, gsis_id TEXT NOT NULL UNIQUE,
    method TEXT NOT NULL CHECK (method IN ('EXACT', 'ESPN', 'ALIAS', 'FUZZY', 'OVERRIDE', 'CREATED')),
    score NUMERIC, evidence JSONB NOT NULL DEFAULT '{}', linked_at TIMESTAMPTZ DEFAULT now()
);
"""


def _scratch_psql(sql):
    subprocess.run(SCRATCH_ADMIN + [sql], check=True, capture_output=True, text=True, timeout=60)


@pytest.fixture
def scratch_connector(monkeypatch):
    """The real psycopg connect for a DSN whose dbname the test firewall's hand-split parser cannot read (repeated
    or quoted). It asserts libpq resolves the DSN to the scratch database before bypassing that parser."""
    from psycopg.conninfo import conninfo_to_dict
    from tests.conftest import _ORIG_PSYCOPG_CONNECT

    def connect(dsn):
        target = conninfo_to_dict(dsn).get("dbname")
        assert target == SCRATCH_DB, f"refusing a non-scratch connection to {target!r}"
        return _ORIG_PSYCOPG_CONNECT(dsn)

    monkeypatch.setattr(mirror_main, "_connect", connect)
    return connect


@pytest.fixture(scope="module")
def scratch_pg():
    """A scratch `mu_scratch_u19_test` built for these cases only and dropped afterwards."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL='host=localhost dbname=mu_scratch_u19_test "
                    "user=postgres password=postgres' to run the PostgreSQL cases")
    from psycopg.conninfo import conninfo_to_dict
    try:
        name = conninfo_to_dict(dsn).get("dbname")
    except Exception as e:      # noqa: BLE001
        pytest.skip(f"TEST_DATABASE_URL is not a usable conninfo string: {e}")
    if name != SCRATCH_DB or not name.startswith("mu_scratch_"):
        pytest.skip(f"TEST_DATABASE_URL must name the scratch database {SCRATCH_DB}, not {name!r}")
    created = False
    try:
        try:
            _scratch_psql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            _scratch_psql(f'CREATE DATABASE "{name}"')
            created = True
        except (OSError, subprocess.SubprocessError) as e:
            pytest.skip(f"no scratch PostgreSQL (docker exec madden-db psql): {e}")
        import psycopg
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(SCRATCH_SCHEMA)
        except Exception as e:      # noqa: BLE001
            pytest.skip(f"no scratch PostgreSQL at {dsn}: {e}")
        yield dsn
    finally:
        if created:
            _scratch_psql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _pg_reset(dsn, *, league_id=9001, season=SEASON, week=6):
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE nfl_mirror_settings, players, nfl_player_links, nfl_players RESTART IDENTITY")
        conn.execute("INSERT INTO nfl_mirror_settings (id) VALUES (1)")
        conn.execute("UPDATE league_state SET current_season = %s, current_week = %s, madden_league_id = %s "
                     "WHERE id = 1", (season, week, league_id))


def _pg_settings(dsn):
    import psycopg
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT enabled, last_week, updated_at FROM nfl_mirror_settings WHERE id = 1").fetchone()


def _pg_add_link(dsn, gsis="00-X", pgid=100):
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        player_id = conn.execute("INSERT INTO players (player_name, madden_pgid) VALUES ('Linked Man', %s) "
                                 "RETURNING id", (pgid,)).fetchone()[0]
        conn.execute("INSERT INTO nfl_player_links (player_id, gsis_id, method) VALUES (%s, %s, 'EXACT')",
                     (player_id, gsis))


def test_f1_real_pg_repeated_dbname_refuses_and_the_correct_name_updates(scratch_pg, scratch_connector):
    _pg_reset(scratch_pg)
    dsn = (f"host=localhost dbname=confirmed_name dbname={SCRATCH_DB} user=postgres password=postgres")
    before = _pg_settings(scratch_pg)

    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", "confirmed_name"]) == 2
    assert _pg_settings(scratch_pg) == before, "a mismatch must leave enabled, last_week and updated_at untouched"

    assert mirror_main.main(["enable", "--last-week", "10", "--dsn", dsn, "--confirm", SCRATCH_DB]) == 0
    enabled, last_week, updated = _pg_settings(scratch_pg)
    assert enabled is True and last_week == 10 and updated != before[2]

    before = _pg_settings(scratch_pg)
    assert mirror_main.main(["disable", "--dsn", dsn, "--confirm", "confirmed_name"]) == 2
    assert _pg_settings(scratch_pg) == before
    assert mirror_main.main(["disable", "--dsn", dsn, "--confirm", SCRATCH_DB]) == 0
    assert _pg_settings(scratch_pg)[0] is False


@pytest.mark.parametrize("dsn", [
    "postgresql://postgres:postgres@localhost/wrong?dbname=mu_scratch_u19_test",
    "host=localhost dbname='mu_scratch_u19_test' user=postgres password=postgres",
    "host=localhost user=postgres password=postgres dbname='mu_scratch_u19_test'",
    "host=localhost dbname='mu_scratch_u19_test' user=postgres password=postgres options='-c application_name=u19'",
])
def test_f1_real_pg_url_and_quoted_dsns_use_the_opened_database(scratch_pg, scratch_connector, dsn):
    _pg_reset(scratch_pg)
    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", SCRATCH_DB]) == 0
    assert _pg_settings(scratch_pg)[0] is True


def test_f1_real_pg_url_query_override_still_needs_the_right_confirm(scratch_pg):
    _pg_reset(scratch_pg)
    dsn = f"postgresql://postgres:postgres@localhost/{SCRATCH_DB}?dbname={SCRATCH_DB}"
    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", "wrong"]) == 2
    assert _pg_settings(scratch_pg)[0] is False
    assert mirror_main.main(["enable", "--dsn", dsn, "--confirm", SCRATCH_DB]) == 0
    assert _pg_settings(scratch_pg)[0] is True


def test_f2_real_pg_binds_the_capture_to_the_league_state_franchise(monkeypatch, tmp_path, scratch_pg, capsys):
    _pg_reset(scratch_pg, league_id=9001, season=SEASON, week=6)
    _pg_add_link(scratch_pg)
    root = tmp_path / "captures"
    a = save_capture(root, "A", 9001, player_team=0, mtime=1)
    b = save_capture(root, "B", 2002, player_team=1, mtime=2)
    monkeypatch.setattr(mirror_main, "CAPTURES", str(root))
    fetch = mock.Mock(return_value=fetched_files())
    monkeypatch.setattr(mirror_main, "_fetch_week", fetch)
    monkeypatch.setattr(mirror_main, "_read_rows", lambda fetched: [])
    monkeypatch.setattr(mirror_main, "_build_week", lambda *args, **kwargs: linked_real_week())
    monkeypatch.setattr(mirror_main, "_read_trades", lambda: [])
    monkeypatch.setattr(mirror_main, "league_cap", lambda snapshot: 255_400_000)

    # Real psycopg connection and real store.links against the scratch database: league A's link is used.
    assert mirror_main.main(["preview", "--week", "6", "--dsn", scratch_pg]) == 0
    out = capsys.readouterr().out
    assert f"NFL mirror preview from {a}" in out and "roster ops: 0" in out
    assert str(b) not in out

    fetch.reset_mock()
    assert mirror_main.main(["preview", "--week", "6", "--snapshot", str(b), "--dsn", scratch_pg]) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "league 2002" in err
    fetch.assert_not_called()
