"""OpenCode Go allowance watchdog.
(docs/in_progress/OPENCODE_GO_PIPELINE_PLAN.md §2.5)

Reports how much of the Go subscription's rolling 5-hour, weekly and monthly allowance is used,
with the same exit-code convention as tools/usage_check.py: 0 under every threshold, 2 at or
over one, 1 when no source answers.

    python3 -m tools.go_usage [--source auto|api|ledger] [--monthly-limit USD]
                              [--five-hour-threshold PCT] [--weekly-threshold PCT]
                              [--monthly-threshold PCT] [--max-age HOURS] [--file PATH] [--json]

Sources:
  api     GET {OPENCODE_GO_BASE_URL minus the trailing /openai/v1}/v1/usage, i.e.
          https://opencode.ai/inference/go/v1/usage, with OPENCODE_GO_API_KEY and a
          project-mu/... User-Agent (Cloudflare answers a generic agent with 403 code 1010).
          It returns rolling / weekly / monthly as {percent, resetsAt}, already
          workspace-wide, so agent sessions are counted. This is the authoritative number.
  ledger  Sums allowance_usd from data/usage/go_allowance.jsonl (MU_GO_ALLOWANCE_FILE
          overrides) over the rolling 5 hours, 7 days and the UTC month. That is a dollar
          figure for Mu's own traffic, not a percentage: Go's monthly limit is per model
          (DeepSeek V4.1 Flash $100, GLM-5.3-Flash $60, Kimi K3 $15, ...), so the tool does
          not guess one. It judges thresholds only when --monthly-limit (or
          MU_GO_MONTHLY_LIMIT) is given, using Go's fixed shares of that limit: 5-hour 20%,
          weekly 50%, month 100%. Without a limit it prints dollars and exits 1 ("limit unknown"),
          the way tools/usage_check.py treats a stale file. A zero or negative limit is refused
          the same way (exit 1, no percentages), since Go has no free tier to divide by.
  auto    The default: try the api, fall back to the ledger when the key is missing or the
          request fails. --source api alone exits 1 instead of falling back.

Whether the API percentage is per-model or for the whole workspace is undocumented. Either way
it is a superset of Mu's own traffic, so this watchdog errs high: the percentage is never a
lower bound for Mu's spend.

Thresholds default 85 / 99 / 95 for rolling / weekly / monthly (MU_GO_5H_THRESHOLD,
MU_GO_WEEKLY_THRESHOLD and MU_GO_MONTHLY_THRESHOLD override them). --max-age HOURS (default 24)
marks a ledger whose newest entry is older than that as stale; a stale ledger is still reported
and judged, so only its `stale` flag changes. Exit 0 under every threshold, 2 at or over any,
1 when no source answers.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
USER_AGENT = "project-mu-go-usage/1.0"
WINDOWS = ("rolling", "weekly", "monthly")
SHORT_NAMES = {"rolling": "5h", "weekly": "weekly", "monthly": "month"}
# Go's fixed shares of the monthly limit (plan §2.5).
GO_SHARES = {"rolling": 0.20, "weekly": 0.50, "monthly": 1.0}
LEDGER_WINDOWS = {"rolling": timedelta(hours=5), "weekly": timedelta(days=7)}
DEFAULT_THRESHOLDS = {
    "rolling": float(os.environ.get("MU_GO_5H_THRESHOLD", 85)),
    "weekly": float(os.environ.get("MU_GO_WEEKLY_THRESHOLD", 99)),
    "monthly": float(os.environ.get("MU_GO_MONTHLY_THRESHOLD", 95)),
}
DEFAULT_MAX_AGE_HOURS = 24.0


class UsageUnavailable(Exception):
    """The API answered nothing usable: no key, no network, or an unexpected body."""


def _env(name: str) -> Optional[str]:
    """A setting from the environment, else from the project-root .env."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        path = _REPO_ROOT / ".env"
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip().startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def usage_url(base_url: Optional[str] = None) -> str:
    """The usage endpoint derived from OPENCODE_GO_BASE_URL (strip /openai/v1, add /v1/usage)."""
    base = (base_url or _env("OPENCODE_GO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base + "/v1/usage"


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_at(value: Any) -> Optional[datetime]:
    """Parse resetsAt (ISO-8601 string or epoch seconds/milliseconds) into an aware UTC time."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value) / (1000.0 if value > 1e11 else 1.0)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def parse_api_body(body: Any) -> Dict[str, Dict[str, Any]]:
    """Pull rolling / weekly / monthly out of the usage endpoint's ``{"usage": {...}}`` body."""
    if isinstance(body, dict) and isinstance(body.get("usage"), dict):
        body = body["usage"]
    if not isinstance(body, dict):
        raise UsageUnavailable("usage response was not a JSON object")
    windows: Dict[str, Dict[str, Any]] = {}
    for window in WINDOWS:
        raw = body.get(window)
        if not isinstance(raw, dict):
            continue
        percent = _as_float(raw.get("percent"))
        if percent is None:
            continue
        windows[window] = {"percent": percent, "resets_at": raw.get("resetsAt")}
    if not windows:
        raise UsageUnavailable("usage response had no rolling/weekly/monthly window")
    return windows


def fetch_api_usage(api_key: Optional[str] = None, base_url: Optional[str] = None,
                    timeout: float = 10.0) -> Dict[str, Dict[str, Any]]:
    """GET the usage endpoint. Raises UsageUnavailable for a missing key or any failed request."""
    key = api_key or _env("OPENCODE_GO_API_KEY")
    if not key:
        raise UsageUnavailable("OPENCODE_GO_API_KEY is not set")
    url = usage_url(base_url)
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise UsageUnavailable(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UsageUnavailable(f"{type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, AttributeError) as exc:
        raise UsageUnavailable(f"usage response was not JSON: {exc}") from exc
    return parse_api_body(payload)


def ledger_windows(path: Any = None, now: Optional[datetime] = None,
                   max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> Dict[str, Any]:
    """Sum the allowance ledger over the rolling 5 hours, 7 days and the UTC month."""
    from newsroom.tool_loop import go_allowance
    now = now or datetime.now(timezone.utc)
    rows = go_allowance.entries(path)
    sums = {window: 0.0 for window in WINDOWS}
    counts = {window: 0 for window in WINDOWS}
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    newest: Optional[datetime] = None
    total = 0
    for row in rows:
        at = go_allowance.to_datetime(row.get("at"))
        if at is None:
            continue
        total += 1
        if newest is None or at > newest:
            newest = at
        allowance = row.get("allowance_usd")
        if not isinstance(allowance, (int, float)) or isinstance(allowance, bool):
            continue
        for window in WINDOWS:
            if window == "monthly":
                inside = at >= month_start
            else:
                inside = at >= now - LEDGER_WINDOWS[window]
            if inside:
                sums[window] += float(allowance)
                counts[window] += 1
    stale = newest is None or (now - newest) > timedelta(hours=max_age_hours)
    return {"usd": sums, "counts": counts, "entries": total, "stale": stale, "newest": newest}


def _format_resets(value: Any, now: datetime) -> str:
    dt = _parse_at(value)
    if dt is None:
        return ""
    if dt.date() == now.date():
        return dt.strftime("%H:%MZ")
    if dt.year == now.year:
        return dt.strftime("%b %-d")
    return dt.strftime("%b %-d %Y")


def _thresholds(args) -> Dict[str, float]:
    return {
        "rolling": args.five_hour_threshold,
        "weekly": args.weekly_threshold,
        "monthly": args.monthly_threshold,
    }


def _verdict(windows: Dict[str, Dict[str, Any]], thresholds: Dict[str, float]) -> List[str]:
    over = []
    for window in WINDOWS:
        info = windows.get(window) or {}
        info["threshold"] = thresholds[window]
        info["over"] = bool(info.get("percent") is not None and info["percent"] >= thresholds[window])
        if info["over"]:
            over.append(f"{SHORT_NAMES[window]} {info['percent']:.0f}% >= {thresholds[window]:g}%")
        windows[window] = info
    return over


def _thresholds_text(thresholds: Dict[str, float]) -> str:
    return f"{thresholds['rolling']:g}%/{thresholds['weekly']:g}%/{thresholds['monthly']:g}%"


def _api_report(windows: Dict[str, Dict[str, Any]], thresholds: Dict[str, float],
                now: datetime) -> Tuple[int, str, Dict[str, Any]]:
    out = {}
    for window in WINDOWS:
        info = windows.get(window) or {"percent": None, "resets_at": None}
        resets = _format_resets(info.get("resets_at"), now)
        out[window] = {
            "percent": info.get("percent"),
            "usd": None,
            "limit_usd": None,
            "threshold": thresholds[window],
            "over": False,
            "resets_at": info.get("resets_at"),
            "resets": resets,
        }
    over = _verdict(out, thresholds)
    shown = " · ".join(
        f"{SHORT_NAMES[w]} {out[w]['percent']:.0f}%"
        + (f" (resets {out[w]['resets']})" if out[w].get("resets") else "")
        for w in WINDOWS if out[w]["percent"] is not None
    )
    if over:
        return 2, f"go allowance: {shown} - OVER: {'; '.join(over)}: stop drivers now", out
    return 0, f"go allowance: {shown} - under {_thresholds_text(thresholds)}", out


def _ledger_report(ledger: Dict[str, Any], thresholds: Dict[str, float],
                   monthly_limit: Optional[float]) -> Tuple[int, str, Dict[str, Any]]:
    positive = monthly_limit is not None and monthly_limit > 0
    out: Dict[str, Dict[str, Any]] = {}
    for window in WINDOWS:
        usd = float(ledger["usd"][window])
        limit = monthly_limit * GO_SHARES[window] if positive else None
        percent = (usd / limit * 100.0) if limit else None
        out[window] = {
            "percent": percent,
            "usd": round(usd, 6),
            "limit_usd": round(limit, 6) if limit is not None else None,
            "threshold": thresholds[window],
            "over": False,
            "resets_at": None,
            "resets": "",
            "entries": ledger["counts"][window],
        }
    if not ledger["entries"]:
        return 1, ("go allowance: unknown (no readable ledger entries; "
                   "is any pipeline call reporting an allowance?)"), out
    if monthly_limit is not None and monthly_limit <= 0:
        shown = " · ".join(f"{SHORT_NAMES[w]} ${out[w]['usd']:.4f}" for w in WINDOWS)
        return 1, (f"go allowance: {shown} (invalid monthly limit: {monthly_limit:g}; "
                   f"it must be positive, like --monthly-limit 100 or MU_GO_MONTHLY_LIMIT=100)"), out
    if not positive:
        shown = " · ".join(f"{SHORT_NAMES[w]} ${out[w]['usd']:.4f}" for w in WINDOWS)
        return 1, (f"go allowance: {shown} (limit unknown: pass --monthly-limit "
                   f"or set MU_GO_MONTHLY_LIMIT)"), out
    over = _verdict(out, thresholds)
    shown = " · ".join(
        f"{SHORT_NAMES[w]} ${out[w]['usd']:.4f} of ${out[w]['limit_usd']:.2f}"
        f" ({out[w]['percent']:.0f}%)" for w in WINDOWS
    )
    if over:
        return 2, f"go allowance: {shown} - OVER: {'; '.join(over)}: stop drivers now", out
    return 0, f"go allowance: {shown} - under {_thresholds_text(thresholds)}", out


def check(source: str = "auto", monthly_limit: Optional[float] = None,
          thresholds: Optional[Dict[str, float]] = None, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
          ledger_path: Any = None, api_key: Optional[str] = None, base_url: Optional[str] = None,
          now: Optional[datetime] = None) -> Tuple[int, str, Dict[str, Any]]:
    """Judge the three windows. Returns (exit code, one-line message, JSON-ready windows)."""
    thresholds = thresholds or dict(DEFAULT_THRESHOLDS)
    now = now or datetime.now(timezone.utc)
    api_error: Optional[str] = None
    if source in ("auto", "api"):
        try:
            windows = fetch_api_usage(api_key=api_key, base_url=base_url)
        except UsageUnavailable as exc:
            api_error = str(exc)
        else:
            code, message, out = _api_report(windows, thresholds, now)
            return code, message, {"source": "api", "stale": False, "windows": out, "message": message}
    if source == "api":
        message = f"go allowance: unknown (api: {api_error})"
        return 1, message, {"source": "api", "stale": False, "windows": {}, "message": message}
    ledger = ledger_windows(ledger_path, now=now, max_age_hours=max_age_hours)
    code, message, out = _ledger_report(ledger, thresholds, monthly_limit)
    if api_error and source == "auto":
        message = f"{message} (api unavailable: {api_error})"
    return code, message, {"source": "ledger", "stale": bool(ledger["stale"]),
                           "windows": out, "message": message}


def _monthly_limit(args) -> Optional[float]:
    if args.monthly_limit is not None:
        return args.monthly_limit
    env_limit = _env("MU_GO_MONTHLY_LIMIT")
    return _as_float(env_limit)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", choices=("auto", "api", "ledger"), default="auto")
    parser.add_argument("--monthly-limit", type=float, default=None,
                        help="USD of Go's monthly limit for this model (ledger source only)")
    parser.add_argument("--five-hour-threshold", type=float, default=DEFAULT_THRESHOLDS["rolling"],
                        help="rolling 5-hour threshold, percent (default 85)")
    parser.add_argument("--weekly-threshold", type=float, default=DEFAULT_THRESHOLDS["weekly"],
                        help="weekly threshold, percent (default 99)")
    parser.add_argument("--monthly-threshold", type=float, default=DEFAULT_THRESHOLDS["monthly"],
                        help="monthly threshold, percent (default 95)")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_HOURS,
                        help="hours before a ledger's newest entry counts as stale (default 24)")
    parser.add_argument("--file", default=None,
                        help="ledger path (default MU_GO_ALLOWANCE_FILE or data/usage/go_allowance.jsonl)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    code, message, document = check(source=args.source, monthly_limit=_monthly_limit(args),
                                    thresholds=_thresholds(args), max_age_hours=args.max_age,
                                    ledger_path=args.file)
    if args.json:
        print(json.dumps({"status": code, **document}, indent=2))
    else:
        print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
