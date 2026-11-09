"""Inbox and press latency spikes (CLOSE_OUT_CHECKLIST P1-09 and P1-10).

Both measurements need the model reachable (`.env` GEMINI_API_KEY) and, for the numbers to be
comparable to the plan's, the PC in its normal state with Madden open.

  python3 -m tools.latency_spike inbox [--calls 20]
      make_llm("inbox") on a filled CONTRACT prompt: one warm-up call, then `calls` measured
      calls. Prints p50/p95 (and mean/min/max) and the token totals; sets nothing.

  python3 -m tools.latency_spike press --season 2026 --week 1 --team SEA [--dsn ...]
      One warm presser through newsroom.press_room.run_week on a scratch database. Every model
      call is timed. Prints p50/p95 per call, calls, tokens, cost and wall time.

Nothing here writes the live franchise: `press` takes the DSN explicitly and refuses to run when
the target database is the active franchise unless --allow-active is passed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_OUT = os.path.join(ROOT, "verify_output", "latency_spike")


def _percentile(values, pct):
    """Nearest-rank percentile (p50/p95 of a short list needs no interpolation)."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[k - 1]


def _summary(durations):
    return {
        "n": len(durations),
        "p50_s": round(statistics.median(durations), 3) if durations else None,
        "p95_s": round(_percentile(durations, 95), 3) if durations else None,
        "mean_s": round(statistics.mean(durations), 3) if durations else None,
        "min_s": round(min(durations), 3) if durations else None,
        "max_s": round(max(durations), 3) if durations else None,
    }


def _write(name, payload):
    os.makedirs(DEFAULT_OUT, exist_ok=True)
    path = os.path.join(DEFAULT_OUT, f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


# ------------------------------------------------------------------------------------------------ inbox ---

def run_inbox(calls=20, model=None):
    from engines.gm_llm import make_llm
    from engines.llm_provider import estimate_cost
    from tools.llm_compare import build_inbox_appraisal_inputs

    prompt = build_inbox_appraisal_inputs(season=2026, week=8, limit=1)[0].prompt
    llm = make_llm("inbox", model=model) if model else make_llm("inbox")
    if llm is None:
        raise SystemExit("no inbox model: set GEMINI_API_KEY (or MU_INBOX_LLM is 0 / no key)")

    llm("Reply with exactly: OK")          # warm-up, not measured
    if hasattr(llm, "meter"):
        llm.meter.take()

    durations, failures = [], 0
    for _ in range(int(calls)):
        started = time.perf_counter()
        try:
            text = llm(prompt)
            if not text:
                failures += 1
        except Exception as exc:            # noqa: BLE001 - a failed call is a result too
            failures += 1
            text = f"[ERROR {type(exc).__name__}: {exc}]"
        durations.append(time.perf_counter() - started)
        print(f"  {len(durations):2d}/{calls}: {durations[-1]:6.2f}s  {str(text)[:70]!r}", flush=True)

    totals = llm.meter.totals() if hasattr(llm, "meter") else {}
    cost = estimate_cost(getattr(llm, "model_name", None) or model or "", totals)
    payload = {"kind": "inbox-latency-spike", "when": time.strftime("%Y-%m-%d %H:%M:%S"),
               "model": getattr(llm, "model_name", None) or model,
               "calls": int(calls), "failures": failures, "prompt_chars": len(prompt),
               "latency": _summary(durations), "tokens": totals, "cost_usd": round(cost, 6)}
    path = _write("inbox", payload)
    print(json.dumps({k: v for k, v in payload.items() if k != "tokens"}, indent=2))
    print(f"tokens: {totals}")
    print(f"report: {path}")
    return payload


# ------------------------------------------------------------------------------------------------ press ---

class TimingLLM:
    """prompt -> text through the press provider, timing and metering every call."""

    def __init__(self, model=None):
        self.model = model
        self.durations = []
        self.usage = []

    def __call__(self, prompt):
        from engines.llm_provider import generate_text
        kwargs = {"source": "press", "return_usage": True}
        if self.model:
            kwargs["model"] = self.model
        started = time.perf_counter()
        try:
            text, usage = generate_text(prompt, **kwargs)
        finally:
            self.durations.append(time.perf_counter() - started)
        self.usage.append(usage or {})
        return text


def run_press(dsn, season, week, team, model=None, allow_active=False):
    from engines.llm_provider import estimate_cost
    from newsroom import press_room

    if not allow_active:
        try:
            from db.franchises import active_dsn
            if dsn and active_dsn() and dsn.strip() == str(active_dsn()).strip():
                raise SystemExit("refusing to run a presser against the active franchise; point --dsn at a scratch DB")
        except SystemExit:
            raise
        except Exception:                    # no registry: the explicit DSN is the caller's responsibility
            pass

    llm = TimingLLM(model=model)
    llm("Reply with exactly: OK")            # warm-up, not measured
    llm.durations.clear()
    llm.usage.clear()

    started = time.time()
    summary = press_room.run_week(dsn, int(season), int(week), workers=1, llm=llm, teams=[team])
    wall = time.time() - started

    tokens = {"input_tokens": sum(int((u or {}).get("input_tokens") or 0) for u in llm.usage),
              "output_tokens": sum(int((u or {}).get("output_tokens") or 0) for u in llm.usage),
              "thinking_tokens": sum(int((u or {}).get("thinking_tokens") or 0) for u in llm.usage),
              "calls": len(llm.usage)}
    model_name = model or press_room.PRESS_MODEL or "gemini-3.8-flash"
    payload = {"kind": "press-latency-spike", "when": time.strftime("%Y-%m-%d %H:%M:%S"),
               "dsn": dsn, "season": int(season), "week": int(week), "team": team, "model": model_name,
               "wall_s": round(wall, 2), "latency": _summary(llm.durations), "tokens": tokens,
               "cost_usd": round(estimate_cost(model_name, tokens), 6), "run_week": summary}
    path = _write("press", payload)
    print(json.dumps({k: v for k, v in payload.items() if k != "tokens"}, indent=2))
    print(f"tokens: {tokens}")
    print(f"report: {path}")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inbox and press latency spikes (P1-09, P1-10)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    inbox = sub.add_parser("inbox", help="time make_llm('inbox') on a filled CONTRACT prompt")
    inbox.add_argument("--calls", type=int, default=20)
    inbox.add_argument("--model", default=None)
    press = sub.add_parser("press", help="run one warm presser and time every model call")
    press.add_argument("--dsn", required=True)
    press.add_argument("--season", type=int, required=True)
    press.add_argument("--week", type=int, required=True)
    press.add_argument("--team", required=True)
    press.add_argument("--model", default=None)
    press.add_argument("--allow-active", action="store_true",
                       help="permit the active franchise (never used for a measurement)")
    args = parser.parse_args(argv)
    if args.cmd == "inbox":
        run_inbox(args.calls, args.model)
    else:
        run_press(args.dsn, args.season, args.week, args.team, args.model, args.allow_active)
    return 0


if __name__ == "__main__":
    sys.exit(main())
