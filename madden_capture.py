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
import argparse, datetime, hashlib, os, re, subprocess, sys, time

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
        out.append((str(cand[0].string()).upper(), (x0, y0, x0 + bb.size.width * W, y0 + bb.size.height * H)))
    return out


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
        """Exact (normalized) match. Returns box or None."""
        n = norm(label)
        for t, box in self.items:
            if norm(t) == n:
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
    if "PLAYER NAME" in t and "VIEW PLAYER CARD" in t:
        return "PLAYER_STATS"
    if "TEAM NAME" in t and "SORT" in t and f.find("R2"):
        return "TEAM_STATS"
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


def open_league_schedule():
    f = to_league_tab()
    f = move_to(f, LEAGUE_MENU, "SCHEDULE", "up", "down", "League menu item")
    f, _ = cross_into("HUB_LEAGUE", ["SCHED_TILES"])
    move_to(f, SCHED_TILES, "LEAGUE SCHEDULE", "left", "right", "schedule tile")
    return cross_into("SCHED_TILES", ["SCHED_LIST"], timeout=10)[0]


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


def dropdown_entries(f):
    """Text items inside the open dropdown column (left side, below the chips)."""
    W, H = f.size
    return [(t, box) for t, box in f.items
            if box[2] < W * 0.21 and H * 0.35 < box[1] < H * 0.92 and re.match(r"^[A-Z0-9 ]+$", t)]


def dropdown_selected(f):
    """The highlighted entry. The highlight is always the TOP row of the open list,
    drawn as a light box, so take the topmost entry and confirm its background is light."""
    entries = dropdown_entries(f)
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
        last = None
        for _ in range(70):
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
            if norm(sel) in DIVISION_ENTRIES and tnorm in DIVISION_ENTRIES:
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


def all_zero_rows(f, top=0.45):
    """True when every numeric cell in the lower part of the table is zero."""
    H = f.size[1]
    nums = [t for t, b in f.items if b[1] > H * top and NUM_RE.match(t.replace(" ", ""))]
    return len(nums) >= 20 and all(ZERO_RE.match(n.replace(",", "")) for n in nums)


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


def capture_list(folder, prefix, rows_per_page=10, max_pages=250, stop_at_zeros=True, direction="down"):
    os.makedirs(folder, exist_ok=True)
    wait_table_loaded()
    prev = None
    for page in range(max_pages):
        path = os.path.join(folder, f"{prefix}_{page:03d}.png")
        snap(path)
        sig = table_sig(path)
        if prev is not None and same_table(prev, sig):
            os.remove(path)
            log(f"  {prefix}: end of list after {page} pages")
            return page
        prev = sig
        if stop_at_zeros:
            f = Frame.__new__(Frame)
            f.path, f.items, f.size = path, ocr(path), Image.open(path).size
            if all_zero_rows(f):
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


def capture_wide_list(folder, base, rows_per_page=10, max_pages=250):
    """Wide tables: NAME/POS are NOT pinned when the table scrolls right, so every page is
    captured at both edges at the same row position. Row N of {base}_NNN.png is row N of
    {base}_right_NNN.png. Zig-zags (left, right, down, right, left, down...) so each page
    needs one trip across the columns."""
    os.makedirs(folder, exist_ok=True)
    wait_table_loaded()
    traverse("left")                     # the column highlight can be left anywhere; start at the left edge
    side = "left"
    prev = {"left": None, "right": None}

    def path(s, page):
        return os.path.join(folder, f"{base}_{page:03d}.png" if s == "left" else f"{base}_right_{page:03d}.png")

    for page in range(max_pages):
        first = path(side, page)
        f = traverse(side)                      # confirm we're still at this side's edge
        subprocess.run(["cp", f.path, first])
        sig = table_sig(first)
        if prev[side] is not None and same_table(prev[side], sig):
            os.remove(first)
            log(f"  {base}: end of list after {page} pages")
            if side == "right":
                traverse("left")                # leave the table at its left edge
            return page
        prev[side] = sig
        other = "right" if side == "left" else "left"
        f = traverse(other)
        subprocess.run(["cp", f.path, path(other, page)])
        prev[other] = table_sig(path(other, page))
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
        _, cat = chip_labels(f)
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


def run_schedule(out, week=None):
    log("== Schedule ==")
    f = open_league_schedule()
    cur = week_label(f)
    if not cur:
        raise Abort("Can't read the schedule week label")
    target = clean_week(week) if week else previous_week(cur)
    log(f"Current week: {cur}. Target: {target}")
    if week_index(cur) != week_index(target):
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
    log(f"On {week_label(f)}")
    if "FORCE WIN" in f.text and "ACTIONS" in f.text:
        log("Note: this week shows an ACTIONS button; Cross will NOT be pressed here.")
    n = capture_list(os.path.join(out, "schedule"), norm(target).lower(), stop_at_zeros=False)
    log(f"Schedule {target}: {n} pages")


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
    ap.add_argument("--only", choices=["stats", "team", "schedule"])
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
    if FOREGROUND:
        activate_app()
    global DEBUG_DIR
    out = os.path.join(OUT_ROOT, datetime.datetime.now().strftime("%Y-%m-%d_%H%M"))
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
        if a.only in (None, "stats"):
            run_stats(out, a.all_rows)
        if a.only in (None, "team"):
            run_team_stats(out)
        if a.only in (None, "schedule"):
            run_schedule(out, a.week)
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
