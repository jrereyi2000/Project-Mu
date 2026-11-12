#!/usr/bin/env bash
# Starts a new agent session in its own tmux window (CLAUDE.md § "Starting other sessions").
# Any agent, or JR, may run it. It only ever OPENS a new window; it never types into an existing one
# (agents must never `tmux send-keys` into another agent's window).
#
#   tools/spawn_agent.sh [--dry-run] <label> <claude|opus|deepseek|deepseek-claude|muse|codex|gemini> [prompt]
#   e.g. tools/spawn_agent.sh builder-2 opus "/builder"
#        tools/spawn_agent.sh gm-hiring codex "Pick up the HANDOFF addressed to gm-hiring on the board."
#
# Opus, Codex and Argon sessions may spawn a DeepSeek session for builder or task-implementation work
# (`... <label> deepseek "/builder"`), coordinating on the board to run the fewest DeepSeek sessions.
# A DeepSeek-routed session may spawn another DeepSeek session itself (JR, 2026-11-11; the rule said
# "never" from 2026-11-10 and "only when JR says so" earlier on 2026-11-11): for a hand-off or a
# successor — context or quota nearly out, or passing a plan to a fresh session — it sets
# MU_ALLOW_DEEPSEEK_SPAWN=1, says why on the board first and gives the new label. The flag is the
# caller's declaration that it is following that rule; without it the script refuses the attempt.
#
# - The window is named after the label, in tmux session $MU_TMUX_SESSION (default `mu`; created detached
#   if it doesn't exist). A window with that name already open is refused.
# - The agent runs in $MU_REPO (default: the main checkout this script belongs to) inside `bash -lic`, so
#   nvm and ~/bin are on PATH; when the agent exits, the window drops to a shell instead of closing.
# - A prompt that starts with `/` (a skill such as /builder or /audit) is passed unchanged, since skills
#   choose their own label. Any other prompt, or none, is prefixed with "Your session label is <label>."
#   so the new session signs the board with it.
# - deepseek starts an OpenCode Go session through tools/opencode_session.sh, the one DeepSeek launcher
#   (it sets the route markers and the session id, then runs `opencode`). deepseek-claude is the
#   fallback: it runs $MU_DEEPSEEK_CMD (default ~/bin/claude-deepseek, JR's local wrapper that points
#   Claude Code at DeepSeek; it holds the API key and is never committed), kept until 2026-11-26.
# - muse starts an OpenCode session on a Muse model through tools/muse_session.sh; the model is
#   whatever MU_MUSE_MODEL says (Spark by default, Ember for the benchmark's second step).
# - The new session still does the full session start itself (spec, board, listener, NOTICE) and stops at
#   its first permission prompt until someone answers it in that window.
set -euo pipefail

caller_is_deepseek() {
  [ "${MU_AGENT_CLASS:-}" = deepseek ] ||
  [[ "${ANTHROPIC_BASE_URL:-}" == *deepseek* ]] ||
  [[ "${ANTHROPIC_MODEL:-}" == deepseek-* ]]
}

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
usage="usage: spawn_agent.sh [--dry-run] <label> <claude|opus|deepseek|deepseek-claude|muse|codex|gemini> [prompt]"
label="${1:?$usage}"
agent="${2:?$usage}"
prompt="${3:-}"

if ! [[ "$label" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "spawn_agent: label must be short kebab-case (got '$label')" >&2; exit 2
fi

S="${MU_TMUX_SESSION:-mu}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="${MU_REPO:-$(git -C "$here" worktree list --porcelain 2>/dev/null | awk 'NR==1 { print $2 }')}"
R="${R:-$(dirname "$here")}"

if [ -n "$prompt" ] && [ "${prompt:0:1}" != "/" ]; then
  prompt="Your session label is $label. $prompt"
elif [ -z "$prompt" ]; then
  prompt="Your session label is $label. Do the session start in CLAUDE.md, then wait for JR."
fi

case "$agent" in
  claude)          cmd=(claude "$prompt") ;;
  opus)            cmd=(claude --model claude-opus-5-5 --effort medium "$prompt") ;;
  deepseek)        cmd=("$here/opencode_session.sh" --prompt "$prompt") ;;
  deepseek-claude) cmd=("${MU_DEEPSEEK_CMD:-$HOME/bin/claude-deepseek}" "$prompt") ;;
  muse)            cmd=("$here/muse_session.sh" --prompt "$prompt") ;;
  codex)           cmd=(codex "$prompt") ;;
  gemini)          cmd=(gemini -i "$prompt") ;;
  *) echo "spawn_agent: unknown agent '$agent' (claude, opus, deepseek, deepseek-claude, muse, codex, gemini)" >&2; exit 2 ;;
esac

case "$agent" in
  deepseek|deepseek-claude)
    if [ "${MU_ALLOW_DEEPSEEK_SPAWN:-0}" != "1" ] && caller_is_deepseek; then
      echo "spawn_agent: a DeepSeek-routed session may spawn another DeepSeek session for a hand-off or successor (JR's standing rule, CLAUDE.md)." >&2
      echo "Rerun with MU_ALLOW_DEEPSEEK_SPAWN=1, after saying why on the board and giving the new label." >&2
      exit 3
    fi
    ;;
esac

inner="$(printf '%q ' "${cmd[@]}")"
inner="${inner% }; exec bash"

if [ "$dry" = 1 ]; then
  printf 'session=%s window=%s dir=%s\nruns (in bash -lic): %s\n' "$S" "$label" "$R" "$inner"
  exit 0
fi

command -v tmux >/dev/null || { echo "spawn_agent: tmux is not installed" >&2; exit 1; }
if ! tmux has-session -t "=$S" 2>/dev/null; then
  tmux new-session -d -s "$S" -n shell -c "$R"
fi
if tmux list-windows -t "=$S" -F '#{window_name}' | grep -qxF "$label"; then
  echo "spawn_agent: window '$label' already exists in tmux session '$S'" >&2; exit 1
fi

tmux new-window -d -t "=$S:" -n "$label" -c "$R" bash -lic "$inner"
echo "spawned $agent as '$label' in tmux window $S:$label (switch to it with Ctrl-b w)"
