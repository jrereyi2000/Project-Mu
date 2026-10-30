# Propose and Execute

Two Claude Code skills (`.claude/skills/`) take an idea from a sentence to merged code:

1. **`/propose <idea>`** writes a plan in `docs/not_started/` and asks JR to approve it. It never builds.
2. **`/execute-plan <plan path>`** builds an approved plan with parallel subagents, merges their work into `main`, keeps `PROJECT_SPEC.md` current, and moves the plan to `docs/in_progress/` and then `docs/done/`.

## The flow

| Step | Who | What happens |
|---|---|---|
| 1 | JR | `/propose add a --csv flag to tools/fbmod_conflicts.py` |
| 2 | Claude | Reads the spec and code. Asks up to four questions, only ones whose answers change the design, each with a recommendation. |
| 3 | Claude | Writes `docs/not_started/<NAME>_PLAN.md` from `.claude/skills/propose/plan_template.md`: survey (`file:line` facts), scope and non-goals, design, risks and spikes, tests, **work breakdown**, **waves**, spec changes, decisions for JR. Asks "Approve as written, or change something?" |
| 4 | JR | Asks for changes, or approves. |
| 5 | Claude | On approval (JR's own message), runs `/execute-plan`: moves the plan to `docs/in_progress/` and commits that on `main`. |
| 6 | Claude | Per wave: one background subagent per unit, each in its own worktree (at most 3 at once). Reviews each finished branch (scope, diff, Verify), merges it into `main`, then applies the units' spec changes to `PROJECT_SPEC.md` and cleans up the worktrees and branches it made. Then the next wave. |
| 7 | Claude | Marks the plan built, moves it to `docs/done/`, and summarizes. |

An example plan from a dry run: [`examples/EXAMPLE_PLAN.md`](examples/EXAMPLE_PLAN.md).

## Units and waves

- A **unit** is one subagent's job: the files it **owns**, what it depends on, when it's done, and the command that verifies it.
- **Every file belongs to exactly one unit**, across the whole plan, so parallel branches can't edit the same file. `PROJECT_SPEC.md`, `CLAUDE.md` and the plan itself belong to no unit; units report spec changes and the orchestrator writes them.
- A **wave** is a set of units with no dependencies on each other. Wave 2 starts only after every wave 1 unit is merged and the spec is updated, so wave 2's worktrees start from that code.
- Each subagent starts cold, so plans prefer a few solid units over many small ones. A one-unit plan says it can be built inline.

## Where JR is asked

- Clarifying questions (step 2) and plan approval (step 4).
- A unit fails (bad report, out-of-scope files, failing Verify): retry with a fixed prompt, or stop.
- A merge conflict where both sides edited the same line. Conflicts on different lines are combined without asking (`.claude/skills/execute-plan/combine_conflicts.py` checks this mechanically) and noted in the summary.
- Verify fails on `main` after a merge: follow-up fix or revert.

## Rules the subagents get

Own files only; no `PROJECT_SPEC.md` edits (report SPEC CHANGES instead); offline tests only, never the live `madden` or any `madden_*` database; commit on their branch; never merge, check out `main`, or push. The prompt is `.claude/skills/execute-plan/unit_prompt.md`.

Nothing is ever pushed.
