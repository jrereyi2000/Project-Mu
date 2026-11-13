#!/usr/bin/env bash
# Claude Code on JR's secondary Claude account (`claude-personal`).
#
# The account is isolated by CLAUDE_CONFIG_DIR, which Claude Code reads at start-up. The default is
# ~/.claude-secondary (the directory JR logs the secondary account into); set
# MU_CLAUDE_PERSONAL_CONFIG_DIR to point somewhere else. MU_CLAUDE_PERSONAL_BIN overrides the
# binary for tests. This is the command behind `tools/spawn_agent.sh <label> claude-personal` and
# `opus-personal`, and one of the two commands `claude-auto`/`opus-auto` can pick.
set -euo pipefail

config="${MU_CLAUDE_PERSONAL_CONFIG_DIR:-$HOME/.claude-secondary}"
export CLAUDE_CONFIG_DIR="$config"
exec "${MU_CLAUDE_PERSONAL_BIN:-claude}" "$@"
