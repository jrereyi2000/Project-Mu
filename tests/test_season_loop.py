"""engines.tuning.season_loop (CLOSE_OUT_CHECKLIST P2-05) and the P2-03 stage loop it drives.
Offline: every Madden-touching step goes through a fake runner; no PC, no database."""
import json
import os
import types

import pytest

from engines.tuning import season_loop as L


def _snapshot():
    box = {"OFFPASSYARDS": 240, "OFFRUSHYARDS": 110, "SACKS": 2, "GIVEAWAYS": 1}
    games = [{"franchise_year": 0, "week_type": "RegularSeason", "status": "HomeWon", "home_team_index": h,
              "away_team_index": a, "home_score": 24, "away_score": 20, "home_team_stats": box,
              "away_team_stats": box} for h, a in ((0, 1), (1, 0))]
    return {"season": {"calendar_year": 2026, "franchise_year": 0}, "games": games, "players": []}


TARGETS = {"meta": {"seasons": [2015, 2025]},
           "stats": {"league.points_per_team_team": {"mean": 22.0, "lo": 21.0, "hi": 23.0, "n": 11},
                     "team.total_yards.p50": {"mean": 6000, "lo": 5600, "hi": 6400, "n": 11}}}


def _runner(calls, stops=None, advance_status="ok", fail=None):
    """A fake subprocess.run: records every command, writes the snapshot for node, and prints
    the pilot's result JSON for advance."""
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
            stop = stops.pop(0) if stops else {"status": "ok", "to": {"year": 2027, "phase": "PRESEASON", "week": None}}
            if advance_status != "ok":
                stop = {"status": "stopped", "kind": "Unknown", "reason": "new screen",
                        "screenshot": "/runs/steps/0009_UNKNOWN.png"}
            return types.SimpleNamespace(returncode=1 if advance_status != "ok" else 0,
                                         stdout=json.dumps(stop), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    return runner


# ---------------------------------------------------------------- naming
def test_db_dsn_and_save_names():
    assert L.db_for("P2C") == "madden_tune_p2c"
    assert L.save_for("P2C") == "CAREER-TUNE-P2C"
    assert "dbname=madden_tune_p2c" in L.dsn_for("P2C")
    assert L.db_for("a-b c") == "madden_tune_a_b_c"


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


# ---------------------------------------------------------------- one whole season
def test_one_season_clones_sims_ingests_and_reports(tmp_path, capsys):
    calls = []
    stops = [{"status": "ok", "to": {"year": 2026, "phase": "STAFF", "week": None}},
             {"status": "ok", "to": {"year": 2026, "phase": "RE-SIGN", "week": None}},
             {"status": "ok", "to": {"year": 2027, "phase": "PRESEASON", "week": None, "stop": "PRESEASON_0"}},
             {"status": "ok", "to": {"year": 2027, "phase": "WEEK", "week": 1}}]
    tpath = tmp_path / "targets.json"
    tpath.write_text(json.dumps(TARGETS))
    out = L.run("unit", runner=_runner(calls, stops), python="py", seasons_dir=str(tmp_path / "seasons"),
                saves_dir="/saves", targets_path=str(tpath), report_fn=lambda s: None)
    # the harness leaves the franchise first, then clone, sim, sim, snapshot, ingest
    assert "quit" in calls[0] and "--franchise" in calls[0]
    assert "clone-save" in calls[1]
    assert ["--from", "CAREER-MU2026", "--to", "CAREER-TUNE-UNIT"] == calls[1][-4:]
    assert "regular-season-end" in calls[2] and "--no-quit" in calls[2]      # the postseason continues in place
    assert "--franchise" in calls[3] and "playoffs-end" in calls[3]
    assert calls[4][0] == "node"                                             # the report snapshot
    assert "pipeline.watch_save" in calls[5]
    assert "/saves/CAREER-TUNE-UNIT" in calls[5] and "madden_tune_unit" in " ".join(calls[5])
    assert calls[5].count("--no-articles") == 1 and "--no-freeze" in calls[5] and "--auto-init" in calls[5]
    assert out["db"] == "madden_tune_unit" and out["save"] == "CAREER-TUNE-UNIT"
    assert len(out["stops"]) == 4 and out["steps"] == ["clone", "regular-season", "playoffs", "snapshot",
                                                       "ingest-playoffs", "offseason"]
    assert out["report"]["season"] == 2026 and os.path.exists(out["report"]["report"])
    md = open(out["report"]["report"]).read()
    assert "Tuning comparison: season 2026" in md
    assert os.path.exists(os.path.join(out["run_dir"], "run.json"))


def test_the_offseason_loop_ingests_after_every_stop_and_stops_at_preseason(tmp_path):
    calls = []
    # the eight offseason stops, the preseason weeks and the cut day, then 2027 week 1
    stops = [{"status": "ok", "to": {"year": 2026, "phase": p, "week": None}}
             for p in ("STAFF", "RE-SIGN", "FREE AGENCY", "COMBINE", "PRO DAY", "DRAFT", "UDFA")]
    stops += [{"status": "ok", "to": {"year": 2026, "phase": "PRESEASON", "week": None, "stop": f"PRESEASON_{n}"}}
              for n in (0, 1, 2, 3)]
    stops += [{"status": "ok", "to": {"year": 2027, "phase": "WEEK", "week": 1}}]
    out = L.run("unit", runner=_runner(calls, stops), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=True)
    advances = [c for c in calls if "advance" in c]
    ingests = [c for c in calls if "pipeline.watch_save" in c]
    assert len(advances) == 12 and len(ingests) == 13        # the playoffs ingest plus one per stop
    assert out["stops"][-1]["to"]["phase"] == "WEEK" and out["stops"][-1]["to"]["week"] == 1
    assert out["stops"][-2]["to"]["stop"] == "PRESEASON_3"   # the cut day is ingested before week 1
    # every advance quits the franchise: the league office writes with it closed
    assert all("--no-quit" not in c for c in advances)


def test_a_stopped_advance_ends_the_loop_without_raising(tmp_path):
    calls = []
    stops = [{"to": {"year": 2026, "phase": "STAFF", "week": None}}]
    out = L.run("unit", runner=_runner(calls, stops, advance_status="stopped"), python="py",
                seasons_dir=str(tmp_path / "s"), saves_dir="/saves", do_report=False)
    assert len(out["stops"]) == 1 and out["stops"][0]["status"] == "stopped"
    assert out["stops"][0]["screenshot"].endswith("UNKNOWN.png")      # the screenshot to catalogue from
    assert sum(1 for c in calls if "pipeline.watch_save" in c) == 1      # the playoffs ingest only


def test_no_offseason_stops_after_the_postseason(tmp_path):
    calls = []
    out = L.run("unit", runner=_runner(calls), python="py", seasons_dir=str(tmp_path / "s"),
                saves_dir="/saves", do_report=False, offseason=False)
    assert out["stops"] == []
    assert not any("advance" in c for c in calls)


def test_resume_skips_the_clone_and_the_regular_season(tmp_path):
    calls = []
    L.run("unit", runner=_runner(calls), python="py", seasons_dir=str(tmp_path / "s"), saves_dir="/saves",
          do_report=False, offseason=False, resume=True)
    assert not any("clone-save" in c for c in calls)
    assert not any("regular-season-end" in c for c in calls)
    assert any("playoffs-end" in c for c in calls)
    sims = [c for c in calls if "sim-season" in c]
    assert sims and not any("--franchise" in c for c in sims)                # nothing is loaded: it loads the clone
    assert calls[0].count("quit") == 1                                       # the franchise is released first


def test_the_ingest_runs_with_the_franchise_closed(tmp_path):
    calls = []
    envs = []

    def runner(cmd, **kw):
        if "pipeline.watch_save" in " ".join(cmd):
            envs.append(kw.get("env") or {})
        return _runner(calls)(cmd, **kw)

    L.run("unit", runner=runner, python="py", seasons_dir=str(tmp_path / "s"), saves_dir="/saves",
          do_report=False, offseason=False, environ={"PATH": "/bin"})
    assert envs and envs[0]["LEAGUE_OFFICE_FRANCHISE_CLOSED"] == "1" and envs[0]["PATH"] == "/bin"


def test_a_failed_clone_stops_the_season(tmp_path):
    with pytest.raises(L.StepFailed, match="clone save"):
        L.run("unit", runner=_runner([], fail="clone-save"), python="py", seasons_dir=str(tmp_path), do_report=False)


# ---------------------------------------------------------------- CLI
def test_main_dry_run_prints_the_plan(capsys):
    assert L.main(["--run", "p2c", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["save"] == "CAREER-TUNE-P2C" and out["db"] == "madden_tune_p2c" and out["offseason"] is True


def test_main_refuses_a_bad_run_name(capsys):
    assert L.main(["--run", "bad name"]) == 3
    assert "refused" in capsys.readouterr().err
