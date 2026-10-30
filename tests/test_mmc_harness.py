"""tools/mod_builder/harness.py against a fake MmcHarness (a Python script printing JSON).

Offline: nothing here runs the real harness or touches the game.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    try:
        from tools.mod_builder import harness  # type: ignore
        return harness
    except ImportError:
        # The package __init__ arrives with another unit; load the module by file path.
        spec = importlib.util.spec_from_file_location("mu_harness", ROOT / "tools" / "mod_builder" / "harness.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


harness = _load_harness()


FAKE = textwrap.dedent("""\
    #!{python}
    import json, os, sys, time
    args = sys.argv[1:]
    mode = os.environ.get("FAKE_MODE", "ok")
    sys.stderr.write("fake harness log line\\n")
    if mode == "ok":
        print("[native noise without braces]")
        print(json.dumps({{"ok": True, "argv": args, "cwd": os.getcwd()}}))
    elif mode == "noisy_prefix":
        print("[Using DirectCompute]" + json.dumps({{"ok": True, "argv": args}}))
    elif mode == "error":
        print(json.dumps({{"ok": False, "error": "no ebx asset named x"}}))
        sys.exit(1)
    elif mode == "ok_but_exit":
        print(json.dumps({{"ok": True}}))
        sys.exit(3)
    elif mode == "garbage":
        print("not json at all")
    elif mode == "sleep":
        time.sleep(30)
        print(json.dumps({{"ok": True}}))
""")


@pytest.fixture
def fake_exe(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "MmcHarness.exe"
    exe.parent.mkdir()
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("MU_MMC_HARNESS", str(exe))
    monkeypatch.setenv("FAKE_MODE", "ok")
    return exe


@pytest.fixture
def fake_wslpath(monkeypatch):
    """Deterministic path conversion: /x/y -> W:\\x\\y (records what was converted)."""
    seen = []

    def conv(p):
        s = os.fspath(p)
        seen.append(s)
        if harness._looks_windows(s):
            return s
        return "W:" + str(Path(s).absolute()).replace("/", "\\")

    monkeypatch.setattr(harness, "to_windows_path", conv)
    return seen


# ---------------------------------------------------------------- path conversion

def test_convert_args_converts_only_path_positions(fake_wslpath):
    asset = "contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline"
    out = harness.convert_args(["import-texture", "/b/p.fbproject", asset, "/b/in.dds"])
    assert out == ["import-texture", "W:\\b\\p.fbproject", asset, "W:\\b\\in.dds"]

    out = harness.convert_args(["export-texture", asset, "/b/out.png", "--project", "/b/p.fbproject"])
    assert out == ["export-texture", asset, "W:\\b\\out.png", "--project", "W:\\b\\p.fbproject"]

    out = harness.convert_args(["ebx-set", "/b/p.fbproject", "a/b", "Field.x", "#FF69B4"])
    assert out == ["ebx-set", "W:\\b\\p.fbproject", "a/b", "Field.x", "#FF69B4"]


def test_convert_args_option_values_are_not_positional(fake_wslpath):
    out = harness.convert_args(["export-mod", "/b/p.fbproject", "--title", "/not/a/path", "/b/Mu_X.fbmod",
                                "--author", "JR (Project Mu)", "--version", "1.0"])
    assert out == ["export-mod", "W:\\b\\p.fbproject", "--title", "/not/a/path", "W:\\b\\Mu_X.fbmod",
                   "--author", "JR (Project Mu)", "--version", "1.0"]
    out = harness.convert_args(["find", "/footballcharacter/*jaguars*", "--kind", "ebx", "--limit", 5])
    assert out == ["find", "/footballcharacter/*jaguars*", "--kind", "ebx", "--limit", "5"]


def test_convert_args_pathlike_anywhere(fake_wslpath):
    out = harness.convert_args(["find", Path("/x/y")])
    assert out == ["find", "W:\\x\\y"]


def test_windows_paths_pass_through():
    assert harness.to_windows_path(r"C:\Madden27Modding\x.png") == r"C:\Madden27Modding\x.png"
    assert harness.to_windows_path(r"\\wsl.localhost\Ubuntu\home\x") == r"\\wsl.localhost\Ubuntu\home\x"


@pytest.mark.skipif(shutil.which("wslpath") is None, reason="needs WSL's wslpath")
def test_real_wslpath_handles_missing_files(tmp_path):
    target = tmp_path / "not_yet" / "out.png"
    win = harness.to_windows_path(target)
    parent = harness.to_windows_path(tmp_path)
    assert win == parent.rstrip("\\") + "\\not_yet\\out.png"


# ---------------------------------------------------------------- run

def test_run_ok_returns_json_and_converts_paths(fake_exe, fake_wslpath):
    r = harness.run(["export-texture", "some/asset", "/tmp/out.png"])
    assert r["ok"] is True
    assert r["argv"] == ["export-texture", "some/asset", "W:\\tmp\\out.png"]
    assert Path(r["cwd"]).resolve() == fake_exe.parent.resolve()


def test_run_tolerates_native_prefix(fake_exe, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "noisy_prefix")
    r = harness.run(["info"])
    assert r == {"ok": True, "argv": ["info"]}


def test_run_ok_false_raises_with_message(fake_exe, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(harness.HarnessError) as ei:
        harness.run(["ebx-dump", "x", "C:\\o.json"])
    assert "no ebx asset named x" in str(ei.value)
    assert ei.value.result == {"ok": False, "error": "no ebx asset named x"}
    assert "fake harness log line" in ei.value.stderr


def test_run_nonzero_exit_raises_even_if_ok(fake_exe, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok_but_exit")
    with pytest.raises(harness.HarnessError, match="exited 3"):
        harness.run(["info"])


def test_run_non_json_raises(fake_exe, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    with pytest.raises(harness.HarnessError, match="no JSON"):
        harness.run(["info"])


def test_run_timeout_raises(fake_exe, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    with pytest.raises(harness.HarnessError, match="timed out"):
        harness.run(["info"], timeout=1)


def test_run_missing_exe_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_MMC_HARNESS", str(tmp_path / "nope" / "MmcHarness.exe"))
    with pytest.raises(harness.HarnessError, match="not found"):
        harness.run(["info"])


def test_default_exe_path(monkeypatch):
    monkeypatch.delenv("MU_MMC_HARNESS", raising=False)
    assert harness.harness_path() == "/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe"


def test_harness_module_is_self_contained():
    src = (ROOT / "tools" / "mod_builder" / "harness.py").read_text()
    assert "from tools.mod_builder" not in src and "from ." not in src
    for banned in ("FrostyModCryptor", "FrostyModKeyProvider", "GetResourceData"):
        cs = (ROOT / "tools" / "mmc_harness" / "MmcHarness.cs").read_text()
        # Only the warning comment may name them; no code calls them.
        code = "\n".join(ln for ln in cs.splitlines() if not ln.lstrip().startswith("//"))
        assert banned not in code
