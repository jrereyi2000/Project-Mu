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
