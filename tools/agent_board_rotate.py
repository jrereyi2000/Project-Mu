"""Archive old, closed messages from AGENT_CONVERSATION.md so new sessions don't read the whole board.

Run by an auditor session (/audit) while no build wave is active (JR, 2026-11-10). Messages move verbatim to
docs/agent_board_archive/ (git-ignored, like the board) and keep their M- numbers. What stays on the board:

- the header (everything before the first message);
- the last --keep-last messages (default 40), by position in the file, not by timestamp;
- every CLAIM its owner hasn't released: no later message from the same label with RELEASE in its Kind that cites it,
  and no later SIGN-OFF from that label;
- every HANDOFF or QUESTION no later message from another label cites;
- anything named with --keep.

The open-item rules are deliberately loose in the "keep" direction: a wrongly kept message costs a few lines, a wrongly
archived open claim lets someone edit a claimed file. Dry run by default; --apply writes.

    python3 -m tools.agent_board_rotate                  # show what would move and what stays open
    python3 -m tools.agent_board_rotate --apply [--keep M-0187 ...] [--keep-last 40]

Appends that land while it runs are kept: the board is replaced by rename, and any bytes another agent appended to the
old file before the rename are copied onto the new one.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime

BOARD = "/home/jrereyi/Project-Mu/AGENT_CONVERSATION.md"
ARCHIVE_DIR = "/home/jrereyi/Project-Mu/docs/agent_board_archive"

# The sender is the whole "model (label)" pair: claude-opus-5-5 (audit) and gemini (audit) are different sessions.
HEADER_RE = re.compile(r"^## (M-\d{4}) · [^\n]*?· from: ([^(\n]*\([^)\n]+\))", re.M)
MSG_START_RE = re.compile(r"^## M-\d{4} ", re.M)
KIND_RE = re.compile(r"^\*\*Kind:\*\*([^\n]*)", re.M)
REF_RE = re.compile(r"M-(\d{4})")
# "M-0245 … M-0252", "M-0245..M-0252", "M-0245–M-0252", "M-0245 to M-0252"
RANGE_RE = re.compile(r"M-(\d{4})\s*(?:…|\.\.\.?|–|—|-|to)\s*M-(\d{4})")


@dataclass
class Message:
    num: str
    label: str
    kind: str
    text: str
    refs: set = field(default_factory=set)


def split_board(text):
    starts = [m.start() for m in MSG_START_RE.finditer(text)]
    if not starts:
        return text, []
    header, msgs = text[: starts[0]], []
    for i, s in enumerate(starts):
        chunk = text[s : starts[i + 1] if i + 1 < len(starts) else len(text)]
        h = HEADER_RE.match(chunk)
        num = chunk[3:9]
        label = h.group(2).strip() if h else "?"
        k = KIND_RE.search(chunk)
        refs = {f"M-{n}" for n in REF_RE.findall(chunk)}
        for a, b in RANGE_RE.findall(chunk):
            if int(a) < int(b) and int(b) - int(a) < 200:
                refs |= {f"M-{n:04d}" for n in range(int(a), int(b) + 1)}
        refs.discard(num)
        msgs.append(Message(num, label, (k.group(1) if k else "").upper(), chunk, refs))
    return header, msgs


def open_items(msgs):
    """{index: reason} for CLAIMs, HANDOFFs and QUESTIONs that still look open."""
    reasons = {}
    for i, m in enumerate(msgs):
        later = msgs[i + 1 :]
        if "CLAIM" in m.kind:
            released = any(
                n.label == m.label and ("SIGN-OFF" in n.kind or ("RELEASE" in n.kind and m.num in n.refs))
                for n in later
            )
            if not released:
                reasons[i] = f"CLAIM by {m.label}, not released"
        if "HANDOFF" in m.kind or "QUESTION" in m.kind:
            if not any(n.label != m.label and m.num in n.refs for n in later):
                reasons[i] = f"{'HANDOFF' if 'HANDOFF' in m.kind else 'QUESTION'} from {m.label}, no reply cites it"
    return reasons


def plan(msgs, keep_last, keep):
    reasons = open_items(msgs)
    for i, m in enumerate(msgs):
        if m.num in keep:
            reasons.setdefault(i, "--keep")
    tail = set(range(max(0, len(msgs) - keep_last), len(msgs)))
    stay = sorted(tail | set(reasons))
    move = [i for i in range(len(msgs)) if i not in tail and i not in reasons]
    return stay, move, reasons


def first_line(m):
    return m.text.split("\n", 1)[0][3:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the archive and the trimmed board (default: dry run)")
    ap.add_argument("--keep-last", type=int, default=40, help="always keep this many of the newest messages")
    ap.add_argument("--keep", nargs="*", default=[], help="extra message numbers to keep, e.g. M-0187")
    ap.add_argument("--board", default=BOARD)
    ap.add_argument("--archive-dir", default=ARCHIVE_DIR)
    a = ap.parse_args(argv)

    fd = os.open(a.board, os.O_RDONLY)
    with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as f:
        text = f.read()
    read_len = len(text.encode("utf-8"))
    header, msgs = split_board(text)
    stay, move, reasons = plan(msgs, a.keep_last, set(a.keep))

    older_open = [i for i in sorted(reasons) if i < len(msgs) - a.keep_last]
    print(f"{len(msgs)} messages: {len(move)} to archive, {len(stay)} stay "
          f"({len(stay) - len(older_open)} newest + {len(older_open)} older ones still open).")
    print("\nStaying as open (check these; pass --keep for anything else still live):")
    for i in older_open:
        print(f"  {first_line(msgs[i])}\n      -> {reasons[i]}")
    if not move:
        print("\nNothing to archive.")
        os.close(fd)
        return 0
    lo, hi = msgs[move[0]].num, msgs[move[-1]].num
    out = os.path.join(a.archive_dir, f"AGENT_CONVERSATION_{lo}_to_{hi}.md")
    print(f"\nArchive: {out}")
    if not a.apply:
        print("Dry run; nothing written. Re-run with --apply.")
        os.close(fd)
        return 0

    os.makedirs(a.archive_dir, exist_ok=True)
    if os.path.exists(out):
        sys.exit(f"{out} already exists; refusing to overwrite")
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    arch = (f"# Agent board archive: {lo} to {hi}\n\nMoved verbatim from {a.board} on {stamp} by "
            f"tools/agent_board_rotate.py. Messages that were still open, and the newest ones, stayed on the board, "
            f"so a few numbers in this range are there instead.\n\n---\n\n")
    arch += "".join(msgs[i].text for i in move)
    new = header + "".join(msgs[i].text for i in stay)
    # Nothing lost: every message is in exactly one of the two.
    assert sorted(move + stay) == list(range(len(msgs)))
    assert len(arch) > len("".join(msgs[i].text for i in move)) and new.startswith(header)

    with open(out, "w", encoding="utf-8") as f:
        f.write(arch)
    tmp = a.board + ".rotate-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
        os.lseek(fd, read_len, os.SEEK_SET)
        late = os.read(fd, 1 << 24)
        if late:
            f.write(late.decode("utf-8"))
    os.replace(tmp, a.board)
    # A writer that opened the old file just before the rename lands in the old inode; copy that across too.
    os.lseek(fd, read_len + len(late), os.SEEK_SET)
    later = os.read(fd, 1 << 24)
    os.close(fd)
    if later:
        with open(a.board, "a", encoding="utf-8") as f:
            f.write(later.decode("utf-8"))
    print(f"Done: archived {len(move)} messages; the board kept {len(stay)}"
          f"{' plus late appends' if late or later else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
