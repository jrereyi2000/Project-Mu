"""Offline tests for the Madden pilot Windows driver (tools/madden_pilot/win.py, win_agent.py).

The modules are loaded by file path (tools/madden_pilot/__init__.py belongs to another unit), and
win_agent's Windows-only modules are stubbed in sys.modules. No Windows, no Madden, no database.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

PILOT = Path(__file__).resolve().parents[1] / "tools" / "madden_pilot"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- win.call

FAKE_PY = """#!{exe}
import json, sys, time
cmd = sys.argv[2] if len(sys.argv) > 2 else ""
if cmd == "echo":
    print("some native library noise")
    print(json.dumps({{"ok": True, "argv": sys.argv[1:]}}))
elif cmd == "fail":
    print(json.dumps({{"ok": False, "error": "nope"}})); sys.exit(1)
elif cmd == "exit3":
    print(json.dumps({{"ok": True}})); sys.exit(3)
elif cmd == "nojson":
    print("hello")
elif cmd == "sleep":
    time.sleep(10)
"""

FAKE_WSLPATH = """#!/bin/sh
# fake `wslpath -w PATH`: C:\\fake + the path with backslashes
p="$2"
printf 'C:\\\\fake%s\\n' "$(printf '%s' "$p" | tr '/' '\\\\')"
"""


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def win(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _executable(bindir / "wslpath", FAKE_WSLPATH)
    py = _executable(tmp_path / "python.exe", FAKE_PY.format(exe=sys.executable))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("MU_WIN_PYTHON", str(py))
    return _load("madden_pilot_win_under_test", PILOT / "win.py")


def test_call_converts_paths_and_parses_last_json_line(win, tmp_path):
    out = tmp_path / "shots" / "new.png"  # parent doesn't exist yet
    res = win.call(["echo", "--title", "Madden NFL 27", "--out", str(out), "--ocr",
                    "--small", tmp_path / "s.png", "--args", "/not/a/path", "-x"])
    argv = res["argv"]
    assert argv[0] == win.to_windows_path(win.AGENT)
    assert argv[0].startswith("C:\\fake\\") and argv[0].endswith("\\win_agent.py")
    assert argv[1:4] == ["echo", "--title", "Madden NFL 27"]
    assert argv[4] == "--out"
    assert argv[5] == "C:\\fake" + str(tmp_path).replace("/", "\\") + "\\shots\\new.png"
    assert argv[6] == "--ocr"
    assert argv[7:9] == ["--small", "C:\\fake" + str(tmp_path / "s.png").replace("/", "\\")]
    assert argv[9:] == ["--args", "/not/a/path", "-x"]  # passed to the started process untouched


def test_windows_paths_pass_through(win):
    assert win.convert_args(["run", "--exe", r"C:\Games\Madden27.exe"]) == ["run", "--exe", r"C:\Games\Madden27.exe"]


def test_convert_args_converts_dir(win, tmp_path):
    out = tmp_path / "clips" / "001"
    args = win.convert_args(["record", "--title", "Madden", "--dir", str(out),
                             "--seconds", "5", "--fps", "2"])
    assert args == ["record", "--title", "Madden", "--dir",
                    "C:\\fake" + str(out).replace("/", "\\"), "--seconds", "5", "--fps", "2"]


def test_ok_false_raises_with_result(win):
    with pytest.raises(win.WinError, match="nope") as e:
        win.call(["fail"])
    assert e.value.result == {"ok": False, "error": "nope"}


def test_nonzero_exit_raises(win):
    with pytest.raises(win.WinError, match="exited 3"):
        win.call(["exit3"])


def test_no_json_raises(win):
    with pytest.raises(win.WinError, match="no JSON"):
        win.call(["nojson"])


def test_timeout_raises(win):
    with pytest.raises(win.WinError, match="timed out"):
        win.call(["sleep"], timeout=0.5)


def test_missing_interpreter_raises(win, tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIN_PYTHON", str(tmp_path / "nope" / "python.exe"))
    with pytest.raises(win.WinError, match="not found"):
        win.call(["windows"])


def test_default_interpreter(win, monkeypatch):
    monkeypatch.delenv("MU_WIN_PYTHON")
    assert win.python_path() == "/mnt/c/Madden27Modding/MuPilot/venv/Scripts/python.exe"


# ---------------------------------------------------------------- win_agent (Windows modules stubbed)

@pytest.fixture
def agent(monkeypatch):
    win32api = types.ModuleType("win32api")
    win32api.GetCursorPos = lambda: (500, 500)
    win32api.GetSystemMetrics = lambda i: 0
    win32api.keybd_event = lambda *a: None
    win32con = types.ModuleType("win32con")
    win32con.GWL_STYLE = -16
    win32con.SW_RESTORE = 9
    for name, mod in {"win32api": win32api, "win32con": win32con,
                      "win32gui": types.ModuleType("win32gui"),
                      "win32process": types.ModuleType("win32process")}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    mod = _load("madden_pilot_win_agent_under_test", PILOT / "win_agent.py")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def _window(foreground=True, elevated=False):
    return {"hwnd": 7, "title": "Madden NFL 27", "pid": 1, "exe": "Madden27.exe", "rect": [0, 0, 2560, 1440],
            "foreground": foreground, "minimized": not foreground, "elevated": elevated, "style": "0x14000000"}


def _run(agent, capsys, argv):
    code = agent.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _fast_wait(agent, monkeypatch):
    """Fake agent clock: each sleep jumps 10 s, so ensure_foreground's 20 s wait ends at once."""
    clock = {"now": 0.0}
    monkeypatch.setattr(agent, "time", types.SimpleNamespace(
        sleep=lambda s: clock.__setitem__("now", clock["now"] + 10.0),
        monotonic=lambda: clock["now"]))


def test_keys_map(agent):
    required = ["up", "down", "left", "right", "space", "backspace", "enter", "esc", "tab",
                "pgup", "pgdn", "lshift", "lctrl"]
    required += [chr(c) for c in range(ord("a"), ord("z") + 1)]
    required += [str(d) for d in range(10)] + [f"f{n}" for n in range(1, 13)]
    assert sorted(agent.KEYS) == sorted(required)
    extended = {n for n, (_, ext) in agent.KEYS.items() if ext}
    assert extended == {"up", "down", "left", "right", "pgup", "pgdn"}
    codes = [sc for sc, _ in agent.KEYS.values()]
    assert len(codes) == len(set(codes))
    spot = {"up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D, "space": 0x39, "backspace": 0x0E,
            "enter": 0x1C, "esc": 0x01, "tab": 0x0F, "a": 0x1E, "e": 0x12, "q": 0x10, "z": 0x2C, "c": 0x2E,
            "1": 0x02, "9": 0x0A, "0": 0x0B, "f1": 0x3B, "f10": 0x44, "f11": 0x57, "f12": 0x58,
            "pgup": 0x49, "pgdn": 0x51, "lshift": 0x2A, "lctrl": 0x1D}
    assert {n: agent.KEYS[n][0] for n in spot} == spot


@pytest.mark.parametrize("name", ["Madden27.exe", "madden27.EXE", "MMCModManager.exe", "mmcmodmanager.exe"])
def test_kill_allow_list_accepts(agent, name):
    assert agent.check_kill_allowed(name).lower() == name.lower()


@pytest.mark.parametrize("name", ["notepad.exe", "explorer.exe", "Madden27.exe.bak", "Madden27", "EADesktop.exe",
                                  "EAAntiCheat.GameServiceLauncher.exe", "python.exe", ""])
def test_kill_refuses_other_exes(agent, capsys, monkeypatch, name):
    with pytest.raises(agent.AgentError):
        agent.check_kill_allowed(name)

    def boom(*a, **k):
        raise AssertionError("must not run any process")

    monkeypatch.setattr(agent.subprocess, "run", boom)
    code, res = _run(agent, capsys, ["kill", "--name", name])
    assert code == 1 and res["ok"] is False and "refusing" in res["error"]


@pytest.fixture
def keyrig(agent, monkeypatch):
    """Mocks the window/focus/input layer; records presses and focus attempts."""
    state = {"foreground": True, "refocus_works": True, "corner": False, "elevated": False,
             "sent": [], "focus_calls": 0}

    def find_window(title_re):
        w = _window(state["foreground"], state["elevated"])
        if w["elevated"] is not False:
            raise agent.AgentError("refusing window: elevated", window=w)
        return w

    def focus_window(hwnd):
        state["focus_calls"] += 1
        if state["refocus_works"]:
            state["foreground"] = True
        return state["foreground"]

    monkeypatch.setattr(agent, "find_window", find_window)
    monkeypatch.setattr(agent, "focus_window", focus_window)
    monkeypatch.setattr(agent, "in_abort_corner", lambda: state["corner"])
    monkeypatch.setattr(agent, "send_scan", lambda name, hold: state["sent"].append((name, hold)))
    return state


def test_key_sends_when_foreground(agent, capsys, keyrig):
    code, res = _run(agent, capsys, ["key", "--name", "Down", "--times", "3", "--hold", "0.1"])
    assert code == 0 and res == {"ok": True, "name": "down", "sent": 3}
    assert keyrig["sent"] == [("down", 0.1)] * 3
    assert keyrig["focus_calls"] == 0


def test_key_refocuses_then_sends(agent, capsys, keyrig):
    keyrig["foreground"] = False
    code, res = _run(agent, capsys, ["key", "--name", "space"])
    assert code == 0 and res["ok"] is True
    assert keyrig["focus_calls"] == 1 and keyrig["sent"] == [("space", 0.08)]


def test_key_refuses_when_still_not_foreground(agent, capsys, keyrig, monkeypatch):
    keyrig["foreground"] = False
    keyrig["refocus_works"] = False
    _fast_wait(agent, monkeypatch)
    code, res = _run(agent, capsys, ["key", "--name", "space"])
    assert code == 1 and res["ok"] is False and "foreground" in res["error"]
    assert res["sent"] == 0
    assert keyrig["focus_calls"] >= 2 and keyrig["sent"] == []  # retries until the wait is over


def test_key_refuses_in_abort_corner(agent, capsys, keyrig):
    keyrig["corner"] = True
    code, res = _run(agent, capsys, ["key", "--name", "down"])
    assert code == 1 and res["ok"] is False and res.get("aborted") is True
    assert keyrig["sent"] == [] and keyrig["focus_calls"] == 0


def test_key_checks_before_every_press(agent, capsys, keyrig, monkeypatch):
    def send(name, hold):
        keyrig["sent"].append(name)
        if len(keyrig["sent"]) == 2:
            keyrig["corner"] = True  # JR moves the mouse to the corner mid-run

    monkeypatch.setattr(agent, "send_scan", send)
    code, res = _run(agent, capsys, ["key", "--name", "down", "--times", "5"])
    assert code == 1 and res["sent"] == 2 and keyrig["sent"] == ["down", "down"]


def test_key_refuses_elevated_window(agent, capsys, keyrig):
    keyrig["elevated"] = True
    code, res = _run(agent, capsys, ["key", "--name", "down"])
    assert code == 1 and "elevated" in res["error"] and keyrig["sent"] == []


def test_key_rejects_unknown_key_and_bad_counts(agent, capsys, keyrig):
    assert _run(agent, capsys, ["key", "--name", "f13"])[1]["ok"] is False
    assert _run(agent, capsys, ["key", "--name", "down", "--times", "0"])[1]["ok"] is False
    assert _run(agent, capsys, ["key", "--name", "down", "--hold", "60"])[1]["ok"] is False
    assert keyrig["sent"] == []


@pytest.mark.parametrize("elevated", [True, None])
def test_find_window_refuses_elevated_or_unknown(agent, monkeypatch, elevated):
    monkeypatch.setattr(agent, "list_windows", lambda t: [_window(elevated=elevated)])
    with pytest.raises(agent.AgentError, match="elevated"):
        agent.find_window("Madden")


def test_find_window_prefers_madden_exe(agent, monkeypatch):
    term = {**_window(), "hwnd": 9, "exe": "WindowsTerminal.exe", "title": "...\\Madden NFL 27\\x.exe"}
    monkeypatch.setattr(agent, "list_windows", lambda t: [term, _window()])
    assert agent.find_window("Madden NFL 27")["hwnd"] == 7


def test_look_refuses_when_not_foreground(agent, capsys, keyrig, monkeypatch, tmp_path):
    keyrig["foreground"] = False
    keyrig["refocus_works"] = False
    _fast_wait(agent, monkeypatch)
    monkeypatch.setattr(agent, "capture", lambda *a: (_ for _ in ()).throw(AssertionError("no capture")))
    code, res = _run(agent, capsys, ["look", "--title", "Madden", "--out", str(tmp_path / "x.png")])
    assert code == 1 and res["ok"] is False and keyrig["focus_calls"] >= 2


@pytest.mark.parametrize("pos,virtual,expected", [
    ((0, 0), (0, 0), True), ((5, 5), (0, 0), True), ((6, 0), (0, 0), False), ((500, 500), (0, 0), False),
    ((-1918, 3), (-1920, 0), True), ((-1900, 3), (-1920, 0), False),
])
def test_abort_corner(agent, monkeypatch, pos, virtual, expected):
    monkeypatch.setattr(agent.win32api, "GetCursorPos", lambda: pos)
    monkeypatch.setattr(agent.win32api, "GetSystemMetrics", lambda i: {76: virtual[0], 77: virtual[1]}[i])
    assert agent.in_abort_corner() is expected


# ---------------------------------------------------------------- win_agent record

def _fake_mss(monkeypatch, size=(1600, 900), on_grab=None):
    """Install a fake ``mss`` module. Returns the grab regions; ``on_grab(n)`` runs after each."""
    grabs = []

    class Session:
        def grab(self, region):
            grabs.append(region)
            if on_grab:
                on_grab(len(grabs))
            im = Image.new("RGB", size, (10, 80, 140))
            return types.SimpleNamespace(size=size, bgra=im.tobytes("raw", "BGRX"))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    mod = types.ModuleType("mss")
    mod.mss = Session
    monkeypatch.setitem(sys.modules, "mss", mod)
    return grabs


@pytest.mark.parametrize("value", ["0", "31"])
def test_record_refuses_bad_seconds(agent, capsys, keyrig, value):
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", "D",
                                     "--seconds", value, "--fps", "5"])
    assert code == 1 and res["ok"] is False and "--seconds" in res["error"]


def test_record_refuses_non_numeric_seconds(agent, capsys):
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", "D",
                                     "--seconds", "soon", "--fps", "5"])
    assert code == 2 and res["ok"] is False


@pytest.mark.parametrize("value", ["0", "11"])
def test_record_refuses_bad_fps(agent, capsys, keyrig, value):
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", "D",
                                     "--seconds", "1", "--fps", value])
    assert code == 1 and res["ok"] is False and "--fps" in res["error"]


def test_record_writes_expected_jpegs(agent, capsys, keyrig, monkeypatch, tmp_path):
    grabs = _fake_mss(monkeypatch)
    out = tmp_path / "clip"
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", str(out),
                                     "--seconds", "1", "--fps", "3", "--width", "320"])
    assert code == 0 and res["ok"] is True
    assert res["dir"] == str(out) and res["frames"] == 3
    assert res["size"] == [320, 180]
    assert isinstance(res["fps_actual"], float) and res["fps_actual"] > 0
    files = sorted(out.glob("frame_*.jpg"))
    assert [f.name for f in files] == ["frame_0000.jpg", "frame_0001.jpg", "frame_0002.jpg"]
    for f in files:
        with Image.open(f) as im:
            assert im.format == "JPEG" and im.size == (320, 180)
    assert grabs == [{"left": 0, "top": 0, "width": 2560, "height": 1440}] * 3


def test_record_refocuses_before_grabbing(agent, capsys, keyrig, monkeypatch, tmp_path):
    keyrig["foreground"] = False
    _fake_mss(monkeypatch)
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", str(tmp_path / "clip"),
                                     "--seconds", "1", "--fps", "1"])
    assert code == 0 and res["frames"] == 1 and keyrig["focus_calls"] == 1


def test_record_aborts_partway(agent, capsys, keyrig, monkeypatch, tmp_path):
    grabs = _fake_mss(monkeypatch, on_grab=lambda n: keyrig.__setitem__("corner", n >= 2))
    out = tmp_path / "clip"
    code, res = _run(agent, capsys, ["record", "--title", "Madden", "--dir", str(out),
                                     "--seconds", "2", "--fps", "5"])
    assert code == 1 and res["ok"] is False and res.get("aborted") is True
    assert res["frames"] == 2 and res["dir"] == str(out)
    assert [f.name for f in sorted(out.glob("frame_*.jpg"))] == ["frame_0000.jpg", "frame_0001.jpg"]
    assert len(grabs) == 2
