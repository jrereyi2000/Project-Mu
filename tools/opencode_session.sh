#!/usr/bin/env bash
# The one DeepSeek launcher (OPENCODE_DEEPSEEK_ROUTE_PLAN §2.3).
#
# DeepSeek agent sessions run in OpenCode on JR's OpenCode Go subscription instead of Claude Code
# pointed at DeepSeek's paid API. This wrapper exists so every such session starts the same way: it
# refuses to start outside a checkout that carries the project OpenCode config, exports the route
# markers the rest of Project Mu reads (the spawn guard, /audit's refusal, /context-check's harness
# choice, the /builder model string), mints one session id and passes it as `--session` so the window
# has a stable id `/context-check` can export, prints the route line, then hands the terminal to
# OpenCode. `tools/spawn_agent.sh <label> deepseek` and JR's `~/bin/mu` window both call it; nothing
# else starts an OpenCode DeepSeek session. No key lives here or in the repo — OpenCode uses its own
# stored Console credential.
#
#   tools/opencode_session.sh [--session-only] [--title TEXT] [--prompt TEXT] [any opencode arguments]
#
# --session-only records the session (same minted id, same model, the title you give) and prints
# `session: <id>` instead of handing the terminal to OpenCode. `tools/spawn_agent.sh <label> deepseek`
# uses it so a DeepSeek session belongs to the shared OpenCode window instead of getting a tmux window
# of its own (JR, 2026-11-12). The service cannot open a TUI tab from outside, so the launcher prints
# how to open it; the session is live either way.
#
# MU_OPENCODE_MODEL overrides the pinned model and MU_OPENCODE_SESSION reuses an existing session (a
# resume); both are exported so a session can be continued. MU_OPENCODE_BIN exists for the tests.
set -euo pipefail

session_only=0
title='Project Mu DeepSeek session'
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --session-only) session_only=1; shift ;;
    --title) title="${2:?--title needs a value}"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done

if ! root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "opencode_session: not inside a git checkout (git rev-parse --show-toplevel failed)" >&2
  exit 2
fi
if [ ! -f "$root/.opencode/opencode.json" ]; then
  echo "opencode_session: $root has no .opencode/opencode.json; start this session from the Project Mu checkout" >&2
  exit 2
fi

export MU_AGENT_CLASS=deepseek
export MU_AGENT_HARNESS=opencode
export MU_AGENT_MODEL=deepseek-v4.1-flash
export MU_OPENCODE_MODEL="${MU_OPENCODE_MODEL:-opencode-go/deepseek-v4.1-flash}"

if [ -z "${MU_OPENCODE_SESSION:-}" ]; then
  MU_OPENCODE_SESSION="ses_mu_$(date +%s)_$(printf '%05d' $((RANDOM % 100000)))"
fi
export MU_OPENCODE_SESSION

echo "Project Mu DeepSeek route: $MU_OPENCODE_MODEL (harness opencode, session $MU_OPENCODE_SESSION)" >&2

if [ "$session_only" = 1 ]; then
  provider="${MU_OPENCODE_MODEL%%/*}"
  model_id="${MU_OPENCODE_MODEL#*/}"
  "${MU_OPENCODE_BIN:-opencode}" api post /api/session --data \
    "{\"id\":\"$MU_OPENCODE_SESSION\",\"title\":\"$title\",\"model\":{\"id\":\"$model_id\",\"providerID\":\"$provider\"},\"location\":{\"directory\":\"$root\"}}" \
    >/dev/null 2>&1 ||
    echo "opencode_session: the shared service did not answer; $MU_OPENCODE_SESSION will be created when a client opens it." >&2
  echo "session: $MU_OPENCODE_SESSION"
  exit 0
fi

exec "${MU_OPENCODE_BIN:-opencode}" --session "$MU_OPENCODE_SESSION" ${args[@]+"${args[@]}"}
