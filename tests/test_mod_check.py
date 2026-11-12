"""Visual mod check: segment plans, the synthetic run and the report (VISUAL_MOD_CHECK_PLAN U1).

No PC, no database, no ntfy, no model: a fake driver writes Pillow frames, an injected clone
hands back a CAREER-TUNE-CHK<letter> name, and every refusal is checked against its message.
"""
import io
import json
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

import pipeline.notify
from tools.madden_pilot import vision
from tools.mod_check import runner
from tools.mod_check.__main__ import main
from tools.mod_check.segments import PlanError, load_checks, load_plan

RED, BLUE, GREY = (255, 0, 0), (0, 0, 255), (47, 47, 47)


def write_png(path: Path, colour, size=(64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def reference(pass_name="refs/red_pass.png", fail_name="refs/red_native.png", margin=0.05):
    return {"region": [0.0, 0.0, 1.0, 1.0],
            "reference": {"pass": pass_name, "fail": fail_name, "margin": margin}}


def write_plan(folder: Path, segments, pack="Mu Test") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "segments.json"
    data = {"segments": segments}
    if pack is not None:
        data["pack"] = pack
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_checks(folder: Path, checks) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "checks.json"
    path.write_text(json.dumps(checks), encoding="utf-8")
    return path


@pytest.fixture
def mod(tmp_path):
    """data/mod_checks/demo: red pass ref, blue fail ref, four segments, three assets."""
    root = tmp_path / "data"
    folder = root / "demo"
    write_png(folder / "refs/red_pass.png", RED)
    write_png(folder / "refs/red_native.png", BLUE)
    write_checks(folder, {
        "mod_asset": reference(),
        "native_asset": reference(),
        "odd_asset": reference(),
    })
    write_plan(folder, [
        {"name": "game_start", "after": "kickoff", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["mod_asset"]},
        {"name": "halftime", "after": "halftime_start", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["native_asset"]},
        {"name": "supersim", "cue": "supersim_menu", "record_s": 1, "fps": 1, "width": 640,
         "preroll_s": 1.5, "checks": ["odd_asset"]},
        {"name": "flip", "after": "kickoff", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["mod_asset"]},
    ])
    return root, folder


class FakeDriver:
    """A driver that writes one solid PNG per frame for the segment it is recording."""

    def __init__(self, frames_for):
        self.frames_for = frames_for
        self.log = []
        self.timeouts = {}

    def start(self, save, pack=None):
        self.log.append(("start", save, pack))
        return {"status": "ok", "save": save, "pack": pack}

    def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
        self.log.append(("wait", cue, hold_s))
        self.timeouts[cue] = timeout_s

    def record(self, out_dir, seconds, fps, width):
        name = Path(out_dir).parent.name
        self.log.append(("record", name, seconds, fps, width))
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for index, colour in enumerate(self.frames_for.get(name, [])):
            path = folder / f"frame_{index:04d}.png"
            Image.new("RGB", (64, 64), colour).save(path)
            written.append(path)
        return written

    def finish(self):
        self.log.append(("finish",))
        return {"status": "ok", "pack": "Mu Test", "autosave_written": True,
                "quit": True, "quit_route": "quit-game", "end_screen": "FRANCHISE_LIST"}


def fake_frames():
    return {
        "game_start": [RED],
        "halftime": [BLUE],
        "supersim": [GREY],
        "flip": [BLUE, RED],
    }


def clone_to(name="CAREER-TUNE-CHK1"):
    return lambda src, prefix: name


def run_mod(root, driver, **kwargs):
    kwargs.setdefault("watcher", lambda: False)
    kwargs.setdefault("clone", clone_to())
    kwargs.setdefault("describe", lambda png: "The mod is on screen. It looks installed.")
    kwargs.setdefault("notice", lambda line: None)
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("launch", lambda pack: {"status": "ok", "pack": pack,
                                              "already_running": False})
    return runner.run("CAREER-TUNE-X", "demo", data_dir=root, driver=driver, **kwargs)


# ---------------------------------------------------------------- plan validation
def test_unknown_cue_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "halftime", "record_s": 2,
                         "fps": 1, "width": 640, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "after must be one of kickoff, halftime_start, supersim_menu" in str(caught.value)


def test_record_s_outside_the_drivers_limits_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 31,
                         "fps": 1, "width": 640, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "record_s must be an int 1..30" in str(caught.value)


def test_fps_outside_the_drivers_limits_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 2,
                         "fps": 11, "width": 640, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "fps must be an int 1..10" in str(caught.value)


def test_width_below_the_minimum_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 2,
                         "fps": 1, "width": 100, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "width must be an int >= 320" in str(caught.value)


def test_preroll_outside_its_range_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 2,
                         "fps": 1, "width": 640, "preroll_s": 11, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "preroll_s must be a number 0..10" in str(caught.value)


def test_a_segment_without_a_cue_is_refused_unless_static(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "record_s": 2, "fps": 1, "width": 640,
                         "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "no after cue" in str(caught.value)
    assert "supersim_menu" in str(caught.value)


def test_a_check_name_missing_from_checks_json_is_refused(mod):
    root, folder = mod
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 2,
                         "fps": 1, "width": 640, "checks": ["not_a_check"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    message = str(caught.value)
    assert "unknown check(s) ['not_a_check']" in message
    assert "mod_asset" in message


def test_a_missing_reference_file_is_refused(mod):
    root, folder = mod
    write_checks(folder, {"mod_asset": reference(pass_name="refs/gone_pass.png")})
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    message = str(caught.value)
    assert "reference pass file is missing: refs/gone_pass.png" in message


def test_checks_resolve_reference_paths_against_the_mod_directory(mod):
    root, folder = mod
    checks = load_checks(folder)
    assert checks["mod_asset"]["reference"]["pass"] == str(folder / "refs/red_pass.png")


def test_dry_run_capture_load_skips_missing_references(mod):
    root, folder = mod
    write_checks(folder, {"mod_asset": reference(pass_name="refs/new_pass.png",
                                                 fail_name="refs/new_native.png")})
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 2,
                         "fps": 1, "width": 640, "checks": ["mod_asset"]}])
    plan = load_plan(folder, require_refs=False)       # capture is about to write the pair
    assert plan.checks["mod_asset"]["reference"]["pass"].endswith("refs/new_pass.png")


def test_a_segment_name_cannot_escape_the_run_folder(mod):
    root, folder = mod
    write_plan(folder, [{"name": "../escape", "after": "kickoff", "record_s": 2,
                         "fps": 1, "width": 640, "checks": ["mod_asset"]}])
    with pytest.raises(PlanError) as caught:
        load_plan(folder)
    assert "plain name" in str(caught.value)


def test_run_refuses_an_unknown_segment_selection(mod):
    root, folder = mod
    with pytest.raises(PlanError) as caught:
        run_mod(root, FakeDriver(fake_frames()), segments=["nope"])
    assert "unknown segment(s) ['nope']" in str(caught.value)


# ---------------------------------------------------------------- refusals before work
def test_a_non_tune_save_is_refused(mod):
    root, folder = mod
    result = runner.run("CAREER-MU2026", "demo", data_dir=root, watcher=lambda: False,
                        clone=clone_to(), driver=FakeDriver(fake_frames()))
    assert result["status"] == "refused"
    assert "CAREER-TUNE-*" in result["why"]


def test_a_running_watcher_is_refused(tmp_path, mod):
    root, folder = mod
    result = runner.run("CAREER-TUNE-X", "demo", data_dir=root, watcher=lambda: True,
                        clone=clone_to(), driver=FakeDriver(fake_frames()))
    assert result["status"] == "refused"
    assert "watch_save" in result["why"]


def test_the_run_scores_a_clone_and_never_the_given_save(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver)
    assert result["clone"] == "CAREER-TUNE-CHK1"
    assert driver.log[0] == ("start", "CAREER-TUNE-CHK1", "Mu Test")
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["save"] == "CAREER-TUNE-CHK1"
    assert result["source_save"] == "CAREER-TUNE-X"


def test_a_clone_that_returns_the_given_save_is_refused(mod):
    root, folder = mod
    result = runner.run("CAREER-TUNE-X", "demo", data_dir=root, watcher=lambda: False,
                        clone=lambda src, prefix: src, driver=FakeDriver(fake_frames()))
    assert result["status"] == "refused"
    assert "never the one it was given" in result["why"]


def test_default_clone_picks_the_first_free_chk_name(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "CAREER-TUNE-X").write_bytes(b"source")
    (saves / "CAREER-TUNE-CHKA").write_bytes(b"old")
    (saves / "career-tune-chkb").write_bytes(b"old")      # Windows is case-blind
    name = runner.default_clone("CAREER-TUNE-X", folder=saves)
    assert name == "CAREER-TUNE-CHKC"                    # letters, never digits: OCR reads 1 as I
    assert (saves / name).read_bytes() == b"source"


def test_default_clone_refuses_a_non_tune_save(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "CAREER-MU2026").write_bytes(b"source")
    with pytest.raises(ValueError):
        runner.default_clone("CAREER-MU2026", folder=saves)


# ---------------------------------------------------------------- the synthetic run
def test_a_run_reports_pass_fail_and_unknown(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver)
    assert result["status"] == "fail"
    report = json.loads(Path(result["report_json"]).read_text())
    statuses = {segment["name"]: {name: entry["status"]
                                  for name, entry in segment["checks"].items()}
                for segment in report["segments"]}
    assert statuses == {"game_start": {"mod_asset": "pass"},
                        "halftime": {"native_asset": "fail"},
                        "supersim": {"odd_asset": "unknown"},
                        "flip": {"mod_asset": "pass"}}
    assert report["verdict"] == "fail"
    assert report["pack"] == "Mu Test"
    assert report["pack_required"] == "Mu Test"
    assert report["pack_actual"] == "Mu Test" and report["pack_verified"] is True
    assert report["operational"]["status"] == "complete"
    assert report["autosave_written"] is True


def test_the_latest_decisive_frame_decides_and_is_named(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver)
    report = json.loads(Path(result["report_json"]).read_text())
    flip = next(segment for segment in report["segments"] if segment["name"] == "flip")
    assert flip["checks"]["mod_asset"]["status"] == "pass"          # BLUE then RED: latest wins
    assert flip["checks"]["mod_asset"]["frame"] == "segments/flip/frames/frame_0001.png"
    assert (Path(result["run_dir"]) / flip["checks"]["mod_asset"]["frame"]).is_file()


def test_every_asset_passing_is_a_pass(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver(fake_frames()), segments=["game_start", "flip"])
    assert result["status"] == "pass"


def test_an_inconclusive_pair_is_unknown(mod):
    root, folder = mod
    frames = {"supersim": [GREY]}
    result = run_mod(root, FakeDriver(frames), segments=["supersim"])
    assert result["status"] == "unknown"
    report = json.loads(Path(result["report_json"]).read_text())
    entry = report["segments"][0]["checks"]["odd_asset"]
    assert entry["status"] == "unknown"
    assert entry["why"]


def test_a_segment_with_no_frames_is_unknown(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver({}), segments=["game_start"])
    assert result["status"] == "unknown"
    report = json.loads(Path(result["report_json"]).read_text())
    entry = report["segments"][0]["checks"]["mod_asset"]
    assert entry["frame"] is None
    assert "no frames" in entry["why"]


def test_the_report_has_exactly_the_plan_shape(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver(fake_frames()))
    report = json.loads(Path(result["report_json"]).read_text())
    assert set(report) == {"save", "mod", "segments", "verdict", "minutes",
                           "pack", "pack_required", "pack_actual", "pack_verified", "launch",
                           "provenance",
                           "quit", "quit_route", "end_screen", "operational",
                           "autosave_written"}
    assert report["minutes"] >= 0
    for segment in report["segments"]:
        assert set(segment) == {"name", "cue", "recorded_s", "preroll_s", "frames", "checks",
                                "summary"}
        assert isinstance(segment["frames"], list)
        for entry in segment["checks"].values():
            assert set(entry) == {"status", "measures", "frame", "why"}


def test_the_report_markdown_names_each_segment_and_its_summary(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver(fake_frames()))
    markdown = Path(result["report_md"]).read_text()
    for segment in ("game_start", "halftime", "supersim", "flip"):
        assert f"## {segment}" in markdown
    assert markdown.count("The mod is on screen. It looks installed.") == 4
    assert "| asset | status | measures | frame | why |" in markdown


def test_a_missing_model_is_reported_and_the_run_still_finishes(mod):
    root, folder = mod
    calls = []

    def unavailable(png):
        calls.append(png)
        raise vision.VisionUnavailable("no Ollama")

    result = run_mod(root, FakeDriver(fake_frames()), describe=unavailable)
    assert result["status"] == "fail"
    report = json.loads(Path(result["report_json"]).read_text())
    assert all(segment["summary"] == "model unavailable" for segment in report["segments"])
    assert len(calls) == 4                      # one description per segment, never per frame


def test_a_model_returning_none_is_unavailable(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver(fake_frames()), describe=lambda png: None)
    report = json.loads(Path(result["report_json"]).read_text())
    assert all(segment["summary"] == "model unavailable" for segment in report["segments"])


def test_a_preroll_is_reported_and_slept_before_recording(mod):
    root, folder = mod
    slept = []
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver, segments=["supersim"], sleep=slept.append)
    assert slept == [1.5]
    assert ("wait", "supersim_menu", 23) in driver.log
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["segments"][0]["cue"] == "supersim_menu"
    assert report["segments"][0]["preroll_s"] == 1.5


def test_a_missed_cue_stops_the_run_without_a_report(mod):
    root, folder = mod
    class Missing(FakeDriver):
        def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
            raise runner.CueMissed(cue, "never seen")
    result = run_mod(root, Missing(fake_frames()), segments=["game_start"])
    assert result["status"] == "stopped"
    assert "cue 'kickoff'" in result["why"]
    assert not (Path(result["run_dir"]) / "report.json").exists()


def test_the_notice_line_follows_the_pilot_style(mod):
    root, folder = mod
    notices = []
    run_mod(root, FakeDriver(fake_frames()),
            segments=["game_start", "halftime"], notice=notices.append)
    assert notices == ["mod_check demo: 1/2 pass (halftime fail)"]


def test_the_notice_goes_through_pipeline_notify(mod, monkeypatch):
    root, folder = mod
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify", lambda message, **kw: sent.append(message) or True)
    run_mod(root, FakeDriver(fake_frames()), segments=["game_start"], notice=None)
    assert sent and sent[0].startswith("mod_check demo: 1/1 pass")


def test_a_dry_run_prints_the_plan_and_scores_nothing(mod, monkeypatch, capsys):
    root, folder = mod
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    def boom(*args, **kwargs):
        raise AssertionError("a dry run must not clone, drive, describe or notify")

    monkeypatch.setattr(runner, "default_clone", boom)
    monkeypatch.setattr(runner, "PilotDriver", boom)
    monkeypatch.setattr(pipeline.notify, "notify", boom)
    code = main(["run", "--save", "CAREER-TUNE-X", "--mod", "demo", "--dry-run",
                 "--data-dir", str(root)])
    output = capsys.readouterr().out
    assert code == 0
    for expected in ("mod_check plan: demo", "game_start", "cue kickoff", "record_s 2",
                     "fps 1", "width 640", "preroll 1.5s", "checks:",
                     "mod_asset: region"):
        assert expected in output
    assert "nothing recorded" in output
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_run_selects_only_the_named_segments(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    run_mod(root, driver, segments=["halftime"])
    assert [entry[1] for entry in driver.log if entry[0] == "record"] == ["halftime"]
    assert ("wait", "halftime_start", 22) in driver.log
    assert driver.timeouts["halftime_start"] >= 1800       # room for the bounded 320-play route


def test_a_run_refuses_a_run_dir_outside_the_check_folder(mod, tmp_path):
    root, folder = mod
    outside = tmp_path / "elsewhere"
    with pytest.raises(PlanError) as caught:
        run_mod(root, FakeDriver(fake_frames()), run_dir=outside)
    assert "inside" in str(caught.value)


def test_one_report_per_run_under_the_check_folder(mod):
    root, folder = mod
    run_mod(root, FakeDriver(fake_frames()))
    runs = list((root / "runs").iterdir())
    assert len(runs) == 1
    assert runs[0].name.endswith("_demo")
    assert (runs[0] / "report.json").is_file()
    assert (runs[0] / "report.md").is_file()


# ---------------------------------------------------------------- F2/F3/F4/F6 regression seams
def test_a_missed_supersim_cue_stops_before_recording_or_scoring(mod):
    """F2: a red current frame must never become a supersim pass without its route."""
    root, folder = mod
    class Missing(FakeDriver):
        def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
            self.log.append(("wait", cue, hold_s))
            raise runner.CueMissed(cue, "never reached SUPERSIM_MENU")
        def record(self, *args, **kwargs):
            raise AssertionError("a missed cue must not record")
    driver = Missing(fake_frames())
    result = run_mod(root, driver, segments=["supersim"])
    assert result["status"] == "stopped" and result["stop"] == "cue_missed"
    assert not (Path(result["run_dir"]) / "report.json").exists()
    assert ("wait", "supersim_menu", 23) in driver.log
    assert not [entry for entry in driver.log if entry[0] == "record"]


def test_an_explicit_run_dir_that_already_holds_a_run_is_refused(mod):
    """F3: a second run cannot reuse the first run's evidence."""
    root, folder = mod
    live = root / "runs" / "live"
    first = run_mod(root, FakeDriver(fake_frames()), run_dir=live)
    before = (live / "report.json").read_text()
    second_driver = FakeDriver(fake_frames())
    second = run_mod(root, second_driver, run_dir=live)
    assert second["status"] == "refused"
    assert "not fresh" in second["why"]
    assert second_driver.log == []                      # refused before clone or driver
    assert (live / "report.json").read_text() == before


def test_two_default_runs_in_the_same_second_get_their_own_directory(mod, monkeypatch):
    """F3: the second-resolution stamp must not merge two invocations."""
    root, folder = mod
    import datetime as _datetime

    class Frozen(_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 12, 12, 0, 0)

    monkeypatch.setattr(runner.datetime, "datetime", Frozen)
    first = run_mod(root, FakeDriver(fake_frames()), segments=["game_start"])
    second = run_mod(root, FakeDriver(fake_frames()), segments=["game_start"])
    assert first["run_dir"] != second["run_dir"]
    assert first["run_dir"].endswith("_demo")
    assert second["run_dir"].endswith("_demo-2")
    first_report = json.loads(Path(first["report_json"]).read_text())
    second_report = json.loads(Path(second["report_json"]).read_text())
    assert first_report["segments"][0]["frames"][0].startswith("segments/game_start/")
    assert second_report["segments"][0]["frames"][0].startswith("segments/game_start/")


def test_old_frames_are_never_a_fallback_for_an_empty_recording(mod, tmp_path):
    """F3: an explicit empty recording stays empty even when frames sit in the folder."""
    root, folder = mod
    class Empty(FakeDriver):
        def record(self, out_dir, seconds, fps, width):
            self.log.append(("record", Path(out_dir).parent.name, seconds, fps, width))
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 64), RED).save(out / "left_over.png")
            return []                                   # explicitly nothing was recorded
    result = run_mod(root, Empty(fake_frames()), segments=["game_start"])
    assert result["status"] == "unknown"
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["segments"][0]["checks"]["mod_asset"]["frame"] is None
    assert "no frames" in report["segments"][0]["checks"]["mod_asset"]["why"]


def test_frame_paths_filters_a_folder_fallback_to_this_recording(tmp_path):
    old = tmp_path / "old.png"
    Image.new("RGB", (8, 8), RED).save(old)
    import os
    os.utime(old, (1_000_000, 1_000_000))
    assert runner._frame_paths(None, tmp_path, since=time.time() - 1) == []
    new = tmp_path / "new.png"
    Image.new("RGB", (8, 8), RED).save(new)
    assert runner._frame_paths(None, tmp_path, since=time.time() - 60) == [new]


class FinishDriver(FakeDriver):
    def __init__(self, frames_for, finished=None, raise_error=False):
        super().__init__(frames_for)
        self.finished = finished
        self.raise_error = raise_error

    def finish(self):
        self.log.append(("finish",))
        if self.raise_error:
            raise runner.DriverError("quit failed; game still open")
        return self.finished


def test_a_failed_quit_is_incomplete_but_keeps_the_pixel_rows(mod):
    """F4: passing pixels with an open game are not a completed run."""
    root, folder = mod
    noticed = []
    driver = FinishDriver(fake_frames(), finished={
        "quit": False, "why": "quit failed; game still open", "autosave_written": None,
        "end_screen": "GAME_PLAY"})
    result = run_mod(root, driver, segments=["game_start"], notice=noticed.append)
    assert result["status"] == "incomplete"
    assert "run cleanup_failed" in noticed[0] and "game still open" in noticed[0]
    assert result["verdict"] == "pass"
    assert result["operational"]["status"] == "cleanup_failed"
    assert "game still open" in result["operational"]["why"]
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["verdict"] == "pass"
    assert report["quit"] is False and report["end_screen"] == "GAME_PLAY"
    assert report["operational"]["status"] == "cleanup_failed"
    assert "leave it by hand" in report["operational"]["recovery"]
    assert report["segments"][0]["checks"]["mod_asset"]["status"] == "pass"


def test_a_finish_exception_is_incomplete(mod):
    root, folder = mod
    result = run_mod(root, FinishDriver(fake_frames(), raise_error=True), segments=["game_start"])
    assert result["status"] == "incomplete"
    assert "finish failed" in result["operational"]["why"]


def test_a_successful_quit_keeps_its_pixel_verdict(mod):
    root, folder = mod
    result = run_mod(root, FakeDriver(fake_frames()), segments=["game_start"])
    assert result["status"] == "pass"
    assert result["operational"]["status"] == "complete"
    assert result["quit"] is True and result["end_screen"] == "FRANCHISE_LIST"


def test_missing_result_nonzero_exit_and_finish_timeout_are_not_a_clean_run():
    """F4: every failed job exit shape is operational, never a pass."""
    for finished, needle in [
        ({"quit": False, "why": "watch-game wrote no result.json", "exit_code": 0,
          "job_status": None}, "no result.json"),
        ({"quit": True, "exit_code": 1, "job_status": "ok"}, "exited 1"),
        ({"quit": True, "exit_code": 0, "job_status": "stopped", "why": "Stuck"}, "reported stopped"),
        ({"quit": True, "exit_code": 0, "job_status": "ok", "stopped": ["finish-timeout"]},
         "finish-timeout"),
    ]:
        operational = runner._operational(finished, None)
        assert operational["status"] == "cleanup_failed", finished
        assert needle in operational["why"], (needle, operational)
    assert runner._operational({"quit": True, "exit_code": 0, "job_status": "ok"}, None)[
        "status"] == "complete"


def test_a_plan_without_its_expected_pack_is_refused(mod):
    """F6: the mod plan is bound to its test pack."""
    root, folder = mod
    write_plan(folder, [
        {"name": "game_start", "after": "kickoff", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["mod_asset"]}], pack=None)
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver)
    assert result["status"] == "refused"
    assert "expected test pack" in result["why"]
    assert driver.log == [] and not (root / "runs").exists()


def test_a_caller_pack_that_disagrees_with_the_plan_is_refused(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver, pack="Mu Base")
    assert result["status"] == "refused"
    assert "disagrees" in result["why"]
    assert driver.log == []


def test_a_launch_that_reports_another_pack_stops_before_recording(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver, launch=lambda pack: {
        "status": "ok", "pack": "Mu Base", "already_running": False})
    assert result["status"] == "stopped" and result["stop"] == "launch"
    assert result["pack_verified"] is False
    assert driver.log == []
    assert not list((root / "runs").rglob("report.json"))


def test_an_already_running_madden_is_unknown_provenance(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver, launch=lambda pack: {
        "status": "ok", "pack": pack, "already_running": True})
    assert result["status"] == "stopped" and result["stop"] == "launch"
    assert driver.log == []


def test_passing_pixels_cannot_override_a_refused_launch(mod):
    root, folder = mod
    driver = FakeDriver(fake_frames())
    result = run_mod(root, driver, launch=lambda pack: {
        "status": "ok", "pack": "Mu Base", "already_running": False},
        check=lambda *args: {"status": "pass"})
    assert result["status"] == "stopped"
    assert driver.log == []


# ---------------------------------------------------------------- the real driver's hold
def _write_step(native: Path, screen: str, number: int = 1) -> Path:
    steps = Path(native) / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    path = steps / f"{number:04d}_{screen}.json"
    path.write_text(json.dumps({"step": number, "screen": screen}), encoding="utf-8")
    return path


class FakeProcess:
    """A stand-in for the background ``watch-game`` process, including its ready marker."""

    def __init__(self, command, *, steps=None, result=None, exit_code=None, on_wait=None,
                 ready=None):
        self.command = list(command)
        self.native = Path(self.command[self.command.index("--run-dir") + 1])
        self.pid = 4242
        self._returncode = exit_code
        self.alive = exit_code is None
        self.terminated = False
        self.killed = False
        self.waits = []
        self.result = result
        self.on_wait = on_wait
        self.stdout = io.StringIO("watch-game: loading the franchise\n")
        self.stderr = io.StringIO("")
        for index, screen in enumerate(steps or [], 1):
            _write_step(self.native, screen, index)
        if ready is not None:
            token = (self.command[self.command.index("--ready-token") + 1]
                     if "--ready-token" in self.command else None)
            marker = {"anchor": "kickoff", "route": "kickoff", "screen": "GAME_KICKOFF",
                      "clock": {"quarter": 1, "seconds": 660}, "hold_seconds": 28,
                      "ready_token": token, "pid": self.pid,
                      "hold_until_epoch": time.time() + 3600}
            marker.update(ready)
            (self.native / runner.READY_FILE).write_text(json.dumps(marker), encoding="utf-8")

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._returncode is not None:
            self.alive = False
            return self._returncode
        if self.on_wait is None and self.result is None:
            raise subprocess.TimeoutExpired("watch-game", timeout)
        if self.on_wait is not None:
            self.on_wait(self)
        if self.result is not None:
            (self.native / "result.json").write_text(json.dumps(self.result), encoding="utf-8")
        self._returncode, self.alive = 0, False
        return 0

    def terminate(self):
        self.terminated, self.alive = True, False
        self._returncode = -15

    def kill(self):
        self.killed, self.alive = True, False
        self._returncode = -9


def fake_popen(monkeypatch, **kwargs):
    """Patch ``subprocess.Popen`` with a factory that records the process it handed out."""
    made = []

    def factory(command, **kw):
        process = FakeProcess(command, **kwargs)
        made.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", factory)
    return made


def test_pilot_driver_starts_watch_game_and_waits_for_its_own_ready_marker(tmp_path, monkeypatch):
    made = fake_popen(monkeypatch, steps=["GAME_PREGAME_READY", "GAME_KICKOFF"], ready={})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver.wait_for("kickoff", timeout_s=5, hold_s=28, record_s=8)
    (process,) = made
    command = process.command
    assert command[:4] == ["python3", "-m", "tools.madden_pilot", "watch-game"]
    assert command[command.index("--save") + 1] == "CAREER-TUNE-CHKA"
    assert command[command.index("--to") + 1] == "kickoff"
    assert command[command.index("--hold-seconds") + 1] == "28"
    assert command[command.index("--ready-token") + 1]
    native = Path(command[command.index("--run-dir") + 1])
    assert native == tmp_path / "watch-game" / "01_kickoff"
    assert native.is_dir()
    assert process.alive and not process.terminated      # the job is holding, not finished


def test_pilot_driver_forwards_the_pack(tmp_path, monkeypatch):
    made = fake_popen(monkeypatch, steps=["GAME_PREGAME_READY", "GAME_KICKOFF"], ready={})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA", pack="Mu Test")
    driver.wait_for("kickoff", hold_s=21)
    (process,) = made
    assert process.command[process.command.index("--pack") + 1] == "Mu Test"


def test_the_halftime_cue_drives_the_halftime_route_and_records_on_ready(tmp_path, monkeypatch):
    """F1: halftime is no longer an unconditional stop."""
    made = fake_popen(monkeypatch, steps=["GAME_PLAY"],
                      ready={"anchor": "halftime_start", "route": "supersim",
                             "screen": "GAME_PLAY"})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver.wait_for("halftime_start", timeout_s=5, hold_s=32, record_s=12)
    (process,) = made
    assert process.command[process.command.index("--to") + 1] == "halftime"
    assert process.alive


def test_the_supersim_cue_requires_the_supersim_ready_screen(tmp_path, monkeypatch):
    """F2: the static screen is positioned and verified before any recording."""
    made = fake_popen(monkeypatch, ready={"anchor": "supersim_menu", "route": "supersim",
                                          "screen": "SUPERSIM_MENU"})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver.wait_for("supersim_menu", timeout_s=5, hold_s=23, record_s=3)
    (process,) = made
    assert process.command[process.command.index("--to") + 1] == "supersim"


def test_a_supersim_ready_marker_on_another_screen_is_refused(tmp_path, monkeypatch):
    fake_popen(monkeypatch, ready={"anchor": "supersim_menu", "route": "supersim",
                                   "screen": "PAUSE_MENU"})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("supersim_menu", timeout_s=5, hold_s=23, record_s=3)
    assert "ready screen is 'PAUSE_MENU'" in str(caught.value)


def test_a_kickoff_run_records_while_the_job_is_still_alive(mod, monkeypatch):
    """The whole point: record during the hold, then take the job's own quit."""
    root, folder = mod
    made = fake_popen(monkeypatch, steps=["GAME_PREGAME_READY", "GAME_KICKOFF"], ready={},
                      result={"quit": True, "quit_route": "quit-game",
                              "autosave_written": True, "pack": "Mu Test",
                              "end_screen": "FRANCHISE_LIST"})
    from tools.madden_pilot import win
    recordings = []

    def fake_record(command, timeout=60):
        process = made[0]
        recordings.append(process.alive)
        out = Path(command[command.index("--dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), RED).save(out / "frame_0000.png")
        return {"ok": True}

    monkeypatch.setattr(win, "call", fake_record)
    result = runner.run("CAREER-TUNE-X", "demo", segments=["game_start"], data_dir=root,
                        driver=runner.PilotDriver(run_dir=root / "runs" / "live"),
                        watcher=lambda: False, clone=lambda src, prefix: "CAREER-TUNE-CHKA",
                        launch=lambda pack: {"status": "ok", "pack": pack,
                                             "already_running": False},
                        describe=lambda png: "The mod is on screen.", sleep=lambda s: None,
                        notice=lambda line: None)
    assert result["status"] == "pass"
    assert recordings == [True]                # recorded while watch-game held the game
    assert made[0].alive is False              # finish collected the job
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["pack"] == "Mu Test" and report["pack_verified"] is True
    assert report["autosave_written"] is True


def test_a_halftime_run_records_while_the_job_holds(mod, monkeypatch):
    """F1: the halftime cue reaches its route and records during its ready hold."""
    root, folder = mod
    made = fake_popen(monkeypatch, steps=["GAME_PLAY"],
                      ready={"anchor": "halftime_start", "route": "supersim",
                             "screen": "GAME_PLAY"},
                      result={"status": "ok", "quit": True, "quit_route": "quit-game",
                              "autosave_written": True, "pack": "Mu Test",
                              "end_screen": "FRANCHISE_LIST"})
    from tools.madden_pilot import win
    recordings = []

    def fake_record(command, timeout=60):
        recordings.append(made[0].alive)
        out = Path(command[command.index("--dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), BLUE).save(out / "frame_0000.png")
        return {"ok": True}

    monkeypatch.setattr(win, "call", fake_record)
    result = runner.run("CAREER-TUNE-X", "demo", segments=["halftime"], data_dir=root,
                        driver=runner.PilotDriver(run_dir=root / "runs" / "live-halftime"),
                        watcher=lambda: False, clone=lambda src, prefix: "CAREER-TUNE-CHKA",
                        launch=lambda pack: {"status": "ok", "pack": pack,
                                             "already_running": False},
                        describe=lambda png: "The halftime show.", sleep=lambda s: None,
                        notice=lambda line: None)
    assert recordings == [True]
    assert made[0].command[made[0].command.index("--to") + 1] == "halftime"
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["segments"][0]["cue"] == "halftime_start"
    assert report["operational"]["status"] == "complete"


def test_a_job_that_exits_after_its_ready_marker_is_not_accepted(tmp_path, monkeypatch):
    """F3: an exited process can never satisfy readiness, even with a marker on disk."""
    fake_popen(monkeypatch, steps=["GAME_KICKOFF", "PAUSE_MENU"], ready={}, exit_code=1)
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("kickoff", timeout_s=5, hold_s=21)
    message = str(caught.value)
    assert "exited (1) after signalling ready" in message
    assert "PAUSE_MENU" in message
    assert "loading the franchise" in message


def test_a_stale_ready_marker_from_an_earlier_job_is_not_accepted(tmp_path, monkeypatch):
    """F3: a reused directory's older marker cannot become this job's readiness."""
    native = tmp_path / "watch-game" / "01_kickoff"
    native.mkdir(parents=True)
    (native / runner.READY_FILE).write_text(json.dumps({
        "anchor": "kickoff", "ready_token": "stale", "pid": 4242,
        "hold_until_epoch": time.time() + 3600}), encoding="utf-8")
    fake_popen(monkeypatch, ready=None)
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("kickoff", timeout_s=0.02, hold_s=21)
    assert "did not signal ready" in str(caught.value)


def test_a_latest_pause_step_is_not_readiness(tmp_path, monkeypatch):
    """F3: historical GAME_KICKOFF classification does not satisfy the cue."""
    fake_popen(monkeypatch, steps=["GAME_KICKOFF", "PAUSE_MENU"])
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("kickoff", timeout_s=0.02, hold_s=21)
    assert "did not signal ready" in str(caught.value)
    assert "PAUSE_MENU" in str(caught.value)


def test_a_ready_marker_whose_hold_is_too_short_is_stopped(tmp_path, monkeypatch):
    """F1: the ready signal carries a deadline; recording past it is refused."""
    made = fake_popen(monkeypatch, ready={"hold_until_epoch": time.time() + 1})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("kickoff", timeout_s=5, hold_s=28, record_s=30)
    assert "hold has" in str(caught.value) and "margin" in str(caught.value)
    assert made[0].terminated is True


def test_a_job_short_of_the_cue_at_the_timeout_is_stopped(tmp_path, monkeypatch):
    made = fake_popen(monkeypatch, steps=["FRANCHISE_LIST"])
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    with pytest.raises(runner.CueMissed) as caught:
        driver.wait_for("kickoff", timeout_s=0.01, hold_s=21)
    message = str(caught.value)
    assert "did not signal ready" in message
    assert "FRANCHISE_LIST" in message
    assert made[0].terminated is True          # never leave a job behind
    assert made[0].alive is False


def test_finish_takes_the_jobs_own_quit_without_a_second_quit(tmp_path, monkeypatch):
    made = fake_popen(monkeypatch, steps=["GAME_PREGAME_READY", "GAME_KICKOFF"], ready={},
                      result={"status": "ok", "quit": True, "quit_route": "quit-game",
                              "autosave_written": True, "pack": "Mu Test",
                              "end_screen": "FRANCHISE_LIST"})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA", pack="Mu Test")
    driver.wait_for("kickoff", hold_s=21)

    def boom(*args, **kwargs):
        raise AssertionError("finish must not run a second quit after a background job")

    monkeypatch.setattr(driver, "_call", boom)
    result = driver.finish()
    assert result["quit"] is True
    assert result["quit_route"] == "quit-game"
    assert result["autosave_written"] is True
    assert result["job_status"] == "ok"
    assert result["exit_code"] == 0
    assert result["pack"] == "Mu Test"
    assert result["end_screen"] == "FRANCHISE_LIST"
    assert made[0].alive is False


def test_finish_without_a_background_job_still_quits(tmp_path, monkeypatch):
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA", pack="Mu Test")
    calls = []
    monkeypatch.setattr(driver, "_call",
                        lambda *args, **kw: calls.append(args) or {"quit": True, "quit_route": "route",
                                                                   "status": "ok"})
    monkeypatch.setattr(driver, "saves", tmp_path / "saves")
    result = driver.finish()
    assert calls == [("quit", "--franchise", "TUNE-CHKA")]
    assert result["quit"] is True
    assert result["pack"] == "Mu Test"


def test_a_job_with_no_result_json_is_reported_by_finish(tmp_path, monkeypatch):
    made = fake_popen(monkeypatch, ready={})
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver.wait_for("kickoff", hold_s=21)
    made[0]._returncode, made[0].alive = 0, False     # it exited without writing result.json
    result = driver.finish()
    assert result["quit"] is False
    assert "wrote no result.json" in result["why"]
    assert result["exit_code"] == 0 and result["job_status"] is None
