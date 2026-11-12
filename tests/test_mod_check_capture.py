"""Visual mod check capture: the pass/native reference pair (VISUAL_MOD_CHECK_PLAN 2.3/R3).

A two-frame fixture clip proves the timestamps pick the right frames; a pair the margin cannot
separate is refused instead of saved. No PC, no model, no database.
"""
import json
from pathlib import Path

import pytest
from PIL import Image

from tools.mod_check import runner
from tools.mod_check.__main__ import main
from tools.mod_check.runner import capture, frame_at, parse_timestamp
from tools.mod_check.segments import PlanError

RED, BLUE, GREY = (255, 0, 0), (0, 0, 255), (47, 47, 47)


def write_png(path: Path, colour, size=(64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def centre_colour(path: Path):
    with Image.open(path) as opened:
        return opened.convert("RGB").getpixel((opened.width // 2, opened.height // 2))


@pytest.fixture
def mod(tmp_path):
    """data/mod_checks/demo with one reference spec whose pair has not been captured yet."""
    root = tmp_path / "data"
    folder = root / "demo"
    folder.mkdir(parents=True)
    (folder / "checks.json").write_text(json.dumps({
        "ball": {"region": [0.0, 0.0, 1.0, 1.0],
                 "reference": {"pass": "refs/ball_pass.png", "fail": "refs/ball_native.png",
                               "margin": 0.05}},
    }), encoding="utf-8")
    (folder / "segments.json").write_text(json.dumps({"pack": "Mu Test", "segments": [
        {"name": "game_start", "after": "kickoff", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["ball"]},
    ]}), encoding="utf-8")
    return root, folder


@pytest.fixture
def clip(tmp_path):
    """A two-frame clip at 1 fps: frame 0 is the mod, frame 1 is native."""
    folder = tmp_path / "clip"
    return [write_png(folder / "frame_0000.png", RED),
            write_png(folder / "frame_0001.png", BLUE)]


def test_timestamps_pick_the_right_frames(clip):
    assert frame_at(clip, 1, 0.0) == clip[0]
    assert frame_at(clip, 1, 1.0) == clip[1]
    with pytest.raises(PlanError) as caught:
        frame_at(clip, 1, 2.0)
    assert "past the end" in str(caught.value)


def test_parse_timestamp_reads_m_ss():
    assert parse_timestamp("0:03") == 3
    assert parse_timestamp("1:30") == 90
    assert parse_timestamp("12") == 12
    for bad in ("", "abc", "1:2:3", "-0:01"):
        with pytest.raises(PlanError):
            parse_timestamp(bad)


def test_capture_previews_without_writing_a_pair(mod, clip):
    root, folder = mod
    result = capture("demo", "game_start", "0:00", "0:01", frames=clip, data_dir=root)
    assert result["status"] == "ok"
    assert result["saved"] is False
    assert result["verdicts"] == {"pass": "pass", "fail": "fail"}
    assert Path(result["preview"]["pass"]).is_file()
    assert not (folder / "refs/ball_pass.png").exists()
    assert not (folder / "refs/ball_native.png").exists()


def test_capture_writes_the_pair_on_yes(mod, clip):
    root, folder = mod
    result = capture("demo", "game_start", "0:00", "0:01", frames=clip, yes=True, data_dir=root)
    assert result["status"] == "ok"
    assert result["saved"] is True
    pass_path = Path(result["paths"]["pass"])
    native_path = Path(result["paths"]["native"])
    assert pass_path == folder / "refs/ball_pass.png"
    assert native_path == folder / "refs/ball_native.png"
    assert centre_colour(pass_path) == RED          # 0:00 is the mod frame
    assert centre_colour(native_path) == BLUE       # 0:01 is the native frame
    numbers = result["similarities"]["pass"]
    assert numbers["similarity_pass"] > numbers["similarity_fail"]
    assert result["crossed"]["pass_frame_vs_pass"] > result["crossed"]["pass_frame_vs_fail"]
    assert result["crossed"]["fail_frame_vs_fail"] > result["crossed"]["fail_frame_vs_pass"]


def test_capture_refuses_a_pair_inside_the_margin(mod):
    root, folder = mod
    frames = [write_png(folder.parent / "grey0.png", GREY),
              write_png(folder.parent / "grey1.png", GREY)]
    result = capture("demo", "game_start", "0:00", "0:01", frames=frames, yes=True,
                     data_dir=root)
    assert result["status"] == "refused"
    assert result["saved"] is False
    assert "inside the margin" in result["why"]
    assert not (folder / "refs/ball_pass.png").exists()
    assert not (folder / "refs/ball_native.png").exists()


def test_capture_refuses_a_moment_past_the_clip(mod, clip):
    root, folder = mod
    with pytest.raises(PlanError) as caught:
        capture("demo", "game_start", "0:00", "0:05", frames=clip, yes=True, data_dir=root)
    assert "past the end" in str(caught.value)
    assert not (folder / "refs/ball_pass.png").exists()


def test_capture_loads_a_previous_runs_frames(mod):
    root, folder = mod
    run_dir = root / "runs" / "2026-11-12_120000_demo"
    frames_dir = run_dir / "segments" / "game_start" / "frames"
    frames_dir.mkdir(parents=True)
    write_png(frames_dir / "frame_0000.png", RED)
    write_png(frames_dir / "frame_0001.png", BLUE)
    result = capture("demo", "game_start", "0:00", "0:01", yes=True, run_dir=run_dir,
                     data_dir=root)
    assert result["saved"] is True
    assert centre_colour(folder / "refs/ball_pass.png") == RED
    assert centre_colour(folder / "refs/ball_native.png") == BLUE


def test_capture_refuses_with_no_frames_and_no_save(mod):
    root, folder = mod
    result = capture("demo", "game_start", "0:00", "0:01", data_dir=root)
    assert result["status"] == "refused"
    assert "no frames" in result["why"]


def test_capture_refuses_an_unknown_asset(mod, clip):
    root, folder = mod
    with pytest.raises(PlanError) as caught:
        capture("demo", "game_start", "0:00", "0:01", frames=clip, asset="not_a_check",
                data_dir=root)
    assert "no check named" in str(caught.value)


def test_capture_refuses_a_reference_outside_the_check_folder(mod, clip):
    root, folder = mod
    (folder / "checks.json").write_text(json.dumps({
        "ball": {"region": [0.0, 0.0, 1.0, 1.0],
                 "reference": {"pass": "../../outside_pass.png",
                               "fail": "../../outside_native.png", "margin": 0.05}},
    }), encoding="utf-8")
    with pytest.raises(PlanError) as caught:
        capture("demo", "game_start", "0:00", "0:01", frames=clip, yes=True, data_dir=root)
    assert "inside" in str(caught.value)


def test_the_capture_cli_writes_the_pair_from_a_run_dir(mod, capsys):
    root, folder = mod
    run_dir = root / "runs" / "2026-11-12_120000_demo"
    frames_dir = run_dir / "segments" / "game_start" / "frames"
    frames_dir.mkdir(parents=True)
    write_png(frames_dir / "frame_0000.png", RED)
    write_png(frames_dir / "frame_0001.png", BLUE)
    code = main(["capture", "--mod", "demo", "--segment", "game_start",
                 "--pass-at", "0:00", "--fail-at", "0:01", "--yes",
                 "--run-dir", str(run_dir), "--data-dir", str(root)])
    output = capsys.readouterr().out
    assert code == 0
    assert "similarity_pass=" in output
    assert "wrote pass:" in output
    assert centre_colour(folder / "refs/ball_pass.png") == RED


def test_the_capture_cli_previews_without_yes(mod, capsys):
    root, folder = mod
    run_dir = root / "runs" / "2026-11-12_120000_demo"
    frames_dir = run_dir / "segments" / "game_start" / "frames"
    frames_dir.mkdir(parents=True)
    write_png(frames_dir / "frame_0000.png", RED)
    write_png(frames_dir / "frame_0001.png", BLUE)
    code = main(["capture", "--mod", "demo", "--segment", "game_start",
                 "--pass-at", "0:00", "--fail-at", "0:01",
                 "--run-dir", str(run_dir), "--data-dir", str(root)])
    output = capsys.readouterr().out
    assert code == 0
    assert "nothing written" in output
    assert not (folder / "refs/ball_pass.png").exists()


# ---------------------------------------------------------------- the live capture lifecycle (F5)
class LiveDriver:
    """A recording driver that logs every call and can fail at one named point."""

    def __init__(self, frames=(RED, BLUE), fail_wait=False, fail_finish=False,
                 finish_state=None):
        self.calls = []
        self.frames = frames
        self.fail_wait = fail_wait
        self.fail_finish = fail_finish
        self.finish_state = finish_state

    def start(self, save, pack=None):
        self.calls.append(("start", save, pack))
        return {"status": "ok", "save": save, "pack": pack}

    def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
        self.calls.append(("wait", cue, hold_s))
        if self.fail_wait:
            raise runner.CueMissed(cue, "never reached its ready marker")

    def record(self, out_dir, seconds, fps, width):
        self.calls.append(("record", seconds, fps, width))
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for index, colour in enumerate(self.frames):
            path = folder / f"frame_{index:04d}.png"
            Image.new("RGB", (64, 64), colour).save(path)
            written.append(path)
        return written

    def finish(self):
        self.calls.append(("finish",))
        if self.fail_finish:
            raise runner.DriverError("quit failed; game still open")
        return self.finish_state or {"status": "ok", "quit": True,
                                     "quit_route": "quit-game",
                                     "end_screen": "FRANCHISE_LIST",
                                     "autosave_written": True}


def live_capture(root, driver, **kwargs):
    kwargs.setdefault("save", "CAREER-TUNE-X")
    kwargs.setdefault("launch", lambda pack: {"status": "ok", "pack": pack,
                                              "already_running": False})
    kwargs.setdefault("watcher", lambda: False)
    kwargs.setdefault("clone", lambda src, prefix: "CAREER-TUNE-CHKA")
    kwargs.setdefault("sleep", lambda seconds: None)
    return capture("demo", "game_start", kwargs.pop("pass_at", "0:00"),
                   kwargs.pop("fail_at", "0:01"), data_dir=root, driver=driver, **kwargs)


def test_a_live_capture_starts_and_finishes_exactly_once(mod):
    root, folder = mod
    driver = LiveDriver()
    result = live_capture(root, driver, yes=True)
    assert result["status"] == "ok" and result["saved"] is True
    assert [call[0] for call in driver.calls] == ["start", "wait", "record", "finish"]
    assert result["operational"]["status"] == "complete"
    assert result["pack_verified"] is True
    assert centre_colour(folder / "refs/ball_pass.png") == RED


def test_a_live_capture_with_an_empty_recording_still_finishes(mod):
    root, folder = mod
    driver = LiveDriver(frames=())
    result = live_capture(root, driver)
    assert result["status"] == "refused" and "no frames" in result["why"]
    assert [call[0] for call in driver.calls] == ["start", "wait", "record", "finish"]


def test_a_live_capture_whose_cue_is_missed_still_finishes(mod):
    root, folder = mod
    driver = LiveDriver(fail_wait=True)
    result = live_capture(root, driver)
    assert result["status"] == "stopped" and result["stop"] == "cue_missed"
    assert [call[0] for call in driver.calls] == ["start", "wait", "finish"]
    assert result["operational"]["status"] == "complete"


def test_a_live_capture_finishes_before_a_bad_timestamp_is_raised(mod):
    root, folder = mod
    driver = LiveDriver()
    with pytest.raises(PlanError) as caught:
        live_capture(root, driver, fail_at="0:05")
    assert "past the end" in str(caught.value)
    assert [call[0] for call in driver.calls] == ["start", "wait", "record", "finish"]


def test_a_live_capture_finishes_before_a_margin_refusal(mod):
    root, folder = mod
    driver = LiveDriver(frames=(GREY, GREY))
    result = live_capture(root, driver, yes=True)
    assert result["status"] == "refused" and "inside the margin" in result["why"]
    assert driver.calls[-1] == ("finish",)
    assert not (folder / "refs/ball_pass.png").exists()


def test_a_live_capture_surfaces_a_failed_quit(mod):
    root, folder = mod
    driver = LiveDriver(fail_finish=True)
    result = live_capture(root, driver, yes=True)
    assert result["status"] == "ok" and result["saved"] is True
    assert result["operational"]["status"] == "cleanup_failed"
    assert "game still open" in result["operational"]["why"]


def test_a_live_capture_refuses_a_clone_that_is_the_source(mod):
    root, folder = mod
    driver = LiveDriver()
    result = live_capture(root, driver, clone=lambda src, prefix: src)
    assert result["status"] == "refused" and "never the one it was given" in result["why"]
    assert driver.calls == []


def test_a_live_capture_refuses_an_unverified_launch_before_recording(mod):
    root, folder = mod
    driver = LiveDriver()
    result = live_capture(root, driver, launch=lambda pack: {
        "status": "ok", "pack": "Mu Base", "already_running": False})
    assert result["status"] == "stopped" and result["stop"] == "launch"
    assert driver.calls == []


def test_offline_capture_never_creates_or_finishes_a_driver(mod, monkeypatch):
    root, folder = mod
    def boom(*args, **kwargs):
        raise AssertionError("offline capture must not create a driver")
    monkeypatch.setattr(runner, "PilotDriver", boom)
    result = capture("demo", "game_start", "0:00", "0:01",
                     frames=[write_png(folder.parent / "r0.png", RED),
                             write_png(folder.parent / "r1.png", BLUE)],
                     data_dir=root)
    assert result["status"] == "ok" and "operational" not in result


def test_the_capture_cli_returns_2_for_an_incomplete_live_run(mod, monkeypatch, capsys):
    root, folder = mod
    monkeypatch.setattr(runner, "capture", lambda *a, **k: {
        "status": "ok", "saved": True,
        "operational": {"status": "cleanup_failed", "why": "game still open",
                        "recovery": "leave it by hand"}})
    code = main(["capture", "--mod", "demo", "--segment", "game_start",
                 "--pass-at", "0:00", "--fail-at", "0:01", "--yes", "--data-dir", str(root)])
    output = capsys.readouterr()
    assert code == 2
    assert "INCOMPLETE: game still open" in output.err


def test_a_live_capture_refuses_an_occupied_run_dir_before_recording(mod):
    root, folder = mod
    run_dir = root / "runs" / "2026-11-12_120000_demo"
    frames_dir = run_dir / "segments" / "game_start" / "frames"
    frames_dir.mkdir(parents=True)
    write_png(frames_dir / "frame_0000.png", RED)
    write_png(frames_dir / "frame_0001.png", BLUE)
    driver = LiveDriver()
    result = live_capture(root, driver, run_dir=run_dir, yes=True)
    assert result["status"] == "refused" and "not fresh" in result["why"]
    assert driver.calls == []


def test_a_live_capture_with_save_ignores_an_old_runs_frames(mod):
    root, folder = mod
    run_dir = root / "runs" / "2026-11-12_120000_demo"
    frames_dir = run_dir / "segments" / "game_start" / "frames"
    frames_dir.mkdir(parents=True)
    write_png(frames_dir / "frame_0000.png", GREY)
    write_png(frames_dir / "frame_0001.png", GREY)
    driver = LiveDriver()
    result = live_capture(root, driver, run_dir=run_dir, yes=True)
    assert result["status"] == "refused"          # the occupied directory is refused first
    assert driver.calls == []
    fresh = root / "captures" / "fresh"
    result = live_capture(root, driver, run_dir=fresh, yes=True)
    assert result["status"] == "ok" and [c[0] for c in driver.calls] == [
        "start", "wait", "record", "finish"]
    assert centre_colour(folder / "refs/ball_pass.png") == RED


def test_a_live_supersim_capture_stops_before_recording_when_its_screen_is_not_reached(mod):
    root, folder = mod
    (folder / "segments.json").write_text(json.dumps({"pack": "Mu Test", "segments": [
        {"name": "supersim", "after": "supersim_menu", "record_s": 2, "fps": 1, "width": 640,
         "checks": ["ball"]},
    ]}), encoding="utf-8")
    driver = LiveDriver(fail_wait=True)
    result = capture("demo", "supersim", "0:00", "0:01", save="CAREER-TUNE-X", driver=driver,
                     launch=lambda pack: {"status": "ok", "pack": pack,
                                          "already_running": False},
                     watcher=lambda: False, clone=lambda src, prefix: "CAREER-TUNE-CHKA",
                     sleep=lambda s: None, data_dir=root)
    assert result["status"] == "stopped" and result["stop"] == "cue_missed"
    assert [call[0] for call in driver.calls] == ["start", "wait", "finish"]
    assert not (root / "captures").exists() or not list(
        (root / "captures").rglob("previews/*.png"))
