"""
Weekly trigger (docs/PIPELINE_MIGRATION.md, phase 5, step 1): watches JR's Twitch broadcast of the
PS5 at 1 frame a second and starts the weekly run (pipeline/weekly_capture.py) once a week has been
played.

  python3 -m pipeline.main --watch [--stream https://twitch.tv/jrereyi]     # BROADCAST_URL in .env

- Only two screens are matched (pipeline/templates): ADVANCE_WEEK (the Advance Week screen's COACH
  PROGRESS button) and MAIN_MENU (the franchise hub's highlighted WEEKLY HUB tab), on frames scaled to
  1920x1080.
- Advance Week arms the trigger. Once the Weekly Hub has stayed on screen for FIRE_AFTER seconds
  (30, JR Oct 14) it fires: Remote Play takes the console, which ends the broadcast.
- Fallback: on every hub visit the hub's week label ("2035: WEEK 18") is read with Apple Vision. A
  week later than league_state.current_week that no run has been started for fires too (after the
  same 30 seconds), so a missed Advance Week frame doesn't lose the week.
- A label whose season isn't league_state.current_season never fires (a new season is set by hand);
  JR gets one notice. A week already attempted (captures/trigger_state.json) never fires twice; a
  failed week is resumed by hand (pipeline.weekly_capture --resume).
- When the broadcast is offline (between Remote Play and JR restarting it), the URL is retried every
  OFFLINE_RETRY seconds and watching resumes.
"""
import datetime, json, os, re, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "captures", "trigger_state.json")
FIRE_AFTER = 30.0          # seconds the Weekly Hub must stay on screen (JR, Oct 14)
MISS_FRAMES = 3            # this many frames in a row off the hub restart the hub timer
OFFLINE_RETRY = 60.0
FRAME_SIZE = (1920, 1080)  # the templates were cut from the 1080p broadcast
LABEL_BOX = (60, 120, 760, 215)   # hub week label + matchup line at 1920x1080 (x0, y0, x1, y1)

LABEL_RE = re.compile(r"\b(20\d\d)\s*[:;.]?\s*(PRE\s*SEASON\s*)?(WEEK\s*\d{1,2}|WILD\s*CARD|DIVISIONAL|CONFERENCE|"
                      r"PRO\s*BOWL|SUPER\s*BOWL)")


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] [trigger] {msg}", flush=True)


def parse_hub_label(text):
    """(season, games.week) from the hub's label text: '2035: WEEK 18' -> (2035, 18),
    '2035: WILD CARD' -> (2035, 19). Preseason, the Pro Bowl and unreadable text give None."""
    from pipeline.capture_matching import db_week
    m = LABEL_RE.search(re.sub(r"\s+", " ", (text or "").upper()))
    if not m or m.group(2) or "PRO" in m.group(3):
        return None
    part = re.sub(r"^WEEK\s*", "WEEK ", re.sub(r"\s+", " ", m.group(3)))
    week = db_week(part) if part.startswith("WEEK") else db_week("POST SEASON " + part)
    return (int(m.group(1)), week) if week else None


class Trigger:
    """Timing only: update() once per classified frame; True when the run should fire now.
    armed is set by ADVANCE_WEEK; fallback is set from outside when the hub label shows a week that
    is due. Either one fires once the hub has been on screen for fire_after seconds."""

    def __init__(self, fire_after=FIRE_AFTER, miss_frames=MISS_FRAMES):
        self.fire_after, self.miss_frames = fire_after, miss_frames
        self.armed = False
        self.fallback = False
        self.hub_since = None
        self.misses = 0
        self.label_read = False      # the hub label was read on this hub visit

    def update(self, state, t):
        if state == "ADVANCE_WEEK":
            if not self.armed:
                log("Advance Week seen: armed")
            self.armed = True
            self.leave_hub()
            return False
        if state == "MAIN_MENU":
            self.misses = 0
            if self.hub_since is None:
                self.hub_since = t
            return (self.armed or self.fallback) and t - self.hub_since >= self.fire_after
        self.misses += 1
        if self.misses >= self.miss_frames:
            self.leave_hub()
        return False

    def leave_hub(self):
        self.hub_since = None
        self.label_read = False
        self.fallback = False
        self.misses = 0

    def fired(self):
        self.armed = False
        self.leave_hub()


# ------------------------------------------------------------ helpers ----
def load_state():
    try:
        return json.load(open(STATE_PATH))
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=1)


def league_state():
    import psycopg
    from db.db_handler import DatabaseHandler
    with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
        return conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()


def read_hub_label(frame):
    """(season, week) from a 1920x1080 BGR hub frame, via Apple Vision on the label area."""
    import cv2
    from madden_capture import ocr
    x0, y0, x1, y1 = LABEL_BOX
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = fh.name
    try:
        crop = cv2.resize(frame[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(path, crop)
        return parse_hub_label(" ".join(t for t, _ in ocr(path)))
    finally:
        os.remove(path)


def resolve(url):
    """The broadcast's raw video URL, or None while it is offline."""
    import streamlink
    try:
        streams = streamlink.streams(url)
    except Exception as e:
        log(f"streamlink: {e}")
        return None
    s = streams.get("1080p60") or streams.get("1080p") or streams.get("best")
    return s.url if s else None


def classifier():
    from pipeline.template_classifier import TEMPLATE_DIR, TemplateClassifier
    c = TemplateClassifier(template_dir=TEMPLATE_DIR, threshold=0.9)
    c.templates = {k: v for k, v in c.templates.items() if k in ("ADVANCE_WEEK", "MAIN_MENU")}
    return c


# -------------------------------------------------------------- watch ----
def due(label, league, state):
    """Whether a hub label is a week to run now, and why not when it isn't."""
    if not label:
        return False, "label not read"
    season, week = label
    cur_season, cur_week = league
    if season != cur_season:
        return False, f"season {season} isn't league_state's {cur_season}"
    if week <= cur_week:
        return False, f"week {week} is already loaded (current week {cur_week})"
    last = state.get("attempted") or {}
    if (last.get("season"), last.get("week")) == (season, week):
        return False, f"a run for week {week} was already started ({last.get('folder') or last.get('at')})"
    return True, f"week {week} is past the loaded week {cur_week}"


def fire(label, reason):
    """Start the weekly run and wait for it. Records the attempt first, so a crash can't refire it."""
    from pipeline.notify import notify
    state = load_state()
    season, week = label if label else (None, None)
    state["attempted"] = {"season": season, "week": week, "reason": reason,
                          "at": datetime.datetime.now().isoformat(timespec="seconds")}
    save_state(state)
    log(f"FIRING the weekly run ({reason})")
    code = subprocess.run([sys.executable, "-m", "pipeline.weekly_capture"], cwd=ROOT).returncode
    log(f"Weekly run finished with exit code {code}")
    if code not in (0, 1):                     # 1 = stopped, and weekly_capture already sent its notice
        notify(f"The weekly run crashed (exit code {code}); see the terminal running pipeline.main --watch.",
               title="Mu: week stopped", priority="urgent", tags=("warning",))


def watch(url, fire_after=FIRE_AFTER):
    import cv2
    from pipeline.main import VideoCaptureThread
    from pipeline.notify import notify
    cls = classifier()
    trig = Trigger(fire_after)
    warned_season = None
    offline_logged = False
    log(f"Watching {url} (fires {fire_after:.0f}s after the Weekly Hub settles following Advance Week)")
    while True:
        raw = resolve(url)
        if not raw:
            if not offline_logged:
                log(f"Broadcast offline; retrying every {OFFLINE_RETRY:.0f}s")
                offline_logged = True
            time.sleep(OFFLINE_RETRY)
            continue
        offline_logged = False
        log("Broadcast online")
        try:
            video = VideoCaptureThread(raw)
        except ValueError as e:
            log(f"couldn't open the stream: {e}")
            time.sleep(OFFLINE_RETRY)
            continue
        video.start()
        label = None
        try:
            while video.running:
                time.sleep(1.0)
                frame = video.latest_frame
                if frame is None:
                    continue
                if (frame.shape[1], frame.shape[0]) != FRAME_SIZE:
                    frame = cv2.resize(frame, FRAME_SIZE, interpolation=cv2.INTER_AREA)
                state, _ = cls.detect_ui_state(frame)
                go = trig.update(state, time.monotonic())
                if state == "MAIN_MENU" and not trig.label_read:
                    trig.label_read = True
                    try:
                        label = read_hub_label(frame)
                        ok, why = due(label, league_state(), load_state())
                    except Exception as e:
                        label, ok, why = None, False, f"label check failed: {e}"
                    log(f"Weekly Hub: label {label}; {why}")
                    trig.fallback = ok
                    if label and not ok and why.startswith("season") and warned_season != label[0]:
                        warned_season = label[0]
                        notify(f"The hub shows season {label[0]}, but league_state is on another season. "
                               "Weekly runs won't start until current_season is set by hand.",
                               title="Mu: new season?", priority="high", tags=("warning",))
                if not go:
                    continue
                ok, why = due(label, league_state(), load_state()) if label else (trig.armed, "label not read")
                if label and not ok:
                    log(f"Not firing: {why}")
                    trig.fired()
                    continue
                reason = "Advance Week, then the Weekly Hub" if trig.armed else f"fallback: {why}"
                video.stop()
                trig.fired()
                fire(label, reason)
                break                              # the broadcast ended with Remote Play; resolve again
        finally:
            video.stop()
