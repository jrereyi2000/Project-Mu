#!/usr/bin/env bash
# The one Muse launcher (MUSE_EMBER_ROTATION_PLAN §2.2).
#
# Muse agent sessions run in OpenCode, the same harness as DeepSeek, but on a Muse model: Spark on
# the Go plan for the benchmark's first step, Ember on OpenCode Zen for the second. This wrapper
# exists so every such session starts the same way: it refuses to start outside a checkout that
# carries the project OpenCode config, exports the route markers the rest of Project Mu reads (the
# board inbox, /context-check's harness choice, the model string, the commit trailer), mints one
# session id and pins the model on that session, so the window has a stable id `/context-check` can
# export, prints the route line, then hands the terminal to OpenCode.
#
# Why the model is pinned through the API and not on the command line (spike S-M1, 2026-11-12): the
# OpenCode TUI takes `--session` and `--prompt` but has no `--model` in v2.2.7 — it answers
# `Unrecognized flag: --model in command opencode` and exits 1. Only `opencode run` and
# `opencode mini` accept `--model`. The project pin in `.opencode/opencode.json` is DeepSeek, so a
# bare TUI would run DeepSeek and misattribute every board message and commit. The fix is the
# session-scoped model: `POST /api/session` creates the session with the model, and
# `POST /api/session/<id>/model` re-pins it on a resume (`--session` reuse), because a create on an
# existing id leaves that session's model alone. Both calls go through the same binary, so a stored
# Console credential and the shared background service serve them. If the pin fails the wrapper
# exits 3 rather than open a window that would run the wrong model.
#
#   tools/muse_session.sh [--prompt TEXT] [any opencode arguments]
#
# MU_MUSE_MODEL overrides the pinned model (step 2 is `MU_MUSE_MODEL=opencode/muse-ember-1.0`), and
# MU_OPENCODE_SESSION reuses an existing session (a resume); both are exported so a session can be
# continued. MU_OPENCODE_BIN exists for the tests. No key lives here or in the repo — OpenCode
# uses its own stored Console credential.
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

bin="${MU_OPENCODE_BIN:-opencode}"
provider="${MU_MUSE_MODEL%%/*}"
model_id="${MU_MUSE_MODEL#*/}"

pin_failed() {
  echo "muse_session: could not pin $MU_MUSE_MODEL on session $MU_OPENCODE_SESSION ($1)." >&2
  echo "muse_session: refusing to start — the OpenCode TUI has no --model and the project pin is DeepSeek, so this window would run the wrong model. Check 'opencode service status'." >&2
  exit 3
}

# Create the session with the Muse model (idempotent: an existing id is returned unchanged), then
# re-pin the model so a resumed session follows MU_MUSE_MODEL too.
"$bin" api post /api/session \
  --data "{\"id\":\"$MU_OPENCODE_SESSION\",\"title\":\"Project Mu Muse session\",\"model\":{\"id\":\"$model_id\",\"providerID\":\"$provider\"},\"location\":{\"directory\":\"$root\"}}" \
  >/dev/null 2>&1 || pin_failed "session create failed"
"$bin" api post "/api/session/$MU_OPENCODE_SESSION/model" \
  --data "{\"model\":{\"id\":\"$model_id\",\"providerID\":\"$provider\"}}" \
  >/dev/null 2>&1 || pin_failed "model pin failed"

echo "Project Mu Muse route: $MU_OPENCODE_MODEL (harness opencode, model $MU_AGENT_MODEL, session $MU_OPENCODE_SESSION)" >&2

exec "$bin" --session "$MU_OPENCODE_SESSION" "$@"
