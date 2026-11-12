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
    (folder / "segments.json").write_text(json.dumps({"segments": [
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
