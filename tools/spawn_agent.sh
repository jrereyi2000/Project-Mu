#!/usr/bin/env bash
# Starts a new agent session in its own tmux window (CLAUDE.md § "Starting other sessions").
# Any agent, or JR, may run it. It only ever OPENS a new window; it never types into an existing one
# (agents must never `tmux send-keys` into another agent's window).
#
#   tools/spawn_agent.sh [--dry-run] <label> <claude|opus|deepseek|deepseek-claude|muse|codex|gemini|argon|hydrogen> [prompt]
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
# - argon and hydrogen start a Google Antigravity CLI session (`agy`), the two Gemini 4 routes that
#   serve this repo. argon runs MU_ARGON_MODEL (default gemini-4-argon-medium); hydrogen runs
#   MU_HYDROGEN_MODEL (default gemini-4-hydrogen-high) and exports MU_AGENT_CLASS=hydrogen and
#   MU_AGENT_MODEL so the audit skill and /context-check can tell the route. Both use `agy -i` so the
#   prompt opens an interactive session, as `gemini -i` does (JR, 2026-11-12: Hydrogen is a
#   first-class agent with auditor-class rights, after the blind trial in
#   docs/reference/GEMINI_4_HYDROGEN_EVALUATION.md §3.5).
# - The new session still does the full session start itself (spec, board, listener, NOTICE) and stops at
#   its first permission prompt until someone answers it in that window.
# - Every route this script starts carries MU_AGENT_MODEL so the switchboard records the actor that threw a
#   switch (personality/features.py _actor), not an assumed JR: the route's own model pin for opus, codex,
#   argon, hydrogen and deepseek-claude; the OpenCode wrappers export it for deepseek and muse; bare `claude`
#   and `gemini` have no pinned model, so they record `unknown` (CLAUDE.md: never guess a model) unless
#   MU_CLAUDE_MODEL / MU_GEMINI_MODEL names it.
set -euo pipefail

caller_is_deepseek() {
  [ "${MU_AGENT_CLASS:-}" = deepseek ] ||
  [[ "${ANTHROPIC_BASE_URL:-}" == *deepseek* ]] ||
  [[ "${ANTHROPIC_MODEL:-}" == deepseek-* ]]
}

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
usage="usage: spawn_agent.sh [--dry-run] <label> <claude|opus|deepseek|deepseek-claude|muse|codex|gemini|argon|hydrogen> [prompt]"
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
  claude)          model="${MU_CLAUDE_MODEL:-unknown}"; cmd=(claude "$prompt") ;;
  opus)            model="${MU_OPUS_MODEL:-claude-opus-5-5}"; cmd=(claude --model "$model" --effort medium "$prompt") ;;
  deepseek)        model=""; cmd=("$here/opencode_session.sh" --prompt "$prompt") ;;
  deepseek-claude) model="deepseek-v4.1-flash"; cmd=("${MU_DEEPSEEK_CMD:-$HOME/bin/claude-deepseek}" "$prompt") ;;
  muse)            model=""; cmd=("$here/muse_session.sh" --prompt "$prompt") ;;
  codex)           model="${MU_CODEX_MODEL:-gpt-6.1-sol}"; cmd=(codex "$prompt") ;;
  gemini)          model="${MU_GEMINI_MODEL:-unknown}"; cmd=(gemini -i "$prompt") ;;
  argon)           model="${MU_ARGON_MODEL:-gemini-4-argon-medium}"; cmd=(agy --model "$model" -i "$prompt") ;;
  hydrogen)        model="${MU_HYDROGEN_MODEL:-gemini-4-hydrogen-high}"
                   cmd=(agy --model "$model" -i "$prompt") ;;
  *) echo "spawn_agent: unknown agent '$agent' (claude, opus, deepseek, deepseek-claude, muse, codex, gemini, argon, hydrogen)" >&2; exit 2 ;;
esac

# The route marker the switchboard reads. deepseek and muse set it inside their own wrappers.
env_args=()
if [ "$agent" = hydrogen ]; then env_args+=("MU_AGENT_CLASS=hydrogen"); fi
if [ -n "$model" ]; then env_args+=("MU_AGENT_MODEL=$model"); fi
if [ ${#env_args[@]} -gt 0 ]; then
  cmd=(env "${env_args[@]}" "${cmd[@]}")
fi

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
