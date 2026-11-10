#!/usr/bin/env bash
# Background listener for AGENT_CONVERSATION.md (CLAUDE.md § "Talking to other agents"): prints the header of every new message
# addressed to LABEL or to `all`, except this session's own. One line per message; run it in the background
# (Claude Code: the Monitor tool; elsewhere: `&` or the harness's background-task feature) and re-arm it if the
# harness expires it. Watches by byte offset, so it never replays old messages. When the board shrinks (tools/agent_board_rotate.py
# moved old messages to the archive), it rescans the new file and prints only headers numbered above the last one it saw.
#
#   tools/agent_board_listen.sh <label> [interval_seconds]
#   e.g. tools/agent_board_listen.sh coach-save-writes
set -u
LABEL="${1:?usage: agent_board_listen.sh <label> [interval_seconds]}"
INTERVAL="${2:-5}"
BOARD="${AGENT_BOARD:-/home/jrereyi/Project-Mu/AGENT_CONVERSATION.md}"
off=$(wc -c < "$BOARD")
last=$(grep -oE '^## M-[0-9]{4}' "$BOARD" | cut -c6- | sort -n | tail -1)
last=$((10#${last:-0}))
while true; do
  n=$(wc -c < "$BOARD" 2>/dev/null || echo "$off")
  if [ "$n" -lt "$off" ]; then off=0; fresh=$last; else fresh=-1; fi
  if [ "$n" -gt "$off" ]; then
    new=$(tail -c +"$((off + 1))" "$BOARD" | grep -E '^## M-[0-9]{4}' \
      | awk -v f="$fresh" '{ k = substr($2, 3) + 0; if (k > f) print }')
    if [ -n "$new" ]; then
      printf '%s\n' "$new" | grep -vF "($LABEL) · to:" | grep -E "to: .*(\(?$LABEL\)?|all)" || true
      m=$(printf '%s\n' "$new" | awk '{ k = substr($2, 3) + 0; if (k > x) x = k } END { print x + 0 }')
      [ "$m" -gt "$last" ] && last=$m
    fi
    off=$n
  fi
  sleep "$INTERVAL"
done
