"""Claude Code status line that also records the Claude plan quota for tools/usage_check.py (JR, 2026-11-10).

Claude Code runs this on each status-line refresh with the session's JSON on stdin. On a Claude.ai subscription that
JSON carries `rate_limits.five_hour` / `rate_limits.seven_day` ({used_percentage, resets_at}) after the session's first
reply. When it does, they're written atomically to data/usage/rate_limits.json with the time they were seen. The limits
are account-wide, so whichever Claude session refreshed last keeps the file current. Sessions without them (API key, the
DeepSeek routing, no reply yet) leave the file alone.

Prints one line: `<model> · ctx 12% · 5h 42% · wk 61%`. Set in ~/.claude/settings.json:
    "statusLine": {"type": "command", "command": "python3 /home/jrereyi/Project-Mu/tools/usage_statusline.py"}
Never fails loudly: a bad input prints a bare line, because a broken status line is worse than a missing number.
"""

import json
import os
import sys
import time

USAGE_FILE = os.environ.get("MU_USAGE_FILE", "/home/jrereyi/Project-Mu/data/usage/rate_limits.json")
WINDOWS = ("five_hour", "seven_day")


def record(rate_limits, path=USAGE_FILE, now=None):
    windows = {w: rate_limits[w] for w in WINDOWS if isinstance(rate_limits.get(w), dict)}
    if not windows:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump({"seen_at": int(now if now is not None else time.time()), **windows}, f)
    os.replace(tmp, path)
    return True


def line(data):
    parts = [((data.get("model") or {}).get("display_name")) or "Claude"]
    ctx = (data.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"ctx {ctx:.0f}%")
    rl = data.get("rate_limits") or {}
    for w, short in (("five_hour", "5h"), ("seven_day", "wk")):
        pct = (rl.get(w) or {}).get("used_percentage")
        if pct is not None:
            parts.append(f"{short} {pct:.0f}%")
    return " · ".join(parts)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("Claude")
        return
    try:
        if isinstance(data.get("rate_limits"), dict):
            record(data["rate_limits"])
    except Exception:
        pass
    print(line(data))


if __name__ == "__main__":
    main()
