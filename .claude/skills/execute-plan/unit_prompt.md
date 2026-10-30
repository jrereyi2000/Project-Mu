Fill every `{…}` from the plan. The agent starts cold: everything it needs is either in this prompt or in a file the prompt names.

```
You are building unit {ID} ({unit name}) of a Project Mu plan, in your own git worktree.

Read first: CLAUDE.md, then {plan path} §2 Design and §5 {ID}, then these files: {Reads list}. Read these PROJECT_SPEC.md sections only: {§ list}.

Base check: run `git merge-base --is-ancestor {BASE_SHA} HEAD`. If it fails, run `git merge --ff-only {BASE_SHA}`. If that fails too, stop and report BLOCKED.

What to build: {the unit's part of the design, in 3-8 lines, with the exact names and interfaces from §2 that this unit provides or relies on}.

Files you own (the only files you may change or create):
{Owns list}

Do not change any other file. In particular, do not touch: PROJECT_SPEC.md, CLAUDE.md, {plan path}, {every file owned by other units of the plan}. If you need a change outside your files, don't make it; describe it under BLOCKERS.

Done when: {Done when}
Verify: run `{Verify}` and expect {expected result}.

Rules (these override CLAUDE.md where they conflict):
- Don't edit PROJECT_SPEC.md. Report the spec changes instead (below).
- Tests run offline: `pytest -m "not integration" ...`. Never connect to the live `madden` database or any `madden_*` database. Don't run the napper tests wholesale.
- Commit your work on your current branch (conventional message, e.g. `feat(tools): ...`, ending with the Co-Authored-By attribution line your session uses). Don't merge, rebase, check out main, or push.
- Keep to the plan. If the plan is wrong, do the smallest sensible thing inside your files and say so under NOTES.

Final report (your last message; use SubagentHandback if you have it), in this shape:
STATUS: done | failed | blocked
BRANCH: <branch>  COMMITS: <sha subject, one per line>
FILES CHANGED: <paths>
VERIFY: <command> -> <result, with the last lines of output>
NOTES: <deviations from the plan and why; decisions you made>
SPEC CHANGES: <for each: the PROJECT_SPEC.md section (or Repository Layout) and the exact text to add or replace; or "none" and why>
BLOCKERS: <anything outside your files that's needed; or "none">
```
