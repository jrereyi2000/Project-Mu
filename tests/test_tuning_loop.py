"""engines/tuning progression_fit + supersim_loop (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4, §4).
Offline: synthetic snapshots, no Madden, no pilot, no harness, no database. The supersim loop's CLI steps go through a
fake runner."""
import json
import os
import types

import pytest

from engines import progression as P
from engines.tuning import progression_fit as F
from engines.tuning import supersim_loop as S


# ---------- a tiny synthetic league ----------

ROSTER = (("QB", 2), ("HB", 2), ("WR", 4), ("TE", 2), ("LT", 1), ("LG", 1), ("C", 1), ("RG", 1), ("RT", 1),
          ("DT", 2), ("LE", 1), ("RE", 1), ("MLB", 2), ("LOLB", 1), ("ROLB", 1), ("CB", 3), ("FS", 1), ("SS", 1),
          ("K", 1), ("P", 1))
DEVS = ("Normal", "Normal", "Normal", "College_Impact", "College_Star")


def _player(pgid, team, pos, age, ovr, years_pro, games=None):
    stats = []
    if games is not None:
        stats = [{"kind": "offense", "stats": {"SEAS_YEAR": -1, "GAMESPLAYED": games, "RUSHYARDS": ovr * 5,
                                                 "RECYARDS": ovr * 3, "PASSYARDS": ovr * 20 if pos == "QB" else 0}}]
    return {"pgid": pgid, "team_index": team, "status": "Signed", "position": pos, "age": age, "overall": ovr,
            "years_pro": years_pro, "dev_trait": DEVS[pgid % len(DEVS)],
            "injury": {"status": "Injured" if pgid % 9 == 0 else "Uninjured"}, "season_stats": stats}


def tiny_snapshot(teams=2):
    players, pgid = [], 1
    for team in range(teams):
        for pos, n in ROSTER:
            for i in range(n + 1):           # one backup more than the starters
                age = 22 + (pgid * 7) % 13
                ovr = max(55, min(97, 92 - 6 * i - abs(age - 27) + (pgid * 5) % 7))
                players.append(_player(pgid, team, pos, age, ovr, max(0, age - 22), games=12 + pgid % 6))
                pgid += 1
    players.append({**_player(pgid, 32, "WR", 25, 70, 3), "status": "FreeAgent"})    # not rostered
    return {"season": {"calendar_year": 2026, "franchise_year": 0, "week_type": "RegularSeason"},
            "players": players, "games": []}


CAREERS = {"years_share": {"0": 0.2, "1-2": 0.28, "3-5": 0.29, "6+": 0.23},
           "games_rel": {"0": 0.56, "1-2": 0.76, "3-5": 0.71, "6+": 1.0}, "games": {}, "retention_biased": {},
           "meta": {"source": "synthetic", "careers": 0, "active": 0, "last_season": 2025}}


# ---------- progression_fit ----------

def test_league_from_snapshot_keeps_rostered_players_and_rookies():
    lg = F.league_from_snapshot(tiny_snapshot())
    assert len(lg["players"]) == 2 * sum(n + 1 for _, n in ROSTER)
    assert all(p["team"] < 32 for p in lg["players"])
    assert lg["rookies"] and all(r["ovr"] for r in lg["rookies"])
    assert lg["last_year"] == -1
    assert {p["dev"] for p in lg["players"]} <= {"Normal", "Star", "Superstar", "X-Factor"}
    assert lg["shape"]["players"] == len(lg["players"])


def test_replay_is_deterministic_under_a_seed():
    snap = tiny_snapshot()
    a = F.replay(snap, seasons=3, seed=4)
    b = F.replay(snap, seasons=3, seed=4)
    assert a == b
    assert len(a["seasons"]) == 3
    assert all(s["shape"]["players"] == a["players"] for s in a["seasons"])      # league size holds
    c = F.replay(snap, seasons=3, seed=5)
    assert c["seasons"] != a["seasons"]


def test_replay_ages_players_and_brings_in_rookies():
    lg = F.league_from_snapshot(tiny_snapshot())
    h = F.replay(lg, seasons=1, seed=0, hold_shape=False)
    s = h["seasons"][0]
    assert s["exits"] == s["rookies"] > 0                          # one rookie per leaver: the size holds
    assert s["shape"]["years_share"]["0"] > 0
    # expected exits are the save's rookie class size: on a bigger league the draw lands close to it
    big = F.league_from_snapshot(tiny_snapshot(teams=8))
    exits = F.replay(big, seasons=1, seed=0, hold_shape=False)["seasons"][0]["exits"]
    assert abs(exits - len(big["rookies"])) <= 0.35 * len(big["rookies"])
    assert F.replay(big, seasons=1, seed=0, hold_shape=False)["seasons"][0]["shape"]["avg_age"] != big["shape"]["avg_age"]


def test_params_patch_and_restore():
    before_offset, before_weight = P.AGE_BAND_OFFSET, P.PERF_WEIGHT
    mtime = os.path.getmtime(P.__file__)
    with F.patched({"AGE_BAND_OFFSET[2]": -1.5, "PERF_WEIGHT": 0.4, "STAR_OVR": 88.4}):
        assert P.AGE_BAND_OFFSET[2] == -1.5 and isinstance(P.AGE_BAND_OFFSET, tuple)
        assert P.AGE_BAND_OFFSET[0] == before_offset[0]
        assert P.PERF_WEIGHT == 0.4
        assert P.STAR_OVR == 88 and isinstance(P.STAR_OVR, int)
    assert P.AGE_BAND_OFFSET is before_offset and P.PERF_WEIGHT == before_weight
    with pytest.raises(RuntimeError):
        with F.patched({"BAND_GROWTH_PER_SPREAD[0][1]": 0.9}):
            assert P.BAND_GROWTH_PER_SPREAD[0] == (24, 0.9)
            raise RuntimeError("boom")
    assert P.BAND_GROWTH_PER_SPREAD[0] == (24, 0.5)
    with pytest.raises(KeyError):
        with F.patched({"PERF_WEIGHT": 0.1, "NO_SUCH_CONSTANT": 1}):
            pass
    assert P.PERF_WEIGHT == before_weight
    with pytest.raises(KeyError):
        F.get_param("compute_player_delta")
    assert os.path.getmtime(P.__file__) == mtime                  # the module file is never written


def test_replay_params_are_restored_afterwards():
    before = P.AGE_BAND_OFFSET
    F.replay(tiny_snapshot(), seasons=1, params={"AGE_BAND_OFFSET[0]": 1.0}, hold_shape=False)
    assert P.AGE_BAND_OFFSET == before


def test_fit_improves_a_planted_bad_constant():
    snap = tiny_snapshot(teams=3)
    out = F.fit(snap, budget=14, seed=0, seasons=2, careers=CAREERS, hold_shape=False,
                tunables=[("AGE_BAND_OFFSET[1]", 0.75, -4.0, 2.0)], start={"AGE_BAND_OFFSET[1]": -4.0})
    assert out["score_after"] < out["score_before"]
    assert out["params"]["AGE_BAND_OFFSET[1]"] > -4.0
    assert out["evaluations"] <= 14
    assert P.AGE_BAND_OFFSET[1] == -0.76                           # the engine itself is untouched


def test_score_is_zero_on_its_own_start():
    lg = F.league_from_snapshot(tiny_snapshot())
    h = {"start": lg["shape"], "seasons": [{"shape": lg["shape"], "games": {}}]}
    s, detail = F.score(h, {})
    assert s == 0 and detail


def test_career_targets_from_a_csv(tmp_path):
    p = tmp_path / "careers.csv"
    rows = [("2025", "2025", 5), ("2024", "2025", 20), ("2020", "2025", 90), ("2018", "2022", 60), ("2023", "2025", 30)]
    p.write_text("first_season,last_season,games_played\n" + "\n".join(f"{a},{b},{g}" for a, b, g in rows) + "\n")
    c = F.career_targets(str(p))
    assert c["meta"]["active"] == 4
    assert c["years_share"]["0"] == 0.25 and c["years_share"]["3-5"] == 0.25
    assert max(c["games_rel"].values()) == 1.0


def test_write_fit_writes_json_and_report(tmp_path):
    out = F.fit(tiny_snapshot(), budget=3, seasons=1, careers=CAREERS, hold_shape=False,
                tunables=["PERF_WEIGHT"])
    js, md = F.write_fit(out, str(tmp_path / "progression_fit.json"), "snap.json")
    data = json.loads(open(js).read())
    assert data["meta"]["applied"] is False and "score_before" in data and "detail_after" in data
    assert "recommendation" in open(md).read()


# ---------- supersim_loop: next_change ----------

FIELDS = [{"asset": "football/attribsys/data/supersim_a", "path": "Run.Chance", "start": 1.0, "step": 0.4,
           "min": 0.0, "max": 2.0},
          {"asset": "football/attribsys/data/supersim_b", "path": "Pass.Chance", "start": 10, "step": 2}]


def _entry(change, score):
    return {"change": change, "score": score}


def test_next_change_starts_with_the_baseline_then_steps_up():
    assert S.next_change([], FIELDS) == {"baseline": True}
    ch = S.next_change([_entry(None, 1.0)], FIELDS)
    assert ch["key"] == "football/attribsys/data/supersim_a::Run.Chance"
    assert ch["value"] == pytest.approx(1.4) and ch["direction"] == 1


def test_next_change_halves_the_step_on_overshoot():
    log = [_entry(None, 1.0)]
    first = S.next_change(log, FIELDS)
    log.append(_entry(first, 1.2))                        # worse: overshoot
    second = S.next_change(log, FIELDS)
    assert second["key"] == first["key"]
    assert second["step"] == pytest.approx(0.2)
    assert second["value"] == pytest.approx(0.8)          # undone, then the other way by half
    log.append(_entry(second, 0.9))                       # a 10% gain: kept, same direction
    third = S.next_change(log, FIELDS)
    assert third["old"] == pytest.approx(0.8) and third["value"] == pytest.approx(0.6)


def test_next_change_moves_to_the_next_field_and_stops_without_gain():
    log = [_entry(None, 1.0)]
    for score in (1.1, 0.995):                            # an overshoot, then a gain under 2%
        log.append(_entry(S.next_change(log, FIELDS), score))
    ch = S.next_change(log, FIELDS)
    assert ch["key"].endswith("::Pass.Chance")            # two tries without gain on field A: next field
    log.append(_entry(ch, 1.5))                           # third try without a 2% gain
    assert S.next_change(log, FIELDS) is None


def test_next_change_a_gain_resets_the_stop_count():
    log = [_entry(None, 1.0)]
    log.append(_entry(S.next_change(log, FIELDS), 1.1))
    log.append(_entry(S.next_change(log, FIELDS), 1.05))
    log.append(_entry(S.next_change(log, FIELDS), 0.5))   # a big gain
    assert S.next_change(log, FIELDS) is not None


# ---------- supersim_loop: refusal and a mocked iteration ----------

def test_supersim_refuses_without_ebx(tmp_path):
    assert S.ebx_readable({"ok": True, "ebx_readable": False}) is False
    with pytest.raises(S.Refused, match="ebx_readable"):
        S.run("t1", FIELDS, check_ebx=lambda: False, runs_dir=str(tmp_path),
              runner=lambda *a, **k: pytest.fail("no step may run"))
    assert not (tmp_path / "t1").exists()


def test_supersim_refuses_a_tune_save_as_the_base(tmp_path):
    with pytest.raises(S.Refused):
        S.run("t1", FIELDS, base_save="CAREER-TUNE-x", check_ebx=lambda: True, runs_dir=str(tmp_path))


def _sim_snapshot():
    box = {"OFFPASSYARDS": 240, "OFFRUSHYARDS": 110, "SACKS": 2, "GIVEAWAYS": 1}
    games = [{"franchise_year": 0, "week_type": "RegularSeason", "status": "HomeWon", "home_team_index": h,
              "away_team_index": a, "home_score": 24, "away_score": 20, "home_team_stats": box, "away_team_stats": box}
             for h, a in ((0, 1), (1, 0))]
    return {"season": {"calendar_year": 2026, "franchise_year": 0}, "games": games, "players": []}


def test_one_mocked_iteration_end_to_end(tmp_path):
    targets = {"meta": {"seasons": [2015, 2025]},
               "stats": {"league.points_per_team_game": {"mean": 22.0, "lo": 21.0, "hi": 23.0, "n": 11},
                         "team.total_yards.p50": {"mean": 6000, "lo": 5600, "hi": 6400, "n": 11}}}
    tpath = tmp_path / "targets.json"
    tpath.write_text(json.dumps(targets))
    calls = []

    def runner(cmd, **kw):
        calls.append(list(map(str, cmd)))
        if cmd[0] == "node":
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w") as f:
                json.dump(_sim_snapshot(), f)
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    reports = []
    out = S.run("unit", FIELDS, max_iters=2, check_ebx=lambda: True, runner=runner, targets_path=str(tpath),
                runs_dir=str(tmp_path / "runs"), builds_root=str(tmp_path / "builds"), saves_dir="/saves",
                python="py", report=reports.append)
    log = S.read_log(out["run_dir"])
    assert len(log) == 2 and log[0]["change"] is None and log[1]["change"]["path"] == "Run.Chance"
    assert log[0]["score"] is not None and log[0]["accepted"]
    jobs = [c[2:4] if c[0] == "py" else ["node"] for c in calls[:6]]
    assert jobs == [["tools.mod_builder", "build"], ["tools.mod_builder", "install"],
                    ["tools.madden_pilot", "clone-save"], ["tools.madden_pilot", "launch"],
                    ["tools.madden_pilot", "sim-season"], ["node"]]
    assert calls[1][-2:] == ["--pack", "Mu Tune"] and calls[3][-1] == "Mu Tune"
    assert ["--from", "CAREER-WEEK1", "--to", "CAREER-TUNE-unit"] == calls[2][-4:]
    assert calls[5][2] == "/saves/CAREER-TUNE-unit"
    recipe = json.loads((tmp_path / "builds" / "mu_tune_unit" / "recipe.json").read_text())
    assert recipe["title"] == "Mu Tune unit" and {o["op"] for o in recipe["ops"]} == {"ebx-set"}
    assert recipe["ops"][0]["value"] == pytest.approx(1.4)          # the second iteration's change
    assert os.path.exists(os.path.join(out["run_dir"], "state.json")) and len(reports) == 2


def test_a_failed_step_stops_the_iteration(tmp_path):
    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=1 if "sim-season" in cmd else 0, stdout="", stderr="refused")

    tpath = tmp_path / "t.json"
    tpath.write_text(json.dumps({"meta": {}, "stats": {}}))
    with pytest.raises(S.StepFailed, match="sim season"):
        S.run("unit", FIELDS, max_iters=1, check_ebx=lambda: True, runner=runner, targets_path=str(tpath),
              runs_dir=str(tmp_path / "runs"), builds_root=str(tmp_path / "b"), python="py")
    assert S.read_log(str(tmp_path / "runs" / "unit")) == []
