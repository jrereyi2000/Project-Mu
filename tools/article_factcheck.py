"""Veracity check for compared articles: does the copy agree with the evidence its writer had?

Reads a `tools/article_model_compare.py` output directory (`prompts.json`, `results.jsonl`) and
for each article asks a checker model to judge every factual claim against that run's own
evidence — the assignment brief plus the tool results the writer actually received.

Three verdicts per claim:

- **SUPPORTED** — the evidence states it.
- **CONTRADICTED** — the evidence states something different for the same thing (the real error).
- **UNSUPPORTED** — the evidence does not mention it. Not an error by itself: a fact can come from
  the brief, from arithmetic on evidence values, or from the system prompt, none of which is fully
  in front of the checker.

So the headline number is `contradicted / claims`, and `supported / claims` is the grounding share.
A claim the checker cannot tie to the evidence is reported, never counted as a lie.

  python3 -m tools.article_factcheck --dir verify_output/model_compare/20261112_053537
  python3 -m tools.article_factcheck --dir ... --model gpt-6-luna --slot "Slot 4"

Writes `veracity.jsonl` (one article per line, resumable), `veracity.csv` (one claim per line) and
`veracity.md` (per-model summary) beside the articles. Costs a few cents per pass on Go.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The rubric, the parser and the no-checker behaviour live in the gate module now, so the offline
# audit and the write-time gate judge claims by the same rules.
from newsroom.fact_gate import (  # noqa: E402
    ask_checker,
    client_for_model,
    count_claims,
    normalize_claims,
    render_prompt,
)


def _load(dirpath: str):
    prompts = json.load(open(os.path.join(dirpath, "prompts.json")))
    briefs = {job["slot"]: job.get("task_instruction") or "" for job in prompts.get("jobs", [])}
    results = [json.loads(line) for line in open(os.path.join(dirpath, "results.jsonl")) if line.strip()]
    return briefs, results


def _prompt(result: dict, brief: str) -> tuple[str, bool]:
    """The prompt and whether its evidence was cut (the brief is already the evidence's head)."""
    return render_prompt(result.get("text") or "", result.get("evidence") or "", brief)


def verify(dirpath: str, model: str, slots: list | None, max_claims: int) -> int:
    briefs, results = _load(dirpath)
    if slots:
        wanted = {s.lower() for s in slots}
        results = [r for r in results if r["slot"].lower() in wanted]
    out_path = os.path.join(dirpath, "veracity.jsonl")
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path):
            try:
                row = json.loads(line)
            except Exception:
                continue
            done.add((row["slot"], row["model"]))
        print(f"[factcheck] resuming: {len(done)} article(s) already checked")

    client, error = client_for_model(model)
    if client is None:
        print(f"[factcheck] no checker: {error}")
        return 1

    for result in results:
        key = (result["slot"], result["model"])
        if key in done:
            continue
        prompt, truncated = _prompt(result, briefs.get(result["slot"], ""))
        answer = None
        try:
            # Temperature 0, low thinking, thinking-inclusive output cap (see newsroom/fact_gate.py).
            answer = ask_checker(client, model, prompt, max_claims, truncated)
        except Exception as exc:
            print(f"[factcheck] {key}: {type(exc).__name__}: {exc}")
        if not answer:
            print(f"[factcheck] {key}: no parsable answer")
            continue
        claims = normalize_claims(answer, max_claims)
        counts = count_claims(claims)
        row = {"slot": result["slot"], "model": result["model"], "claims": claims,
               "counts": counts, "claim_total": len(claims), "note": answer.get("note", ""),
               "evidence_truncated": truncated}
        with open(out_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[factcheck] {result['slot']:12s} {result['model']:30s} "
              f"claims={len(claims):3d} ok={counts['SUPPORTED']:3d} "
              f"wrong={counts['CONTRADICTED']:3d} unknown={counts['UNSUPPORTED']:3d}"
              + ("  [truncated evidence]" if truncated else ""))

    rows = [json.loads(line) for line in open(out_path) if line.strip()] if os.path.exists(out_path) else []
    write_reports(dirpath, rows)
    return 0


def write_reports(dirpath: str, rows: list) -> None:
    with open(os.path.join(dirpath, "veracity.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["slot", "model", "claim", "verdict", "derived", "evidence"])
        for row in rows:
            for claim in row["claims"]:
                writer.writerow([row["slot"], row["model"], claim.get("claim", ""),
                                 str(claim.get("verdict", "")).upper(),
                                 bool(claim.get("derived")), claim.get("evidence", "")])

    models = sorted({row["model"] for row in rows})
    lines = ["# Veracity of the compared articles", "",
             "Claims are judged only against the evidence each writer retrieved. CONTRADICTED is a "
             "disagreement with the evidence; UNSUPPORTED means the evidence did not mention it (not "
             "an error by itself).", "",
             "| model | articles | claims | supported | contradicted | unsupported | contradicted % | grounded % |",
             "|---|---|---|---|---|---|---|---|"]
    for model in models:
        mine = [r for r in rows if r["model"] == model]
        claims = sum(r["claim_total"] for r in mine)
        c = {k: sum(r["counts"].get(k, 0) for r in mine) for k in ("SUPPORTED", "CONTRADICTED", "UNSUPPORTED")}
        wrong_pct = (100.0 * c["CONTRADICTED"] / claims) if claims else 0.0
        grounded_pct = (100.0 * c["SUPPORTED"] / claims) if claims else 0.0
        lines.append(f"| {model} | {len(mine)} | {claims} | {c['SUPPORTED']} | {c['CONTRADICTED']} | "
                     f"{c['UNSUPPORTED']} | {wrong_pct:.1f}% | {grounded_pct:.1f}% |")
    lines += ["", "## Contradicted claims", ""]
    for row in rows:
        wrong = [c for c in row["claims"] if str(c.get("verdict", "")).upper() == "CONTRADICTED"]
        if not wrong:
            continue
        lines.append(f"### {row['slot']} — {row['model']}")
        lines.append("")
        for claim in wrong:
            lines.append(f"- **{claim.get('claim', '')}** — evidence: {claim.get('evidence', '')}")
        lines.append("")
    with open(os.path.join(dirpath, "veracity.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Judge article claims against the evidence their writer had.")
    ap.add_argument("--dir", required=True, help="a tools/article_model_compare.py output directory")
    ap.add_argument("--model", default="deepseek-v4.1-flash", help="checker model (Go or Gemini id)")
    ap.add_argument("--slot", nargs="*", default=None, help="only these slots")
    ap.add_argument("--max-claims", type=int, default=40)
    args = ap.parse_args(argv)
    return verify(args.dir, args.model, args.slot, args.max_claims)


if __name__ == "__main__":
    sys.exit(main())
