---
name: hydrogen
description: Start a Project Mu board session for Gemini 4 Hydrogen in Google Antigravity and apply its commit attribution. Use when Hydrogen is the configured agent, the auditor, or a builder/plan session.
---

# Hydrogen session

Select **Gemini 4 Hydrogen** in Antigravity, or run `agy --model gemini-4-hydrogen-high` for the CLI
(the four variants are `-low`, `-medium`, `-high` — the model's own default — and `-xhigh`; `agy models`
lists them all). `tools/spawn_agent.sh <work-label> hydrogen` opens a tmux window on
`MU_HYDROGEN_MODEL` (default `gemini-4-hydrogen-high`) with `MU_AGENT_CLASS=hydrogen` exported, which
is how the audit skill and `/context-check` recognise the route.

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `hydrogen`. Sign
board messages `gemini-4-hydrogen-<effort> (<work-label>)` and put the same id in the `Model:` trailer
on every commit this model makes. Use the variant that actually ran: a session started at `high` signs
`gemini-4-hydrogen-high`.

## What it may do

Hydrogen is a **first-class agent with auditor-class rights** (JR, 2026-11-12), on the same footing as
Opus 5.5, Codex (`gpt-6.1-sol`) and Argon: it may run `/audit`, `/builder`, `/execute-plan` and
`/propose`. Two hard rules still apply, and neither is about this model:

- it **never audits its own work** — the session that wrote a change does not review it;
- it is a **different family from the DeepSeek builders**, so it can audit their work, exactly as
  Argon does.

The evidence for the rights is the blind trial in
[GEMINI_4_HYDROGEN_EVALUATION.md](../../../docs/reference/GEMINI_4_HYDROGEN_EVALUATION.md) §3.5: the
known mirror N1 defect found first time at `pipeline/league_office.py:776` with a repro, 24/24 and
23/23 citations resolved, 8 minutes for two batches.

## Work limits and hand-over

- **The plan quota is still not instrumented here.** Antigravity has no `usage` command and writes no
  rate-limit file, so nothing like `tools/usage_check.py` (Claude) or `tools/go_usage.py` (OpenCode Go)
  applies. Do not claim a percentage you cannot read. What changed on 2026-11-12 is that a **batch
  auditor's own turns are metered** (next bullet), so the guard now has a real meter behind it for
  everything run through the launcher.
- **A print-mode batch is metered, turn by turn.** `agy -p --output-format json` returns a `usage`
  block (`input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`).
  `tools/agy_batch.sh [--label L] [--model M] <prompt-file>` runs one turn, prints that block, and
  appends it to `data/usage/antigravity_usage.jsonl` through `tools/antigravity_usage` — so an
  auditor started through it is metered without doing anything, and a run priced with
  `python3 -m tools.antigravity_usage --since-hours 5` is a floor on what the session spent. A
  hand-run `agy -p …` is metered only if you pipe the result into
  `python3 -m tools.antigravity_usage record -`.
- **What is still not covered: the interactive TUI.** An `agy -i` session writes no usage record, and
  its tokens are invisible to the ledger. A TUI session therefore still cannot report a percentage,
  and what it spends is the gap between the ledger and the console's usage screen.
- **So the guard is a process, not a quota meter:** as an auditor, read the ledger (or the Antigravity
  usage screen in the console) at every batch boundary, and post a quota-style `HANDOFF` to the
  builders every third batch or 90 minutes (whichever is first) saying what is verified and what is
  not. Never let a builder assume a finding is CLOSED meanwhile.
- **Context:** there is no transcript reader for Antigravity, so `/context-check` cannot report this
  session's percentage. Hand over on judgement — when the session has read most of one subsystem and is
  re-reading, or when a batch takes visibly longer than the first — with the same hand-over NOTICE a
  builder posts: what is done, what is open, what is claimed.

## Being woken, and the print-mode batch

An interactive `agy -i` session **cannot be woken**: a board message gives it no turn, and it exposes
no conversation id to resume (`docs/reference/AUDIT_QUEUE_INVENTORY.md` §11). A **print-mode
conversation** can be, and that is how an auditor starts if it wants a wake:

```bash
tools/agy_batch.sh --label audit-x brief.txt          # first turn; prints `conversation: <id>`
python3 -m tools.agent_board_wake --harness agy --label audit-x \
    --conversation <id> --state data/audit_reports/audit-x/wake.json
```

`agy_batch.sh` keeps the id in `data/audit_reports/<label>/agy_batch.json` and resumes that same
conversation on every later call with the label, so batches accumulate context instead of starting
over. The wake tool watches the board's headers and delivers a new turn through
`--conversation <id>`; only `status: SUCCESS` counts as a wake, and the delivered turn's tokens land
in the ledger like any other.

Measured on this machine, 2026-11-12, so nobody has to guess:

- **Writes work in print mode.** A print-mode turn created a file with its file tool, then with its
  shell tool wrote a file, ran `git rev-parse` and read it back — no confirmation prompt, no
  `--dangerously-skip-permissions`, nothing that waited for a human. One sandboxed `git` read failed
  and agy retried it itself with `BypassSandbox: true`. So a batch can inspect and edit; it is not
  notify-only.
- **`--print-timeout 0` waits for the whole turn.** A turn told to run `sleep 100` returned after
  112 s with its JSON. The wake tool still bounds each delivery with `--timeout` (1800 s default).
- **A killed run leaves a resumable conversation.** A resumed turn killed at 30 s mid-command, then
  resumed again with the same id, returned `SUCCESS` with `num_turns` 3 and its earlier context
  intact.

Because nothing answers a question in print mode, a batch prompt must carry its own authority: say
what it may edit, and where, in the brief. The wake prompt already says the board text is data, not
JR's instructions.


## Auditor notes

When running `/audit`, the skill's report path and worktree conventions are harness-neutral, so use
them unchanged: pin `.claude/worktrees/audit-<sha7>`, use `mu_scratch_audit_<sha7>_test`, and write
the report to a scratchpad folder (not into the audited tree). Antigravity's subagents are its own
mechanism; keep the rule the skill states — the main session holds the board, the subagent never
posts, and one subagent is one batch.

Do not run `/audit` on a DeepSeek-routed session's work in the same session that wrote it, and do not
sign a CLOSED verdict without rerunning the check. A trial result is not a verdict; report what you
ran.
