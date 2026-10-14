"""
Debug page backend (/api/debug/*, the frontend's #debug page; JR, Oct 14). Opened from the site's LIVE
badge, also from a phone on the home network.

- League state: read, and edit (season / week); recent captures rows and the run folders that can be
  resumed (weekly_run.log present, not loaded; a run that stopped before its load has no captures row).
- Tables: every table and view in the public schema, with columns, sorted / filtered / paginated rows,
  and cell edits on base tables by primary key (generated and key columns can't be edited).
- Endpoints: every route of the FastAPI app. Docs: PROJECT_SPEC.md and the other spec docs as Markdown.
- Jobs: the weekly runs started on demand (pipeline/weekly_capture.py), one at a time, in their own
  process session so an API reload doesn't stop them: scan_week (full run with articles),
  backfill_week (the same, no articles), game_scan (every game of last week, --mode games), each
  optionally resuming a stopped run folder. Records, logs and exit codes in captures/jobs/.

Anything that writes (state, cells, jobs) needs the X-Debug-Token header to equal DEBUG_TOKEN (env or the
project-root .env); reads are open, like the rest of the API.
"""
import datetime, json, os, re, signal, subprocess, sys
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, Header, HTTPException, Request
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel

from pipeline.notify import env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS_DIR = os.path.join(ROOT, "captures", "jobs")
CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
DOCS = {"spec": "PROJECT_SPEC.md", "migration": "docs/PIPELINE_MIGRATION.md", "frontend": "Mu_Network/PROJECT_SPEC.md"}
MAX_PAGE_SIZE = 200
JOB_KINDS = {
    "scan_week": ("Scan week", []),
    "backfill_week": ("Backfill previous week (no articles)", ["--no-articles"]),
    "game_scan": ("Game scan (last week's games)", ["--mode", "games"]),
}

router = APIRouter(prefix="/api/debug")


def connect():
    return psycopg.connect(CONN_INFO, row_factory=dict_row)


def require_token(token: Optional[str]):
    expected = env("DEBUG_TOKEN")
    if not expected:
        raise HTTPException(403, "DEBUG_TOKEN isn't set in .env; writes are off")
    if token != expected:
        raise HTTPException(401, "wrong or missing debug token")


# ------------------------------------------------------------- state ----
class StateIn(BaseModel):
    current_season: int
    current_week: int


def resumable_runs(loaded, limit=15):
    """Weekly-run folders (captures/<run>/weekly_run.log) not loaded yet, newest first, with how far they
    got. A run that stopped before its load has no captures row, so the folders are the list."""
    from pipeline.weekly_capture import CAPTURES, capture_done, extract_done
    out = []
    for name in sorted(os.listdir(CAPTURES), reverse=True):
        folder = os.path.join(CAPTURES, name)
        if name in loaded or not os.path.exists(os.path.join(folder, "weekly_run.log")):
            continue
        mode = "games" if os.path.isdir(os.path.join(folder, "games")) else "weekly"
        stage = ("extracted" if extract_done(folder, mode) else "captured" if capture_done(folder, mode)
                 else "capture incomplete")
        out.append({"folder": name, "mode": mode, "stage": stage})
        if len(out) >= limit:
            break
    return out


@router.get("/state")
def get_state():
    with connect() as conn:
        state = conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()
        captures = conn.execute("""SELECT id, folder, mode, season, week, status, error, captured_at, extracted_at,
                                          loaded_at FROM captures ORDER BY id DESC LIMIT 10""").fetchall()
        db = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        loaded = {r["folder"] for r in conn.execute("SELECT folder FROM captures WHERE status = 'loaded'").fetchall()}
    return {"state": state, "database": db, "captures": captures, "writes_enabled": bool(env("DEBUG_TOKEN")),
            "running_job": running_job(), "resumable": resumable_runs(loaded)}


@router.put("/state")
def put_state(body: StateIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if not (1 <= body.current_week <= 23) or not (2000 <= body.current_season <= 2100):
        raise HTTPException(400, "week must be 1-23 and season a year")
    with connect() as conn:
        row = conn.execute("""UPDATE league_state SET current_season = %s, current_week = %s WHERE id = 1
                              RETURNING current_season, current_week""",
                           (body.current_season, body.current_week)).fetchone()
    return {"state": row}


# ------------------------------------------------------------ tables ----
def table_info(conn, name=None):
    """{table: {kind, columns: [{name, type, nullable, generated, pk}], pk: [...], rows (estimate)}}."""
    rows = conn.execute("""
        SELECT c.relname AS table, c.relkind, c.reltuples::bigint AS estimate,
               a.attname AS column, format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable,
               a.attgenerated <> '' AS generated, a.attnum,
               COALESCE(a.attnum = ANY(i.indkey), false) AS pk
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary
        WHERE c.relkind IN ('r', 'v', 'm') AND (%s::text IS NULL OR c.relname = %s)
        ORDER BY c.relname, a.attnum""", (name, name)).fetchall()
    out = {}
    for r in rows:
        t = out.setdefault(r["table"], {"kind": {"r": "table", "v": "view", "m": "view"}[r["relkind"]],
                                        "rows": max(int(r["estimate"]), 0) if r["relkind"] == "r" else None,
                                        "columns": [], "pk": []})
        t["columns"].append({k: r[k] for k in ("column", "type", "nullable", "generated", "pk")})
        if r["pk"]:
            t["pk"].append(r["column"])
    return out


@router.get("/tables")
def list_tables():
    with connect() as conn:
        info = table_info(conn)
    return [{"name": k, "kind": v["kind"], "rows": v["rows"], "columns": len(v["columns"]), "pk": v["pk"]}
            for k, v in sorted(info.items())]


def _table(conn, name):
    info = table_info(conn, name).get(name)
    if not info:
        raise HTTPException(404, f"no table or view {name!r}")
    return info


FILTER_RE = re.compile(r"^(>=|<=|=|>|<|!=)\s*(.*)$")


def where_clause(info, q, filters):
    """SQL + params for a free-text search over every column and per-column filters. A filter value
    starting with =, !=, >, <, >=, <= compares (the value cast to the column's type); 'null' / '!null'
    match NULLs; anything else is a case-insensitive contains."""
    cols = {c["column"]: c for c in info["columns"]}
    parts, params = [], []
    if q:
        parts.append(sql.SQL("({})").format(sql.SQL(" OR ").join(
            sql.SQL("{}::text ILIKE %s").format(sql.Identifier(c)) for c in cols)))
        params += [f"%{q}%"] * len(cols)
    for col, raw in (filters or {}).items():
        if col not in cols:
            raise HTTPException(400, f"no column {col!r}")
        raw = str(raw).strip()
        if raw == "":
            continue
        ident = sql.Identifier(col)
        if raw.lower() in ("null", "!null"):
            parts.append(sql.SQL("{} IS NOT NULL" if raw.startswith("!") else "{} IS NULL").format(ident))
            continue
        m = FILTER_RE.match(raw)
        if m:
            parts.append(sql.SQL("{} " + m.group(1) + " CAST(%s AS {})").format(ident, sql.SQL(cols[col]["type"])))
            params.append(m.group(2))
        else:
            parts.append(sql.SQL("{}::text ILIKE %s").format(ident))
            params.append(f"%{raw}%")
    if not parts:
        return sql.SQL(""), []
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


@router.get("/tables/{name}")
def table_rows(name: str, page: int = 1, page_size: int = 50, sort: Optional[str] = None, dir: str = "asc",
               q: Optional[str] = None, filters: Optional[str] = None):
    """filters: a JSON object {column: value} (see where_clause)."""
    page, page_size = max(1, page), max(1, min(page_size, MAX_PAGE_SIZE))
    try:
        flt = json.loads(filters) if filters else {}
    except ValueError:
        raise HTTPException(400, "filters must be a JSON object")
    with connect() as conn:
        info = _table(conn, name)
        names = [c["column"] for c in info["columns"]]
        where, params = where_clause(info, q, flt)
        order = sql.SQL("")
        if sort:
            if sort not in names:
                raise HTTPException(400, f"no column {sort!r}")
            order = sql.SQL(" ORDER BY {} {} NULLS LAST").format(sql.Identifier(sort),
                                                                 sql.SQL("DESC" if dir == "desc" else "ASC"))
        elif info["pk"]:
            order = sql.SQL(" ORDER BY ") + sql.SQL(", ").join(sql.Identifier(c) for c in info["pk"])
        try:
            total = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(name)) + where,
                                 params).fetchone()["n"]
            rows = conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(name)) + where + order
                                + sql.SQL(" LIMIT %s OFFSET %s"), params + [page_size, (page - 1) * page_size]).fetchall()
        except psycopg.errors.DataError as e:
            raise HTTPException(400, f"filter value doesn't fit the column: {e}")
    return {"table": name, "kind": info["kind"], "columns": info["columns"], "pk": info["pk"], "total": total,
            "page": page, "page_size": page_size, "rows": rows}


class CellEdit(BaseModel):
    key: dict[str, Any]          # primary key column -> value of the row to edit
    changes: dict[str, Any]      # column -> new value (None = NULL)


@router.patch("/tables/{name}")
def edit_row(name: str, body: CellEdit, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    with connect() as conn:
        info = _table(conn, name)
        if info["kind"] != "table" or not info["pk"]:
            raise HTTPException(400, f"{name} is a view or has no primary key; it can't be edited here")
        cols = {c["column"]: c for c in info["columns"]}
        if set(body.key) != set(info["pk"]):
            raise HTTPException(400, f"key must give exactly {info['pk']}")
        if not body.changes:
            raise HTTPException(400, "nothing to change")
        for col in body.changes:
            if col not in cols:
                raise HTTPException(400, f"no column {col!r}")
            if cols[col]["generated"] or cols[col]["pk"]:
                raise HTTPException(400, f"{col} is a generated or key column")
        sets = sql.SQL(", ").join(sql.SQL("{} = CAST(%s AS {})").format(sql.Identifier(c), sql.SQL(cols[c]["type"]))
                                  for c in body.changes)
        keys = sql.SQL(" AND ").join(sql.SQL("{} = CAST(%s AS {})").format(sql.Identifier(c), sql.SQL(cols[c]["type"]))
                                     for c in info["pk"])
        params = [None if v is None else str(v) for v in body.changes.values()] + [str(body.key[c]) for c in info["pk"]]
        try:
            row = conn.execute(sql.SQL("UPDATE {} SET ").format(sql.Identifier(name)) + sets + sql.SQL(" WHERE ")
                               + keys + sql.SQL(" RETURNING *"), params).fetchone()
        except psycopg.Error as e:
            raise HTTPException(400, f"update refused: {e}")
        if not row:
            raise HTTPException(404, "no row with that key")
    return {"row": row}


# ----------------------------------------------------- endpoints, docs ----
@router.get("/endpoints")
def endpoints(request: Request):
    out = []
    for r in request.app.routes:
        methods = sorted(m for m in (getattr(r, "methods", None) or []) if m not in ("HEAD", "OPTIONS"))
        if not methods:
            continue
        dep = getattr(r, "dependant", None)
        doc = (getattr(r, "endpoint", None).__doc__ or "").strip().split("\n")[0] if getattr(r, "endpoint", None) else ""
        out.append({"path": r.path, "methods": methods, "name": getattr(r, "name", ""), "doc": doc,
                    "path_params": [p.name for p in dep.path_params] if dep else [],
                    "query_params": [p.name for p in dep.query_params] if dep else []})
    return sorted(out, key=lambda e: (e["path"], e["methods"]))


@router.get("/docs/{doc}")
def doc(doc: str):
    if doc not in DOCS:
        raise HTTPException(404, f"docs: {sorted(DOCS)}")
    with open(os.path.join(ROOT, DOCS[doc])) as fh:
        return {"doc": doc, "path": DOCS[doc], "markdown": fh.read()}


# -------------------------------------------------------------- jobs ----
class JobIn(BaseModel):
    kind: str
    resume: Optional[str] = None     # a captures/<run> folder to continue


def _job_path(job_id, ext):
    return os.path.join(JOBS_DIR, f"{job_id}.{ext}")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                   # a finished child of this process is a zombie until reaped
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True
    except OSError:
        return True


def _load_job(job_id):
    with open(_job_path(job_id, "json")) as fh:
        job = json.load(fh)
    exit_path = _job_path(job_id, "exit")
    if os.path.exists(exit_path):
        code = open(exit_path).read().strip()
        job["exit_code"] = int(code) if code.lstrip("-").isdigit() else None
        job["status"] = "done" if job["exit_code"] == 0 else ("stopped" if job.get("stop_requested") else "failed")
    elif _alive(job["pid"]):
        job["status"] = "running"
    else:
        job["status"] = "lost"             # killed without writing its exit code
    return job


def list_jobs(limit=20):
    if not os.path.isdir(JOBS_DIR):
        return []
    ids = sorted((f[:-5] for f in os.listdir(JOBS_DIR) if f.endswith(".json")), reverse=True)[:limit]
    return [_load_job(i) for i in ids]


def running_job():
    return next((j for j in list_jobs(5) if j["status"] == "running"), None)


def tail(path, lines):
    try:
        with open(path, errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except OSError:
        return ""


@router.get("/jobs")
def get_jobs():
    return [{**j, "kind_label": JOB_KINDS.get(j["kind"], (j["kind"],))[0],
             "log_tail": tail(_job_path(j["id"], "log"), 30)} for j in list_jobs()]


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str, lines: int = 400):
    if not re.fullmatch(r"[0-9_]+", job_id) or not os.path.exists(_job_path(job_id, "json")):
        raise HTTPException(404, "no such job")
    return {"job": _load_job(job_id), "log": tail(_job_path(job_id, "log"), max(1, min(lines, 5000)))}


def job_command(kind, resume=None):
    """The weekly run for a job kind (pipeline/weekly_capture.py)."""
    args = [sys.executable, "-u", "-m", "pipeline.weekly_capture", *JOB_KINDS[kind][1]]
    return args + (["--resume", resume] if resume else [])


@router.post("/jobs")
def start_job(body: JobIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if body.kind not in JOB_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(JOB_KINDS)}")
    if body.resume and not re.fullmatch(r"[0-9A-Za-z_\-]+", body.resume):
        raise HTTPException(400, "resume must be a captures folder name")
    if body.resume and not os.path.isdir(os.path.join(ROOT, "captures", body.resume)):
        raise HTTPException(404, f"no captures/{body.resume}")
    busy = running_job()
    if busy:
        raise HTTPException(409, f"job {busy['id']} ({busy['kind']}) is still running")
    os.makedirs(JOBS_DIR, exist_ok=True)
    job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    args = job_command(body.kind, body.resume)
    # sh writes the exit code next to the log, so the status survives an API restart
    cmd = ["sh", "-c", '"$@"; echo $? > "$EXIT"', "sh", *args]
    log = open(_job_path(job_id, "log"), "w")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "EXIT": _job_path(job_id, "exit"), "PYTHONUNBUFFERED": "1"})
    job = {"id": job_id, "kind": body.kind, "resume": body.resume, "args": args[1:], "pid": p.pid,
           "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(_job_path(job_id, "json"), "w") as fh:
        json.dump(job, fh, indent=1)
    return _load_job(job_id)


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str, x_debug_token: Optional[str] = Header(None)):
    """Ctrl-C to the job's process group: madden_capture releases every key and stops where it is."""
    require_token(x_debug_token)
    if not re.fullmatch(r"[0-9_]+", job_id) or not os.path.exists(_job_path(job_id, "json")):
        raise HTTPException(404, "no such job")
    job = _load_job(job_id)
    if job["status"] != "running":
        raise HTTPException(409, f"job is {job['status']}")
    os.killpg(job["pid"], signal.SIGINT)
    job["stop_requested"] = True
    with open(_job_path(job_id, "json"), "w") as fh:
        json.dump({k: v for k, v in job.items() if k not in ("status", "exit_code")}, fh, indent=1)
    return {"stopping": job_id}
