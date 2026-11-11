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
