# GLM-5.3-Flash Unification: Findings and Measurements

Findings for `docs/done/GLM_5_3_UNIFICATION_PLAN.md`. That plan built the plumbing; this file is the
evidence for the one decision left, which is **not a build item**: which model writes each short
text site.

**Decision status: deferred by JR (2026-11-12).** The evidence is in hand and nothing is blocked;
JR is holding the choice because he wants to improve the writing side of these models — prompting,
per-archetype instructions, the facts packets — before switching, and because he does not rate
`gemini-3.8-flash` highly either, so "beats Gemini" is not a satisfying bar on its own. The decision
is tracked in **`docs/CLOSE_OUT_CHECKLIST.md` P8-06b** (and P8-06's per-job routing table), not in
this plan. **No default is flipped:** every site is still on `gemini-3.8-flash` until that row is
settled.

Every number below was taken on `go:glm-5.3-flash` through the `go` provider
(`engines/llm_provider.py`, plan U1), not through a separate client. Nothing was written to a
database; the archive runs were read-only.

**Go allowance when the probes ran (2026-11-12, ~12:05 EST):** 5-hour window 7% (resets 18:50Z),
weekly 39%, via `python3 -m tools.go_usage --json` (source `api`). Every measurement below cost under
$0.01 of list tokens in total.

## 1. Summary: what passes, what does not

| Site | Gate | Result | Read |
|---|---|---|---|
| Inbox voice | p95 ≤ 4.0 s warm | **3.06 s p95**, 0 failures (§2) | passes (PC number with Madden open still to take) |
| Inbox voice | JR's read of replies | **5–4 to GLM** over 9 cases (§8) | a wash, no regression either way |
| Agent Wire | JSON parse | **6/6** (§4) | passes |
| Agent Wire | JR's blind read of 20 posts | **Gemini 46 pts, GLM 35, Muse 39** (§8) | **fails** — GLM last on 11 of 20 |
| Memory takes | JSON parse | **3/3** (§4) | passes |
| Memory summaries | JR's read of a block | not taken | open |
| Scouting prose | JR's read of 10 reports | not taken | open |
| GM lines | `llm_compare` GM tasks | not taken | open |
| Press | one dry conference + JR's read | not taken (§3) | open |

**What that means for a routing table today:** Agent Wire should stay on Gemini on this evidence;
the inbox is a coin flip on voice with its latency gate already cleared; memories, scouting, GM
lines and press have no read yet, so they stay Gemini by default. A decision to move everything
onto GLM now would be justified by allowance accounting, not by measured quality.

## 2. Inbox voice latency (R1, the hard gate)

Budget: `INBOX_LLM_BUDGET_S = 4.0` s (P1-09, `docs/reference/P1_LATENCY_AND_INBOX_GATE.md`).
Method: `python3 -m tools.latency_spike inbox --calls N --model "go:glm-5.3-flash:low"` — one warm
call, then N measured calls on a filled CONTRACT prompt (1,463 characters), same prompt P1-09 used.

| Thinking level | Calls | p50 | p95 | max | Failures | Thinking tokens | Cost |
|---|---|---|---|---|---|---|---|
| `low` | 20 | 2.28 s | **3.06 s** | 3.43 s | 0 | 0 | $0.00236 |
| `default` (no cap) | 10 | 2.32 s | 2.67 s | 2.67 s | 0 | 0 | $0.00118 |

- **Passes on this machine, with ~0.94 s of p95 headroom** (3.06 s against 4.0 s). The P1-09 method
  wants the PC in its normal state with Madden open; these probes ran without Madden. **The real
  p95 on JR's PC is still to be taken** (same command, Madden open) before `INBOX_MODEL` moves.
- Reply shape: all 30 replies were a single JSON object (`message` + appraisal fields).
- The model reports no `thinking_tokens` at either level, so the `low` cap is a no-op for this
  model rather than a cost saving — R2's worry (slow, expensive short calls) did not materialise.
  Keep `low` anyway: it is free and pins the intent.

## 3. Press conferences

Per-call cost and latency are the same `GoText` path measured above (2.28 s p50 / 3.06 s p95 for a
~1.5k-character prompt; a presser's calls are longer — 1.8k–2.5k characters on the `memory_takes`
inputs — and `run_week` runs `PRESS_WORKERS = 6` at once). Not measured yet:

- a 10-question dry conference (`python3 -m newsroom.press_room run-week --season S --week W
  --team ABBR --dry-run` prints casts and prompts only; a measured run needs a scratch database),
  with its wall time, calls, tokens and per-call p50/p95 (`tools/latency_spike press`);
- JR's read of one whole presser.

## 4. JSON-parse rates on the real prompts (`tools/llm_compare`)

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
article's own model to `gemini_text`, and articles run on `glm-5.3-flash` since `ac8df19`. Before the
plan's U3 stood up the `go` route, that call asked Gemini for a `glm-5.3-flash` model and failed
quietly (`MEMORY NOT RECORDED`). U3 restores takes extraction on Go as a side effect; the 3/3 parse
above is its first evidence. Season **summaries** still use `AGENT_MEMORY_MODEL` (default
`gemini-3.8-flash`).

## 5. Agent Wire comparison method

The decision here is a blind read, not a latency one: Agent Wire has no 4 s budget.

```
python3 -m tools.social_model_compare --season <S> --week <W> \
    --models gemini-3.8-flash glm-5.3-flash muse-spark-1.3-contributor --prompts new
```

on a finished week (read-only; writes nothing to the database). It saved the posts, prompts and
usage to `captures/social_compare/2035_w9_20261112_125730.json`. The blinded read was assembled
from that file with a per-post shuffled A/B/C order and a withheld key.

## 6. Memories, scouting prose and GM lines

**Gate:** JSON/take parse rate 100% and JR's read of the output (a memory block, 10 scouting
reports, `tools/llm_compare`'s GM tasks). The takes parse rate is 3/3 (§4); the three reads are
still open, so these sites stay on Gemini.

## 7. Deliberately out of scope

- Embeddings: `gemini-embedding-001` stays (`newsroom/league_memory.py`), since Go serves no
  embedding model.
- Vision: `extract.py` screenshot extraction stays on Gemini.
- Article writing: already on `glm-5.3-flash` (`ac8df19`), priced and read in
  `docs/reference/MODEL_ROUTING_EVALUATION.md`.

## 8. JR's blind reads (2026-11-12)

Both reads were blind: the per-item A/B/C order was randomized and the key withheld until JR had
ranked every item. Nothing was written to a database.

### Agent Wire — 2035 Week 9, 20 posts, 3-way (Gemini / GLM-5.3-Flash / Muse Spark)

Scores are 3/2/1 by JR's rank (first / second / third).

| Model | Points | Firsts | Lasts |
|---|---|---|---|
| `gemini-3.8-flash` | **46** | **10** | 4 |
| `muse-spark-1.3-contributor` | 39 | 4 | 5 |
| `glm-5.3-flash` | 35 | 6 | **11** |

GLM's six firsts: posts 1, 3, 7, 8, 17, 20 — mostly short reaction and performance posts. It was
last on 11 of 20, and no model was flagged by the writer's own guards (0/20 each). **This is the
plan's R6 in the data:** Agent Wire is the one site where voice is the product, and GLM is not tuned
against 192 accounts. Muse does not beat Gemini either.

### Inbox voice — 9 production-shaped cases, 2-way (Gemini / GLM-5.3-Flash)

JR preferred **GLM on 5** and Gemini on 4: GLM won the LB rejection, the accepted extension, the
talk turn, the beat question and the owner expectation; Gemini won the WR counter, the GM counter,
the GM rejection and the role talk. **A wash on voice**, with the latency gate already cleared.
JR flagged the beat question in both versions as off-brief (a reporter would not ask a GM to confirm
his own practice report) — a prompt issue, not a model one.

## 9. What would change the recommendation

JR's reason for deferring is that the writing can be improved on either model, so a fair next
comparison is not the same prompts again. In rough order of leverage:

1. **Per-archetype prompt work first, then re-read.** Agent Wire's loss tracks persona fidelity more
   than football sense; the post shapes, `post_style` and `SOCIAL_RULES` were tuned against Gemini
   output. A model-neutral prompt pass, then the same blind read, would answer whether GLM is
   actually worse or just differently tuned.
2. **A baseline JR respects.** He does not rate `gemini-3.8-flash` as a voice benchmark. A
   comparison against the best Go voice model (Muse Spark was close at 39) or a re-scored Gemini
   would be more useful than "beats 3.8 Flash".
3. **The unread sites.** Memory blocks, 10 scouting reports and the GM tasks are cheap to read and
   cover three of the four remaining sites.
4. **The PC p95.** The inbox number that matters is the one with Madden open.
5. **Cost, once quality says "either".** Gemini's short calls are billed cash (~$45/mo measured);
   a Go model draws on its monthly allowance. If quality ties, allowance accounting decides.
