"""Windows side of the Madden pilot driver (MADDEN_PILOT_AND_TUNING_PLAN §2.1).

Runs under the Windows venv ``C:\\Madden27Modding\\MuPilot\\venv\\Scripts\\python.exe`` (packages in
``requirements-win.txt``); WSL code calls it through ``tools/madden_pilot/win.py``. Each command prints
one JSON line and exits 0 when ``"ok": true``, 1 otherwise:

    windows [--title RE]                                  list visible top-level windows
    focus   --title RE                                    bring a matching window to the front
    look    --title RE --out PNG [--ocr] [--small PNG]    capture the client area (+ Windows OCR)
    key     --name NAME [--hold S] [--times N] [--gap S] [--title RE]   scan-code SendInput
    run     --exe PATH [--args ...]                       start a process (never elevated)
    kill    --name EXE                                    end Madden27.exe / MMCModManager.exe only

Safety rules (spike P-a, plan R8):
- Madden minimizes when it loses focus, so ``look`` and ``key`` check the target is the foreground
  window first and refocus it if not (restore + Alt tap + SetForegroundWindow). ``key`` refuses when it
  still isn't foreground afterwards.
- No command acts on a window whose process reports elevated (or whose elevation can't be read).
- ``key`` refuses while the mouse is within 5 px of the top-left screen corner (JR's abort gesture);
  the check runs before every press.

This module imports nothing from the repo; it is run as a script by file path.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

_windll = getattr(ctypes, "windll", None)
user32 = _windll.user32 if _windll else None
kernel32 = _windll.kernel32 if _windll else None
advapi32 = _windll.advapi32 if _windll else None

MADDEN_TITLE = r"^Madden NFL 27$"
KILL_ALLOW = ("Madden27.exe", "MMCModManager.exe")
ABORT_CORNER_PX = 5
FOCUS_SETTLE_S = 1.5
MAX_TIMES = 100
MAX_HOLD_S = 10.0
SMALL_WIDTH = 960

# name -> (hardware scan code, extended). Games read raw input, so virtual-key-only events are ignored.
KEYS: dict[str, tuple[int, bool]] = {
    "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True),
    "pgup": (0x49, True), "pgdn": (0x51, True),
    "space": (0x39, False), "backspace": (0x0E, False), "enter": (0x1C, False),
    "esc": (0x01, False), "tab": (0x0F, False),
    "lshift": (0x2A, False), "lctrl": (0x1D, False),
}
KEYS.update({ch: (sc, False) for ch, sc in zip(
    "abcdefghijklmnopqrstuvwxyz",
    (0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26, 0x32,
     0x31, 0x18, 0x19, 0x10, 0x13, 0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C))})
KEYS.update({str(d): (0x02 + d - 1 if d else 0x0B, False) for d in range(10)})
KEYS.update({f"f{n}": (0x3B + n - 1, False) for n in range(1, 11)})
KEYS.update({"f11": (0x57, False), "f12": (0x58, False)})

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


class AgentError(Exception):
    """A refusal or failure; becomes ``{"ok": false, "error": ...}``."""

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


# ---------------------------------------------------------------- processes and windows

def elevated(pid: int) -> bool | None:
    """True/False from the process token's TokenElevation; None when it can't be read."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            val, ret = wintypes.DWORD(), wintypes.DWORD()
            if not advapi32.GetTokenInformation(tok, TOKEN_ELEVATION, ctypes.byref(val), 4, ctypes.byref(ret)):
                return None
            return bool(val.value)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def exe_name(pid: int) -> str | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)


def _window_info(hwnd: int) -> dict:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    return {
        "hwnd": hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "pid": pid,
        "exe": exe_name(pid),
        "rect": [x, y, right - left, bottom - top],
        "foreground": hwnd == win32gui.GetForegroundWindow(),
        "minimized": bool(win32gui.IsIconic(hwnd)),
        "elevated": elevated(pid),
        "style": hex(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & 0xFFFFFFFF),
    }


def list_windows(title_re: str | None = None) -> list[dict]:
    """Visible top-level windows with a title, optionally filtered by a case-insensitive regex."""
    pattern = re.compile(title_re, re.IGNORECASE) if title_re else None
    hwnds: list[int] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and (pattern is None or pattern.search(title)):
                hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return [_window_info(h) for h in hwnds]


def find_window(title_re: str) -> dict:
    """The one window to act on: the first match, preferring Madden27.exe's own window.
    Refuses elevated (or unreadable) windows."""
    matches = list_windows(title_re)
    if not matches:
        raise AgentError(f"no window matches {title_re!r}")
    matches.sort(key=lambda w: (w.get("exe") or "").lower() != "madden27.exe")
    w = matches[0]
    if w.get("elevated") is not False:
        raise AgentError(f"refusing window {w['title']!r}: elevated={w.get('elevated')}", window=w)
    return w


def focus_window(hwnd: int) -> bool:
    """Restore + Alt tap + SetForegroundWindow (Windows only hands over the foreground right
    after input). Returns whether it is the foreground window afterwards."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32api.keybd_event(0x12, 0, 0, 0)
    win32api.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - pywin32 raises pywintypes.error; the check below decides
        pass
    time.sleep(FOCUS_SETTLE_S)
    return win32gui.GetForegroundWindow() == hwnd


def ensure_foreground(title_re: str) -> dict:
    """Find the window and make sure it's in front, refocusing once if not. Returns the fresh
    window info (its rect changes after a restore); raises when it still isn't foreground."""
    w = find_window(title_re)
    if w["foreground"] and not w["minimized"]:
        return w
    focus_window(w["hwnd"])
    w = find_window(title_re)
    if not w["foreground"]:
        raise AgentError(f"{w['title']!r} is not the foreground window after refocusing", window=w)
    return w


# ---------------------------------------------------------------- input

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def send_scan(name: str, hold: float) -> None:
    """One press of KEYS[name]: scan-code key down, hold, key up."""
    sc, ext = KEYS[name]
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    for f in (flags, flags | KEYEVENTF_KEYUP):
        inp = INPUT(type=1)
        inp.u.ki = KEYBDINPUT(0, sc, f, 0, None)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise AgentError(f"SendInput failed for {name!r}")
        if not f & KEYEVENTF_KEYUP:
            time.sleep(hold)


def in_abort_corner() -> bool:
    """Mouse within ABORT_CORNER_PX of the primary screen's or the virtual desktop's top-left."""
    x, y = win32api.GetCursorPos()
    origins = {(0, 0), (win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77))}  # SM_X/YVIRTUALSCREEN
    return any(abs(x - ox) <= ABORT_CORNER_PX and abs(y - oy) <= ABORT_CORNER_PX for ox, oy in origins)


# ---------------------------------------------------------------- capture and OCR

def capture(rect: list[int], path: str) -> dict:
    import mss
    from PIL import Image

    x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise AgentError(f"window client area is empty ({rect}); is it minimized?")
    with mss.mss() as s:
        shot = s.grab({"left": x, "top": y, "width": w, "height": h})
    im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    im.save(path)
    lo, hi = im.convert("L").getextrema()
    return {"image": im, "size": list(im.size), "luma_range": [lo, hi]}


async def _ocr(path: str) -> list[dict]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
    stream = await f.open_async(FileAccessMode.READ)
    dec = await BitmapDecoder.create_async(stream)
    bmp = await dec.get_software_bitmap_async()
    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise AgentError("no Windows OCR language available")
    res = await eng.recognize_async(bmp)
    lines = []
    for ln in res.lines:
        words = []
        for wd in ln.words:
            r = wd.bounding_rect
            words.append({"text": wd.text, "box": [round(r.x), round(r.y), round(r.width), round(r.height)]})
        lines.append({"text": ln.text, "words": words})
    return lines


def ocr(path: str) -> tuple[list[str], list[dict]]:
    """(line texts, words) where each word is {"text", "box": [x, y, w, h], "line": index}."""
    lines = asyncio.run(_ocr(path))
    words = [{**wd, "line": i} for i, ln in enumerate(lines) for wd in ln["words"]]
    return [ln["text"] for ln in lines], words


# ---------------------------------------------------------------- commands

def cmd_windows(a) -> dict:
    return {"ok": True, "windows": list_windows(a.title)}


def cmd_focus(a) -> dict:
    w = ensure_foreground(a.title)
    return {"ok": True, "hwnd": w["hwnd"], "window": w}


def cmd_look(a) -> dict:
    w = ensure_foreground(a.title)
    shot = capture(w["rect"], a.out)
    out = {"ok": True, "png": a.out, "size": shot["size"], "luma_range": shot["luma_range"], "window": w}
    if a.small:
        im = shot["image"]
        sw = min(SMALL_WIDTH, im.width)
        im.resize((sw, max(1, round(im.height * sw / im.width)))).save(a.small)
        out["small"] = a.small
    if a.ocr:
        out["lines"], out["words"] = ocr(a.out)
    return out


def cmd_key(a) -> dict:
    name = a.name.lower()
    if name not in KEYS:
        raise AgentError(f"unknown key {a.name!r}", known=sorted(KEYS))
    if not 1 <= a.times <= MAX_TIMES:
        raise AgentError(f"--times must be 1..{MAX_TIMES}")
    if not 0 <= a.hold <= MAX_HOLD_S or a.gap < 0:
        raise AgentError(f"--hold must be 0..{MAX_HOLD_S} s and --gap >= 0")
    sent = 0
    for i in range(a.times):
        if in_abort_corner():
            raise AgentError("abort: mouse is in the top-left screen corner", sent=sent, aborted=True)
        try:
            ensure_foreground(a.title)
        except AgentError as e:
            raise AgentError(f"not sending {name!r}: {e}", sent=sent, **e.extra) from e
        send_scan(name, a.hold)
        sent += 1
        if i + 1 < a.times:
            time.sleep(a.gap)
    return {"ok": True, "name": name, "sent": sent}


def cmd_run(a) -> dict:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen([a.exe, *a.args], cwd=os.path.dirname(a.exe) or None, creationflags=flags,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:  # e.g. WinError 740: the exe demands elevation, which we never grant
        raise AgentError(f"could not start {a.exe}: {e}") from e
    return {"ok": True, "pid": proc.pid}


def check_kill_allowed(name: str) -> str:
    """The allow-listed spelling of ``name`` (case-insensitive), or AgentError."""
    for allowed in KILL_ALLOW:
        if name.lower() == allowed.lower():
            return allowed
    raise AgentError(f"refusing to kill {name!r}: only {', '.join(KILL_ALLOW)}")


def _pids_of(name: str) -> list[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True)
    pids = []
    for line in out.stdout.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) > 1 and cells[0].lower() == name.lower() and cells[1].strip('"').isdigit():
            pids.append(int(cells[1].strip('"')))
    return pids


def cmd_kill(a) -> dict:
    name = check_kill_allowed(a.name)
    pids = _pids_of(name)
    for pid in pids:
        if elevated(pid) is not False:
            raise AgentError(f"refusing to kill elevated (or unreadable) process {name} pid {pid}")
    killed = []
    for pid in pids:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
        if r.returncode != 0:
            raise AgentError(f"taskkill {pid} failed: {(r.stderr or r.stdout).strip()}", killed=killed)
        killed.append(pid)
    return {"ok": True, "name": name, "killed": killed}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="win_agent")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("windows")
    s.add_argument("--title")
    s.set_defaults(fn=cmd_windows)
    s = sub.add_parser("focus")
    s.add_argument("--title", required=True)
    s.set_defaults(fn=cmd_focus)
    s = sub.add_parser("look")
    s.add_argument("--title", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--ocr", action="store_true")
    s.add_argument("--small")
    s.set_defaults(fn=cmd_look)
    s = sub.add_parser("key")
    s.add_argument("--name", required=True)
    s.add_argument("--hold", type=float, default=0.08)
    s.add_argument("--times", type=int, default=1)
    s.add_argument("--gap", type=float, default=0.4)
    s.add_argument("--title", default=MADDEN_TITLE)
    s.set_defaults(fn=cmd_key)
    s = sub.add_parser("run")
    s.add_argument("--exe", required=True)
    s.add_argument("--args", nargs=argparse.REMAINDER, default=[])
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("kill")
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_kill)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        if e.code:
            print(json.dumps({"ok": False, "error": "bad arguments"}))
        return int(e.code or 0)
    try:
        result = a.fn(a)
    except AgentError as e:
        result = {"ok": False, "error": str(e), **e.extra}
    except Exception as e:  # noqa: BLE001 - every failure must still print one JSON line
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(result))
    sys.stdout.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
