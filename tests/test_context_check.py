"""tools/context_check.py: reading a session transcript and tripping at the over-context line."""

import json

from tools import context_check as cc


def entry(role, usage=None):
    message = {"role": role, "content": "x"}
    if usage:
        message["usage"] = usage
    return json.dumps({"type": role, "message": message})


def write_transcript(path, *lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def test_context_tokens_sums_prompt_buckets_only():
    assert (
        cc.context_tokens(
            {
                "input_tokens": 10,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 5,
                "output_tokens": 999,
            }
        )
        == 35
    )
    assert cc.context_tokens({}) == 0


def test_last_usage_skips_entries_without_usage_and_bad_json(tmp_path):
    p = write_transcript(
        tmp_path / "s.jsonl",
        entry("user"),
        entry("assistant", {"input_tokens": 10}),
        "{not json",
        entry("assistant", {"input_tokens": 40, "cache_read_input_tokens": 60}),
        entry("user"),
    )
    assert cc.context_tokens(cc.last_usage(p)) == 100


def test_report_trips_at_the_threshold():
    assert cc.report(600_000, 1_000_000, 60) == {
        "tokens": 600_000,
        "limit": 1_000_000,
        "threshold": 60,
        "percent": 60.0,
        "over": True,
    }
    assert cc.report(599_999, 1_000_000, 60)["over"] is False


def test_main_exits_2_over_the_line(tmp_path, capsys):
    p = write_transcript(
        tmp_path / "over.jsonl",
        entry("assistant", {"input_tokens": 500_000, "cache_read_input_tokens": 100_000}),
    )
    assert cc.main(["--transcript", str(p), "--limit", "1000000", "--threshold", "60"]) == 2
    out = capsys.readouterr().out
    assert "600,000 / 1,000,000" in out and "OVER 60%" in out


def test_main_exits_0_under_the_line(tmp_path, capsys):
    p = write_transcript(tmp_path / "under.jsonl", entry("assistant", {"input_tokens": 100}))
    assert cc.main(["--transcript", str(p)]) == 0
    assert "under 60%" in capsys.readouterr().out


def test_json_output(tmp_path, capsys):
    p = write_transcript(tmp_path / "j.jsonl", entry("assistant", {"input_tokens": 250_000}))
    assert cc.main(["--transcript", str(p), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tokens"] == 250_000 and result["percent"] == 25.0


def test_find_transcript_by_session_id(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "-home-x").mkdir(parents=True)
    f = write_transcript(projects / "-home-x" / "abc.jsonl", entry("assistant", {"input_tokens": 1}))
    monkeypatch.setattr(cc, "PROJECTS", projects)
    assert cc.find_transcript("abc") == f


def test_main_reports_a_missing_transcript(tmp_path, capsys):
    assert cc.main(["--transcript", str(tmp_path / "nope.jsonl")]) == 1
    assert "context_check:" in capsys.readouterr().err
