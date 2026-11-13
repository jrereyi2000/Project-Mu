# Open decisions — only JR can make these

**Written 2026-11-12 by `deepseek-v4.1-flash (decide-e)`. Briefs only: nothing built, merged, or applied.**
Nine sections, eleven calls. Each: the question, the options, what it unblocks, the evidence, my one-line
recommendation, and the smallest reversible first step. When you answer one on the board, this file is edited
to say **DECIDED <date> — "your words"**.

**Status after the 2026-11-12 recheck (this pass): 4 calls still open.** DECIDED: **D1** (merge `68f58943`),
**D3** (P8-06b deferred — improve the writing, then re-read), **D6** (no constant applied; the question belongs
to P5-06), **D7** (through D1, implicit), **D9-order** (P4-01 is running now, P5-02 held behind its verdict).
Still open: **D2** (narrowed by `8fbac3e3`), **D4**, **D5**, **D8/P8-13**. Two optional one-liners: pin **P8-15**
to post-2034 and make **D7** explicit.

---

### D1 · Merge branch `p3-decision-fit`?
**DECIDED 2026-11-12 — "Okay fix the merge" (JR).** Merged into `main` as **`68f58943`** with the combined
resolution option (a): main's R9/R8/P3-04a attribution kept, the branch's "P3-06a done" and "corrected-engine
re-run open" added, main's P3-08 row kept beside the branch's P3-06a/P3-07 rows. `tests/test_decision_compare.py`
gave 42 passed / 20 skipped at the merge (the Postgres cases need a database). The branch is not deleted; the
checklist's stale P5-01a and visual-mod-check lines were corrected in the same pass. **Resolved by
`deepseek-v4.1-flash (orchestrator)` on JR's word — not by an agent acting alone.**
**Question.** `git merge p3-decision-fit` (`6ea814fc`) conflicts on two lines of `docs/CLOSE_OUT_CHECKLIST.md`;
do we merge it with the mechanical resolution both sessions described?

**Options.** (a) Merge with the combined resolution: keep main's R9/R8/P3-04a attribution, add the branch's
"P3-06a done (247 entrants, ≥224 drafted)" and "corrected-engine re-run open", and take main's P3-08 row beside
the branch's P3-06a/P3-07 rows. (b) Hold it. (c) "Take theirs" — **unsafe**: the branch predates today's P3-08,
P7-00e and P8-12 commits and would silently revert them (M-0852).

**Unblocks.** P3-06a ☑, P3-07's F4/F5 tests, the P3-02 row's true state; the whole P3 lane stops being a branch.
**Evidence.** M-0844 (conflict list), M-0850 (exact resolution + branch contents `d6f451ee`/`d0330764`),
`docs/CLOSE_OUT_CHECKLIST.md:17-23`, `:235`, `:248-249`.
**Recommend.** (a) — it is a two-hunk paste, and nothing else can tick P3-06a without it.
**Done 2026-11-12** (see the DECIDED note above): `68f58943`.

---

### D2 · P8-04 F1 — is the record book or the season line authoritative?
**Still open — but `8fbac3e3` (2026-11-12, P8-04 F2–F5) shrank it.** The reconcile is now one-directional: a
recorded line may only make a mark *worse*, so it can never upgrade a result (Davis's tie defect is fixed that
way, in code, without this decision). What is left is exactly the Crawford direction.
**Question.** Should a recorded season line that makes a mark **worse** override a BOOK row — the only direction
still allowed — or should BOOK stay authoritative and the tool only report the dispute?

**Options.** (a) Recorded line wins, applied in **one shared place** for `get_record_book`,
`find_records_broken_in_week`, `press_facts` and `#records`, spec §2.9 updated. (b) BOOK stays authoritative;
the tool **reports** `source_conflict` ("disputed — do not call it broken") and keeps BOOK's result in both
directions. (c) Leave it — not viable: one response contradicts itself.
**Unblocks.** P8-04 D3/F1 CLOSED and the record/citation surfaces agreeing with each other.
**Evidence.** `data/audit_reports/audit-p8-dossier/p8-04-recheck.md` §D3/F1 (Crawford repro, DB rows);
`newsroom/db_tools.py` `reconcile_record_events` (the one-directional rule and `kept_book_result`) and
`get_record_book`'s `records_set_this_season`; `engines/records_engine.py:14`; `PROJECT_SPEC.md:1044` (§2.9).
Crawford still flips: book 67, recorded 71, new 69 → "ranks" (that is the *worse* direction, so the fix permits it).
**Recommend.** (b) — no surface disagrees, Davis still fixed, no unconfirmed number becomes a league record, and
it reverses by keeping BOOK's result instead of the resolved one.
**First step.** One builder takes the `kept_book_result` branch unconditionally and adds the dispute note to the
payload; a fake-cursor test asserts BOOK's result plus the note. One file, one test, revert = delete the line.

---

### D3 · P8-06b — which model writes the decision call sites?
**DECIDED 2026-11-12 — deferred by JR, not open.** The recorded ruling (`CLOSE_OUT_CHECKLIST.md` P8-06b):
"**Decision deferred by JR (2026-11-12):** improve the writing and prompting first and re-read, because passing
`gemini-3.8-flash` is not the bar he wants — he does not rate 3.8 Flash as a voice benchmark either." Every site
stays on Gemini until then; nothing is flipped.
**What reopens it.** The per-archetype prompt pass (`GLM_UNIFICATION_MEASUREMENTS.md` §9.1), then the same blind
read; the three unread sites (a memory block, 10 scouting reports, the `llm_compare` GM tasks) and the inbox p95
with Madden open are measurements to collect on the way, not decisions.
**Unblocks once decided.** P8-06b ☑ and P8-06's per-job routing table; ~$45/mo of Gemini cash calls.
**Evidence.** `docs/reference/GLM_UNIFICATION_MEASUREMENTS.md` §1, §2 (p95 3.06 s vs 4.0), §8 (Agent Wire Gemini
46 / GLM 35 / Muse 39; inbox 5–4 GLM, a wash), §9 (what would change it); checklist P8-06b row.
**If it reopens, my line is still (c)** — no site has a read that beats Gemini, so a switch buys allowance, not
writing. **First step then:** one dry press conference + your blind read (≈$0.01, no code, no defaults).

---

### D4 · Fact-gate migration line-11 comment
**Question.** `db/migrations/2027_02_30_fact_gate.sql:11` describes `skipped` in words that collide with NULL
("not checked"); the file is applied and its sha is in the ledger — may the comment be corrected?

**Options.** (a) Leave it (the applied-migration rule: never edit a shipped migration). (b) Correct the comment
only, accept a sha mismatch, and note the change in the checklist. (c) Leave the file and put the correction in
spec §7.3 only.
**Unblocks.** Nothing. It only ends the fact-gate N2 thread.
**Evidence.** `db/migrations/2027_02_30_fact_gate.sql:11`; `data/audit_reports/audit-fact-gate-2/report.md:61-64`
(N2) and `:74` (applied-migration check by sha). **Re-asked twice today** — `fix-c-findings` left it alone in both
batches (M-0856, M-0857, M-0862: "line-11 comment is JR's decision"), so it is genuinely unanswered, not lost.
**Recommend.** (a) — it blocks nothing, and editing an applied migration breaks the ledger's sha chain.
**First step.** None; the N2 wording goes in §7.3, which is already correct (`PROJECT_SPEC.md:2821` needs N1's
fix to be reworded at the same time).

---

### D5 · `--set-by` — require it?
**Question.** Should `set`/`activate-cleared` require an actor instead of falling back flag → `MU_SET_BY` →
`MU_AGENT_MODEL` → `'JR'`?

**Options.** (a) Require the flag on `shadow`/`active` writes. (b) Keep the chain as is. (c) Keep the chain but
**drop the literal `'JR'` fallback** for mode-changing writes — refuse when no actor can be identified.
**Unblocks.** P5-01a's last open question; trustworthy `personality_feature_modes` history.
**Evidence.** M-0722, M-0810 item 7, M-0812's answer; `docs/reference/P5_01_SHADOW_GATE.md:335-339` (40 P5-01 rows
read `set_by = JR`, not rewritten); `tools/spawn_agent.sh` exports `MU_AGENT_MODEL` on every route (M-0828 /
`b8a7170`), so the gap is only a hand-started bare session. **Recheck 2026-11-12: no answer anywhere** — the last
word on it is `audit-p5-01a`'s sign-off (M-0818), which leaves it open and non-blocking; nothing since.
**Recommend.** (c) — identification is nearly always available now, and a refusal beats a wrong name.
**First step.** One-line builder change: replace the `or "JR"` default with an exit 2 on `shadow`/`active` writes
only, leaving `off` and reads permissive.

---

### D6 · P3-02 constants — is "wait for P5" still the call?
**DECIDED 2026-11-12 — yes, and it was already your call, not an open question.** The ruling is recorded in
`docs/reference/PROGRESSION_FIT_2026-11-12.md` §0 decision 1 ("No constant is applied… The constants question
belongs to the first shadow season's reports (P5-06), not to this fit") and in `docs/CLOSE_OUT_CHECKLIST.md:235`
("Nothing from either set is applied (JR 2026-11-12)"). The P3-02 row also records the still-open corrected-engine
re-run; that is a *measurement* owed, not a decision.
**What would reopen it.** Only a wish to revisit before P5-06, or the fit's tripwire: the development term reaching
+1.50 a year on the full roster (it is +1.00 now; the sample's line was +1.50).
**The numbers, for the record.** 0.9603→0.9184 (sample) and 0.9337→0.8927 (full roster); `AGE_BAND_OFFSET[0]`
survives 4 of 5 seeds with one direction dissent — which is why "no change" is the defensible freeze.
**Evidence.** `docs/reference/PROGRESSION_FIT_2026-11-12.md` §0 and §2; `docs/CLOSE_OUT_CHECKLIST.md:235`; P5-06
row `:367`.
**First step.** None. Do not mark this open again unless the tripwire trips.

---

### D7 · P3-06a — is the drafted cohort the A1 gate population?
**DECIDED 2026-11-12, through D1 — the drafted cohort, all-comers 4.36 as the reported floor.** The merge
(`68f58943`) ticked P3-06a ☑ and carried `d0330764`'s recorded population into
`docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md` and the checklist. JR approved the merge that contains it; **if
he wants this to read as an explicit call rather than an accepted one, say so and it becomes a line in the
ledger.** Nothing else is blocked either way.
**Question.** Mu produces ~247 entrants a season (≥224 drafted, ≤20 undrafted, 92% drafted; roster bound 7.57
seasons); confirm the drafted cohort as the A1 gate population with all-comers 4.36 reported as the floor.

**Options.** (a) Confirm as drafted, floor reported. (b) Gate on all-comers. (c) Report both, gate on neither
until the first shadow season.
**Unblocks.** P3-06a ☑, the A1 career-length gate's final population, P3-03's comparison.
**Evidence.** `docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md:52`, `:70-71`, `:111-112`, `:308-310`; M-0850
(the measurement, `d0330764`); checklist `:248` and `:271`.
**Recommend.** (a) — Madden's own 224-pick class makes the drafted cohort 92% of inflow by construction, so the
floor is reported but cannot move the gate.
**First step.** Tick P3-06a in the same merge as D1 (the row is on that branch).

---

### D8 · P8-13 pack-vs-static, and P8-15 portraits
**Question A (P8-13) — STILL OPEN.** Ship the dynamic presentation work as one static mod with native selection, or
keep per-game pack switching? **Question B (P8-15) — already answered "not now":** `V2_MASTER_PLAN.md:340` records
**"Deferred (JR, 2026-11-03)"**; the only thing left is pinning *when*, which the checklist still asks as
"do-now or post-2034".

**Options A.** (a) One static mod for the broadcast variants G2 proved native (v0.5.1 switches native→cyan→native
in-process), keep packs for crowd/ball/BCA the plan lists as unproven. (b) Keep per-game packs. (c) A runtime
bridge. **Options B (formality):** (a) confirm post-2034 and leave the one-portrait spike for a free PC slot.
(b) run the spike now. (c) leave the deferral undated.
**Unblocks.** P8-13 (XL, the last presentation item) and P8-15 (L, optional; already deferred, blocks nothing).
**Evidence.** `docs/not_started/DYNAMIC_PRESENTATION_MOD_PLAN.md:153-230` (§Phase 2–6, G2/G3 gates, the Phase 6
outcome table); checklist `:446`, `:448`; `docs/in_progress/V2_MASTER_PLAN.md:340` (the 2026-11-03 deferral),
`:510-512`; converter exists at `headshots/portrait_converter/`, nothing writes `PLYR_PORTRAIT`.
**Recommend.** A: (a) — broadcast variants proven in-process, packs stay where the plan says unproven. B: (a) —
generated players only arrive from the 2027 class and nothing in 2026–2033 needs a face, so pin it and stop
re-deciding it.
**First step.** A: record the Phase 6 outcome in the plan (one paragraph, no code). B: one line in the P8-15 row
("post-2034; spike when the PC idles"); no work.

---

### D9 · Sequencing — P4-01 or P5-02 first, and does P3-05 start?
**DECIDED IN PRACTICE 2026-11-12 — nothing is waiting on you here.** P4-01 runs first: `check-d` claimed the PC
and started it (M-0854 check-d), and the standing rule is **"P5-02 stays unassigned until P4-01's verdict"**
(M-0834, restated in `orchestrator-2`'s table). The order my brief recommended is the order now running, so the
only open part is whether you want P5-02 to jump the queue — say so and `check-d` hands the PC over.
**P3-05: no — answered by the code, not a choice.** P3-05 calibrates the retirement base rate against P3-03, and
P3-03 has no Mu-side numbers until a shadow season runs (`CLOSE_OUT_CHECKLIST.md:236`, `:238`;
`CLOSE_OUT_NEXT_STEP.md` §1 rejected P3-05 as "blocked: it needs P3-03, which needs a shadow season"). So
P3-08's close does **not** release P3-05, whatever the board table says; P3-05 starts after P5-02's season.
**Unblocks.** P4 (all of it), P5's per-feature loop, the "tuned league" checkpoint.
**Evidence.** checklist `:293` (P4-01), `:363` (P5-02), `:236` (P3-03 needs P5), `:238` (P3-05 needs P3-03);
`M-0854 (check-d)` for the live claim; `docs/reference/CLOSE_OUT_NEXT_STEP.md:66-80` and §2.1 (which rejected
P4-01 first on gating breadth — overruled by P4-01 being a ~20 min sim, not a season per feature).
**Risk to note.** N1 (`screens.py`) is measured clean but *unverified* until `audit-q1` rechecks it, so P4-01's
season is provisional until that verdict (`audit-queue`, M-0865).
**First step.** None from you; `check-d` is running it.

---

**Status of the eleven: 4 open (D2, D4, D5, D8/P8-13), 5 decided or deferred (D1, D3, D6, D7, D9), 2 optional
one-liners (P8-15's date, D7's explicit line).** D2 is the only one holding a builder to a P8-04 verdict.
