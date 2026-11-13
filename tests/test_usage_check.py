"""tools/usage_statusline.py records the Claude quota; tools/usage_check.py judges it."""

import io
import json
import time

from tools import usage_check, usage_statusline

NOW = 1_800_000_000


def win(pct, resets=NOW + 3600):
    return {"used_percentage": pct, "resets_at": resets}


def test_statusline_line():
    data = {"model": {"display_name": "Opus 5.5"}, "context_window": {"used_percentage": 12.4},
            "rate_limits": {"five_hour": win(42.0), "seven_day": win(61.0)}}
    assert usage_statusline.line(data) == "Opus 5.5 · ctx 12% · 5h 42% · wk 61%"


def test_record_writes_windows_and_skips_empty(tmp_path):
    f = tmp_path / "usage" / "rl.json"
    assert not usage_statusline.record({}, str(f), NOW)
    assert not f.exists()
    assert usage_statusline.record({"five_hour": win(5), "seven_day": None}, str(f), NOW)
    assert json.loads(f.read_text()) == {"seen_at": NOW, "five_hour": win(5)}


def test_main_survives_bad_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    usage_statusline.main()
    assert capsys.readouterr().out.strip() == "Claude"


def check(data, now=NOW):
    return usage_check.check(data, 98, 99, 600, now)


def test_thresholds():
    assert check({"seen_at": NOW, "five_hour": win(97.9), "seven_day": win(98.9)})[0] == 0
    code, msg, _ = check({"seen_at": NOW, "five_hour": win(98), "seven_day": win(50)})
    assert code == 2 and "5-hour 98% >= 98%" in msg
    code, msg, _ = check({"seen_at": NOW, "five_hour": win(10), "seven_day": win(99.2)})
    assert code == 2 and "weekly 99% >= 99%" in msg


def test_stale_or_empty_is_unknown():
    assert check({"seen_at": NOW - 601, "five_hour": win(10)})[0] == 1
    assert check({"seen_at": NOW})[0] == 1


def test_a_future_seen_at_is_unknown_not_fresh():
    """M-0823 B-2: a seen_at ahead of the clock used to pass the age check as fresh."""
    code, msg, _ = check({"seen_at": NOW + 3600, "five_hour": win(10)})
    assert code == 1 and "in the future" in msg
    # A small lead from the writer's clock is tolerated.
    assert check({"seen_at": NOW + 5, "five_hour": win(10)})[0] == 0


def test_the_usage_file_follows_the_session_account(monkeypatch, tmp_path):
    """M-0823 B-3: a session on the personal account must read the personal file, not the primary one."""
    personal_dir = tmp_path / "secondary"
    personal_file = tmp_path / "rate_limits_personal.json"
    monkeypatch.setenv("MU_CLAUDE_PERSONAL_CONFIG_DIR", str(personal_dir))
    monkeypatch.setenv("MU_USAGE_PERSONAL_FILE", str(personal_file))
    monkeypatch.delenv("MU_USAGE_FILE", raising=False)

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(personal_dir))
    assert usage_check.account_file() == str(personal_file)
    # The same directory reached through a symlink is still the personal account.
    link = tmp_path / "link"
    link.symlink_to(personal_dir)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link))
    assert usage_check.account_file() == str(personal_file)

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "primary"))
    assert usage_check.account_file() == usage_check.PRIMARY_FILE
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert usage_check.account_file() == usage_check.PRIMARY_FILE
    # An explicit MU_USAGE_FILE still wins (the status line and the tests rely on it).
    monkeypatch.setenv("MU_USAGE_FILE", str(tmp_path / "explicit.json"))
    assert usage_check.account_file() == str(tmp_path / "explicit.json")


def test_the_cli_reads_the_personal_file_for_a_personal_config_dir(monkeypatch, tmp_path, capsys):
    personal = tmp_path / "personal.json"
    usage_statusline.record({"five_hour": win(10, time.time() + 600)}, str(personal))
    monkeypatch.setenv("MU_CLAUDE_PERSONAL_CONFIG_DIR", str(tmp_path / "secondary"))
    monkeypatch.setenv("MU_USAGE_PERSONAL_FILE", str(personal))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "secondary"))
    monkeypatch.delenv("MU_USAGE_FILE", raising=False)

    assert usage_check.main([]) == 0
    assert "5h 10%" in capsys.readouterr().out


def test_passed_reset_counts_as_zero():
    code, _, out = check({"seen_at": NOW, "five_hour": win(99.5, resets=NOW - 1)})
    assert code == 0 and out["five_hour"]["used_percentage"] == 0.0


def test_cli_missing_file(tmp_path, capsys):
    assert usage_check.main(["--file", str(tmp_path / "none.json")]) == 1
    assert "unknown" in capsys.readouterr().out


def test_cli_end_to_end(tmp_path, capsys):
    f = tmp_path / "rl.json"
    usage_statusline.record({"five_hour": win(98.5, time.time() + 600), "seven_day": win(40, time.time() + 9000)},
                            str(f))
    assert usage_check.main(["--file", str(f)]) == 2
    assert "OVER" in capsys.readouterr().out
