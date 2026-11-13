#!/usr/bin/env bash
# Disposable scratch franchise for the P8-08 / P8-09 walk: a COPY of the live database, a scratch registry in /tmp with
# CAR as the user team, and a second uvicorn on :8011. Never touches the live site (:8000), data/franchises.json,
# madden_mu2026 or any save. Full recipe, URLs and teardown: docs/reference/P8_SCRATCH_FRANCHISE.md
#
#   tools/p8_scratch_franchise.sh up            # create/copy the DB, write the registry, start :8011, prove + take control
#   tools/p8_scratch_franchise.sh refresh       # stop :8011, drop and re-copy the DB from the live source, registry, up
#   tools/p8_scratch_franchise.sh status        # DB/registry/server state and the CAR-vs-CHI checks
#   tools/p8_scratch_franchise.sh take-control [ROLE]   # the one POST the page needs (default GM); idempotent
#   tools/p8_scratch_franchise.sh down          # stop :8011 only (DB and registry stay)
#   tools/p8_scratch_franchise.sh drop          # stop :8011 and DROP DATABASE mu_scratch_p808
#
# Env: MU_SCRATCH_SOURCE_DB (default madden_mu2026), MU_PG_CONTAINER (default madden-db), MU_SCRATCH_DIR
#      (default /tmp/opencode/p808), MU_SCRATCH_PORT (default 8011).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DB=mu_scratch_p808
SOURCE_DB=${MU_SCRATCH_SOURCE_DB:-madden_mu2026}
PG=${MU_PG_CONTAINER:-madden-db}
DIR=${MU_SCRATCH_DIR:-/tmp/opencode/p808}
PORT=${MU_SCRATCH_PORT:-8011}
REGISTRY="$DIR/franchises.json"
PIDFILE="$DIR/server.pid"
LOG="$DIR/server.log"
PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"   # run from any cwd (audit F3)
DSN="host=localhost dbname=$DB user=postgres password=postgres"
USER_TEAM=CAR

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

# Guards: this script must never create, drop or point at a real franchise database.
case "$DB" in
  mu_scratch_*) ;;
  *) echo "refusing to work on database '$DB' (must be mu_scratch_*)" >&2; exit 2 ;;
esac
case "$SOURCE_DB" in
  mu_scratch_*|postgres|template*) echo "refusing to copy from '$SOURCE_DB'" >&2; exit 2 ;;
esac
# The two overrides are guarded too: this script must never take the live site's port or the live registry (audit F4).
case "$PORT" in
  ''|*[!0-9]*) echo "MU_SCRATCH_PORT must be a port number, got '$PORT'" >&2; exit 2 ;;
  8000) echo "refusing port 8000 (the live site); pick a scratch port such as 8011" >&2; exit 2 ;;
esac
LIVE_REGISTRY="$ROOT/data/franchises.json"
if [ -e "$LIVE_REGISTRY" ] && [ "$(readlink -f "$REGISTRY")" = "$(readlink -f "$LIVE_REGISTRY")" ]; then
  echo "refusing to write the live franchise registry ($LIVE_REGISTRY); set MU_SCRATCH_DIR elsewhere" >&2
  exit 2
fi

psql_t() { docker exec -i "$PG" psql -U postgres -d "${2:-postgres}" -tAc "$1"; }
db_exists() { [ "$(psql_t "select 1 from pg_database where datname = '$DB'")" = "1" ]; }
# `|| true` on both lines: under `set -o pipefail` a bare grep with no match would abort every caller (audit F1).
listening_pid() { ss -ltnp 2>/dev/null | grep ":$PORT " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1 || true; }
server_pid() {
  local pid; pid=$([ -f "$PIDFILE" ] && cat "$PIDFILE" || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "$pid"; else listening_pid; fi
  return 0
}
server_up() {
  local pid; pid=$(server_pid)
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
    && curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/overview"
}
port_busy() { ss -ltn 2>/dev/null | grep -q ":$PORT "; }
# A pid taken from the port (no pidfile) is only ours if it is really this scratch uvicorn (audit F4).
owns_listener() {
  local pid=${1:-} args
  [ -n "$pid" ] || return 1
  args=$(ps -o args= -p "$pid" 2>/dev/null || true)
  case "$args" in *uvicorn*server.api:app*"--port $PORT"*) return 0 ;; *) return 1 ;; esac
}

stop_server() {
  local pid; pid=$(server_pid || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    if [ ! -f "$PIDFILE" ] && ! owns_listener "$pid"; then
      echo "refusing to stop pid $pid on :$PORT: it is not this scratch server (no $PIDFILE)" >&2
      return 1
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    echo "stopped :$PORT (pid $pid)"
  else
    echo ":$PORT is not running"
  fi
  rm -f "$PIDFILE"
  return 0
}

copy_db() {
  echo "copying $SOURCE_DB -> $DB (pg_dump | psql, no CREATE DATABASE ... TEMPLATE)"
  db_exists || psql_t "create database $DB" >/dev/null
  docker exec "$PG" pg_dump -U postgres -d "$SOURCE_DB" \
    | docker exec -i "$PG" psql -q -v ON_ERROR_STOP=1 -U postgres -d "$DB" >/dev/null
  local tables version
  tables=$(psql_t "select count(*) from information_schema.tables where table_schema='public'" "$DB")
  version=$(psql_t "select version from schema_migrations order by version desc limit 1" "$DB")
  echo "$DB: $tables tables, schema_migrations head = $version"
}

write_registry() {
  local league_id
  league_id=$(psql_t "select madden_league_id from league_state where id = 1" "$DB")
  case "$league_id" in
    ''|*[!0-9]*) echo "no numeric league_state.madden_league_id in $DB; refusing to write a registry" >&2; return 1 ;;
  esac
  mkdir -p "$DIR"
  MU_FRANCHISE_REGISTRY="$REGISTRY" MU_SCRATCH_LEAGUE_ID="$league_id" "$PY" - <<'PY'
import os
from db import franchises as f
league_id = int(os.environ["MU_SCRATCH_LEAGUE_ID"])
key = f.franchise_key(league_id)
with f.editing_registry() as reg:
    entry = reg["franchises"].get(key)
    if entry is None:
        entry = f.register(reg, league_id, name="P8-08 scratch (CAR user team)",
                           user_teams=["CAR"], save_name="CAREER-MUBASE")
    entry["db"] = "mu_scratch_p808"          # the scratch copy, never a madden_* database
    entry["user_teams"] = ["CAR"]
    reg["active"] = key
PY
  echo "registry: $REGISTRY (active $league_id, db $DB, user_teams [$USER_TEAM])"
}

start_server() {
  if server_up; then echo ":$PORT already up (pid $(server_pid))"; return 0; fi
  if port_busy; then
    echo "port $PORT is taken by something that is not this scratch server:" >&2
    ss -ltnp 2>/dev/null | grep ":$PORT " >&2 || true
    return 1
  fi
  mkdir -p "$DIR"
  MU_FRANCHISE_REGISTRY="$REGISTRY" DATABASE_URL="host=localhost dbname=$DB user=postgres password=postgres" \
    setsid nohup "$PY" -m uvicorn server.api:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null 2>/dev/null "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/overview"; then
      echo ":$PORT up (pid $(server_pid)), log $LOG"
      return 0
    fi
    sleep 0.5
  done
  echo ":$PORT did not come up; last log lines:" >&2
  tail -20 "$LOG" >&2
  return 1
}

json_field() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }

show_status() {
  echo "db:       $(db_exists && echo "$DB (present)" || echo "$DB (missing)")"
  echo "registry: $([ -f "$REGISTRY" ] && echo "$REGISTRY" || echo 'missing')"
  echo "server:   $(server_up && echo "http://127.0.0.1:$PORT (pid $(server_pid))" || echo 'not running')"
  server_up || return 0
  local car chi inbox_car inbox_chi
  car=$(curl -fsS "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/overview" | json_field "d.get('user_controlled')")
  chi=$(curl -fsS "http://127.0.0.1:$PORT/api/franchise/CHI/overview" | json_field "d.get('user_controlled')")
  inbox_car=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/inbox")
  inbox_chi=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/franchise/CHI/inbox")
  echo "check:    $USER_TEAM user_controlled=$car, CHI user_controlled=$chi, $USER_TEAM inbox=$inbox_car, CHI inbox=$inbox_chi"
  echo "actors:   $(curl -fsS "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/inbox/actors")"
}

take_control() {
  local role=${1:-GM} body
  server_up || { echo ":$PORT is not running; run 'up' first" >&2; return 1; }
  body=$(curl -fsS "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/inbox/actors" \
    | ROLE="$role" "$PY" -c 'import json,os,sys
d = json.load(sys.stdin)
if d.get("controlled"):
    print(json.dumps({"already": d["controlled"]})); raise SystemExit
c = [x for x in d.get("candidates") or [] if x.get("role") == os.environ["ROLE"]]
if not c:
    print(json.dumps({"error": "no " + os.environ["ROLE"] + " candidate"})); raise SystemExit(1)
print(json.dumps({"role": os.environ["ROLE"], "persona_id": c[0]["persona_id"]}))' || true)
  case "$body" in
    '') echo "take-control: the actors endpoint gave no decision; nothing posted" >&2; return 1 ;;
    *already*) echo "take-control: already held -> $body"; return 0 ;;
    *error*) echo "take-control: $body" >&2; return 1 ;;
  esac
  echo -n "take-control POST /api/franchise/$USER_TEAM/inbox/actors $body -> "
  curl -s -w ' [%{http_code}]\n' -X POST "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/inbox/actors" \
    -H 'Content-Type: application/json' -d "$body"
  echo "actors now: $(curl -fsS "http://127.0.0.1:$PORT/api/franchise/$USER_TEAM/inbox/actors")"
}

cmd=${1:-}
case "$cmd" in
  up)
    db_exists || copy_db
    write_registry
    start_server
    take_control GM
    echo
    show_status
    echo
    echo "walk it at http://127.0.0.1:$PORT/#franchise-inbox; stop with '$0 down', drop with '$0 drop'"
    ;;
  refresh)
    stop_server
    if db_exists; then psql_t "drop database if exists $DB with (force)" >/dev/null; fi
    copy_db
    write_registry
    start_server
    take_control GM
    show_status
    ;;
  status) show_status ;;
  take-control) shift; take_control "${1:-GM}" ;;
  down) stop_server ;;
  drop)
    stop_server
    if db_exists; then
      psql_t "drop database if exists $DB with (force)" >/dev/null
      echo "dropped $DB"
    else
      echo "$DB does not exist"
    fi
    ;;
  -h|--help|help|'') usage ;;
  *) echo "unknown command '$cmd'" >&2; usage >&2; exit 2 ;;
esac
