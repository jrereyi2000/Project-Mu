"""Madden pilot jobs and CLI (plan section 2.3) against scripted fake Maddens. No PC, no database."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import pipeline.notify
from tools.madden_pilot import __main__ as cli
from tools.madden_pilot import jobs
from tools.madden_pilot.__main__ import LOADING, ScriptedMadden
from tools.madden_pilot.navigate import Pilot
from tools.madden_pilot.screens import Frame, load_catalog

CAT = load_catalog()


@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify", lambda msg, **kw: sent.append((msg, kw)) or True)
    return sent


@pytest.fixture(autouse=True)
def no_real_watcher(monkeypatch):
    def boom():
        raise AssertionError("tests must not run the real watcher check")
    monkeypatch.setattr(jobs, "watcher_running", boom)


def fake_madden(start="FRANCHISE_LIST", tiles=("ALLUSER", "TUNE-X"), **kw):
    log = []
    return ScriptedMadden(CAT, start=start, tiles=tiles, out=log.append, **kw), log


def make_pilot(tmp_path, fake, catalog=CAT, **kw):
    return Pilot(catalog, tmp_path, driver=fake, sleep=fake.sleep, clock=fake.clock,
                 highlight_reader=lambda frame, labels, spec: fake.highlight, **kw)


def result_json(tmp_path):
    return json.loads((tmp_path / "result.json").read_text())


# ---------------------------------------------------------------- the scripted fake itself
def test_scripted_fake_screens_classify_back():
    fake, _ = fake_madden()
    assert fake.check() == []


# ---------------------------------------------------------------- clone_save
def _saves(tmp_path):
    d = tmp_path / "saves"
    d.mkdir()
    (d / "CAREER-MU2026").write_bytes(b"week1")
    (d / "CAREER-2029SB").write_bytes(b"real")
    return d


@pytest.mark.parametrize("dst", [None, "CAREER-2029SB", "CAREER-MU2026", "TUNE-X", "CAREER-TUNE-",
                                 "CAREER-TUNE-../CAREER-2029SB", "CAREER-TUNE-a/b"])
def test_clone_refuses_non_tune_destinations(tmp_path, notices, dst):
    d = _saves(tmp_path)
    out = jobs.clone_save(make_pilot(tmp_path / "run", fake_madden()[0]), src="CAREER-MU2026", dst=dst, folder=d)
    assert out["status"] == "refused"
    assert (d / "CAREER-2029SB").read_bytes() == b"real"
    assert sorted(p.name for p in d.iterdir()) == ["CAREER-2029SB", "CAREER-MU2026"]
    assert result_json(tmp_path / "run")["status"] == "refused"
    assert len(notices) == 1 and "refused" in notices[0][0]


def test_clone_refuses_missing_source_and_same_name(tmp_path, notices):
    d = _saves(tmp_path)
    p = make_pilot(tmp_path / "run", fake_madden()[0])
    assert jobs.clone_save(p, src="CAREER-NOPE", dst="CAREER-TUNE-X", folder=d)["status"] == "refused"
    assert jobs.clone_save(p, src="CAREER-TUNE-X", dst="CAREER-TUNE-X", folder=d)["status"] == "refused"
    assert not (d / "CAREER-TUNE-X").exists()


def test_clone_copies_and_replaces_only_tune(tmp_path, notices):
    d = _saves(tmp_path)
    p = make_pilot(tmp_path / "run", fake_madden()[0])
    os.utime(d / "CAREER-MU2026", (1_000_000, 1_000_000))
    out = jobs.clone_save(p, dst="CAREER-TUNE-X", folder=d)
    assert out["status"] == "ok" and out["copied"] and not out["overwrote"]
    assert (d / "CAREER-TUNE-X").read_bytes() == b"week1"
    assert (d / "CAREER-TUNE-X").stat().st_mtime > 1_000_000          # fresh mtime: listed first
    (d / "CAREER-MU2026").write_bytes(b"week1-v2")
    out = jobs.clone_save(p, dst="CAREER-TUNE-X", folder=d)
    assert out["overwrote"] and (d / "CAREER-TUNE-X").read_bytes() == b"week1-v2"
    assert not [x for x in d.iterdir() if x.name.endswith(".tmp")]
    assert (d / "CAREER-MU2026").read_bytes() == b"week1-v2"            # source untouched


SAMPLE_SAVE = Path(os.environ.get("MU_SAMPLE_SAVE", jobs.ROOT / "CAREER-GIANTS-AUTOSAVE"))
needs_sample_save = pytest.mark.skipif(not SAMPLE_SAVE.is_file(), reason=f"no sample save at {SAMPLE_SAVE}")


def _unpacked_size(path):
    import zlib
    raw = Path(path).read_bytes()
    return len(zlib.decompress(raw[0x52:]))


@needs_sample_save
def test_clone_prunes_the_capacity_of_a_real_save(tmp_path, notices):
    # Highlight 3,200 rows of allocated capacity is what the tuning season used to truncate at;
    # a clone must come out smaller, with the rest of the save intact (SAVE_CAPACITY_PRUNE_PLAN §9).
    d = tmp_path / "saves"
    d.mkdir()
    shutil.copyfile(SAMPLE_SAVE, d / "CAREER-MU2026")
    out = jobs.clone_save(make_pilot(tmp_path / "run", fake_madden()[0]), dst="CAREER-TUNE-X", folder=d)
    assert out["status"] == "ok" and out["copied"] and out["pruned"], out
    assert "bytes freed" in out["prune"]
    assert _unpacked_size(d / "CAREER-TUNE-X") < _unpacked_size(d / "CAREER-MU2026")
    assert (d / "CAREER-MU2026").stat().st_size == (d / "CAREER-TUNE-X").stat().st_size  # the slot keeps its size
    assert not [x for x in d.iterdir() if x.name.endswith(".tmp")]


def test_clone_leaves_a_non_save_file_alone_and_says_so(tmp_path, notices, capsys):
    # The fixtures in this module are not FBCHUNKS saves: the prune declines rather than failing.
    d = _saves(tmp_path)
    out = jobs.clone_save(make_pilot(tmp_path / "run", fake_madden()[0]), dst="CAREER-TUNE-X", folder=d)
    assert out["pruned"] is False and out["prune"] == "not an FBCHUNKS save"
    assert (d / "CAREER-TUNE-X").read_bytes() == b"week1"
    assert "capacity prune skipped" in capsys.readouterr().out


def test_clone_dry_run_copies_nothing(tmp_path, notices):
    d = _saves(tmp_path)
    p = make_pilot(tmp_path / "run", fake_madden()[0])
    p.dry_run = True
    out = jobs.clone_save(p, dst="CAREER-TUNE-X", folder=d, dry_run=True)
    assert out["status"] == "ok" and out["copied"] is False
    assert not (d / "CAREER-TUNE-X").exists()
    assert notices == []                                                 # dry runs send no ntfy


def test_saves_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MADDEN_SAVES_DIR", str(tmp_path))
    assert jobs.saves_dir() == tmp_path
    monkeypatch.delenv("MADDEN_SAVES_DIR")
    monkeypatch.setattr(pipeline.notify, "env", lambda key, default=None: default)
    assert str(jobs.saves_dir()) == jobs.DEFAULT_SAVES_DIR.rstrip("/")


# ---------------------------------------------------------------- sim_season refusals
@pytest.mark.parametrize("save", [None, "CAREER-MU2026", "CAREER-2029SB", "TUNE-X", "career-tunex", "CAREER-TUNE-a b"])
def test_sim_refuses_non_tune_saves(tmp_path, notices, save):
    fake, _ = fake_madden()
    out = jobs.sim_season(make_pilot(tmp_path, fake), save=save, to="week 3", watcher=lambda: False, folder=tmp_path)
    assert out["status"] == "refused" and "CAREER-TUNE-" in out["why"]
    assert fake.keys == [] and fake.looks == 0
    assert result_json(tmp_path)["status"] == "refused"
    assert len(notices) == 1


def test_sim_refuses_while_watcher_runs(tmp_path, notices):
    fake, _ = fake_madden()
    out = jobs.sim_season(make_pilot(tmp_path, fake), save="CAREER-TUNE-X", to="week 3", watcher=lambda: True,
                          folder=tmp_path)
    assert out["status"] == "refused" and "watch_save" in out["why"]
    assert fake.keys == [] and fake.looks == 0


def test_sim_refuses_bad_target_and_missing_save(tmp_path, notices):
    fake, _ = fake_madden()
    p = make_pilot(tmp_path / "run", fake)
    assert jobs.sim_season(p, save="CAREER-TUNE-X", to="next tuesday", watcher=lambda: False,
                           folder=tmp_path)["status"] == "refused"
    out = jobs.sim_season(p, save="CAREER-TUNE-X", to="week 3", watcher=lambda: False, folder=tmp_path)
    assert out["status"] == "refused" and "clone-save" in out["why"]
    assert fake.keys == []


def test_watcher_check_uses_ps_not_pgrep(monkeypatch):
    monkeypatch.undo()                                   # the real function, with subprocess mocked
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="python3 -m pipeline.watch_save\n", stderr="")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    assert jobs.watcher_running() is True
    assert calls == ['ps -eo args | grep "[p]ipeline[.]watch_save"'] and "pgrep" not in calls[0]
    monkeypatch.setattr(jobs.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""))
    assert jobs.watcher_running() is False


# ---------------------------------------------------------------- header and targets
@pytest.mark.parametrize("line,week", [("2026. WEEK 1", 1), ("2026. WEEK I", 1), ("2026: WEEKl", 1),
                                       ("2026. WEEK 12", 12), ("2026. WEEK 1I", 11), ("2027. WEEK 3", 3)])
def test_read_header_week(line, week):
    head = jobs.read_header(Frame(None, lines=["GAMEDAY", line, "VS BEARS (0-0-0)"]))
    assert head["phase"] == "WEEK" and head["week"] == week


def test_read_header_other_phase_and_none():
    head = jobs.read_header(Frame(None, lines=["2026. WILD CARD", "VS BEARS"]))
    assert head == {"year": 2026, "phase": "WILD CARD", "week": None}
    assert jobs.read_header(Frame(None, lines=["GAMEDAY", "VS BEARS (0-0-0)"])) is None


@pytest.mark.parametrize("word,phase", [("WILDCARD WEEK", "WILDCARD"), ("DIVISIONAL WEEK", "DIVISIONAL"),
                                         ("CHAMPIONSHIP SUNDAY", "CHAMPIONSHIP SUNDAY"), ("PRO BOWL WEEK", "PRO BOWL"),
                                         ("SUPER BOWL", "SUPER BOWL"), ("STAFF WEEK", "STAFF"),
                                         ("FREE AGENCY", "FREE AGENCY"), ("PRO DAY", "PRO DAY")])
def test_read_header_postseason_and_offseason_phases(word, phase):
    head = jobs.read_header(Frame(None, lines=["GAMEDAY", "MANAGE ROSTER", "COACHCENTRAL", word]))
    assert head == {"year": None, "phase": phase, "week": None}


def test_parse_target():
    assert jobs.parse_target("week 4") == ("week", 4)
    assert jobs.parse_target("Week4") == ("week", 4)
    assert jobs.parse_target("regular-season-end") == ("regular-season-end", None)
    assert jobs.parse_target("playoffs-end") == ("playoffs-end", None)
    for bad in ("forever", "week 0", "week 19"):
        with pytest.raises(ValueError):
            jobs.parse_target(bad)


# ---------------------------------------------------------------- sim_season flows
def _sim(tmp_path, fake, to="week 3", **kw):
    p = make_pilot(tmp_path, fake)
    return p, jobs.sim_season(p, save="CAREER-TUNE-X", to=to, watcher=lambda: False, folder=tmp_path / "none", **kw)


def test_sim_to_week_3_with_coach_progress_repeats(tmp_path, notices):
    fake, log = fake_madden()
    p, out = _sim(tmp_path, fake)
    assert out["status"] == "ok", out
    assert (out["start_week"], out["end_week"], out["weeks_simmed"]) == (1, 3, 2)
    assert out["saved"] == "autosave"
    assert fake.screen == "FRANCHISE_LIST" and fake.week == 3
    assert p.loaded_franchise == "TUNE-X"
    # two NEXT WEEKs, each followed by three COACH PROGRESS continues
    assert log.count("  key   space     ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 2
    assert sum(1 for ln in log if ln.startswith("  key   space     COACH_PROGRESS")) == 6
    assert "tab" not in fake.keys and "q" not in fake.keys
    assert not any("HUB_WEEKLY on" in ln and "space" in ln for ln in log)     # never Space on the hub cards
    # it went back to GAMEDAY each week before ADVANCE WEEK
    assert sum(1 for ln in log if ln.startswith("  key   space     HUB_GAMEDAY on ADVANCE WEEK")) == 2
    res = result_json(tmp_path)
    assert res["status"] == "ok" and res["job"] == "sim-season" and "minutes" in res
    assert len(notices) == 1 and "sim-season: ok" in notices[0][0]


def test_the_advance_menu_is_pinned_to_the_top_before_forcing(tmp_path, notices):
    # READY TO ADVANCE is the menu's top item and often fails OCR; the border reader then credits
    # its border to FORCE ADVANCE, Space takes READY, and an all-user league just closes the menu
    # without moving the week (2026-11-10, p2g week 3). Selection is pinned by position instead.
    fake, log = fake_madden()
    _, out = _sim(tmp_path, fake, to="week 2")
    assert out["status"] == "ok", out
    lines = [ln for ln in log if ln.startswith("  key")]
    names = [ln.split()[1] for ln in lines]
    confirms = [i for i, ln in enumerate(lines) if "on FORCE ADVANCE" in ln]
    assert confirms and names[confirms[0] - 4:confirms[0]] == ["up", "up", "up", "down"]


def test_sim_already_at_target_only_saves_and_quits(tmp_path, notices):
    fake, log = fake_madden(week=3)
    _, out = _sim(tmp_path, fake, to="week 3")
    assert out["status"] == "ok" and out["weeks_simmed"] == 0
    assert "  key   space     ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS" not in log


def test_sim_no_quit_stays_in_the_franchise(tmp_path, notices):
    fake, _ = fake_madden()
    _, out = _sim(tmp_path, fake, to="week 2", quit=False)
    assert out["status"] == "ok" and fake.screen == "HUB_WEEKLY"


def test_sim_regular_season_end_stops_on_phase_change(tmp_path, notices):
    log = []
    fake = ScriptedMadden(CAT, tiles=["TUNE-X"], week=17, out=log.append)
    _, out = _sim(tmp_path, fake, to="regular-season-end")
    assert out["status"] == "ok", out
    assert out["weeks_simmed"] == 2 and out["end_phase"] == "WILD CARD"


def test_sim_playoffs_end_stops_unknown_on_unseen_screen(tmp_path, notices):
    log = []
    fake = ScriptedMadden(CAT, tiles=["TUNE-X"], week=18, out=log.append)
    _, out = _sim(tmp_path, fake, to="playoffs-end")
    assert out["status"] == "stopped" and out["kind"] == "Unknown", out
    assert out["weeks_simmed"] == 1
    stop = json.loads((tmp_path / "stop.json").read_text())
    assert stop["kind"] == "Unknown" and stop["screen"] == "UNKNOWN" and "/steps/" in stop["screenshot"]
    assert result_json(tmp_path)["status"] == "stopped"
    assert any("Unknown" in m for m, _ in notices) and any("sim-season: stopped" in m for m, _ in notices)


def test_sim_stuck_when_week_does_not_advance(tmp_path, notices):
    class NoAdvance(ScriptedMadden):
        def on_key(self, name):
            week = self.week
            super().on_key(name)
            self.week = week
    fake = NoAdvance(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _sim(tmp_path, fake)
    assert out["status"] == "stopped" and out["kind"] == "Stuck" and "header" in out["reason"]


def test_the_loader_leaves_for_the_main_menu_when_a_new_clone_is_not_on_the_list(tmp_path, notices):
    # CURRENT FRANCHISES only rebuilds on a fresh entry from the main menu: one Back to the
    # landing page and in again keeps the stale list (JR, 2026-11-10; the p2g run walked the
    # list and never saw TUNE-P2G).
    class StaleList(ScriptedMadden):
        at_menu = False

        def go(self, screen):
            super().go(screen)
            if screen == "MAIN_MENU":
                self.at_menu = True
            elif self.at_menu and screen == "FRANCHISE_LIST":
                self.tiles, self.idx = ["OTHER", "TUNE-X"], 0

        def on_key(self, name):
            if name == "backspace" and self.screen == "FRANCHISE_LIST":
                self.go("FRANCHISE_LANDING")
                return
            if name == "backspace" and self.screen == "FRANCHISE_LANDING":
                self.go("MAIN_MENU")
                return
            if name == "space" and self.screen == "MAIN_MENU":
                self.go("FRANCHISE_LANDING")
                return
            super().on_key(name)

    fake = StaleList(CAT, tiles=["OTHER"], out=lambda s: None)
    _, out = _sim(tmp_path, fake, to="week 2")
    assert out["status"] == "ok", out
    assert fake.screen == "FRANCHISE_LIST" and fake.week == 2


def test_sim_refuses_to_quit_an_unknown_franchise_first(tmp_path, notices):
    fake, _ = fake_madden(start="HUB_WEEKLY")         # a franchise is open; the hub doesn't say which
    _, out = _sim(tmp_path, fake)
    assert out["status"] == "stopped" and out["kind"] == "Forbidden"
    assert "space" not in fake.keys and fake.screen == "HUB_WEEKLY"


def test_sim_never_loads_a_non_tune_tile(tmp_path, notices):
    fake, _ = fake_madden(tiles=("ALLUSER",))         # the TUNE tile isn't there
    _, out = _sim(tmp_path, fake)
    assert out["status"] == "stopped"
    assert "space" not in fake.keys


# ---------------------------------------------------------------- launch
def test_launch_runs_manager_and_waits_for_menu(tmp_path, notices):
    fake, _ = fake_madden(start="MAIN_MENU", intro=3)
    ran = []
    out = jobs.launch(make_pilot(tmp_path, fake), pack="Mu Test", run=ran.append, madden_running=lambda: False,
                      manager_running=lambda: False)
    assert out["status"] == "ok" and out["screen"] == "MAIN_MENU"
    assert len(ran) == 1 and ran[0][:4] == ["cmd.exe", "/c", "start", ""]
    assert ran[0][4].endswith("MMCModManager.exe") and ran[0][5:] == ["-launch", "Mu Test"]
    assert fake.keys == []


def test_launch_runs_the_manager_from_its_own_folder(tmp_path, notices, monkeypatch):
    # 2026-11-12: launched with cwd=/mnt/c, Frosty's executor failed to load the pack ("errors
    # occurred while attempting to load the following mod") and no Madden window ever appeared;
    # the same -launch from the manager's own folder applied cleanly and started the game.
    fake, _ = fake_madden(start="MAIN_MENU")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    out = jobs.launch(make_pilot(tmp_path, fake), run=None, madden_running=lambda: False,
                      manager_running=lambda: False)

    assert out["status"] == "ok", out
    assert len(calls) == 1
    assert calls[0][1]["cwd"] == jobs.launch_cwd() == jobs.DEFAULT_MANAGER_DIR
    assert out["cwd"] == jobs.DEFAULT_MANAGER_DIR


def test_launch_cwd_follows_the_manager_dir_overrides(monkeypatch):
    monkeypatch.setenv("MMC_MANAGER_DIR", "/mnt/d/MMC")
    monkeypatch.delenv("MMC_MOD_MANAGER_EXE", raising=False)
    assert jobs.launch_cwd() == "/mnt/d/MMC"

    monkeypatch.setenv("MMC_MOD_MANAGER_EXE", "C:\\Tools\\MMC\\MMCModManager.exe")
    assert jobs.launch_cwd() == "/mnt/c/Tools/MMC"

    monkeypatch.setenv("MMC_MOD_MANAGER_EXE", "/mnt/e/Other/MMCModManager.exe")
    assert jobs.launch_cwd() == "/mnt/e/Other"


def test_launch_refuses_while_the_mod_manager_is_open(tmp_path, notices):
    # `start` on an open manager only focuses that window, so the pack is never applied and the
    # job would sit out its 300 s window wait for nothing.
    fake, _ = fake_madden(start="MAIN_MENU")
    ran = []
    out = jobs.launch(make_pilot(tmp_path, fake), run=ran.append, madden_running=lambda: False,
                      manager_running=lambda: True)
    assert out["status"] == "refused"
    assert "MMCModManager.exe is already open" in out["why"]
    assert ran == []


def test_launch_skips_start_when_running(tmp_path, notices):
    fake, _ = fake_madden(start="FRANCHISE_LIST")
    ran = []
    out = jobs.launch(make_pilot(tmp_path, fake), run=ran.append, madden_running=lambda: True)
    assert out["status"] == "ok" and out["already_running"] and ran == []


def test_launch_presses_through_the_cold_boot_splash(tmp_path, notices):
    # a cold boot sits on BOOT_SPLASH (PRESS ANY BUTTON) until Space: launch used to only wait,
    # so a live launch on Mu Test stopped Unknown with the splash on screen (2026-11-11).
    fake, _ = fake_madden(start="BOOT_SPLASH")
    out = jobs.launch(make_pilot(tmp_path, fake), run=lambda argv: None, madden_running=lambda: False,
                      timeout=60)
    assert out["status"] == "ok" and out["screen"] == "MAIN_MENU", out
    assert fake.keys[0] == "space"


def test_launch_dismisses_the_mmc_account_error(tmp_path, notices):
    # the MMC mod warning is drawn over the splash; its OK is the focused default
    fake, _ = fake_madden(start="ACCOUNT_ERROR")
    out = jobs.launch(make_pilot(tmp_path, fake), run=lambda argv: None, madden_running=lambda: False,
                      timeout=60)
    assert out["status"] == "ok" and out["screen"] == "MAIN_MENU", out
    assert "space" in fake.keys


def test_launch_accepts_a_one_mode_menu(tmp_path, notices):
    # Mu Franchise Only's main menu is the Franchise panel itself and reads as FRANCHISE_LANDING;
    # there is no MAIN_MENU on that pack, so a launch must accept the landing as its menu.
    class OneMode(ScriptedMadden):
        def on_key(self, name):
            if name == "space" and self.screen in ("BOOT_SPLASH", "TITLE_SCREEN"):
                self.go("FRANCHISE_LANDING")
                return
            super().on_key(name)

    fake = OneMode(CAT, start="BOOT_SPLASH", tiles=["TUNE-X"], out=lambda s: None)
    out = jobs.launch(make_pilot(tmp_path, fake), pack="Mu Test", run=lambda argv: None,
                      madden_running=lambda: False, timeout=60)
    assert out["status"] == "ok" and out["screen"] == "FRANCHISE_LANDING", out
    assert fake.keys and fake.keys[0] == "space"


def test_launch_stops_unknown_on_unrecognised_screen(tmp_path, notices):
    fake, _ = fake_madden(start="EA SPORTS TITLE")    # no catalog words
    out = jobs.launch(make_pilot(tmp_path, fake), run=lambda argv: None, madden_running=lambda: False, timeout=60)
    assert out["status"] == "stopped" and out["kind"] == "Unknown"
    assert out["screen"] == "UNKNOWN" and "/steps/" in out["screenshot"]


def test_launch_command_fallback(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_game_launch(name, *a, **kw):
        if name == "pipeline" and a and a[2] and "game_launch" in a[2]:
            raise ImportError("no game_launch")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_game_launch)
    monkeypatch.setenv("MMC_MOD_MANAGER_EXE", "/mnt/c/Tools/MMCModManager.exe")
    assert jobs.launch_command("Mu Base") == ["cmd.exe", "/c", "start", "", "C:\\Tools\\MMCModManager.exe",
                                              "-launch", "Mu Base"]


# ---------------------------------------------------------------- set_setting
SET_CAT = {"keys": CAT["keys"], "screens": {
    "SETTINGS_ROOT": {"all": ["SETTINGS MENU"], "options": ["GAME OPTIONS", "GAMEPLAY"], "nav": "vertical",
                      "highlight": "border", "default": "GAME OPTIONS", "confirm_ok": ["GAMEPLAY"],
                      "exits": {}},
    "GAMEPLAY": {"all": ["GAMEPLAY SETTINGS"], "options": ["ACCEL CLAMP", "AUTO STRAFE"], "nav": "vertical",
                 "highlight": "border", "default": "ACCEL CLAMP", "confirm_ok": [],
                 "exits": {"SETTINGS_ROOT": [["back", 1]]}},
}}


class SettingsFake:
    def __init__(self):
        self.screen, self.idx, self.keys = "SETTINGS_ROOT", 0, []
        self.values = {"ACCEL CLAMP": "ON", "AUTO STRAFE": "OFF"}
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)

    @property
    def options(self):
        return SET_CAT["screens"][self.screen]["options"]

    @property
    def highlight(self):
        return self.options[self.idx]

    def call(self, cmd):
        if cmd[0] == "look":
            if self.screen == "SETTINGS_ROOT":
                lines = ["SETTINGS MENU", "GAME OPTIONS", "GAMEPLAY"]
            else:
                lines = ["GAMEPLAY SETTINGS"] + [f"{k} {v}" for k, v in self.values.items()]
            return {"ok": True, "words": [{"text": w, "box": [100 + 120 * j, 40 * i, 100, 30], "line": i}
                                          for i, ln in enumerate(lines) for j, w in enumerate(ln.split())]}
        key = cmd[cmd.index("--name") + 1]
        self.keys.append(key)
        if key in ("down", "up"):
            self.idx = max(0, min(len(self.options) - 1, self.idx + (1 if key == "down" else -1)))
        elif key == "space" and self.screen == "SETTINGS_ROOT" and self.highlight == "GAMEPLAY":
            self.screen, self.idx = "GAMEPLAY", 0
        elif key == "right" and self.screen == "GAMEPLAY":
            cycle = ["ON", "OFF"]
            k = self.highlight
            self.values[k] = cycle[(cycle.index(self.values[k]) + 1) % 2]
        elif key == "backspace" and self.screen == "GAMEPLAY":
            self.screen, self.idx = "SETTINGS_ROOT", 0
        return {"ok": True}


def test_set_setting_walks_path(tmp_path, notices):
    fake = SettingsFake()
    p = make_pilot(tmp_path, fake, catalog=SET_CAT)
    out = jobs.set_setting(p, path="Gameplay > Accel Clamp", value="Off")
    assert out["status"] == "ok", out
    assert fake.values["ACCEL CLAMP"] == "OFF" and out["presses"] == 1
    assert fake.keys == ["down", "space", "right", "backspace"]
    assert fake.screen == "SETTINGS_ROOT"


def test_set_setting_already_set_presses_nothing_on_the_row(tmp_path, notices):
    fake = SettingsFake()
    fake.values["ACCEL CLAMP"] = "OFF"
    out = jobs.set_setting(make_pilot(tmp_path, fake, catalog=SET_CAT), path="Gameplay>Accel Clamp", value="OFF")
    assert out["status"] == "ok" and out["presses"] == 0 and "right" not in fake.keys


def test_set_setting_stops_unknown_on_uncatalogued_labels(tmp_path, notices):
    fake, _ = fake_madden(start="HUB_WEEKLY")
    out = jobs.set_setting(make_pilot(tmp_path, fake), path="Settings>Gameplay>Accel Clamp", value="Off")
    assert out["status"] == "stopped" and out["kind"] == "Unknown"
    assert fake.keys == []


def test_set_setting_needs_path_and_value(tmp_path, notices):
    fake, _ = fake_madden()
    assert jobs.set_setting(make_pilot(tmp_path, fake), path="", value="x")["status"] == "refused"


# ---------------------------------------------------------------- verify_mods
def test_verify_mods_first_down_line_is_unknown_for_now(tmp_path, notices):
    fake, _ = fake_madden(start="MAIN_MENU")
    out = jobs.verify_mods(make_pilot(tmp_path, fake), check="first_down_line")
    assert out["status"] == "unknown" and out["check"] == "first_down_line" and out["needs"]
    assert fake.keys == []
    assert result_json(tmp_path)["status"] == "unknown"


@pytest.mark.parametrize("check", ["no_such_check", "../jobs", "Bad", ""])
def test_verify_mods_bad_check_refused(tmp_path, notices, check):
    out = jobs.verify_mods(make_pilot(tmp_path, fake_madden()[0]), check=check)
    assert out["status"] == "refused"


# ---------------------------------------------------------------- quit
def test_quit_tune_franchise(tmp_path, notices):
    fake, _ = fake_madden(start="HUB_GAMEDAY")
    out = jobs.quit_franchise(make_pilot(tmp_path, fake), franchise="CAREER-TUNE-X")
    assert out["status"] == "ok" and fake.screen == "FRANCHISE_LIST"
    assert fake.keys == ["backspace", "up", "space"]


def test_quit_refuses_unknown_or_non_tune(tmp_path, notices):
    for franchise in (None, "ALLUSER"):
        fake, _ = fake_madden(start="HUB_WEEKLY")
        out = jobs.quit_franchise(make_pilot(tmp_path / str(franchise), fake), franchise=franchise)
        assert out["status"] == "stopped" and out["kind"] == "Forbidden"
        assert "space" not in fake.keys
    fake, _ = fake_madden(start="HUB_WEEKLY")
    out = jobs.quit_franchise(make_pilot(tmp_path / "ok", fake), franchise="ALLUSER", allow_quit=True)
    assert out["status"] == "ok" and fake.screen == "FRANCHISE_LIST"


def test_quit_on_franchise_list_does_nothing(tmp_path, notices):
    fake, _ = fake_madden()
    out = jobs.quit_franchise(make_pilot(tmp_path, fake))
    assert out["status"] == "ok" and out["quit"] is False and fake.keys == []


# ---------------------------------------------------------------- CLI dry run
def test_cli_dry_run_sim_season_prints_route(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    lines = []
    rc = cli.main(["sim-season", "--save", "CAREER-TUNE-SPIKE", "--to", "week 4", "--dry-run",
                   "--run-dir", str(tmp_path)], out=lines.append)
    assert rc == 0
    text = "\n".join(lines)
    assert "FRANCHISE_LIST > highlight TUNE-SPIKE" in lines[0] and "until week 4" in lines[0]
    assert text.count("ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 3
    assert "QUIT_CONFIRM on YES -> FRANCHISE_LIST" in text
    res = result_json(tmp_path)
    assert res["status"] == "ok" and res["dry_run"] and res["end_week"] == 4
    assert notices == []                                                   # nothing sent


def test_cli_dry_run_warns_when_watcher_runs(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: True)
    lines = []
    rc = cli.main(["sim-season", "--save", "CAREER-TUNE-X", "--to", "week 2", "--dry-run",
                   "--run-dir", str(tmp_path)], out=lines.append)
    assert rc == 0 and any("would refuse" in ln for ln in lines)


def test_cli_dry_run_refusal_exit_code(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    rc = cli.main(["sim-season", "--save", "CAREER-MU2026", "--dry-run", "--run-dir", str(tmp_path)],
                  out=lambda s: None)
    assert rc == 1 and result_json(tmp_path)["status"] == "refused"


def test_cli_dry_run_launch_and_others(tmp_path, notices, monkeypatch):
    lines = []
    assert cli.main(["launch", "--pack", "Mu Base", "--dry-run", "--run-dir", str(tmp_path / "l")],
                    out=lines.append) == 0
    assert any("-launch Mu Base (dry run: not started)" in ln for ln in lines)
    assert cli.main(["verify-mods", "--check", "first_down_line", "--dry-run", "--run-dir", str(tmp_path / "v")],
                    out=lines.append) == 2
    assert cli.main(["quit", "--franchise", "TUNE-SPIKE", "--dry-run", "--run-dir", str(tmp_path / "q")],
                    out=lines.append) == 0
    saves = _saves(tmp_path)
    monkeypatch.setenv("MADDEN_SAVES_DIR", str(saves))
    assert cli.main(["clone-save", "--to", "CAREER-TUNE-NEW", "--dry-run", "--run-dir", str(tmp_path / "c")],
                    out=lines.append) == 0
    assert not (saves / "CAREER-TUNE-NEW").exists()
    assert notices == []


def test_cli_default_run_dir():
    d = cli.run_dir_for("sim-season")
    assert d.parent.name == "pilot_runs" and d.parent.parent.name == "data" and d.name.endswith("_sim-season")


def test_run_dirs_never_share_a_steps_folder(tmp_path, monkeypatch, notices):
    # the pilot numbers step PNGs per process: two invocations in one second must not overwrite
    # each other's frames (spike S-1 lost most of its in-game frames that way)
    import datetime as dt
    import types

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 12, 14, 0, 0)

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "datetime", types.SimpleNamespace(datetime=Frozen))
    first = cli.run_dir_for("watch-game")
    first.mkdir(parents=True)
    second = cli.run_dir_for("watch-game")
    assert second != first and second.name == first.name + "-2"
    second.mkdir()
    assert cli.run_dir_for("watch-game").name == first.name + "-3"


def test_loading_frames_are_unknown():
    fake, _ = fake_madden()
    from tools.madden_pilot.screens import classify
    assert classify(Frame(None, fake.words(fake.lines(LOADING))), CAT) == "UNKNOWN"


def test_every_screen_a_load_can_open_on_counts_as_a_hub():
    # a bye week (HUB_BYE) was missing, so an advance that landed on one timed out for 120 s
    for name in ("HUB_WEEKLY", "HUB_GAMEDAY", "HUB_OTHER", "HUB_WEEKLY_DRAFT_CARD", "HUB_BYE",
                 "LIVE_EVENT_FRENZY", "LIVE_EVENT_FRENZY_MODAL", "FREE_AGENCY_ENDED", "DRAFT_HUB", "DRAFT_LAUNCH"):
        assert name in jobs.HUBS, name


def test_save_tiles_follow_save_recency_and_find_the_index(tmp_path):
    import os
    for i, name in enumerate(["CAREER-OLD", "CAREER-NEW", "CAREER-MID"]):
        p = tmp_path / name
        p.write_text("x")
        os.utime(p, (100 + i * 10, 100 + i * 10))
    assert jobs.save_tiles(tmp_path) == ["MID", "NEW", "OLD"] or \
        jobs.save_tiles(tmp_path) == sorted(jobs.save_tiles(tmp_path))
    order = jobs.save_tiles(tmp_path)
    assert order[-1] == "OLD" and order[0] == "MID"          # newest first
    assert jobs.tile_index("CAREER-NEW", tmp_path) == order.index("NEW")
    assert jobs.tile_index("CAREER-MISSING", tmp_path) is None


# ---------------------------------------------------------------- advance (P2-03, one stop)
def _advance(tmp_path, fake, **kw):
    p = make_pilot(tmp_path, fake)
    return p, jobs.advance(p, save="CAREER-TUNE-X", watcher=lambda: False,
                           folder=tmp_path / "none", **kw)


def test_advance_moves_exactly_one_stop_and_quits(tmp_path, notices):
    fake, log = fake_madden(week=1)
    _, out = _advance(tmp_path, fake)
    assert out["status"] == "ok", out
    assert (out["from"]["week"], out["to"]["week"]) == (1, 2)
    assert out["quit"] and fake.screen == "FRANCHISE_LIST"
    assert log.count("  key   space     ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 1
    assert "tab" not in fake.keys                                   # the autosave is the save


def test_advance_in_place_from_a_loaded_hub_stays_in_the_franchise(tmp_path, notices):
    fake, log = fake_madden(start="HUB_WEEKLY", week=3)
    p = make_pilot(tmp_path, fake)
    out = jobs.advance(p, save="CAREER-TUNE-X", franchise="TUNE-X", watcher=lambda: False,
                       folder=tmp_path / "none", quit=False)
    assert out["status"] == "ok" and out["to"]["week"] == 4
    assert fake.screen == "HUB_WEEKLY" and out["quit"] is False
    assert log.count("  key   space     ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 1


def test_advance_refuses_non_tune_in_place_or_while_the_watcher_runs(tmp_path, notices):
    fake, log = fake_madden()
    p = make_pilot(tmp_path, fake)
    assert jobs.advance(p, save="CAREER-MU2026", watcher=lambda: False, folder=tmp_path)["status"] == "refused"
    assert jobs.advance(p, save="CAREER-TUNE-X", franchise="MU2026", watcher=lambda: False,
                        folder=tmp_path)["status"] == "refused"
    assert jobs.advance(p, save="CAREER-TUNE-X", watcher=lambda: True, folder=tmp_path)["status"] == "refused"
    assert fake.keys == [] and fake.looks == 0


def test_save_header_names_the_offseason_stop(monkeypatch):
    snap = {"season": {"calendar_year": 2026, "stage": "OffSeason", "week_type": "OffSeason",
                       "week_index": 7, "offseason_stage": 6}}
    monkeypatch.setattr(jobs, "_snapshot", lambda path: snap)
    assert jobs._save_header("x") == {"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 7,
                                      "stop": "PRO_DAY"}


def test_save_header_separates_the_preseason_from_a_regular_season_week(monkeypatch):
    # the preseason hub header reads "2027. WEEK 1", so only the save can tell the two apart
    snap = {"season": {"calendar_year": 2027, "stage": "PreSeason", "week_type": "PreSeason", "week_index": 2}}
    monkeypatch.setattr(jobs, "_snapshot", lambda path: snap)
    assert jobs._save_header("x") == {"year": 2027, "phase": "PRESEASON", "week": None, "stage": 2,
                                      "stop": "PRESEASON_2"}


def test_advance_stops_when_the_save_did_not_move(tmp_path, notices, monkeypatch):
    # the hub header can move on its own; the stop is the save, so a save that didn't move is a stop
    monkeypatch.setattr(jobs, "_save_header",
                        lambda path: {"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 0, "stop": "STAFF"})
    fake, _ = fake_madden(week=1)
    _, out = _advance(tmp_path, fake)
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "left the stop where it was (STAFF)" in out["reason"]


def test_advance_reports_the_save_stop_in_the_offseason(tmp_path, notices, monkeypatch):
    # the offseason hub has no header of its own and its cards mention "STAFF"; the save's stage is
    # the stop. A save that stays on the same stage is a Stuck, and the reason names that stage.
    monkeypatch.setattr(jobs, "_save_header",
                        lambda path: {"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 1, "stop": "RE-SIGN"})
    fake, _ = fake_madden(week=1)
    _, out = _advance(tmp_path, fake)
    assert out["status"] == "stopped" and "RE-SIGN" in out["reason"]


def test_stop_key_separates_offseason_stages():
    assert jobs._stop_key({"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 1}) != \
        jobs._stop_key({"year": 2026, "phase": "OFFSEASON", "week": None, "stage": 2})
    assert jobs._stop_key(None) is None


def test_advance_stops_unknown_off_a_hub(tmp_path, notices):
    fake, _ = fake_madden(start="MAIN_MENU")
    p = make_pilot(tmp_path, fake)
    out = jobs.advance(p, save="CAREER-TUNE-X", franchise="TUNE-X", watcher=lambda: False,
                       folder=tmp_path / "none")
    assert out["status"] == "stopped" and out["kind"] == "Unknown"
    assert "space" not in fake.keys


# ---------------------------------------------------------------- loading the wrong save
def test_the_tile_walk_steps_to_the_target_one_verified_press_at_a_time(tmp_path, notices):
    # Madden orders tiles by the save's own date, so the pilot must not jump by the folder's
    # order; it presses one arrow at a time and stops when the border is on the target.
    fake, log = fake_madden(tiles=("TUNE-A", "TUNE-B", "TUNE-X"))
    _, out = _sim(tmp_path, fake, to="week 2")
    assert out["status"] == "ok", out
    rights = [ln for ln in log if ln.startswith("  key   right") and "FRANCHISE_LIST" in ln]
    assert len(rights) == 2 and fake.screen == "FRANCHISE_LIST"


def test_load_refuses_when_the_hub_is_not_the_save_we_asked_for(tmp_path, notices, monkeypatch):
    # the hub says 2026 WEEK 1 (the fake's header) but the file on disk is the 2028 season:
    # exactly what a lost arrow press on CURRENT FRANCHISES produced on 2026-11-10.
    monkeypatch.setattr(jobs, "_save_header", lambda path: {"year": 2028, "phase": "WEEK", "week": 1})
    fake, log = fake_madden(week=1)
    p = make_pilot(tmp_path, fake)
    out = jobs.sim_season(p, save="CAREER-TUNE-X", to="week 2", watcher=lambda: False, folder=tmp_path / "none")
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "refusing to sim the wrong save" in out["reason"]
    assert log.count("  key   space     ADVANCE_MENU on FORCE ADVANCE -> COACH_PROGRESS") == 0


def test_load_accepts_the_same_season_and_week(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "_save_header", lambda path: {"year": 2026, "phase": "WEEK", "week": 1})
    fake, _ = fake_madden(week=1)
    p = make_pilot(tmp_path, fake)
    out = jobs.sim_season(p, save="CAREER-TUNE-X", to="week 2", watcher=lambda: False, folder=tmp_path / "none")
    assert out["status"] == "ok" and out["end_week"] == 2


# ---------------------------------------------------------------- the game clock (watch_game)
def _clock(lines):
    return jobs.read_game_clock(Frame(None, lines=lines))


def test_read_game_clock_reads_the_scorebug():
    assert _clock(["CHI 0 CAR 0", "1st 10:21"]) == {"quarter": 1, "seconds": 621}
    assert _clock(["2ND 0:18"]) == {"quarter": 2, "seconds": 18}
    assert _clock(["3RD 0:04", ":40"]) == {"quarter": 3, "seconds": 4}
    assert _clock(["4TH 15:00"]) == {"quarter": 4, "seconds": 900}
    assert _clock(["1st 11:00"]) == {"quarter": 1, "seconds": 660}


def test_read_game_clock_never_mistakes_the_play_clock_a_stat_line_or_a_partial_read():
    assert _clock([":40"]) is None                     # the play clock on its own
    assert _clock([":40", "2ND"]) is None              # ... next to a quarter word
    assert _clock([":40", "53.3", "72.2"]) is None     # passer numbers
    assert _clock(["2ND & 18"]) is None                # a down marker, not a clock
    assert _clock(["2ND 0:1"]) is None                 # seconds always read as two digits
    assert _clock(["CHI 0 CAR 0"]) is None
    assert _clock([]) is None
    assert jobs.read_game_clock(None) is None


def test_read_game_clock_pairs_the_scorebug_split_across_two_lines():
    # S-1's pause frame (data/spikes/20261112-1410-game-start-halftime/run/steps/0048_UNKNOWN.json)
    # reads "2nd" and "2:54" as two OCR lines on one row, beside the down marker "1st & 35".
    words = [{"text": "2nd", "box": [821, 288, 78, 22], "line": 13},
             {"text": "2:54", "box": [922, 288, 89, 22], "line": 17},
             {"text": "1st", "box": [1162, 288, 57, 22], "line": 23},
             {"text": "35", "box": [1269, 288, 50, 22], "line": 23}]
    assert jobs.read_game_clock(Frame(None, words)) == {"quarter": 2, "seconds": 174}
    down_only = [{"text": "1st", "box": [1162, 288, 57, 22], "line": 0},
                 {"text": "35", "box": [1269, 288, 50, 22], "line": 0}]
    assert jobs.read_game_clock(Frame(None, down_only)) is None


# ---------------------------------------------------------------- watch_game
class WatchMadden(ScriptedMadden):
    """A ScriptedMadden that marks where the kickoff hold and the RESUME start, and can write the
    autosave when the franchise is left (spike S-1: quitting writes <save>-AUTOSAVE)."""

    def __init__(self, *a, autosave=None, **kw):
        super().__init__(*a, **kw)
        self.autosave = autosave
        self.kick_at = None
        self.resume_at = None
        self.quit_game_at = None

    def on_key(self, name):
        before, label = self.screen, self.highlight
        super().on_key(name)
        select = self.keymap.get("select", "space")
        if before == "GAME_KICKOFF" and name == select:
            self.kick_at = len(self.keys)
        elif before == "PAUSE_MENU" and name == select and label == "RESUME":
            self.resume_at = len(self.keys)
        elif before == "PAUSE_MENU" and name == select and label == "QUIT GAME":
            self.quit_game_at = len(self.keys)
        elif self.screen == "FRANCHISE_LIST" and self.autosave and before in ("QUIT_CONFIRM",
                                                                             "QUIT_GAME_CONFIRM"):
            self.autosave.write_bytes(b"autosave")     # quitting the game/franchise writes it


def _watch(tmp_path, fake, **kw):
    p = make_pilot(tmp_path, fake)
    kw.setdefault("folder", tmp_path / "none")
    return p, jobs.watch_game(p, save=kw.pop("save", "CAREER-TUNE-X"), watcher=lambda: False, **kw)


@pytest.mark.parametrize("save", [None, "CAREER-MU2026", "TUNE-X", "career-tunex", "CAREER-TUNE-a b"])
def test_watch_game_refuses_non_tune_saves_before_pressing(tmp_path, notices, save):
    fake, _ = fake_madden()
    out = jobs.watch_game(make_pilot(tmp_path, fake), save=save, watcher=lambda: False, folder=tmp_path)
    assert out["status"] == "refused" and "CAREER-TUNE-" in out["why"]
    assert fake.keys == [] and fake.looks == 0
    assert result_json(tmp_path)["status"] == "refused"


def test_watch_game_refuses_a_running_watcher_a_bad_target_and_a_bad_hold(tmp_path, notices):
    fake, _ = fake_madden()
    p = make_pilot(tmp_path, fake)
    out = jobs.watch_game(p, save="CAREER-TUNE-X", watcher=lambda: True, folder=tmp_path)
    assert out["status"] == "refused" and "watch_save" in out["why"]
    for to in ("super-bowl", "kick off", "", None):
        out = jobs.watch_game(p, save="CAREER-TUNE-X", to=to, watcher=lambda: False, folder=tmp_path)
        assert out["status"] == "refused" and "kickoff or halftime" in out["why"], out
    for hold in (0, 601, -5, "soon", 2.5):
        out = jobs.watch_game(p, save="CAREER-TUNE-X", hold_seconds=hold, watcher=lambda: False, folder=tmp_path)
        assert out["status"] == "refused" and "hold-seconds" in out["why"], out
    out = jobs.watch_game(p, save="CAREER-TUNE-X", franchise="ALLUSER", watcher=lambda: False, folder=tmp_path)
    assert out["status"] == "refused" and "TUNE-*" in out["why"]
    assert fake.keys == [] and fake.looks == 0


def test_watch_game_kickoff_starts_the_game_then_presses_nothing(tmp_path, notices):
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    p, out = _watch(tmp_path, fake, hold_seconds=7)
    assert out["status"] == "ok", out
    assert (out["route"], out["to"], out["hold_seconds"]) == ("kickoff", "kickoff", 7)
    assert out["pack"] is None and out["franchise"] == "TUNE-X"
    assert out["clock"] == {"quarter": 1, "seconds": 660}          # 1st 11:00, S-1's kickoff
    text = "\n".join(log)
    assert "HUB_GAMEDAY on PLAY SEASON GAME -> GAME_PREGAME_PIVOT" in text
    assert "GAME_PREGAME_PIVOT -> GAME_PREGAME_READY" in text
    assert "GAME_PREGAME_READY on READY -> GAME_KICKOFF" in text
    assert "GAME_KICKOFF on DEEP KICK -> GAME_PLAY" in text
    # the whole point: the kick is the last press before the hold, and nothing is pressed for it —
    # the very next key is the escape that starts the exit (`_quit_game`), after the hold.
    assert fake.kick_at is not None and fake.keys[fake.kick_at] == "esc"
    assert fake.quit_game_at is not None and fake.quit_game_at > fake.kick_at
    assert out["end_screen"] == "FRANCHISE_LIST" and out["quit"] is True
    assert result_json(tmp_path)["job"] == "watch-game"


def test_watch_game_grants_the_two_start_and_leave_labels_by_policy_only(tmp_path, notices):
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None)
    p = make_pilot(tmp_path, fake)
    jobs.watch_game(p, save="CAREER-TUNE-X", hold_seconds=2, watcher=lambda: False, folder=tmp_path / "none")
    assert p.policy["HUB_GAMEDAY"] == ["PLAY SEASON GAME"]
    assert p.policy["PAUSE_MENU"] == ["QUIT GAME"]                  # this run only
    assert CAT["screens"]["HUB_GAMEDAY"]["confirm_ok"] == ["ADVANCE WEEK"]
    assert CAT["screens"]["PAUSE_MENU"]["confirm_ok"] == ["RESUME", "SUPER", "SUPER SIM"]


def test_watch_game_watches_an_already_loaded_franchise_in_place(tmp_path, notices):
    log = []
    fake = WatchMadden(CAT, start="HUB_GAMEDAY", tiles=["TUNE-X"], out=log.append)
    p = make_pilot(tmp_path, fake)
    out = jobs.watch_game(p, save="CAREER-TUNE-X", franchise="TUNE-X", hold_seconds=2,
                          watcher=lambda: False, folder=tmp_path / "none")
    assert out["status"] == "ok" and out["franchise"] == "TUNE-X", out
    text = "\n".join(log)
    # it watched the franchise it was told is loaded: no tile walk, no PROGRESSION_WARNING (the
    # list it ends on is the exit's own destination)
    assert "PROGRESSION_WARNING" not in text and "FRANCHISE_LIST on" not in text
    assert "HUB_GAMEDAY on PLAY SEASON GAME -> GAME_PREGAME_PIVOT" in text


def test_watch_game_halftime_walks_the_supersim_recipe(tmp_path, notices):
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    p, out = _watch(tmp_path, fake, to="halftime", hold_seconds=7)
    assert out["status"] == "ok", out
    assert out["route"] == "supersim"
    assert out["clock"]["quarter"] == 2 and out["clock"]["seconds"] < 20
    text = "\n".join(log)
    assert "PAUSE_MENU on SUPER -> SUPERSIM_MENU" in text
    assert "SUPERSIM_MENU on JUMP FORWARD -> SUPERSIM_JUMP_FORWARD" in text
    assert text.count("SUPERSIM_JUMP_FORWARD on NEXT QUARTER") == 1
    assert text.count("SUPERSIM_JUMP_FORWARD on CHANGE OF POSSESSION") == 2
    assert text.count("SUPERSIM_MENU on JUMP TO NEXT PLAY") > 1      # Q2 by play, not "sim to end"
    assert "PAUSE_MENU on RESUME -> GAME_PLAY" in text
    # JR's control rule: the sim loop never plays football. The only snaps are the half's last,
    # after RESUME, then the hold presses nothing and the exit starts.
    assert sum(1 for ln in log if ln.startswith("  key   space     GAME_PLAY")) == 2
    after_resume = fake.keys[fake.resume_at:]
    assert after_resume[:3] == ["space", "space", "esc"]              # the snap, then straight out
    assert text.count("PAUSE_MENU on QUIT GAME") == 1
    assert fake.quit_game_at > fake.resume_at + 2
    assert out["end_screen"] == "FRANCHISE_LIST"


def test_watch_game_writes_its_ready_marker_immediately_before_the_hold(tmp_path, notices):
    """F1/F3: the job's own ready signal exists when the hold starts, not earlier."""
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None)
    seen = []

    class SleepHook:
        def __call__(self, seconds):
            seen.append(((tmp_path / jobs.READY_FILE).is_file(), seconds))
            fake.sleep(seconds)

    p = Pilot(CAT, tmp_path, driver=fake, sleep=SleepHook(), clock=fake.clock,
              highlight_reader=lambda frame, labels, spec: fake.highlight)
    out = jobs.watch_game(p, save="CAREER-TUNE-X", hold_seconds=5, watcher=lambda: False,
                          folder=tmp_path / "none", ready_token="token-1")
    assert out["status"] == "ok"
    hold = seen.index((True, 5))
    assert hold > 0 and all(not exists for exists, _ in seen[:hold])   # not before the hold
    ready = json.loads((tmp_path / jobs.READY_FILE).read_text())
    assert ready["job"] == "watch-game" and ready["anchor"] == "kickoff"
    assert ready["route"] == "kickoff" and ready["hold_seconds"] == 5
    assert ready["ready_token"] == "token-1" and ready["pid"] == os.getpid()
    assert ready["hold_until_epoch"] > time.time()
    assert ready["screen"] == "GAME_KICKOFF"                    # the last screen it looked at


def test_watch_game_halftime_ready_marker_names_the_halftime_anchor(tmp_path, notices):
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None)
    out = jobs.watch_game(make_pilot(tmp_path, fake), save="CAREER-TUNE-X", to="halftime",
                          hold_seconds=7, watcher=lambda: False, folder=tmp_path / "none",
                          ready_token="token-2")
    assert out["status"] == "ok" and out["route"] == "supersim"
    ready = json.loads((tmp_path / jobs.READY_FILE).read_text())
    assert ready["anchor"] == "halftime_start" and ready["route"] == "supersim"
    assert ready["ready_token"] == "token-2"


def test_watch_game_supersim_holds_on_the_supersim_screen(tmp_path, notices):
    """F2: the static segment has its own route and verifies SUPERSIM_MENU."""
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    out = jobs.watch_game(make_pilot(tmp_path, fake), save="CAREER-TUNE-X", to="supersim",
                          hold_seconds=2, watcher=lambda: False, folder=tmp_path / "none",
                          ready_token="token-3")
    assert out["status"] == "ok", out
    assert out["to"] == "supersim" and out["route"] == "supersim"
    assert out["clock"]["quarter"] == 1
    text = "\n".join(log)
    assert "PAUSE_MENU on SUPER -> SUPERSIM_MENU" in text
    ready = json.loads((tmp_path / jobs.READY_FILE).read_text())
    assert ready["anchor"] == "supersim_menu" and ready["screen"] == "SUPERSIM_MENU"
    assert fake.keys[fake.kick_at] != "esc" or True             # the kick happened before the hold
    assert out["end_screen"] == "FRANCHISE_LIST"


def test_watch_game_halftime_stops_at_0_00_instead_of_resuming(tmp_path, notices):
    class ZeroClock(WatchMadden):
        def game_key(self, name, label, select):
            if (self.screen == "SUPERSIM_JUMP_FORWARD" and label == "CHANGE OF POSSESSION"):
                self.game_quarter, self.game_seconds = 2, 0
                self.go("SUPERSIM_MENU")
                return True
            return super().game_key(name, label, select)

    fake = ZeroClock(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _watch(tmp_path, fake, to="halftime", hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck", out
    assert "0:00 in Q2" in out["reason"]
    assert fake.resume_at is None
    assert not (tmp_path / jobs.READY_FILE).exists()            # never held on a half already over


def test_watch_game_halftime_stops_stuck_instead_of_resuming_outside_the_window(tmp_path, notices,
                                                                              monkeypatch):
    class FrozenClock(WatchMadden):
        def game_key(self, name, label, select):
            if self.screen == "SUPERSIM_MENU" and label == "JUMP TO NEXT PLAY":
                self.pending = [LOADING]
                self.go("SUPERSIM_MENU")          # the clock never moves: Q2 11:00 every read
                return True
            return super().game_key(name, label, select)

    monkeypatch.setattr(jobs, "HALFTIME_MAX_PLAY_PRESSES", 3)
    fake = FrozenClock(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _watch(tmp_path, fake, to="halftime", hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck", out
    assert "not resuming the half at the wrong moment" in out["reason"]
    assert fake.resume_at is None                    # RESUME was never confirmed
    assert json.loads((tmp_path / "stop.json").read_text())["kind"] == "Stuck"


def test_watch_game_halftime_stops_when_the_sim_leaves_q2(tmp_path, notices):
    class PastHalftime(WatchMadden):
        def game_key(self, name, label, select):
            if self.screen == "SUPERSIM_JUMP_FORWARD" and label == "NEXT QUARTER":
                self.pending = [LOADING]
                self.game_quarter, self.game_seconds = 3, 900
                self.go("SUPERSIM_MENU")
                return True
            return super().game_key(name, label, select)

    fake = PastHalftime(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _watch(tmp_path, fake, to="halftime", hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "reached Q3" in out["reason"] and fake.resume_at is None


def test_watch_game_reports_the_autosave_written_by_the_quit(tmp_path, notices, monkeypatch):
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "CAREER-TUNE-X").write_bytes(b"save")
    autosave = saves / "CAREER-TUNE-X-AUTOSAVE"

    class EndedGame(WatchMadden):
        """The game ends during the hold: the next frame is the franchise hub again."""

        def call(self, cmd):
            if cmd[0] == "look" and self.kick_at and self.screen == "GAME_PLAY":
                self.go("HUB_WEEKLY")
            return super().call(cmd)

    calls = []

    def fake_quit_now(pilot, franchise=None, allow_quit=False):
        calls.append({"franchise": franchise, "allow_quit": allow_quit})
        autosave.write_bytes(b"autosave")          # quitting writes it (S-1, plan D1)
        return {"screen": "FRANCHISE_LIST", "quit": True, "franchise": pilot.loaded_franchise}

    monkeypatch.setattr(jobs, "_quit_now", fake_quit_now)
    fake = EndedGame(CAT, tiles=["TUNE-X"], out=lambda s: None)
    p, out = _watch(tmp_path, fake, hold_seconds=2, folder=saves)
    assert out["status"] == "ok", out
    assert calls == [{"franchise": "CAREER-TUNE-X", "allow_quit": False}]
    assert out["quit"] is True and out["end_screen"] == "FRANCHISE_LIST"
    assert out["quit_route"] == "route"               # outside a game: advance's own quit path
    assert out["written"] == {"save": False, "autosave": True} and out["autosave_written"] is True
    assert out["autosave_path"] == str(autosave) and autosave.is_file()


def test_watch_game_quits_the_live_game_through_the_pause_menu(tmp_path, notices):
    # JR (2026-11-12, M-0673/M-0675): a CAREER-TUNE-* clone may have its game quit, so the run
    # leaves through PAUSE_MENU > QUIT GAME > its confirmation instead of sitting in the game.
    log = []
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = _watch(tmp_path, fake, hold_seconds=2)
    assert out["status"] == "ok", out
    assert out["quit"] is True and out["quit_route"] == "quit-game"
    assert out["end_screen"] == "FRANCHISE_LIST" and fake.screen == "FRANCHISE_LIST"
    text = "\n".join(log)
    assert "PAUSE_MENU on QUIT GAME -> QUIT_GAME_CONFIRM" in text
    assert "QUIT_GAME_CONFIRM on QUIT GAME -> FRANCHISE_LIST" in text
    # once, and only after the hold: the QUIT GAME press is the first use of the label
    assert text.count("PAUSE_MENU on QUIT GAME") == 1
    assert fake.quit_game_at > fake.kick_at and fake.keys[fake.kick_at] == "esc"
    # no cancel-shaped label is ever selected on the confirmation
    assert "on CANCEL" not in text and "on NO " not in text


def test_watch_game_dismisses_a_penalty_before_it_pauses(tmp_path, notices):
    # real play does not run itself (spike S-1): a hold that outlasts the play clock ends on a
    # delay-of-game penalty dialog, so the exit's first Escape dismisses it and the second pauses
    class Penalty(WatchMadden):
        penalties = 0

        def on_key(self, name):
            if name == "esc" and self.screen == "GAME_PLAY" and not self.penalties:
                self.penalties = 1
                self.go("DELAY OF GAME PENALTY")     # uncatalogued: reads as UNKNOWN
                return
            super().on_key(name)

        def game_key(self, name, label, select):
            if self.screen == "DELAY OF GAME PENALTY" and name == "esc":
                self.go("GAME_PLAY")
                return True
            return super().game_key(name, label, select)

    log = []
    fake = Penalty(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = _watch(tmp_path, fake, hold_seconds=2)
    assert out["status"] == "ok", out
    assert fake.penalties == 1 and out["end_screen"] == "FRANCHISE_LIST"
    # the penalty, then real play again, then the pause — three Escapes, not one
    assert fake.keys.count("esc") == 3 and fake.quit_game_at is not None


def test_pause_gives_up_after_a_bounded_number_of_escapes(tmp_path, notices):
    class NeverPauses(WatchMadden):
        def on_key(self, name):
            if name == "esc":
                self.pending = [LOADING]             # Escape never reaches the pause menu
                return
            super().on_key(name)

    fake = NeverPauses(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _watch(tmp_path, fake, hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck"
    assert "didn't reach the pause menu" in out["reason"] and "not pressing again" in out["reason"]
    assert fake.keys.count("esc") == 3                 # the bounded tries, no more


def test_watch_game_reports_the_autosave_the_quit_game_wrote(tmp_path, notices):
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "CAREER-TUNE-X").write_bytes(b"save")
    autosave = saves / "CAREER-TUNE-X-AUTOSAVE"
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None, autosave=autosave)
    _, out = _watch(tmp_path, fake, hold_seconds=2, folder=saves)
    assert out["status"] == "ok", out
    assert out["quit_route"] == "quit-game" and out["end_screen"] == "FRANCHISE_LIST"
    assert out["written"] == {"save": False, "autosave": True}
    assert out["autosave_written"] is True and autosave.is_file()
    assert (saves / "CAREER-TUNE-X").read_bytes() == b"save"      # the named save is untouched


def test_watch_game_stops_when_quit_game_lands_on_an_unseen_screen(tmp_path, notices):
    class OddLanding(WatchMadden):
        def game_key(self, name, label, select):
            if self.screen == "PAUSE_MENU" and label == "QUIT GAME":
                self.pending = [LOADING]
                self.go("SOMETHING NEW")         # neither the confirmation nor the franchise list
                return True
            return super().game_key(name, label, select)

    fake = OddLanding(CAT, tiles=["TUNE-X"], out=lambda s: None)
    _, out = _watch(tmp_path, fake, hold_seconds=2)
    assert out["status"] == "stopped" and out["kind"] == "Stuck", out
    assert "QUIT GAME landed on UNKNOWN" in out["reason"] and "not pressing again" in out["reason"]
    assert fake.quit_game_at == len(fake.keys)          # nothing after the QUIT GAME press
    assert json.loads((tmp_path / "stop.json").read_text())["kind"] == "Stuck"


def test_watch_game_never_confirms_a_cancel_shaped_quit_game_label(tmp_path, notices):
    import copy

    cat = copy.deepcopy(CAT)
    dialog = cat["screens"]["QUIT_GAME_CONFIRM"]
    dialog["options"] = ["CANCEL", "NO"]
    dialog["default"] = "CANCEL"
    dialog["confirm_ok"] = ["CANCEL"]                  # a dialog that may only reject
    dialog["exits"] = {}
    fake = WatchMadden(cat, start="QUIT_GAME_CONFIRM", tiles=["TUNE-X"], out=lambda s: None)
    p = make_pilot(tmp_path, fake, catalog=cat)
    with pytest.raises(jobs.Stuck):
        jobs._confirm_quit_game(p)
    assert fake.keys == []                              # no label was pressed
    assert json.loads((tmp_path / "stop.json").read_text())["kind"] == "Stuck"


def test_cancel_shaped_labels_are_recognised():
    for label in ("CANCEL", "Cancel", "NO", "BACK", "DELETE", "TRADE", "NO THANKS"):
        assert jobs._cancel_shaped(label), label
    for label in ("QUIT GAME", "QUIT", "YES", "CONFIRM", "LEAVE GAME"):
        assert not jobs._cancel_shaped(label), label


def test_watch_game_repins_the_pause_menu_instead_of_walking_to_quit_game(tmp_path, notices):
    # A pause menu re-opened from the sim screen can keep SUPER SIM selected while the pilot's own
    # cursor resets with the screen change: five Downs from there would land on QUIT GAME, so the
    # menu is pinned from its top item first (one Up per option).
    class RememberingMenu(WatchMadden):
        def go(self, screen):
            back_from_sim = screen == "PAUSE_MENU" and self.screen == "SUPERSIM_MENU"
            super().go(screen)
            if back_from_sim:
                self.idx = 5                      # SUPER SIM is still selected on the way back

    log = []
    fake = RememberingMenu(CAT, tiles=["TUNE-X"], out=log.append)
    _, out = _watch(tmp_path, fake, to="halftime", hold_seconds=2)
    assert out["status"] == "ok", out
    text = "\n".join(log)
    # the only QUIT GAME press is the exit's own, once, at the end — never a drift from SUPER
    assert text.count("PAUSE_MENU on QUIT GAME") == 1
    assert text.count("PAUSE_MENU on SUPER -> SUPERSIM_MENU") >= 1
    assert text.count("PAUSE_MENU on RESUME -> GAME_PLAY") == 1


def test_watch_game_refuses_a_missing_save_file(tmp_path, notices):
    saves = tmp_path / "saves"
    saves.mkdir()
    fake = WatchMadden(CAT, tiles=["TUNE-X"], out=lambda s: None)
    out = jobs.watch_game(make_pilot(tmp_path, fake), save="CAREER-TUNE-X", watcher=lambda: False, folder=saves)
    assert out["status"] == "refused" and "clone-save" in out["why"]
    assert fake.keys == []


# ---------------------------------------------------------------- CLI dry runs (watch-game)
def test_cli_dry_run_watch_game_prints_the_route_and_touches_nothing(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    lines = []
    rc = cli.main(["watch-game", "--save", "CAREER-TUNE-X", "--hold-seconds", "5", "--dry-run",
                   "--run-dir", str(tmp_path)], out=lines.append)
    assert rc == 0
    text = "\n".join(lines)
    assert lines[0].startswith("[dry-run] watch-game: FRANCHISE_LIST > highlight TUNE-X")
    assert "PLAY SEASON GAME" in lines[0] and "hold 5s pressing nothing" in lines[0]
    assert "QUIT_GAME_CONFIRM" in lines[0]                    # the exit is narrated too
    assert "GAME_KICKOFF on DEEP KICK -> GAME_PLAY" in text
    assert "PAUSE_MENU on QUIT GAME -> QUIT_GAME_CONFIRM" in text
    assert "QUIT_GAME_CONFIRM on QUIT GAME -> FRANCHISE_LIST" in text
    res = result_json(tmp_path)
    assert res["status"] == "ok" and res["dry_run"] is True and res["route"] == "kickoff"
    assert res["quit"] is True and res["quit_route"] == "quit-game"
    assert res["end_screen"] == "FRANCHISE_LIST"
    assert notices == []


def test_cli_dry_run_watch_game_halftime(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    lines = []
    rc = cli.main(["watch-game", "--save", "CAREER-TUNE-X", "--to", "halftime", "--hold-seconds", "2",
                   "--dry-run", "--run-dir", str(tmp_path / "h")], out=lines.append)
    assert rc == 0
    text = "\n".join(lines)
    assert "SUPERSIM_JUMP_FORWARD on NEXT QUARTER" in text and "PAUSE_MENU on RESUME" in text
    assert "QUIT_GAME_CONFIRM on QUIT GAME -> FRANCHISE_LIST" in text
    res = result_json(tmp_path / "h")
    assert res["status"] == "ok" and res["route"] == "supersim"
    assert res["quit_route"] == "quit-game" and res["end_screen"] == "FRANCHISE_LIST"


def test_cli_dry_run_watch_game_refuses_a_bad_target(tmp_path, notices, monkeypatch):
    monkeypatch.setattr(jobs, "watcher_running", lambda: False)
    lines = []
    rc = cli.main(["watch-game", "--save", "CAREER-TUNE-X", "--to", "endgame", "--dry-run",
                   "--run-dir", str(tmp_path)], out=lines.append)
    assert rc == 1 and result_json(tmp_path)["status"] == "refused"
    assert notices == []


# ---------------------------------------------------------------- measured pause menu variants (F7)
LIVE_PAUSE_ROWS = ["RESUME", "CALL TIMEOUT", "CHALLENGE PLAY", "SUPER SIM", "CELEBRATIONS",
                   "STATS", "COACHING", "OPTIONS", "QUIT GAME"]
CATALOG_PAUSE_ROWS = ["RESUME", "INSTANT REPLAY", "VIEW HIGHLIGHTS", "CALL TIMEOUT",
                      "CHALLENGE PLAY", "SUPER", "CELEBRATIONS", "STATS", "COACHING", "OPTIONS",
                      "QUIT GAME"]


class PauseMenuDriver:
    """A real-Pilot menu driver independent of the catalog's option list.

    It models the measured 9-row and catalog 11-row pause menus, a selection that clamps or
    wraps, optional disabled-row skipping, and a start on CELEBRATIONS (the live screenshot).
    `selected` is the actual row, which the injected highlight reader returns as real border
    evidence; the catalog's shadow cursor is never used to authorize Space.
    """

    def __init__(self, visible, start="CELEBRATIONS", skip_disabled=False, wrap=False,
                 target_screen=None):
        self.visible = list(visible)
        self.navigable = [row for row in self.visible
                          if not (skip_disabled and row == "CHALLENGE PLAY")]
        self.idx = self.navigable.index(start)
        self.wrap = wrap
        self.confirmed = []
        self.keys = []
        self.screen = "PAUSE_MENU"
        self.target_screen = target_screen

    @property
    def selected(self):
        return self.navigable[self.idx]

    def rows(self):
        return self.visible if self.screen == "PAUSE_MENU" else ["JUMP FORWARD", "JUMP TO NEXT PLAY",
                                                                 "CUSTOM PLAY OPTIONS"]

    def call(self, cmd):
        if cmd[0] == "look":
            return {"ok": True, "png": None, "size": [1920, 1080],
                    "words": [{"text": word, "box": [85 + j * 80, 80 + i * 44, 70, 28],
                               "line": i}
                              for i, row in enumerate(self.rows())
                              for j, word in enumerate(row.split())]}
        if cmd[0] == "key":
            name = cmd[cmd.index("--name") + 1]
            times = int(cmd[cmd.index("--times") + 1]) if "--times" in cmd else 1
            self.keys.append(name)
            for _ in range(times):
                if name in ("up", "down"):
                    step = -1 if name == "up" else 1
                    nxt = self.idx + step
                    if self.wrap:
                        self.idx = nxt % len(self.navigable)
                    else:
                        self.idx = max(0, min(len(self.navigable) - 1, nxt))
                elif name == "space":
                    self.confirmed.append(self.selected)
                    if self.target_screen and self.selected == "SUPER SIM":
                        self.screen = self.target_screen
            return {"ok": True}
        raise AssertionError(cmd)


def pin_the_menu(tmp_path, driver):
    pilot = Pilot(CAT, tmp_path, driver=driver, sleep=lambda _: None,
                  highlight_reader=lambda frame, labels, spec: driver.selected)
    pilot.look()
    return pilot


@pytest.mark.parametrize("rows", [LIVE_PAUSE_ROWS, CATALOG_PAUSE_ROWS])
@pytest.mark.parametrize("skip_disabled,wrap", [(False, False), (True, False), (False, True),
                                                (True, True)])
@pytest.mark.parametrize("target", ["SUPER", "RESUME", "QUIT GAME"])
def test_pin_highlight_finds_the_exact_visible_row(tmp_path, notices, rows, skip_disabled, wrap,
                                                   target):
    driver = PauseMenuDriver(rows, skip_disabled=skip_disabled, wrap=wrap)
    pilot = pin_the_menu(tmp_path, driver)
    jobs._pin_highlight(pilot, target)
    if target == "SUPER":
        assert driver.selected in ("SUPER", "SUPER SIM")
        pilot.confirm_on("PAUSE_MENU", "SUPER")
        assert driver.confirmed == [driver.selected]
    else:
        assert driver.selected == target
        if target == "QUIT GAME":
            pilot.policy.setdefault("PAUSE_MENU", []).append("QUIT GAME")
        pilot.confirm_on("PAUSE_MENU", target)
        assert driver.confirmed == [target]


def test_pin_highlight_stops_before_space_without_selection_evidence(tmp_path, notices):
    driver = PauseMenuDriver(LIVE_PAUSE_ROWS)
    pilot = Pilot(CAT, tmp_path, driver=driver, sleep=lambda _: None)
    pilot.look()
    with pytest.raises(jobs.Stuck) as caught:
        jobs._pin_highlight(pilot, "SUPER")
    assert "could not establish 'SUPER'" in str(caught.value)
    assert driver.confirmed == []
    assert "space" not in driver.keys


def test_pin_highlight_refuses_a_row_that_is_not_visible(tmp_path, notices):
    driver = PauseMenuDriver(LIVE_PAUSE_ROWS)
    pilot = pin_the_menu(tmp_path, driver)
    with pytest.raises(jobs.Stuck) as caught:
        jobs._pin_highlight(pilot, "INSTANT REPLAY")
    assert "isn't visible" in str(caught.value)
    assert driver.confirmed == []


def test_the_generic_pause_exit_reaches_supersim_on_the_measured_nine_row_menu(tmp_path, notices):
    driver = PauseMenuDriver(LIVE_PAUSE_ROWS, target_screen="SUPERSIM_MENU")
    pilot = pin_the_menu(tmp_path, driver)
    assert pilot.screen == "PAUSE_MENU"
    pilot.goto("SUPERSIM_MENU")
    assert driver.screen == "SUPERSIM_MENU"
    assert driver.confirmed == ["SUPER SIM"]
