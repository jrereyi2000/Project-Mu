# Project Mu: OpenCode Go DeepSeek Route Plan

**Status:** approved by JR (2026-11-12); building, wave 1 of 2 done.

**As built (wave 1):**
- U1's route line also names the session id: `Project Mu DeepSeek route: <model> (harness opencode, session ses_mu_…)`. `deepseek` and `deepseek-claude` share one `caller_is_deepseek()` guard.
- U2's config test asserts the file's key set is exactly `{$schema, model}`, so a later addition needs this plan's consent.
- U3 resolves sources as `--transcript`, then an explicit `--harness`, then `--session`, `MU_OPENCODE_SESSION`, `MU_AGENT_HARNESS`, auto — an explicit `--harness` beats the env (the plan listed the env step first). A named OpenCode source never silently falls back to Claude; only auto-detect does. Claude report lines gained the same `(claude <path>)` source suffix.
- `/offline`'s intro line still says the side chat is "read-only tools, Haiku"; that is only true on Claude Code. Left for U4.
**Builds on:** [AGENTS.md](../reference/AGENTS.md) (model inboxes and launchers), `.claude/skills/deepseek/SKILL.md`, PROJECT_SPEC §1.2 (agents and parallel builds).

**Goal:** DeepSeek sessions start in OpenCode on JR's OpenCode Go subscription instead of Claude Code pointed at DeepSeek's paid API. Nothing else about a DeepSeek session changes: same board, same label, same `Model: deepseek-v4.1-flash` trailer, same skills.

**Delivers:**
- `tools/opencode_session.sh` — the one DeepSeek launcher; sets the route markers, then runs `opencode`.
- `tools/spawn_agent.sh` — `deepseek` starts an OpenCode Go session; new `deepseek-claude` keeps the old wrapper as a fallback.
- `.opencode/opencode.json` — pins this repo to `opencode-go/deepseek-v4.1-flash`.
- `.opencode/agents/repo-qa.md` — the read-only `/offline` answerer, so it exists in OpenCode too.
- `tools/context_check.py` — reads an OpenCode session's token usage, so `/context-check` and the builder hand-over still work.
- Updated `deepseek`, `audit`, `builder`, `execute-plan`, `offline` and `context-check` skills plus `docs/reference/AGENTS.md`.

**JR's answers (2026-11-12):**
- **D1 — Scope.** Agent sessions now; the repo's own programmatic DeepSeek calls (`newsroom/tool_loop/openai_loop.py`, `tools/dossier_pilot.py`, `tools/madden_pilot/vision.py`) get their own plan later.
- **D2 — Switch-over.** `deepseek` flips to OpenCode Go in this build. `deepseek-claude` keeps the Claude Code wrapper as a fallback for two weeks; `~/bin/claude-deepseek` is used by that fallback only.
- **D3 — Interface.** Full TUI everywhere: spawned windows run `opencode --prompt …`, and the project config pins the model, so no `mini` and no per-command `--model`.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `tools/spawn_agent.sh:56` runs `$MU_DEEPSEEK_CMD` (default `~/bin/claude-deepseek`) for `deepseek`. | The launch command is one line to change, and one place to add a second type. |
| S2 | `~/bin/claude-deepseek:2-4` sets `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `ANTHROPIC_AUTH_TOKEN=<key>`, `ANTHROPIC_MODEL=deepseek-flash`. The key is stored in clear text and the file is uncommitted. | The API is reached only through that wrapper. Retiring it removes a plaintext key from the machine. |
| S3 | `tools/spawn_agent.sh:62-67` decides "caller is DeepSeek-routed" from `ANTHROPIC_BASE_URL`/`ANTHROPIC_MODEL`. | Under OpenCode neither variable is set, so the hand-off guard would silently stop working. It needs a route marker. |
| S4 | `.claude/skills/deepseek/SKILL.md:8` says to launch with the wrapper and to confirm the backend from `ANTHROPIC_*`. | The skill is the one place that documents the route, so it has to change with it. |
| S5 | OpenCode discovers project skills in `.claude/skills` and `.agents/skills` (OpenCode docs, Skills › Discovery), and reads `AGENTS.md`; `AGENTS.md` is a symlink to `CLAUDE.md` (`PROJECT_SPEC.md:25`). | All 14 existing skills and the session instructions already load in OpenCode. Nothing to port. |
| S6 | OpenCode does **not** read `.claude/agents`. In this session `subagent_type: "repo-qa"` failed with `Unknown agent: repo-qa`. | `/offline` loses its side chat unless `.opencode/agents/repo-qa.md` is added. |
| S7 | `opencode-go/deepseek-v4.1-flash` is the model id on JR's subscription (`opencode models`), and `opencode auth list` shows `OpenCode Console Personal stored`. | No key file is needed in the repo or in a wrapper; auth lives in OpenCode's own store. |
| S8 | `opencode session list --format json` returns `id`, `directory`, `updated`; `opencode session export <id>` returns `messages[].tokens` for each assistant turn — this session's last turn reads `input 1214`, `cache.read 104064`. | A context check for OpenCode is buildable from the CLI alone, with no database poking. |
| S9 | `tools/context_check.py:7,39` only reads `~/.claude/projects/*/` named by `CLAUDE_CODE_SESSION_ID`. | Builders on OpenCode would get exit 1 and the 60% hand-over rule would stop working. |
| S10 | `tools/usage_check.py:1` and `docs/reference/AGENTS.md:47` are Claude-plan only. | Go's 5-hour / weekly / monthly allowance has no watchdog. Out of scope here; see §8 Q1. |
| S11 | `docs/reference/AGENTS.md:14` gives the DeepSeek launcher and trailer; `PROJECT_SPEC.md:562` describes `spawn_agent.sh`, `:567` describes `context_check.py`. | Both layout lines and the table row are wrong after this change. |
| S12 | `.claude/skills/audit/SKILL.md:16` and `.claude/skills/builder/SKILL.md:16` carry the route check and the model-string rule. | They must match the new markers or the "DeepSeek never audits" rule can be bypassed by accident. |

## 1. Scope

**In:**
- One DeepSeek launcher for OpenCode Go, used by `spawn_agent.sh` and by JR's `~/bin/mu` window.
- `deepseek` = OpenCode Go; `deepseek-claude` = the old Claude Code wrapper, kept until 2026-11-26 and mentioned in the docs as the fallback only.
- Route markers (`MU_AGENT_CLASS`, `MU_AGENT_HARNESS`, `MU_AGENT_MODEL`) so the spawn guard, the audit restriction and the builder's model string keep working without reading harness-specific variables.
- A project OpenCode config and the `repo-qa` agent.
- `/context-check` support for OpenCode sessions.
- Doc and skill updates.

**Out (non-goals):**
- The pipeline's own DeepSeek calls (`newsroom/tool_loop/openai_loop.py:71-77`, `tools/dossier_pilot.py:318-321`, `tools/madden_pilot/vision.py:217-233`). Separate plan (D1).
- Moving Opus, Codex or Argon onto OpenCode. They keep Claude Code, Codex and Antigravity.
- A Go usage watchdog (`tools/go_usage.py`). See §8 Q1.
- Changing `tools/agent_board_inbox.py` recipients, the commit trailer, or any board format.
- Deleting `~/bin/claude-deepseek` in this build. It is JR's file, outside the repo, and the fallback needs it until 2026-11-26.
- Permission-parity work. OpenCode keeps its default `build` agent permissions, so JR still sees prompts.

## 2. Design

### 2.1 The route

| Piece | Value |
|---|---|
| Harness | OpenCode 2.2.7, TUI (`opencode --prompt …`) |
| Provider | `opencode-go` (OpenCode Go subscription, `/connect` → OpenCode Console Personal) |
| Model | `opencode-go/deepseek-v4.1-flash` (API id `deepseek-v4.1-flash`) |
| Attribution | `deepseek-v4.1-flash` — unchanged in board messages and commit trailers |
| Auth | OpenCode's stored Console credential; **no key in the repo, no key in a wrapper** |

The wrapper prints one line before the TUI starts: `Project Mu DeepSeek route: opencode-go/deepseek-v4.1-flash (harness opencode)`.

### 2.2 Route markers

Three variables, exported by `tools/opencode_session.sh` and inherited by every shell the session starts, including `spawn_agent.sh`:

| Variable | Value | Used by |
|---|---|---|
| `MU_AGENT_CLASS` | `deepseek` | the spawn guard, the `/audit` refusal, `/execute-plan`'s hand-off check |
| `MU_AGENT_HARNESS` | `opencode` (legacy wrapper sets `claude-code`) | `tools/context_check.py` source choice |
| `MU_AGENT_MODEL` | `deepseek-v4.1-flash` | the `/builder` model string, board signature |

A session started by hand with a bare `opencode` has no markers. Its skill falls back to the model id the session reports (`deepseek-v4.1-flash`) and, last, to `ANTHROPIC_*`. That order is written into the skills, not into code.

### 2.3 `tools/opencode_session.sh` (new)

```
tools/opencode_session.sh [--prompt TEXT] [any opencode arguments]
```

1. Refuses to run outside a git checkout that has `.opencode/opencode.json` (exit 2 with a one-line message), so a session never starts unconfigured.
2. Exports the three markers, `MU_OPENCODE_MODEL` (default `opencode-go/deepseek-v4.1-flash`) and `MU_OPENCODE_SESSION`. The session id is `MU_OPENCODE_SESSION` if already set (a resume), else a fresh `ses_mu_<epoch>_<random>`; it is passed to OpenCode as `--session`, so every window has a stable id that `tools/context_check.py` can export without guessing (spike S1).
3. Prints the route line.
4. `exec "${MU_OPENCODE_BIN:-opencode}" --session "$MU_OPENCODE_SESSION" "$@"`.

`MU_OPENCODE_BIN` exists for tests; nothing else reads it.

### 2.4 `tools/spawn_agent.sh`

- Usage becomes `… <claude|opus|deepseek|deepseek-claude|codex|gemini> [prompt]`.
- `deepseek)` → `cmd=("$here/opencode_session.sh" --prompt "$prompt")`.
- `deepseek-claude)` → `cmd=("${MU_DEEPSEEK_CMD:-$HOME/bin/claude-deepseek}" "$prompt")`.
- The guard becomes a small function and applies to both DeepSeek types:

```bash
caller_is_deepseek() {
  [ "${MU_AGENT_CLASS:-}" = deepseek ] ||
  [[ "${ANTHROPIC_BASE_URL:-}" == *deepseek* ]] ||
  [[ "${ANTHROPIC_MODEL:-}" == deepseek-* ]]
}
```

Refusal text and exit code 3 are unchanged, as is `MU_ALLOW_DEEPSEEK_SPAWN=1`.
- `--dry-run` prints the same `runs (in bash -lic): …` line as today.
- Header comment and usage text updated; nothing else in the script changes.

### 2.5 `.opencode/opencode.json` (new)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4.1-flash"
}
```

The project pin is what makes `opencode --prompt …` deterministic in tmux (D3). It also means a bare `opencode` in this repo starts on DeepSeek; `/models` still switches a session by hand. No `default_agent`, no permissions block, no `instructions` entry (the `AGENTS.md` symlink already carries `CLAUDE.md`).

### 2.6 `.opencode/agents/repo-qa.md` (new)

Markdown agent, `mode: subagent`, body copied from `.claude/agents/repo-qa.md:8-17`. Permissions, in this order:

```yaml
permissions:
  - action: "*"
    resource: "*"
    effect: deny
  - action: read
    resource: "*"
    effect: allow
  - action: glob
    resource: "*"
    effect: allow
  - action: grep
    resource: "*"
    effect: allow
```

No `model:` line, so the subagent inherits the session's model. No shell, no edit, no web tools — the same read-only promise as the Claude Code version.

### 2.7 `tools/context_check.py`

New flag `--harness auto|claude|opencode` (default `auto`). Resolution order:

1. `--transcript PATH` → Claude transcript, as today.
2. `--session ID` → if it starts with `ses_`, export it from OpenCode; otherwise treat it as a Claude session id.
3. `MU_OPENCODE_SESSION` → export that OpenCode session (the id `tools/opencode_session.sh` minted for this window). This is the normal path for a spawned DeepSeek session.
4. `MU_AGENT_HARNESS` → `opencode` or `claude-code`.
5. `auto`: try OpenCode when `opencode` is on PATH and a session exists; else fall back to the Claude transcript.

The OpenCode source, for cases 2–5:

1. `opencode session export <id>` — the id from step 2 or 3, else the newest entry from `opencode session list --format json` (a warning goes on stderr: the shared service gives every session the same `directory`, so this is only a fallback).
2. Context = `input + cache.read + cache.write` of the newest entry in `messages` with `type == "assistant"` and a `tokens` object.
3. The report line names its source: `context: 105,278 / 1,000,000 tokens (10.5%) - under 60% (opencode ses_…)`.

Exit codes, thresholds and `MU_CONTEXT_LIMIT` / `MU_CONTEXT_THRESHOLD` are unchanged. A session that has exported nothing (a fresh session before its first reply) counts as exit 1 with "no usage yet", not as zero.

### 2.8 `~/bin/mu` (JR's file, not a unit)

DeepSeek window becomes `tools/opencode_session.sh --prompt "/deepseek"`. The plan lists it as a JR step after wave 2; a unit never edits a file outside the repo.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The TUI may ignore the project `model` pin (only `opencode run`/`mini` document `--model`). | Spike S1: start one window and read `/models`. | Fall back to `opencode mini --model … --prompt …` for spawned windows and tell JR the TUI has to be switched by hand. |
| R2 | `/builder`, `/propose`, `/audit` may load by `@skill-id` but not as slash commands in the TUI. | Spike S1: type `/propose` and `@propose` in the spike window. | Skill text says `@skill-id`; `spawn_agent.sh` prefixes stay unchanged. |
| R3 | `/execute-plan` needs background subagents in git worktrees that commit their own branch. OpenCode has both, but this flow has never run there. | Spike S2: one-unit throwaway plan, subagent commits in a worktree. | Builds run inline in the orchestrator at a cost of time, or `/execute-plan` stays on Claude Code until fixed. |
| R4 | The shared OpenCode service reports one `directory` for every session (spike S1: a session started in `/tmp/opencode/oc-spike` exported `location.directory = /home/jrereyi/Project-Mu`), so "newest by directory" cannot identify this session, and two builders would read each other's usage. | Solved by design: `tools/opencode_session.sh` mints `MU_OPENCODE_SESSION` and passes `--session`, and `/context-check` reads it first. | `--session ses_…` still works by hand; the newest-session fallback only runs for sessions started without the wrapper. |
| R5 | OpenCode's context allowance for this model is unproven; `MU_CONTEXT_LIMIT` assumes 1,000,000. | Spike S1: `/context-check` after a long tool run; the console if it errors. | Lower `MU_CONTEXT_LIMIT` and note it in the spec. |
| R6 | One shared OpenCode service serves every window; a bad config reload or stuck server affects all sessions at once. | Spike S1 starts two windows. | `opencode service restart`; `--standalone` per window as a last resort. |
| R7 | Go's allowance is $100/month for this model (5-hour 20%, weekly 50%), shared by every builder, and nothing watches it. | §8 Q1; the console. | Sessions start failing mid-wave; the hand-over rules then rely on context only, as today. |
| R8 | The guard could be bypassed by a DeepSeek session started without the wrapper. | Skills check the model id as well as `MU_AGENT_CLASS`. | Board rule stands: a session whose model id is `deepseek-*` never audits. |

**Spike first (S1, before wave 1) — done 2026-11-12, 03:40 EST.** In a detached worktree at `/tmp/opencode/oc-spike` with the §2.5 config written by hand, `opencode run --auto` answered as `build · deepseek-v4.1-flash`: the project `model` pin is honoured, the `propose` skill loaded by id from `.claude/skills`, and the session named `CLAUDE.md` as its project instruction file (the `AGENTS.md` symlink works). A hand-supplied id worked too — `opencode run --session ses_mu_spike_0001` created and exported that session — which is why §2.3 mints `MU_OPENCODE_SESSION`. Two findings changed the design: every session reports the service's `directory`, not its own (R4), and the shared service lists sessions from all worktrees at once. R1, R2 and the instruction loading are cleared; the TUI's own `--prompt` path is confirmed by JR's first window after wave 1. The worktree and the spike sessions were deleted.

**Spike S2 (before the first real `/execute-plan` on OpenCode, ~30 minutes):** a throwaway one-unit plan whose unit writes one file, run end to end by a spawned OpenCode session. Answers R3.

## 4. Tests (synthetic, no live DB, no network)

- `tests/test_opencode_session.py` (new) → the wrapper exits 2 outside a configured checkout; with a stub `opencode` on `PATH` it exports `MU_AGENT_CLASS=deepseek`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL=deepseek-v4.1-flash`, `MU_OPENCODE_MODEL` default and override, and `MU_OPENCODE_SESSION` (minted `ses_mu_…`, or the one it was given for a resume), passes `--session <that id>` to OpenCode, prints the route line, and passes the remaining arguments through unchanged.
- `tests/test_spawn_agent.py` (new) → `--dry-run` for `deepseek` prints `opencode_session.sh --prompt '/builder'`; for `deepseek-claude` prints the wrapper; `deepseek` with a non-slash prompt still gets the `Your session label is …` prefix; a caller with `MU_AGENT_CLASS=deepseek` and no flag exits 3; with `MU_ALLOW_DEEPSEEK_SPAWN=1` it dry-runs; a caller with the legacy `ANTHROPIC_BASE_URL` also exits 3; an unknown agent exits 2; no tmux window is created (dry run only).
- `tests/test_opencode_config.py` (new) → `.opencode/opencode.json` parses, has `$schema`, and `model == "opencode-go/deepseek-v4.1-flash"`; `.opencode/agents/repo-qa.md` has `mode: subagent`, no `model:`, a deny-all rule followed by read/glob/grep allows, and no shell/edit; no config file contains a value starting `sk-`.
- `tests/test_context_check.py` (extend) → OpenCode fixture: `MU_OPENCODE_SESSION` selects that session, `--session ses_…` overrides it, the newest-session fallback runs only with neither, context sums `input + cache.read + cache.write`, under/over threshold exits 0/2, a session with no assistant usage exits 1, `--transcript` still wins over both, and the Claude transcript path keeps its existing tests passing.
- `tests/test_opencode_session.py` also asserts the wrapper never prints a secret.

Run with `pytest -m "not integration" tests/test_opencode_session.py tests/test_spawn_agent.py tests/test_opencode_config.py tests/test_context_check.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: OpenCode launcher and spawn types

- **Owns:** `tools/opencode_session.sh` (new), `tools/spawn_agent.sh`, `tests/test_opencode_session.py` (new), `tests/test_spawn_agent.py` (new)
- **Reads:** `CLAUDE.md` § Starting other sessions, `docs/reference/AGENTS.md` § Model inboxes, `tools/spawn_agent.sh`
- **Depends on:** none
- **Done when:** `deepseek` launches the wrapper, `deepseek-claude` launches `$MU_DEEPSEEK_CMD`, every launch gets a stable `MU_OPENCODE_SESSION` passed as `--session`, the guard refuses from a DeepSeek-marked caller without the flag, and `--dry-run` stays side-effect free.
- **Verify:** `pytest -m "not integration" tests/test_spawn_agent.py tests/test_opencode_session.py` → all pass; `tools/spawn_agent.sh --dry-run b1 deepseek "/builder"` → the run line contains `opencode_session.sh --prompt /builder`.

### U2: Project OpenCode config and `/offline` agent

- **Owns:** `.opencode/opencode.json` (new), `.opencode/agents/repo-qa.md` (new), `.claude/skills/offline/SKILL.md`, `tests/test_opencode_config.py` (new)
- **Reads:** `.claude/agents/repo-qa.md`, `.claude/skills/offline/SKILL.md`, `.claude/skills/propose/plan_template.md`
- **Depends on:** none
- **Done when:** the config pins the model, `opencode debug agents` lists `repo-qa`, and `/offline` names both agent paths.
- **Verify:** `pytest -m "not integration" tests/test_opencode_config.py` → all pass; `opencode debug config | grep -c 'opencode-go/deepseek-v4.1-flash'` → at least 1; `opencode debug agents | grep -c '"repo-qa"'` → 1.

### U3: `/context-check` for OpenCode sessions

- **Owns:** `tools/context_check.py`, `tests/test_context_check.py`, `.claude/skills/context-check/SKILL.md`
- **Reads:** `tools/context_check.py`, `tests/test_context_check.py`, `docs/reference/AGENTS.md` § Model inboxes
- **Depends on:** none
- **Done when:** `python3 -m tools.context_check` inside an OpenCode session prints a real number, names its source, and prefers `MU_OPENCODE_SESSION`; the three exit codes still mean what the skill says.
- **Verify:** `pytest -m "not integration" tests/test_context_check.py` → all pass; in this session after merge, `python3 -m tools.context_check --json` → `tokens > 0` and `source` starts `opencode`.

### U4: Skill and reference docs

- **Owns:** `.claude/skills/deepseek/SKILL.md`, `.claude/skills/audit/SKILL.md`, `.claude/skills/builder/SKILL.md`, `.claude/skills/execute-plan/SKILL.md`, `docs/reference/AGENTS.md`, `docs/reference/MADDEN_SPIKE_AGENT.md`
- **Reads:** U1–U3's merged code, `CLAUDE.md`, this plan §2.2–2.7
- **Depends on:** U1, U2, U3
- **Done when:** every route mention leads with OpenCode Go, names `deepseek-claude` as the fallback until 2026-11-26, and the audit/builder checks use `MU_AGENT_CLASS` and the model id, not `ANTHROPIC_*` alone.
- **Verify:** `grep -rn 'claude-deepseek' .claude/skills docs/reference` → only the `deepseek-claude` fallback lines (2 files); `grep -c 'MU_AGENT_CLASS' .claude/skills/audit/SKILL.md .claude/skills/builder/SKILL.md` → 1 or more each; `grep -rn 'deepseek-claude' docs/reference/AGENTS.md` → 1.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — | No shared files and no dependencies; each is a self-contained change. |
| 2 | U4 | U1, U2, U3 | Writes the docs against merged code, so the names it quotes are real. |

U1's wrapper refuses to start without U2's `.opencode/opencode.json`, so wave 1 merges as a set before anyone spawns a DeepSeek session.

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/audit/SKILL.md` | U4 |
| `.claude/skills/builder/SKILL.md` | U4 |
| `.claude/skills/context-check/SKILL.md` | U3 |
| `.claude/skills/deepseek/SKILL.md` | U4 |
| `.claude/skills/execute-plan/SKILL.md` | U4 |
| `.claude/skills/offline/SKILL.md` | U2 |
| `.opencode/agents/repo-qa.md` | U2 |
| `.opencode/opencode.json` | U2 |
| `docs/reference/AGENTS.md` | U4 |
| `docs/reference/MADDEN_SPIKE_AGENT.md` | U4 |
| `tests/test_context_check.py` | U3 |
| `tests/test_opencode_config.py` | U2 |
| `tests/test_opencode_session.py` | U1 |
| `tests/test_spawn_agent.py` | U1 |
| `tools/context_check.py` | U3 |
| `tools/opencode_session.sh` | U1 |
| `tools/spawn_agent.sh` | U1 |

No path appears twice. `tests/test_context_check.py` already exists and is owned by U3 alone.

## 7. Spec changes when built

- §1.1 Repository Layout:
  - new `.opencode/` block: `opencode.json` (project model pin `opencode-go/deepseek-v4.1-flash`) and `agents/repo-qa.md`.
  - `tools/spawn_agent.sh` line: new `deepseek`/`deepseek-claude` behaviour and the `MU_AGENT_CLASS` guard.
  - new `tools/opencode_session.sh` line.
  - `tools/context_check.py` line: the OpenCode source, the `--harness` flag and the source in the report line.
  - `.claude/skills/deepseek/` line: OpenCode Go route, fallback window.
- §1.2: a short "Agent routes" paragraph — DeepSeek on OpenCode Go, Opus on Claude Code, Codex on Codex, Argon on Antigravity; the three markers; the trailer.
- `docs/reference/AGENTS.md` is a guide, not the spec; the spec's layout entry for `docs/reference/` already says "keep it current" and does not change.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Do you want a Go usage watchdog (`tools/go_usage.py`, 5-hour/weekly/monthly), or is the console enough? | Console only for now. The builders hand over on context, and a watchdog needs a quota source that the CLI does not expose; revisit if a builder ever dies on the limit mid-wave. |
| Q2 | Is 2026-11-26 the right date to retire `deepseek-claude` and `~/bin/claude-deepseek`? | Yes. Two weeks covers a full build wave and an audit cycle. Retiring it also removes the plaintext key (S2). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U2 | 1 | `opencode-u2` | `.claude/worktrees/opencode-u2` | merged — config pin verified by `opencode debug config`; `repo-qa` listed by `opencode debug agents`; 8 tests pass | `c636b1d` |
| U1 | 1 | `opencode-u1` | `.claude/worktrees/opencode-u1` | merged — 20 tests pass on main; `--dry-run b1 deepseek` runs `tools/opencode_session.sh --prompt /builder`, `deepseek-claude` runs `~/bin/claude-deepseek`; guard covers both types | `6d955e4` |
| U3 | 1 | `opencode-u3` | `.claude/worktrees/opencode-u3` | merged — 18 tests pass on main; live `python3 -m tools.context_check --json` reads this session (195,496 tokens, 19.5%) and names `opencode ses_…` | `5982ea3` |
