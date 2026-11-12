"""Synthetic tests for the guarded spike agent (MADDEN_SPIKE_AGENT_PLAN section 4).

Everything here is offline: fake pilots and clocks, synthetic PNG frames, and a scripted Madden
for the CLI. Nothing launches a game, presses a real key, reads a database or calls a model.
"""
import json
from pathlib import Path

import pytest

from tools.madden_pilot import __main__ as cli
from tools.madden_pilot import spike, vision
from tools.madden_pilot.navigate import TITLE, Stuck
from tools.madden_pilot.screens import Frame, load_catalog, norm

CATALOG = load_catalog()
CONTRACT_KEYS = {"ok", "job", "step", "steps_left", "minutes_left", "screen", "lines",
                 "highlight", "png", "refused", "stopped"}


# --------------------------------------------------------------------------- helpers
def brief_file(tmp_path, **over):
    data = {
        "id": "20261111-1200-test",
        "from": "tester",
        "goal": "answer the questions",
        "questions": ["What is the team?"],
        "pack": "Mu Base",
        "save": None,
        "start": "FRANCHISE_LIST",
        "allow_confirm": [],
        "allow_keys": [],
        "max_steps": 20,
        "max_minutes": 20,
        "record": True,
        "launch": "pilot",
        "checks": [],
        "checkpoints": [],
    }
    data.update(over)
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def brief_of(tmp_path, **over):
    return spike.load_brief(brief_file(tmp_path, **over))


def desc(kind="other", warning=None, selected=None, title="screen"):
    return vision.ScreenDescription(kind=kind, title=title, options=(), selected=selected,
                                    overlays=(), warning=warning, summary="test", model="fake",
                                    seconds=0.0)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakePilot:
    """The parts of navigate.Pilot the guard uses, without a driver or a screenshot."""

    def __init__(self, screen="FRANCHISE_LIST", frame=None, loaded_franchise=None,
                 highlight_reader=None):
        self.screens = CATALOG["screens"]
        self.title = TITLE
        self.screen = screen
        self.last_known = screen
        self.frame = frame if frame is not None else Frame(None, [], ["CURRENT FRANCHISES"],
                                                           [2560, 1440])
        self.last_png = self.frame.png
        self.loaded_franchise = loaded_franchise
        self.highlight_reader = highlight_reader
        self.unknown_limit = 20
        self.policy = {}
        self.keys = []
        self.confirms = []
        self.calls = []
        self.looks = 0

    def spec(self, screen=None):
        return self.screens.get(screen or self.screen) or {}

    def look(self):
        self.looks += 1
        return self.frame, self.screen

    def press(self, key, hold=0.08, wait=0.6, times=1, _confirm=False):
        self.keys.append((key, times, _confirm))

    def confirm_on(self, screen, label, wait=1.0):
        self.confirms.append((screen, label))
        if self.spec(screen).get("loads_franchise"):
            self.loaded_franchise = norm(label)
        return True

    def plan(self, start, target):
        if start == target:
            return []
        return [(start, target, [["down", 1]])]

    def goto(self, target, **kw):
        self.calls.append(("goto", target))
        self.screen = target

    def _call(self, cmd):
        self.calls.append(tuple(cmd))
        return {"ok": True}


def make_guard(tmp_path, brief, pilot=None, clock=None, describe_fn=None, root=None):
    state = (root or tmp_path / "root") / "state.json"

    def down(png):
        raise vision.VisionUnavailable("test: no backend")

    return spike.SpikeGuard(brief, pilot or FakePilot(), state, clock=clock or (lambda: 1000.0),
                            describe_fn=describe_fn or down)


def frame_with_lines(tmp_path, lines, screen="FRANCHISE_LIST", size=(2560, 1440)):
    """A frame whose OCR lines carry boxes stacked from y=100 (40 px apart)."""
    words = []
    for row, line in enumerate(lines):
        x = 100
        for token in line.split():
            words.append({"text": token, "box": [x, 100 + 40 * row, max(30, 14 * len(token)), 30],
                          "line": row})
            x += max(40, 15 * len(token))
    return Frame(None, words, list(lines), list(size))


def border_frame(tmp_path, bordered="TEAM SETTINGS"):
    """A frame with Madden's white selection border under one label."""
    from PIL import Image, ImageDraw

    png = tmp_path / f"border-{bordered.replace(' ', '-')}.png"
    img = Image.new("RGB", (400, 400), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    if bordered == "TEAM SETTINGS":
        draw.rectangle([100, 90, 250, 94], fill=(255, 255, 255))
    else:
        draw.rectangle([100, 290, 250, 294], fill=(255, 255, 255))
    img.save(png)
    words = [
        {"text": "TEAM", "box": [100, 100, 60, 30], "line": 0},
        {"text": "SETTINGS", "box": [170, 100, 80, 30], "line": 0},
        {"text": "OTHER", "box": [100, 300, 80, 30], "line": 1},
    ]
    return Frame(str(png), words, ["TEAM SETTINGS", "OTHER"], [400, 400])


# --------------------------------------------------------------------------- 2.2 brief
@pytest.mark.parametrize("over,field", [
    ({"id": "not-an-id"}, "id"),
    ({"save": "CAREER-MU2026"}, "save"),
    ({"start": "NOT_A_SCREEN"}, "start"),
    ({"start": "ROSTER_DECISION_PROMPT"}, "start"),          # no_route
    ({"allow_confirm": [{"label": "DELETE FRANCHISE"}]}, "allow_confirm"),
    ({"allow_keys": [{"label": "not-a-key"}]}, "allow_keys"),
    ({"allow_keys": [{"label": "q"}]}, "allow_keys"),        # q needs on_words
    ({"allow_keys": [{"label": "esc", "on_kind": "nonsense"}]}, "allow_keys"),
    ({"max_steps": 0}, "max_steps"),
    ({"max_steps": 201}, "max_steps"),
    ({"max_minutes": 0}, "max_minutes"),
    ({"max_minutes": 46}, "max_minutes"),
    ({"checks": [{"name": "probe", "reference": {"pass": "no/such/file.png"}}]}, "checks"),
    ({"checkpoints": [{"name": "half", "checks": ["missing"]}]}, "checkpoints"),
])
def test_load_brief_refusals(tmp_path, over, field):
    with pytest.raises(spike.BriefError) as caught:
        spike.load_brief(brief_file(tmp_path, **over))
    assert caught.value.field == field
    assert str(caught.value).startswith(field + ":")


def test_load_brief_accepts_the_plan_shape(tmp_path):
    brief = spike.load_brief(brief_file(
        tmp_path, save="CAREER-TUNE-SPIKE", start="HUB_WEEKLY",
        allow_confirm=[{"label": "PROCEED WITHOUT PROGRESSION", "on_words": ["PROGRESSION WARNING"]}],
        allow_keys=[{"label": "q", "on_words": ["WEEKLY HUB"]},
                    {"label": "esc", "on_kind": "gameplay"}],
        checks=[{"name": "probe", "measures": []}],
        checkpoints=[{"name": "half", "checks": ["probe"]}],
    ))
    assert brief.save == "CAREER-TUNE-SPIKE"
    assert brief.allow_confirm[0].label == "PROCEED WITHOUT PROGRESSION"
    assert brief.allow_keys[0].on_words == ("WEEKLY HUB",)
    assert brief.allow_keys[1].on_kind == "gameplay"
    assert brief.max_steps == 20 and brief.max_minutes == 20


# --------------------------------------------------------------------------- hard stops
def test_hard_word_stops_and_presses_back(tmp_path):
    pilot = FakePilot(frame=frame_with_lines(tmp_path, ["DELETE FRANCHISE", "SOMETHING ELSE"]))
    guard = make_guard(tmp_path, brief_of(tmp_path), pilot)
    result = guard.look()
    assert result["ok"] is False
    assert result["stopped"] == "hard:DELETE"
    assert ("back", 1, False) in pilot.keys
    assert result["step"] == 0
    assert guard.look()["refused"].startswith("the spike is stopped")


def test_hard_word_inside_the_ticker_band_does_not_stop(tmp_path):
    words = [
        {"text": "DELETE", "box": [100, 1350, 80, 30], "line": 0},
        {"text": "FRANCHISE", "box": [190, 1350, 120, 30], "line": 0},
        {"text": "WEEKLY", "box": [100, 100, 80, 30], "line": 1},
    ]
    frame = Frame(None, words, ["DELETE FRANCHISE", "WEEKLY"], [2560, 1440])
    guard = make_guard(tmp_path, brief_of(tmp_path), FakePilot(frame=frame))
    result = guard.look()
    assert result["ok"] is True
    assert result["stopped"] is None


def test_never_select_screen_stops_but_a_hub_does_not(tmp_path):
    pilot = FakePilot(screen="ROSTER_DECISION_PROMPT",
                      frame=frame_with_lines(tmp_path, ["KEEP ON ACTIVE ROSTER"]))
    guard = make_guard(tmp_path, brief_of(tmp_path), pilot)
    assert guard.look()["stopped"].startswith("hard:")

    hub = FakePilot(screen="HUB_WEEKLY", frame=frame_with_lines(tmp_path, ["MANAGE ROSTER"]))
    guard = make_guard(tmp_path, brief_of(tmp_path), hub, root=tmp_path / "root2")
    assert guard.look()["ok"] is True


# --------------------------------------------------------------------------- keys
def test_arrows_are_allowed_space_and_enter_refused(tmp_path):
    guard = make_guard(tmp_path, brief_of(tmp_path), FakePilot(screen="MAIN_MENU"))
    assert guard.key("down")["ok"] is True
    assert guard.key("pgdn")["ok"] is True
    for key in ("space", "enter"):
        result = guard.key(key)
        assert result["ok"] is False and result["refused"]


def test_q_needs_on_words_and_catalog_forbidden_keys_win(tmp_path):
    refused = make_guard(tmp_path, brief_of(tmp_path), FakePilot(screen="MAIN_MENU"))
    assert refused.key("q")["refused"].endswith("not in the brief's allow_keys")

    allowed = make_guard(tmp_path, brief_of(
        tmp_path, allow_keys=[{"label": "q", "on_words": ["WEEKLY HUB"]}]),
        FakePilot(screen="MAIN_MENU", frame=frame_with_lines(tmp_path, ["WEEKLY HUB"])))
    assert allowed.key("q")["ok"] is True

    missing = make_guard(tmp_path, brief_of(
        tmp_path, allow_keys=[{"label": "q", "on_words": ["WEEKLY HUB"]}]),
        FakePilot(screen="MAIN_MENU", frame=frame_with_lines(tmp_path, ["SOMETHING ELSE"])))
    assert missing.key("q")["ok"] is False

    hub = make_guard(tmp_path, brief_of(
        tmp_path, allow_keys=[{"label": "q", "on_words": ["WEEKLY HUB"]},
                              {"label": "tab"}]),
        FakePilot(screen="HUB_WEEKLY", frame=frame_with_lines(tmp_path, ["WEEKLY HUB"])))
    assert hub.key("q")["refused"].endswith("forbidden on HUB_WEEKLY")
    assert hub.key("tab")["refused"].endswith("forbidden on HUB_WEEKLY")

    loose = make_guard(tmp_path, brief_of(tmp_path, allow_keys=[{"label": "tab"}]),
                       FakePilot(screen="MAIN_MENU"))
    assert "on_words or on_kind" in loose.key("tab")["refused"]


def test_on_kind_keys(tmp_path):
    brief = brief_of(tmp_path, allow_keys=[{"label": "esc", "on_kind": "gameplay"}])
    allowed = make_guard(tmp_path, brief, FakePilot(screen="FRANCHISE_LIST"),
                         describe_fn=lambda png: desc(kind="gameplay"))
    assert allowed.key("esc")["ok"] is True
    refused = make_guard(tmp_path, brief, FakePilot(screen="FRANCHISE_LIST"),
                         describe_fn=lambda png: desc(kind="other"))
    assert refused.key("esc")["ok"] is False
    assert "gameplay" in refused.key("esc")["refused"]
    assert refused.key("space")["ok"] is False


def test_vision_warning_blocks_an_on_kind_key(tmp_path):
    brief = brief_of(tmp_path, allow_keys=[{"label": "esc", "on_kind": "gameplay"}])
    pilot = FakePilot(screen="FRANCHISE_LIST")
    guard = make_guard(tmp_path, brief, pilot,
                       describe_fn=lambda png: desc(kind="gameplay", warning="ONLINE"))
    result = guard.key("esc")
    assert result["ok"] is False
    assert result["stopped"] == "hard:ONLINE"
    assert not any(key[0] == "esc" for key in pilot.keys)


# --------------------------------------------------------------------------- confirms
def test_confirm_on_unknown_needs_the_border_on_the_label(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[{"label": "TEAM SETTINGS"}])
    pilot = FakePilot(screen="UNKNOWN", frame=border_frame(tmp_path))
    guard = make_guard(tmp_path, brief, pilot)
    result = guard.confirm("TEAM SETTINGS")
    assert result["ok"] is True
    assert pilot.keys == [("select", 1, True)]


def test_confirm_on_unknown_refusals(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[{"label": "TEAM SETTINGS"}])

    elsewhere = make_guard(tmp_path, brief,
                           FakePilot(screen="UNKNOWN", frame=border_frame(tmp_path, "OTHER")))
    assert elsewhere.confirm("TEAM SETTINGS")["ok"] is False
    assert "selection border" in elsewhere.confirm("TEAM SETTINGS")["refused"]

    from PIL import Image
    dark = tmp_path / "dark.png"
    Image.new("RGB", (400, 300), (0, 0, 0)).save(dark)
    unreadable = Frame(str(dark), [], ["SOMETHING"], [400, 300])
    guard = make_guard(tmp_path, brief, FakePilot(screen="UNKNOWN", frame=unreadable))
    assert guard.confirm("TEAM SETTINGS")["ok"] is False
    assert "not an OCR line" in guard.confirm("TEAM SETTINGS")["refused"]

    words = [{"text": "TEAM", "box": [100, 210, 60, 30], "line": 0},
             {"text": "SETTINGS", "box": [170, 210, 80, 30], "line": 0}]
    black = tmp_path / "black.png"
    Image.new("RGB", (400, 300), (0, 0, 0)).save(black)
    frame = Frame(str(black), words, ["TEAM SETTINGS"], [400, 300])
    no_border = make_guard(tmp_path, brief, FakePilot(screen="UNKNOWN", frame=frame))
    assert no_border.confirm("TEAM SETTINGS")["ok"] is False
    assert "unreadable" in no_border.confirm("TEAM SETTINGS")["refused"]


def test_confirm_on_unknown_refuses_missing_on_words(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[
        {"label": "TEAM SETTINGS", "on_words": ["PRESEASON"]}])
    guard = make_guard(tmp_path, brief,
                       FakePilot(screen="UNKNOWN", frame=border_frame(tmp_path)))
    result = guard.confirm("TEAM SETTINGS")
    assert result["ok"] is False
    assert "PRESEASON" in result["refused"]


def test_confirm_on_catalogued_screen_uses_confirm_on(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[{"label": "PROCEED WITHOUT PROGRESSION"}])
    pilot = FakePilot(screen="PROGRESSION_WARNING", frame=frame_with_lines(
        tmp_path, ["PROGRESSION WARNING", "PROCEED WITHOUT PROGRESSION"]))
    guard = make_guard(tmp_path, brief, pilot)
    result = guard.confirm("PROCEED WITHOUT PROGRESSION")
    assert result["ok"] is True
    assert pilot.confirms == [("PROGRESSION_WARNING", "PROCEED WITHOUT PROGRESSION")]
    assert pilot.policy["PROGRESSION_WARNING"] == ["PROCEED WITHOUT PROGRESSION"]


def test_confirm_on_roster_decision_prompt_is_refused(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[{"label": "KEEP ON ACTIVE ROSTER"}])
    pilot = FakePilot(screen="ROSTER_DECISION_PROMPT",
                      frame=frame_with_lines(tmp_path, ["KEEP ON ACTIVE ROSTER"]))
    guard = make_guard(tmp_path, brief, pilot)
    result = guard.confirm("KEEP ON ACTIVE ROSTER")
    assert result["ok"] is False
    assert pilot.confirms == []


def test_franchise_list_only_the_briefs_tile(tmp_path):
    brief = brief_of(tmp_path, save="CAREER-TUNE-SPIKE",
                     allow_confirm=[{"label": "CAREER-OTHER"}, {"label": "TUNE-SPIKE"}])
    pilot = FakePilot(screen="FRANCHISE_LIST",
                      frame=frame_with_lines(tmp_path, ["CURRENT FRANCHISES", "TUNE-SPIKE"]))
    guard = make_guard(tmp_path, brief, pilot)
    wrong = guard.confirm("CAREER-OTHER")
    assert wrong["ok"] is False and "brief's franchise tile" in wrong["refused"]
    assert guard.confirm("TUNE-SPIKE")["ok"] is True
    assert pilot.confirms == [("FRANCHISE_LIST", "TUNE-SPIKE")]


# --------------------------------------------------------------------------- vision
def test_rule_4b_vision_and_border_disagree(tmp_path):
    brief = brief_of(tmp_path, allow_confirm=[{"label": "TEAM SETTINGS"}])
    guard = make_guard(tmp_path, brief, FakePilot(screen="UNKNOWN", frame=border_frame(tmp_path)))
    guard.description = desc(selected="OTHER")
    result = guard.confirm("TEAM SETTINGS")
    assert result["ok"] is False
    assert result["refused"] == "vision and border disagree"


def test_vision_warning_adds_a_hard_stop(tmp_path):
    guard = make_guard(tmp_path, brief_of(tmp_path), FakePilot(screen="FRANCHISE_LIST"),
                       describe_fn=lambda png: desc(warning="TRADE PLAYER"))
    result = guard.describe()
    assert result["ok"] is False
    assert result["stopped"] == "hard:TRADE"


def test_vision_cannot_remove_a_hard_stop(tmp_path):
    pilot = FakePilot(frame=frame_with_lines(tmp_path, ["TRADE PLAYER"]))
    guard = make_guard(tmp_path, brief_of(tmp_path), pilot,
                       describe_fn=lambda png: desc(warning=None))
    result = guard.look(describe=True)
    assert result["ok"] is False
    assert result["stopped"] == "hard:TRADE"


# --------------------------------------------------------------------------- caps and state
def test_step_cap_counts_keys_and_stops_the_spike(tmp_path):
    brief = brief_of(tmp_path, max_steps=2)
    guard = make_guard(tmp_path, brief, FakePilot(screen="MAIN_MENU"))
    assert guard.key("down")["step"] == 1
    second = guard.key("down")
    assert second["step"] == 2
    assert second["stopped"] == "cap"
    refused = guard.key("down")
    assert refused["ok"] is False
    assert "stopped (cap)" in refused["refused"]
    assert guard.steps_left == 0


def test_minute_cap_with_a_fake_clock(tmp_path):
    clock = Clock()
    brief = brief_of(tmp_path, max_minutes=1)
    guard = make_guard(tmp_path, brief, FakePilot(screen="MAIN_MENU"), clock=clock)
    clock.advance(61)
    result = guard.look()
    assert result["ok"] is False
    assert result["stopped"] == "cap"
    assert result["minutes_left"] == 0.0


def test_state_persists_across_guards_and_a_stop_refuses_everything(tmp_path):
    brief = brief_of(tmp_path)
    clock = Clock()
    first = make_guard(tmp_path, brief, FakePilot(screen="MAIN_MENU"), clock=clock)
    first.key("down")

    second = make_guard(tmp_path, brief, FakePilot(screen="MAIN_MENU"), clock=clock)
    assert second.steps == 1
    assert second.key("down")["step"] == 2
    state = json.loads((tmp_path / "root" / "state.json").read_text())
    assert state["steps"] == 2
    assert state["started"] and state["deadline"]
    assert state["loaded_franchise"] is None

    stopped = make_guard(tmp_path, brief, FakePilot(
        frame=frame_with_lines(tmp_path, ["DELETE FRANCHISE"])), clock=clock)
    stopped.look()
    again = make_guard(tmp_path, brief, FakePilot(screen="MAIN_MENU"), clock=clock)
    assert again.look()["refused"].startswith("the spike is stopped")
    assert again.key("down")["ok"] is False


# --------------------------------------------------------------------------- spike-start
def test_start_refuses_a_running_watcher(tmp_path):
    state = tmp_path / "root" / "state.json"
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "CAREER-TUNE-SPIKE").write_bytes(b"save")
    guard, result = spike.start(
        brief_of(tmp_path, save="CAREER-TUNE-SPIKE"),
        FakePilot(screen="FRANCHISE_LIST"), state, dry_run=False,
        watcher=lambda: True, saves_folder=folder)
    assert guard is None
    assert "watch_save" in result["refused"]
    assert not state.exists()


def test_start_refuses_a_non_tune_franchise(tmp_path):
    guard, result = spike.start(
        brief_of(tmp_path, save="CAREER-TUNE-SPIKE", start="HUB_WEEKLY"),
        FakePilot(screen="HUB_WEEKLY", loaded_franchise="CAREER-MU2026"),
        tmp_path / "root" / "state.json", dry_run=True, watcher=lambda: False)
    assert guard is None
    assert "TUNE-" in result["refused"]


def test_start_refuses_a_running_spike(tmp_path):
    state = tmp_path / "root" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"steps": 1, "stopped": None}))
    guard, result = spike.start(brief_of(tmp_path), FakePilot(), state, dry_run=True,
                                watcher=lambda: False)
    assert guard is None
    assert "already running" in result["refused"]


def test_start_refuses_a_missing_save(tmp_path):
    folder = tmp_path / "saves"
    folder.mkdir()
    guard, result = spike.start(
        brief_of(tmp_path, save="CAREER-TUNE-SPIKE"), FakePilot(),
        tmp_path / "root" / "state.json", dry_run=False, watcher=lambda: False,
        saves_folder=folder)
    assert guard is None
    assert "no save" in result["refused"]
    assert not (tmp_path / "root" / "state.json").exists()


def test_start_launch_jr_waits_for_the_window_then_the_menu(tmp_path):
    calls = []
    guard, result = spike.start(
        brief_of(tmp_path, launch="jr"), FakePilot(screen="FRANCHISE_LIST"),
        tmp_path / "root" / "state.json", dry_run=False, watcher=lambda: False,
        wait_window=lambda: calls.append("window"), wait_menu=lambda: calls.append("menu"),
        launch=lambda: calls.append("launch"))
    assert result["ok"] is True
    assert calls == ["window", "menu"]


class ClosedPilot(FakePilot):
    """A pilot whose driver has no Madden window until launch() has run, like a closed game."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.launched = False

    def look(self):
        if not self.launched:
            raise Stuck("no window matches Madden NFL 27")
        return super().look()


def test_start_launches_before_the_first_look(tmp_path):
    pilot = ClosedPilot(screen="FRANCHISE_LIST")
    calls = []

    def launch():
        calls.append("launch")
        pilot.launched = True

    state = tmp_path / "root" / "state.json"
    guard, result = spike.start(
        brief_of(tmp_path, launch="pilot"), pilot, state, dry_run=False,
        watcher=lambda: False, launch=launch)
    assert calls == ["launch"]
    assert result["ok"] is True, result
    assert pilot.looks >= 1
    assert state.exists()


def test_start_launch_runs_before_an_open_wrong_franchise_refusal(tmp_path):
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "CAREER-TUNE-SPIKE").write_bytes(b"save")
    pilot = ClosedPilot(screen="HUB_WEEKLY", loaded_franchise="CAREER-MU2026")
    calls = []

    def launch():
        calls.append("launch")
        pilot.launched = True

    state = tmp_path / "root" / "state.json"
    guard, result = spike.start(
        brief_of(tmp_path, save="CAREER-TUNE-SPIKE", start="HUB_WEEKLY"), pilot, state,
        dry_run=False, watcher=lambda: False, saves_folder=folder, launch=launch)
    assert calls == ["launch"]
    assert guard is None
    assert "TUNE-" in result["refused"]
    assert not state.exists()


# --------------------------------------------------------------------------- clips
def test_build_clip_writes_a_gif_and_a_sheet(tmp_path):
    from PIL import Image

    frames = tmp_path / "clips" / "001"
    frames.mkdir(parents=True)
    for index in range(3):
        Image.new("RGB", (640, 360), (20 * index, 40, 60)).save(frames / f"frame_{index:04d}.jpg",
                                                                 "JPEG")
    gif, sheet = tmp_path / "clip.gif", tmp_path / "sheet.png"
    info = spike.build_clip(frames, gif, sheet, fps=5)
    assert info["frames"] == 3 and info["sheet_frames"] == 3
    assert gif.is_file() and sheet.is_file()
    with Image.open(gif) as opened:
        assert opened.n_frames == 3
        assert opened.size[0] == 480
    with Image.open(sheet) as opened:
        assert opened.size[0] == 6 * (320 + 12)      # a 6-column grid, one row for 3 frames


def test_build_clip_refuses_an_empty_dir(tmp_path):
    with pytest.raises(ValueError):
        spike.build_clip(tmp_path, tmp_path / "a.gif", tmp_path / "a.png")


# --------------------------------------------------------------------------- report
def tree_for_report(tmp_path):
    run_dir = tmp_path / "root" / "run"
    (run_dir / "steps").mkdir(parents=True)
    (run_dir / "steps" / "0001_FRANCHISE_LIST.png").write_bytes(b"png")
    (tmp_path / "root" / "clips" / "001").mkdir(parents=True)
    (tmp_path / "root" / "clips" / "001" / "sheet.png").write_bytes(b"png")
    return run_dir


def good_report(brief, **over):
    report = {
        "id": brief.id,
        "status": "answered",
        "answers": [{"question": brief.questions[0], "answer": "not found",
                     "evidence": ["steps/0001_FRANCHISE_LIST.png", "clips/001/sheet.png"]}],
        "route": [{"from": "FRANCHISE_LIST", "keys": [], "to": "FRANCHISE_LIST"}],
        "proposed_screens": [{"name": "TEAM_SETTINGS", "all": ["TEAM SETTINGS"], "any": [],
                              "none": [], "options": [], "seen": "steps/0001_FRANCHISE_LIST.png",
                              "note": "seen on the hub"}],
        "stops": [],
        "notes": "",
    }
    report.update(over)
    return report


def write_report(tmp_path, report):
    path = tmp_path / "root" / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report))
    return path


def test_check_report_passes_a_complete_report(tmp_path):
    brief = brief_of(tmp_path)
    run_dir = tree_for_report(tmp_path)
    assert spike.check_report(write_report(tmp_path, good_report(brief)), brief, run_dir) == []


def test_check_report_finds_problems(tmp_path):
    brief = brief_of(tmp_path)
    run_dir = tree_for_report(tmp_path)

    missing = good_report(brief)
    missing["answers"] = []
    assert any("missing an answer" in p for p in
               spike.check_report(write_report(tmp_path, missing), brief, run_dir))

    evidence = good_report(brief)
    evidence["answers"][0]["evidence"] = ["steps/no-such-frame.png"]
    assert any("evidence does not exist" in p for p in
               spike.check_report(write_report(tmp_path, evidence), brief, run_dir))

    duplicate = good_report(brief)
    duplicate["proposed_screens"] = [duplicate["proposed_screens"][0],
                                     dict(duplicate["proposed_screens"][0])]
    assert any("appears twice" in p for p in
               spike.check_report(write_report(tmp_path, duplicate), brief, run_dir))

    (tmp_path / "root" / "state.json").write_text(json.dumps({"stopped": "hard:TRADE"}))
    wrong = good_report(brief, status="partial")
    assert any("not 'stopped'" in p for p in
               spike.check_report(write_report(tmp_path, wrong), brief, run_dir))


# --------------------------------------------------------------------------- checkpoint + analyze
def test_spike_check_records_checkpoint_results(tmp_path):
    brief = brief_of(tmp_path, checks=[{"name": "probe", "measures": []}],
                     checkpoints=[{"name": "half", "checks": ["probe"]}])
    guard = make_guard(tmp_path, brief, FakePilot(screen="FRANCHISE_LIST"))
    result = guard.check(checkpoint="half")
    assert result["ok"] is True
    assert result["checks"]["probe"]["status"] == "unknown"
    state = json.loads((tmp_path / "root" / "state.json").read_text())
    assert state["checkpoints"][0]["name"] == "half"
    assert "probe" in state["checkpoints"][0]["checks"]


def test_end_checks_the_report_and_writes_the_markdown(tmp_path):
    brief = brief_of(tmp_path)
    run_dir = tree_for_report(tmp_path)
    report = write_report(tmp_path, good_report(brief))
    guard = make_guard(tmp_path, brief, FakePilot(screen="FRANCHISE_LIST"))
    result = spike.end(brief, guard, report, run_dir, leave=lambda pilot: None)
    assert result["ok"] is True
    assert result["status"] == "answered"
    markdown = (tmp_path / "root" / "report.md").read_text()
    assert "What is the team?" in markdown and "TEAM_SETTINGS" in markdown
    assert json.loads((tmp_path / "root" / "state.json").read_text())["stopped"] == "ended"
    assert guard.pilot.looks >= 1


def test_end_refuses_a_report_with_problems(tmp_path):
    brief = brief_of(tmp_path)
    run_dir = tree_for_report(tmp_path)
    report = good_report(brief)
    report["answers"] = []
    path = write_report(tmp_path, report)
    guard = make_guard(tmp_path, brief, FakePilot())
    result = spike.end(brief, guard, path, run_dir, leave=lambda pilot: None)
    assert result["ok"] is False
    assert "missing an answer" in result["refused"]
    assert not (tmp_path / "root" / "report.md").exists()


def test_end_quits_only_a_tune_franchise(tmp_path):
    brief = brief_of(tmp_path, save="CAREER-TUNE-SPIKE")
    run_dir = tree_for_report(tmp_path)
    report = write_report(tmp_path, good_report(brief))
    calls = []
    tune = make_guard(tmp_path, brief, FakePilot(screen="HUB_WEEKLY", loaded_franchise="TUNE-SPIKE"))
    assert spike.end(brief, tune, report, run_dir, quit_to_list=lambda: calls.append("quit"),
                     leave=lambda pilot: calls.append("leave"))["ok"] is True
    assert calls == ["quit"]

    calls.clear()
    other = make_guard(tmp_path, brief, FakePilot(screen="HUB_WEEKLY", loaded_franchise="CAREER-MU2026"),
                       root=tmp_path / "other")
    other.state["loaded_franchise"] = None
    assert spike.end(brief, other, report, run_dir, quit_to_list=lambda: calls.append("quit"),
                     leave=lambda pilot: calls.append("leave"))["ok"] is True
    assert calls == ["leave"]


def test_spike_check_runs_a_real_asset_check(tmp_path):
    from PIL import Image

    png = tmp_path / "cyan.png"
    Image.new("RGB", (64, 64), (0, 255, 255)).save(png)
    pilot = FakePilot(screen="FRANCHISE_LIST",
                      frame=Frame(str(png), [], ["CYAN FRAME"], [64, 64]))
    brief = brief_of(tmp_path, checks=[{"name": "cyan",
                                        "colour": {"hue": [170, 190], "pass_at": 0.9}}])
    guard = make_guard(tmp_path, brief, pilot)
    result = guard.check(checks=["cyan"])
    assert result["ok"] is True
    assert result["checks"]["cyan"]["status"] == "pass"
    assert result["checks"]["cyan"]["measures"]["colour_share"] > 0.9


def test_analyze_refuses_while_madden_runs(tmp_path):
    brief = brief_of(tmp_path)
    result = spike.analyze(tmp_path / "root", brief, tmp_path / "root" / "run",
                           running=lambda: True)
    assert result["ok"] is False
    assert "Madden27.exe" in result["refused"]


def test_analyze_writes_analysis_and_the_report_section(tmp_path):
    from PIL import Image

    root = tmp_path / "root"
    run_dir = tree_for_report(tmp_path)
    Image.new("RGB", (64, 36), (10, 10, 10)).save(run_dir / "steps" / "0002_FRANCHISE_LIST.png")
    (root / "report.md").write_text("# Report\n\nbody\n")
    result = spike.analyze(root, brief_of(tmp_path), run_dir,
                           describe_fn=lambda png: desc(), running=lambda: False)
    assert result["ok"] is True
    analysis = json.loads((root / "analysis.json").read_text())
    assert analysis["frames"] >= 1
    assert "## After-run analysis" in (root / "report.md").read_text()


# --------------------------------------------------------------------------- CLI
def test_cli_jobs_print_the_contract_and_exit_codes(tmp_path):
    path = brief_file(tmp_path, save=None)
    root = tmp_path / "root"

    def run(*argv):
        lines = []
        code = cli.main(list(argv), out=lines.append)
        assert len(lines) == 1, lines
        return code, json.loads(lines[0])

    code, data = run("spike-start", "--brief", str(path), "--run-dir", str(root), "--dry-run")
    assert code == 0 and data["ok"] is True
    assert CONTRACT_KEYS <= set(data)
    assert data["job"] == "spike-start" and data["steps_left"] == 20

    code, data = run("spike-look", "--brief", str(path), "--run-dir", str(root), "--dry-run")
    assert code == 0 and data["screen"] == "FRANCHISE_LIST" and data["lines"]

    code, data = run("spike-goto", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--target", "FRANCHISE_LIST")
    assert code == 0 and data["step"] == 0

    code, data = run("spike-key", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--name", "down")
    assert code == 0 and data["step"] == 1
    assert data["steps_left"] == 19

    code, data = run("spike-key", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--name", "space")
    assert code == 1 and data["refused"]

    code, data = run("spike-confirm", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--label", "NOT ALLOWED")
    assert code == 1 and data["refused"]

    code, data = run("spike-record", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--seconds", "1", "--fps", "2")
    assert code == 0
    assert (root / "clips" / "001" / "clip.gif").is_file()
    assert (root / "clips" / "001" / "sheet.png").is_file()

    code, data = run("spike-check", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--check", "nope")
    assert code == 1 and "unknown check" in data["refused"]

    code, data = run("spike-end", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--report", str(root / "missing.json"))
    assert code == 1 and data["refused"]


def test_cli_refuses_an_invalid_brief(tmp_path):
    path = brief_file(tmp_path, max_steps=0)
    lines = []
    code = cli.main(["spike-start", "--brief", str(path), "--run-dir", str(tmp_path / "root"),
                     "--dry-run"], out=lines.append)
    data = json.loads(lines[-1])
    assert code == 1 and data["ok"] is False
    assert "max_steps" in data["refused"]
    assert CONTRACT_KEYS <= set(data)


def test_cli_vision_null_when_the_backend_is_down(tmp_path, monkeypatch):
    path = brief_file(tmp_path)
    root = tmp_path / "root"
    assert cli.main(["spike-start", "--brief", str(path), "--run-dir", str(root), "--dry-run"],
                    out=lambda _msg: None) == 0

    def down(png):
        raise vision.VisionUnavailable("backend down")

    monkeypatch.setattr(cli, "_fake_describe", down)
    lines = []
    code = cli.main(["spike-look", "--brief", str(path), "--run-dir", str(root), "--dry-run",
                     "--describe"], out=lines.append)
    data = json.loads(lines[-1])
    assert code == 0
    assert data["vision"] is None
    assert data["vision_error"] == "backend down"


def test_cli_spike_analyze_dry_run(tmp_path):
    path = brief_file(tmp_path)
    root = tmp_path / "root"
    lines = []
    code = cli.main(["spike-analyze", "--brief", str(path), "--run-dir", str(root), "--dry-run"],
                    out=lines.append)
    data = json.loads(lines[-1])
    assert code == 0 and data["ok"] is True
    assert CONTRACT_KEYS <= set(data)
    assert (root / "analysis.json").is_file()


def test_spike_exit_codes():
    assert cli._spike_exit({"ok": True, "screen": "FRANCHISE_LIST"}) == 0
    assert cli._spike_exit({"ok": False, "screen": "FRANCHISE_LIST"}) == 1
    assert cli._spike_exit({"ok": True, "screen": "UNKNOWN"}) == 2
    assert cli._spike_exit({"ok": True, "screen": "FRANCHISE_LIST", "stopped": "cap"}) == 1
    assert cli._spike_exit({"ok": True, "screen": "FRANCHISE_LIST", "stopped": "ended"}) == 0


# --------------------------------------------------------------------------- allow_space
def test_allow_space_entry_needs_words_and_a_bounded_limit(tmp_path):
    with pytest.raises(spike.BriefError) as exc:
        brief_of(tmp_path, allow_space=[{"limit": 2}])
    assert exc.value.field == "allow_space"
    with pytest.raises(spike.BriefError):
        brief_of(tmp_path, allow_space=[{"on_words": ["JUMP FORWARD"], "limit": 0}])
    with pytest.raises(spike.BriefError):
        brief_of(tmp_path, allow_space=[{"on_words": ["JUMP FORWARD"], "limit": 99}])
    brief = brief_of(tmp_path, allow_space=[{"on_words": ["JUMP FORWARD"], "not_words": ["TRADE"],
                                             "limit": 2}])
    assert brief.allow_space[0].words == ("JUMP FORWARD",)
    assert brief.allow_space[0].not_words == ("TRADE",)
    assert brief.allow_space[0].limit == 2


def test_space_is_pressed_only_on_an_allow_space_frame_and_only_to_its_limit(tmp_path):
    """An in-game frame carries no OCR label; the brief's own words stand in for the border."""
    brief = brief_of(tmp_path, start="HUB_WEEKLY",
                     allow_space=[{"on_words": ["JUMP FORWARD"], "limit": 2}])
    frame = frame_with_lines(tmp_path, ["JUMP FORWARD", "NEXT QUARTER", "END OF GAME"])
    pilot = FakePilot(screen="UNKNOWN", frame=frame)
    guard = make_guard(tmp_path, brief, pilot)

    assert guard.key("space")["ok"] is True
    assert pilot.keys[-1] == ("select", 1, True)          # Space, once, as a checked select
    assert guard.key("space")["ok"] is True
    third = guard.key("space")
    assert third["ok"] is False and "allow_space" in third["refused"]
    assert len([key for key in pilot.keys if key[0] == "select"]) == 2


def test_allow_space_does_not_match_another_frame_or_a_naughty_word(tmp_path):
    brief = brief_of(tmp_path, start="HUB_WEEKLY",
                     allow_space=[{"on_words": ["JUMP FORWARD"], "not_words": ["TRADE"],
                                   "limit": 3}])
    other = FakePilot(screen="UNKNOWN", frame=frame_with_lines(tmp_path, ["ROSTER", "OVERALL"]))
    refused = make_guard(tmp_path, brief, other).key("space")
    assert refused["ok"] is False and "no allow_space entry matches" in refused["refused"]
    assert not other.keys

    guarded = FakePilot(screen="UNKNOWN",
                        frame=frame_with_lines(tmp_path, ["JUMP FORWARD", "TRADE PLAYER"]))
    blocked = make_guard(tmp_path, brief, guarded).key("space")
    assert blocked["ok"] is False and "TRADE" in blocked["refused"]
    assert not guarded.keys


def test_space_is_refused_with_no_allow_space_entry_and_enter_stays_refused(tmp_path):
    brief = brief_of(tmp_path, start="HUB_WEEKLY")
    pilot = FakePilot(screen="UNKNOWN", frame=frame_with_lines(tmp_path, ["JUMP FORWARD"]))
    guard = make_guard(tmp_path, brief, pilot)
    assert guard.key("space")["ok"] is False
    assert guard.key("enter")["ok"] is False
    assert not pilot.keys
