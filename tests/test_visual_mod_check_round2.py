"""Visual mod check, second fix round: the real-frame and lifecycle regressions.

Every real-image/OCR case here reads a committed fixture under
``tests/fixtures/visual_mod_check/``, which was copied from the auditor's saved PC artifacts
(see that folder's README). No PC, no Windows, no database, no model.

Groups:
  * F8  receiving-side kickoff vs the kicking panel, and the no-key automatic transition;
  * F9  the selected row must lie inside the selected rectangle (truncated/missing OCR);
  * F10 Back from super-sim lands on the play call, and the last snap re-reads the clock;
  * F11 the real EXIT GAME modal vs the pause menu's QUIT GAME tooltip;
  * F6  a verified launch is reusable only for the same process and unchanged pack;
  * R1/R2/R3  record-after-preroll, failed earlier cleanup, and same-second run names.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from tools.madden_pilot import jobs, screens
from tools.madden_pilot.__main__ import ScriptedMadden
from tools.madden_pilot.navigate import Pilot, Stuck
from tools.madden_pilot.screens import Frame, border_highlight, classify, load_catalog
from tools.mod_check import runner

# Accessed through the module, not imported by name: on the parent pin these seams do not exist
# yet, and a behavioural failure per test is what the audit wants, not a collection error.
DriverError = runner.DriverError
capture = runner.capture

CAT = load_catalog()
FIXTURES = Path(__file__).parent / "fixtures" / "visual_mod_check"


# --------------------------------------------------------------------------- fixtures
def fixture_frame(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    png = str(FIXTURES / data["png"]) if data.get("png") else None
    return Frame(png, data["words"], data["lines"])


def fixture_lines(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["lines"]


def make_pilot(tmp_path, fake, catalog=None, **kw):
    return Pilot(catalog or CAT, tmp_path, driver=fake, sleep=fake.sleep, clock=fake.clock, **kw)


class WatchMadden(ScriptedMadden):
    """The scripted Madden with the measured quit modal and the play-call landing (F10/F11)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.kick_at = None
        self.play_at = None
        self.quit_game_at = None
        self.receive_select = False

    def on_key(self, name):
        before, label = self.screen, self.highlight
        super().on_key(name)
        select = self.keymap.get("select", "space")
        if before == "GAME_KICKOFF" and name == select:
            self.kick_at = len(self.keys)
        elif before == "GAME_PLAY_CALL" and name == select and self.play_at is None:
            self.play_at = len(self.keys) - 1
        elif before == "PAUSE_MENU" and name == select and label == "QUIT GAME":
            self.quit_game_at = len(self.keys)
        elif before == "GAME_KICKOFF_RECEIVE" and name == select:
            self.receive_select = True


@pytest.fixture
def notices(monkeypatch):
    import pipeline.notify
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify", lambda msg, **kw: sent.append(msg) or True)
    return sent


@pytest.fixture(autouse=True)
def no_real_watcher(monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)


def watch(tmp_path, fake, **kw):
    p = make_pilot(tmp_path, fake, highlight_reader=lambda frame, labels, spec: fake.highlight)
    kw.setdefault("folder", tmp_path / "none")
    kw.setdefault("save", "CAREER-TUNE-X")
    return p, jobs.watch_game(p, watcher=lambda: False, **kw)


# --------------------------------------------------------------------------- F8
def test_real_receiving_kickoff_is_a_distinct_measured_state():
    receiving = fixture_frame("receiving_kickoff_0047")
    assert classify(receiving, CAT) == "GAME_KICKOFF_RECEIVE"
    assert not screens.matches(receiving, CAT["screens"]["GAME_KICKOFF"])
    assert receiving.line_is("KICKOFF") and receiving.has("KICKOFF")   # own banner line
    # no kick controls and no surname anchor in the catalog rule
    for word in ("DEEP KICK", "PLACEMENT", "POWER SQUIB"):
        assert not receiving.has(word), word
    # a player surname is on the frame but is never a catalog anchor (rosters change)
    catalog_words = " ".join(CAT["screens"]["GAME_KICKOFF_RECEIVE"].get("all", [])
                             + CAT["screens"]["GAME_KICKOFF_RECEIVE"].get("any", [])
                             + CAT["screens"]["GAME_KICKOFF_RECEIVE"].get("none", []))
    assert "LEGETTE" not in catalog_words and "XAVIER" not in catalog_words
    # the referee/empty frame that ended the live wait is not a kickoff state
    assert classify(fixture_frame("referee_0072"), CAT) == "UNKNOWN"
    # a later KICKOFF ticker line does not make a super-sim menu a receiving kickoff
    ticker = Frame(None, [{"text": line, "line": i}
                          for i, line in enumerate(fixture_lines("playcall_0004"))])
    assert classify(ticker, CAT) == "GAME_PLAY_CALL"
    # the kicking view still needs its own controls
    kicking = Frame(None, [{"text": line, "line": i} for i, line in enumerate(
        ["KICKOFF", "DEEP KICK", "PLACEMENT", "POWER SQUIB", "1st 11:00"])])
    assert classify(kicking, CAT) == "GAME_KICKOFF"


def test_real_receiving_transition_reaches_the_play_call():
    assert classify(fixture_frame("playcall_after_receiving"), CAT) == "GAME_PLAY_CALL"


class FakeStartPilot:
    """Only what _start_game touches: scripted looks and a recorded press log."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.presses = []
        self.policy = {}
        self.screen = None
        self.frame = None
        self.loaded_franchise = None
        self.unknown_limit = 20
        self.last_known = None
        self.step = 0

    def look(self):
        self.frame, self.screen = self.frames.pop(0)
        return self.frame, self.screen

    def press(self, key, hold=0.08, wait=0.6, times=1, _confirm=False):
        self.presses.append((key, hold, self.screen))

    def highlight(self, label):
        self.presses.append(("highlight", label, self.screen))

    def confirm_on(self, screen, label, wait=1.0):
        self.presses.append(("confirm", label, screen))

    def goto(self, target, **kw):
        raise AssertionError("the hub is already on screen")

    def sleep(self, seconds):
        pass

    def clock(self):
        return time.monotonic()

    def stop(self, reason, kind=Stuck):
        self.failure = reason
        raise kind(reason)

    def spec(self, screen=None):
        return CAT["screens"].get(screen or self.screen, {})


def _clock_frame(quarter_line="1st 11:00"):
    return Frame(None, [{"text": quarter_line, "line": 0}])


def test_start_game_waits_out_a_receiving_kickoff_without_a_kick_hold():
    ready = Frame(None, [{"text": "READY", "line": 0}])
    pilot = FakeStartPilot([
        (ready, "HUB_GAMEDAY"),
        (ready, "GAME_PREGAME_READY"),
        (_clock_frame(), "GAME_KICKOFF_RECEIVE"),
        (Frame(None, [{"text": "COACH SUGGESTIONS", "line": 0}]), "GAME_PLAY_CALL"),
    ])
    screen, how = jobs._start_game(pilot)
    assert (screen, how) == ("GAME_PLAY_CALL", "receiving")
    # no 1.5 s kick hold, and no select at all while the receiving frame is up
    assert not any(press[0] == "select" and press[1] == jobs.KICK_HOLD for press in pilot.presses)
    assert not any(press[2] == "GAME_KICKOFF_RECEIVE" and press[0] == "select"
                   for press in pilot.presses)


def test_start_game_refuses_a_receiving_frame_with_no_scorebug_clock():
    ready = Frame(None, [{"text": "READY", "line": 0}])
    pilot = FakeStartPilot([
        (ready, "HUB_GAMEDAY"),
        (ready, "GAME_PREGAME_READY"),
        (Frame(None, [{"text": "KICKOFF", "line": 0}]), "GAME_KICKOFF_RECEIVE"),
    ])
    with pytest.raises(Stuck) as caught:
        jobs._start_game(pilot)
    assert "no readable scorebug clock" in str(caught.value)
    assert not any(press[0] == "select" for press in pilot.presses)


class ReceivingWatchMadden(WatchMadden):
    """The measured receiving start: RECEIVE, then the CPU advances with no agent key."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.receive_looks = 0

    def game_key(self, name, label, select):
        if self.screen == "GAME_PREGAME_READY" and label == "READY":
            self.pending = [jobs.READY_FILE and "LOADING"] * 2
            self.go("GAME_KICKOFF_RECEIVE")
            return True
        if self.screen == "GAME_KICKOFF_RECEIVE":
            return True                          # a key here would be the bug; swallow it
        return super().game_key(name, label, select)

    def call(self, cmd):
        if cmd[0] == "look" and self.screen == "GAME_KICKOFF_RECEIVE":
            self.receive_looks += 1
            if self.receive_looks >= 2:          # the unattended CPU transition
                self.go("GAME_PLAY_CALL")
        return super().call(cmd)


def test_watch_game_receiving_start_records_no_kick_and_routes_to_supersim(tmp_path, notices):
    log = []
    fake = ReceivingWatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = watch(tmp_path, fake, to="supersim", hold_seconds=2)
    assert out["status"] == "ok", out
    assert not fake.receive_select and fake.kick_at is None
    assert "GAME_KICKOFF_RECEIVE" in "\n".join(log)
    assert out["route"] == "supersim" and fake.screen == "FRANCHISE_LIST"


def test_watch_game_receiving_start_refuses_a_kickoff_record_without_a_kick_view(tmp_path, notices):
    fake = ReceivingWatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = watch(tmp_path, fake, to="kickoff", hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck", out
    assert "receiving side" in out["reason"]
    assert not fake.receive_select


# --------------------------------------------------------------------------- F9
def test_real_truncated_supersim_label_never_authorizes_the_neighbour():
    frame = fixture_frame("supersim_next_play_0003")
    spec = CAT["screens"]["SUPERSIM_MENU"]
    assert border_highlight(frame, spec["options"], aliases=spec.get("aliases")) \
        == "JUMP TO NEXT PLAY"
    # the old filtered list (only labels whose exact text OCRs) drops the selected row; the
    # reader must then say "unknown", never hand the border to JUMP FORWARD
    visible = ["CUSTOM PLAY OPTIONS", "JUMP FORWARD"]
    assert border_highlight(frame, visible, aliases=spec.get("aliases")) is None


def test_real_truncated_supersim_frame_presses_no_space_for_the_wrong_row(tmp_path, notices):
    frame = fixture_frame("supersim_next_play_0003")

    class Recorder:
        def __init__(self):
            self.calls = []

        def call(self, args):
            self.calls.append(args)
            return {"ok": True}

    driver = Recorder()
    pilot = Pilot(CAT, tmp_path, driver=driver, sleep=lambda _: None)
    pilot.frame, pilot.screen = frame, "SUPERSIM_MENU"
    with pytest.raises(Stuck):
        pilot.confirm_on("SUPERSIM_MENU", "JUMP FORWARD")
    assert not [call for call in driver.calls if "space" in call]


def _selection_image(tmp_path, name, rects, labels):
    png = tmp_path / f"{name}.png"
    img = Image.new("RGB", (800, 500), (15, 15, 20))
    draw = ImageDraw.Draw(img)
    for box, fill in rects:
        if fill:
            draw.rectangle(box, fill=(235, 235, 235))
        else:
            draw.rectangle(box, outline=(255, 255, 255), width=3)
    img.save(png)
    words = [{"text": text, "box": box, "line": i} for i, (text, box) in enumerate(labels)]
    return Frame(str(png), words, [text for text, _ in labels])


def test_a_selected_fill_containing_no_label_is_never_awarded_to_a_neighbour(tmp_path):
    # the selected row's text is gone (as the modal's NO is); the next label sits just below it
    frame = _selection_image(
        tmp_path, "missing-selected",
        [((90, 120, 380, 190), True)],
        [("ROW ONE", [100, 60, 140, 28]), ("ROW TWO", [100, 205, 140, 28])])
    assert border_highlight(frame, ["ROW ONE", "ROW TWO"]) is None


def test_a_fill_containing_two_labels_is_ambiguous(tmp_path):
    frame = _selection_image(
        tmp_path, "two-inside",
        [((70, 90, 430, 240), True)],
        [("ROW ONE", [100, 120, 140, 28]), ("ROW TWO", [100, 190, 140, 28])])
    assert border_highlight(frame, ["ROW ONE", "ROW TWO"]) is None


def test_a_contained_label_is_the_selection_in_both_reader_shapes(tmp_path):
    filled = _selection_image(
        tmp_path, "filled", [((70, 90, 430, 165), True)],
        [("ROW ONE", [100, 112, 140, 28]), ("ROW TWO", [100, 260, 140, 28])])
    assert border_highlight(filled, ["ROW ONE", "ROW TWO"]) == "ROW ONE"
    outlined = _selection_image(
        tmp_path, "outlined", [((70, 90, 430, 165), False)],
        [("ROW ONE", [100, 112, 140, 28]), ("ROW TWO", [100, 260, 140, 28])])
    assert border_highlight(outlined, ["ROW ONE", "ROW TWO"]) == "ROW ONE"


def test_a_lone_bright_line_is_not_selection_evidence(tmp_path):
    # the old reader credited whichever label sat nearest a bright row; that is exactly how the
    # selected row's border leaked to FORWARD. One line, no rectangle, is not a selection.
    png = tmp_path / "line.png"
    img = Image.new("RGB", (800, 400), (10, 10, 10))
    ImageDraw.Draw(img).rectangle([90, 180, 400, 184], fill=(255, 255, 255))
    img.save(png)
    frame = Frame(str(png), [{"text": "ROW ONE", "box": [100, 200, 140, 30], "line": 0}],
                  ["ROW ONE"])
    assert border_highlight(frame, ["ROW ONE"]) is None


# --------------------------------------------------------------------------- F10
def test_real_play_call_frames_classify_and_unknown_is_not_one():
    for name in ("playcall_0004", "playcall_0022", "playcall_after_receiving"):
        assert classify(fixture_frame(name), CAT) == "GAME_PLAY_CALL", name
    assert classify(fixture_frame("referee_0072"), CAT) == "UNKNOWN"


def test_supersim_back_returns_to_the_play_call_and_quit_escapes_from_it(tmp_path, notices):
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = watch(tmp_path, fake, to="supersim", hold_seconds=2)
    assert out["status"] == "ok", out
    text = "\n".join(log)
    assert "SUPERSIM_MENU -> GAME_PLAY_CALL" in text
    assert "esc       GAME_PLAY_CALL -> PAUSE_MENU" in text
    # the QUIT GAME press happens once, only after a verified PAUSE_MENU
    assert text.index("GAME_PLAY_CALL -> PAUSE_MENU") < text.index("PAUSE_MENU on QUIT GAME")
    assert out["end_screen"] == "FRANCHISE_LIST"


def test_alternate_supersim_back_landing_on_pause_needs_no_extra_escape(tmp_path, notices):
    class PauseLanding(WatchMadden):
        def on_key(self, name):
            if name == "backspace" and self.screen == "SUPERSIM_MENU":
                self.go("PAUSE_MENU")
                return
            super().on_key(name)

    log = []
    fake = PauseLanding(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = watch(tmp_path, fake, to="supersim", hold_seconds=2)
    assert out["status"] == "ok", out
    text = "\n".join(log)
    # Back already landed on Pause: the quit goes straight to QUIT GAME, with no Escape toggle
    assert "esc       PAUSE_MENU" not in text
    assert text.count("PAUSE_MENU on QUIT GAME") == 1


@pytest.mark.parametrize("clock_line,reason", [
    ("2nd 0:00", "no last"), ("3rd 12:40", "Q2"), ("2nd :40", "clock read None")])
def test_halftime_revalidates_the_clock_before_the_last_snap(tmp_path, notices, clock_line, reason):
    class ClockAfterBack(WatchMadden):
        def lines(self, screen):
            lines = [ln for ln in super().lines(screen) if ln != self.game_clock]
            if screen == "GAME_PLAY_CALL":
                return [clock_line] + lines
            return lines

    log = []
    fake = ClockAfterBack(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = watch(tmp_path, fake, to="halftime", hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck", out
    assert fake.play_at is None                     # no last-play Space
    assert "clock" in out["reason"]


# --------------------------------------------------------------------------- F11
def test_real_pause_tooltip_is_the_pause_menu_not_a_confirmation():
    frame = fixture_frame("pause_quit_tooltip_0010")
    assert classify(frame, CAT) == "PAUSE_MENU"
    assert not screens.matches(frame, CAT["screens"]["QUIT_GAME_CONFIRM"])
    assert frame.has("PROGRESS") and frame.has("QUIT GAME")


def test_real_exit_game_modal_is_its_own_screen_and_never_lends_its_fill_to_menu():
    frame = fixture_frame("exit_game_modal_0002")
    assert classify(frame, CAT) == "QUIT_GAME_CONFIRM"
    assert frame.line_is("EXIT GAME") and frame.has("ARE YOU SURE")
    # the selected NO is absent from OCR: the filled row must not be read as the MENU row above it
    assert frame.find("NO") is None
    assert border_highlight(frame, ["YES - EXIT TO DESKTOP", "YES - EXIT TO MENU", "NO"]) is None
    assert border_highlight(frame, ["YES - EXIT TO MENU"]) is None
    # no "QUIT GAME" wording is invented for this modal
    assert not frame.has("QUIT GAME")


def test_confirm_quit_game_never_picks_desktop_or_no(tmp_path, notices):
    import copy
    cat = copy.deepcopy(CAT)
    modal = cat["screens"]["QUIT_GAME_CONFIRM"]
    modal["confirm_ok"] = ["YES - EXIT TO DESKTOP", "NO"]   # a hostile/incorrect catalog
    modal["forbidden_confirm"] = True
    fake = WatchMadden(cat, start="QUIT_GAME_CONFIRM", tiles=["TUNE-X"], out=lambda s: None)
    pilot = make_pilot(tmp_path, fake, catalog=cat,
                       highlight_reader=lambda frame, labels, spec: fake.highlight)
    pilot.look()
    with pytest.raises(Stuck):
        jobs._confirm_quit_game(pilot)
    assert "space" not in fake.keys


def test_quit_game_presses_quit_once_then_only_the_measured_menu_row(tmp_path, notices):
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = watch(tmp_path, fake, to="kickoff", hold_seconds=2)
    assert out["status"] == "ok", out
    text = "\n".join(log)
    assert text.count("PAUSE_MENU on QUIT GAME") == 1
    assert "QUIT_GAME_CONFIRM on YES - EXIT TO MENU -> FRANCHISE_LIST" in text
    assert "EXIT TO DESKTOP" not in text and "on NO " not in text


def test_the_modal_needs_its_own_anchors_not_pause_progress(tmp_path, notices):
    lines = ["RESUME", "CALL TIMEOUT", "QUIT GAME", "Any unsaved progress will be lost.",
             "SELECT", "BACK"]
    assert classify(Frame(None, [{"text": ln, "line": i} for i, ln in enumerate(lines)]),
                    CAT) == "PAUSE_MENU"


# --------------------------------------------------------------------------- F6
def test_pack_fingerprint_tracks_the_pack_and_its_enabled_files(tmp_path):
    from pipeline import mod_packs
    config = tmp_path / "manager_config.json"
    mods = tmp_path / "Mods"
    mods.mkdir()
    (mods / "a.fbmod").write_bytes(b"aaa")
    config.write_text(json.dumps({"Games": {"Madden27": {
        "Options": {"SelectedPack": "Other"},
        "Packs": {"Mu Test": "a.fbmod:True|b.fbmod:False"}}}}), encoding="utf-8")
    first = mod_packs.pack_fingerprint("Mu Test", path=str(config), mods_dir=str(mods))
    assert first["pack"] == "Mu Test" and first["selected"] == "Other"
    assert first["entries"] == 2 and first["enabled"] == 1
    time.sleep(0.01)
    (mods / "a.fbmod").write_bytes(b"changed")
    second = mod_packs.pack_fingerprint("Mu Test", path=str(config), mods_dir=str(mods))
    assert second["fingerprint"] != first["fingerprint"]
    assert mod_packs.pack_fingerprint("Nope", path=str(config), mods_dir=str(mods)) is None


def _receipt(**over):
    base = {"status": "ok", "pack": "Mu Test", "already_running": False,
            "process_verified": True, "applied_verified": True,
            "process": {"pid": 4242, "started": 1_700_000_000.0, "name": "Madden27"},
            "applied": {"pack": "Mu Test", "fingerprint": "abc", "selected": "Other"}}
    base.update(over)
    return base


def test_attach_accepts_the_same_verified_process_and_unchanged_pack():
    receipt = _receipt()
    got = runner.attach_launch(receipt, "Mu Test",
                        identity=lambda: {"pid": 4242, "started": 1_700_000_001.0},
                        fingerprint=lambda pack: {"pack": pack, "fingerprint": "abc"})
    assert got["pack"] == "Mu Test"


@pytest.mark.parametrize("receipt,identity,fingerprint,word", [
    (None, None, None, "no successful launch receipt"),
    (_receipt(already_running=True), None, None, "already-running"),
    (_receipt(pack="Other"), None, None, "not the plan's"),
    (_receipt(process_verified=False), None, None, "process identity"),
    (_receipt(applied_verified=False), None, None, "applied-pack fingerprint"),
    (_receipt(), lambda: None, None, "cannot read the running Madden"),
    (_receipt(), lambda: {"pid": 1, "started": 1_700_000_000.0}, None, "not the launched"),
    (_receipt(), lambda: {"pid": 4242, "started": 1_600_000_000.0}, None, "not the launched"),
    (_receipt(), lambda: {"pid": 4242, "started": 1_700_000_000.0}, lambda p: None,
     "cannot read the applied pack"),
    (_receipt(), lambda: {"pid": 4242, "started": 1_700_000_000.0},
     lambda p: {"pack": p, "fingerprint": "different"}, "changed since the launch"),
])
def test_attach_refuses_unknown_stale_wrong_pack_and_changed_state(receipt, identity,
                                                                  fingerprint, word):
    with pytest.raises(runner.LaunchError) as caught:
        runner.attach_launch(receipt, "Mu Test", identity=identity, fingerprint=fingerprint)
    assert word in str(caught.value)


def test_default_launch_still_refuses_a_running_madden():
    from pipeline.gpu_queue import process_check
    with patch.object(process_check, "is_madden_running", return_value=True):
        with pytest.raises(runner.LaunchError) as caught:
            runner.default_launch("Mu Test", "/tmp/unused")
    assert "already running" in str(caught.value)


def test_process_identity_and_start_time_are_the_identity(tmp_path):
    from pipeline.gpu_queue import process_check

    class Result:
        returncode = 0
        stdout = "4242|1700000000|Madden27\n"

    got = process_check.madden_process_identity(run=lambda *a, **k: Result())
    assert got == {"pid": 4242, "started": 1_700_000_000.0, "name": "Madden27"}
    assert process_check.same_process_identity(
        got, {"pid": 4242, "started": 1_700_000_001.5})
    assert not process_check.same_process_identity(
        got, {"pid": 4242, "started": 1_700_000_010.0})
    assert not process_check.same_process_identity(
        got, {"pid": 9999, "started": 1_700_000_000.0})

    class Failed:
        returncode = 3
        stdout = ""

    assert process_check.madden_process_identity(run=lambda *a, **k: Failed()) is None


# F6 integration through `run`: --reuse-launch never calls a launcher.
def write_plan(folder, segments, pack="Mu Test"):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"pack": pack, "segments": segments}
    (folder / "segments.json").write_text(json.dumps(data), encoding="utf-8")


def write_checks(folder):
    from PIL import Image
    (folder / "refs").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (255, 0, 0)).save(folder / "refs/red_pass.png")
    Image.new("RGB", (64, 64), (0, 0, 255)).save(folder / "refs/red_native.png")
    (folder / "checks.json").write_text(json.dumps({"asset": {
        "region": [0.0, 0.0, 1.0, 1.0],
        "reference": {"pass": "refs/red_pass.png", "fail": "refs/red_native.png",
                      "margin": 0.05}}}), encoding="utf-8")


class OneFrameDriver:
    def start(self, save, pack=None):
        self.pack = pack
        return {"status": "ok"}

    def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
        pass

    def record(self, out_dir, seconds, fps, width):
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "frame_0000.png"
        Image.new("RGB", (64, 64), (255, 0, 0)).save(path)
        return [path]

    def finish(self):
        return {"status": "ok", "quit": True, "quit_route": "quit-game",
                "end_screen": "FRANCHISE_LIST", "pack": self.pack}


def _reuse_setup(tmp_path, receipt):
    root = tmp_path / "data"
    folder = root / "demo"
    folder.mkdir(parents=True)
    write_checks(folder)
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 1,
                         "fps": 1, "width": 640, "checks": ["asset"]}])
    if receipt is not None:
        runner.write_launch_receipt(folder, receipt)
    return root, folder


def test_reuse_launch_records_without_relaunching_and_reports_provenance(tmp_path):
    root, folder = _reuse_setup(tmp_path, _receipt())
    calls = []

    def launcher(pack):
        calls.append(pack)
        raise AssertionError("a reuse must never launch")

    result = runner.run(
        "CAREER-TUNE-X", "demo", data_dir=root, driver=OneFrameDriver(), watcher=lambda: False,
        clone=lambda src, prefix: "CAREER-TUNE-CHK1", describe=lambda png: "ok",
        notice=lambda line: None, sleep=lambda s: None, launch=launcher, reuse=True,
        identity=lambda: {"pid": 4242, "started": 1_700_000_000.5},
        fingerprint=lambda pack: {"pack": pack, "fingerprint": "abc"})
    assert result["status"] == "pass", result
    assert calls == [] and result["attached"] is True
    assert result["provenance"]["reused"] is True
    assert result["provenance"]["process_verified"] is True
    assert result["provenance"]["applied_verified"] is True
    report = json.loads(Path(result["report_json"]).read_text())
    assert report["provenance"]["reused"] is True
    assert report["pack"] == "Mu Test"


def test_reuse_launch_refuses_an_unknown_receipt_before_cloning_or_recording(tmp_path):
    root, folder = _reuse_setup(tmp_path, None)
    result = runner.run(
        "CAREER-TUNE-X", "demo", data_dir=root, driver=OneFrameDriver(), watcher=lambda: False,
        clone=lambda src, prefix: "CAREER-TUNE-CHK1", describe=lambda png: "ok",
        notice=lambda line: None, sleep=lambda s: None,
        launch=lambda pack: (_ for _ in ()).throw(AssertionError("no launch")), reuse=True)
    assert result["status"] == "stopped" and result["stop"] == "attach"
    assert "no successful launch receipt" in result["why"]
    assert not (root / "runs").exists()


def test_a_cold_launch_stores_its_receipt_for_the_next_fixture(tmp_path):
    root, folder = _reuse_setup(tmp_path, None)
    receipt = {"status": "ok", "pack": "Mu Test", "already_running": False,
               "process_verified": False, "applied_verified": False}
    runner.run("CAREER-TUNE-X", "demo", data_dir=root, driver=OneFrameDriver(),
               watcher=lambda: False, clone=lambda src, prefix: "CAREER-TUNE-CHK1",
               describe=lambda png: "ok", notice=lambda line: None, sleep=lambda s: None,
               launch=lambda pack: receipt)
    stored = runner.read_launch_receipt(folder)
    assert stored["pack"] == "Mu Test" and stored["status"] == "ok"


# --------------------------------------------------------------------------- R1
class _Dead:
    pid = 11

    def poll(self):
        return 1


class _Alive:
    pid = 12

    def poll(self):
        return None


def _driver_with_job(tmp_path, process, hold_left):
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver._jobs = [{"process": process, "cue": "kickoff",
                     "ready": {"hold_until_epoch": time.time() + hold_left}}]
    return driver


def test_record_refuses_a_job_that_died_during_the_preroll(tmp_path, monkeypatch):
    from tools.madden_pilot import win
    calls = []
    monkeypatch.setattr(win, "call", lambda *a, **k: calls.append(a) or {"ok": True})
    driver = _driver_with_job(tmp_path, _Dead(), 60)
    with pytest.raises(DriverError) as caught:
        driver.record(tmp_path / "record", 1, 1, 320)
    assert "exited" in str(caught.value)
    assert calls == []


def test_record_refuses_a_hold_that_ended_during_the_preroll(tmp_path, monkeypatch):
    from tools.madden_pilot import win
    calls = []
    monkeypatch.setattr(win, "call", lambda *a, **k: calls.append(a) or {"ok": True})
    driver = _driver_with_job(tmp_path, _Alive(), 1.0)
    with pytest.raises(DriverError) as caught:
        driver.record(tmp_path / "record", 5, 1, 320)
    assert "hold has" in str(caught.value)
    assert calls == []


def test_record_with_a_live_job_and_a_covering_hold_records(tmp_path, monkeypatch):
    from tools.madden_pilot import win
    calls = []
    monkeypatch.setattr(win, "call", lambda *a, **k: calls.append(a) or {"ok": True})
    driver = _driver_with_job(tmp_path, _Alive(), 60)
    assert driver.record(tmp_path / "record", 1, 1, 320) == []
    assert calls and calls[0][0][0] == "record"


def test_record_with_no_watch_game_job_is_an_explicit_refusal(tmp_path, monkeypatch):
    from tools.madden_pilot import win
    calls = []
    monkeypatch.setattr(win, "call", lambda *a, **k: calls.append(a) or {"ok": True})
    with pytest.raises(DriverError) as caught:
        runner.PilotDriver(run_dir=tmp_path).record(tmp_path / "record", 1, 1, 320)
    assert "no accepted watch-game job" in str(caught.value)
    assert calls == []


# --------------------------------------------------------------------------- R2
class _Exited:
    pid = 13

    def poll(self):
        return 0


@pytest.mark.parametrize("result,word", [
    ({}, "no result.json"),
    ({"quit": True, "exit_code": 1, "job_status": "ok"}, "exited 1"),
    ({"quit": True, "exit_code": 0, "job_status": "Stuck"}, "reported Stuck"),
    ({"quit": False, "exit_code": 0, "job_status": "ok", "why": "quit failed"},
     "did not confirm"),
])
def test_failed_previous_cleanup_stops_the_next_job_before_popen(tmp_path, monkeypatch,
                                                                 result, word):
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver._jobs = [{"process": _Exited(), "cue": "kickoff", "dir": tmp_path}]
    monkeypatch.setattr(driver, "_collect_job", lambda job: result)
    started = []
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: started.append(a) or (_ for _ in ()).throw(
                            AssertionError("no new job")))
    with pytest.raises(DriverError) as caught:
        driver.wait_for("kickoff", timeout_s=1, hold_s=10)
    assert word in str(caught.value)
    assert started == []


def test_an_exited_clean_job_is_collected_once_and_permits_the_next(tmp_path, monkeypatch):
    driver = runner.PilotDriver(run_dir=tmp_path)
    driver.start("CAREER-TUNE-CHKA")
    driver._jobs = [{"process": _Exited(), "cue": "kickoff", "dir": tmp_path}]
    collected = []
    monkeypatch.setattr(driver, "_collect_job",
                        lambda job: collected.append(job) or {"quit": True, "exit_code": 0,
                                                              "job_status": "ok"})
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop here")))
    with pytest.raises(RuntimeError, match="stop here"):
        driver.wait_for("kickoff", timeout_s=1, hold_s=10)
    assert len(collected) == 1                       # collected once, before the next job
    driver._collect_running()
    assert len(collected) == 1                       # never twice


def test_a_stopped_run_keeps_the_pixel_rows_it_already_scored(tmp_path):
    class StopsOnSecond(OneFrameDriver):
        def wait_for(self, cue, timeout_s=180.0, hold_s=None, record_s=0):
            if cue == "halftime_start":
                from tools.mod_check.runner import CueMissed
                raise CueMissed(cue, "the job failed its own quit")

    root = tmp_path / "data"
    folder = root / "demo"
    folder.mkdir(parents=True)
    write_checks(folder)
    write_plan(folder, [
        {"name": "game_start", "after": "kickoff", "record_s": 1, "fps": 1, "width": 640,
         "checks": ["asset"]},
        {"name": "halftime", "after": "halftime_start", "record_s": 1, "fps": 1, "width": 640,
         "checks": ["asset"]}])
    result = runner.run("CAREER-TUNE-X", "demo", data_dir=root, driver=StopsOnSecond(),
                        watcher=lambda: False, clone=lambda s, p: "CAREER-TUNE-CHK1",
                        describe=lambda png: "ok", notice=lambda line: None,
                        sleep=lambda s: None,
                        launch=lambda pack: {"status": "ok", "pack": pack,
                                             "already_running": False})
    assert result["status"] == "stopped"
    assert [row["name"] for row in result["segments"]] == ["game_start"]
    assert result["segments"][0]["checks"]["asset"]["status"] == "pass"


# --------------------------------------------------------------------------- R3
def _mkdir_run(root, name, report_mod=None):
    path = root / "runs" / name
    path.mkdir(parents=True)
    if report_mod is not None:
        (path / "report.json").write_text(json.dumps({"mod": report_mod}), encoding="utf-8")
    return path


def test_newest_run_orders_same_second_suffixes_numerically(tmp_path):
    root = tmp_path / "data"
    base = _mkdir_run(root, "2026-11-12_170000_demo")
    second = _mkdir_run(root, "2026-11-12_170000_demo-2")
    tenth = _mkdir_run(root, "2026-11-12_170000_demo-10")
    assert runner._newest_run(root, "demo") == tenth
    next_second = _mkdir_run(root, "2026-11-12_170001_demo")
    assert runner._newest_run(root, "demo") == next_second
    assert base.exists() and second.exists()


def test_newest_run_ignores_another_mod_with_shared_words_and_a_renamed_report(tmp_path):
    root = tmp_path / "data"
    _mkdir_run(root, "2026-11-12_170000_demo")
    _mkdir_run(root, "2026-11-12_170001_my_demo")        # not this mod
    _mkdir_run(root, "2026-11-12_170002_demo-x")         # not the -<number> allocation
    renamed = _mkdir_run(root, "2026-11-12_170003_demo", report_mod="something-else")
    assert runner._newest_run(root, "demo").name == "2026-11-12_170000_demo"
    assert renamed.exists()


def test_capture_uses_the_newest_same_second_run_not_an_older_one(tmp_path):
    root = tmp_path / "data"
    folder = root / "demo"
    folder.mkdir(parents=True)
    write_checks(folder)
    write_plan(folder, [{"name": "game_start", "after": "kickoff", "record_s": 1,
                         "fps": 1, "width": 640, "checks": ["asset"]}])
    older = root / "runs" / "2026-11-12_170000_demo" / "segments/game_start/frames"
    newer = root / "runs" / "2026-11-12_170000_demo-2" / "segments/game_start/frames"
    older.mkdir(parents=True)
    Image.new("RGB", (64, 64), (47, 47, 47)).save(older / "frame_0000.png")
    Image.new("RGB", (64, 64), (47, 47, 47)).save(older / "frame_0001.png")
    newer.mkdir(parents=True)
    Image.new("RGB", (64, 64), (255, 0, 0)).save(newer / "frame_0000.png")
    Image.new("RGB", (64, 64), (0, 0, 255)).save(newer / "frame_0001.png")
    result = capture("demo", "game_start", "0:00", "0:01", yes=True, data_dir=root)
    assert result["saved"] is True
    with Image.open(folder / "refs/red_pass.png") as opened:
        assert opened.convert("RGB").getpixel((32, 32)) == (255, 0, 0)
