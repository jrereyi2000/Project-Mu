# P1-09 and P1-10: inbox latency, press latency, press constants

Measurement report for `CLOSE_OUT_CHECKLIST.md` **P1-09** (inbox latency spike + `llm_compare` gate)
and **P1-10** (press cloud latency + constants re-check). Taken 2026-11-09 on the PC, with Madden 27
running (process `Madden27`, main menu), the save watcher stopped, and model routing `gemini-3.8-flash`
(the provider default for both `source="press"` and `make_llm("inbox")`).

Raw JSON: `verify_output/latency_spike/press_20261109_175058.json`,
`verify_output/latency_spike/inbox_20261109_175149.json`,
`verify_output/compare_suite_20261109_173516.json`.
Tools: `tools/latency_spike.py` (both spikes), `tools/llm_compare.py` (the gate).

## P1-10 first half — press constants (corpus-derived)

`python3 -m tools.press_corpus stats` re-read the local corpus: **175 transcripts, 2,092 questions**,
mean **11.954** questions per presser, median **11.0**, p10 7 / p90 17, p90 length 40. Loss pressers are
longer than wins (**13.106 vs 10.867**), matching `PRESS_CONFERENCE_CORPUS.md` §5.4.

Both assumptions still hold, so **no constant changed**:

- `BASE_PATIENCE = 14` — still the proposal from §8 (a base above the typical presser; the corpus mean is
  11.95, the median 11).
- `LOSS_ADJUST = 0` — still no loss penalty; loss rooms ran longer, not shorter.

`python3 -m tools.press_corpus eval` was smoke-tested against the corpus itself: all four checks pass and
the tooling runs (a generated-presser directory was not available offline; the eval's real use is a dry
week's generated transcripts).

## P1-10 second half — one warm cloud presser

One AI press conference (Seattle, 2026 week 1, after a 27–30 home loss) through
`newsroom.press_room.run_week` with `workers=1`, on a throwaway clone of the database
(`madden_press_scratch`, a template copy of `madden_mu2026`). The live franchise and every save were
untouched. One warm-up call preceded the measured run.

| | Measured (one presser) | Plan §2.10 estimate |
|---|---:|---:|
| Model calls | **83** | ~75 |
| Input tokens | **278,679** | ~280k |
| Output tokens (+ thinking) | **9,200 (+156)** | 12–25k |
| Cost at the repo's table | **$0.0474** | ~$0.05–0.06 |
| Wall time | **111.1 s (1 min 51 s)** | ~1.5–2 min per presser |
| Per-call latency | **p50 2.251 s, p95 3.324 s** (mean 2.353, min 1.403, max 3.593) | — |

The call count, input tokens, cost and per-presser wall time all land inside the plan's estimates; output
tokens came in below the 12–25k band, which is the one figure worth watching if the transcript is ever
asked to grow. At 6 pressers in parallel the week would be ≈32 pressers, ≈2,650 calls and ≈$1.52, against
§2.10's ~2,400 calls and $1.5–2 — close enough to keep the estimate.

## P1-09 — inbox latency and the budget constant

`make_llm("inbox")` on a filled CONTRACT prompt (the appraisal shape: message + private appraisal,
1,463 prompt characters, ~465 input tokens), one warm-up call then **20 measured calls**, Madden open:

| p50 | p95 | mean | min | max | failures |
|---:|---:|---:|---:|---:|---:|
| **1.988 s** | **2.462 s** | 2.106 s | 1.852 s | 2.501 s | 0 |

20 calls: 9,300 input + 2,256 output tokens, **$0.00275** total (≈$0.00014 per call).

`engines/inbox/voice.py` `INBOX_LLM_BUDGET_S` was set from this: **8.0 s (an estimate) → 4.0 s** —
the measured p95 (2.46 s) plus roughly 60% headroom, still 1.6× the slowest call seen. Templates remain
the fallback when a call runs past it.

## P1-09 — the `llm_compare` gate (D7)

`tools/llm_compare.py` had no inbox task. Two were added, following the existing task pattern, both built
from the **real** `engines.inbox.voice.prompt()` (a synthesised CONTRACT thread with production-shaped
facts, one COUNTER case and one REJECTED case):

- `inbox_reply` — the counterpart's words, no appraisal (the `judge=False` shape).
- `inbox_appraisal` — the same reply plus the private appraisal, validated with
  `engines.inbox.appraisal.normalize` (the production call's shape).

Neither is added to `JSON_TASKS`: production does not ask for a JSON MIME type, so the gate does not either.

Run: `python3 -m tools.llm_compare --suite --tasks inbox_reply inbox_appraisal --models gemini-3.8-flash:low --limit 2`.

**First run: 50% (2/4).** The COUNTER case failed in both tasks with `amount $88M not on the sheet`. This
is a real defect, not a bad model answer: the inbox's CONTRACT facts carry dollars as integers
(`88000000`), while `engines.gm_rationale.validate_rationale` only recorded money written as `"$88.0M"`
strings. The inbox's own template (`I'm looking for 4 years, $88.0M total…`) fails the same check, so every
model reply that named a dollar amount was being vetoed to the template.

Fixed minimally in `engines/gm_rationale.py` (`validate_rationale`): a dollar amount now also grounds
against a plain sheet number within the same $51k tolerance. `tests/test_gm_rationale.py` covers an
int-dollar sheet (accepts `$88M`, rejects `$95M`).

**Re-run after the fix: 100% (4/4), 9.1 s, $0.0004.** The gate now passes for both tasks on the production
model. Reports: `verify_output/compare_suite_20261109_173516.{json,html}`.

Scope note: the gate covers CONTRACT, the kind with the filled-negotiation facts the plan's spike names.
The other inbox kinds (GM_TALK, ROLE_TALK, REPORTER, owner, scout) are not in the gate.
