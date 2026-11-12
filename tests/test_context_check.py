"""tools/context_check.py: reading a session transcript or OpenCode session and tripping at the
over-context line."""

import json
import re
import sys
from pathlib import Path

from tools import context_check as cc

STUB = """#!{python}
import json, os, sys

with open(os.environ["MU_STUB_FIXTURES"]) as f:
    fixtures = json.load(f)
argv = sys.argv[1:]
if argv[:2] == ["session", "list"]:
    print(json.dumps(fixtures.get("list", [])))
    sys.exit(0)
if argv[:2] == ["session", "export"]:
    sid = argv[2] if len(argv) > 2 else None
    exports = fixtures.get("exports", {{}})
    if sid not in exports:
        print("no such session", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(exports[sid]))
    sys.exit(0)
print("unexpected arguments: %r" % (argv,), file=sys.stderr)
sys.exit(2)
"""


def entry(role, usage=None):
    message = {"role": role, "content": "x"}
    if usage:
        message["usage"] = usage
    return json.dumps({"type": role, "message": message})


def write_transcript(path, *lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def assistant(*, input=0, read=0, write=0, output=0, reasoning=0):
    return {
        "type": "assistant",
        "tokens": {
            "input": input,
            "output": output,
            "reasoning": reasoning,
            "cache": {"read": read, "write": write},
        },
    }


def opencode_stub(tmp_path, monkeypatch, exports, listing=None):
    """Install a stub `opencode` and point MU_OPENCODE_BIN at it."""
    script = tmp_path / "opencode"
    script.write_text(STUB.format(python=sys.executable))
    script.chmod(0o755)
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "exports": exports,
                "list": listing if listing is not None else [{"id": k} for k in exports],
            }
        )
    )
    monkeypatch.setenv("MU_OPENCODE_BIN", str(script))
    monkeypatch.setenv("MU_STUB_FIXTURES", str(fixtures))
    # An empty ~/.claude/projects, so auto mode can't find this machine's own transcripts and
    # pick Claude through the directory hint.
    empty_projects = tmp_path / "projects-empty"
    empty_projects.mkdir(exist_ok=True)
    monkeypatch.setattr(cc, "PROJECTS", empty_projects)
    monkeypatch.delenv("MU_OPENCODE_SESSION", raising=False)
    monkeypatch.delenv("MU_AGENT_HARNESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    return script


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


# --- OpenCode sessions -------------------------------------------------------------------------


def test_opencode_context_sums_input_and_cache_only():
    assert cc.opencode_context_tokens(
        {"input": 5, "cache": {"read": 7, "write": 3}, "output": 999, "reasoning": 500}
    ) == 15
    assert cc.opencode_context_tokens({}) == 0
    assert cc.opencode_context_tokens({"input": 4, "cache": None}) == 4


def test_last_opencode_tokens_takes_the_newest_assistant_usage():
    export = {
        "messages": [
            {"type": "user", "tokens": {"input": 1}},
            assistant(input=10),
            {"type": "assistant", "content": "no usage here"},
            assistant(input=20, read=5, write=5),
            {"type": "user"},
        ]
    }
    assert cc.opencode_context_tokens(cc.last_opencode_tokens(export)) == 30
    assert cc.last_opencode_tokens({"messages": [{"type": "user"}]}) is None


def test_mu_opencode_session_selects_that_session(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {
            "ses_one": {"messages": [assistant(input=100, read=20, write=5)]},
            "ses_two": {"messages": [assistant(input=999_999)]},
        },
        listing=[{"id": "ses_two"}, {"id": "ses_one"}],
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_one")

    assert cc.main(["--limit", "1000", "--threshold", "60"]) == 0
    captured = capsys.readouterr()
    assert "125 / 1,000 tokens" in captured.out
    assert "(opencode ses_one)" in captured.out
    assert "ses_two" not in captured.out
    assert captured.err == ""


def test_session_flag_overrides_the_env_session(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {
            "ses_one": {"messages": [assistant(input=100)]},
            "ses_two": {"messages": [assistant(input=42, read=8)]},
        },
        listing=[{"id": "ses_one"}],
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_one")

    assert cc.main(["--session", "ses_two", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tokens"] == 50
    assert result["source"] == "opencode ses_two"
    assert result["harness"] == "opencode"


def test_newest_session_fallback_only_without_a_named_session(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {
            "ses_new": {"messages": [assistant(input=1_000)]},
            "ses_old": {"messages": [assistant(input=7_000)]},
        },
        listing=[{"id": "ses_new"}, {"id": "ses_old"}],
    )

    assert cc.main(["--limit", "10000"]) == 0
    captured = capsys.readouterr()
    assert "1,000 / 10,000 tokens" in captured.out
    assert "(opencode ses_new)" in captured.out
    assert "warning:" in captured.err and "ses_new" in captured.err


def test_opencode_over_the_threshold_exits_2(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_x": {"messages": [assistant(input=500_000, read=90_000, write=10_000)]}},
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_x")

    assert cc.main(["--limit", "1000000", "--threshold", "60"]) == 2
    out = capsys.readouterr().out
    assert "600,000 / 1,000,000" in out
    assert "OVER 60%: hand over now" in out
    assert "(opencode ses_x)" in out


def test_opencode_session_without_assistant_usage_exits_1(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_fresh": {"info": {"tokens": {"input": 500}}, "messages": [{"type": "user"}]}},
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_fresh")

    assert cc.main([]) == 1
    assert "no usage yet" in capsys.readouterr().err


def test_transcript_still_wins_over_opencode(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path, monkeypatch, {"ses_one": {"messages": [assistant(input=999_999)]}}
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_one")
    p = write_transcript(tmp_path / "t.jsonl", entry("assistant", {"input_tokens": 10}))

    assert cc.main(["--transcript", str(p), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tokens"] == 10
    assert result["harness"] == "claude"
    assert result["source"] == str(p)
    assert result["transcript"] == str(p)


def test_harness_claude_ignores_opencode(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path, monkeypatch, {"ses_one": {"messages": [assistant(input=999_999)]}}
    )
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_one")
    monkeypatch.setenv("MU_AGENT_HARNESS", "opencode")
    projects = tmp_path / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    (projects / slug).mkdir(parents=True)
    p = write_transcript(
        projects / slug / "cwd.jsonl", entry("assistant", {"input_tokens": 321})
    )
    monkeypatch.setattr(cc, "PROJECTS", projects)

    assert cc.main(["--harness", "claude", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["harness"] == "claude"
    assert result["tokens"] == 321
    assert result["source"] == str(p)


def test_harness_env_selects_opencode_without_a_named_session(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_env": {"messages": [assistant(input=2_000)]}},
        listing=[{"id": "ses_env"}],
    )
    monkeypatch.setenv("MU_AGENT_HARNESS", "opencode")

    assert cc.main(["--limit", "10000", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["tokens"] == 2_000
    assert result["source"] == "opencode ses_env"
    assert "warning:" in captured.err


# --- auto must not hijack a Claude Code session (R3 F1) ----------------------------------------


def claude_project(tmp_path, monkeypatch, session_id, tokens):
    projects = tmp_path / "projects"
    (projects / "-home-x").mkdir(parents=True)
    path = write_transcript(
        projects / "-home-x" / f"{session_id}.jsonl",
        entry("assistant", {"input_tokens": tokens}),
    )
    monkeypatch.setattr(cc, "PROJECTS", projects)
    return path


def test_auto_prefers_a_claude_code_session_over_an_opencode_binary(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_other": {"messages": [assistant(input=99_999)]}},
        listing=[{"id": "ses_other"}],
    )
    transcript = claude_project(tmp_path, monkeypatch, "claude-sid-123", 111)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sid-123")

    assert cc.main(["--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["harness"] == "claude"
    assert result["tokens"] == 111
    assert result["source"] == str(transcript)
    assert captured.err == ""


def test_auto_uses_this_directories_transcript_without_a_session_id(tmp_path, monkeypatch, capsys):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_other": {"messages": [assistant(input=99_999)]}},
        listing=[{"id": "ses_other"}],
    )
    projects = tmp_path / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    (projects / slug).mkdir(parents=True)
    transcript = write_transcript(
        projects / slug / "cwd.jsonl", entry("assistant", {"input_tokens": 222})
    )
    monkeypatch.setattr(cc, "PROJECTS", projects)
    monkeypatch.setenv("CLAUDECODE", "1")

    assert cc.main(["--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["harness"] == "claude"
    assert result["tokens"] == 222
    assert result["source"] == str(transcript)
    assert captured.err == ""


def test_a_named_opencode_session_still_beats_a_claude_code_environment(
    tmp_path, monkeypatch, capsys
):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {
            "ses_one": {"messages": [assistant(input=100, read=20, write=5)]},
            "ses_other": {"messages": [assistant(input=99_999)]},
        },
        listing=[{"id": "ses_other"}],
    )
    claude_project(tmp_path, monkeypatch, "claude-sid-123", 111)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sid-123")
    monkeypatch.setenv("MU_OPENCODE_SESSION", "ses_one")

    assert cc.main(["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["harness"] == "opencode"
    assert result["tokens"] == 125
    assert result["source"] == "opencode ses_one"


def test_mu_agent_harness_opencode_beats_a_claude_code_environment(
    tmp_path, monkeypatch, capsys
):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {"ses_env": {"messages": [assistant(input=2_000)]}},
        listing=[{"id": "ses_env"}],
    )
    claude_project(tmp_path, monkeypatch, "claude-sid-123", 111)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-sid-123")
    monkeypatch.setenv("MU_AGENT_HARNESS", "opencode")

    assert cc.main(["--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["harness"] == "opencode"
    assert result["tokens"] == 2_000


def test_explicit_harness_opencode_accepts_a_session_id_without_the_ses_prefix(
    tmp_path, monkeypatch, capsys
):
    opencode_stub(
        tmp_path,
        monkeypatch,
        {
            "custom-id": {"messages": [assistant(input=42)]},
            "ses_other": {"messages": [assistant(input=99_999)]},
        },
        listing=[{"id": "ses_other"}],
    )

    assert cc.main(["--harness", "opencode", "--session", "custom-id", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["tokens"] == 42
    assert result["source"] == "opencode custom-id"
    assert captured.err == ""
