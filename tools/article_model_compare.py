"""Article-level model comparison for the newsroom (NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN U1).

Writes the same real article assignments with several models and saves them for a blind read:
compiled prompts come from the production commissioning code, every article runs through the
production `SwarmDispatcher._execute_agent` path (same tools, same AFC loop, same validation
rewrites), and only the client and the model id differ. Nothing is written to `news_feed` or
`llm_usage`; the database is opened read-only.

Go models run on OpenCode Go's Google-compatible endpoint (`newsroom/tool_loop/go_transport.py`),
so they bill to the Go allowance rather than a card. The Gemini control uses `GEMINI_API_KEY`.

  # what it would do, no model calls
  python3 -m tools.article_model_compare --season 2036 --week 18 --dry-run

  # six approved slots, three models, no judge
  python3 -m tools.article_model_compare --season 2036 --week 18 \
      --models gemini-3.8-flash muse-spark-1.3-contributor glm-5.3-flash \
      --slots "Hero Slot" "Slot 1" "Slot 4" Standalone "Fantasy 1" --no-judge

  # add the pairwise judge (both presentation orders)
  python3 -m tools.article_model_compare ... --judge glm-5.3

Outputs, under verify_output/model_compare/<timestamp>/: prompts.json, results.json,
blind_read.md (answer key at the end), factual_review.csv, grounding.csv, summary.md, and
judge.json / judge.md when the judge runs. See the plan for the cost and slot rationale.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# $/1M tokens (input, cached read, output+thinking), list prices 2026-11-12.
# Go models: Console Go page; gemini-3.8-flash: Google direct (JR's existing credit).
PRICES = {
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "glm-5.3-flash": (0.15, 0.03, 0.50),
    "glm-5.3": (1.40, 0.26, 4.40),
    "glm-5.2": (1.40, 0.26, 4.40),
    "deepseek-v4.1-flash": (0.15, 0.003, 0.60),
    "kimi-k3": (3.00, 0.30, 15.00),
    "kimi-k3.1-pro": (2.00, 0.20, 10.00),
    "qwen3.8-flash": (0.15, 0.016, 0.47),
    "minimax-m3.1-flash": (0.15, 0.015, 0.60),
    "mimo-v2.6-flash": (0.14, 0.0028, 0.28),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
}

DEFAULT_DB = "host=localhost dbname=madden_2036 user=postgres password=postgres"
DEFAULT_SLOTS = ["Hero Slot", "Slot 1", "Slot 4", "Standalone", "Fantasy 1"]
DEFAULT_MODELS = ["gemini-3.8-flash", "muse-spark-1.3-contributor", "glm-5.3-flash"]

JUDGE_RUBRIC = """You are the articles editor of a 32-team NFL newsroom. Two writers were given the \
same assignment, the same house style and the same research tools; you see only their finished copy. \
Judge the copy, not the model.

Score each version 1-5 on: VOICE (does it sound like a specific columnist, not generic AI?), FACTS \
(is every number and name supported by the supplied tool trace? penalise anything else hard), \
STRUCTURE (does it follow the assignment's shape and length?), PROSE (clichés, purple prose, filler), \
SPECIFICITY (real detail from the trace rather than vague summary). Ignore which version is longer.

Reply with JSON only:
{"a": {"voice": n, "facts": n, "structure": n, "prose": n, "specificity": n},
 "b": {"voice": n, "facts": n, "structure": n, "prose": n, "specificity": n},
 "winner": "a" | "b" | "tie", "reason": "one sentence"}"""


# ---------------------------------------------------------------- model calls / pricing

def is_go_model(model: str) -> bool:
    """One rule, shared with the transport: anything not a Gemini-family id is a Go id."""
    from newsroom.tool_loop.go_transport import is_go_model as _is_go
    return _is_go(model)


def price_for(model: str):
    return PRICES.get((model or "").strip().lower())


def cost_usd(model: str, usage_calls: list) -> float | None:
    """List cost of one run's calls, from the meter rows. None when the model has no price row."""
    prices = price_for(model)
    if not prices or not usage_calls:
        return None
    p_in, p_cache, p_out = prices
    total = 0.0
    for row in usage_calls:
        inp = float(row.get("input_tokens") or 0)
        cached = float(row.get("cached_tokens") or 0)
        out = float(row.get("output_tokens") or 0) + float(row.get("thinking_tokens") or 0)
        total += ((inp - cached) * p_in + cached * p_cache + out * p_out) / 1_000_000.0
    return round(total, 6)


def token_totals(usage_calls: list) -> dict:
    keys = ("input_tokens", "cached_tokens", "output_tokens", "thinking_tokens")
    return {k: int(sum(int(r.get(k) or 0) for r in usage_calls)) for k in keys} | {"calls": len(usage_calls)}


def go_allowance() -> dict | None:
    """The workspace's 5-hour/weekly/monthly Go percentages, or None when the query fails."""
    try:
        out = subprocess.run([sys.executable, "-m", "tools.go_usage", "--json"],
                             capture_output=True, text=True, timeout=60, cwd=ROOT)
        if out.returncode not in (0, 2):
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


def _percent(payload: dict | None, window: str) -> float | None:
    try:
        return float(payload["windows"][window]["percent"])
    except Exception:
        return None


def allowance_guard(model: str, before: dict | None, budget_pct=(50.0, 25.0)) -> str | None:
    """Refuse a Go pass that would cross the approved headroom. Returns a reason or None."""
    if not is_go_model(model):
        return None
    five, month = budget_pct
    p5 = _percent(before, "five_hour") if before else None
    pm = _percent(before, "monthly") if before else None
    if p5 is not None and p5 >= five:
        return f"Go 5-hour window already at {p5:.0f}% (limit {five:.0f}%)"
    if pm is not None and pm >= month:
        return f"Go month already at {pm:.0f}% (limit {month:.0f}%)"
    return None


# ---------------------------------------------------------------- clients

def client_for(model: str):
    """(client, error). Go models get the Go Google-compatible transport; Gemini gets its own key."""
    from newsroom.tool_loop.go_transport import go_client, is_go_model as _is_go
    if _is_go(model):
        import os as _os
        for line in _read_env_lines():
            _os.environ.setdefault(*line)
        try:
            return go_client(), None
        except Exception as exc:
            return None, str(exc)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        for k, v in _read_env_lines():
            if k == "GEMINI_API_KEY":
                api_key = v
    if not api_key:
        return None, "GEMINI_API_KEY is not set"
    from google import genai
    return genai.Client(api_key=api_key), None


def _read_env_lines():
    """(key, value) pairs from the repo .env, so a tool run needs no shell export."""
    path = os.path.join(ROOT, ".env")
    pairs = []
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                pairs.append((k.strip(), v.strip().strip('"').strip("'")))
    except OSError:
        pass
    return pairs


# ---------------------------------------------------------------- the slate

def plan_jobs(dispatcher, season: int, week: int, slots: list) -> list:
    """Compiled article jobs for the named slots, from the production commissioning code."""
    from db.db_handler import DatabaseHandler
    from newsroom.swarm_dispatcher import build_weekly_assignment_brief

    wanted = {s.strip().lower() for s in slots}
    jobs = []
    brief = None
    national = dispatcher.commission_weekly_editorial_slate(season, week, target_articles=None)
    for item in national:
        slot = (item.get("slot") or "").strip()
        if wanted and slot.lower() not in wanted:
            continue
        agent = item["agent_record"]
        target = item.get("target")
        subject_team_id = target["team_id"] if target else agent[5]
        if brief is None:
            brief = build_weekly_assignment_brief(DatabaseHandler().db_conn_info, season=season, week=week)
        prompt = dispatcher.compile_agent_prompt(
            agent[0], agent[1], agent[2], agent[3], agent[4], subject_team_id,
            brief, season, week, "ARTICLE", article_type=item["article_type_id"], slot_target=target,
        )
        jobs.append({
            "slot": slot, "article_type_id": item["article_type_id"],
            "article_type_name": item["article_type_name"], "display_name": item["display_name"],
            "handle": item["handle"], "archetype": item["archetype"], "team_id": subject_team_id,
            "desk": "national", "desk_note": None,
            "system_instruction": prompt["system_instruction"], "task_instruction": prompt["task_instruction"],
        })

    if any(w.startswith("fantasy") for w in wanted):
        from newsroom.fantasy_desk import fantasy_phase, fantasy_week_label
        phase = fantasy_phase(week)
        for item in (dispatcher.commission_fantasy_slate(season, week) if phase else []):
            slot = (item.get("slot") or "").strip()
            if slot.lower() not in wanted:
                continue
            agent = item["agent_record"]
            fbrief = {
                "desk": "Mu Fantasy", "season": season, "publication_week": week, "fantasy_phase": phase,
                "this_week": fantasy_week_label(week) if week > 1 else "preseason draft prep",
                "results_available_through_week": week - 1, "scoring": "full PPR",
            }
            prompt = dispatcher.compile_agent_prompt(
                agent[0], agent[1], agent[2], agent[3], agent[4], None, fbrief, season, week, "ARTICLE",
                article_type=item["article_type_id"], desk_note=item["desk_note"],
            )
            jobs.append({
                "slot": slot, "article_type_id": item["article_type_id"],
                "article_type_name": item["article_type_name"], "display_name": item["display_name"],
                "handle": item.get("handle") or agent[3], "archetype": item["archetype"], "team_id": None,
                "desk": "fantasy", "desk_note": item["desk_note"],
                "system_instruction": prompt["system_instruction"], "task_instruction": prompt["task_instruction"],
            })
    return jobs


# ---------------------------------------------------------------- running

def run_job(dispatcher, job: dict, season: int, week: int, model: str, client):
    """One article through the production writer path. Returns a result record."""
    from newsroom.run_context import set_article_context, reset_article_context
    compiled = {
        "display_name": job["display_name"], "handle": job["handle"], "content_type": "ARTICLE",
        "system_instruction": job["system_instruction"], "task_instruction": job["task_instruction"],
        "article_type": job["article_type_id"], "article_type_name": job["article_type_name"],
        "slot": job["slot"],
    }
    capture = {}
    token = set_article_context(season, week)
    started = time.time()
    try:
        dispatcher._execute_agent(
            compiled, season, week, job["team_id"], live=True, model=model,
            save_to_db=False, slot=job["slot"], article_type=job["article_type_id"],
            client=client, capture=capture,
        )
    finally:
        reset_article_context(token)
    usage_calls = capture.get("usage_calls") or []
    text = capture.get("text") or ""
    return {
        "model": model, "slot": job["slot"], "article_type_id": job["article_type_id"],
        "handle": job["handle"], "display_name": job["display_name"], "desk": job["desk"],
        "seconds": round(time.time() - started, 1),
        "text": text, "words": len(text.split()),
        "failure_reason": capture.get("failure_reason"),
        "rewrites": capture.get("rewrites") or [],
        "tool_calls": capture.get("tool_calls") or [],
        "tool_call_count": len(capture.get("tool_calls") or []),
        "usage_calls": usage_calls, "tokens": token_totals(usage_calls),
        "cost_usd": cost_usd(model, usage_calls),
        "evidence": job["task_instruction"] + "\n" + "\n".join(
            (c.get("result") or "") for c in (capture.get("tool_calls") or [])),
    }


# ---------------------------------------------------------------- artifacts

def _digits(text: str) -> set:
    """Numeric tokens normalised for comparison: commas dropped, trailing punctuation trimmed."""
    out = set()
    for match in re.findall(r"\d[\d,\.]*", text or ""):
        token = re.sub(r"[^0-9.]", "", match).rstrip(".")
        if token:
            out.add(token)
    return out


def grounding_flags(result: dict) -> list:
    """Numbers in the article that never appear in that run's own prompt or tool trace."""
    evidence = _digits(result.get("evidence") or "")
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", result.get("text") or ""):
        missing = sorted(d for d in _digits(sentence) - evidence if d)
        if missing:
            out.append({"numbers": missing, "sentence": sentence.strip()[:300]})
    return out


def blind_read(records: list, jobs: list, models: list, season: int, week: int, path: str):
    """A/B/C per article with the model names stripped, answer key at the end."""
    by_key = {(r["slot"], r["model"], r.get("repeat", 0)): r for r in records}
    lines = [f"# Article model comparison, {season} Week {week} (blind)", "",
             "The same assignment written by each model. Read A against B and C; the answer key is at the end.", ""]
    key_lines = []
    for job in jobs:
        slot = job["slot"]
        order = list(models)
        random.Random(f"{season}-{week}-{slot}").shuffle(order)
        labels = "ABC"[:len(order)]
        lines.append(f"## {slot} — {job['article_type_name']} ({job['handle']})")
        lines.append("")
        for label, model in zip(labels, order):
            record = by_key.get((slot, model, 0)) or {}
            lines.append(f"### {label}")
            lines.append("")
            lines.append(record.get("text") or f"*(no text: {record.get('failure_reason') or 'missing'})*")
            lines.append("")
        key_lines.append(f"- {slot}: " + ", ".join(f"{lbl} = {m}" for lbl, m in zip(labels, order)))
        lines.append("---")
        lines.append("")
    lines.append("## Answer key")
    lines.append("")
    lines.extend(key_lines)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_factual(records: list, directory: str):
    with open(os.path.join(directory, "factual_review.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample", "paragraph", "text", "claim", "source_reference", "expected_value",
                         "verdict", "reviewer_note"])
        for result in records:
            sample = hashlib.sha256(
                f"{result['slot']}:{result['model']}:{result.get('repeat', 0)}".encode()).hexdigest()[:10]
            for paragraph, text in enumerate((result.get("text") or "").split("\n\n"), 1):
                if text.strip():
                    writer.writerow([sample, paragraph, text, "", "", "", "unreviewed", ""])


def write_grounding(records: list, directory: str):
    with open(os.path.join(directory, "grounding.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["slot", "model", "repeat", "numbers", "sentence"])
        for result in records:
            for flag in grounding_flags(result):
                writer.writerow([result["slot"], result["model"], result.get("repeat", 0),
                                 " ".join(flag["numbers"]), flag["sentence"]])


def write_summary(records: list, models: list, jobs: list, directory: str, allowance: dict):
    lines = ["# Article model comparison summary", ""]
    lines.append(f"Allowance before/after (Go): {json.dumps(allowance, default=str)[:400]}")
    lines.append("")
    header = "| slot | model | words | tools | rewrites | seconds | input | cached | out+think | $ | flags |"
    for slot in [j["slot"] for j in jobs]:
        lines += [f"## {slot}", "", header, "|" + "---|" * 12]
        for result in [r for r in records if r["slot"] == slot]:
            tokens = result["tokens"]
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                slot, result["model"], result["words"], result["tool_call_count"],
                ",".join(result["rewrites"]) or "—", result["seconds"],
                tokens["input_tokens"], tokens["cached_tokens"],
                tokens["output_tokens"] + tokens["thinking_tokens"],
                result["cost_usd"] if result["cost_usd"] is not None else "?",
                len(grounding_flags(result))))
        lines.append("")
    lines.append("## Totals by model")
    lines.append("")
    lines.append("| model | articles | words | tools | seconds | $ |")
    lines.append("|---|---|---|---|---|---|")
    for model in models:
        rows = [r for r in records if r["model"] == model]
        if not rows:
            continue
        total = round(sum(r["cost_usd"] or 0 for r in rows), 4)
        lines.append(f"| {model} | {len(rows)} | {sum(r['words'] for r in rows)} | "
                     f"{sum(r['tool_call_count'] for r in rows)} | {round(sum(r['seconds'] for r in rows), 1)} | {total} |")
    with open(os.path.join(directory, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- judge

def _judge_call(client, model: str, system: str, prompt: str) -> dict | None:
    from google.genai import types
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.0, max_output_tokens=1200),
    )
    text = getattr(response, "text", "") or ""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    return data if data.get("winner") in ("a", "b", "tie") else None


def judge_pairs(records: list, jobs: list, models: list, judge_model: str, directory: str) -> dict:
    client, error = client_for(judge_model)
    if client is None:
        return {"error": error, "pairs": []}
    by_key = {(r["slot"], r["model"], r.get("repeat", 0)): r for r in records}
    pairs, verdicts = [], []
    for job in jobs:
        for i, model_a in enumerate(models):
            for model_b in models[i + 1:]:
                a = by_key.get((job["slot"], model_a, 0)) or {}
                b = by_key.get((job["slot"], model_b, 0)) or {}
                if not (a.get("text") and b.get("text")):
                    continue
                trace = ((a.get("evidence") or "")[-9000:] + "\n" + (b.get("evidence") or "")[-9000:])[:18000]
                head = (f"ASSIGNMENT: {job['article_type_name']} by {job['handle']} "
                        f"({job['archetype']}). Desk note: {job.get('desk_note') or 'none'}.\n\n"
                        f"TOOL TRACE (the only facts allowed):\n{trace}\n\n")
                prompt = head + f"VERSION 1:\n{a['text']}\n\nVERSION 2:\n{b['text']}"
                first = _judge_call(client, judge_model, JUDGE_RUBRIC, prompt)
                swapped = head + f"VERSION 1:\n{b['text']}\n\nVERSION 2:\n{a['text']}"
                second = _judge_call(client, judge_model, JUDGE_RUBRIC, swapped)
                if not first or not second:
                    verdict = "unjudged"
                else:
                    # `first` shows a as VERSION 1 and b as VERSION 2; `second` swaps them, so its
                    # "a" is model b. Normalise both to model keys before comparing.
                    def normalise(answer: dict, swapped: bool) -> str:
                        w = answer.get("winner")
                        if w == "tie":
                            return "tie"
                        if not swapped:
                            return w
                        return {"a": "b", "b": "a"}.get(w, w)

                    one = normalise(first, False)
                    two = normalise(second, True)
                    verdict = one if one == two else "order-dependent"
                pairs.append({"slot": job["slot"], "a": model_a, "b": model_b, "first": first,
                              "second": second, "verdict": verdict})
                verdicts.append(verdict)
    table = {}
    for model in models:
        table[model] = {"wins": 0, "losses": 0, "ties": 0, "order_dependent": 0}
    for pair in pairs:
        if pair["verdict"] in ("a", "b"):
            win, lose = (pair["a"], pair["b"]) if pair["verdict"] == "a" else (pair["b"], pair["a"])
            table[win]["wins"] += 1
            table[lose]["losses"] += 1
        elif pair["verdict"] == "tie":
            table[pair["a"]]["ties"] += 1
            table[pair["b"]]["ties"] += 1
        elif pair["verdict"] == "order-dependent":
            table[pair["a"]]["order_dependent"] += 1
            table[pair["b"]]["order_dependent"] += 1
    payload = {"judge": judge_model, "pairs": pairs, "scoreboard": table}
    with open(os.path.join(directory, "judge.json"), "w") as f:
        json.dump(payload, f, indent=1)
    with open(os.path.join(directory, "judge.md"), "w") as f:
        f.write(f"# Judge: {judge_model}\n\n| model | wins | losses | ties | order-dependent |\n|---|---|---|---|---|\n")
        for model, row in table.items():
            f.write(f"| {model} | {row['wins']} | {row['losses']} | {row['ties']} | {row['order_dependent']} |\n")
        f.write("\nPairs are judged twice with the order swapped; a split verdict is reported, never counted as a win.\n")
    return payload


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the same articles with several models, for a blind read.")
    ap.add_argument("--season", type=int, default=2036)
    ap.add_argument("--week", type=int, default=18)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--slots", nargs="+", default=DEFAULT_SLOTS)
    ap.add_argument("--db", default=None, help="Postgres DSN (default: madden_2036 on localhost)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=1, help="article threads per model; 1 keeps allowance attribution clean")
    ap.add_argument("--judge", default="glm-5.3", help="judge model ('' or --no-judge to skip)")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="compile and save prompts; make no model calls")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    dsn = args.db or os.environ.get("MU_COMPARE_DB") or DEFAULT_DB
    from tools.llm_compare import use_database_read_only
    use_database_read_only(dsn)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = args.out or os.path.join(ROOT, "verify_output", "model_compare", stamp)
    os.makedirs(directory, exist_ok=True)

    from newsroom.swarm_dispatcher import SwarmDispatcher
    dispatcher = SwarmDispatcher(debug_mode=False)
    jobs = plan_jobs(dispatcher, args.season, args.week, args.slots)
    if not jobs:
        print(f"No slots matched {args.slots} for {args.season} Week {args.week}.")
        return 1
    with open(os.path.join(directory, "prompts.json"), "w") as f:
        json.dump({"season": args.season, "week": args.week, "models": args.models, "jobs": jobs}, f, indent=1)
    print(f"[compare] {len(jobs)} article(s): " + ", ".join(j["slot"] for j in jobs))
    if args.dry_run:
        print(f"[compare] dry run: prompts written to {directory}")
        return 0

    records = []
    allowance = {"before": go_allowance()}
    for model in args.models:
        reason = allowance_guard(model, allowance["before"])
        if reason:
            print(f"[compare] skipping {model}: {reason}")
            continue
        client, error = client_for(model)
        if client is None:
            print(f"[compare] skipping {model}: {error}")
            continue
        print(f"[compare] {model}: {len(jobs)} article(s)")
        jobs_for_model = [(job, repeat) for job in jobs for repeat in range(max(1, args.repeats))]

        def one(pair):
            job, repeat = pair
            try:
                result = run_job(dispatcher, job, args.season, args.week, model, client)
            except Exception as exc:                     # one failure never ends the comparison
                result = {"model": model, "slot": job["slot"], "article_type_id": job["article_type_id"],
                          "handle": job["handle"], "display_name": job["display_name"], "desk": job["desk"],
                          "seconds": 0, "text": "", "words": 0, "failure_reason": f"{type(exc).__name__}: {exc}",
                          "rewrites": [], "tool_calls": [], "tool_call_count": 0, "usage_calls": [],
                          "tokens": token_totals([]), "cost_usd": 0.0, "evidence": ""}
            result["repeat"] = repeat
            print(f"[compare]   {job['slot']} #{repeat}: {result['words']} words, "
                  f"{result['tool_call_count']} tools, {result['seconds']}s, ${result['cost_usd']}")
            return result

        if args.workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                records.extend(pool.map(one, jobs_for_model))
        else:
            records.extend(one(pair) for pair in jobs_for_model)
        allowance[model] = go_allowance()

    with open(os.path.join(directory, "results.json"), "w") as f:
        json.dump(records, f, indent=1)
    blind_read(records, jobs, args.models, args.season, args.week, os.path.join(directory, "blind_read.md"))
    write_factual(records, directory)
    write_grounding(records, directory)
    write_summary(records, args.models, jobs, directory, allowance)

    if not args.no_judge and args.judge:
        print(f"[compare] judging with {args.judge}")
        verdict = judge_pairs(records, jobs, args.models, args.judge, directory)
        print("[compare] judge:", json.dumps(verdict.get("scoreboard", verdict.get("error")), indent=None))

    print(f"[compare] written to {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
