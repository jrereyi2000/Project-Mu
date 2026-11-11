"""Offline tests for the NFL Weekly Mirror's watcher gating and CLI (NFL_WEEKLY_MIRROR_PLAN.md §2.10, §2.11).

A mirrored week skips the GM trade proposals, the newsroom articles and the press conferences, and the week-done
notification carries ``mirror: N moves (M unconfirmed switches)``. A normal week keeps all three. The CLI's five
commands run through ``main(argv)`` against a fake cursor: no database and no network.
"""
import json
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
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def signed(*runs, tables=True):
    """A records-free fake cursor whose to_regclass answers are `tables` and whose other queries return `runs`."""
    class SignedCursor(ScriptedCursor):
        def execute(self, sql, args=None):
            norm = " ".join(sql.split())
            self.sql.append((norm, args))
            if norm.startswith("SELECT to_regclass"):
                self._ret = (tables,)
            elif "nfl_player_links l JOIN players" in norm:
                self._ret = None
                self._all = []
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

    monkeypatch.setattr(mirror_main, "_newest_snapshot", lambda: str(snap))
    monkeypatch.setattr(mirror_main, "_fetch_week", lambda season, refresh=True: dict(files))
    monkeypatch.setattr(mirror_main, "_read_rows", lambda fetched: [])
    monkeypatch.setattr(mirror_main, "_build_week", build)
    monkeypatch.setattr(mirror_main, "_plan_rosters", lambda *a, **k: roster)
    monkeypatch.setattr(mirror_main, "_read_trades", lambda: [])
    monkeypatch.setattr(mirror_main, "team_codes", lambda snapshot, season: {})
    monkeypatch.setattr(mirror_main, "league_cap", lambda snapshot: 255_400_000)
    conn = signed((True, SEASON, None, None, "ON", None))      # read_settings in settings_season
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
    args = parser.parse_args(["trades", "--week", "3", "--dsn", "x"])
    assert args.func is mirror_main.cmd_trades and args.week == 3
    args = parser.parse_args(["enable", "--last-week", "9", "--confirm", "db", "--dsn", "x"])
    assert args.func is mirror_main.cmd_enable and args.last_week == 9
    args = parser.parse_args(["disable", "--confirm", "db", "--dsn", "x"])
    assert args.func is mirror_main.cmd_disable
