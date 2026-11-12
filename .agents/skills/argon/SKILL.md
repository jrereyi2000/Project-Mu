---
name: argon
description: Start a Project Mu board session for Gemini 4 Argon in Google Antigravity and apply its commit attribution. Use when Argon starts work in this repository.
---

# Argon session

Select Gemini 4 Argon in Antigravity. For the CLI, `agy models` lists the available IDs and `agy --model gemini-4-argon-medium` starts the Medium variant. If JR selects Low or High, use that variant's actual ID. `tools/spawn_agent.sh <work-label> argon` opens a tmux window on `MU_ARGON_MODEL` (default `gemini-4-argon-medium`).

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `argon`. Sign board messages with the active Argon ID and work label, for example `gemini-4-argon-medium (<work-label>)`. Include the same ID in the `Model:` trailer on every authorized commit made by that model in the session.
