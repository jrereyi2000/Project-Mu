"""Classify merge conflicts as combinable (different lines) or same-line (JR decides).

Run inside a repo with a merge in progress. For each unmerged file it diffs the
merge base against each side (git's own hunks, -U0) and checks whether any base
line was changed or deleted by both sides. Both sides inserting at the same spot
counts as combinable (ours first, then theirs). Add/add and delete/modify
conflicts are always same-line.

    python3 .claude/skills/execute-plan/combine_conflicts.py [--write] [FILE ...]

Exit 0: every conflict is combinable (with --write the combined files are written;
review and `git add` them). Exit 1: at least one same-line conflict, nothing written.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def stage(path, n):
    r = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", "surrogateescape") if r.returncode == 0 else None


def edits(base, side):
    """[(start, end, new_lines)] over 0-indexed base lines; start == end is an insertion."""
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d, "base"), Path(d, "side")
        a.write_text(base, "utf-8", "surrogateescape")
        b.write_text(side, "utf-8", "surrogateescape")
        out = subprocess.run(["git", "diff", "--no-index", "--no-color", "-U0", str(a), str(b)],
                             capture_output=True, text=True).stdout
    result, cur = [], None
    for line in out.splitlines():
        m = HUNK.match(line)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            cur = [start, start + count, []] if count else [start, start, []]
            if count:
                cur[0] -= 1
                cur[1] -= 1
            result.append(cur)
        elif cur is not None and line.startswith("+") and not line.startswith("+++"):
            cur[2].append(line[1:] + "\n")
    return [tuple(e) for e in result]


def clash(a, b):
    if a[:2] == b[:2] and a[2] == b[2]:
        return False  # identical edit
    (s1, e1, _), (s2, e2, _) = a, b
    if s1 == e1 and s2 == e2:
        return False  # two insertions at one spot: keep both
    if s1 == e1:
        return s2 < s1 < e2
    if s2 == e2:
        return s1 < s2 < e1
    return s1 < e2 and s2 < e1


def combine(base, ours, theirs):
    lines = base.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # Insertions before replacements at the same base line; ours before theirs.
    merged = sorted([(e, 0) for e in ours] + [(e, 1) for e in theirs],
                    key=lambda x: (x[0][0], x[0][1] != x[0][0], x[1]))
    seen, out, pos = set(), [], 0
    for (s, e, new), _ in merged:
        key = (s, e, tuple(new))
        if key in seen:
            continue
        seen.add(key)
        out += lines[pos:max(s, pos)]
        out += new
        pos = max(pos, e)
    return "".join(out + lines[pos:])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--write", action="store_true", help="write combined files when all are combinable")
    args = ap.parse_args(argv)
    files = args.files or git("diff", "--name-only", "--diff-filter=U").split()
    if not files:
        print("no unmerged files")
        return 0
    bad, results = False, {}
    for f in files:
        base, ours, theirs = stage(f, 1), stage(f, 2), stage(f, 3)
        if base is None or ours is None or theirs is None:
            print(f"SAME-LINE {f}: add/add or delete/modify conflict")
            bad = True
            continue
        eo, et = edits(base, ours), edits(base, theirs)
        hits = [(a, b) for a in eo for b in et if clash(a, b)]
        if hits:
            bad = True
            for a, b in hits:
                print(f"SAME-LINE {f}: base lines {a[0] + 1}-{max(a[1], a[0] + 1)} (ours) "
                      f"vs {b[0] + 1}-{max(b[1], b[0] + 1)} (theirs)")
        else:
            print(f"COMBINABLE {f}: {len(eo)} ours / {len(et)} theirs hunks, no shared lines")
            results[f] = combine(base, eo, et)
    if bad:
        return 1
    if args.write:
        for f, text in results.items():
            Path(f).write_text(text, "utf-8", "surrogateescape")
            print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
