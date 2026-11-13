---
name: opus
description: Start a Project Mu board session for Claude Opus 5.5 at Medium effort and apply its commit attribution. Use when Opus 5.5 starts work in this repository.
---

# Opus session

Use Claude Opus 5.5 at Medium effort. In Claude Code, start with `claude --model claude-opus-5-5 --effort medium` or select that model and effort in the app. In Antigravity, select Claude Opus 5.5 (Medium) or launch `agy --model claude-opus-5-5-medium`. Confirm the model and effort before taking model-directed work.

JR's second Claude account is the same model and inbox, not a new agent class: reach it with
`tools/spawn_agent.sh <label> opus-personal`, or let `opus-auto` pick primary vs personal from the
two usage files. Attribution stays `Model: claude-opus-5-5`.

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `opus`. Sign board messages `claude-opus-5-5 (<work-label>)` and include `Model: claude-opus-5-5` on every authorized commit made by this model in the session. Medium is the reasoning effort.
