#!/usr/bin/env bash
# The one Muse launcher (MUSE_EMBER_ROTATION_PLAN §2.2).
#
# Muse agent sessions run in OpenCode, the same harness as DeepSeek, but on a Muse model: Spark on
# the Go plan for the benchmark's first step, Ember on OpenCode Zen for the second. This wrapper
# exists so every such session starts the same way: it refuses to start outside a checkout that
# carries the project OpenCode config, exports the route markers the rest of Project Mu reads (the
# board inbox, /context-check's harness choice, the model string, the commit trailer), mints one
# session id and passes it as `--session` so the window has a stable id `/context-check` can export,
# prints the route line, then hands the terminal to OpenCode. `tools/spawn_agent.sh <label> muse`
# calls it. `--model` is on the command line because the project pin is DeepSeek; a bare `opencode`
# in this repo would otherwise start the wrong model. No key lives here or in the repo — OpenCode
# uses its own stored Console credential.
#
#   tools/muse_session.sh [--prompt TEXT] [any opencode arguments]
#
# MU_MUSE_MODEL overrides the pinned model (step 2 is `MU_MUSE_MODEL=opencode/muse-ember-1.0`), and
# MU_OPENCODE_SESSION reuses an existing session (a resume); both are exported so a session can be
# continued. MU_OPENCODE_BIN exists for the tests.
set -euo pipefail

if ! root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "muse_session: not inside a git checkout (git rev-parse --show-toplevel failed)" >&2
  exit 2
fi
if [ ! -f "$root/.opencode/opencode.json" ]; then
  echo "muse_session: $root has no .opencode/opencode.json; start this session from the Project Mu checkout" >&2
  exit 2
fi

export MU_MUSE_MODEL="${MU_MUSE_MODEL:-opencode-go/muse-spark-1.3-contributor}"
export MU_AGENT_CLASS=muse
export MU_AGENT_HARNESS=opencode
export MU_AGENT_MODEL="${MU_MUSE_MODEL#*/}"
export MU_OPENCODE_MODEL="$MU_MUSE_MODEL"

if [ -z "${MU_OPENCODE_SESSION:-}" ]; then
  MU_OPENCODE_SESSION="ses_mu_$(date +%s)_$(printf '%05d' $((RANDOM % 100000)))"
fi
export MU_OPENCODE_SESSION

echo "Project Mu Muse route: $MU_OPENCODE_MODEL (harness opencode, model $MU_AGENT_MODEL, session $MU_OPENCODE_SESSION)" >&2

exec "${MU_OPENCODE_BIN:-opencode}" --model "$MU_OPENCODE_MODEL" --session "$MU_OPENCODE_SESSION" "$@"
