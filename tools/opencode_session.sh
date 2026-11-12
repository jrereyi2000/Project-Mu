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
#   tools/opencode_session.sh [--prompt TEXT] [any opencode arguments]
#
# MU_OPENCODE_MODEL overrides the pinned model and MU_OPENCODE_SESSION reuses an existing session (a
# resume); both are exported so a session can be continued. MU_OPENCODE_BIN exists for the tests.
set -euo pipefail

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

exec "${MU_OPENCODE_BIN:-opencode}" --session "$MU_OPENCODE_SESSION" "$@"
