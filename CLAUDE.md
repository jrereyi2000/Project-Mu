# Project Mu — Claude Instructions

## Start of every session
Before doing any work, read `PROJECT_SPEC.md` at the repo root. It is the system-level spec and the source of truth for the project's current architecture, repository layout, data model, engines, pipeline, newsroom, and frontend. Use it to orient yourself instead of re-deriving structure from the code. If the spec and the code disagree, trust the code, tell the user about the mismatch, and fix the spec.

## After every change
Whenever you change anything in this directory (code, pipeline, schema, engines, newsroom, frontend, config, scripts, or file layout), update `PROJECT_SPEC.md` in the same piece of work so it reflects the new state of the project. This includes:
- New, moved, renamed, or deleted files and folders (keep the Repository Layout tree current)
- New or changed database tables/columns, API endpoints, CLI flags, formulas, or scoring scales
- Changed behavior, data flow, or run order of pipeline steps and engines

Edit the relevant existing section rather than appending a changelog. A change is not done until the spec matches it. If a change genuinely doesn't affect anything the spec describes, say so explicitly.

## Commits
Whenever you make a commit, bring it into `main` right away without asking: if you committed on another branch, check out `main` and merge that branch in. Stop and tell the user only if the merge hits a conflict — do not resolve conflicts on your own. Leave uncommitted changes that aren't yours untouched, and don't push unless asked.

Every commit message ends with a trailer naming the exact model that did the work, so commits can be audited by another model:

```
Model: claude-sonnet-5
```

Use the committing agent's own model ID (`claude-sonnet-5`, `claude-opus-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`), not the display name. A subagent that makes the commit records its own model, not the orchestrator's, and the orchestrator records the model it was told the unit ran on. If the model is unknown, write `Model: unknown` — never guess.

**Current routing (JR, 2026-11-07):** the session is DeepSeek v4.1 Flash called through the Anthropic API, so commits currently end with `Model: deepseek-v4.1-flash`. The Claude Code harness still reports Sonnet 5; JR's statement of what is actually running wins over the harness's self-report. The orchestrator passes the current model string to every unit agent. JR will say when the routing changes back.
