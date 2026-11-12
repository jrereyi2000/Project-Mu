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

- **Plan quota is not instrumented here.** Antigravity has no `usage` command and writes no
  rate-limit file, so nothing like `tools/usage_check.py` (Claude) or `tools/go_usage.py` (OpenCode Go)
  applies. Do not claim a percentage you cannot read.
- **What is measurable:** `agy -p --output-format json` returns a `usage` block
  (`input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`).
  `python3 -m tools.antigravity_usage` records those runs to
  `data/usage/antigravity_usage.jsonl`, prices them at Hydrogen's published list rates, and exits 2
  when a budget is passed. An auditor runs it after each scripted batch and summarises it at batch
  boundaries. The interactive session's own tokens are **not** visible to that tool.
- **So the guard is a process, not a meter:** as an auditor, check the Antigravity usage screen in the
  console at every batch boundary, and post a quota-style `HANDOFF` to the builders every third batch
  or 90 minutes (whichever is first) saying what is verified and what is not. Never let a builder
  assume a finding is CLOSED meanwhile.
- **Context:** there is no transcript reader for Antigravity, so `/context-check` cannot report this
  session's percentage. Hand over on judgement — when the session has read most of one subsystem and is
  re-reading, or when a batch takes visibly longer than the first — with the same hand-over NOTICE a
  builder posts: what is done, what is open, what is claimed.

## Auditor notes

When running `/audit`, the skill's report path and worktree conventions are harness-neutral, so use
them unchanged: pin `.claude/worktrees/audit-<sha7>`, use `mu_scratch_audit_<sha7>_test`, and write
the report to a scratchpad folder (not into the audited tree). Antigravity's subagents are its own
mechanism; keep the rule the skill states — the main session holds the board, the subagent never
posts, and one subagent is one batch.

Do not run `/audit` on a DeepSeek-routed session's work in the same session that wrote it, and do not
sign a CLOSED verdict without rerunning the check. A trial result is not a verdict; report what you
ran.
