import os
import subprocess
import sys
from pathlib import Path

from tools.agent_board_inbox import model_inbox


def test_model_inboxes_match_explicit_recipients_and_ignore_broadcast_body():
    board = """# Agent Conversation
Board rules.

## M-0001 · Today · from: claude-opus-5-5 (audit) · to: GPT-6.1-Sol, Argon
**Kind:** HANDOFF · **Re:** —
Shared model work.

## M-0002 · Today · from: gpt-6.1-sol (build) · to: opus
**Kind:** QUESTION · **Re:** —
Review this.

## M-0003 · Today · from: gemini-4-argon-medium (other) · to: all
**Kind:** NOTICE · **Re:** —
Codex, Opus and DeepSeek appear in this broadcast.

## M-0004 · Today · from: gpt-6.1-sol (build) · to: codex-extra
**Kind:** NOTICE · **Re:** —
A different recipient.

## M-0005 · Today · from: gpt-6.1-sol (build) · to: DeepSeek, deepseek-flash, deepseek-v4.1-flash
**Kind:** HANDOFF · **Re:** —
One DeepSeek message addressed by several aliases.
"""
    for model, expected in (("codex", "M-0001"), ("opus", "M-0002"), ("argon", "M-0001"), ("deepseek", "M-0005")):
        rules, messages = model_inbox(board, model)
        assert "Board rules." in rules
        assert len(messages) == 1
        assert messages[0].startswith(f"## {expected}")


def test_hydrogen_inbox_matches_its_aliases_and_stays_out_of_argon():
    board = """# Agent Conversation
Board rules.

## M-0006 · Today · from: gpt-6.1-sol (audit) · to: Hydrogen, gemini-4-hydrogen-high
**Kind:** HANDOFF · **Re:** —
Review this batch.

## M-0007 · Today · from: gemini-4-hydrogen-high (hydrogen-audit) · to: argon
**Kind:** NOTICE · **Re:** —
A Hydrogen session addressing Argon.
"""
    rules, messages = model_inbox(board, "hydrogen")
    assert "Board rules." in rules
    assert [m.splitlines()[0] for m in messages] == [
        "## M-0006 · Today · from: gpt-6.1-sol (audit) · to: Hydrogen, gemini-4-hydrogen-high"
    ]
    _, argon_messages = model_inbox(board, "argon")
    assert [m.splitlines()[0] for m in argon_messages] == [
        "## M-0007 · Today · from: gemini-4-hydrogen-high (hydrogen-audit) · to: argon"
    ]


def test_model_inbox_skips_own_messages_and_keeps_duplicate_numbers():
    board = """## M-0001 · Today · from: gpt-6.1-sol (mine) · to: codex
**Kind:** NOTICE · **Re:** —
My own message.

## M-0001 · Today · from: gpt-6.1-sol (other) · to: codex
**Kind:** HANDOFF · **Re:** —
Another Codex session.

## M-0001 · Later · from: claude-opus-5-5 (audit) · to: codex
**Kind:** HANDOFF · **Re:** —
Same number from Opus.
"""
    _, messages = model_inbox(board, "codex", "mine")
    assert len(messages) == 2
    assert "Another Codex session." in messages[0]
    assert "Same number from Opus." in messages[1]


def test_muse_inbox_matches_every_muse_alias_and_ignores_deepseek():
    board = """# Agent Conversation
Board rules.

## M-0101 · Today · from: muse-ember-1.0 (muse-lab) · to: muse
**Kind:** NOTICE · **Re:** —
Short alias.

## M-0102 · Today · from: muse-spark-1.3-contributor (muse-lab) · to: Muse-Ember-1.0
**Kind:** HANDOFF · **Re:** —
Ember by its full model id.

## M-0103 · Today · from: claude-opus-5-5 (audit) · to: muse-spark-1.3-contributor
**Kind:** QUESTION · **Re:** —
Spark by its full model id.

## M-0104 · Today · from: claude-opus-5-5 (audit) · to: muse-ember-1.0, all
**Kind:** NOTICE · **Re:** —
Comma list with the broadcast recipient.

## M-0105 · Today · from: claude-opus-5-5 (audit) · to: deepseek-flash
**Kind:** NOTICE · **Re:** —
DeepSeek only.

## M-0106 · Today · from: claude-opus-5-5 (audit) · to: muse-extra
**Kind:** NOTICE · **Re:** —
A different recipient.
"""
    rules, messages = model_inbox(board, "muse")
    assert "Board rules." in rules
    assert [message.split("\n")[0].split(" · ")[0] for message in messages] == [
        "## M-0101",
        "## M-0102",
        "## M-0103",
        "## M-0104",
    ]

    _, deepseek_messages = model_inbox(board, "deepseek")
    assert len(deepseek_messages) == 1
    assert deepseek_messages[0].startswith("## M-0105")


def test_muse_inbox_label_filter_skips_own_messages():
    board = """## M-0201 · Today · from: muse-ember-1.0 (muse-lab) · to: muse
**Kind:** NOTICE · **Re:** —
My own message.

## M-0202 · Today · from: muse-ember-1.0 (other) · to: muse
**Kind:** HANDOFF · **Re:** —
Another Muse session.
"""
    _, messages = model_inbox(board, "muse", "muse-lab")
    assert len(messages) == 1
    assert "Another Muse session." in messages[0]


def test_cli_accepts_muse(tmp_path):
    board = tmp_path / "board.md"
    board.write_text(
        """# Agent Conversation
Board rules.

## M-0301 · Today · from: muse-spark-1.3-contributor (other) · to: muse
**Kind:** NOTICE · **Re:** —
Hello Muse.
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "tools.agent_board_inbox", "muse", "--label", "X"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "AGENT_BOARD": str(board)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Board rules." in result.stdout
    assert "Hello Muse." in result.stdout
