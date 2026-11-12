#!/usr/bin/env bash
# Start or resume one Gemini 4 Hydrogen print-mode batch, and record its conversation id.
#
# A board message cannot give an interactive `agy -i` session a turn, and an interactive session
# exposes no id to resume (docs/reference/AUDIT_QUEUE_INVENTORY.md §11). A print-mode conversation
# can be resumed from outside: the first turn returns a `conversation_id`, and later turns reopen it
# with `--conversation <id>`. This launcher is how a Hydrogen auditor starts and continues, and
# `tools/agent_board_wake.py --harness agy --conversation <id>` sits behind it to deliver board
# messages as turns.
#
#   tools/agy_batch.sh [--label L] [--model M] [--state PATH] [--timeout S] <prompt-file|-> 
#
# The first call is a normal turn; the id is saved to the label's state file
# (`data/audit_reports/<label>/agy_batch.json` at the checkout root, git-ignored) and printed as
# `conversation: <id>`; every later call with the same label resumes that conversation. Each turn's
# `usage` block is printed and appended to the Antigravity ledger, so a scripted batch is metered.
#
# MU_AGY_BIN exists for the tests; MU_HYDROGEN_MODEL overrides the model; MU_ANTIGRAVITY_LEDGER
# moves the usage ledger. Exit: 0 SUCCESS, 1 any other status, 2 usage error, 3 no JSON result.
set -euo pipefail

usage_line="usage: agy_batch.sh [--label L] [--model M] [--state PATH] [--timeout S] <prompt-file|->"
label=hydrogen-batch
model="${MU_HYDROGEN_MODEL:-gemini-4-hydrogen-high}"
state=''
timeout_s=1800
prompt_file=''
while [ $# -gt 0 ]; do
  case "$1" in
    --label)   label="${2:?--label needs a value}"; shift 2 ;;
    --model)   model="${2:?--model needs a value}"; shift 2 ;;
    --state)   state="${2:?--state needs a value}"; shift 2 ;;
    --timeout) timeout_s="${2:?--timeout needs a value}"; shift 2 ;;
    -h|--help) printf '%s\n' "$usage_line"; exit 0 ;;
    -)         if [ -n "$prompt_file" ]; then echo "agy_batch: one prompt file only" >&2; exit 2; fi
               prompt_file='-'; shift ;;
    -*)        echo "agy_batch: unknown option $1" >&2; printf '%s\n' "$usage_line" >&2; exit 2 ;;
    *)         if [ -n "$prompt_file" ]; then echo "agy_batch: one prompt file only" >&2; exit 2; fi
               prompt_file="$1"; shift ;;
  esac
done
if [ -z "$prompt_file" ]; then printf '%s\n' "$usage_line" >&2; exit 2; fi
case "$timeout_s" in ''|*[!0-9]*) echo "agy_batch: --timeout must be a whole number of seconds" >&2; exit 2 ;; esac

if ! root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "agy_batch: not inside a git checkout; run it from the Project Mu checkout" >&2
  exit 2
fi
# The usage ledger is the repo's own tool, so import it from this script's checkout, not from
# whatever tree the batch runs in.
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "$state" ]; then state="$root/data/audit_reports/$label/agy_batch.json"; fi
bin="${MU_AGY_BIN:-agy}"

if [ "$prompt_file" = '-' ]; then
  prompt="$(cat)"
else
  if [ ! -f "$prompt_file" ]; then echo "agy_batch: no such prompt file: $prompt_file" >&2; exit 2; fi
  prompt="$(cat "$prompt_file")"
fi
if [ -z "${prompt//[[:space:]]/}" ]; then echo "agy_batch: the prompt is empty" >&2; exit 2; fi

conversation=''
if [ -f "$state" ]; then
  conversation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("conversation_id") or "")' "$state")"
fi

argv=("$bin" --model "$model" --output-format json --print-timeout 0)
if [ -n "$conversation" ]; then argv+=(--conversation "$conversation"); fi
argv+=("-p=$prompt")

if [ -n "$conversation" ]; then
  echo "agy_batch: resuming $label ($model, conversation $conversation)" >&2
else
  echo "agy_batch: starting $label ($model, new conversation)" >&2
fi

result_file="$(mktemp "${TMPDIR:-/tmp}/agy_batch.XXXXXX")"
error_file="$(mktemp "${TMPDIR:-/tmp}/agy_batch.XXXXXX")"
trap 'rm -f "$result_file" "$error_file"' EXIT

status=0
if [ "$timeout_s" -gt 0 ]; then
  timeout "$timeout_s" "${argv[@]}" >"$result_file" 2>"$error_file" || status=$?
else
  "${argv[@]}" >"$result_file" 2>"$error_file" || status=$?
fi
if [ "$status" -ne 0 ]; then
  echo "agy_batch: agy exited $status; no turn was recorded" >&2
  sed -n '1,20p' "$error_file" >&2
  exit "$status"
fi
if [ -s "$error_file" ]; then sed -n '1,20p' "$error_file" >&2; fi

python3 - "$result_file" "$state" "$label" "$model" "$source_root" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

result_path, state_path, label, model, source_root = sys.argv[1:6]
raw = Path(result_path).read_text(encoding="utf-8")
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print(f"agy_batch: agy printed no JSON result: {raw[:400]!r}", file=sys.stderr)
    raise SystemExit(3)
if not isinstance(payload, dict):
    print(f"agy_batch: agy printed JSON that is not a result object: {raw[:400]!r}", file=sys.stderr)
    raise SystemExit(3)

status = str(payload.get("status") or "UNKNOWN").upper()
response = payload.get("response")
if isinstance(response, str) and response.strip():
    print(response.rstrip())
print(f"status: {status}  turns: {payload.get('num_turns')}  "
      f"duration_seconds: {payload.get('duration_seconds')}")
print("usage: " + json.dumps(payload.get("usage") or {}, sort_keys=True))

conversation = payload.get("conversation_id") or ""
if conversation:
    state = Path(state_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "label": label,
        "model": model,
        "conversation_id": conversation,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(state)
    print(f"conversation: {conversation}")
    print(f"state: {state}")

# The turn's tokens are the meter this route was missing: record them, but never fail a batch over it.
sys.path.insert(0, source_root)
try:
    from tools.antigravity_usage import BadRecord, append_record, parse_result
except ImportError as exc:
    print(f"usage ledger not updated: {exc}")
else:
    try:
        print(f"usage recorded: {append_record(parse_result(payload, model))}")
    except (BadRecord, OSError, ValueError) as exc:
        print(f"usage ledger not updated: {exc}")

raise SystemExit(0 if status == "SUCCESS" else 1)
PY
