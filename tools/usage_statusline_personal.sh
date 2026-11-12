#!/usr/bin/env bash
# Claude Code status line for the secondary account (JR, 2026-11-12).
#
# Same line as tools/usage_statusline.py, but records the two plan windows to the personal file so
# tools/claude_account.py can compare both accounts. Put this in ~/.claude-secondary/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "/home/jrereyi/Project-Mu/tools/usage_statusline_personal.sh"
#   }
#
# MU_USAGE_PERSONAL_FILE overrides the output path.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MU_USAGE_FILE="${MU_USAGE_PERSONAL_FILE:-/home/jrereyi/Project-Mu/data/usage/rate_limits_personal.json}"
exec python3 "$here/usage_statusline.py"
