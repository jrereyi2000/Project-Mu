"""Choose which Claude account has quota headroom (JR's primary or `claude-personal`).

The two accounts are separate Claude Code config directories, so each has its own status line and
its own usage file:

* primary:  ``data/usage/rate_limits.json`` (``tools/usage_statusline.py``)
* personal: ``data/usage/rate_limits_personal.json`` (``tools/usage_statusline_personal.sh``)

``tools/spawn_agent.sh <label> opus-auto`` (and ``claude-auto``) runs ``choose`` and starts the
chosen account's ``claude``. The chooser is deliberately conservative:

* an account is **available** when ``tools.usage_check`` reports exit 0 (under 85% of the 5-hour
  window and 99% of the weekly one);
* if both are available, the one with the lower ``max(5h/85, weekly/99)`` score wins (primary on a
  tie), so a session lands on whichever has more headroom;
* if one is available, that one is chosen even when the other is over or unknown;
* if neither is known-under, ``choose`` exits 2 rather than guess — a missing/stale file is not a
  reason to spend an account that may be at its limit. The one exception is both files unknown
  (never set up): the primary is the legacy default and is returned with a warning.

    python3 -m tools.claude_account choose [--json]
    python3 -m tools.claude_account status [--json]

Both commands accept ``--primary-file``, ``--personal-file``, ``--five-hour``, ``--weekly`` and
``--max-age``; ``MU_USAGE_FILE`` and ``MU_USAGE_PERSONAL_FILE`` override the two paths.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from tools import usage_check

PRIMARY_FILE = os.environ.get(
    "MU_USAGE_FILE", "/home/jrereyi/Project-Mu/data/usage/rate_limits.json")
PERSONAL_FILE = os.environ.get(
    "MU_USAGE_PERSONAL_FILE", "/home/jrereyi/Project-Mu/data/usage/rate_limits_personal.json")
DEFAULT_MAX_AGE = 600


class NoAccount(RuntimeError):
    """Neither account is known to be under its quota thresholds."""


def _read(path: str | Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def account_state(name: str, path: str | Path, five_hour: float, weekly: float,
                  max_age: int, now: float | None = None) -> dict:
    """One account's usage state: ``code`` 0 under, 2 over, 1 unknown; plus its headroom score."""
    now = time.time() if now is None else now
    data = _read(path)
    if data is None:
        return {"account": name, "path": str(path), "code": 1,
                "reason": f"no readable {path}", "score": None, "windows": {}}
    code, message, windows = usage_check.check(data, five_hour, weekly, max_age, now)
    score = None
    if code in (0, 2):
        ratios = []
        for key, threshold in (("five_hour", five_hour), ("seven_day", weekly)):
            window = windows.get(key)
            if window:
                ratios.append(float(window.get("used_percentage", 0)) / max(float(threshold), 1.0))
        score = max(ratios) if ratios else None
        if score is None:
            code = 1
    return {"account": name, "path": str(path), "code": code, "reason": message,
            "score": score, "windows": windows}


def choose(primary_file: str | Path = PRIMARY_FILE, personal_file: str | Path = PERSONAL_FILE,
           five_hour: float = usage_check.DEFAULT_5H, weekly: float = usage_check.DEFAULT_WEEKLY,
           max_age: int = DEFAULT_MAX_AGE, now: float | None = None) -> tuple[str, dict]:
    """Return ``("primary"|"personal", state)`` or raise ``NoAccount`` with both states attached."""
    primary = account_state("primary", primary_file, five_hour, weekly, max_age, now)
    personal = account_state("personal", personal_file, five_hour, weekly, max_age, now)
    states = {"primary": primary, "personal": personal}
    if primary["code"] == 0 and personal["code"] == 0:
        picked = "personal" if personal["score"] < primary["score"] else "primary"
        return picked, states
    if primary["code"] == 0:
        return "primary", states
    if personal["code"] == 0:
        return "personal", states
    if primary["code"] == 1 and personal["code"] == 1:
        # Neither status line has ever written a usable file: keep the legacy primary default.
        return "primary", states
    raise NoAccount(states)


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("choose", "status"))
    parser.add_argument("--primary-file", default=PRIMARY_FILE)
    parser.add_argument("--personal-file", default=PERSONAL_FILE)
    parser.add_argument("--five-hour", type=float, default=usage_check.DEFAULT_5H)
    parser.add_argument("--weekly", type=float, default=usage_check.DEFAULT_WEEKLY)
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.command == "status":
        states = {
            "primary": account_state("primary", args.primary_file, args.five_hour,
                                     args.weekly, args.max_age),
            "personal": account_state("personal", args.personal_file, args.five_hour,
                                      args.weekly, args.max_age),
        }
        print(json.dumps(states, indent=1) if args.json else json.dumps(states))
        return 0
    try:
        picked, states = choose(args.primary_file, args.personal_file, args.five_hour,
                                args.weekly, args.max_age)
    except NoAccount as exc:
        states = exc.args[0]
        if args.json:
            print(json.dumps({"account": None, "states": states}, indent=1))
        print("claude_account: neither account is known to be under its quota; "
              "set up the personal status line or lower the thresholds", file=sys.stderr)
        for name in ("primary", "personal"):
            state = states[name]
            print(f"  {name}: {state['reason']}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"account": picked, "states": states}, indent=1))
    else:
        print(picked)
    other = states["personal" if picked == "primary" else "primary"]
    print(f"claude_account: {picked} chosen ({states[picked]['reason']}); "
          f"other {other['account']}: {other['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
