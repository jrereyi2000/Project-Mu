# Open decisions — only JR can make these

**Written 2026-11-12 by `deepseek-v4.1-flash (decide-e)`. Briefs only: nothing built, merged, or applied.**
Nine sections, eleven calls. Each: the question, the options, what it unblocks, the evidence, my one-line
recommendation, and the smallest reversible first step. When you answer one on the board, this file is edited
to say **DECIDED <date> — "your words"**.

**Status after JR's answers (2026-11-12): nothing is open.** D2 the season line owns the record; D4 correct the
migration comment; D5 drop the literal `'JR'` fallback; D8/P8-13 settled by the Codex presentation work (one static
mod, in-process native selection — my brief was stale); P8-15 pinned post-2034; D7 now an explicit call. D1 and D9
were already decided. **Two of these need a builder** (D2, D5) and one is a docs edit (D4).

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
**DECIDED 2026-11-12 — "The season line always owns the record." (JR).** Option (a): where a recorded season
line disagrees with a `BOOK` row, the **recorded line wins**, and the rule is applied in **one shared place** so
every surface agrees — `get_record_book`, `find_records_broken_in_week` (swarm slate + `press_facts`),
`server/history_api.py` (`#records`) and `records_engine --apply`. Spec §2.9's "the record book is the authority
for its seasons" is replaced by this rule.

**What a builder must undo and do.** `8fbac3e3` made the reconcile **one-directional** (a recorded line may only
make a mark worse) to fix Davis without this decision. That is now wrong: the reconcile becomes **bidirectional**
again, and the `kept_book_result` branch goes. The narrow fix is *not* enough — three surfaces still read BOOK, so
the same awkward case (Crawford: book 67, recorded 71, new 69) would be "ranks" in the tool and "broke" on
`#records` and in a press fact. One helper, called by all four.
**Evidence.** `data/audit_reports/audit-p8-dossier/p8-04-recheck.md` §D3/F1; `newsroom/db_tools.py`
(`reconcile_record_events`, `get_record_book`), `newsroom/swarm_dispatcher.py:446/2403`,
`newsroom/press_facts.py:507`, `server/history_api.py`, `engines/records_engine.py:14` and its `evaluate`;
`PROJECT_SPEC.md:1044` (§2.9).
**First step.** A builder extracts the reconcile into one helper with a BOOK-vs-recorded policy parameter set to
"recorded wins", calls it from the four surfaces, and adds the fake-cursor test the audit asked for (BOOK 67 /
recorded 71 / new 69 → "ranks", and the same answer from the top-10 and `records_set_this_season` sections).
**Reverses by** flipping the parameter; keep the helper either way.

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
**DECIDED 2026-11-12 — "Correct the comment as well." (JR).** Option (b): the line-11 comment is corrected even
though the file is applied.

**What it costs, so the edit is done safely.** `db/migrate.py:69-74` checksums every file: `status` reports
`drift` when an applied file's bytes changed (`:136-137`) and `apply` **raises** on a mismatch (`:180-181`). So a
comment edit needs three things in one commit: (1) the corrected comment; (2) `db/init_db.sql`'s ledger row
regenerated (`python3 -m db.migrate sync-init-db`, `db/init_db.sql:4309` holds the current
`9cbd17d5…59bb4`); (3) a checklist/spec note that every existing database will now show
`2027_02_30_fact_gate` as **one DRIFT row** in `db.migrate status`, by design — do not "fix" it with
`db.migrate baseline`, which would stamp genuinely missing migrations as applied.
**What the comment should say.** `skipped` = the gate deliberately did not judge it (mock run, `MU_NO_LLM=1`,
`FACT_GATE_ARTICLES=0`); the column's NULL = **not checked** (a row written before the migration). The spec
§7.3 sentence `fix-c-findings` corrected (`ec8cebec`) already has this wording — reuse it verbatim.
**Evidence.** `db/migrations/2027_02_30_fact_gate.sql:11`; `data/audit_reports/audit-fact-gate-2/report.md:61-64`
(N2) and `:74` (the sha check that kept it untouched); `db/migrate.py:69-74`, `:136-137`, `:180-181`;
`PROJECT_SPEC.md` §7.3.
**First step.** A docs-only commit: the comment, the regenerated ledger row, the drift note. No schema change, so
nothing re-runs.

---

### D5 · `--set-by` — require it?
**DECIDED 2026-11-12 — "Drop the literal chair fullback." (JR).** Read as *drop the literal `'JR'` fallback* —
option (c); the transcription garbled it, so correct me on the board if that is not what you meant.

**The change.** `personality/features.py`'s `_actor` keeps its chain — flag → `MU_SET_BY` → `MU_AGENT_MODEL` —
and loses only the final `or "JR"`. With no actor identifiable, a **`shadow`/`active`** write exits 2 with a
message naming the flag; `off` and read paths stay permissive, so a kill switch can never be blocked by a
missing name. Nothing is required that is not already exported on every spawn route (`b8a7170`), so the only
callers that change behaviour are hand-started bare `claude`/`gemini` sessions.
**Evidence.** M-0722; `docs/reference/P5_01_SHADOW_GATE.md:335-339` (the 40 P5-01 rows that read `set_by = JR`);
`tools/spawn_agent.sh` exporting `MU_AGENT_MODEL` on every route; `personality/features.py:_actor`.
`audit-p5-01a`'s F1 LOW (no `schema_migrations` table → traceback) is a separate, still-open item.
**First step.** One builder edits `_actor` plus one test per branch (flag, env, model, none→exit 2 on shadow,
none→ok on off), and adds the P5-01a row note.

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
**DECIDED 2026-11-12 — an explicit call: the drafted cohort, with all-comers 4.36 as the reported floor**
("write it as an explicit call", JR). Mu produces ~247 entrants a season (≥224 drafted, ≤20 undrafted, 92%
drafted; roster bound 7.57 seasons against 1,869 slots), so the drafted cohort is the gate population and the
all-comers mean is reported, never gated. Recorded in `TUNING_ACCEPTANCE_THRESHOLDS.md` §0.2/§2 A1/§7 decision 2
and the P3-06a row, which went ☑ with the D1 merge (`68f58943`, carrying `d0330764`).
**What it unblocks.** The A1 career-length gate is final: median 5–6 by group, ±1 (±1.5 QB/K/P), measured on
players with `draft_round BETWEEN 1 AND 7`; P3-03's comparison uses the same population.
**Evidence.** `docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md:52`, `:70-71`, `:111-112`, `:308-310`; M-0850
(the measurement); checklist `:248`, `:271`.
**Revisit only if** Mu's own inflow shifts the drafted share materially below ~90% in a shadow season.

---

### D8 · P8-13 pack-vs-static, and P8-15 portraits
**P8-13 — SETTLED, and my brief was stale. Corrected 2026-11-12 on JR's word: "a lot of the work has been done
and is being done by Codex. The dynamic presentation mod is actually nearly wrapped up."** I had built this
section from the checklist row and `docs/not_started/DYNAMIC_PRESENTATION_MOD_PLAN.md`; the live state is in
**`docs/in_progress/DYNAMIC_PRESENTATION_HANDOFF.md`** (Codex, 2026-11-10/11) and
`docs/in_progress/DYNAMIC_PRESENTATION_RESEARCH.md`, and the checklist's "decide pack-vs-static" line is itself
stale. What that work shows:

- **The decision is already made and proved:** one static `.fbmod` switching in-process from native Madden
  context. **v0.5.1 passed same-stadium AFC → NFC → AFC with no reload** (`RESEARCH.md:299`, `:309`, `:553`), and
  v0.36/v0.37 played the CBS/FOX matchup and replay-out movies in one session (`HANDOFF.md` verified table).
  Native selection is the accepted route; "no manifest consumer is required for the accepted deterministic rule"
  (v0.5.1 receipt). No runtime bridge.
- **What is actually left** (`HANDOFF.md` "Remaining work"): night halftime recovery and trace; source
  reconciliation (CBS ticker, postseason corners/fonts, banner/QB panels, team ID, touchdown media,
  timeout/two-minute flows); team logos and broadcast music scoping (shared global slots, per-game ownership
  undecided); then **consolidating the three research mods into one `.fbmod`**, removing the v0.42/v0.43 diagnostic
  markers, and running the same-session Franchise matrix on the combined export.
- **What it explicitly does not establish:** a production replacement for `pipeline/mod_packs.py`, and it says
  nothing about crowd density or ball/BCA. So packs stay for those three; the broadcast mod is the static half.
- **Builds on disk:** `data/mod_builds/halftime_{route,outro}_marker/` (v0.42, v0.43 — diagnostics, to be removed
  before the release), the three research probes, and `verify_output/dynamic_presentation/` (r4–r5 assets).
  Receipts and hashes are in each `TEST.md`/`install_receipt.json`.

**So there is no pack-vs-static call for you to make.** The one genuine open item is P8-13's *production cut-over*:
after consolidation, whether to replace `mod_packs.py`'s broadcast routing with the single mod (keeping packs for
crowd/ball/BCA) — that is a plan question for Codex, not a decision for you.

**P8-15 — DECIDED 2026-11-12: "pin the portraits to post to 2034" (JR).** Explicit, and it supersedes the undated
2026-11-03 deferral (`V2_MASTER_PLAN.md:340`). No portrait work before 2034; the one-portrait spike is not
scheduled. `headshots/portrait_converter/` stays as built, and `PLYR_PORTRAIT` stays unwritten. The P8-15 row
should read "post-2034" instead of "decide do-now or post-2034".

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

**Status of the eleven: nothing open.** Decided 2026-11-12 — D1, D2, D3 (deferred with a trigger), D4, D5, D6
(deferred), D7 (explicit), D8/P8-13 (settled by the Codex work), D8/P8-15 (post-2034), D9. **Work this
creates:** D2 (builder, four surfaces + spec §2.9), D5 (builder, `_actor` + tests), D4 (docs + ledger sha). The
P8-15 and P8-13 checklist rows need one-line corrections (P8-15 "post-2034"; P8-13 no longer "decide
pack-vs-static").
