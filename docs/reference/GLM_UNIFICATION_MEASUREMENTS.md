# GLM-5.3-Flash Unification: Measurements

Reference worksheet for `docs/done/GLM_5_3_UNIFICATION_PLAN.md`. It records what was
measured before a site may flip its default to `glm-5.3-flash` on OpenCode Go, and what is still
unmeasured. The plan flips nothing itself (U4 is JR's): this file is the evidence a flip needs.

Every number below was taken on `go:glm-5.3-flash` through the new `go` provider
(`engines/llm_provider.py`, plan U1), not through a separate client.

**Go allowance when the probes ran (2026-11-12, ~12:05 EST):** 5-hour window 7% (resets 18:50Z),
weekly 39%, via `python3 -m tools.go_usage --json` (source `api`). Every measurement below cost under
$0.01 of list tokens in total (the two inbox probes $0.0035, the `llm_compare` arms ~$0.0032).

## 1. Inbox voice (R1, the hard gate)

Budget: `INBOX_LLM_BUDGET_S = 4.0` s (P1-09, `docs/reference/P1_LATENCY_AND_INBOX_GATE.md`).
Method: `python3 -m tools.latency_spike inbox --calls N --model "go:glm-5.3-flash:low"` — one warm
call, then N measured calls on a filled CONTRACT prompt (1,463 characters), same prompt P1-09 used.

| Thinking level | Calls | p50 | p95 | max | Failures | Thinking tokens | Cost |
|---|---|---|---|---|---|---|---|
| `low` | 20 | 2.28 s | **3.06 s** | 3.43 s | 0 | 0 | $0.00236 |
| `default` (no cap) | 10 | 2.32 s | 2.67 s | 2.67 s | 0 | 0 | $0.00118 |

- **Verdict: passes on this machine, with ~0.94 s of p95 headroom** (3.06 s against the 4.0 s
  budget). The P1-09 method wants the PC in its normal state with Madden open; these probes ran
  without Madden running. **The real p95 on JR's PC is still to be taken** (same command, with
  Madden open) before U4 flips `INBOX_MODEL`.
- Reply shape: all 30 replies were a single JSON object (`message` + appraisal fields), i.e. no
  format drift on the prompt the inbox actually sends.
- The model returns no `thinking_tokens` at either level (the transport reports 0), so the `low`
  cap is a no-op here rather than a cost saving — R2's worry (slow, expensive short calls) does
  not materialise for this model. Keep `low` anyway: it is free and pins the intent.

## 2. Press conferences

Per-call cost and latency are the same `GoText` path measured above (2.28 s p50 / 3.06 s p95 for a
~1.5k-character prompt; a presser's calls are longer — 1.8k–2.5k characters on the `memory_takes`
inputs — and `run_week` runs `PRESS_WORKERS = 6` at once). What is **not** measured yet:

- a 10-question dry conference (`python3 -m newsroom.press_room run-week --season S --week W
  --team ABBR --dry-run` prints casts and prompts only; a measured run needs a scratch database),
  with its wall time, calls, tokens and per-call p50/p95 (`tools/latency_spike press`);
- JR's read of one whole presser.

No press default flips until both exist.

## 3. JSON-parse rates on the real prompts (`tools/llm_compare`)

Read-only archive run (`madden_2036`, `PGOPTIONS` read-only, `log_tokens=False`), one Go arm:

| Task | Calls | PASS (schema-valid JSON) | Per call | Cost |
|---|---|---|---|---|
| `agent_wire` (post JSON) | 6 | **6/6** | 0.85–2.18 s | ~$0.0003 |
| `memory_takes` (takes JSON) | 3 | **3/3** | 2.13–3.94 s | ~$0.0004 |
| `inbox_reply` (message + appraisal) | 2 | **2/2** | 1.28–2.41 s | ~$0.0001 |

Command: `python3 -m tools.llm_compare --task <task> --season 2035 --week 9 --models go:glm-5.3-flash --limit N`.
The harness validates each reply's schema, so a PASS is a parse, not a hand read. Occasional thinking
tokens (8 and 28 on two calls) appear even though the inbox probe reported none; they are small.

**Memory takes now follow the article model.** `SwarmDispatcher._record_agent_memory` passes the
article's own model to `gemini_text`, and articles run on `glm-5.3-flash` since `ac8df19`. Before U3
stood up the `go` route, that call asked Gemini for a `glm-5.3-flash` model and failed quietly
(`MEMORY NOT RECORDED`). U3 therefore restores takes extraction on Go as a side effect; the 3/3 parse
above is its first evidence. Season **summaries** still use `AGENT_MEMORY_MODEL` (default
`gemini-3.8-flash`) until U4.

## 4. Agent Wire

The decision here is a blind read, not a latency one: Agent Wire has no 4 s budget. The artefact is
produced by

```
python3 -m tools.social_model_compare --season <S> --week <W> \
    --models gemini-3.8-flash glm-5.3-flash muse-spark-1.3-contributor --prompts new
```

on a finished week (it reads the database read-only and writes nothing), which saves the posts,
prompts and usage to `captures/social_compare/` and a blinded A/B/C Markdown file for JR.

**Gate:** GLM's 20 posts are not worse than Gemini's on voice and hashtag hygiene, and the
JSON-parse rate is 100%. **Pending:** the read itself.

## 5. Memories, scouting prose and GM lines

**Gate:** JSON/take parse rate 100% and JR's read of the output (a memory block, 10 scouting
reports, `tools/llm_compare`'s GM tasks). The takes parse rate is 3/3 (§3); **pending:** JR's read of
a memory block, the 10 scouting reports and the GM tasks; no default flips first.

## 6. What is deliberately not measured here

- Embeddings: `gemini-embedding-001` stays (`newsroom/league_memory.py`), since Go serves no
  embedding model.
- Vision: `extract.py` screenshot extraction stays on Gemini.
- Article writing: already on `glm-5.3-flash` (`ac8df19`), priced and read in
  `docs/reference/MODEL_ROUTING_EVALUATION.md`.
