"""Antigravity (Gemini 4 Hydrogen / Argon) usage ledger and budget guard.

`agy` serves the two Gemini 4 models Project Mu runs (docs/reference/GEMINI_4_HYDROGEN_EVALUATION.md),
and it reports per-run token usage but publishes no plan-quota number and has no `usage` command
(measured 2026-11-12). So this tool does what is measurable and says plainly what is not:

    python3 -m tools.antigravity_usage [--ledger PATH] [--since-hours H]
                                       [--max-tokens N] [--max-usd X] [--json]
    python3 -m tools.antigravity_usage record FILE|- [--model ID] [--ledger PATH]

Sources:
  record  One `agy -p --output-format json` result, whose `usage` block carries
          ``input_tokens``, ``output_tokens``, ``thinking_tokens``, ``cache_read_tokens`` and
          ``total_tokens``. The record is appended to the ledger with a UTC timestamp, the model id
          (``--model``, else ``MU_AGENT_MODEL``) and the list-price equivalent.
  ledger  ``data/usage/antigravity_usage.jsonl`` (``MU_ANTIGRAVITY_LEDGER`` overrides). Every entry is
          one scripted run; an interactive Antigravity session writes nothing here, so the ledger is
          a floor on Mu's traffic, never a total.

**Plan quota is not measurable from this box**: there is no percentage to print, and the tool never
invents one. The Antigravity usage screen is the only source for the plan (Pro / Ultra); use the
ledger to size a campaign and the screen to decide when to stop. Prices are the published Gemini API
list rates, used as a plan-quota proxy:

    1M tokens (in / cached read / out):  Hydrogen 1.50 / 0.075 / 7.50
                                         Argon    2.00 / 0.10  / 10.00

Exit codes match ``tools/go_usage.py``: 0 under every budget, 2 at or over one, 1 when nothing is
measured yet, 3 when a record cannot be parsed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = _REPO_ROOT / "data" / "usage" / "antigravity_usage.jsonl"
USAGE_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
FAMILIES = {
    "hydrogen": {"input": 1.50, "cache_read": 0.075, "output": 7.50},
    "argon": {"input": 2.00, "cache_read": 0.10, "output": 10.00},
}


class BadRecord(ValueError):
    """A JSON body that carries no usable usage block."""


def ledger_path(path: Any = None) -> Path:
    return Path(path or os.environ.get("MU_ANTIGRAVITY_LEDGER") or DEFAULT_LEDGER)


def model_family(model: Optional[str]) -> Optional[str]:
    """'hydrogen' or 'argon' from a model id such as gemini-4-hydrogen-high."""
    if not model:
        return None
    name = model.strip().lower().split(":")[-1].split("/")[-1]
    for family in FAMILIES:
        if family in name:
            return family
    return None


def _token(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def parse_result(payload: Any, model: Optional[str] = None) -> Dict[str, Any]:
    """Normalise one `agy --output-format json` result (or a bare usage block)."""
    if not isinstance(payload, dict):
        raise BadRecord("result was not a JSON object")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        if any(field in payload for field in USAGE_FIELDS):
            usage = payload
        else:
            raise BadRecord("result carries no usage block")
    tokens = {field: _token(usage.get(field)) for field in USAGE_FIELDS}
    if not tokens["input_tokens"] and not tokens["output_tokens"]:
        raise BadRecord("usage block has no input or output tokens")
    # agy reports thinking inside output_tokens (a 9,630 + 27 probe totals 9,657), so nothing is
    # added for it here; it is kept only so a report can show the split.
    if not tokens["total_tokens"]:
        tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
    record: Dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model or payload.get("model") or os.environ.get("MU_AGENT_MODEL") or "unknown",
        "conversation_id": payload.get("conversation_id"),
        "duration_seconds": payload.get("duration_seconds"),
        **tokens,
    }
    record["list_cost_usd"] = round(list_cost(record), 6)
    return record


def list_cost(record: Dict[str, Any]) -> float:
    """Published list price of the tokens a record reports, in USD."""
    rates = FAMILIES.get(model_family(record.get("model")))
    if rates is None:
        return 0.0
    cached = min(_token(record.get("cache_read_tokens")), _token(record.get("input_tokens")))
    uncached = _token(record.get("input_tokens")) - cached
    return (uncached * rates["input"] + cached * rates["cache_read"]
            + _token(record.get("output_tokens")) * rates["output"]) / 1_000_000


def append_record(record: Dict[str, Any], path: Any = None) -> Path:
    target = ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return target


def entries(path: Any = None) -> Tuple[List[Dict[str, Any]], int]:
    """Read the ledger. Returns (records, skipped) — a malformed line is skipped, never fatal."""
    target = ledger_path(path)
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


def _at(row: Dict[str, Any]) -> Optional[datetime]:
    text = row.get("at")
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summarise(rows: List[Dict[str, Any]], since_hours: float = 24.0,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=since_hours)
    tokens = 0
    usd = 0.0
    runs = 0
    models: Dict[str, int] = {}
    for row in rows:
        at = _at(row)
        if at is None or at < cutoff:
            continue
        runs += 1
        tokens += _token(row.get("total_tokens"))
        usd += float(row.get("list_cost_usd") or 0.0)
        name = str(row.get("model") or "unknown")
        models[name] = models.get(name, 0) + 1
    return {"runs": runs, "tokens": tokens, "usd": round(usd, 6), "models": models,
            "since_hours": since_hours}


def check(path: Any = None, since_hours: float = 24.0, max_tokens: Optional[int] = None,
          max_usd: Optional[float] = None) -> Tuple[int, str, Dict[str, Any]]:
    """Judge the ledger against the budgets. Never prints a plan percentage (there is none)."""
    rows, skipped = entries(path)
    summary = summarise(rows, since_hours=since_hours)
    document = {"source": "ledger", "ledger": str(ledger_path(path)), "skipped": skipped,
                "plan_quota": "unknown (no local probe; check the Antigravity usage screen)",
                **summary}
    if not rows:
        return 1, ("antigravity usage: nothing measured yet — record a run first "
                   "(python3 -m tools.antigravity_usage record FILE)"), document
    shown = f"{summary['runs']} runs, {summary['tokens']:,} tokens, ${summary['usd']:.4f} list ({since_hours:g}h)"
    over = []
    if max_tokens is not None and summary["tokens"] >= max_tokens:
        over.append(f"{summary['tokens']:,} tokens >= {max_tokens:,}")
    if max_usd is not None and summary["usd"] >= max_usd:
        over.append(f"${summary['usd']:.4f} >= ${max_usd:.4f}")
    if over:
        return 2, f"antigravity usage: {shown} - OVER: {'; '.join(over)}: hand off", document
    if max_tokens is None and max_usd is None:
        return 1, (f"antigravity usage: {shown} (no budget: pass --max-tokens or --max-usd); "
                   "plan quota unknown"), document
    return 0, f"antigravity usage: {shown} - under budget; plan quota unknown", document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", nargs="?", choices=("record",), help="record one agy JSON result")
    parser.add_argument("file", nargs="?", help="the JSON file for `record`, or - for stdin")
    parser.add_argument("--model", help="model id to attribute the record to (else MU_AGENT_MODEL)")
    parser.add_argument("--ledger", default=None,
                        help="ledger path (default MU_ANTIGRAVITY_LEDGER or data/usage/antigravity_usage.jsonl)")
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--max-tokens", type=int, default=None, help="exit 2 at or over this many tokens")
    parser.add_argument("--max-usd", type=float, default=None, help="exit 2 at or over this list cost")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "record":
        if not args.file:
            parser.error("record needs a FILE (or - for stdin)")
        try:
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            payload = json.loads(text)
            record = parse_result(payload, model=args.model)
        except (OSError, ValueError) as exc:
            print(f"antigravity usage: cannot record: {exc}", file=sys.stderr)
            return 3
        target = append_record(record, args.ledger)
        print(f"recorded {record['total_tokens']:,} tokens (${record['list_cost_usd']:.4f} list) "
              f"for {record['model']} -> {target}")
        return 0

    code, message, document = check(path=args.ledger, since_hours=args.since_hours,
                                    max_tokens=args.max_tokens, max_usd=args.max_usd)
    if args.json:
        print(json.dumps({"status": code, "message": message, **document}, indent=2))
    else:
        print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
