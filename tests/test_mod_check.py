"""Visual mod check: segment plans, the synthetic run and the report (VISUAL_MOD_CHECK_PLAN U1).

No PC, no database, no ntfy, no model: a fake driver writes Pillow frames, an injected clone
hands back a CAREER-TUNE-CHK<letter> name, and every refusal is checked against its message.
"""
import json
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


def write_plan(folder: Path, segments) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "segments.json"
    path.write_text(json.dumps({"segments": segments}), encoding="utf-8")
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

    def start(self, save, pack=None):
        self.log.append(("start", save, pack))
        return {"status": "ok", "save": save, "pack": pack}

    def wait_for(self, cue, timeout_s=180.0):
        self.log.append(("wait", cue))

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
        return {"status": "ok", "pack": "Mu Test", "autosave_written": True}


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
    assert driver.log[0] == ("start", "CAREER-TUNE-CHK1", None)
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
                           "pack", "autosave_written"}
    assert report["minutes"] >= 0
    for segment in report["segments"]:
        assert set(segment) == {"name", "cue", "recorded_s", "frames", "checks", "summary"}
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
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["segments"][0]["cue"] == "supersim_menu"


def test_a_missed_cue_stops_the_run_without_a_report(mod):
    root, folder = mod
    class Missing(FakeDriver):
        def wait_for(self, cue, timeout_s=180.0):
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
    assert ("wait", "halftime_start") in driver.log


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


# ---------------------------------------------------------------- the real driver's stops
def test_pilot_driver_maps_a_segment_cue_to_watch_game_stops(monkeypatch):
    """A cue names a moment; `watch-game --to` names the stop it drives to."""
    driver = runner.PilotDriver()
    driver.start("CAREER-TUNE-CHKA")
    calls = []
    monkeypatch.setattr(driver, "_call", lambda *args, **kw: calls.append(args) or {"status": "ok"})
    driver.wait_for("kickoff")
    driver.wait_for("halftime_start")
    assert calls == [("watch-game", "--save", "CAREER-TUNE-CHKA", "--to", "kickoff"),
                     ("watch-game", "--save", "CAREER-TUNE-CHKA", "--to", "halftime")]
    with pytest.raises(runner.CueMissed):
        driver.wait_for("supersim_menu")        # a fixed-wait segment is never waited for
