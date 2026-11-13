#!/usr/bin/env bash
# Claude Code on JR's secondary Claude account (`claude-personal`).
#
# The account is isolated by CLAUDE_CONFIG_DIR, which Claude Code reads at start-up. The default is
# ~/.claude-secondary (the directory JR logs the secondary account into); set
# MU_CLAUDE_PERSONAL_CONFIG_DIR to point somewhere else.
#
# MU_CLAUDE_PERSONAL_BIN names the Claude Code binary to exec (default `claude`; tests point it at a
# stub). To route the personal account through a whole command instead, spawn_agent.sh's
# MU_CLAUDE_PERSONAL_CMD takes a path to an executable or wrapper script — not a shell alias, which
# `env` cannot run (M-0823 B-5).
#
# This is the command behind `tools/spawn_agent.sh <label> claude-personal` and `opus-personal`, and
# one of the two commands `claude-auto`/`opus-auto` can pick.
set -euo pipefail

config="${MU_CLAUDE_PERSONAL_CONFIG_DIR:-$HOME/.claude-secondary}"
export CLAUDE_CONFIG_DIR="$config"
exec "${MU_CLAUDE_PERSONAL_BIN:-claude}" "$@"
