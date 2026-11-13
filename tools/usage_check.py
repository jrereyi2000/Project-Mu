"""Claude plan-quota watchdog for auditor sessions (/audit; JR, 2026-11-10).

Reads the status line's usage file — ``data/usage/rate_limits.json`` on JR's primary account,
``data/usage/rate_limits_personal.json`` on the secondary one (``claude-personal``) — and reports how much of the
5-hour and weekly Claude limits is used. At 85% of the 5-hour window or 99% of the weekly one the auditor posts a
HANDOFF to the builders and signs off before the quota runs out.

    python3 -m tools.usage_check [--five-hour PCT] [--weekly PCT] [--max-age SECONDS] [--file PATH] [--json]

Exit 0 under both thresholds, 2 at or over either, 1 when the numbers are unknown: no file, older than --max-age
(default 600 s; the status line hasn't refreshed, e.g. no Claude session has replied lately), or a ``seen_at`` in
the future (more than a minute off this clock, so a date-typo or a clock-skewed writer cannot pass an old file off
as fresh). A window whose resets_at has passed counts as reset (0%).

The file follows the session's account (B-3, board M-0823): when ``CLAUDE_CONFIG_DIR`` is the personal config
directory (``MU_CLAUDE_PERSONAL_CONFIG_DIR``, default ``~/.claude-secondary``) the personal file is read, so an
auditor session on the secondary account measures its own quota. ``--file`` then ``MU_USAGE_FILE`` override that.

Env: MU_USAGE_FILE, MU_USAGE_PERSONAL_FILE, MU_CLAUDE_PERSONAL_CONFIG_DIR, MU_USAGE_5H_THRESHOLD (default 85),
MU_USAGE_WEEKLY_THRESHOLD (default 99).
The secondary Claude account writes its file through tools/usage_statusline_personal.sh;
tools/claude_account.py compares the two files.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_FILE = str(REPO_ROOT / "data" / "usage" / "rate_limits.json")
PERSONAL_FILE = os.environ.get(
    "MU_USAGE_PERSONAL_FILE", str(REPO_ROOT / "data" / "usage" / "rate_limits_personal.json"))
DEFAULT_5H = float(os.environ.get("MU_USAGE_5H_THRESHOLD", 85))
DEFAULT_WEEKLY = float(os.environ.get("MU_USAGE_WEEKLY_THRESHOLD", 99))
DEFAULT_MAX_AGE = 600
FUTURE_TOLERANCE = 60          # seconds a seen_at may run ahead of this clock before it counts as unknown


def account_file(config_dir=None, environ=None):
    """The usage file for the account this session runs on.

    Precedence: ``MU_USAGE_FILE`` → the personal file when ``CLAUDE_CONFIG_DIR`` is the personal config
    directory → the primary file. The wrapper must NOT export ``MU_USAGE_FILE`` to pick the account:
    ``claude_account.py`` reads that variable too, so it would hide the primary file from the chooser (B-3).
    """
    environ = os.environ if environ is None else environ
    override = environ.get("MU_USAGE_FILE")
    if override:
        return override
    config_dir = environ.get("CLAUDE_CONFIG_DIR") if config_dir is None else config_dir
    if config_dir:
        personal = environ.get("MU_CLAUDE_PERSONAL_CONFIG_DIR", "~/.claude-secondary")
        try:
            if Path(config_dir).expanduser().resolve() == Path(personal).expanduser().resolve():
                return environ.get("MU_USAGE_PERSONAL_FILE", PERSONAL_FILE)
        except OSError:
            pass
    return PRIMARY_FILE


def check(data, five_hour, weekly, max_age, now):
    seen = data.get("seen_at", 0)
    age = now - seen
    if age < -FUTURE_TOLERANCE:
        return 1, (f"usage: unknown (last seen {int(-age) // 60} min in the future; "
                   "the file or this clock is wrong)"), {}
    if age > max_age:
        return 1, f"usage: unknown (last seen {age // 60:.0f} min ago; no fresh status-line refresh)", {}
    out, over = {}, []
    for key, short, limit in (("five_hour", "5-hour", five_hour), ("seven_day", "weekly", weekly)):
        w = data.get(key)
        if not w:
            continue
        pct, resets = float(w.get("used_percentage", 0)), int(w.get("resets_at", 0))
        if resets and resets <= now:
            pct = 0.0
        out[key] = {"used_percentage": pct, "resets_at": resets, "threshold": limit}
        if pct >= limit:
            over.append(f"{short} {pct:.0f}% >= {limit:g}%")
    if not out:
        return 1, "usage: unknown (no 5-hour or weekly window in the file)", {}
    shown = []
    for key, short in (("five_hour", "5h"), ("seven_day", "wk")):
        if key in out:
            r = out[key]["resets_at"]
            at = datetime.fromtimestamp(r).astimezone().strftime("%a %H:%M %Z") if r else "?"
            shown.append(f"{short} {out[key]['used_percentage']:.0f}% (resets {at})")
    msg = "usage: " + " · ".join(shown)
    if over:
        return 2, msg + "\nOVER: " + "; ".join(over) + ": hand off now", out
    return 0, msg + " - under threshold", out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--five-hour", type=float, default=DEFAULT_5H, help="5-hour threshold, percent (default 85)")
    ap.add_argument("--weekly", type=float, default=DEFAULT_WEEKLY, help="weekly threshold, percent (default 99)")
    ap.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE, help="seconds before the file counts as stale")
    ap.add_argument("--file", default=None,
                    help="usage file (default: the one for this session's Claude account)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = a.file or account_file()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        code, msg, out = 1, f"usage: unknown (no readable {path}; is the status line set up?)", {}
    else:
        code, msg, out = check(data, a.five_hour, a.weekly, a.max_age, time.time())
    print(json.dumps({"status": code, "message": msg, "windows": out, "file": path}) if a.json else msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
