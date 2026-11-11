"""engines.tuning.season_loop (CLOSE_OUT_CHECKLIST P2-05, P2-10) and the walks it drives.
Offline: every Madden-touching step goes through a fake runner; no PC, no database."""
import json
import os
import types

import pytest

from engines.tuning import season_loop as L


@pytest.fixture(autouse=True)
def _db_present(monkeypatch):
    """The resume check asks the server; the unit tests run without a tuning database."""
    monkeypatch.setattr(L, "db_exists", lambda db: True)


def _snapshot():
    box = {"OFFPASSYARDS": 240, "OFFRUSHYARDS": 110, "SACKS": 2, "GIVEAWAYS": 1}
    games = [{"franchise_year": 0, "week_type": "RegularSeason", "status": "HomeWon", "home_team_index": h,
              "away_team_index": a, "home_score": 24, "away_score": 20, "home_team_stats": box,
              "away_team_stats": box} for h, a in ((0, 1), (1, 0))]
    return {"season": {"calendar_year": 2026, "franchise_year": 0}, "games": games, "players": []}


TARGETS = {"meta": {"seasons": [2015, 2025]},
           "stats": {"league.points_per_team_team": {"mean": 22.0, "lo": 21.0, "hi": 23.0, "n": 11},
                     "team.total_yards.p50": {"mean": 6000, "lo": 5600, "hi": 6400, "n": 11}}}


def _week(n):
    return {"status": "ok", "to": {"year": 2026, "phase": "WEEK", "week": n}}


def _round(phase):
    return {"status": "ok", "to": {"year": 2026, "phase": phase, "week": None}}


STAFF = {"status": "ok", "to": {"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 0, "stop": "STAFF"}}
STOPPED = {"status": "stopped", "kind": "Unknown", "reason": "new screen", "screenshot": "/runs/week_03/stop.png"}


def season_stops():
    """What a default week-by-week season sees: weeks 2-18, the four playoff rounds, then STAFF."""
    return [_week(n) for n in range(2, 19)] + [_round(p) for p in ("WILDCARD", "DIVISIONAL", "CONFERENCE",
                                                                   "SUPER BOWL")] + [STAFF]


def _runner(calls, stops=None, advance_status="ok", fail=None):
    """A fake subprocess.run: records every command, writes the snapshot for node, and prints the
    pilot's result JSON for advance."""
    stops = list(stops or [])

    def runner(cmd, **kw):
        calls.append([str(c) for c in cmd])
        joined = " ".join(str(c) for c in cmd)
        if fail and fail in joined:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="refused")
        if cmd[0] == "node":
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w") as fh:
                json.dump(_snapshot(), fh)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "quit" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"status": "ok", "screen": "FRANCHISE_LIST"}),
                                         stderr="")
        if "advance" in cmd:
            stop = stops.pop(0) if stops else {"status": "ok",
                                               "to": {"year": 2027, "phase": "PRESEASON", "week": None}}
            if advance_status != "ok" and stop.get("status") == "ok":
                stop = {"status": "stopped", "kind": "Unknown", "reason": "new screen",
                        "screenshot": "/runs/steps/0009_UNKNOWN.png"}
            return types.SimpleNamespace(returncode=0 if stop.get("status") == "ok" else 1,
                                         stdout=json.dumps(stop), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    return runner


def _targets_file(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(TARGETS))
    return str(path)


# ---------------------------------------------------------------- naming
def test_db_dsn_and_save_names():
    assert L.db_for("P2C") == "madden_tune_p2c"
    assert L.save_for("P2C") == "CAREER-TUNE-P2C"
    assert "dbname=madden_tune_p2c" in L.dsn_for("P2C")
    assert L.db_for("a-b c") == "madden_tune_a_b_c"


def test_in_season_covers_the_weeks_and_the_playoff_rounds():
    assert L.in_season("WEEK") and L.in_season("wildcard") and L.in_season("SUPER BOWL")
    for phase in ("OFFSEASON", "PRESEASON", "STAFF", None, ""):
        assert not L.in_season(phase)


@pytest.mark.parametrize("name", ["", "has space", "../x", "-lead"])
def test_bad_run_names_are_refused(name, tmp_path):
    calls = []
    with pytest.raises(L.Refused):
        L.run(name, runner=_runner(calls), python="py", seasons_dir=str(tmp_path))
    assert calls == []


def test_a_missing_targets_file_is_refused_before_any_work(tmp_path):
    calls = []
    with pytest.raises(L.Refused, match="no real-NFL targets"):
        L.run("x", runner=_runner(calls), python="py", seasons_dir=str(tmp_path),
              targets_path=str(tmp_path / "nope.json"))
    assert calls == []


def test_a_tune_save_is_refused_as_the_base(tmp_path):
    calls = []
    with pytest.raises(L.Refused, match="base save"):
        L.run("x", base_save="CAREER-TUNE-X", runner=_runner(calls), python="py", seasons_dir=str(tmp_path))
    assert calls == []


# ---------------------------------------------------------------- the default: week by week
def test_a_season_is_walked_one_week_at_a_time(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False)
    advances = [c for c in calls if "advance" in c]
    ingests = [c for c in calls if "pipeline.watch_save" in c]
    # 17 landings on weeks 2-18, four playoff rounds and the STAFF landing
    assert len(advances) == 22 and len(out["weeks"]) == 22
    assert len(ingests) == 23                      # the seed, one per in-season stop, the season's last stop
    assert ingests[0].index("--auto-init") > 0      # the clone seeds the database
    assert all("--auto-init" not in c for c in ingests[1:])
    assert out["weeks"][0]["to"]["week"] == 2 and out["weeks"][-1]["to"]["stop"] == "STAFF"
    assert out["season_complete"] is True
    assert out["steps"] == ["clone", "ingest-clone", "season", "ingest-playoffs"]
    # every advance is preceded by a release, so the game is on CURRENT FRANCHISES when it loads
    quits = [i for i, c in enumerate(calls) if "quit" in c]
    first_advance = next(i for i, c in enumerate(calls) if "advance" in c)
    assert quits and quits[0] < first_advance and len(quits) == 23


def test_each_week_ingests_the_league_office_write_and_records_its_time(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False)
    # the walk ingests every landing inside the season, and the last stop is ingested once, after the walk
    stops = [w for w in out["weeks"] if w.get("ingest_s") is not None]
    assert len(stops) == 21 and all(w["advance_s"] >= 0 for w in out["weeks"])
    assert out["weeks"][-1].get("ingest_s") is None          # STAFF is ingested by the caller
    progress = json.load(open(os.path.join(out["run_dir"], "progress.json")))
    assert len(progress["stops"]) == 22
    assert progress["stops"][0]["advance_s"] is not None and progress["stops"][0]["ingest_s"] is not None
    assert progress["season_complete"] is True and progress["steps"] == out["steps"]


def test_the_playoff_rounds_are_ingested_between_the_regular_weeks_and_the_season_end(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", targets_path=_targets_file(tmp_path), report_fn=lambda s: None,
                offseason=False)
    phases = [(w.get("to") or {}).get("phase") for w in out["weeks"]]
    assert phases[-5:] == ["WILDCARD", "DIVISIONAL", "CONFERENCE", "SUPER BOWL", "OFFSEASON"]
    # the snapshot is taken after the last advance and before the stop's ingest: the report must
    # see the season's games, and the league office's write must not touch the save before it
    last_advance = max(i for i, c in enumerate(calls) if "advance" in c)
    snap = next(i for i, c in enumerate(calls) if c[0] == "node")
    next_ingest = next(i for i, c in enumerate(calls) if i > snap and "pipeline.watch_save" in c)
    assert last_advance < snap < next_ingest


def test_a_stopped_advance_ends_the_season_without_a_report_or_an_offseason(tmp_path):
    calls = []
    stops = [_week(2), _week(3), STOPPED]
    out = L.run("unit", runner=_runner(calls, stops), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=True)
    assert out["season_complete"] is False and len(out["weeks"]) == 3
    assert out["weeks"][-1]["screenshot"].endswith("stop.png")
    assert out["steps"] == ["clone", "ingest-clone", "season"]
    assert out["stops"] == []
    run_json = json.load(open(os.path.join(out["run_dir"], "run.json")))
    assert run_json["season_complete"] is False
    assert sum(1 for c in calls if "pipeline.watch_save" in c) == 3      # the seed and weeks 2-3


def test_the_season_walk_is_capped(tmp_path):
    calls = []
    with pytest.raises(L.StepFailed, match="didn't reach its end"):
        L.run("unit", runner=_runner(calls, [_week(n) for n in range(2, 40)]), python="py",
              seasons_dir=str(tmp_path / "s"), saves_dir="/saves", do_report=False, max_weeks=3)


# ---------------------------------------------------------------- the one-shot sim
def test_fast_season_keeps_the_one_shot_sim(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False, fast_season=True)
    assert not any("advance" in c for c in calls)
    sims = [c for c in calls if "sim-season" in c]
    assert len(sims) == 2
    assert "regular-season-end" in sims[0] and "--no-quit" in sims[0]      # the postseason continues in place
    assert "playoffs-end" in sims[1] and "--franchise" in sims[1]
    assert out["weeks"] == [] and out["season_complete"] is True
    assert out["steps"] == ["clone", "ingest-clone", "regular-season", "playoffs", "ingest-playoffs"]


def test_the_week_one_clone_seeds_the_database_before_the_season(tmp_path):
    # The neutral progression bootstrap is only taken from a neutral save, and a played season's
    # save has drifted ConfidenceRatings: seeding at the season's end left madden_tune_p2f and p2g
    # with no progression state, and the league office stopped OFFSEASON_START with "explicit
    # neutral bootstrap is required" (2026-11-10). The clone's week-1 save is still neutral.
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False)
    ingests = [i for i, c in enumerate(calls) if "pipeline.watch_save" in c]
    clone = next(i for i, c in enumerate(calls) if "clone-save" in c)
    first_advance = next(i for i, c in enumerate(calls) if "advance" in c)
    assert clone < ingests[0] < first_advance             # seeded before any week is played
    assert "--auto-init" in calls[ingests[0]]
    assert out["steps"][:3] == ["clone", "ingest-clone", "season"]


def test_the_ingest_runs_with_the_franchise_closed(tmp_path):
    calls = []
    envs = []
    inner = _runner(calls, season_stops())

    def runner(cmd, **kw):
        if "pipeline.watch_save" in " ".join(cmd):
            envs.append(kw.get("env") or {})
        return inner(cmd, **kw)

    L.run("unit", runner=runner, python="py", seasons_dir=str(tmp_path / "s"), saves_dir="/saves",
          do_report=False, offseason=False, environ={"PATH": "/bin"})
    assert len(envs) == 23 and all(e["LEAGUE_OFFICE_FRANCHISE_CLOSED"] == "1" for e in envs)
    assert envs[0]["PATH"] == "/bin"


# ---------------------------------------------------------------- resume
def _walked(tmp_path, stops=None):
    """A finished week-by-week run's directory, for the resume tests."""
    calls = []
    out = L.run("unit", runner=_runner(calls, stops or season_stops()), python="py",
                seasons_dir=str(tmp_path / "s"), saves_dir="/saves", do_report=False, offseason=False)
    return out["run_dir"]


def test_resume_restarts_the_week_walk_where_the_last_run_stopped(tmp_path):
    calls = []
    L.run("unit", runner=_runner(calls, [_week(2), _week(3), STOPPED]), python="py",
          seasons_dir=str(tmp_path / "s"), saves_dir="/saves", do_report=False, offseason=False)
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False, resume=True)
    assert not any("clone-save" in c for c in calls)
    assert not any("--auto-init" in c for c in calls)
    assert any("advance" in c for c in calls)              # it walks from wherever the save is
    assert out["steps"][:3] == ["clone", "ingest-clone", "season"]   # the earlier run's steps are kept
    assert len(out["weeks"]) == 22 and out["season_complete"] is True


def test_a_resume_of_a_finished_season_does_not_walk_it_again(tmp_path):
    _walked(tmp_path)
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False, resume=True)
    assert not any("advance" in c for c in calls) and not any("sim-season" in c for c in calls)
    assert not any("clone-save" in c for c in calls)
    assert out["weeks"] == [] and out["season_complete"] is True


def test_a_resume_without_a_progress_record_is_refused(tmp_path):
    calls = []
    with pytest.raises(L.Refused, match="no progress record"):
        L.run("unit", runner=_runner(calls), python="py", seasons_dir=str(tmp_path / "s"), resume=True)
    assert calls == [] or not any("advance" in c for c in calls)


def test_a_resume_without_its_database_is_refused(tmp_path, monkeypatch):
    # a resumed season has no week-1 clone to seed from, so without the database the bootstrap
    # would be taken from a played season's save and silently skipped
    monkeypatch.setattr(L, "db_exists", lambda db: False)
    calls = []
    with pytest.raises(L.Refused, match="no madden_tune_unit to resume"):
        L.run("unit", runner=_runner(calls), python="py", seasons_dir=str(tmp_path), resume=True)
    assert calls == []


# ---------------------------------------------------------------- the offseason
def test_the_offseason_loop_ingests_after_every_stop_and_stops_at_preseason(tmp_path):
    calls = []
    # a season's last stop, then the eight offseason stops, the preseason weeks and the cut day, then 2027 week 1
    stops = season_stops() + [{"status": "ok", "to": {"year": 2026, "phase": p, "week": None}}
                              for p in ("STAFF", "RE-SIGN", "FREE AGENCY", "COMBINE", "PRO DAY", "DRAFT", "UDFA")]
    stops += [{"status": "ok", "to": {"year": 2026, "phase": "PRESEASON", "week": None, "stop": f"PRESEASON_{n}"}}
              for n in (0, 1, 2, 3)]
    stops += [{"status": "ok", "to": {"year": 2027, "phase": "WEEK", "week": 1}}]
    out = L.run("unit", runner=_runner(calls, stops), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=True)
    advances = [c for c in calls if "advance" in c]
    ingests = [c for c in calls if "pipeline.watch_save" in c]
    assert len(advances) == 22 + 12
    assert len(ingests) == 23 + 12            # the seed, the week stops, the last stop, one per offseason stop
    assert out["stops"][-1]["to"]["phase"] == "WEEK" and out["stops"][-1]["to"]["week"] == 1
    assert out["stops"][-2]["to"]["stop"] == "PRESEASON_3"   # the cut day is ingested before week 1
    assert all("--no-quit" not in c for c in advances)


def test_a_stopped_advance_ends_the_offseason_loop_without_raising(tmp_path):
    calls = []
    stops = season_stops() + [STOPPED]
    out = L.run("unit", runner=_runner(calls, stops), python="py",
                seasons_dir=str(tmp_path / "s"), saves_dir="/saves", do_report=False)
    assert len(out["stops"]) == 1 and out["stops"][0]["status"] == "stopped"
    assert out["stops"][0]["screenshot"].endswith("stop.png")


def test_no_offseason_stops_after_the_postseason(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False)
    assert out["stops"] == [] and out["season_complete"] is True


# ---------------------------------------------------------------- the reports
def test_a_failed_clone_stops_the_season(tmp_path):
    with pytest.raises(L.StepFailed, match="clone save"):
        L.run("unit", runner=_runner([], fail="clone-save"), python="py", seasons_dir=str(tmp_path), do_report=False)


def test_an_empty_report_never_replaces_a_real_one(tmp_path):
    real, empty = tmp_path / "real.json", tmp_path / "empty.json"
    real.write_text(json.dumps(_snapshot()))
    empty.write_text(json.dumps({"season": {"calendar_year": 2026, "franchise_year": 0}, "games": [], "players": []}))
    first = L.report(real, tmp_path / "out", targets_path=_targets_file(tmp_path))
    assert first["stats"] and not first.get("kept_existing")
    before = open(first["report"]).read()
    second = L.report(empty, tmp_path / "out", targets_path=_targets_file(tmp_path))
    assert second.get("kept_existing") and second["stats"] == 0
    assert open(first["report"]).read() == before


def test_the_season_report_is_followed_by_a_decision_report(tmp_path):
    calls = []
    seen = {}

    def decision_fn(run, season, out_dir, *, targets_path=None):
        seen.update(run=run, season=season, out_dir=out_dir, targets=targets_path)
        return {"season": season, "verdict": "INCOMPLETE", "report": "d.md", "json": "d.json"}

    out = L.run("unit", runner=_runner(calls, season_stops()), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", targets_path=_targets_file(tmp_path), report_fn=lambda s: None,
                decision_fn=decision_fn, decision_targets="dt.json")
    assert seen == {"run": "unit", "season": 2026, "out_dir": out["run_dir"], "targets": "dt.json"}
    assert out["decisions"]["verdict"] == "INCOMPLETE"
    run_json = json.load(open(os.path.join(out["run_dir"], "run.json")))
    assert run_json["decisions"]["verdict"] == "INCOMPLETE"


def test_decision_report_skips_a_missing_unreadable_or_malformed_targets_file(tmp_path, monkeypatch):
    from engines.tuning import decision_compare as DC

    class Rep:
        season = 2026
        overall = "INCOMPLETE"

        def counts(self):
            return {"PASS": 2, "FAIL": 0, "NOT MEASURED": 4}

    monkeypatch.setattr(DC, "run", lambda dsn, season, **kw: {
        "report": Rep(), "paths": {"md": str(tmp_path / "d.md"), "json": str(tmp_path / "d.json")}})
    out = L.decision_report("unit", 2026, str(tmp_path))
    assert out["season"] == 2026 and out["verdict"] == "INCOMPLETE"
    assert out["counts"]["PASS"] == 2 and out["report"].endswith("d.md")

    def missing(*a, **k):
        raise FileNotFoundError("no decision targets at /nowhere")

    monkeypatch.setattr(DC, "run", missing)
    assert "no decision targets" in L.decision_report("unit", 2026, str(tmp_path))["skipped"]

    # audit F2 (M-0299): a malformed or partial targets file must not kill a finished season
    def malformed(*a, **k):
        raise json.JSONDecodeError("Expecting value", "{\"meta\": ", 9)

    monkeypatch.setattr(DC, "run", malformed)
    assert "unreadable" in L.decision_report("unit", 2026, str(tmp_path))["skipped"]

    def partial(*a, **k):
        raise KeyError("career_length")

    monkeypatch.setattr(DC, "run", partial)
    assert "unreadable" in L.decision_report("unit", 2026, str(tmp_path))["skipped"]


# ---------------------------------------------------------------- CLI
def test_main_dry_run_prints_the_plan(capsys):
    assert L.main(["--run", "p2c", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["save"] == "CAREER-TUNE-P2C" and out["db"] == "madden_tune_p2c" and out["offseason"] is True
    assert out["season"] == "week by week" and out["max_weeks"] == L.MAX_SEASON_STOPS
    assert L.main(["--run", "p2c", "--dry-run", "--fast-season"]) == 0
    assert json.loads(capsys.readouterr().out)["season"] == "one-shot sim"


def test_main_hands_the_flags_to_the_run(tmp_path, monkeypatch):
    captured = {}

    def fake_run(name, **kw):
        captured.update(name=name, **kw)
        return {"run_dir": str(tmp_path), "report": {"season": 2026}, "stops": [], "minutes": 1.0}

    monkeypatch.setattr(L, "run", fake_run)
    assert L.main(["--run", "unit"]) == 0
    assert captured["fast_season"] is False and captured["max_weeks"] == L.MAX_SEASON_STOPS
    assert captured["decision_fn"] is L.decision_report and captured["decision_targets"] is None
    assert L.main(["--run", "unit", "--fast-season", "--max-weeks", "9"]) == 0
    assert captured["fast_season"] is True and captured["max_weeks"] == 9
    assert L.main(["--run", "unit", "--no-decisions"]) == 0
    assert captured["decision_fn"] is None
    assert L.main(["--run", "unit", "--decision-targets", "dt.json"]) == 0
    assert captured["decision_targets"] == "dt.json"


def test_main_refuses_a_bad_run_name(capsys):
    assert L.main(["--run", "bad name"]) == 3
    assert "refused" in capsys.readouterr().err
