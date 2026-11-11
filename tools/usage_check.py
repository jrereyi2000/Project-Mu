"""Claude plan-quota watchdog for auditor sessions (/audit; JR, 2026-11-10).

Reads data/usage/rate_limits.json, which tools/usage_statusline.py writes from Claude Code's status line, and reports
how much of the 5-hour and weekly Claude limits is used. At 85% of the 5-hour window or 99% of the weekly one the
auditor posts a HANDOFF to the builders and signs off before the quota runs out.

    python3 -m tools.usage_check [--five-hour PCT] [--weekly PCT] [--max-age SECONDS] [--json]

Exit 0 under both thresholds, 2 at or over either, 1 when the numbers are unknown: no file, or older than --max-age
(default 600 s; the status line hasn't refreshed, e.g. no Claude session has replied lately). A window whose
resets_at has passed counts as reset (0%).

Env: MU_USAGE_FILE, MU_USAGE_5H_THRESHOLD (default 85), MU_USAGE_WEEKLY_THRESHOLD (default 99).
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

USAGE_FILE = os.environ.get("MU_USAGE_FILE", "/home/jrereyi/Project-Mu/data/usage/rate_limits.json")
DEFAULT_5H = float(os.environ.get("MU_USAGE_5H_THRESHOLD", 85))
DEFAULT_WEEKLY = float(os.environ.get("MU_USAGE_WEEKLY_THRESHOLD", 99))
DEFAULT_MAX_AGE = 600


def check(data, five_hour, weekly, max_age, now):
    age = now - data.get("seen_at", 0)
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
    ap.add_argument("--file", default=USAGE_FILE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(a.file) as f:
            data = json.load(f)
    except (OSError, ValueError):
        code, msg, out = 1, f"usage: unknown (no readable {a.file}; is the status line set up?)", {}
    else:
        code, msg, out = check(data, a.five_hour, a.weekly, a.max_age, time.time())
    print(json.dumps({"status": code, "message": msg, "windows": out}) if a.json else msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
