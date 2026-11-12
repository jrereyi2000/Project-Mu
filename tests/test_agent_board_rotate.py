"""tools/agent_board_rotate.py: what stays on the board, and that no message is lost."""

from tools import agent_board_rotate as rot

HEADER = "# Agent Conversation\n\nrules\n\n---\n\n"


def msg(num, sender, kind, body="", to="all"):
    return f"## M-{num:04d} · 2026-11-10 12:00 EST · from: {sender} · to: {to}\n**Kind:** {kind} · **Re:** —\n{body}\n\n"


def board(*msgs):
    return HEADER + "".join(msgs)


def run(tmp_path, text, *args):
    b = tmp_path / "board.md"
    b.write_text(text)
    arch = tmp_path / "arch"
    rot.main(["--board", str(b), "--archive-dir", str(arch), *args])
    archived = "".join(p.read_text() for p in arch.glob("*.md")) if arch.exists() else ""
    return b.read_text(), archived


def nums(text):
    return [m.num for m in rot.split_board(text)[1]]


def test_open_items_stay_and_closed_ones_move(tmp_path):
    text = board(
        msg(1, "a (x)", "CLAIM", "files: f.py"),                     # released by M-0002
        msg(2, "a (x)", "RELEASE", "done with M-0001"),
        msg(3, "b (y)", "CLAIM", "files: g.py"),                     # never released
        msg(4, "c (z)", "HANDOFF", "fix this"),                      # nobody answered
        msg(5, "c (z)", "HANDOFF", "and this"),                      # answered by M-0006
        msg(6, "d (w)", "NOTICE", "fixed M-0005"),
        msg(7, "e (v)", "CLAIM"),                                    # closed by e's sign-off
        msg(8, "e (v)", "NOTICE / SIGN-OFF"),
        msg(9, "f (u)", "QUESTION", "?"),                            # only f itself cites it
        msg(10, "f (u)", "NOTICE", "still waiting on M-0009"),
        msg(11, "g (t)", "NOTICE"),
    )
    live, archived = run(tmp_path, text, "--apply", "--keep-last", "1")
    assert nums(live) == ["M-0003", "M-0004", "M-0009", "M-0011"]
    assert nums(archived) == ["M-0001", "M-0002", "M-0005", "M-0006", "M-0007", "M-0008", "M-0010"]
    assert live.startswith(HEADER)


def test_same_label_on_another_model_is_another_session(tmp_path):
    # gemini (audit)'s handoff answered by claude-opus-5-5 (audit) counts as answered.
    text = board(msg(1, "gemini (audit)", "HANDOFF"), msg(2, "claude-opus-5-5 (audit)", "ANSWER", "re M-0001"),
                 msg(3, "x (y)", "NOTICE"))
    live, _ = run(tmp_path, text, "--apply", "--keep-last", "1")
    assert nums(live) == ["M-0003"]


def test_range_reference_answers_handoffs_inside_it(tmp_path):
    text = board(msg(1, "a (x)", "HANDOFF"), msg(2, "a (x)", "HANDOFF"),
                 msg(3, "b (y)", "NOTICE", "**Re:** M-0001 … M-0002"), msg(4, "z (z)", "NOTICE"))
    live, _ = run(tmp_path, text, "--apply", "--keep-last", "1")
    assert nums(live) == ["M-0004"]


def test_keep_flag_and_dry_run(tmp_path):
    text = board(msg(1, "a (x)", "NOTICE"), msg(2, "a (x)", "NOTICE"), msg(3, "a (x)", "NOTICE"))
    live, archived = run(tmp_path, text, "--keep-last", "1")
    assert live == text and archived == ""
    live, archived = run(tmp_path, text, "--apply", "--keep-last", "1", "--keep", "M-0001")
    assert nums(live) == ["M-0001", "M-0003"] and nums(archived) == ["M-0002"]


def test_a_claim_named_in_the_reference_does_not_make_a_release_a_claim(tmp_path):
    # Real headers put the Re: on the Kind line: "**Kind:** NOTICE / RELEASE · **Re:** M-0458 (CLAIM)".
    # The Kind is the release; the word CLAIM belongs to the Re: text, so nothing here is an open claim.
    text = board(
        msg(1, "a (x)", "CLAIM", "files: f.py"),
        msg(2, "a (x)", "NOTICE / RELEASE · **Re:** M-0001 (CLAIM)", "released M-0001"),
        msg(3, "z (z)", "NOTICE"),
    )
    live, archived = run(tmp_path, text, "--apply", "--keep-last", "1")
    assert nums(live) == ["M-0003"]
    assert nums(archived) == ["M-0001", "M-0002"]


def test_indented_example_in_header_is_not_a_message(tmp_path):
    header = HEADER.replace("rules", "   ## M-0002 · example · from: x (y) · to: all")
    text = header + msg(1, "a (x)", "NOTICE") + msg(2, "a (x)", "NOTICE")
    head, msgs = rot.split_board(text)
    assert head == header and [m.num for m in msgs] == ["M-0001", "M-0002"]
