#!/usr/bin/env python3
"""
Madden franchise capture over chiaki-ng (macOS).

Navigates Madden's franchise menus through the chiaki-ng Remote Play window,
checking the screen (local Apple Vision OCR) before every press, and saves
screenshots of:
  * Season Stats  (League > Stats & Awards > Player Stats, scope = NFL, every category)
  * Team Stats    (League > Stats & Awards > Team Stats, scope = NFL: Conversions,
                   Red Zone Efficiency, Penalties)
  * Schedule      (League > Schedule > League Schedule, the PREVIOUS week only)

Screenshots land in captures/<YYYY-MM-DD_HHMM>/{stats,team_stats,schedule}/.
Run extract.py afterwards to turn them into CSVs.

Usage
  python3 madden_capture.py --test        # read the current screen only, no key presses
  python3 madden_capture.py --probe-l2    # record how the L2 dropdown behaves (from Player Stats)
  python3 madden_capture.py               # full run: stats + schedule
  python3 madden_capture.py --only stats  # or: --only team, --only schedule
  python3 madden_capture.py --week "SEASON WEEK 12"   # capture a specific schedule week
  python3 madden_capture.py --step down   # mapping: press one key, save screenshot + OCR (--step none: capture only)

Key map (chiaki-ng "Keyboard as controller"):
  Cross=S  Circle=D  L1=2  R1=3  L2=1  R2=4  D-pad=arrow keys  Escape=Esc
"""
import argparse, datetime, glob, hashlib, json, os, re, shutil, subprocess, sys, time

import Quartz
import Vision
from Foundation import NSURL
from PIL import Image, ImageStat

# ---------------------------------------------------------------- keys ----
KEYCODES = {
    "cross": 1,    # S
    "circle": 2,   # D
    "l1": 19,      # 2
    "r1": 20,      # 3
    "l2": 18,      # 1
    "r2": 21,      # 4
    "up": 126, "down": 125, "left": 123, "right": 124,
    "escape": 53,  # Esc: closes the PlayStation menu after chiaki-ng connects
}
TAP_HOLD = 0.12        # Madden drops instant taps; hold every press a little
NAV_WAIT = 1.0         # settle time after a menu move
LIST_WAIT = 0.6        # settle time after scrolling a list
FOREGROUND = False     # set by --foreground

APP_NAME = "chiaki-ng"
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


class Abort(Exception):
    pass


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------- window ----
def find_window():
    wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    best = None
    for w in wins:
        if w.get("kCGWindowOwnerName") != APP_NAME or w.get("kCGWindowLayer") != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        area = b.get("Width", 0) * b.get("Height", 0)
        if best is None or area > best[2]:
            best = (w["kCGWindowNumber"], w["kCGWindowOwnerPID"], area)
    if not best:
        raise Abort("chiaki-ng window not found. Is it open and streaming?")
    return best[0], best[1]


WIN_ID, PID = None, None


def activate_app():
    subprocess.run(["osascript", "-e", f'tell application "{APP_NAME}" to activate'], check=False)
    time.sleep(0.4)


# ------------------------------------------------------------- stream ----
# chiaki-ng's stream froze several times on Oct 13 (Wi-Fi packet loss): the window keeps the
# last picture and no key reaches the PS5. Identical frames don't prove it (the Manage Roster
# hub is static even when live), so the test is a harmless press that must change the screen.
# JR asked (Oct 13) for the capture to restart the stream itself before stopping.
STREAM_RESTARTS = 8          # per run (Oct 13 evening: 3 freezes in 7 minutes)


def _env(key, default):
    v = os.environ.get(key)
    if v:
        return v
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


CHIAKI_HOST = _env("CHIAKI_HOST", "PS5-263")             # chiaki-ng console nickname
CHIAKI_HOST_IP = _env("CHIAKI_HOST_IP", "192.168.1.181")


LIST_SCREENS = ("PLAYER_STATS", "TEAM_STATS", "SCHED_LIST", "GAME_STATS", "BOX_SCORE", "ROSTER_LIST")
TILE_SCREENS = ("SCHED_TILES", "STATS_TILES")
HUB_SCREENS = ("HUB", "HUB_LEAGUE", "HUB_ROSTER")


def stream_frozen():
    """Press a harmless key and see whether the picture reacts (a frozen stream gives
    pixel-identical frames; Madden's menus are static too, so only a press tells them apart).
    Lists: Down, or Up at the end of a list. Tile screens: Right, or Left. Hub: R1 then L1
    (switches the tab and comes back). Other screens (loading, quit prompt) aren't probed
    and count as not frozen. The press moves a highlight; callers redo their step anyway."""
    from PIL import ImageChops
    f, s = look()

    def changed(before):
        return ImageChops.difference(cg_to_pil(before), cg_to_pil(grab())).getbbox() is not None

    keys = (("down", "up") if s in LIST_SCREENS else ("right", "left") if s in TILE_SCREENS else ())
    for key in keys:
        before = grab()
        press(key, hold=0.12, wait=1.2)
        if changed(before):
            return False
    if s in HUB_SCREENS:
        before = grab()
        press("r1", wait=1.5)
        moved = changed(before)
        press("l1", wait=1.5)
        return not moved
    return bool(keys)


RESTARTS = [0]               # stream restarts used in this run (all modes share STREAM_RESTARTS)


def recover_stream(e):
    """After a failure: if the stream is frozen and restarts are left, restart it (the caller
    then redoes its step); otherwise re-raise the failure."""
    if RESTARTS[0] >= STREAM_RESTARTS or not stream_frozen():
        raise e
    RESTARTS[0] += 1
    log(f"  The stream is frozen ({e}); restart {RESTARTS[0]} of {STREAM_RESTARTS}")
    restart_stream()


def run_mode(name, fn, reset=None):
    """Run one capture mode; when it fails on a frozen stream, restart the stream, call
    reset() (clears the mode's partial output) and run the mode again."""
    while True:
        try:
            return fn()
        except Abort as e:
            recover_stream(e)
            log(f"Redoing {name} after the stream restart")
            if reset:
                reset()


WINDOW_PX = (3024, 1888)      # chiaki-ng window in pixels (1512x944 points, Retina); every
WINDOW_PT = (1512, 944)       # screen box and the card-value columns were mapped at this size


def ensure_window_size():
    """After a relaunch chiaki-ng once came back at 2560x1415 (Oct 13). Set it back to
    WINDOW_PT and check; stop if it won't take the size."""
    global WIN_ID, PID
    for attempt in range(3):
        cg = grab()
        size = (Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg))
        if size == WINDOW_PX:
            return
        # JR's Mac has two displays; on the external (non-Retina) one the same window is captured
        # at half the pixels, so move the window to the built-in display, then size it
        log(f"  chiaki-ng window is {size[0]}x{size[1]}, not {WINDOW_PX[0]}x{WINDOW_PX[1]}; "
            "moving it to the main display and resizing it")
        subprocess.run(["osascript", "-e", f'tell application "System Events" to tell process "{APP_NAME}" '
                        f'to set position of window 1 to {{0, 38}}'], capture_output=True)
        time.sleep(0.5)
        subprocess.run(["osascript", "-e", f'tell application "System Events" to tell process "{APP_NAME}" '
                        f'to set size of window 1 to {{{WINDOW_PT[0]}, {WINDOW_PT[1]}}}'], capture_output=True)
        time.sleep(1.5)
        WIN_ID, PID = find_window()
    raise Abort(f"chiaki-ng's window is {size[0]}x{size[1]} and won't resize to "
                f"{WINDOW_PX[0]}x{WINDOW_PX[1]}; set it back to {WINDOW_PT[0]}x{WINDOW_PT[1]} points")


def restart_stream():
    """Force-quit chiaki-ng (what JR does to unfreeze it; a polite quit then relaunch left the
    console ignoring input on Oct 13), start the stream again and wait for a franchise screen.
    Presses nothing: if no known screen shows within two minutes, stop."""
    global WIN_ID, PID
    log("Restarting the chiaki-ng stream (force quit + relaunch)")
    subprocess.run(["pkill", "-9", "-f", "chiaki-ng.app/Contents/MacOS/chiaki"])
    for _ in range(20):
        if subprocess.run(["pgrep", "-f", "chiaki-ng.app/Contents/MacOS/chiaki"], capture_output=True).returncode:
            break
        time.sleep(0.5)
    time.sleep(3)
    subprocess.run(["open", "-a", APP_NAME, "--args", "stream", CHIAKI_HOST, CHIAKI_HOST_IP])
    end = time.time() + 120
    while True:
        time.sleep(2)
        try:
            WIN_ID, PID = find_window()
            ready = window_info(WIN_ID).get("kCGWindowBounds", {}).get("Height", 0) > 400
        except Abort:
            ready = False                    # no window yet
        if ready:
            try:
                ensure_window_size()         # a wrong size that can't be fixed stops the run
                f, s = look()
            except Abort as e:
                # the window is blank gray for a while as the stream reconnects (Oct 14)
                if "blank" not in str(e) or time.time() > end:
                    raise
                log("  still connecting (blank window); waiting")
                continue
            if s != "UNKNOWN":
                log(f"Stream is back on {s}")
                return f, s
        if time.time() > end:
            raise Abort("chiaki-ng didn't come back on a franchise screen within two minutes of "
                        "restarting; check the chiaki-ng window")


def _post(code, down):
    ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
    if FOREGROUND:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    else:
        Quartz.CGEventPostToPid(PID, ev)


def key_down(name):
    _post(KEYCODES[name], True)


def key_up(name):
    _post(KEYCODES[name], False)


def press(name, hold=TAP_HOLD, wait=NAV_WAIT, times=1):
    for _ in range(times):
        key_down(name)
        time.sleep(hold)
        key_up(name)
        time.sleep(0.12)
    time.sleep(wait)


# ---------------------------------------------------------- screenshot ----
TMP = "/tmp/madden_capture_frame.png"


def recover_blank(path, timeout=15.0):
    """chiaki-ng stops drawing the stream when macOS thinks nobody can see it (fully covered,
    full screen on another desktop). Seen Oct 13 mid-run with a normal window. Wait a moment,
    then bring chiaki-ng to the front once so it draws again. Never return a blank frame:
    a blank page would look like 'end of list' and a blind Back can open the quit prompt."""
    start = time.time()
    activated = False
    while time.time() - start < timeout:
        time.sleep(1.0)
        if not activated and time.time() - start > 2.5:
            log("  chiaki-ng stopped drawing (blank window); bringing it to the front")
            activate_app()
            activated = True
        subprocess.run(["screencapture", "-x", "-o", "-l", str(WIN_ID), path], capture_output=True)
        if os.path.exists(path) and os.path.getsize(path) > 0 and not is_blank(path):
            if activated:
                log("  chiaki-ng is drawing again; continuing")
            return path
    saved = save_debug(types_frame(path), "blank_chiaki_frame")
    raise Abort("chiaki-ng is showing a blank gray window instead of the game, even after bringing "
                "it to the front. Check that the stream is still connected, that chiaki-ng isn't "
                f"minimized or in full screen, and keep part of its window visible. Frame saved: {saved}")


def types_frame(path):
    """Minimal stand-in so save_debug can copy a file path."""
    class _F:
        pass
    f = _F()
    f.path = path
    return f


def window_info(win_id):
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID):
        if w.get("kCGWindowNumber") == win_id:
            return w
    return None


def snap(path=TMP):
    """Screenshot the chiaki-ng window. If it fails (the stream reconnected and chiaki-ng made
    a new window, or the window is minimized), look the window up again and retry."""
    global WIN_ID, PID
    err = ""
    for attempt in range(4):
        if os.path.exists(path):
            os.remove(path)                    # never hand back a stale frame
        r = subprocess.run(["screencapture", "-x", "-o", "-l", str(WIN_ID), path],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
            if is_blank(path):
                return recover_blank(path)
            return path
        err = (r.stderr or r.stdout or "").strip()
        time.sleep(1.0)
        try:
            new_id, new_pid = find_window()
        except Abort:
            continue                           # window gone for now; keep trying
        if new_id != WIN_ID:
            log(f"chiaki-ng's window changed ({WIN_ID} -> {new_id}); switching to the new one")
            WIN_ID, PID = new_id, new_pid
    w = window_info(WIN_ID)
    if w is None:
        why = "the chiaki-ng window no longer exists (closed or the stream dropped)"
    elif not w.get("kCGWindowIsOnscreen", True):
        why = "the chiaki-ng window is minimized or hidden; un-minimize it (covering it is fine)"
    else:
        why = "macOS refused the capture; check Screen Recording permission for Terminal"
    raise Abort(f"Can't screenshot chiaki-ng: {why}. screencapture said: {err or 'nothing'}")


def ocr(path):
    """Return list of (TEXT, (x0, y0, x1, y1)) in pixel coords, top-left origin."""
    img = Image.open(path)
    W, H = img.size
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)
    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        bb = obs.boundingBox()
        x0 = bb.origin.x * W
        y0 = (1 - bb.origin.y - bb.size.height) * H
        out.append((str(cand[0].string()).translate(HOMOGLYPHS).upper(), (x0, y0, x0 + bb.size.width * W, y0 + bb.size.height * H)))
    return out


# Vision sometimes returns Cyrillic/Greek look-alikes inside Latin words ("MCPHЕЕ", Oct 13)
HOMOGLYPHS = str.maketrans("АВСЕНІЈКМОРЅТХУаеорсухіјΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
                           "ABCEHIJKMOPSTXYaeopcyxijABEHIKMNOPTXYZ")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def brightness(path, box, pad=8):
    img = Image.open(path).convert("L")
    x0, y0, x1, y1 = box
    crop = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))
    return ImageStat.Stat(crop).mean[0]


class Frame:
    def __init__(self):
        self.path = snap()
        self.items = ocr(self.path)
        self.text = " | ".join(t for t, _ in self.items)
        self.size = Image.open(self.path).size

    def find(self, label):
        """Exact (normalized) match, else the label followed by at most 2 stray characters
        (the lock icon after TRADE reads as "TRADE Q"). Returns box or None."""
        n = norm(label)
        for t, box in self.items:
            if norm(t) == n:
                return box
        for t, box in self.items:
            nt = norm(t)
            if nt.startswith(n) and len(nt) - len(n) <= 2 and len(n) >= 4:
                return box
        return None

    def bright(self, label, threshold=120):
        box = self.find(label)
        return box is not None and brightness(self.path, box) > threshold

    def highlighted(self, labels):
        """Which of these labels sits on the light 'selected' background."""
        best, best_b = None, -1
        for lab in labels:
            box = self.find(lab)
            if box is None:
                continue
            b = brightness(self.path, box)
            if b > best_b:
                best, best_b = lab, b
        return best if best_b > 120 else None


# -------------------------------------------------------------- screens ----
WEEK_RE = re.compile(r"(PRESEASON WEEK ?[0-9TILO]{1,2}\b|SEASON WEEK ?[0-9TILO]{1,2}\b|POST SEASON (?:WILD ?CARD|DIVISIONAL|CONFERENCE|SUPER ?BOWL))")
HUB_TABS = ["GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "LEAGUE", "COACH CENTRAL", "OPTIONS"]
LEAGUE_MENU = ["NFL STANDINGS", "SCHEDULE", "STATS & AWARDS", "LEAGUE TRANSACTIONS", "LEAGUE HISTORY", "RELOCATE OR REBUILD"]
SCHED_TILES = ["TEAM SCHEDULE", "LEAGUE SCHEDULE", "PLAYOFF PICTURE"]
# Manage Roster tab's left-hand menu (mapped Oct 13; TRADE can show a lock icon)
ROSTER_MENU = ["UPGRADE PLAYERS", "ADJUST LINEUP", "ROSTER", "CONTRACTS", "DEPTH CHART", "TEAM SALARY",
               "INJURY REPORT", "TRADE", "FREE AGENCY", "SCOUTING", "FORMATION SUBS", "AUTO SUBS", "TEAM SCHEMES"]


def classify(f):
    t = f.text
    if "WANT TO QUIT" in t:
        return "QUIT"
    # Manage Roster > Roster (mapped Oct 13): also shows PLAYER NAME (sort chip) and VIEW
    # PLAYER CARD, so it must be told apart from Player Stats by its own columns. The button
    # bar blanks for a moment when it changes (rows with a contract action add SPEAK WITH
    # PLAYER), so don't rely on it.
    if f.find("SAVINGS") and f.find("PENALTY") and f.find("MOR"):
        return "ROSTER_LIST"
    # Box score > R1 (mapped Oct 13): one game's player stats, TEAM column, L2 BOTH / R2 category.
    # It also shows TEAM NAME, SORT and R2, so it must come before TEAM_STATS.
    if f.find("BOX SCORE") and f.find("PLAYER STATS") and f.find("TEAM") and "TOTAL YARDS GAINED" not in t:
        return "GAME_STATS"
    if "PLAYER NAME" in t and "VIEW PLAYER CARD" in t:
        return "PLAYER_STATS"
    if "TEAM NAME" in t and "SORT" in t and f.find("R2"):
        return "TEAM_STATS"
    # League Schedule > Cross on a played game (mapped Oct 13): L1 BOX SCORE | PLAYER STATS R1
    if f.find("BOX SCORE") and f.find("PLAYER STATS") and any(
            lab in t for lab in ("OFF YARDS GAINED", "TOTAL YARDS GAINED", "TIME OF POSSESSION", "TURNOVERS")):
        return "BOX_SCORE"                     # top or scrolled to the bottom
    if "PLAYER STATS" in t and "LEGACY LEADERBOARD" in t:
        return "STATS_TILES"
    if "LEAGUE SCHEDULE" in t and "PLAYOFF PICTURE" in t:
        return "SCHED_TILES"
    if f.find("GAMEDAY") and f.find("COACH CENTRAL"):
        if f.find("STATS & AWARDS"):
            return "HUB_LEAGUE"
        if f.find("ADJUST LINEUP") and f.find("DEPTH CHART"):
            return "HUB_ROSTER"
        return "HUB"
    if (WEEK_RE.search(t) or "GAME TIME" in t) and ("FORCE WIN" in t or "SORT" in t or "BACK" in t):
        return "SCHED_LIST"
    return "UNKNOWN"


def is_blank(path):
    """chiaki-ng shows a flat gray window when it isn't drawing the stream (e.g. it went
    full screen on another desktop). Measured Oct 13: that frame's brightness stddev is ~2;
    Madden's darkest loading screens are 15-19."""
    img = Image.open(path).convert("L")
    W, H = img.size
    inner = img.crop((int(W * .05), int(H * .1), int(W * .95), int(H * .9))).resize((300, 200))
    return ImageStat.Stat(inner).stddev[0] < 6


def look():
    f = Frame()                      # snap() never returns a blank frame (see recover_blank)
    return f, classify(f)


UNKNOWN_GRACE = 20.0   # extra seconds allowed while the screen is unrecognized (loading / blank)
DEBUG_DIR = None       # set in main(); frames that cause a stop are saved here


def save_debug(f, name):
    if DEBUG_DIR:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        dst = os.path.join(DEBUG_DIR, f"{datetime.datetime.now():%H%M%S}_{name}.png")
        subprocess.run(["cp", f.path, dst])
        return dst


def wait_for(screens, timeout=8.0):
    """Wait until one of `screens` is showing. A screen we don't recognize is usually
    Madden still loading (blank background), so it gets UNKNOWN_GRACE extra seconds,
    re-checked once a second, before giving up."""
    start = time.time()
    end = start + timeout
    hard_end = end + UNKNOWN_GRACE
    told = False
    while True:
        f, s = look()
        if s in screens:
            if told:
                log(f"  ...{s} after {time.time() - start:.1f}s")
            return f, s
        now = time.time()
        if s == "UNKNOWN" and now < hard_end:
            if not told:
                log("  Screen not recognized yet (probably loading); waiting")
                told = True
            time.sleep(1.0)
            continue
        if now > end:
            path = save_debug(f, f"expected_{'_'.join(screens)}_got_{s}")
            hint = f" Saved frame: {path}" if path else ""
            if s == "UNKNOWN" and not f.text.strip():
                hint += " (The frame had no text at all. Is chiaki-ng minimized, or did the stream freeze?)"
            raise Abort(f"Expected {screens}, got {s}. Visible text: {f.text[:300]}{hint}")
        time.sleep(0.5)


def cross(screen, frame=None):
    """Cross only where it is known to be harmless."""
    if screen == "QUIT":
        if not frame or not frame.bright("NO"):
            raise Abort("Quit prompt open but NO is not highlighted. Stopping. Please choose NO yourself.")
    elif screen == "HUB_ROSTER":
        # Manage Roster's menu also holds Trade, Free Agency, Contracts: Cross only on Roster.
        cur = (frame or Frame()).highlighted(ROSTER_MENU)
        if cur != "ROSTER":
            raise Abort(f"Refusing Cross on the Manage Roster menu: {cur} is highlighted, not ROSTER")
    elif screen == "SCHED_LIST":
        # Only to open a played game's box score (JR's rule, Oct 13); an unplayed week's list
        # offers ACTIONS / Force Win instead.
        why = schedule_cross_ok(frame or Frame(), CURRENT_WEEK)
        if why:
            raise Abort(f"Refusing Cross on the League Schedule: {why}")
    elif screen not in ("HUB_LEAGUE", "SCHED_TILES", "STATS_TILES"):
        raise Abort(f"Refusing to press Cross on screen {screen}")
    press("cross", wait=1.5)


def cross_into(screen, expected, timeout=6.0, tries=3):
    """Press Cross to open the next screen. Madden drops some presses, so if we are
    still on the same screen afterwards, press again (up to `tries` times)."""
    for attempt in range(1, tries + 1):
        cross(screen)
        try:
            return wait_for(expected, timeout)
        except Abort:
            f, s = look()
            if s == "UNKNOWN":          # still loading (blank background): give it longer
                try:
                    return wait_for(expected, timeout)
                except Abort:
                    f, s = look()
            if s != screen:
                raise
            log(f"Cross didn't register on {screen} (attempt {attempt}), pressing again")
    raise Abort(f"Cross never opened {expected} from {screen}")


# ----------------------------------------------------------- navigation ----
def go_hub():
    for _ in range(10):
        f, s = look()
        if s in ("HUB", "HUB_LEAGUE", "HUB_ROSTER"):
            return f, s
        if s == "QUIT":
            log("Quit prompt is open, choosing NO")
            cross(s, f)
            continue
        if s == "UNKNOWN":                     # don't press Back on a loading screen
            for _ in range(6):
                time.sleep(1.0)
                f, s = look()
                if s != "UNKNOWN":
                    break
            if s in ("HUB", "HUB_LEAGUE", "HUB_ROSTER", "QUIT"):
                continue
        log(f"On {s}, pressing Back")
        press("circle", wait=1.5)
    raise Abort("Could not reach the franchise hub")


def move_to(f, labels, target, prev_key, next_key, what):
    for _ in range(12):
        cur = f.highlighted(labels)
        if cur == target:
            return f
        if cur is None:
            raise Abort(f"Can't tell which {what} is highlighted. Text: {f.text[:300]}")
        delta = labels.index(target) - labels.index(cur)
        press(next_key if delta > 0 else prev_key)
        f, _ = look()
    raise Abort(f"Could not highlight {target}")


def to_league_tab():
    f, s = go_hub()
    if s != "HUB_LEAGUE":
        log("Switching to LEAGUE tab")
        move_to(f, HUB_TABS, "LEAGUE", "l1", "r1", "hub tab")
        f, s = wait_for(["HUB_LEAGUE"])
    return f


def open_player_stats():
    f = to_league_tab()
    f = move_to(f, LEAGUE_MENU, "STATS & AWARDS", "up", "down", "League menu item")
    f, _ = cross_into("HUB_LEAGUE", ["STATS_TILES"])
    for _ in range(6):
        if f.bright("PLAYER STATS"):
            break
        press("left")
        f, _ = look()
    else:
        for _ in range(3):
            press("up")
            f, _ = look()
            if f.bright("PLAYER STATS"):
                break
        else:
            raise Abort("Could not highlight the Player Stats tile")
    return cross_into("STATS_TILES", ["PLAYER_STATS"])[0]


STATS_TILE_ROW = ["PLAYER STATS", "LEGACY LEADERBOARD", "TEAM STATS"]


def open_team_stats():
    """Stats & Awards > Team Stats. From Player Stats it's one Back to the tiles."""
    f, s = look()
    if s == "TEAM_STATS":
        return f
    if s == "PLAYER_STATS":
        press("circle", wait=1.5)
        f, s = wait_for(["STATS_TILES"])
    if s != "STATS_TILES":
        f = to_league_tab()
        f = move_to(f, LEAGUE_MENU, "STATS & AWARDS", "up", "down", "League menu item")
        f, _ = cross_into("HUB_LEAGUE", ["STATS_TILES"])
    for _ in range(4):                        # highlight may be in the right-hand column
        if f.highlighted(STATS_TILE_ROW):
            break
        press("left")
        f, _ = look()
    move_to(f, STATS_TILE_ROW, "TEAM STATS", "left", "right", "stats tile")
    return cross_into("STATS_TILES", ["TEAM_STATS"])[0]


CURRENT_WEEK = None    # the week the League Schedule opens on (set by open_league_schedule)


def open_league_schedule():
    global CURRENT_WEEK
    f = to_league_tab()
    f = move_to(f, LEAGUE_MENU, "SCHEDULE", "up", "down", "League menu item")
    f, _ = cross_into("HUB_LEAGUE", ["SCHED_TILES"])
    move_to(f, SCHED_TILES, "LEAGUE SCHEDULE", "left", "right", "schedule tile")
    f = cross_into("SCHED_TILES", ["SCHED_LIST"], timeout=10)[0]
    CURRENT_WEEK = week_label(f)
    return f


# ---------------------------------------------------------------- chips ----
def chip_labels(f):
    """Return (scope, category) from the two chips above the Player Stats table.
    OCR never reads the L2 icon, so anchor on the 'R2' icon: the closest text to
    its left on the same row is the scope, the closest to its right the category."""
    W, H = f.size
    r2 = next((box for t, box in f.items if norm(t) == "R2"), None)
    if not r2:
        return None, None
    cy = (r2[1] + r2[3]) / 2
    row = [(box, t) for t, box in f.items
           if abs((box[1] + box[3]) / 2 - cy) < H * 0.015 and norm(t) != "R2"]
    left = [(r2[0] - box[2], t) for box, t in row if box[2] <= r2[0] + 5]
    right = [(box[0] - r2[2], t) for box, t in row if box[0] >= r2[2] - 5 and box[0] < W * 0.6]
    return (min(left)[1] if left else None), (min(right)[1] if right else None)


def game_chips(f, tries=4):
    """(scope, category) on a game's player stats (GAME_STATS). chip_labels() needs the R2 icon,
    which OCR often misses here, so fall back to position: both chips sit ~41% down, scope
    left of ~17% of the width, category between 17% and 31% (mapped Oct 13)."""
    for attempt in range(tries):
        scope, cat = chip_labels(f)
        if scope and cat:
            return scope, cat
        W, H = f.size
        row = sorted((b[0], t) for t, b in f.items if H * 0.38 < b[1] < H * 0.44 and b[0] < W * 0.31
                     and norm(t) not in ("L2", "R2") and re.match(r"^[A-Z &]{3,}$", t))
        left = [t for x, t in row if x < W * 0.17]
        right = [t for x, t in row if x >= W * 0.17]
        if left and right:
            return left[0], right[0]
        time.sleep(0.5)
        f = Frame()
    return None, None


def week_label(f):
    m = WEEK_RE.search(f.text)
    if not m:
        return None
    return clean_week(m.group(1))


def clean_week(label):
    """Normalize OCR'd week labels: 'SEASON WEEKT' -> 'SEASON WEEK 1', 'WEEK1' -> 'WEEK 1'."""
    label = re.sub(r"\s+", " ", label.upper().strip())
    label = label.replace("WILDCARD", "WILD CARD").replace("SUPERBOWL", "SUPER BOWL")
    m = re.match(r"^(PRESEASON|SEASON) WEEK ?([0-9TILO]{1,2})$", label)
    if m:
        digits = m.group(2).translate(str.maketrans("TILO", "1110"))
        return f"{m.group(1)} WEEK {int(digits)}"
    return label


def week_index(label):
    """Position of a week in Madden's season order (preseason < regular season < playoffs)."""
    label = clean_week(label)
    m = re.match(r"^PRESEASON WEEK (\d+)$", label)
    if m:
        return -10 + int(m.group(1))
    m = re.match(r"^SEASON WEEK (\d+)$", label)
    if m:
        return int(m.group(1))
    if label in WEEK_ORDER_POST:
        return 19 + WEEK_ORDER_POST.index(label)
    return None


WEEK_ORDER_POST = ["POST SEASON WILD CARD", "POST SEASON DIVISIONAL", "POST SEASON CONFERENCE", "POST SEASON SUPER BOWL"]


def previous_week(label):
    label = label.replace("WILDCARD", "WILD CARD").replace("SUPERBOWL", "SUPER BOWL")
    m = re.match(r"SEASON WEEK (\d+)", label)
    if m:
        n = int(m.group(1))
        if n <= 1:
            raise Abort("Current week is Week 1; there is no previous regular-season week to capture.")
        return f"SEASON WEEK {n - 1}"
    if label in WEEK_ORDER_POST:
        i = WEEK_ORDER_POST.index(label)
        return "SEASON WEEK 18" if i == 0 else WEEK_ORDER_POST[i - 1]
    raise Abort(f"Don't know the week before '{label}'")


# ------------------------------------------------------------- dropdown ----
# Verified with --probe-l2 (Oct 13):
#  * Holding L2 opens the scope list after ~0.7s; the chip then reads "SELECT".
#  * L2 must stay held the whole time; releasing it selects the highlighted entry.
#  * The highlighted entry is always the TOP row of the list (light box); the list
#    scrolls under it. Up/Down move one entry. Some presses get dropped.
#  * Order: teams A-Z by nickname (49ERS ... VIKINGS), then NFL, AFC, NFC, divisions.
#    From PANTHERS, NFL is 11 Downs.
DIVISION_ENTRIES = {"NFL", "AFC", "NFC", "AFCNORTH", "AFCSOUTH", "AFCEAST", "AFCWEST",
                    "NFCNORTH", "NFCSOUTH", "NFCEAST", "NFCWEST"}


def dropdown_entries(f, top=0.35, bottom=0.92):
    """Text items inside the open dropdown column (left side, below the chips). The roster
    list's team dropdown sits higher (top entry at ~24% of the height): top=0.20, bottom=0.76."""
    W, H = f.size
    return [(t, box) for t, box in f.items
            if box[2] < W * 0.21 and H * top < box[1] < H * bottom and re.match(r"^[A-Z0-9 ]+$", t)]


def dropdown_selected(f, top=0.35, bottom=0.92):
    """The highlighted entry. The highlight is always the TOP row of the open list,
    drawn as a light box, so take the topmost entry and confirm its background is light."""
    entries = dropdown_entries(f, top, bottom)
    if not entries:
        return None
    t, box = min(entries, key=lambda e: e[1][1])
    return t if brightness(f.path, box) > 120 else None


def set_scope(target="NFL", debug_dir=None):
    f, s = look()
    if s not in ("PLAYER_STATS", "TEAM_STATS"):
        raise Abort(f"set_scope needs Player Stats or Team Stats, on {s}")
    screen = s
    scope, _ = chip_labels(f)
    log(f"Scope chip reads: {scope}")
    if scope and norm(scope) == norm(target):
        return f
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
    step = [0]

    def frame():
        fr = Frame()
        if debug_dir:
            step[0] += 1
            subprocess.run(["cp", fr.path, os.path.join(debug_dir, f"scope_{step[0]:02d}.png")])
        return fr

    tnorm = norm(target)
    key_down("l2")
    try:
        # 1. wait for the list to open (chip turns into SELECT, entries appear)
        opened = False
        t_end = time.time() + 3.0
        while time.time() < t_end:
            time.sleep(0.35)
            f = frame()
            if "SELECT" in f.text and dropdown_selected(f):
                opened = True
                break
        if not opened:
            raise Abort("Scope list did not open while holding L2")
        # 2. move the highlight onto the target, verifying every press
        stuck = 0
        scan, turned = ["up"], [False]         # team targets: teams come before NFL in the list
        last = None
        for _ in range(90):
            sel = dropdown_selected(f)
            for _retry in range(3):          # OCR occasionally misses one frame
                if sel is not None:
                    break
                time.sleep(0.3)
                f = frame()
                sel = dropdown_selected(f)
            if sel and norm(sel) == tnorm:
                break
            if sel is None:
                raise Abort("Lost track of the scope list (can't read the highlighted entry)")
            visible = [norm(t) for t, _ in dropdown_entries(f)]
            if tnorm not in DIVISION_ENTRIES:
                # a team (team-scoped Season Stats): head for it by its place on screen once it shows;
                # until then scan one way, and turn round when the list stops moving
                ent = {norm(t): b for t, b in dropdown_entries(f)}
                sb = ent.get(norm(sel))
                if tnorm in ent and sb:
                    direction = "down" if ent[tnorm][1] > sb[1] else "up"
                else:
                    direction = scan[0]
            elif norm(sel) in DIVISION_ENTRIES and tnorm in DIVISION_ENTRIES:
                order = ["NFL", "AFC", "NFC"]
                direction = "up" if norm(sel) not in order or (tnorm in order and order.index(tnorm) < order.index(norm(sel))) else "down"
            elif tnorm in visible or norm(sel) not in DIVISION_ENTRIES:
                direction = "down"     # target is below (teams come before NFL)
            else:
                direction = "up"       # we're past it, in the AFC/NFC/division part
            key_down(direction); time.sleep(0.1); key_up(direction)
            time.sleep(0.45)
            f = frame()
            new = dropdown_selected(f)
            stuck = stuck + 1 if new == sel else 0
            if stuck >= 2 and tnorm not in DIVISION_ENTRIES and not turned[0]:
                scan[0] = "down" if scan[0] == "up" else "up"      # end of the list: scan the other way
                turned[0], stuck = True, 0
            if stuck >= 4:
                raise Abort(f"Up/Down presses aren't registering in the scope list (stuck on {sel})")
            last = sel
        else:
            raise Abort(f"Could not reach {target} in the scope list")
        log(f"{target} highlighted; releasing L2 to select it")
    finally:
        key_up("l2")
    time.sleep(1.5)
    f, _ = wait_for([screen])
    scope, _ = chip_labels(f)
    if not scope or norm(scope) != tnorm:
        raise Abort(f"After selecting, scope chip reads {scope}, not {target}")
    log(f"Scope set to {scope}")
    return f


# --------------------------------------------------------------- lists ----
def table_sig(path, top=0.40, bottom=0.85):
    img = Image.open(path).convert("L")
    W, H = img.size
    return img.crop((0, int(H * top), W, int(H * bottom))).resize((900, 360))


def same_table(sig_a, sig_b):
    """Screens of the same list position differ only by stream noise (pixel diff < 20).
    Measured Oct 13: a real scroll changes 3-9% of pixels by > 40; a repeat changes 0%."""
    from PIL import ImageChops
    d = ImageChops.difference(sig_a, sig_b).point(lambda v: 255 if v > 40 else 0)
    changed = d.histogram()[255] / (d.width * d.height)
    return changed < 0.005


ZERO_RE = re.compile(r"^-?0+(\.0+)?%?$")
NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?%?$")


def all_zero_rows(f, top=0.45, min_nums=20):
    """True when every numeric cell in the lower part of the table is zero. Game tables can
    have a single number column (General: DP), so they use a lower min_nums."""
    H = f.size[1]
    nums = [t for t, b in f.items if b[1] > H * top and NUM_RE.match(t.replace(" ", ""))]
    return len(nums) >= min_nums and all(ZERO_RE.match(n.replace(",", "")) for n in nums)


def wait_table_loaded(timeout=8.0):
    """After switching tabs the header shows before the rows do (seen on Red Zone, Oct 13).
    Wait until the table area has several numeric cells."""
    end = time.time() + timeout
    while True:
        f = Frame()
        H = f.size[1]
        nums = [t for t, b in f.items if b[1] > H * 0.40 and re.search(r"\d", t)]
        if len(nums) >= 6:
            return f
        if time.time() > end:
            log("  table still looks empty; capturing anyway")
            return f
        time.sleep(1.0)


def table_text(path, top=0.48, bottom=0.90):
    """The table's cell texts in reading order. Game player-stats tables show only a few rows
    over an animated background, which can move the pixel test past its threshold (Oct 13:
    0.69% on a 2-row Kick Return page), so those lists also end when the text repeats."""
    H = Image.open(path).size[1]
    return [t for t, b in sorted(ocr(path), key=lambda i: (round(i[1][1] / 20), i[1][0]))
            if H * top < b[1] < H * bottom]


def capture_list(folder, prefix, rows_per_page=10, max_pages=250, stop_at_zeros=True, direction="down",
                 text_end=False, zero_min=20):
    os.makedirs(folder, exist_ok=True)
    wait_table_loaded()
    prev, prev_text = None, None
    for page in range(max_pages):
        path = os.path.join(folder, f"{prefix}_{page:03d}.png")
        snap(path)
        sig = table_sig(path)
        text = table_text(path) if text_end else None
        if prev is not None and (same_table(prev, sig) or (text_end and text and text == prev_text)):
            os.remove(path)
            log(f"  {prefix}: end of list after {page} pages")
            return page
        prev, prev_text = sig, text
        if stop_at_zeros:
            f = Frame.__new__(Frame)
            f.path, f.items, f.size = path, ocr(path), Image.open(path).size
            if all_zero_rows(f, min_nums=zero_min):
                log(f"  {prefix}: rest of list is zeros, stopping at page {page + 1}")
                return page + 1
        press(direction, hold=0.07, wait=LIST_WAIT, times=rows_per_page)
    log(f"  {prefix}: hit max pages")
    return max_pages


# Categories whose table is wider than the screen (verified Oct 13 run): the right-hand
# columns only show after scrolling the table with D-pad Right. NAME/POS stay pinned.
WIDE_CATEGORIES = {"PASSING", "DEFENSE", "KICKING"}


def at_edge(f, direction):
    """Right edge: the highlight is on the last column, DP, whose label reads DOWNS PLAYED
    (true for Passing, Defense and Kicking). Left edge: the NAME column header is visible."""
    if direction == "right":
        return "DOWNS PLAYED" in f.text
    return f.find("NAME") is not None


def traverse(direction, burst=6, max_rounds=12):
    """Press Left/Right in short bursts, reading the screen after each burst, until the edge
    is actually visible. Madden doesn't wrap at either edge, so overshooting is harmless.
    (Oct 13: judging the edge from 'two presses changed nothing' stopped Kicking at FG LNG.)"""
    for _ in range(max_rounds):
        f = Frame()
        if at_edge(f, direction):
            return f
        press(direction, hold=0.1, wait=0.5, times=burst)
    f = Frame()
    if at_edge(f, direction):
        return f
    path = save_debug(f, f"never_reached_{direction}_edge")
    raise Abort(f"Never reached the {direction} edge of the table (frame: {path})")


def capture_wide_list(folder, base, rows_per_page=10, max_pages=250, traverse=traverse, text_end=False,
                      end_on_left=False, stop_at_zeros=False):
    """Wide tables: NAME/POS are NOT pinned when the table scrolls right, so every page is
    captured at both edges at the same row position. Row N of {base}_NNN.png is row N of
    {base}_right_NNN.png. Zig-zags (left, right, down, right, left, down...) so each page
    needs one trip across the columns. end_on_left: judge the end of the list only by the
    left (named) half; a game's right-hand pages are often all zeros and look alike (Oct 13)."""
    os.makedirs(folder, exist_ok=True)
    wait_table_loaded()
    traverse("left")                     # the column highlight can be left anywhere; start at the left edge
    side = "left"
    prev = {"left": None, "right": None}
    prev_text = {"left": None, "right": None}

    def path(s, page):
        return os.path.join(folder, f"{base}_{page:03d}.png" if s == "left" else f"{base}_right_{page:03d}.png")

    def repeats(s, file):
        sig = table_sig(file)
        text = table_text(file) if text_end else None
        same = prev[s] is not None and (same_table(prev[s], sig) or (text_end and text and text == prev_text[s]))
        prev[s], prev_text[s] = sig, text
        return same and (s == "left" or not end_on_left)

    for page in range(max_pages):
        first = path(side, page)
        f = traverse(side)                      # confirm we're still at this side's edge
        subprocess.run(["cp", f.path, first])
        if repeats(side, first):
            os.remove(first)
            log(f"  {base}: end of list after {page} pages")
            if side == "right":
                traverse("left")                # leave the table at its left edge
            return page
        other = "right" if side == "left" else "left"
        f = traverse(other)
        second = path(other, page)
        subprocess.run(["cp", f.path, second])
        if repeats(other, second) and end_on_left:   # Season Stats: only the first half ends it
            os.remove(first)
            os.remove(second)
            log(f"  {base}: end of list after {page} pages")
            return page
        if stop_at_zeros:
            left = path("left", page)
            fl = Frame.__new__(Frame)
            fl.path, fl.items, fl.size = left, ocr(left), Image.open(left).size
            if all_zero_rows(fl, top=0.48):
                log(f"  {base}: rest of list is zeros, stopping at page {page + 1}")
                if side == "left":              # we ended this page on the right edge
                    traverse("left")
                return page + 1
        side = other
        press("down", hold=0.07, wait=LIST_WAIT, times=rows_per_page)
    log(f"  {base}: hit max pages")
    return max_pages


def capture_category(out, index, cat, all_rows):
    folder = os.path.join(out, "stats")
    base = f"{index:02d}_{norm(cat).lower()}"
    if norm(cat) in WIDE_CATEGORIES:
        capture_wide_list(folder, base)
    else:
        capture_list(folder, base, stop_at_zeros=not all_rows)


# ------------------------------------------------------------------ runs ----
def next_category(prev_cat, screen):
    """Press R2 and wait until the category chip shows a different category."""
    press("r2", hold=0.15, wait=1.5)
    cat = None
    for attempt in range(6):               # the new category may still be loading
        f, _ = wait_for([screen])
        _, cat = game_chips(f) if screen == "GAME_STATS" else chip_labels(f)
        if cat and norm(cat) != norm(prev_cat):
            return cat
        if attempt == 2:                   # still the old category: R2 was dropped
            log("  R2 didn't register, pressing again")
            press("r2", hold=0.15, wait=1.5)
        else:
            time.sleep(1.0)
    if not cat:
        raise Abort("Can't read the stats category after pressing R2")
    raise Abort(f"Category stayed on {cat} after pressing R2 twice")


def run_stats(out, all_rows):
    log("== Season stats ==")
    open_player_stats()
    set_scope("NFL", debug_dir=os.path.join(out, "scope_debug"))
    f, _ = look()
    _, first = chip_labels(f)
    if not first:
        raise Abort("Can't read the stats category chip")
    seen = []
    cat = first
    for _ in range(15):
        log(f"Category: {cat}")
        seen.append(cat)
        capture_category(out, len(seen), cat, all_rows)
        cat = next_category(cat, "PLAYER_STATS")
        if norm(cat) == norm(first):
            break
    log(f"Captured {len(seen)} categories: {', '.join(seen)}")


def run_scoped_stats(out, team, categories, all_rows):
    """Season Stats for one team's scope, only the given categories, into stats_scope/
    (<team>_<category>_NNN.png). Used when Season Stats lists two players with the same name and
    position on different teams and their earlier totals can't tell them apart (JR, Oct 14): the
    team's own list says which one is whose. Category names as on the R2 chip (KICKING, RUSHING...)."""
    team, wanted = norm(team), {norm(c) for c in categories}
    log(f"== Season stats, {team} only: {', '.join(sorted(wanted))} ==")
    open_player_stats()
    set_scope(team, debug_dir=os.path.join(out, "scope_debug"))
    f, _ = look()
    _, first = chip_labels(f)
    if not first:
        raise Abort("Can't read the stats category chip")
    folder, cat, done = os.path.join(out, "stats_scope"), first, set()
    for _ in range(15):
        if norm(cat) in wanted and norm(cat) not in done:
            log(f"Category: {cat}")
            base = f"{team.lower()}_{norm(cat).lower()}"
            if norm(cat) in WIDE_CATEGORIES:
                capture_wide_list(folder, base)
            else:
                capture_list(folder, base, stop_at_zeros=not all_rows)
            done.add(norm(cat))
            if done == wanted:
                break
        cat = next_category(cat, "PLAYER_STATS")
        if norm(cat) == norm(first) and done != wanted:
            raise Abort(f"Never found categories {sorted(wanted - done)}")
    log(f"Captured {team}: {', '.join(sorted(done))}")


# Team Stats tabs (R2 cycle, verified Oct 13): OFFENSE, DEFENSE, CONVERSIONS,
# REDZONE EFFICIENCY, PENALTIES, TURNOVERS. Offense/defense/turnovers can be built from
# player stats; these three can't. All three fit on screen (no right-scroll); 32 rows.
TEAM_TABS = ["CONVERSIONS", "REDZONE EFFICIENCY", "PENALTIES"]


def run_team_stats(out):
    log("== Team stats ==")
    f = open_team_stats()
    scope, cat = chip_labels(f)
    if not scope or norm(scope) != "NFL":
        f = set_scope("NFL", debug_dir=os.path.join(out, "scope_debug"))
        scope, cat = chip_labels(f)
    if not cat:
        raise Abort("Can't read the team stats tab")
    wanted = {norm(t) for t in TEAM_TABS}
    done = set()
    for _ in range(8):                        # at most one trip around the 6 tabs
        if norm(cat) in wanted and norm(cat) not in done:
            log(f"Team tab: {cat}")
            capture_list(os.path.join(out, "team_stats"), f"team_{norm(cat).lower()}",
                         stop_at_zeros=False)
            done.add(norm(cat))
            if done == wanted:
                break
        cat = next_category(cat, "TEAM_STATS")
    missing = wanted - done
    if missing:
        raise Abort(f"Never found team tabs: {sorted(missing)}")
    log(f"Captured team tabs: {', '.join(TEAM_TABS)}")


def week_entries(f):
    """Week labels visible on screen, as (clean label, box)."""
    out = []
    for t, box in f.items:
        lab = clean_week(t)
        if week_index(lab) is not None:
            out.append((lab, box))
    return out


def select_week(target, debug_dir=None):
    """Hold L2 to open the week list, move the highlight to `target`, release to select.
    Same mechanics as the stats scope list: L2 held the whole time, release selects."""
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
    step = [0]

    def frame():
        fr = Frame()
        if debug_dir:
            step[0] += 1
            subprocess.run(["cp", fr.path, os.path.join(debug_dir, f"week_{step[0]:02d}.png")])
        return fr

    def selected(fr):
        best, best_b = None, -1
        for lab, box in week_entries(fr):
            b = brightness(fr.path, box)
            if b > best_b:
                best, best_b = lab, b
        return best if best_b > 120 else None

    t_idx = week_index(target)
    key_down("l2")
    try:
        f = None
        t_end = time.time() + 3.0
        while time.time() < t_end:              # list is open once several week labels show
            time.sleep(0.35)
            f = frame()
            if len(week_entries(f)) >= 3 and selected(f):
                break
        else:
            raise Abort("Week list did not open while holding L2")
        stuck = 0
        for _ in range(40):
            sel = selected(f)
            for _retry in range(3):
                if sel:
                    break
                time.sleep(0.3)
                f = frame()
                sel = selected(f)
            if sel is None:
                raise Abort("Lost track of the highlighted week")
            if week_index(sel) == t_idx:
                break
            direction = "up" if week_index(sel) > t_idx else "down"
            key_down(direction); time.sleep(0.1); key_up(direction)
            time.sleep(0.45)
            f = frame()
            stuck = stuck + 1 if selected(f) == sel else 0
            if stuck >= 4:
                raise Abort(f"Up/Down not registering in the week list (stuck on {sel})")
        else:
            raise Abort(f"Could not reach {target} in the week list")
        log(f"{target} highlighted; releasing L2")
    finally:
        key_up("l2")
    time.sleep(1.5)
    return wait_for(["SCHED_LIST"], timeout=10)[0]


def go_to_week(f, target, out):
    """On the League Schedule list, switch to `target` (held-L2 week list; falls back to
    tapping L2, which steps one week forward and wraps). Returns the frame."""
    if week_index(week_label(f) or "") != week_index(target):
        try:
            f = select_week(target, debug_dir=os.path.join(out, "week_debug"))
        except Abort as e:
            log(f"Week list didn't work ({e}); falling back to tapping L2 forward")
            f, _ = wait_for(["SCHED_LIST"], timeout=10)
        for _ in range(30):                     # verify (and fallback: tap = next week, wraps)
            if week_index(week_label(f) or "") == week_index(target):
                break
            press("l2", hold=0.15, wait=1.3)
            f, _ = wait_for(["SCHED_LIST"], timeout=10)
        else:
            raise Abort(f"Could not reach {target}")
    return f


def run_schedule(out, week=None):
    log("== Schedule ==")
    f = open_league_schedule()
    cur = week_label(f)
    if not cur:
        raise Abort("Can't read the schedule week label")
    target = clean_week(week) if week else previous_week(cur)
    log(f"Current week: {cur}. Target: {target}")
    f = go_to_week(f, target, out)
    log(f"On {week_label(f)}")
    if "FORCE WIN" in f.text and "ACTIONS" in f.text:
        log("Note: this week shows an ACTIONS button; Cross will NOT be pressed here.")
    n = capture_list(os.path.join(out, "schedule"), norm(target).lower(), stop_at_zeros=False)
    log(f"Schedule {target}: {n} pages")
    # the loader sets league_state.current_week from the week the schedule opened on
    with open(os.path.join(out, "week.json"), "w") as fh:
        json.dump({"current": cur, "captured": clean_week(target)}, fh)


RESULT_RE = re.compile(r"^([A-Z]{2,3}) ?(\d+) ?- ?(\d+) ?([A-Z]{2,3})$")    # "LAC 34 - 9LV" too


def highlight_band(path, top=0.40, bottom=0.88, x0=0.15, x1=0.80):
    """(y_top, y_bottom) of the white outline around the highlighted list row, or None.
    The outline is a pair of bright horizontal lines about one row apart (mapped on the
    League Schedule list, Oct 13: outline ~190, everything else ~50)."""
    img = Image.open(path).convert("L")
    W, H = img.size
    strip = img.crop((int(W * x0), int(H * top), int(W * x1), int(H * bottom)))
    lines, prev = [], -10
    for y in range(strip.height):
        if ImageStat.Stat(strip.crop((0, y, strip.width, y + 1))).mean[0] > 150:
            if y - prev > 3:
                lines.append(y)
            prev = y
    for a, b in zip(lines, lines[1:]):
        if 0.025 * H < b - a < 0.06 * H:
            return a + int(H * top), b + int(H * top)
    return None


def highlighted_schedule_row(f):
    """Texts of the highlighted League Schedule row (inside the outline), left to right."""
    band = highlight_band(f.path)
    if not band:
        return None
    return [t for t, b in sorted(f.items, key=lambda i: i[1][0]) if band[0] < (b[1] + b[3]) / 2 < band[1]]


def schedule_cross_ok(f, current_week):
    """JR's rule (Oct 13) for opening a box score with Cross on the League Schedule list:
    the week is earlier than the current week, the button bar shows BOX SCORE, ACTIONS
    appears nowhere, FORCE WIN appears only as the column header, and the highlighted row has
    a final score. Returns None if allowed, else the reason."""
    W, H = f.size
    wk = week_label(f)
    if not wk or not current_week or week_index(wk) is None or week_index(wk) >= week_index(current_week):
        return f"week {wk} is not before the current week {current_week}"
    if not any(norm(t) == "BOXSCORE" and b[1] > H * 0.9 for t, b in f.items):
        return "no BOX SCORE button"
    if any("ACTION" in t for t, _ in f.items):
        return "ACTIONS is on screen"
    header = f.find("MATCHUP")
    for t, b in f.items:
        if "FORCE" in t and not (header and abs(b[1] - header[1]) < H * 0.02):
            return "FORCE WIN appears outside the column header"
    row = highlighted_schedule_row(f)
    if not row or not any(RESULT_RE.match(t) for t in row):
        return f"the highlighted row has no final score ({row})"
    return None


# ---------------------------------------------------------------- games ----
# League Schedule > Cross on a played game > box score (mapped Oct 13): 17 team-stat rows
# under the scoreboard, 14 visible, the highlight moves down before the list scrolls (Time of
# Possession is last). R1 = that game's player stats: TEAM column, L2 BOTH, R2 cycles 10
# categories (Kicking, Punting, Kick Return, Punt Return, General, Passing, Rushing,
# Receiving, Blocking, Defense), 8 rows shown. Kicking and Defense are wider than the screen:
# Right moves a column highlight, then scrolls; TEAM/NAME scroll away. Back returns to the
# schedule list with the same game highlighted.
BOX_LAST_ROW = "TIME OF POSSESSION"


def game_header(f):
    """Column labels of the game player-stats table, left to right (header ~46% down)."""
    W, H = f.size
    return [t for t, b in sorted(f.items, key=lambda i: i[1][0]) if H * 0.44 < b[1] < H * 0.48]


def traverse_game(direction, chunk=1.5, max_chunks=8):
    """Left/Right across a game's player-stats table by holding the key (Madden auto-repeats:
    ~4 s of held Right crosses Defense, vs ~20 s of single presses). Right first moves a column
    highlight before the table scrolls, so a stable header isn't enough: at the right edge
    TEAM/NAME have scrolled away, at the left edge NAME shows."""
    prev = None

    def at(h):
        return ("NAME" in h) if direction == "left" else ("NAME" not in h and "TEAM" not in h)

    def key(h):
        # OCR varies between reads ("INT YDS"/"INTYDS", sort marks "Y TAK"/"*TAK"), so compare the
        # normalised labels at the edge being looked for
        n = [re.sub(r"^[YV*4]", "", norm(x)) for x in h]
        return tuple(n[-2:] if direction == "right" else n[:2])

    for _ in range(max_chunks):
        f = Frame()
        h = game_header(f)
        if h and prev is not None and key(h) == key(prev) and at(h):
            return f
        prev = h
        key_down(direction)
        time.sleep(chunk)
        key_up(direction)
        time.sleep(0.5)
    f = Frame()
    if prev and key(game_header(f)) == key(prev) and at(prev):
        return f
    path = save_debug(f, f"game_never_reached_{direction}_edge")
    raise Abort(f"Never reached the {direction} edge of the game stats table (frame: {path})")


# Game categories wider than the screen (mapped Oct 13: horizontal scrollbar, Right scrolls).
GAME_WIDE_CATEGORIES = {"KICKING", "DEFENSE"}
GAME_PAGE_STEP = 6   # Down presses per page: 8 rows show, so pages overlap by 2 (a dropped press can't leave a gap)


def capture_game_category(folder, base, cat, all_rows):
    """One category of a game's player stats; wide tables at both edges, like Season Stats.
    (Testing every category's width cost ~35 s each, so the wide ones are listed.)"""
    os.makedirs(folder, exist_ok=True)
    wait_table_loaded()
    if norm(cat) in GAME_WIDE_CATEGORIES:
        capture_wide_list(folder, base, rows_per_page=GAME_PAGE_STEP, traverse=traverse_game, text_end=True,
                          end_on_left=True, stop_at_zeros=not all_rows)
    else:
        capture_list(folder, base, rows_per_page=GAME_PAGE_STEP, stop_at_zeros=not all_rows, text_end=True,
                     zero_min=6)


def box_score_bottom():
    """Step the box score's highlight down until Time of Possession is highlighted."""
    for _ in range(25):
        f = Frame()
        W, H = f.size
        lab = max(((brightness(f.path, b, pad=0), t) for t, b in f.items
                   if 0.4 * W < b[0] < 0.6 * W and b[1] > 0.3 * H and len(t) > 4), default=(0, None))[1]
        if lab and norm(lab) == norm(BOX_LAST_ROW):
            return f
        press("down", hold=0.12, wait=0.5)
    raise Abort("Couldn't reach the bottom of the box score")


def capture_game(gdir, all_rows):
    """From the box score: top and bottom screenshots, then every player-stats category."""
    os.makedirs(gdir, exist_ok=True)
    wait_for(["BOX_SCORE"], timeout=10)
    snap(os.path.join(gdir, "box_top.png"))
    f = box_score_bottom()
    subprocess.run(["cp", f.path, os.path.join(gdir, "box_bottom.png")])
    for attempt in range(3):                 # R1 is sometimes dropped
        press("r1", wait=2.0)
        f, s = wait_for(["GAME_STATS", "BOX_SCORE"], timeout=15)
        if s == "GAME_STATS":
            break
        log(f"  R1 didn't register (attempt {attempt + 1}), pressing again")
    else:
        raise Abort("R1 never opened the game's player stats")
    _, first = game_chips(f)
    if not first:
        raise Abort("Can't read the game stats category chip")
    seen, cat = [], first
    for _ in range(12):
        seen.append(cat)
        capture_game_category(os.path.join(gdir, "stats"), f"{len(seen):02d}_{norm(cat).lower()}", cat, all_rows)
        cat = next_category(cat, "GAME_STATS")
        if norm(cat) == norm(first):
            break
    return seen


def run_games(out, week=None, all_rows=False, max_games=None):
    """Every game of one week (default: the previous week): box score + both teams' player
    stats, into games/<NN>_<away>_at_<home>/. Cross only through cross("SCHED_LIST"), which
    checks JR's box-score rule."""
    log("== Games ==")
    f = open_league_schedule()
    target = clean_week(week) if week else previous_week(CURRENT_WEEK)
    log(f"Current week: {CURRENT_WEEK}. Games of: {target}")
    f = go_to_week(f, target, out)
    if week_index(week_label(f) or "") != week_index(target):
        raise Abort(f"Not on {target}")
    done, results = [], []
    while True:
        f, s = wait_for(["SCHED_LIST"], timeout=10)
        row = highlighted_schedule_row(f)
        matchup = next((t for t in row or [] if "@" in t), None)
        result = next((t for t in row or [] if RESULT_RE.match(t)), None)
        if not matchup:
            raise Abort(f"Can't read the highlighted game: {row}")
        log(f"Game {len(done) + 1}: {matchup} {result}")
        away, home = [norm(x) for x in matchup.split("@")]
        gdir = os.path.join(out, "games", f"{len(done) + 1:02d}_{away.lower()}_at_{home.lower()}")
        if os.path.exists(os.path.join(gdir, "DONE")):
            log("  already captured (redo after a stream restart)")
        else:
            shutil.rmtree(gdir, ignore_errors=True)      # a game cut short by a frozen stream
            cross_into("SCHED_LIST", ["BOX_SCORE"], timeout=10)
            cats = capture_game(gdir, all_rows)
            open(os.path.join(gdir, "DONE"), "w").write(",".join(cats) + "\n")
            log(f"  {len(cats)} categories")
            for attempt in range(3):            # Back is sometimes dropped (game 16, Oct 14)
                press("circle", wait=2.0)
                f, s = wait_for(["SCHED_LIST", "GAME_STATS"], timeout=15)
                if s == "SCHED_LIST":
                    break
                log(f"  Back didn't register (attempt {attempt + 1}), pressing again")
            else:
                raise Abort("Back never returned to the League Schedule")
        done.append(norm(matchup))
        results.append((matchup, result))
        if max_games and len(done) >= max_games:
            break
        # next game: Down until the highlighted matchup changes (the end of the list: it doesn't)
        moved = False
        for attempt in range(4):
            press("down", hold=0.12, wait=0.8)
            f = Frame()
            nxt = next((t for t in highlighted_schedule_row(f) or [] if "@" in t), None)
            if nxt and norm(nxt) not in done:
                moved = True
                break
        if not moved:
            break
    with open(os.path.join(out, "games", "games.txt"), "w") as fh:
        for i, (mu, res) in enumerate(results, 1):
            fh.write(f"{i:02d}\t{target}\t{mu}\t{res}\n")
    log(f"Captured {len(done)} games of {target}")


def run_season_schedule(out, weeks=18):
    """League Schedule weeks 1-18 (start of season: every game as scheduled). Each week's
    pages go to season_schedule/season_week_N_NNN.png. Presses nothing but L2 and Down."""
    log("== Season schedule ==")
    f = open_league_schedule()
    folder = os.path.join(out, "season_schedule")
    for n in range(1, weeks + 1):
        target = f"SEASON WEEK {n}"
        f = go_to_week(f, target, out)
        log(f"On {week_label(f)}")
        pages = capture_list(folder, f"season_week_{n:02d}", stop_at_zeros=False)
        log(f"  {target}: {pages} pages")
        f, _ = wait_for(["SCHED_LIST"], timeout=10)


# --------------------------------------------------------------- roster ----
# Manage Roster > Roster (mapped Oct 13 with --step / --probe-l2):
#  * The card on the right shows the highlighted row's player; Down moves the highlight one
#    row and the card follows within ~0.2 s. The highlight moves down to the 8th visible row,
#    then the list scrolls under it; at the end it drops to the bottom row and Down does nothing.
#  * 14 rows show at once, sorted by OVR. DEV is an icon in the list; the card spells it out.
#  * L2 held opens the team dropdown (same mechanics as the stats scope list, but higher on
#    screen); the roster behind it reloads as the highlight moves. After VIKINGS comes NFL.
#  * Down presses get dropped now and then (chiaki's decoder falls behind), so every press is
#    checked against the card.
# Card read with Apple Vision on a crop of the card. Vision drops or misreads the digit 1 in
# this font (31 -> missing, 6'1" -> 6'] or 6'7", 5'11" -> 5'71"), so any card with a missing
# or implausible value is flagged for extract.py to re-read with Gemini.
CARD_BOX = (0.740, 0.339, 0.980, 0.561)      # fractions of the window: card text area
LIST_BOX = (0.015, 0.245, 0.715, 0.830)      # roster table rows
CARD_FIELDS = ["first", "last", "number", "position", "archetype", "role", "dev",
               "age", "height", "weight", "experience"]
DEV_TRAITS = ["SUPERSTAR X-FACTOR", "SUPERSTAR", "STAR", "NORMAL"]
HT_RE = re.compile(r"^([4-7])'(\d{1,2})\"$")
VALUE_LABELS = ("AGE", "HT", "WT", "EXP", "MORALE")
POSITIONS = {"QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT", "LEDG", "REDG", "DT",
             "MIKE", "WILL", "SAM", "CB", "FS", "SS", "K", "P", "LS"}
# value columns: left edge as a fraction of the card crop's width (measured Oct 13)
CARD_CROP_W, CARD_CROP_H = 726, 420
VALUE_COLUMNS = {"age": 0.060, "height": 0.245, "weight": 0.427, "experience": 0.613}
# player roles seen on cards (Oct 13); used only when OCR drops the | before the role
ROLES = ["FRANCHISE QB", "ELITE PLAYER", "IMPACT STARTER", "RISING STAR", "BORDERLINE STARTER",
         "QUALITY BACKUP", "DEPTH FILLER", "PROJECT PLAYER", "SPECIAL TEAMS STAR"]
# roster table columns: x centre as a fraction of the window width (header positions, Oct 13)
LIST_COLUMNS = {"POS": 0.279, "AGE": 0.341, "OVR": 0.403}
LIST_PAGE_ROWS = 14


def grab():
    """The chiaki-ng window as a CGImage, in-process (~50 ms vs ~450 ms for screencapture)."""
    cg = Quartz.CGWindowListCreateImage(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
                                        WIN_ID, Quartz.kCGWindowImageBoundsIgnoreFraming)
    if cg is None or Quartz.CGImageGetWidth(cg) < 100:
        snap()                     # let snap() find the window again / recover a blank stream
        cg = Quartz.CGWindowListCreateImage(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
                                            WIN_ID, Quartz.kCGWindowImageBoundsIgnoreFraming)
        if cg is None:
            raise Abort("Can't grab the chiaki-ng window")
    return cg


def crop_cg(cg, frac):
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    x0, y0, x1, y1 = frac
    return Quartz.CGImageCreateWithImageInRect(cg, ((W * x0, H * y0), (W * (x1 - x0), H * (y1 - y0))))


def ocr_cg(cg):
    """Like ocr(), on an in-memory CGImage. Boxes are in that image's pixels."""
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None).performRequests_error_([req], None)
    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if cand:
            bb = obs.boundingBox()
            x0, y0 = bb.origin.x * W, (1 - bb.origin.y - bb.size.height) * H
            out.append((str(cand[0].string()).translate(HOMOGLYPHS).upper(), (x0, y0, x0 + bb.size.width * W, y0 + bb.size.height * H)))
    return out


def cg_to_pil(cg):
    from Foundation import NSMutableData
    data = NSMutableData.data()
    dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cg, None)
    Quartz.CGImageDestinationFinalize(dest)
    import io
    return Image.open(io.BytesIO(bytes(data))).convert("L")


def card_changed(cg_a, cg_b):
    """True when two card crops show different cards (same test as same_table)."""
    from PIL import ImageChops
    a, b = cg_to_pil(cg_a).resize((360, 210)), cg_to_pil(cg_b).resize((360, 210))
    d = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0)
    return d.histogram()[255] / (d.width * d.height) > 0.01


def types_frame_cg(cg):
    """save_debug() wants an object with .path; write the CGImage to the temp frame file."""
    save_cg(cg, TMP)
    return types_frame(TMP)


def save_cg(cg, path):
    dest = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(path), "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cg, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise Abort(f"Couldn't write {path}")


def _rows(items, tol=0.4):
    """Group OCR items into text lines (by vertical overlap), top to bottom, each left to right."""
    lines = []
    for t, b in sorted(items, key=lambda i: i[1][1]):
        h = b[3] - b[1]
        for line in lines:
            lb = line[0][1]
            if abs((b[1] + b[3]) / 2 - (lb[1] + lb[3]) / 2) < tol * max(h, lb[3] - lb[1]):
                line.append((t, b))
                break
        else:
            lines.append([(t, b)])
    return [sorted(l, key=lambda i: i[1][0]) for l in lines]


def parse_card(items):
    """Card OCR items (card-crop pixels) -> dict of CARD_FIELDS (None where unread)."""
    c = dict.fromkeys(CARD_FIELDS)
    lines = _rows(items)
    # The number line ("#64 DT | Nose Tackle | Elite Player") sits at a fixed height on the
    # card (centre ~43% of the crop), so find it by position: its digits can be misread
    # ("#0" as "#O"), and the name above it must still be found.
    num_i = next((i for i, l in enumerate(lines)
                  if 0.37 * CARD_CROP_H < (l[0][1][1] + l[0][1][3]) / 2 < 0.50 * CARD_CROP_H), None)
    if num_i is not None:
        text = " | ".join(t for t, _ in lines[num_i])
        m = re.match(r"^#\s?([\dOIL]{1,2})\s*([A-Z@0]{1,4})(?![A-Z])(.*)$", text)
        if m:
            c["number"] = int(m.group(1).translate(str.maketrans("OIL", "011")))
            pos = m.group(2).replace("@", "Q").replace("0", "O")      # "#10 @B" = QB
            c["position"] = pos if pos in POSITIONS else None
            # separators come back as |, I, 1 or l ("#54 SAM 1 PASS COVERAGE | DEPTH FILLER")
            rest = [p.strip() for p in re.split(r"\s*\|\s*|\s+[I1L!]\s+", " " + m.group(3) + " ") if p.strip()]
            if len(rest) == 1:              # separator not read: split off a known role
                for role in ROLES:
                    if rest[0].endswith(" " + role):
                        rest = [rest[0][: -len(role)].strip(), role]
                        break
            if rest:
                c["archetype"] = rest[0].title()
            if len(rest) > 1:
                c["role"] = rest[1].title()
        above = [l for l in lines[:num_i]]
        if above:
            # last name is the tallest text above the number line; first name is the line over it
            li = max(range(len(above)), key=lambda i: max(b[3] - b[1] for _, b in above[i]))
            c["last"] = " ".join(t for t, _ in above[li]).title()
            if li > 0:
                c["first"] = " ".join(t for t, _ in above[li - 1]).title()
    # The label row (AGE HT WT EXP MORALE) sits at a fixed place; any one label read is
    # enough to find it, and each value is found by its column, since labels go unread too.
    labs = [b for t, b in items if t in VALUE_LABELS]
    if not labs:
        return c
    lab_top, lab_bottom = min(b[1] for b in labs), max(b[3] for b in labs)
    if num_i is not None:
        dev_text = " ".join(t for l in lines[num_i + 1:] for t, b in l if b[3] <= lab_top + 5)
        dev_word = re.split(r"\s*\|\s*", dev_text)[0].replace(" ", "")
        c["dev"] = next((d.title() for d in DEV_TRAITS if d.replace(" ", "") in dev_word), None)
    W = max(b[2] for _, b in items)
    W = CARD_CROP_W if W < CARD_CROP_W else W
    for key, x in VALUE_COLUMNS.items():
        cand = [t for t, b in items if t not in VALUE_LABELS and lab_bottom - 10 < b[1] < lab_bottom + 40
                and abs(b[0] - x * CARD_CROP_W) < 0.06 * CARD_CROP_W]
        if not cand:
            continue
        v = cand[0].replace(" ", "")
        if key == "height":
            c[key] = v
        elif key == "experience" and v == "ROOKIE":
            c[key] = 0                      # years_of_experience 0 = rookie
        elif v.isdigit():
            c[key] = int(v)
    return c


def card_flags(c):
    """Reasons this card read can't be trusted as-is (empty list = fine)."""
    flags = [f"missing_{k}" for k in CARD_FIELDS if c.get(k) is None and k != "role"]
    if c.get("age") is not None and not 20 <= c["age"] <= 45:
        flags.append("bad_age")
    if c.get("weight") is not None and not 150 <= c["weight"] <= 420:
        flags.append("bad_weight")
    if c.get("experience") is not None and not 0 <= c["experience"] <= 25:
        flags.append("bad_experience")
    if c.get("height") is not None:
        m = HT_RE.match(c["height"])
        if not m or int(m.group(2)) > 11 or not 66 <= int(m.group(1)) * 12 + int(m.group(2)) <= 84:
            flags.append("bad_height")
        elif m.group(1) == "6" and int(m.group(2)) >= 7 and (c.get("weight") or 0) < 280:
            flags.append("suspect_height")          # 6'1" is often read as 6'7"
    return flags


SUFFIX_RE = re.compile(r"\s+(?:[IL1]{2,3}|IV|V|JR\.?|SR\.?)$", re.I)


def _strip_suffix(name):
    """Drop a name suffix, OCR variants included: "W.NOLEN IIL" / "Nolen Iii" -> "...NOLEN"."""
    return SUFFIX_RE.sub("", name.strip())


def line_cells(lines, i, win_w, list_x0):
    """{"i", "NAME", "POS", "AGE", "OVR"} of roster-table line i, by column position."""
    line = lines[i]
    row = {"i": i, "NAME": line[0][0]}
    for col, x in LIST_COLUMNS.items():
        for t, b in line[1:]:
            if abs((b[0] + b[2]) / 2 + list_x0 - x * win_w) < 0.025 * win_w:
                row[col] = t
    return row


def _matches(cells, card):
    """A table line agrees with a card: same position, and same age where both were read."""
    if not card.get("position") or norm(cells.get("POS", "")) != card["position"]:
        return False
    age = cells.get("AGE", "")
    return not (card.get("age") and age.isdigit() and int(age) != card["age"])


def check_step(lines, prev, card, win_w, list_x0):
    """Did the highlight move exactly one row from prev to card? Returns ("ok", 0),
    ("skipped", n) or ("unknown", 0). Uses names where OCR read them, otherwise the line
    next to the one player that was found, matched by position and age."""
    p = list_row_for(lines, prev, win_w, list_x0)
    c = list_row_for(lines, card, win_w, list_x0)
    p = None if p and p.get("ambiguous") else p
    c = None if c and c.get("ambiguous") else c
    if p and c:
        d = c["i"] - p["i"]
        return ("ok", 0) if d == 1 else (("skipped", d - 1) if d > 1 else ("unknown", 0))
    if p and p["i"] + 1 < len(lines) and _matches(line_cells(lines, p["i"] + 1, win_w, list_x0), card):
        return "ok", 0
    if c and c["i"] > 0 and _matches(line_cells(lines, c["i"] - 1, win_w, list_x0), prev):
        return "ok", 0
    return "unknown", 0


def list_row_for(lines, card, win_w, list_x0):
    """In the roster-table OCR lines (_rows of the LIST_BOX crop, starting list_x0 px into a
    window win_w px wide), the row naming this card's player ("G.CRAWFORD"):
    {"i": line index, "NAME", "POS", "AGE", "OVR"} as read (a column is absent when OCR
    skipped it), or None. A name on several rows is settled by position; if that fails the
    row comes back with "ambiguous": True."""
    if not card or not card.get("last"):
        return None
    last = norm(_strip_suffix(card["last"]))
    first = norm(card["first"] or "")[:1]
    found, by_last = [], []
    for i, line in enumerate(lines):
        name = norm(_strip_suffix(line[0][0]))
        if name[:1] in "01":                  # "0.CAMPBELL" is O.Campbell (Steelers, Oct 13)
            name = "OI"[int(name[0])] + name[1:]
        if name.endswith(last):
            by_last.append(i)
            if not first or name.startswith(first):
                found.append(line_cells(lines, i, win_w, list_x0))
    if not found and len(by_last) == 1:       # initial misread, but only one row has the name
        found = [line_cells(lines, by_last[0], win_w, list_x0)]
    # the same list name can repeat (Seahawks: S.Lewis LT and S.Lewis HB on adjacent rows)
    if len(found) > 1:
        same_pos = [r for r in found if card.get("position") and norm(r.get("POS", "")) == card["position"]]
        if len(same_pos) == 1:
            return same_pos[0]
        found[0]["ambiguous"] = True        # can't tell which row: don't use it to check a step
    return found[0] if found else None


def read_card(prev_key, timeout=3.0):
    """Read the card until two reads in a row agree and it shows a different player than
    prev_key. Values missing in one read are filled from the other. Returns (card, cg) or
    (None, cg) if the card never moved off prev_key."""
    end = time.time() + timeout
    last = None
    while True:
        cg = grab()
        c = parse_card(ocr_cg(crop_cg(cg, CARD_BOX)))
        key = (c["first"], c["last"], c["number"], c["position"])
        if last is not None and key == last[0] and key != prev_key and c["last"]:
            # mid-animation the card shows the new name over the previous player's numbers,
            # so both reads must also agree on every value they both have
            if all(c[k] == last[1][k] for k in CARD_FIELDS if c[k] is not None and last[1][k] is not None):
                merged = {k: (c[k] if c[k] is not None else last[1][k]) for k in CARD_FIELDS}
                return merged, cg
        last = (key, c)
        if time.time() > end:
            return None, cg
        time.sleep(0.12)


def card_key(c):
    return (c["first"], c["last"], c["number"], c["position"]) if c else None


def roster_dropdown_selected(f):
    return dropdown_selected(f, top=0.20, bottom=0.76)


# Team nicknames as the roster's team dropdown shows them (all 32 seen on Oct 13)
TEAMS = {"49ERS", "BEARS", "BENGALS", "BILLS", "BRONCOS", "BROWNS", "BUCCANEERS", "CARDINALS",
         "CHARGERS", "CHIEFS", "COLTS", "COMMANDERS", "COWBOYS", "DOLPHINS", "EAGLES", "FALCONS",
         "GIANTS", "JAGUARS", "JETS", "LIONS", "PACKERS", "PANTHERS", "PATRIOTS", "RAIDERS",
         "RAMS", "RAVENS", "SAINTS", "SEAHAWKS", "STEELERS", "TEXANS", "TITANS", "VIKINGS"}


def roster_team(f):
    """The team chip (left of R2), always one of TEAMS. OCR sometimes misses the R2 icon
    chip_labels() anchors on, so fall back to the chip's position (top-left, ~18% down);
    anything else (once "NAME", the column header, right after a stream restart) is re-read."""
    for attempt in range(6):
        team, view = chip_labels(f)
        if team and norm(team) in TEAMS:
            return norm(team)
        W, H = f.size
        chip = [norm(t) for t, b in f.items if b[0] < W * 0.15 and H * 0.15 < b[1] < H * 0.21]
        chip = [t for t in chip if t in TEAMS]
        if chip:
            return chip[0]
        time.sleep(0.7)
        f = Frame()
    return None


def next_roster_team(debug_dir, current):
    """Hold L2, move the team list down one team (skipping NFL), release. Returns the team.
    The L2 hold itself is sometimes dropped (Raiders, Oct 13): then release, check the chip
    (a late press may have switched team already, which then counts as the move) and retry."""
    os.makedirs(debug_dir, exist_ok=True)
    for attempt in range(1, 4):
        key_down("l2")
        opened = False
        try:
            t_end = time.time() + 3.0
            while time.time() < t_end:
                time.sleep(0.35)
                f = Frame()
                sel = roster_dropdown_selected(f) if "SELECT" in f.text else None
                if sel:
                    opened = True
                    break
            if opened:
                return _move_team_list(f, sel, debug_dir)
        finally:
            key_up("l2")                    # no-op if _move_team_list already released it
        save_debug(f, f"team_list_not_open_{attempt}")
        time.sleep(1.5)
        f, _ = wait_for(["ROSTER_LIST"], timeout=10)
        team = roster_team(f)
        if team and norm(team) != norm(current):
            log(f"  L2 acted late and switched to {team}; taking that as the next team")
            return team
        log(f"  Team list didn't open (attempt {attempt}); trying again")
    raise Abort("Team list did not open while holding L2 (3 tries). If chiaki-ng's picture has "
                "stopped updating, the stream is frozen; once it's back, --resume this run")


def _move_team_list(f, start, debug_dir):
    """With the team list open (L2 held by the caller): move down one team, skipping NFL.
    Returns the team selected once L2 is released."""
    for _ in range(8):
        key_down("down"); time.sleep(0.1); key_up("down")
        time.sleep(0.5)
        f = Frame()
        new = roster_dropdown_selected(f)
        for _retry in range(3):
            if new:
                break
            time.sleep(0.3)
            f = Frame()
            new = roster_dropdown_selected(f)
        if new and norm(new) != norm(start) and norm(new) in TEAMS:
            break
        if new and norm(new) in DIVISION_ENTRIES:
            start = new                 # passed NFL; keep going to the next team
    else:
        raise Abort(f"Couldn't move the team list off {start}")
    subprocess.run(["cp", f.path, os.path.join(debug_dir, f"{norm(new).lower()}.png")])
    log(f"{new} highlighted; releasing L2")
    key_up("l2")
    time.sleep(1.5)
    f, _ = wait_for(["ROSTER_LIST"], timeout=10)
    team = roster_team(f)
    if not team or norm(team) != norm(new):
        raise Abort(f"After selecting {new}, the team chip reads {team}")
    return team


def roster_to_top():
    """Move the highlight to the first row: press Up until it sits on the top visible row
    and Up changes nothing."""
    for _ in range(80):
        cg = grab()
        c = parse_card(ocr_cg(crop_cg(cg, CARD_BOX)))
        rows = _rows(ocr_cg(crop_cg(cg, LIST_BOX)))
        top = norm(rows[0][0][0]) if rows else ""
        if c["last"] and top.endswith(norm(c["last"])):
            press("up", hold=0.1, wait=0.4)
            if card_key(parse_card(ocr_cg(crop_cg(grab(), CARD_BOX)))) == card_key(c):
                return
        else:
            press("up", hold=0.07, wait=0.15, times=5)
    raise Abort("Couldn't get to the top of the roster list")


def open_roster():
    f, s = look()
    if s == "ROSTER_LIST":
        return f
    f, s = go_hub()
    if s != "HUB_ROSTER":
        log("Switching to MANAGE ROSTER tab")
        move_to(f, HUB_TABS, "MANAGE ROSTER", "l1", "r1", "hub tab")
        f, s = wait_for(["HUB_ROSTER"])
    f = move_to(f, ROSTER_MENU, "ROSTER", "up", "down", "Manage Roster menu item")
    return cross_into("HUB_ROSTER", ["ROSTER_LIST"], timeout=8)[0]


def capture_team_roster(out, index, team, writer):
    """Every row of one team's roster: card read on the Mac (one card crop per row) plus a
    full screenshot per 14-row list page for Gemini. Returns the number of players."""
    folder = os.path.join(out, "roster", f"{index:02d}_{norm(team).lower()}")
    os.makedirs(folder, exist_ok=True)
    roster_to_top()
    prev, prev_card_cg, row, pages, skips = None, None, 0, 0, 0
    while True:
        card, cg = read_card(card_key(prev))
        if card is None and prev is not None and card_changed(crop_cg(cg, CARD_BOX), prev_card_cg):
            # a new card that OCR can't settle on: record it as read, never press blindly
            card = parse_card(ocr_cg(crop_cg(cg, CARD_BOX)))
            card["_unreadable"] = True
        if card is None and prev is not None:
            for attempt in range(3):              # dropped press, or the end of the list
                press("down", hold=0.1, wait=0.25)
                card, cg = read_card(card_key(prev))
                if card:
                    break
            if card is None:
                # The card also stops changing when chiaki's stream freezes (Rams, Oct 13:
                # recorded 40 of 53). Only the last row of the list is the real end.
                W = Quartz.CGImageGetWidth(cg)
                lines = _rows(ocr_cg(crop_cg(cg, LIST_BOX)))
                at = list_row_for(lines, prev, W, W * LIST_BOX[0])
                if at and at["i"] >= len(lines) - 1:
                    break                          # end of list
                save_debug(types_frame_cg(cg), f"{norm(team).lower()}_stuck")
                raise Abort(f"{team}: the card stopped changing at {prev['last']}, which isn't the "
                            "last row of the list. The chiaki-ng stream may be frozen; check it, "
                            "then --resume this run")
        if card is None:
            raise Abort(f"{team}: can't read the first player's card")
        W = Quartz.CGImageGetWidth(cg)
        lines = _rows(ocr_cg(crop_cg(cg, LIST_BOX)))
        list_row = list_row_for(lines, card, W, W * LIST_BOX[0])
        flags = card_flags(card)
        if card.pop("_unreadable", False):
            flags.append("unreadable_card")
        # The new player must be on the line right under the previous one. A late press plus
        # the retry moves two rows; a late key-up reads as Down held and scrolls many rows
        # (Seahawks, Oct 13: 18 rows). Step back when the gap is known; stop when the step
        # can't be checked at all, rather than record a roster with holes.
        if prev is not None:
            verdict, n = check_step(lines, prev, card, W, W * LIST_BOX[0])
            for _retry in range(3):               # OCR misses: read the list again
                if verdict != "unknown":
                    break
                time.sleep(0.3)
                cg = grab()
                lines = _rows(ocr_cg(crop_cg(cg, LIST_BOX)))
                verdict, n = check_step(lines, prev, card, W, W * LIST_BOX[0])
            if verdict == "skipped":
                log(f"  {team}: skipped {n} row(s) after {prev['last']}; stepping back")
                press("up", hold=0.1, wait=0.6, times=n)
                skips += 1
                if skips > 20:
                    raise Abort(f"{team}: rows keep getting skipped; stopping")
                continue
            if verdict == "unknown":
                save_debug(types_frame_cg(cg), f"{norm(team).lower()}_unverified_step")
                raise Abort(f"{team}: can't confirm the step from {prev['last']} to "
                            f"{card.get('last')} on the list; stopping (--resume redoes this team)")
            list_row = list_row_for(lines, card, W, W * LIST_BOX[0])
        if list_row is None:
            flags.append("not_in_list")
        else:
            if card["position"] and "POS" in list_row and norm(list_row["POS"]) != card["position"]:
                flags.append("list_position_mismatch")
            if card["age"] and list_row.get("AGE", "").isdigit() and int(list_row["AGE"]) != card["age"]:
                flags.append("list_age_mismatch")
        card_png = os.path.join(folder, f"card_{row:03d}.png")
        save_cg(crop_cg(cg, CARD_BOX), card_png)
        # list pages: rows 0-13 at row 0; once the list scrolls the highlight sits on the 8th
        # visible row, so rows 14-27 show at row 21, 28-41 at row 35, ...
        if row == 0 or (row >= 21 and (row - 21) % LIST_PAGE_ROWS == 0):
            pages += 1
            save_cg(cg, os.path.join(folder, f"list_{pages:02d}.png"))
        writer.writerow([team, row] + [card[k] if card[k] is not None else "" for k in CARD_FIELDS]
                        + [";".join(flags), os.path.relpath(card_png, out)])
        prev, prev_card_cg, row = card, crop_cg(cg, CARD_BOX), row + 1
        press("down", hold=0.1, wait=0.15)
    # the last page: the list's final 14 rows (highlight on the bottom row)
    save_cg(grab(), os.path.join(folder, f"list_{pages + 1:02d}.png"))
    log(f"  {team}: {row} players")
    if row < 40:
        log(f"  WARNING {team}: only {row} players; check {folder}")
    return row


class _Rows(list):
    """Collects one team's cards.csv rows until the team is complete."""
    def writerow(self, row):
        self.append(row)


def run_roster(out, max_teams=32, resume=False):
    """All teams, starting at the one the list opens on (the user's team) and following the
    team dropdown until it comes back round. A team's rows reach cards.csv only once the team
    is complete, and roster/done.txt lists finished teams. If a team or a team switch fails
    while the stream is frozen, the stream is restarted (STREAM_RESTARTS per run) and the
    team captured again from the top. resume=True continues a stopped run in its folder:
    finished teams are skipped."""
    import csv, shutil
    log("== Rosters ==")
    rdir = os.path.join(out, "roster")
    os.makedirs(rdir, exist_ok=True)
    cards_path, done_path = os.path.join(rdir, "cards.csv"), os.path.join(rdir, "done.txt")
    done = {}                                       # norm(team) -> folder index
    if resume and os.path.exists(done_path):
        for line in open(done_path):
            if line.strip():
                idx, t = line.split()
                done[t] = int(idx)
        kept = [r for r in csv.reader(open(cards_path))][1:] if os.path.exists(cards_path) else []
        kept = [r for r in kept if norm(r[0]) in done]
        log(f"Resuming {out}: {len(done)} teams already done")
    else:
        kept = []
        open(done_path, "w").close()

    def clear_partial():
        for d in glob.glob(os.path.join(rdir, "[0-9]*_*")):  # folders of teams cut short
            if norm(os.path.basename(d).split("_", 1)[1]) not in done:
                shutil.rmtree(d)

    def recover(e):
        """Restart the stream if it is frozen; otherwise (or out of restarts) re-raise."""
        recover_stream(e)
        clear_partial()
        return roster_team(open_roster())

    clear_partial()
    first = roster_team(open_roster())
    if not first:
        raise Abort("Can't read the roster team chip")
    captured, total = 0, len(kept)
    with open(cards_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["team", "row"] + CARD_FIELDS + ["flags", "card_png"])
        w.writerows(kept)
        team, visited = first, 0
        while True:
            if norm(team) in done:
                log(f"Team: {team} (already captured)")
            else:
                log(f"Team: {team}")
                idx = max(list(done.values()) + [0]) + 1
                rows = _Rows()
                try:
                    n = capture_team_roster(out, idx, team, rows)
                except Abort as e:
                    team = recover(e)
                    continue                            # same team again, from the top
                w.writerows(rows)
                fh.flush()
                total += n
                captured += 1
                done[norm(team)] = idx
                with open(done_path, "a") as dh:
                    dh.write(f"{idx} {norm(team)}\n")
            visited += 1
            if captured >= max_teams or len(done) >= 32:
                break
            try:
                team = next_roster_team(os.path.join(out, "team_debug"), team)
            except Abort as e:
                team = recover(e)
                continue
            if norm(team) == norm(first) and visited > 1:
                break                                   # back round to the first team
    log(f"Captured {len(done)} teams, {total} players")
    if max_teams >= 32 and len(done) != 32:
        raise Abort(f"Expected 32 teams, captured {len(done)}: {sorted(done)}")


def probe_l2(out):
    """Record how the L2 dropdown behaves on Player Stats (scope) or the roster list (team).
    Holds L2 the whole time, presses Up 3x then Down 3x (net zero movement),
    screenshots after every step, then releases. Saves PNGs + OCR text."""
    import json
    folder = os.path.join(out, "probe")
    os.makedirs(folder, exist_ok=True)
    f, s = look()
    if s not in ("PLAYER_STATS", "ROSTER_LIST"):
        raise Abort(f"Open Player Stats or the Manage Roster list first (currently on {s})")
    log(f"Before: chips = {chip_labels(f)}")
    notes = {}

    def rec(name):
        p = os.path.join(folder, f"{len(notes):02d}_{name}.png")
        snap(p)
        notes[os.path.basename(p)] = [t for t, _ in ocr(p)]
        log(f"  saved {os.path.basename(p)}")

    rec("before")
    key_down("l2")
    try:
        for i in range(5):                     # watch the dropdown open
            time.sleep(0.35)
            rec(f"held_{(i + 1) * 0.35:.2f}s")
        for i in range(3):
            key_down("up"); time.sleep(0.1); key_up("up"); time.sleep(0.5)
            rec(f"held_up{i + 1}")
        for i in range(3):
            key_down("down"); time.sleep(0.1); key_up("down"); time.sleep(0.5)
            rec(f"held_down{i + 1}")
    finally:
        key_up("l2")
    time.sleep(1.5)
    rec("after_release")
    f, _ = look()
    log(f"After: chips = {chip_labels(f)}")
    with open(os.path.join(folder, "ocr.json"), "w") as fh:
        json.dump(notes, fh, indent=1)
    log(f"Probe done. Files in {folder}")


EXPLORE_DIR = os.path.join(OUT_ROOT, f"{datetime.date.today():%Y-%m-%d}_explore", "explore")
DANGER_TEXT = ("ADVANCE WEEK", "FORCE WIN")


def step(key, wait=1.5, allow_cross=False):
    """Mapping tool, not part of any capture: press one key (or none), wait, save the
    screenshot and its OCR text as explore/NN_<key>.png/.txt and print what classify()
    makes of it. All of one day's steps share one folder, numbered in order."""
    if key == "cross":
        f, s = look()
        if any(d in f.text for d in DANGER_TEXT):
            raise Abort(f"Refusing Cross: the screen shows {[d for d in DANGER_TEXT if d in f.text]}")
        if not allow_cross:
            raise Abort("Cross can trigger actions on an unmapped screen; add --allow-cross once JR has OK'd it")
    if key != "none":
        log(f"Pressing {key}")
        press(key, wait=wait)
    os.makedirs(EXPLORE_DIR, exist_ok=True)
    n = len([x for x in os.listdir(EXPLORE_DIR) if x.endswith(".png")])
    base = os.path.join(EXPLORE_DIR, f"{n:02d}_{key}")
    f, s = look()
    subprocess.run(["cp", f.path, base + ".png"])
    with open(base + ".txt", "w") as fh:
        fh.write(f"screen: {s}\n")
        for t, (x0, y0, x1, y1) in f.items:
            fh.write(f"{t}\t{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}\t{brightness(f.path, (x0, y0, x1, y1)):.0f}\n")
    print("Screen:", s)
    print("Saved:", base + ".png")
    print("Text:", f.text[:600])


def main():
    global WIN_ID, PID, FOREGROUND
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="classify the current screen, no key presses")
    ap.add_argument("--probe-l2", action="store_true")
    ap.add_argument("--scope-only", action="store_true", help="from Player Stats: just switch scope to NFL")
    ap.add_argument("--scope", metavar="TEAM", help="--only stats: one team's Season Stats (team nickname, e.g. SAINTS)")
    ap.add_argument("--category", action="append", metavar="CAT",
                    help="--only stats --scope: a category to capture (repeat for several, e.g. KICKING)")
    ap.add_argument("--only", choices=["stats", "team", "schedule", "roster", "season-schedule", "games"],
                    help="roster and season-schedule are never part of the default run")
    ap.add_argument("--games", type=int, help="--only games: stop after this many games (testing)")
    ap.add_argument("--teams", type=int, default=32, help="--only roster: stop after this many teams (testing)")
    ap.add_argument("--resume", metavar="RUN", help="--only roster / games: continue a stopped run in captures/RUN; "
                    "--scope: write into that (weekly) run's folder")
    ap.add_argument("--week", help='e.g. "SEASON WEEK 12" (default: the week before the current one)')
    ap.add_argument("--all-rows", action="store_true", help="don't stop stats lists at all-zero rows")
    ap.add_argument("--foreground", action="store_true", help="bring chiaki-ng to front and send global keys")
    ap.add_argument("--step", choices=sorted(KEYCODES) + ["none"],
                    help="mapping tool: press one key (none = just capture), save screenshot + OCR to explore/")
    ap.add_argument("--wait", type=float, default=1.5, help="--step: seconds to wait after the press")
    ap.add_argument("--allow-cross", action="store_true", help="--step cross: JR has OK'd Cross on this screen")
    a = ap.parse_args()

    WIN_ID, PID = find_window()
    FOREGROUND = a.foreground
    if not a.test:
        try:
            ensure_window_size()
        except Abort as e:
            sys.exit(f"STOPPED: {e}")
    if FOREGROUND:
        activate_app()
    global DEBUG_DIR
    out = os.path.join(OUT_ROOT, a.resume or datetime.datetime.now().strftime("%Y-%m-%d_%H%M"))
    if a.resume and not os.path.isdir(out):
        sys.exit(f"No capture folder {out}")
    DEBUG_DIR = os.path.join(out, "stop_debug")

    try:
        if a.test:
            f, s = look()
            print("Screen:", s)
            if s in ("HUB", "HUB_LEAGUE", "HUB_ROSTER"):
                print("Highlighted tab:", f.highlighted(HUB_TABS))
            if s == "HUB_LEAGUE":
                print("Highlighted menu item:", f.highlighted(LEAGUE_MENU))
            if s == "PLAYER_STATS":
                print("Chips (scope, category):", chip_labels(f))
            if s == "SCHED_LIST":
                print("Week:", week_label(f))
            print("Text:", f.text[:600])
            return
        if a.step:
            step(a.step, a.wait, a.allow_cross)
            return
        if a.probe_l2:
            probe_l2(out)
            return
        if a.scope_only:
            set_scope("NFL", debug_dir=os.path.join(out, "scope_debug"))
            return
        clear = lambda *dirs: (lambda: [shutil.rmtree(os.path.join(out, d), ignore_errors=True) for d in dirs])
        if a.scope:
            if a.only != "stats" or not a.category:
                sys.exit("--scope needs --only stats and at least one --category")
            def clear_team():                  # a retry redoes this team only; other teams' files stay
                for p in glob.glob(os.path.join(out, "stats_scope", f"{norm(a.scope).lower()}_*")):
                    os.remove(p)
            run_mode(f"{a.scope} Season Stats", lambda: run_scoped_stats(out, a.scope, a.category, a.all_rows),
                     clear_team)
            log("Returning to the hub")
            go_hub()
            log(f"Done. Screenshots in {out}")
            return
        if a.only in (None, "stats"):
            run_mode("Season Stats", lambda: run_stats(out, a.all_rows), clear("stats", "scope_debug"))
        if a.only in (None, "team"):
            run_mode("Team Stats", lambda: run_team_stats(out), clear("team_stats"))
        if a.only in (None, "schedule"):
            run_mode("the schedule", lambda: run_schedule(out, a.week), clear("schedule", "week_debug"))
        if a.only == "roster":
            tries = [0]

            def rosters():
                tries[0] += 1                   # after a stream restart, continue the same run
                return run_roster(out, a.teams, resume=bool(a.resume) or tries[0] > 1)
            run_mode("the rosters", rosters)
        if a.only == "season-schedule":
            run_mode("the season schedule", lambda: run_season_schedule(out), clear("season_schedule"))
        if a.only == "games":
            run_mode("the games", lambda: run_games(out, a.week, a.all_rows, a.games))   # resumes per game
        log("Returning to the hub")
        go_hub()
        log(f"Done. Screenshots in {out}")
    except Abort as e:
        log(f"STOPPED: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        for k in KEYCODES:
            key_up(k)
        log("Interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
