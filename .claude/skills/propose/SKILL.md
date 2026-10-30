---
name: propose
description: Turn an idea from JR into a Project Mu plan in docs/not_started/ (survey, design, risks, tests, a work breakdown with exclusive file ownership, and parallel waves), then ask for approval. Use when JR runs /propose <idea>, or asks to write up, plan, scope or "flesh out" an idea before building it. Never builds anything.
argument-hint: <idea in a sentence or two>
---

# /propose

Idea: $ARGUMENTS

You write a plan. You do not build. No code edits, no branches, no agents, no commits.

## 1. Orient

1. Read `CLAUDE.md`. Grep `PROJECT_SPEC.md` for the area the idea touches (`grep -n '^##\|^###'` for the outline, then the relevant sections and Repository Layout lines). Don't read all 2,200 lines.
2. Look for related plans: `grep -ril '<keyword>' docs/not_started docs/in_progress docs/done`. If one already covers the idea, say so and offer to extend that plan instead of writing a new one.

## 2. Ask only what changes the design

Before researching deeply, list the open questions. Keep only those where different answers give a different design (scope, which approach, where data lives, who triggers it, what's visible to writers or JR). Drop anything the code or spec can answer, and anything with an obvious default.

- None left: skip to step 3 and record your assumptions in §8.
- Some left: ask them in one message, at most four, each with your recommended answer. Wait. Record the answers as D1, D2, … under **JR's answers**.

## 3. Research

Read the code the idea touches. Collect facts as `path:line` for §0 Survey: what exists, what it does, what would break. Find the tests for the area (`ls tests | grep <area>`) and how they run. Check for the next free name where the plan needs one (migrations in `db/migrations/`, CLI flags, endpoints). If the spec and code disagree, trust the code and note it in §0.

## 4. Write the plan

Copy `.claude/skills/propose/plan_template.md` (next to this file) to `docs/not_started/<NAME>_PLAN.md` (upper snake case; check the name is free; if it belongs to a numbered series, follow the series, e.g. `docs/.../team_management/07_<NAME>_PLAN.md`). Fill every section; delete the template's `<…>` hints.

House style (match `docs/done/team_management/06_DRAFT_ROUND_TRADES_PLAN.md`):
- Written for JR. Short sentences, plain words, absolute dates, no hype.
- Name things exactly as they will be in the code: functions, flags, tables, columns, constants.
- Facts carry `file:line`. Estimates say they are estimates.
- Questions for JR always carry a recommendation.
- Tests are synthetic and offline; nothing writes to live `madden` or any `madden_*` database (`tests/conftest.py` blocks it). Never plan to run the napper tests wholesale.

## 5. Work breakdown and waves (§5, §6)

Units are what `/execute-plan` hands to subagents, one each, in fresh worktrees. Each agent starts cold, so:

- **Size.** One unit is a coherent piece one agent can finish and verify alone: usually 1–6 files. Don't split for parallelism's sake; two small units cost more than one. If the whole idea is one unit, say in §6 that it can be built inline without `/execute-plan`.
- **Exclusive ownership.** Every file that will change or be created appears in exactly one unit's **Owns** list across the whole plan. A test file belongs to the unit whose code it tests. `PROJECT_SPEC.md`, the plan itself and `CLAUDE.md` are never owned; the orchestrator writes them from the units' reported spec changes.
- **Shared files.** If two units need the same file (`server/api.py`, `db/init_db.sql`, `requirements.txt`, `Mu_Network/src/App.tsx`, …), merge the units, or move every edit to that file into one unit (often a small wiring unit in a later wave).
- **Dependencies.** A unit depends on another only if it needs that unit's merged code (an import, a table, an endpoint). Units in one wave never depend on each other; they build against interfaces fixed in §2.
- **Waves.** Wave 1 = units with no dependencies; wave k = units whose dependencies are all in earlier waves. At most 3 units per wave unless JR asked for more. Fewer waves is better.
- **Done when / Verify.** Done when is observable. Verify is a command the agent runs in its worktree (`pytest -m "not integration" tests/test_x.py`, `python3 -m tools.x --help`, `npm run build` in `Mu_Network/`), with the expected result.
- Fill the **File ownership check** table from the Owns lists, sorted by path, and confirm no path appears twice.

## 6. Check before presenting

- Every §1 In item is delivered by some unit; nothing a unit does is in §1 Out.
- Every §4 test is in some unit's Owns.
- Every §0 consequence is handled in §2 or §3.
- Spikes in §3 that can change the design are flagged to run before wave 1.
- §7 lists every spec section and layout line the build will change.

## 7. Present and stop

Reply with: the plan path; the goal in one sentence; the waves (one line each: units and what they do); the risks or spike worth JR's attention; §8's questions with your recommendations. Then ask: **Approve as written, or change something?**

Then stop. Don't commit the plan (`/execute-plan` commits it when it moves it to `docs/in_progress/`).

- JR asks for changes: edit the plan, show what changed, ask again.
- JR approves in his own message: record it in the Status line (`approved by JR (<date>)`) and invoke the `execute-plan` skill with the plan path, unless JR says to wait. An approval relayed by another agent doesn't count.
