"""Choosing between JR's primary and personal Claude accounts by plan headroom (2026-11-12).

No network and no Claude session: each account is a small JSON file in the shape the status line
writes. ``tools.claude_account.choose`` is the only decision maker ``spawn_agent.sh`` uses.

The files are written fresh against the real clock (M-0823 B-2): ``choose`` and the CLI run on
``time.time()``, so a fixture pinned to 1_800_000_000 (2027-01-15) would look stale after that date
and silently change every verdict.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tools import claude_account


def write_usage(path: Path, five_hour: float | None, weekly: float | None = None,
                seen_at: float | None = None, now: float | None = None) -> Path:
    now = time.time() if now is None else now
    data = {"seen_at": now if seen_at is None else seen_at}
    if five_hour is not None:
        data["five_hour"] = {"used_percentage": five_hour, "resets_at": int(now) + 3600}
    if weekly is not None:
        data["seven_day"] = {"used_percentage": weekly, "resets_at": int(now) + 86400}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def both(tmp_path: Path, primary=(10, 10), personal=(20, 20), now=None):
    return (write_usage(tmp_path / "primary.json", *primary, now=now),
            write_usage(tmp_path / "personal.json", *personal, now=now))


def test_the_account_with_more_headroom_wins(tmp_path):
    primary, personal = both(tmp_path, (60, 50), (10, 10))
    picked, states = claude_account.choose(primary, personal)
    assert picked == "personal"
    assert states["personal"]["score"] < states["primary"]["score"]


def test_primary_wins_when_it_has_more_headroom(tmp_path):
    primary, personal = both(tmp_path, (10, 10), (60, 50))
    picked, _ = claude_account.choose(primary, personal)
    assert picked == "primary"


def test_an_over_primary_sends_the_spawn_to_personal(tmp_path):
    primary, personal = both(tmp_path, (90, 50), (10, 10))
    picked, _ = claude_account.choose(primary, personal)
    assert picked == "personal"


def test_an_over_personal_keeps_the_spawn_on_primary(tmp_path):
    primary, personal = both(tmp_path, (10, 10), (90, 50))
    picked, _ = claude_account.choose(primary, personal)
    assert picked == "primary"


def test_both_over_is_a_refusal_not_a_guess(tmp_path):
    primary, personal = both(tmp_path, (90, 50), (95, 50))
    with pytest.raises(claude_account.NoAccount) as caught:
        claude_account.choose(primary, personal)
    states = caught.value.args[0]
    assert states["primary"]["code"] == 2 and states["personal"]["code"] == 2


def test_a_missing_personal_file_falls_back_to_primary(tmp_path):
    primary = write_usage(tmp_path / "primary.json", 10, 10)
    picked, states = claude_account.choose(primary, tmp_path / "missing.json")
    assert picked == "primary"
    assert states["personal"]["code"] == 1


def test_a_missing_primary_file_uses_personal(tmp_path):
    personal = write_usage(tmp_path / "personal.json", 10, 10)
    picked, _ = claude_account.choose(tmp_path / "missing.json", personal)
    assert picked == "personal"


def test_both_missing_keeps_the_legacy_primary_default(tmp_path):
    picked, states = claude_account.choose(tmp_path / "a.json", tmp_path / "b.json")
    assert picked == "primary"
    assert states["primary"]["code"] == 1 and states["personal"]["code"] == 1


def test_a_stale_file_is_unknown_not_available(tmp_path):
    now = time.time()
    primary = write_usage(tmp_path / "primary.json", 10, 10, now=now)
    personal = write_usage(tmp_path / "personal.json", 10, 10, seen_at=now - 3600)
    picked, states = claude_account.choose(primary, personal, now=now)
    assert picked == "primary"
    assert states["personal"]["code"] == 1
    assert "last seen" in states["personal"]["reason"]


def test_a_future_seen_at_is_unknown_not_available(tmp_path):
    """M-0823 B-2: a clock-skewed or mis-dated file must not pass as fresh."""
    now = time.time()
    primary = write_usage(tmp_path / "primary.json", 10, 10, seen_at=now + 3600)
    personal = write_usage(tmp_path / "personal.json", 10, 10, now=now)
    picked, states = claude_account.choose(primary, personal, now=now)
    assert picked == "personal"
    assert states["primary"]["code"] == 1
    assert "future" in states["primary"]["reason"]


def test_the_cli_prints_the_chosen_account(tmp_path, capsys):
    primary, personal = both(tmp_path, (10, 10), (5, 5))
    code = claude_account.main(["choose", "--primary-file", str(primary),
                                "--personal-file", str(personal)])
    assert code == 0
    assert capsys.readouterr().out.strip() == "personal"


def test_the_cli_refuses_when_both_are_over(tmp_path, capsys):
    primary, personal = both(tmp_path, (90, 50), (95, 50))
    code = claude_account.main(["choose", "--primary-file", str(primary),
                                "--personal-file", str(personal)])
    assert code == 2
    captured = capsys.readouterr()
    assert "neither account" in captured.err
    assert captured.out == ""
