"""
OpenCode Go allowance ledger.
(docs/in_progress/OPENCODE_GO_PIPELINE_PLAN.md §2.4)

Go reports ``usage.opencode.allowance`` on every inference response: the dollars that call
took out of the monthly allowance. ``run_openai_tool_loop`` appends one line per request that
reports one, so Mu's own spend is knowable per run without a price table and without a
database write (db/llm_usage stays token-only; db/init_db.sql:434-441).

    from newsroom.tool_loop import go_allowance
    go_allowance.record(at=..., model="deepseek-v4.1-flash", label="dossier",
                        allowance_usd=1.2e-05, list_cost_usd=2.0e-05, billing_source="go")
    go_allowance.entries()                       # every line, oldest first

The file is data/usage/go_allowance.jsonl (``MU_GO_ALLOWANCE_FILE`` overrides) and its directory
is created on the first write. Writing is best effort, exactly like db.llm_usage: an OSError
prints one warning to stderr and returns, because metering never stops a run. Reading skips
malformed lines instead of raising.

``tools/go_usage.py`` sums this file over the rolling 5-hour, 7-day and UTC-month windows.
The server's own percentages (``GET /inference/go/v1/usage``) stay authoritative because they
include agent-session traffic this ledger cannot see.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = _REPO_ROOT / "data" / "usage" / "go_allowance.jsonl"


def ledger_path(path: Any = None) -> Path:
    """The ledger file: an explicit path, else ``MU_GO_ALLOWANCE_FILE``, else the repo default."""
    if path is not None:
        return Path(path)
    env_path = os.environ.get("MU_GO_ALLOWANCE_FILE")
    return Path(env_path) if env_path else DEFAULT_LEDGER


def to_iso(at: Any) -> str:
    """Normalise a datetime, epoch number or ISO string to an ISO-8601 UTC string (``...Z``)."""
    dt = to_datetime(at)
    if dt is None:
        return str(at)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_datetime(at: Any) -> Optional[datetime]:
    """Parse a ledger timestamp into an aware UTC datetime; None when it cannot be read."""
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if isinstance(at, (int, float)):
        return datetime.fromtimestamp(float(at), tz=timezone.utc)
    if isinstance(at, str) and at.strip():
        text = at.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def record(path: Any = None, *, at: Any = None, model: str = "", label: str = "",
           allowance_usd: Optional[float] = None, list_cost_usd: Optional[float] = None,
           billing_source: Optional[str] = None,
           tokens: Optional[Dict[str, Any]] = None) -> None:
    """Append one allowance entry to the ledger. Never raises.

    ``tokens`` (optional) is the call's own ``{"input", "cached", "output"}`` counts, written
    beside the dollars as ``input_tokens``/``cached_tokens``/``output_tokens``. ``tools/go_usage.py``
    prices ``allowance_usd`` only; the counts make the line readable without a database row.
    """
    row: Dict[str, Any] = {
        "at": to_iso(at if at is not None else datetime.now(timezone.utc)),
        "model": model,
        "label": label,
        "allowance_usd": None if allowance_usd is None else float(allowance_usd),
        "list_cost_usd": None if list_cost_usd is None else float(list_cost_usd),
        "billing_source": billing_source,
    }
    if tokens:
        for key, field in (("input", "input_tokens"), ("cached", "cached_tokens"),
                           ("output", "output_tokens")):
            value = tokens.get(key)
            row[field] = None if value is None else int(value)
    target = ledger_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:                       # metering never stops a run
        print(f"  (go allowance not recorded: {exc})", file=sys.stderr)


def entries(path: Any = None, since: Any = None) -> List[Dict[str, Any]]:
    """Read the ledger, oldest first, skipping malformed lines. Empty when the file is missing.

    ``since`` (datetime or ISO string) keeps only entries at or after that instant; entries
    whose timestamp cannot be parsed are dropped when a filter is given.
    """
    target = ledger_path(path)
    cutoff = to_datetime(since) if since is not None else None
    rows: List[Dict[str, Any]] = []
    try:
        with open(target, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if cutoff is not None:
                    at = to_datetime(row.get("at"))
                    if at is None or at < cutoff:
                        continue
                rows.append(row)
    except OSError:
        return []
    return rows
