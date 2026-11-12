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

from tools.article_model_compare import _answer_text, client_for, _json_blobs  # noqa: E402

RUBRIC = """You are a fact-checker for a fictional NFL league's newsroom. You get an article, the \
assignment it answered, and the evidence its writer retrieved from the league database. Judge the \
article's factual claims ONLY against that evidence.

Claim types: numbers (yards, records, scores, ranks, percentages, ages, money), names and roles \
(player on team, coach on team), events (a trade, a firing, a result), and stated standings.

For each claim output one of:
- "SUPPORTED": the evidence states this value/fact.
- "CONTRADICTED": the evidence gives a DIFFERENT value/fact for the same thing. Use this only when \
the evidence positively says otherwise; never infer it from silence.
- "UNSUPPORTED": the evidence does not mention it. This is not an error; say so and move on.

Rules:
- Cluster a sentence's numbers into one claim per fact (a stat line is several claims).
- Skip pure opinion, prediction, style, and obvious arithmetic the writer did on evidence values \
(mark arithmetic "SUPPORTED" and set "derived": true).
- Quote the evidence line for every SUPPORTED or CONTRADICTED claim, 12 words maximum.
- Cap at 40 claims, the most load-bearing first.
- Never use outside knowledge about real NFL players or teams; this league is fictional.

Reply with JSON only:
{"claims": [{"claim": "...", "verdict": "SUPPORTED|CONTRADICTED|UNSUPPORTED", "evidence": "...", \
"derived": false}], "note": "one sentence on the article's overall accuracy"}"""


def _load(dirpath: str):
    prompts = json.load(open(os.path.join(dirpath, "prompts.json")))
    briefs = {job["slot"]: job.get("task_instruction") or "" for job in prompts.get("jobs", [])}
    results = [json.loads(line) for line in open(os.path.join(dirpath, "results.jsonl")) if line.strip()]
    return briefs, results


def _prompt(result: dict, brief: str) -> str:
    evidence = (result.get("evidence") or "").strip()
    # The brief is already the head of `evidence`; keep it, and bound the whole thing.
    body = evidence[:24000]
    return (f"ASSIGNMENT (what the writer was asked to produce):\n{brief[:6000]}\n\n"
            f"EVIDENCE THE WRITER RETRIEVED (the only facts it may use):\n{body}\n\n"
            f"ARTICLE:\n{result.get('text') or ''}")


def _parse(text: str) -> dict | None:
    for blob in reversed(_json_blobs(text or "")):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        if isinstance(data.get("claims"), list):
            return data
    return None


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

    client, error = client_for(model)
    if client is None:
        print(f"[factcheck] no checker: {error}")
        return 1

    for result in results:
        key = (result["slot"], result["model"])
        if key in done:
            continue
        prompt = _prompt(result, briefs.get(result["slot"], ""))
        answer = None
        try:
            from google.genai import types
            # Thinking shares the output cap on Go's reasoning models: at 8,192 DeepSeek V4.1 Flash
            # spent the whole budget thinking and returned nothing. The cap is generous and the
            # thinking level is asked low; neither changes the answer, both make one arrive.
            config = types.GenerateContentConfig(system_instruction=RUBRIC, temperature=0.0,
                                                 max_output_tokens=32768)
            try:
                config.thinking_config = types.ThinkingConfig(thinking_level="LOW")
            except Exception:
                pass
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            answer = _parse(_answer_text(response))
        except Exception as exc:
            print(f"[factcheck] {key}: {type(exc).__name__}: {exc}")
        if not answer:
            print(f"[factcheck] {key}: no parsable answer")
            continue
        claims = answer.get("claims", [])[:max_claims]
        counts = {"SUPPORTED": 0, "CONTRADICTED": 0, "UNSUPPORTED": 0}
        for claim in claims:
            verdict = str(claim.get("verdict", "")).upper()
            if verdict in counts:
                counts[verdict] += 1
        row = {"slot": result["slot"], "model": result["model"], "claims": claims,
               "counts": counts, "claim_total": len(claims), "note": answer.get("note", "")}
        with open(out_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[factcheck] {result['slot']:12s} {result['model']:30s} "
              f"claims={len(claims):3d} ok={counts['SUPPORTED']:3d} "
              f"wrong={counts['CONTRADICTED']:3d} unknown={counts['UNSUPPORTED']:3d}")

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
