---
name: deepseek
description: Start a Project Mu board session for DeepSeek V4.1 Flash routed through Claude Code and apply its commit attribution. Use when the configured DeepSeek route is active.
---

# DeepSeek session

Start in Project Mu with JR’s existing `~/bin/claude-deepseek` launcher, or use `tools/spawn_agent.sh <work-label> deepseek` for the tmux workflow. The wrapper uses DeepSeek’s Anthropic-compatible endpoint with the `deepseek-flash` alias. Confirm the backend from the launcher, or check only `ANTHROPIC_BASE_URL` and `ANTHROPIC_MODEL` in the running session. A Claude label in the UI can reflect the harness. Use the confirmed backend for attribution. Keep credentials in the local wrapper.

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `deepseek`. Sign board messages `deepseek-v4.1-flash (<work-label>)` and include `Model: deepseek-v4.1-flash` on every authorized commit made by that model in the session. If the route changes, use the model that actually produced the work.

A DeepSeek session may spawn its own hand-off or successor DeepSeek session (JR's standing rule, 2026-11-11): `MU_ALLOW_DEEPSEEK_SPAWN=1 tools/spawn_agent.sh <label> deepseek "<prompt>"`, after posting why on the board and giving the new label. It does not need `opus`, `argon` or `codex` to start one for it; it spawns a hand-off or successor, not extra help a subagent could give.
