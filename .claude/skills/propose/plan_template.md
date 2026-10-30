# Project Mu: <Title> Plan

**Status:** proposal, awaiting JR's approval (<YYYY-MM-DD>).
**Builds on:** <spec sections and plans this extends, as links, e.g. PROJECT_SPEC §2.17, [`PC_MIGRATION_PLAN.md`](../in_progress/PC_MIGRATION_PLAN.md) §5.1>. <Drop the line if nothing.>

**Goal:** <One short paragraph: what JR gets and why. Plain words.>

**Delivers:**
- <CLI flag / module / table / endpoint / page, each named as it will be in the code>

**JR's answers (<YYYY-MM-DD>):** <Only if clarifying questions were asked. One bullet per answer, D1, D2, ….>

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | <What the code does today, with `path:line`.> | <What that means for this plan.> |

## 1. Scope

**In:** <bullets>

**Out (non-goals):** <bullets; things a reader might expect that this plan won't do, and where they belong instead>

## 2. Design

<Subsections as needed (2.1, 2.2, …). Name every function, flag, table and column as it will be built. State the interfaces units rely on: signatures, column types, JSON shapes, CLI output. Units in one wave build against these, so they must be exact.>

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | | | |

**Spike first:** <A short check to run before wave 1 (who runs it, what it answers, how long), or "none".>

## 4. Tests (synthetic, no live DB)

- <test file → what it checks, one bullet per behaviour>

Run with `pytest -m "not integration" <files>`. <Integration tests, if any, run against `madden_test` only.>

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: <name>

- **Owns:** `<path>` (new), `<path>`
- **Reads:** `<paths the agent should read first>`
- **Depends on:** none
- **Done when:** <observable result>
- **Verify:** `<command>` <expected result>

### U2: <name>

- **Owns:**
- **Reads:**
- **Depends on:** U1
- **Done when:**
- **Verify:**

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U3 | — | <no shared files, no dependency between them> |
| 2 | U2 | U1 | <uses U1's interface> |

**File ownership check:**

| File | Unit |
|---|---|
| `<path>` | U1 |

## 7. Spec changes when built

- <§ section: what changes>
- Repository Layout: <new or moved files>

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | | |
