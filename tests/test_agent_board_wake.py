from tools.agent_board_wake import addressed_headers, deliver, save_state


def message(number, sender, recipient, body="hello"):
    return f"## M-{number:04d} · Today · from: gpt-6.1-sol ({sender}) · to: {recipient}\n**Kind:** NOTICE\n{body}\n"


def test_exact_routing_own_exclusion_and_duplicate_numbers():
    board = "\n".join([
        message(1, "other", "audit-3"), message(1, "other-2", "all"),
        message(2, "other", "codex"), message(3, "audit-3", "all"),
        message(4, "other", "audit-30"), message(5, "other", "opus"),
    ])
    assert len(addressed_headers(board, "audit-3")) == 3


def test_rotation_and_body_appends_do_not_replay():
    first = message(1, "other", "all")
    second = message(2, "other", "audit-3")
    seen = addressed_headers(first + second, "audit-3")
    assert set(addressed_headers(second + "more body", "audit-3")) <= set(seen)


def test_delivery_uses_argument_array_and_resume(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="Queued message", stderr="")
    monkeypatch.setattr("tools.agent_board_wake.subprocess.run", run)
    assert deliver("thread-id", "audit-3", ["header $(touch nope)"])
    assert calls[0][:4] == ["codex", "queue", "--thread", "thread-id"]
    assert calls[0][-1] == "--resume"
    assert "$(touch nope)" in calls[0][-2]


def test_queue_warning_is_not_claimed_as_success(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("tools.agent_board_wake.subprocess.run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="Queued", stderr="Warning: resume failed"))
    assert not deliver("thread-id", "audit-3", ["header"])


def test_state_roundtrip(tmp_path):
    import json
    state = tmp_path / "seen.json"
    save_state(state, {"b", "a"})
    assert json.loads(state.read_text()) == ["a", "b"]
