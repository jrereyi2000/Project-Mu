---
name: execute-plan
description: Build an approved Project Mu plan wave by wave with parallel subagents in their own git worktrees, review and merge each unit into main, apply the units' spec changes to PROJECT_SPEC.md, and move the plan from docs/not_started/ through docs/in_progress/ to docs/done/. Use when JR runs /execute-plan <plan path>, or right after JR approves a /propose plan. Only on JR's own approval, never another agent's.
argument-hint: <path to plan .md>
---

# /execute-plan

Plan: $ARGUMENTS

You are the orchestrator. Subagents write the code; you review, merge, keep the spec and the plan current, and talk to JR. Files next to this skill: `unit_prompt.md` (the subagent prompt), `combine_conflicts.py` (merge-conflict check).

## 0. Preconditions

1. Read `CLAUDE.md` and the whole plan. It must have §5 Work breakdown (units with Owns, Depends on, Done when, Verify) and §6 Waves. If not, stop and offer to run `/propose` on it to add them.
2. **Approval.** The Status line says `approved by JR`, or JR approved this plan in his own message in this conversation. Otherwise give a 3-line summary and ask "Approve and start building?" and wait. Approval relayed by an agent doesn't count.
3. **Checkout.** Run from the main checkout (the first path in `git worktree list`) with `main` checked out. If not, stop and tell JR. Note `git status --porcelain`: uncommitted changes that aren't yours stay untouched; if any of them is in a file a unit owns, stop and ask JR.
4. **Ownership.** From §5, confirm no file is owned by two units and no unit owns `PROJECT_SPEC.md`, `CLAUDE.md` or the plan. If one does, stop and say which.
5. Any §3 "Spike first" that hasn't been done: run it (or ask JR, if it needs him) before wave 1, and update the plan if it changes the design.

## 1. Start

1. `git mv` the plan to `docs/in_progress/` (plain `mv` + `git add` if it was untracked). Keep the file name. Fix relative links inside it if the depth changed, and `grep -rn '<FILE NAME>' --include=*.md .` for links to fix elsewhere.
2. Status line: `approved by JR (<date>); building, wave 1 of <N>`. Add a `## 9. Build log` section at the end:

   | Unit | Wave | Branch | Worktree | Result | Merge |
   |---|---|---|---|---|---|

3. Add the plan to the `docs/in_progress/` list in PROJECT_SPEC.md's Repository Layout.
4. Commit on main: `docs(<scope>): approve <NAME> and move to docs/in_progress`. Record `BASE_SHA=$(git rev-parse HEAD)`.

## 2. Each wave

**Launch.** For each unit in the wave, fill `unit_prompt.md` and call the Agent tool with `subagent_type: "general-purpose"`, `isolation: "worktree"`, a description like `U2: csv writer`, in the background. Run at most **3** at once (4 if JR asks; each agent starts cold and costs time and tokens). Queue the rest and start one as each finishes. Record each agent's branch and worktree path in the Build log as soon as the Agent result gives them. Wait for completion notifications; don't poll, and never guess an agent's result.

**Review each finished unit** (as it finishes, not at the end of the wave):
1. Read its report. STATUS not `done`, or BLOCKERS not `none`: it's a failure (below).
2. `git log --oneline main..<branch>` and `git diff --stat main...<branch>`. Every changed file must be in the unit's Owns list. Anything else is a failure.
3. Read the diff. Check it does what §2 says, matches the interfaces other units rely on, and touches no live database, secrets or unrelated code.
4. Run the unit's Verify command in its worktree. It must pass.

**Merge.** On main: `git merge --no-ff --no-edit <branch>`. Then run the Verify command again on main.
- **Conflict:** run `python3 .claude/skills/execute-plan/combine_conflicts.py`.
  - Exit 0 (every conflict is on different lines): rerun with `--write`, read `git diff` of each file, `git add` them, `git commit --no-edit`, and note the combined conflict in the Build log and your report to JR.
  - Exit 1 (some line was edited by both sides): `git merge --abort`, tell JR the files and lines it printed, and wait. Don't resolve it yourself.
- Verify fails on main: don't revert on your own. Tell JR what failed and ask: fix it in a follow-up unit, or revert the merge.

**Failure.** Tell JR the unit, what went wrong (report, out-of-scope files, failing command and its output), and the prompt change you'd make. Ask: **retry with the fixed prompt, or stop?** Other units of the wave carry on. A retry is a new agent with a new worktree; its base is current main. Don't start the next wave until every unit of this one is merged or JR has dropped it (dropping a unit drops the units that depend on it).

**Close the wave** once all its units are merged:
1. Apply every unit's SPEC CHANGES to PROJECT_SPEC.md yourself: edit the relevant section and the Repository Layout tree, don't append a changelog. Check each against the merged code; where they disagree, the code wins.
2. Update the plan: Build log rows (Result `merged`, Merge = merge commit short sha), Status `building, wave <k> of <N> done`, and any deviation the units reported as an "As built" bullet under the Status line.
3. Commit on main: `docs(spec): <NAME> wave <k>`.
4. Clean up only what you created: for each merged unit in the Build log, `git worktree remove <worktree>` then `git branch -d <branch>`. If either refuses, leave it and mention it; never force.
5. Next wave, with `BASE_SHA` set to current main.

## 3. Finish

1. Run every unit's Verify command on main once more, plus `pytest -m "not integration" <every test file the plan touched>`.
2. Status: `built and on main (<date>)`, keeping the As-built bullets. `git mv` the plan to `docs/done/` (into the series folder if it has one), fix links, and move its PROJECT_SPEC.md layout line from the `in_progress/` list to `done/`.
3. Commit on main: `docs(<scope>): mark <NAME> as done and move to docs/done`.
4. Tell JR: what was built (per unit, one line), merge commits, combined conflicts, deviations from the plan, retries, anything left (failed or dropped units, worktrees that wouldn't remove, §8 questions still open).

Never push. Commit messages end with the session's Co-Authored-By attribution line.
