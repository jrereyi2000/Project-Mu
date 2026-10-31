"""Madden pilot catalog and navigation (plan section 2.2) on synthetic OCR frames and a fake driver."""
import copy
import json

import pytest

import pipeline.notify
from tools.madden_pilot import navigate
from tools.madden_pilot.navigate import Forbidden, Pilot, Stuck, Unknown
from tools.madden_pilot.screens import UNKNOWN, Frame, border_highlight, classify, label_match, load_catalog

CAT = load_catalog()

TABS = ["GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "COACH CENTRAL", "LEAGUE", "OPTIONS"]
SCREEN_LINES = {
    "FRANCHISE_LIST": ["CURRENT FRANCHISES", "ALLUSER-AUTOSAVE", "TUNE-SPIKE", "ALLUSER", "CREATE NEW LEAGUE",
                       "0 LEAGUES", "ACCEPT", "DELETE", "BACK"],
    "PROGRESSION_WARNING": ["CURRENT FRANCHISES", "PROGRESSION WARNING",
                            "Unable to connect to EA servers. You will not be able to earn progression towards",
                            "PROCEED WITHOUT PROGRESSION", "CANCEL", "SELECT"],
    "HUB_WEEKLY": TABS + ["2026. WEEK 1", "VS BEARS (0-0-0)", "BEARS WEEK", "Delegate", "CREATE SAVEPOINT", "BACK"],
    "HUB_GAMEDAY": TABS + ["MATCHUP", "WEEKLY STRATEGY", "ADVANCE WEEK", "PLAY SEASON GAME", "SELECT", "BACK"],
    "ADVANCE_MENU": TABS + ["STATUS REPORT", "NEXT WEEK", "JUMP AHEAD", "SIM TO MIDSEASON", "ADVANCE WEEK",
                            "PLAY SEASON GAME", "SELECT", "BACK"],
    "QUIT_CONFIRM": TABS + ["2026. WEEK 1", "WARNING", "Are you sure you want to quit?", "YES", "NO", "SELECT"],
    "COACH_PROGRESS": ["COACH PROGRESS", "LOADOUT PROGRESS", "UNLOCKS", "D. CANALES", "GOAL PROGRESSION",
                       "CONTINUE", "CLOSE"],
    "WEEKLY_RECAP": ["WEEKLY RECAP", "WEEKLY RECAP", "EARN YOUR SUNDAY"],
    "ROSTER_DECISION_PROMPT": ["PLACE ON INJURED RESERVE", "KEEP ON ACTIVE ROSTER", "ROSTER DECISION LOOMS",
                               "SELECT", "BACK"],
    "CHOOSE_TEAM": ["OFFLINE", "NFL PRESEASON ROSTER", "CHOOSE TEAM", "CUSTOMIZE", "JAGUARS", "RANDOM TEAM", "BACK"],
    "DELETE_CONFIRM": ["DELETE FRANCHISE", "Are you sure? This cannot be undone.", "YES", "NO"],
    "LOADING": [],
}
OPTIONS = {
    "FRANCHISE_LIST": (["ALLUSER-AUTOSAVE", "TUNE-SPIKE", "ALLUSER"], 0),
    "PROGRESSION_WARNING": (["PROCEED WITHOUT PROGRESSION", "CANCEL"], 0),
    "QUIT_CONFIRM": (["YES", "NO"], 1),
    "HUB_GAMEDAY": (["ADVANCE WEEK", "PLAY SEASON GAME"], 0),
    "ADVANCE_MENU": (["NEXT WEEK", "SIM TO MIDSEASON"], 0),
}


def frame_of(lines, png=None):
    words = []
    for i, ln in enumerate(lines):
        for j, w in enumerate(ln.split()):
            words.append({"text": w, "box": [100 + 120 * j, 40 * i, 100, 30], "line": i})
    return Frame(png, words)


class FakeMadden:
    """Moves between screens on key presses. `moves[(screen, key)]` is the next screen, or a
    function of the highlighted option; nav keys move the highlight along OPTIONS."""

    def __init__(self, screen="HUB_WEEKLY", moves=None, interrupts=None):
        self.screen, self.idx = None, 0
        self.go(screen)
        self.keys = []
        self.moves = dict(DEFAULT_MOVES if moves is None else moves)
        self.interrupts = interrupts or {}          # (screen, key) -> [screens to show first]
        self.queue = []

    def go(self, screen):
        self.screen = screen
        self.idx = OPTIONS.get(screen, ([], 0))[1]

    @property
    def options(self):
        return OPTIONS.get(self.screen, ([], 0))[0]

    @property
    def highlight(self):
        return self.options[self.idx] if self.options else None

    def call(self, cmd):
        if cmd[0] == "look":
            out = cmd[cmd.index("--out") + 1]
            with open(out, "wb") as f:
                f.write(b"png")
            lines = SCREEN_LINES[self.screen]
            words = [w for w in frame_of(lines).words]
            return {"ok": True, "png": out, "size": [2560, 1440], "words": words}
        if cmd[0] == "key":
            key = cmd[cmd.index("--name") + 1]
            self.keys.append(key)
            for _ in range(int(cmd[cmd.index("--times") + 1]) if "--times" in cmd else 1):
                self.on_key(key)
            return {"ok": True}
        return {"ok": False, "error": "unknown command"}

    def on_key(self, key):
        if self.queue:
            self.go(self.queue.pop(0))
            return
        nav = {"down": 1, "right": 1, "up": -1, "left": -1}
        if key in nav and self.options:
            self.idx = max(0, min(len(self.options) - 1, self.idx + nav[key]))
            return
        k = (self.screen, key)
        if k in self.interrupts and self.interrupts[k]:
            first, *rest = self.interrupts.pop(k)
            self.go(first)
            self.queue = rest
            return
        nxt = self.moves.get(k)
        if callable(nxt):
            nxt = nxt(self.highlight)
        if nxt:
            self.go(nxt)


DEFAULT_MOVES = {
    ("FRANCHISE_LIST", "space"): lambda h: "PROGRESSION_WARNING",
    ("PROGRESSION_WARNING", "space"): lambda h: "HUB_WEEKLY" if h == "PROCEED WITHOUT PROGRESSION" else "FRANCHISE_LIST",
    ("HUB_WEEKLY", "z"): "HUB_GAMEDAY",
    ("HUB_WEEKLY", "backspace"): "QUIT_CONFIRM",
    ("HUB_GAMEDAY", "c"): "HUB_WEEKLY",
    ("HUB_GAMEDAY", "backspace"): "QUIT_CONFIRM",
    ("HUB_GAMEDAY", "space"): lambda h: "ADVANCE_MENU" if h == "ADVANCE WEEK" else "WEEKLY_RECAP",
    ("ADVANCE_MENU", "backspace"): "HUB_GAMEDAY",
    ("QUIT_CONFIRM", "space"): lambda h: "FRANCHISE_LIST" if h == "YES" else "HUB_WEEKLY",
    ("ROSTER_DECISION_PROMPT", "backspace"): "HUB_WEEKLY",
    ("COACH_PROGRESS", "space"): "HUB_WEEKLY",
}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify", lambda msg, **kw: sent.append((msg, kw)) or True)
    return sent


def make_pilot(tmp_path, fake, catalog=CAT, **kw):
    clock = FakeClock()
    return Pilot(catalog, tmp_path, driver=fake, sleep=clock.sleep, clock=clock,
                 highlight_reader=lambda frame, labels, spec: fake.highlight, **kw)


def read_stop(tmp_path):
    return json.loads((tmp_path / "stop.json").read_text())


# ---------------------------------------------------------------- catalog / classify
def test_catalog_holds_spike_screens_and_pc_keys():
    assert CAT["keys"] == {"select": "space", "back": "backspace", "tab_prev": "z", "tab_next": "c",
                           "savepoint": "tab"}
    for name in ["CHOOSE_TEAM", "HUB_WEEKLY", "HUB_GAMEDAY", "ADVANCE_MENU", "QUIT_CONFIRM", "FRANCHISE_LIST",
                 "PROGRESSION_WARNING", "COACH_PROGRESS", "ROSTER_DECISION_PROMPT", "WEEKLY_RECAP", "MAIN_MENU"]:
        assert name in CAT["screens"], name
    s = CAT["screens"]
    assert s["MAIN_MENU"]["todo"] is True
    assert s["WEEKLY_RECAP"]["wait_or_back"] is True
    assert "q" in s["FRANCHISE_LIST"]["forbidden_keys"]
    assert s["ROSTER_DECISION_PROMPT"]["forbidden_confirm"] and s["ROSTER_DECISION_PROMPT"]["never_select"]
    assert s["HUB_GAMEDAY"]["confirm_ok"] == ["ADVANCE WEEK"]
    for name, spec in s.items():
        assert spec.get("highlight") in (None, "border", "ocr_order"), name
        for target, steps in spec.get("exits", {}).items():
            assert target in s, (name, target)
            assert not s[target].get("no_route"), (name, target)
            for step in steps:
                assert step[0] in ("highlight", "confirm") or isinstance(step[1], int)
                if step[0] == "confirm":
                    assert any(label_match(step[1], ok) for ok in spec["confirm_ok"]), (name, step)


@pytest.mark.parametrize("name", [n for n in SCREEN_LINES if n != "LOADING"])
def test_classify_spike_screens(name):
    assert classify(frame_of(SCREEN_LINES[name]), CAT) == name


def test_classify_unknown_and_word_boundaries():
    assert classify(frame_of([]), CAT) == UNKNOWN
    assert classify(frame_of(["EA SPORTS", "LOADING"]), CAT) == UNKNOWN
    # "FRANCHISE" (main menu guess) never matches inside "CURRENT FRANCHISES"
    assert not frame_of(["CURRENT FRANCHISES"]).has("FRANCHISE")
    # phrases don't span OCR lines
    assert not frame_of(["NEXT", "WEEK"]).has("NEXT WEEK")


def test_classify_order_all_any_none():
    # GAMEDAY after a played week shows a WEEKLY RECAP tile: still the hub (none words)
    gameday = TABS + ["MATCHUP", "WEEKLY RECAP", "PLAY SEASON GAME", "ADVANCE WEEK"]
    assert classify(frame_of(gameday), CAT) == "HUB_GAMEDAY"
    # the progression dialog is drawn over CURRENT FRANCHISES: the more specific screen wins
    assert classify(frame_of(SCREEN_LINES["PROGRESSION_WARNING"]), CAT) == "PROGRESSION_WARNING"
    # the advance menu shows the hub tabs and ADVANCE WEEK too; it is listed first
    assert classify(frame_of(SCREEN_LINES["ADVANCE_MENU"]), CAT) == "ADVANCE_MENU"
    cat = {"screens": {
        "A": {"all": ["ONE", "TWO"], "any": ["X", "Y"], "none": ["NOPE"]},
        "B": {"all": ["ONE"]},
    }}
    assert classify(frame_of(["ONE", "TWO", "Y"]), cat) == "A"
    assert classify(frame_of(["ONE", "TWO"]), cat) == "B"            # no any word
    assert classify(frame_of(["ONE", "TWO", "X", "NOPE"]), cat) == "B"  # a none word
    assert classify(frame_of(["TWO", "X"]), cat) == UNKNOWN          # an all word missing
    cat["screens"] = dict(reversed(list(cat["screens"].items())))
    assert classify(frame_of(["ONE", "TWO", "Y"]), cat) == "B"       # order decides


def test_frame_text_find_near():
    f = frame_of(["Are you sure you want to quit?", "YES", "NO"])
    assert "ARE YOU SURE YOU WANT TO QUIT?" in f.text
    assert f.find("want to quit") == [580, 0, 340, 30]
    assert f.find("MAYBE") is None
    assert f.near("YES", "NO", max_px=50)
    assert not f.near("YES", "QUIT", max_px=50)
    assert Frame(None, [{"text": "x", "box": [0, 0, 1, 1], "line": 0}], lines=["CURRENT FRANCHISES"]).has(
        "CURRENT FRANCHISES")


def test_border_highlight_reads_white_outline(tmp_path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 400), (20, 20, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([90, 90, 360, 160], outline=(255, 255, 255), width=3)  # border around ADVANCE WEEK
    png = tmp_path / "f.png"
    img.save(png)
    words = [{"text": "ADVANCE", "box": [100, 110, 120, 30], "line": 0},
             {"text": "WEEK", "box": [230, 110, 100, 30], "line": 0},
             {"text": "PLAY SEASON GAME", "box": [420, 110, 300, 30], "line": 1}]
    f = Frame(str(png), words)
    assert border_highlight(f, ["ADVANCE WEEK", "PLAY SEASON GAME"], reach=0.1) == "ADVANCE WEEK"
    assert border_highlight(Frame(None, words), ["ADVANCE WEEK"]) is None


# ---------------------------------------------------------------- look / press
def test_look_saves_steps(tmp_path, notices):
    p = make_pilot(tmp_path, FakeMadden("HUB_GAMEDAY"))
    frame, screen = p.look()
    assert screen == "HUB_GAMEDAY"
    assert (tmp_path / "steps" / "0001_HUB_GAMEDAY.png").exists()
    data = json.loads((tmp_path / "steps" / "0001_HUB_GAMEDAY.json").read_text())
    assert data["screen"] == "HUB_GAMEDAY" and "ADVANCE WEEK" in data["lines"]


def test_forbidden_keys_and_bare_space(tmp_path, notices):
    fake = FakeMadden("FRANCHISE_LIST")
    p = make_pilot(tmp_path, fake)
    p.look()
    with pytest.raises(Forbidden):
        p.press("q")
    assert "q" not in fake.keys
    assert read_stop(tmp_path)["kind"] == "Forbidden"
    with pytest.raises(Forbidden):
        p.press("select")
    with pytest.raises(Forbidden):
        p.press("space")
    assert fake.keys == []
    p.press("right")
    assert fake.keys == ["right"]


# ---------------------------------------------------------------- confirm_on
def test_confirm_on_refuses_unlisted_labels(tmp_path, notices):
    fake = FakeMadden("HUB_GAMEDAY")
    p = make_pilot(tmp_path, fake)
    with pytest.raises(Forbidden):
        p.confirm_on("HUB_GAMEDAY", "PLAY SEASON GAME")
    assert "space" not in fake.keys
    p.confirm_on("HUB_GAMEDAY", "ADVANCE WEEK")
    assert fake.keys == ["space"] and fake.screen == "ADVANCE_MENU"


def test_confirm_on_needs_the_highlight_on_the_label(tmp_path, notices):
    fake = FakeMadden("HUB_GAMEDAY")
    fake.idx = 1                                     # focus on PLAY SEASON GAME
    p = make_pilot(tmp_path, fake)
    with pytest.raises(Stuck):
        p.confirm_on("HUB_GAMEDAY", "ADVANCE WEEK")
    assert fake.keys == []


def test_roster_prompt_never_selected_even_with_policy(tmp_path, notices):
    fake = FakeMadden("ROSTER_DECISION_PROMPT")
    p = make_pilot(tmp_path, fake, policy={"ROSTER_DECISION_PROMPT": ["KEEP ON ACTIVE ROSTER"]})
    with pytest.raises(Forbidden):
        p.confirm_on("ROSTER_DECISION_PROMPT", "KEEP ON ACTIVE ROSTER")
    assert fake.keys == []
    assert p.plan("HUB_WEEKLY", "ROSTER_DECISION_PROMPT") is None


def test_policy_lifts_forbidden_confirm(tmp_path, notices):
    cat = copy.deepcopy(CAT)
    cat["screens"]["ROSTER_DECISION_PROMPT"].pop("never_select")
    cat["screens"]["ROSTER_DECISION_PROMPT"]["options"] = ["PLACE ON INJURED RESERVE", "KEEP ON ACTIVE ROSTER"]
    fake = FakeMadden("ROSTER_DECISION_PROMPT")
    p = make_pilot(tmp_path, fake, catalog=cat)
    with pytest.raises(Forbidden):
        p.confirm_on("ROSTER_DECISION_PROMPT", "KEEP ON ACTIVE ROSTER")
    p2 = make_pilot(tmp_path, fake, catalog=cat, policy={"ROSTER_DECISION_PROMPT": ["KEEP ON ACTIVE ROSTER"]})
    p2.highlight_reader = lambda frame, labels, spec: "KEEP ON ACTIVE ROSTER"
    p2.confirm_on("ROSTER_DECISION_PROMPT", "KEEP ON ACTIVE ROSTER")
    assert fake.keys == ["space"]


def test_load_franchise_tracks_loaded_and_refuses_non_tune(tmp_path, notices):
    fake = FakeMadden("FRANCHISE_LIST")
    p = make_pilot(tmp_path, fake)
    p.look()
    with pytest.raises(Forbidden):
        p.confirm_on("FRANCHISE_LIST", "ALLUSER")
    p.highlight("TUNE-SPIKE")
    assert fake.highlight == "TUNE-SPIKE"
    p.confirm_on("FRANCHISE_LIST", "TUNE-SPIKE")
    assert p.loaded_franchise == "TUNE-SPIKE"
    p.goto("HUB_WEEKLY")
    assert fake.screen == "HUB_WEEKLY"


# ---------------------------------------------------------------- goto
def test_goto_plans_shortest_route(tmp_path, notices):
    p = make_pilot(tmp_path, FakeMadden())
    route = p.plan("HUB_WEEKLY", "ADVANCE_MENU")
    assert [(a, b) for a, b, _ in route] == [("HUB_WEEKLY", "HUB_GAMEDAY"), ("HUB_GAMEDAY", "ADVANCE_MENU")]
    assert p.plan("FRANCHISE_LIST", "HUB_WEEKLY") is None      # loading needs a franchise choice
    assert p.plan("HUB_WEEKLY", "HUB_WEEKLY") == []


def test_goto_walks_and_confirms_only_on_advance_week(tmp_path, notices):
    fake = FakeMadden("HUB_WEEKLY")
    p = make_pilot(tmp_path, fake)
    p.goto("ADVANCE_MENU")
    assert fake.screen == "ADVANCE_MENU"
    assert fake.keys == ["z", "space"]


def test_goto_moves_focus_to_advance_week_first(tmp_path, notices):
    fake = FakeMadden("HUB_GAMEDAY")
    fake.idx = 1
    p = make_pilot(tmp_path, fake)
    p.goto("ADVANCE_MENU")
    assert fake.keys == ["left", "space"]


def test_goto_replans_after_interrupt(tmp_path, notices):
    # Tab-next from GAMEDAY lands on the three COACH PROGRESS tabs first (after a sim)
    fake = FakeMadden("ADVANCE_MENU",
                      interrupts={("HUB_GAMEDAY", "c"): ["COACH_PROGRESS", "COACH_PROGRESS", "COACH_PROGRESS"]})
    p = make_pilot(tmp_path, fake)
    p.goto("HUB_WEEKLY")
    assert fake.screen == "HUB_WEEKLY"
    assert fake.keys == ["backspace", "c", "space", "space", "space"]


def test_goto_backs_out_of_weekly_recap_video(tmp_path, notices):
    fake = FakeMadden("WEEKLY_RECAP", interrupts={("WEEKLY_RECAP", "backspace"): ["LOADING", "LOADING"]})
    fake.moves[("LOADING", "backspace")] = "HUB_GAMEDAY"
    p = make_pilot(tmp_path, fake)
    p.goto("HUB_GAMEDAY")
    assert fake.screen == "HUB_GAMEDAY"
    assert set(fake.keys) == {"backspace"}


# ---------------------------------------------------------------- quit guard
def test_quit_refused_for_non_tune_franchise(tmp_path, notices):
    fake = FakeMadden("HUB_WEEKLY")
    p = make_pilot(tmp_path, fake)
    p.loaded_franchise = "ALLUSER"
    with pytest.raises(Forbidden):
        p.goto("FRANCHISE_LIST")
    assert fake.keys == []
    stop = read_stop(tmp_path)
    assert stop["kind"] == "Forbidden" and "ALLUSER" in stop["reason"]
    # landing on the dialog by hand doesn't help either
    fake.go("QUIT_CONFIRM")
    fake.idx = 0
    with pytest.raises(Forbidden):
        p.confirm_on("QUIT_CONFIRM", "YES")
    assert "space" not in fake.keys
    # NO is always fine
    fake.idx = 1
    p.confirm_on("QUIT_CONFIRM", "NO")
    assert fake.screen == "HUB_WEEKLY"


def test_quit_allowed_for_tune_or_explicit(tmp_path, notices):
    fake = FakeMadden("HUB_WEEKLY")
    p = make_pilot(tmp_path, fake)
    p.loaded_franchise = "TUNE-SPIKE"
    p.goto("FRANCHISE_LIST")
    assert fake.screen == "FRANCHISE_LIST"
    assert fake.keys == ["backspace", "up", "space"]

    fake2 = FakeMadden("HUB_WEEKLY")
    p2 = make_pilot(tmp_path / "b", fake2)
    p2.goto("FRANCHISE_LIST", allow_quit=True)
    assert fake2.screen == "FRANCHISE_LIST"
    assert p2.allow_quit is False                      # only for that goto


# ---------------------------------------------------------------- stops
def test_stuck_stop_writes_stop_json(tmp_path, notices):
    fake = FakeMadden("HUB_WEEKLY", moves={})          # Madden ignores every key
    p = make_pilot(tmp_path, fake)
    with pytest.raises(Stuck) as e:
        p.goto("HUB_GAMEDAY")
    stop = read_stop(tmp_path)
    assert stop["kind"] == "Stuck" and stop["screen"] == "HUB_WEEKLY"
    assert stop["screenshot"].endswith("_HUB_WEEKLY.png")
    assert e.value.info == stop
    assert len(notices) == 1 and "Stuck" in notices[0][0] and stop["screenshot"] in notices[0][0]


def test_wait_for_times_out_stuck(tmp_path, notices):
    p = make_pilot(tmp_path, FakeMadden("HUB_WEEKLY"))
    with pytest.raises(Stuck):
        p.wait_for(["ADVANCE_MENU"], timeout=5)
    assert read_stop(tmp_path)["kind"] == "Stuck"


def test_unknown_stop(tmp_path, notices):
    p = make_pilot(tmp_path, FakeMadden("LOADING"), unknown_limit=3)
    with pytest.raises(Unknown):
        p.goto("HUB_WEEKLY")
    stop = read_stop(tmp_path)
    assert stop["kind"] == "Unknown" and stop["screen"] == UNKNOWN
    assert len(notices) == 1


def test_forbidden_screen_stop(tmp_path, notices):
    fake = FakeMadden("DELETE_CONFIRM")
    p = make_pilot(tmp_path, fake)
    with pytest.raises(Forbidden):
        p.goto("HUB_WEEKLY")
    assert fake.keys == []
    stop = read_stop(tmp_path)
    assert stop["kind"] == "Forbidden" and stop["screen"] == "DELETE_CONFIRM"
    assert len(notices) == 1


def test_notice_failure_never_hides_the_stop(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(pipeline.notify, "notify", boom)
    p = make_pilot(tmp_path, FakeMadden("DELETE_CONFIRM"))
    with pytest.raises(Forbidden):
        p.look()
    assert (tmp_path / "stop.json").exists()


def test_driver_failure_is_a_stuck_stop(tmp_path, notices):
    class Broken:
        def call(self, cmd):
            return {"ok": False, "error": "Madden isn't foreground"}
    p = make_pilot(tmp_path, Broken())
    with pytest.raises(Stuck):
        p.look()
    assert "foreground" in read_stop(tmp_path)["reason"]


def test_ocr_order_tracks_presses(tmp_path, notices):
    cat = {"keys": CAT["keys"], "screens": {
        "MENU": {"all": ["MENU"], "options": ["A", "B", "C"], "default": "A", "nav": "vertical",
                 "highlight": "ocr_order", "confirm_ok": ["C"], "exits": {}}}}

    class Menu:
        keys = []

        def call(self, cmd):
            if cmd[0] == "key":
                self.keys.append(cmd[cmd.index("--name") + 1])
            return {"ok": True, "words": frame_of(["MENU", "A", "B", "C"]).words}
    drv = Menu()
    clock = FakeClock()
    p = Pilot(cat, tmp_path, driver=drv, sleep=clock.sleep, clock=clock)
    p.look()
    assert p.highlighted() == "A"
    with pytest.raises(Stuck):
        p.confirm_on("MENU", "C")                      # highlight on A
    p.highlight("C")
    assert drv.keys == ["down", "down"]
    p.confirm_on("MENU", "C")
    assert drv.keys[-1] == "space"


def test_default_driver_is_lazy(monkeypatch, tmp_path):
    import sys
    import types
    mod = types.ModuleType("tools.madden_pilot.win")
    mod.call = lambda cmd, timeout=60: {"ok": True}
    monkeypatch.setitem(sys.modules, "tools.madden_pilot.win", mod)
    import tools.madden_pilot as pkg
    monkeypatch.setattr(pkg, "win", mod, raising=False)
    assert Pilot(CAT, tmp_path).driver is mod
    assert navigate.TITLE == "Madden NFL 27"
