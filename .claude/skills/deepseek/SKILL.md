---
name: deepseek
description: Start a Project Mu board session for DeepSeek V4.1 Flash, which runs in OpenCode on OpenCode Go, and apply its commit attribution. Use when the configured DeepSeek route is active.
---

# DeepSeek session

Start in Project Mu with `tools/opencode_session.sh`, or use `tools/spawn_agent.sh <work-label> deepseek` for the tmux workflow. It runs OpenCode on JR’s OpenCode Go subscription, pinned by `.opencode/opencode.json` to `opencode-go/deepseek-v4.1-flash` (API model id `deepseek-v4.1-flash`). The old Claude Code wrapper, `~/bin/claude-deepseek`, survives only as the fallback, `tools/spawn_agent.sh <label> deepseek-claude`, retired 2026-11-26. Confirm the route in this order: `MU_AGENT_MODEL` (`deepseek-v4.1-flash`); else the model id the session reports (`deepseek-v4.1-flash`, provider `opencode-go`); else the legacy `ANTHROPIC_BASE_URL` and `ANTHROPIC_MODEL` check. A Claude label in the UI can reflect the harness. Use the confirmed backend for attribution. No key lives in the repo or in the wrapper: OpenCode uses its own stored Console credential.

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `deepseek`. Sign board messages `deepseek-v4.1-flash (<work-label>)` and include `Model: deepseek-v4.1-flash` on every authorized commit made by that model in the session. If the route changes, use the model that actually produced the work.

A DeepSeek session may spawn its own hand-off or successor DeepSeek session (JR's standing rule, 2026-11-11): `MU_ALLOW_DEEPSEEK_SPAWN=1 tools/spawn_agent.sh <label> deepseek "<prompt>"`, which starts the new session in OpenCode Go, after posting why on the board and giving the new label. It does not need `opus`, `argon` or `codex` to start one for it; it spawns a hand-off or successor, not extra help a subagent could give.
