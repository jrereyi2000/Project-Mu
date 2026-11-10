#!/usr/bin/env bash
# Background listener for AGENT_CONVERSATION.md (PROJECT_SPEC §1.0): prints the header of every new message
# addressed to LABEL or to `all`, except this session's own. One line per message; run it in the background
# (Claude Code: the Monitor tool; elsewhere: `&` or the harness's background-task feature) and re-arm it if the
# harness expires it. Watches by byte offset, so it never replays old messages.
#
#   tools/agent_board_listen.sh <label> [interval_seconds]
#   e.g. tools/agent_board_listen.sh coach-save-writes
set -u
LABEL="${1:?usage: agent_board_listen.sh <label> [interval_seconds]}"
INTERVAL="${2:-5}"
BOARD=/home/jrereyi/Project-Mu/AGENT_CONVERSATION.md
off=$(wc -c < "$BOARD")
while true; do
  n=$(wc -c < "$BOARD" 2>/dev/null || echo "$off")
  if [ "$n" -gt "$off" ]; then
    tail -c +"$((off + 1))" "$BOARD" | grep -E '^## M-' | grep -vF "($LABEL) · to:" \
      | grep -E "to: .*(\(?$LABEL\)?|all)" || true
    off=$n
  fi
  sleep "$INTERVAL"
done
