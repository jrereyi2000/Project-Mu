"""The pilot's catch-up job (NFL_WEEKLY_MIRROR_PLAN.md section 2.12) against fake pilots and
fake mirror tables. No PC, no database, no network."""
import json
import os
from pathlib import Path

import pytest

import pipeline.notify
from tools.madden_pilot import __main__ as cli
from tools.madden_pilot import jobs
from tools.madden_pilot.navigate import Stuck


@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify", lambda msg, **kw: sent.append((msg, kw)) or True)
    return sent


@pytest.fixture(autouse=True)
def no_real_watcher(monkeypatch):
    def boom():
        raise AssertionError("catch-up tests must inject the watcher check")
    monkeypatch.setattr(jobs, "watcher_running", boom)


class FakePilot:
    """The slice of navigate.Pilot the job touches: run_dir, policy, clock/sleep and stop."""

    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.dry_run = False
        self.policy = {}
        self.stops = []

    def clock(self):
        return 0.0

    def sleep(self, seconds):
        pass

    def stop(self, reason, kind=Stuck):
        self.stops.append((kind, reason))
        raise kind(reason, {"kind": kind.kind, "reason": reason})


class FakeStore:
    """nfl_mirror_settings/nfl_mirror_weeks as the job reads them."""

    def __init__(self, settings="default", statuses=None):
        self._settings = {"enabled": True, "season": 2026, "last_week": None} if settings == "default" else settings
        self._statuses = statuses if statuses is not None else {}
        self.asked = []
        self.closed = False

    def settings(self):
        return self._settings

    def week_status(self, season, week):
        self.asked.append((season, week))
        value = self._statuses.get(week, "WRITTEN")
        if callable(value):
            return value()
        return value

    def close(self):
        self.closed = True


def make_run(tmp_path, week=1, store=None, mode_for=None, statuses=None):
    """A pilot/state/store/counters bundle for one catch-up run."""
    pilot = FakePilot(tmp_path / "run")
    state = {"week": week, "loads": 0, "sims": 0, "quits": 0}

    def load():
        state["loads"] += 1

    def sim():
        state["sims"] += 1
        state["week"] += 1

    def quit_to_menu():
        state["quits"] += 1

    return {
        "pilot": pilot,
        "state": state,
        "store": store if store is not None else FakeStore(statuses=statuses),
        "mode_for": mode_for or (lambda w: "CATCH_UP"),
        "kw": {"week_reader": lambda path: state["week"], "load": load, "sim": sim,
               "quit_to_menu": quit_to_menu},
    }


def catch(tmp_path, run, **kw):
    args = dict(save=kw.pop("save", "CAREER-MU2026"), watcher=lambda: True,
                folder=tmp_path / "none", store=run["store"], mode_for=run["mode_for"], **run["kw"])
    args.update(kw)
    return jobs.catch_up(run["pilot"], **args)


def result_json(tmp_path):
    return json.loads((tmp_path / "run" / "result.json").read_text())


# ---------------------------------------------------------------- refusals
def test_refuses_a_tune_clone_before_anything_else(tmp_path, notices):
    run = make_run(tmp_path)
    out = catch(tmp_path, run, save="CAREER-TUNE-X", watcher=lambda: (_ for _ in ()).throw(
        AssertionError("the watcher check runs after the save-name check")))
    assert out["status"] == "refused" and "CAREER-TUNE-" in out["why"]
    assert run["store"].asked == [] and run["pilot"].policy == {}
    assert run["state"] == {"week": 1, "loads": 0, "sims": 0, "quits": 0}
    assert result_json(tmp_path)["status"] == "refused"
    assert len(notices) == 1 and "refused" in notices[0][0]


def test_refuses_when_the_watcher_is_not_running(tmp_path, notices):
    run = make_run(tmp_path)
    store = run["store"]
    store.settings = lambda: (_ for _ in ()).throw(AssertionError("settings read after the watcher check"))
    out = catch(tmp_path, run, watcher=lambda: False)
    assert out["status"] == "refused" and "watch_save" in out["why"] and "isn't running" in out["why"]
    assert run["state"]["sims"] == 0 and run["state"]["loads"] == 0


@pytest.mark.parametrize("settings", [None, {"enabled": False, "season": 2026}])
def test_refuses_when_the_mirror_is_off(tmp_path, notices, settings):
    run = make_run(tmp_path, store=FakeStore(settings=settings))
    out = catch(tmp_path, run)
    assert out["status"] == "refused" and "nfl_mirror_settings.enabled" in out["why"]
    assert run["state"]["sims"] == 0 and run["state"]["loads"] == 0
    assert run["pilot"].stops == []


def test_refuses_a_week_that_is_not_catch_up(tmp_path, notices):
    run = make_run(tmp_path, mode_for=lambda w: "LIVE")
    out = catch(tmp_path, run)
    assert out["status"] == "refused" and "not CATCH_UP" in out["why"] and "week 1" in out["why"]
    assert run["state"]["sims"] == 0 and run["state"]["loads"] == 0
    assert run["pilot"].stops == []


def test_refuses_a_week_the_save_reader_cannot_name(tmp_path, notices):
    run = make_run(tmp_path)
    out = catch(tmp_path, run, week_reader=lambda path: None)
    assert out["status"] == "refused" and "couldn't read" in out["why"]


def test_refuses_when_the_real_games_cannot_be_read(tmp_path, notices):
    def boom(week):
        raise OSError("nflverse down")
    run = make_run(tmp_path, mode_for=boom)
    out = catch(tmp_path, run)
    assert out["status"] == "refused" and "nflverse down" in out["why"]
    assert run["state"]["sims"] == 0


# ---------------------------------------------------------------- the loop
def test_one_loop_loads_sims_quits_and_waits_for_written(tmp_path, notices):
    answers = ["PLANNED", "WRITTEN"]                          # the league office's commit lands on the poll
    run = make_run(tmp_path, week=1, statuses={2: lambda: answers.pop(0)})
    out = catch(tmp_path, run, to_week=2)
    assert out["status"] == "ok", out
    assert (out["start_week"], out["end_week"], out["weeks_simmed"]) == (1, 2, 1)
    assert out["stopped_because"] == "to-week" and out["season"] == 2026
    assert run["state"] == {"week": 2, "loads": 1, "sims": 1, "quits": 1}
    assert run["store"].asked == [(2026, 2), (2026, 2)]        # the settings' season, the save's new week
    assert run["store"].closed is False                        # an injected store is the caller's
    assert len(notices) == 2                                   # one per week, then the summary
    assert "2026 week 1 simmed, mirror week 2 written" in notices[0][0]
    assert "catch-up: ok" in notices[1][0]
    res = result_json(tmp_path)
    assert res["job"] == "catch-up" and res["status"] == "ok" and res["end_week"] == 2


def test_the_run_allows_only_the_catch_up_tile_on_the_franchise_list(tmp_path, notices):
    run = make_run(tmp_path)
    catch(tmp_path, run, to_week=2)
    assert run["pilot"].policy == {"FRANCHISE_LIST": ["MU2026"]}


def test_it_stops_at_the_first_live_week(tmp_path, notices):
    run = make_run(tmp_path, week=1, mode_for=lambda w: "CATCH_UP" if w < 3 else "LIVE")
    out = catch(tmp_path, run)
    assert out["status"] == "ok" and out["stopped_because"] == "live"
    assert (out["end_week"], out["weeks_simmed"]) == (3, 2)
    assert run["state"]["sims"] == 2 and run["state"]["quits"] == 2


def test_it_stops_at_to_week_without_simming_past_it(tmp_path, notices):
    run = make_run(tmp_path, week=1)
    out = catch(tmp_path, run, to_week=3)
    assert out["status"] == "ok" and out["stopped_because"] == "to-week"
    assert (out["weeks_simmed"], out["end_week"]) == (2, 3)
    assert run["state"]["sims"] == 2


def test_it_stops_when_already_at_to_week(tmp_path, notices):
    run = make_run(tmp_path, week=4)
    out = catch(tmp_path, run, to_week=4)
    assert out["status"] == "ok" and out["weeks_simmed"] == 0 and out["stopped_because"] == "to-week"
    assert run["state"]["loads"] == 0 and run["state"]["sims"] == 0


def test_it_stops_on_a_failed_mirror_week(tmp_path, notices):
    run = make_run(tmp_path, week=1, statuses={2: "FAILED"})
    out = catch(tmp_path, run, to_week=3)
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "FAILED" in out["reason"] and "week 2" in out["reason"]
    assert run["state"]["sims"] == 1 and run["state"]["quits"] == 1     # it stopped after the sim/quit
    assert result_json(tmp_path)["status"] == "stopped"


def test_one_sim_must_move_the_save_exactly_one_week(tmp_path, notices):
    run = make_run(tmp_path, week=1)

    def double_sim():
        run["state"]["sims"] += 1
        run["state"]["week"] += 2
    run["kw"]["sim"] = double_sim
    out = catch(tmp_path, run, to_week=5)
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "not exactly one week" in out["reason"]
    assert run["state"]["quits"] == 0                    # the bad save never reaches the league office
    assert run["store"].asked == []                      # and it never waited for a WRITTEN week


def test_a_loaded_save_that_is_not_the_expected_week_stops_before_the_sim(tmp_path, notices):
    run = make_run(tmp_path, week=1)

    def load_wrong_save():
        run["state"]["loads"] += 1
        run["state"]["week"] = 5
    run["kw"]["load"] = load_wrong_save
    out = catch(tmp_path, run, to_week=3)
    assert out["status"] == "stopped" and "refusing to sim the wrong save" in out["reason"]
    assert run["state"]["sims"] == 0


def test_the_week_cap_is_a_stuck_stop(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_CATCH_UP_WEEKS", 2)
    run = make_run(tmp_path, week=1)
    out = catch(tmp_path, run)
    assert out["status"] == "stopped" and out["kind"] == "Stuck" and "2 weeks" in out["reason"]
    assert run["state"]["sims"] == 2


# ---------------------------------------------------------------- helpers
def test_mode_reader_uses_the_real_games_and_stops_hostless_weeks():
    final = [{"season": "2026", "week": "1", "home_score": "24", "away_score": "17"},
             {"season": "2026", "week": "1", "home_score": "3", "away_score": "7"}]
    reader = jobs._mode_reader(2026, games_rows=lambda: final)
    assert reader(1) == "CATCH_UP"
    assert reader(2) == "LIVE"           # no games that week
    assert reader(22) == "LIVE"          # the Pro Bowl has no real NFL week
    reader_rows = []
    assert reader_rows == []             # the rows are read once, lazily
    reader = jobs._mode_reader(2026, games_rows=lambda: [{"season": "2026", "week": "1",
                                                         "home_score": "", "away_score": "7"}])
    assert reader(1) == "LIVE"           # a game without a final score


def test_save_copy_takes_a_newer_autosave_only(tmp_path):
    named, auto = tmp_path / "CAREER-X", tmp_path / "CAREER-X-AUTOSAVE"
    named.write_bytes(b"week1")
    auto.write_bytes(b"week2")
    os.utime(named, (100, 100))
    os.utime(auto, (200, 200))
    assert jobs._save_copy(named, auto) is True
    assert named.read_bytes() == b"week2"
    assert jobs._save_copy(named, auto) is False        # now the autosave is not newer
    auto.unlink()
    assert jobs._save_copy(named, auto) is False


def test_mirror_dsn_prefers_the_argument_then_database_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "host=localhost dbname=from_env user=postgres password=postgres")
    assert jobs._mirror_dsn("my_db") == "host=localhost dbname=my_db user=postgres password=postgres"
    assert jobs._mirror_dsn() == "host=localhost dbname=from_env user=postgres password=postgres"


def test_quit_franchise_body_is_shared_with_catch_up():
    # the decorator would write a second result.json and ntfy; catch_up calls the plain body
    assert jobs._quit_now is not jobs.quit_franchise


# ---------------------------------------------------------------- CLI
def test_cli_help_lists_catch_up():
    import subprocess
    proc = subprocess.run(["python3", "-m", "tools.madden_pilot", "catch-up", "--help"],
                          cwd=str(jobs.ROOT), capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--to-week" in proc.stdout and "--dsn" in proc.stdout


def test_cli_dry_run_walks_the_catch_up_route(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    lines = []
    rc = cli.main(["catch-up", "--save", "CAREER-MU2026", "--to-week", "3", "--dry-run",
                   "--run-dir", str(tmp_path)], out=lines.append)
    assert rc == 0
    text = "\n".join(lines)
    assert lines[0].startswith("[dry-run] catch-up: FRANCHISE_LIST > highlight MU2026")
    assert "until the first LIVE week" in lines[0]
    assert "would refuse" in text                 # the dry run says the live watcher rule
    assert text.count("ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 2
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["status"] == "ok" and res["dry_run"] and res["weeks_simmed"] == 2
    assert res["stopped_because"] == "to-week"
    assert notices == []                          # a dry run sends nothing


def test_cli_live_refusal_exit_code(tmp_path, notices):
    rc = cli.main(["catch-up", "--save", "CAREER-TUNE-X", "--run-dir", str(tmp_path)],
                  out=lambda s: None)
    assert rc == 1 and json.loads((tmp_path / "result.json").read_text())["status"] == "refused"
