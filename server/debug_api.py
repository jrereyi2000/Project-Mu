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
    "season_scan": ("Season scan (schedule, rosters, contracts, draft board)", ["--mode", "season", "--no-articles"]),
    "roster_scan": ("Roster scan", ["--mode", "roster", "--no-articles"]),
    "contract_scan": ("Contract scan (Team Salary, all 32 teams)", ["--mode", "contracts", "--no-articles"]),
    "transactions_scan": ("League transactions scan", ["--mode", "transactions", "--no-articles"]),
    "draft_scan": ("Draft prospect scan", ["--mode", "prospects", "--no-articles"]),
    "publish_articles": ("Publish articles (newsroom run)", []),
    "custom_commission": ("Commission custom article", []),
    "offseason_run": ("Offseason run (editorial package)", []),
    "gm_proposals": ("GM trade proposals (this week)", []),
    "gm_offseason_stage": ("Next offseason stage (transactions scan + GM trades)",
                           ["--mode", "transactions", "--no-articles", "--gm-next-stage"]),
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
    from pipeline.weekly_capture import CAPTURES, capture_done, extract_done, folder_mode
    out = []
    for name in sorted(os.listdir(CAPTURES), reverse=True):
        folder = os.path.join(CAPTURES, name)
        if name in loaded or not os.path.exists(os.path.join(folder, "weekly_run.log")):
            continue
        mode = folder_mode(folder)
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


# -------------------------------------------------------------- news ----
class ClearNewsIn(BaseModel):
    season: Optional[int] = None
    week: Optional[int] = None
    content_type: str = "ARTICLE"  # "ARTICLE", "ALL", "TWEET"
    backup: bool = True


def backup_news_tables() -> Optional[str]:
    """Dumps news_feed and dependent tables to db/backups/news_feed_backup_<timestamp>.sql."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"news_feed_backup_{ts}.sql"
    backup_dir = os.path.join(ROOT, "db", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, backup_filename)

    candidate_tables = [
        "news_feed", "article_teams", "article_players", "power_rankings",
        "agent_memory_entries", "agent_predictions", "agent_season_summaries", "social_trends"
    ]
    try:
        with connect() as conn:
            existing = [t for t in candidate_tables
                        if conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ex", (f"public.{t}",)).fetchone()["ex"]]
    except Exception:
        existing = candidate_tables

    table_args = []
    for t in existing:
        table_args.extend(["-t", t])

    cmd_docker = ["docker", "exec", "madden-db", "pg_dump", "-U", "postgres", "-d", "madden", "--data-only", *table_args]
    try:
        with open(backup_path, "w") as fh:
            res = subprocess.run(cmd_docker, stdout=fh, stderr=subprocess.PIPE, text=True, timeout=30)
            if res.returncode == 0 and os.path.getsize(backup_path) > 0:
                return backup_filename
    except Exception:
        pass

    try:
        cmd_local = ["pg_dump", "-d", CONN_INFO, "--data-only", *table_args]
        with open(backup_path, "w") as fh:
            res = subprocess.run(cmd_local, stdout=fh, stderr=subprocess.PIPE, text=True, timeout=30)
            if res.returncode == 0 and os.path.getsize(backup_path) > 0:
                return backup_filename
    except Exception:
        pass

    if os.path.exists(backup_path) and os.path.getsize(backup_path) == 0:
        try:
            os.remove(backup_path)
        except OSError:
            pass
    return None


@router.get("/news")
def get_news_summary():
    with connect() as conn:
        state = conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()
        rows = conn.execute("""
            SELECT season, week,
                   COUNT(*) FILTER (WHERE content_type = 'ARTICLE')::int AS articles,
                   COUNT(*) FILTER (WHERE content_type = 'TWEET')::int AS tweets
            FROM news_feed
            GROUP BY season, week
            ORDER BY season DESC, week DESC
        """).fetchall()
        totals = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE content_type = 'ARTICLE')::int AS total_articles,
                COUNT(*) FILTER (WHERE content_type = 'TWEET')::int AS total_tweets
            FROM news_feed
        """).fetchone()
    return {
        "current_season": state["current_season"] if state else None,
        "current_week": state["current_week"] if state else None,
        "summary": rows,
        "total_articles": totals["total_articles"] if totals else 0,
        "total_tweets": totals["total_tweets"] if totals else 0,
    }


@router.get("/transactions")
def get_transactions(season: Optional[int] = None, week: Optional[int] = None, limit: int = 200):
    """League Transactions (team management step 2, PROJECT_SPEC §3.3e): the season's rows (one week with
    `week`), newest first, plus the unparsed and ambiguous rows to review."""
    limit = max(1, min(limit, 1000))
    with connect() as conn:
        if season is None:
            season = conn.execute("SELECT current_season FROM league_state WHERE id = 1").fetchone()["current_season"]
        if not conn.execute("SELECT to_regclass('transactions') IS NOT NULL AS ok").fetchone()["ok"]:
            return {"season": season, "installed": False, "rows": [], "unparsed": [], "ambiguous": []}
        cols = """t.id, t.season, t.week, t.txn_date, t.type, t.player_id, t.player_name, t.position,
                  ft.abbreviation AS from_team, tt.abbreviation AS to_team, t.contract_years, t.contract_value,
                  t.raw_text, t.parse_ok, t.flags, t.applied, t.trade_group, t.gm_proposal_id, t.created_at"""
        base = f"""SELECT {cols} FROM transactions t LEFT JOIN teams ft ON ft.id = t.from_team_id
                   LEFT JOIN teams tt ON tt.id = t.to_team_id WHERE t.season = %s"""
        rows = conn.execute(base + (" AND t.week = %s" if week is not None else "") + " ORDER BY t.id DESC LIMIT %s",
                            (season, week, limit) if week is not None else (season, limit)).fetchall()
        unparsed = conn.execute(base + " AND NOT t.parse_ok ORDER BY t.id DESC LIMIT 100", (season,)).fetchall()
        ambiguous = conn.execute(base + " AND t.flags LIKE '%%ambiguous%%' ORDER BY t.id DESC LIMIT 100", (season,)).fetchall()
    return {"season": season, "installed": True, "rows": rows, "unparsed": unparsed, "ambiguous": ambiguous}


# ------------------------------------------------------- front office ----
# Team management step 3 (docs/team_management/03_GM_AGENTS_PLAN.md §2.7; PROJECT_SPEC §3.3f): the GM agents'
# proposals, JR's approve / reject / confirm, recorded trades and the trade rows the transactions scan saw.
class ReasonIn(BaseModel):
    reason: Optional[str] = None


class RecordIn(BaseModel):
    team_a: str
    team_b: str
    assets: list[dict[str, Any]]
    proposal_id: Optional[int] = None
    note: Optional[str] = None


class ApplyIn(BaseModel):
    assets: Optional[list[dict[str, Any]]] = None


class ControlIn(BaseModel):
    user_controlled: Optional[bool] = None
    suggestions_on: Optional[bool] = None


def _front_office(action, *args, **kwargs):
    """Run a front_office action in one DB transaction (tuple rows); FrontOfficeError -> HTTP error."""
    from psycopg.rows import tuple_row
    from engines import front_office as fo
    with connect() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        if not fo.installed(cur):
            raise HTTPException(409, "the GM agents migration (2026_12_10_gm_agents.sql) isn't applied")
        try:
            out = getattr(fo, action)(cur, *args, **kwargs)
        except fo.FrontOfficeError as e:
            if e.status == 409 and action == "approve":
                conn.commit()                        # an approve that found the deal illegal expired it
            else:
                conn.rollback()
            raise HTTPException(e.status, e.message)
        conn.commit()
    return out


@router.get("/front-office")
def get_front_office(season: Optional[int] = None):
    """Front Office (team management step 3): window, deadline, budget pace, 32 directions with needs,
    proposals with assets, cap effects, legality, Madden steps and status chips, flags, sightings."""
    from psycopg.rows import tuple_row
    from engines import front_office as fo
    with connect() as conn:
        return fo.front_office_state(conn.cursor(row_factory=tuple_row), season)


@router.get("/front-office/players")
def front_office_players(q: str, season: Optional[int] = None):
    """The trade finder's player picker: rostered players matching q, best first."""
    from psycopg.rows import tuple_row
    from engines import front_office as fo
    if len(q.strip()) < 2:
        return []
    with connect() as conn:
        return fo.search_players(conn.cursor(row_factory=tuple_row), q.strip(), season)


@router.get("/front-office/trade-finder")
def front_office_trade_finder(player_id: int, team: Optional[str] = None, per_team: int = 3):
    """Trade finder (read-only, nothing stored): every package the GM engine's rules can build for this player
    from each other team, with both GMs' surplus, cap effects, checks, and why the nearest misses fail."""
    from psycopg.rows import tuple_row
    from engines import front_office as fo
    with connect() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        if not fo.installed(cur):
            raise HTTPException(409, "the GM agents migration (2026_12_10_gm_agents.sql) isn't applied")
        try:
            return fo.trade_finder(cur, player_id, per_team=max(1, min(per_team, 10)), team=team)
        except fo.FrontOfficeError as e:
            raise HTTPException(e.status, e.message)


@router.post("/front-office/proposals/{proposal_id}/{action}")
def front_office_action(proposal_id: int, action: str, body: Optional[ReasonIn] = None,
                        x_debug_token: Optional[str] = Header(None)):
    """approve | reject {reason} | confirm (Made it) | could-not-make {reason}. confirm re-checks legality and
    warns but never blocks: Madden is the truth once JR made the trade."""
    require_token(x_debug_token)
    reason = body.reason if body else None
    if action == "approve":
        return _front_office("approve", proposal_id)
    if action == "reject":
        return _front_office("reject", proposal_id, reason)
    if action == "confirm":
        return _front_office("confirm", proposal_id)
    if action == "could-not-make":
        return _front_office("could_not_make", proposal_id, reason)
    raise HTTPException(404, "action must be approve, reject, confirm or could-not-make")


@router.post("/front-office/record")
def front_office_record(body: RecordIn, x_debug_token: Optional[str] = Header(None)):
    """A trade JR made himself (or, with proposal_id, a different trade than the proposal): its moves are made
    like a confirmed proposal's; legality is a warning."""
    require_token(x_debug_token)
    return _front_office("record", body.team_a, body.team_b, body.assets, body.proposal_id, body.note)


@router.post("/front-office/sightings/{sighting_id}/apply")
def front_office_apply(sighting_id: int, body: Optional[ApplyIn] = None, x_debug_token: Optional[str] = Header(None)):
    """Apply this trade: a trade row Madden logged that no proposal matches becomes an executed trade."""
    require_token(x_debug_token)
    return _front_office("apply_sighting", sighting_id, body.assets if body else None)


class NegotiateIn(BaseModel):
    user_team: str
    gm_team: str
    assets: list[dict[str, Any]]
    note: Optional[str] = None


class OfferIn(BaseModel):
    assets: list[dict[str, Any]]
    note: Optional[str] = None


class NoteIn(BaseModel):
    note: Optional[str] = None


def _negotiation(action, *args, llm=False):
    """Run a gm_negotiation action in one DB transaction (tuple rows); the GM's replies through Gemini
    (label gm_negotiation) when llm, else templates. FrontOfficeError -> HTTP error."""
    from psycopg.rows import tuple_row
    from engines import gm_negotiation as gn
    from engines.front_office import FrontOfficeError
    from engines.gm_llm import flush, make_llm
    client = make_llm("gm_negotiation") if llm else None
    try:
        with connect() as conn:
            cur = conn.cursor(row_factory=tuple_row)
            if not gn.installed(cur):
                raise HTTPException(409, "the negotiations migration (2026_12_21_gm_negotiations.sql) isn't applied")
            try:
                out = getattr(gn, action)(cur, *args, **({"llm": client} if llm else {}))
            except FrontOfficeError as e:
                conn.rollback()
                raise HTTPException(e.status, e.message)
            conn.commit()
        return out
    finally:
        flush(client)


@router.get("/front-office/negotiations")
def front_office_negotiations(season: Optional[int] = None):
    """Trade negotiations with the GM agents, newest first, with every turn (the GM's surplus is never shown)."""
    return _negotiation("negotiations_state", season)


@router.get("/front-office/negotiations/assets")
def front_office_negotiation_assets(user_team: str, gm_team: str):
    """The offer builder: both teams' players (tradeable or why not) and picks, the GM's direction and needs."""
    return _negotiation("team_assets", user_team.upper(), gm_team.upper())


@router.post("/front-office/negotiations")
def front_office_negotiate(body: NegotiateIn, x_debug_token: Optional[str] = Header(None)):
    """Open talks: JR's first offer from a team he controls to another team's GM; returns the GM's answer
    (ACCEPT, COUNTER, REJECT or WALK_AWAY). An accepted deal becomes an APPROVED USER proposal."""
    require_token(x_debug_token)
    return _negotiation("open_negotiation", body.user_team.upper(), body.gm_team.upper(), body.assets, body.note,
                        llm=True)


@router.post("/front-office/negotiations/{negotiation_id}/offer")
def front_office_negotiation_offer(negotiation_id: int, body: OfferIn, x_debug_token: Optional[str] = Header(None)):
    """JR's next offer in open talks; returns the GM's answer."""
    require_token(x_debug_token)
    return _negotiation("offer", negotiation_id, body.assets, body.note, llm=True)


@router.post("/front-office/negotiations/{negotiation_id}/accept")
def front_office_negotiation_accept(negotiation_id: int, x_debug_token: Optional[str] = Header(None)):
    """JR takes the GM's counter (re-judged first) -> an APPROVED USER proposal."""
    require_token(x_debug_token)
    return _negotiation("accept_counter", negotiation_id, llm=True)


@router.post("/front-office/negotiations/{negotiation_id}/close")
def front_office_negotiation_close(negotiation_id: int, body: Optional[NoteIn] = None,
                                   x_debug_token: Optional[str] = Header(None)):
    """JR ends the talks."""
    require_token(x_debug_token)
    return _negotiation("close", negotiation_id, body.note if body else None)


@router.put("/front-office/teams/{abbr}")
def front_office_team(abbr: str, body: ControlIn, x_debug_token: Optional[str] = Header(None)):
    """JR controls this team in Madden (its GM only advises) and/or its GM's suggestions are on or off."""
    require_token(x_debug_token)
    with connect() as conn:
        row = conn.execute("SELECT id FROM teams WHERE abbreviation = %s", (abbr.upper(),)).fetchone()
    if not row:
        raise HTTPException(404, f"no team {abbr}")
    return _front_office("set_team_control", row["id"], body.user_controlled, body.suggestions_on)


class LockIn(BaseModel):
    fixes: Optional[dict[str, str]] = None           # {pick: team abbreviation} owner corrections (K3)


class PickIn(BaseModel):
    prospect_key: str
    source: Optional[str] = None                     # informational: the room derives GM / OFF_SCRIPT / JR_TEAM


def _draft_room(action, *args, **kwargs):
    """Run a draft_room action in one DB transaction (tuple rows); DraftRoomError -> HTTP error."""
    from psycopg.rows import tuple_row
    from engines import draft_room as dr
    with connect() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        if not dr.installed(cur):
            raise HTTPException(409, "the scouting migration (2026_12_12_scouting.sql) isn't applied")
        try:
            out = getattr(dr, action)(cur, *args, **kwargs)
        except dr.DraftRoomError as e:
            conn.rollback()
            raise HTTPException(e.status, e.message)
        conn.commit()
    return out


@router.get("/draft-room")
def get_draft_room(team: Optional[str] = None, draft_season: Optional[int] = None):
    """Draft Room (team management step 4): the 32 Round 1 slots, the pick on the clock with the GM's
    recommendation, alternatives, rationale and Madden steps, the projected rest of Round 1, the consensus board,
    any team's board (?team=ABBR), and draft-day trade proposals."""
    from psycopg.rows import tuple_row
    from engines import draft_room as dr
    with connect() as conn:
        return dr.state(conn.cursor(row_factory=tuple_row), draft_season, team)


@router.post("/draft-room/lock-order")
def draft_room_lock(body: Optional[LockIn] = None, x_debug_token: Optional[str] = Header(None)):
    """Lock the draft order (overall_pick for every round), with JR's Round 1 owner fixes."""
    require_token(x_debug_token)
    return _draft_room("lock_order", None, (body.fixes if body else None))


@router.post("/draft-room/picks/{n}")
def draft_room_pick(n: int, body: PickIn, x_debug_token: Optional[str] = Header(None)):
    """JR made pick n in Madden: record who he took (the GM's recommendation or a different player)."""
    require_token(x_debug_token)
    from engines.gm_llm import flush, make_llm
    llm = make_llm("draft_rationale")
    try:
        return _draft_room("record", None, n, body.prospect_key, llm)
    finally:
        flush(llm)


@router.post("/draft-room/picks/{n}/undo")
def draft_room_undo(n: int, x_debug_token: Optional[str] = Header(None)):
    """Undo the last recorded pick (only the last: n must be it)."""
    require_token(x_debug_token)
    return _draft_room("undo_last", None, n)


@router.post("/draft-room/rounds/{n}/start")
def draft_room_start_round(n: int, x_debug_token: Optional[str] = Header(None)):
    """JR is about to sim round n (2-7): propose the round's draft-day trades (once a round)."""
    require_token(x_debug_token)
    from engines.gm_llm import flush, make_llm
    llm = make_llm("gm_rationale")
    try:
        return _draft_room("start_round", None, n, llm)
    finally:
        flush(llm)


@router.post("/news/clear")
def clear_news(body: ClearNewsIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if body.content_type not in ("ARTICLE", "ALL", "TWEET"):
        raise HTTPException(400, "content_type must be ARTICLE, ALL, or TWEET")
    if body.week is not None and not (1 <= body.week <= 23):
        raise HTTPException(400, "week must be 1-23")
    if body.season is not None and not (2000 <= body.season <= 2100):
        raise HTTPException(400, "season must be a valid year")

    backup_file = None
    if body.backup:
        backup_file = backup_news_tables()

    where_parts = []
    params = []
    if body.content_type == "ARTICLE":
        where_parts.append(sql.SQL("content_type = 'ARTICLE'"))
    elif body.content_type == "TWEET":
        where_parts.append(sql.SQL("content_type = 'TWEET'"))

    if body.season is not None:
        where_parts.append(sql.SQL("season = %s"))
        params.append(body.season)
    if body.week is not None:
        where_parts.append(sql.SQL("week = %s"))
        params.append(body.week)

    where_clause = (sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)) if where_parts else sql.SQL("")

    with connect() as conn:
        with conn.transaction():
            query = sql.SQL("DELETE FROM news_feed{} RETURNING id").format(where_clause)
            deleted = conn.execute(query, params).fetchall()
            deleted_count = len(deleted)

            if body.content_type in ("ARTICLE", "ALL"):
                has_sum = conn.execute("SELECT to_regclass('public.agent_season_summaries') IS NOT NULL AS ex").fetchone()["ex"]
                if has_sum:
                    sum_where = []
                    sum_params = []
                    if body.season is not None:
                        sum_where.append(sql.SQL("season = %s"))
                        sum_params.append(body.season)
                    if body.week is not None:
                        sum_where.append(sql.SQL("through_week >= %s"))
                        sum_params.append(body.week)
                    sum_clause = (sql.SQL(" WHERE ") + sql.SQL(" AND ").join(sum_where)) if sum_where else sql.SQL("")
                    conn.execute(sql.SQL("DELETE FROM agent_season_summaries{}").format(sum_clause), sum_params)

            if body.content_type in ("TWEET", "ALL"):
                has_trends = conn.execute("SELECT to_regclass('public.social_trends') IS NOT NULL AS ex").fetchone()["ex"]
                if has_trends:
                    trend_where = []
                    trend_params = []
                    if body.season is not None:
                        trend_where.append(sql.SQL("season = %s"))
                        trend_params.append(body.season)
                    if body.week is not None:
                        trend_where.append(sql.SQL("week = %s"))
                        trend_params.append(body.week)
                    trend_clause = (sql.SQL(" WHERE ") + sql.SQL(" AND ").join(trend_where)) if trend_where else sql.SQL("")
                    conn.execute(sql.SQL("DELETE FROM social_trends{}").format(trend_clause), trend_params)

            remaining = conn.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE content_type = 'ARTICLE')::int AS remaining_articles,
                    COUNT(*) FILTER (WHERE content_type = 'TWEET')::int AS remaining_tweets
                FROM news_feed
            """).fetchone()

    return {
        "deleted": deleted_count,
        "scope": {
            "season": body.season,
            "week": body.week,
            "content_type": body.content_type,
        },
        "remaining_articles": remaining["remaining_articles"] if remaining else 0,
        "remaining_tweets": remaining["remaining_tweets"] if remaining else 0,
        "backup_file": backup_file,
    }


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
    week: Optional[int] = None       # optional week override (e.g. for publish_articles)


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
        if job["status"] == "done":
            log_tail = tail(_job_path(job_id, "log"), 50)
            m_url = re.search(r"URL Route:\s*(\S+)", log_tail)
            if m_url and m_url.group(1):
                job["url_hash"] = m_url.group(1)
            m_head = re.search(r"Headline:\s*(.+)", log_tail)
            if m_head:
                job["headline"] = m_head.group(1).strip()
            m_id = re.search(r"Article ID:\s*(\d+)", log_tail)
            if m_id:
                job["article_id"] = int(m_id.group(1))
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


def job_command(kind, resume=None, week=None):
    """The weekly run for a job kind."""
    if kind == "publish_articles":
        args = [sys.executable, "-u", "-m", "newsroom.weekly_run"]
        if week is not None:
            args.extend(["--week", str(week)])
        return args
    if kind == "gm_proposals":                       # team management step 3: one engine run, no console
        args = [sys.executable, "-u", "-m", "engines.gm_trade_engine"]
        if week is not None:
            args.extend(["--week", str(week)])
        return args
    args = [sys.executable, "-u", "-m", "pipeline.weekly_capture", *JOB_KINDS[kind][1]]
    return args + (["--resume", resume] if resume else [])


@router.post("/jobs")
def start_job(body: JobIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if body.kind not in JOB_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(JOB_KINDS)}")
    if body.week is not None and not (1 <= body.week <= 23):
        raise HTTPException(400, "week must be 1-23")
    if body.resume and not re.fullmatch(r"[0-9A-Za-z_\-]+", body.resume):
        raise HTTPException(400, "resume must be a captures folder name")
    if body.resume and not os.path.isdir(os.path.join(ROOT, "captures", body.resume)):
        raise HTTPException(404, f"no captures/{body.resume}")
    busy = running_job()
    if busy:
        raise HTTPException(409, f"job {busy['id']} ({busy['kind']}) is still running")
    os.makedirs(JOBS_DIR, exist_ok=True)
    job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    args = job_command(body.kind, body.resume, week=body.week) if body.week is not None else job_command(body.kind, body.resume)
    # sh writes the exit code next to the log, so the status survives an API restart
    cmd = ["sh", "-c", '"$@"; echo $? > "$EXIT"', "sh", *args]
    log = open(_job_path(job_id, "log"), "w")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "EXIT": _job_path(job_id, "exit"), "PYTHONUNBUFFERED": "1"})
    job = {"id": job_id, "kind": body.kind, "resume": body.resume, "week": body.week, "args": args[1:], "pid": p.pid,
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


# --------------------------------------------------- custom commissioning ----
class ClassifyIn(BaseModel):
    prompt: str
    season: Optional[int] = None
    week: Optional[int] = None


class CommissionIn(BaseModel):
    prompt: str
    season: int
    week: int
    target_desk: str
    article_type_id: str
    writer_archetype: str
    team_id: Optional[int] = None
    desk_note: Optional[str] = None
    slot: Optional[str] = None


@router.post("/classify_article")
def classify_article_endpoint(body: ClassifyIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if not body.prompt.strip():
        raise HTTPException(400, "prompt cannot be empty")
    from newsroom.custom_commission import classify_prompt, load_article_catalog, load_teams
    try:
        classification = classify_prompt(body.prompt, body.season, body.week, conn_info=CONN_INFO)
    except Exception as e:
        raise HTTPException(500, f"Classification failed: {e}")

    article_types, fantasy_writers, national_personas = load_article_catalog()
    teams = load_teams(CONN_INFO)

    national_writers = [
        {"id": "BARNWELL", "name": "Bill Barnwell (Analytics / Regression)"},
        {"id": "SANDO", "name": "Mike Sando (Front Office / Coaching)"},
        {"id": "JOYNER", "name": "KC Joyner (Tape Breakdown / Efficiency)"},
        {"id": "ROSENTHAL", "name": "Gregg Rosenthal (Aesthetics / Vibes)"},
        {"id": "CHADIHA", "name": "Jeffri Chadiha (Legacy / Narrative)"},
        {"id": "LOCAL_BEAT_REPORTER", "name": "Local Beat Reporter (Franchise Beat)"},
        {"id": "SIMMONS", "name": "Bill Simmons (Pop-Culture / Column)"},
        {"id": "KIMES", "name": "Mina Kimes (Film Dissection / Scheme)"},
        {"id": "ORLOVSKY", "name": "Dan Orlovsky (QB Play / Protection)"},
        {"id": "MEL_KIPER", "name": "Mel Kiper Jr. (Draft / Prospects)"},
    ]

    fantasy_writers_list = [
        {"id": "BERRY", "name": "Matthew Berry (Love/Hate / Anecdotes)"},
        {"id": "YATES", "name": "Field Yates (Field Notes / Lists / Waivers)"},
        {"id": "FANTASY_GURU", "name": "Mu Fantasy Guru (Algorithm / Projections)"},
    ]

    return {
        "classification": classification,
        "catalog": {
            "national_types": [
                {"id": k, "name": v.get("name", k), "allowed_writers": v.get("allowed_writers", [])}
                for k, v in article_types.items() if not k.startswith("FANTASY_")
            ],
            "fantasy_types": [
                {"id": k, "name": v.get("name", k), "allowed_writers": v.get("allowed_writers", [])}
                for k, v in article_types.items() if k.startswith("FANTASY_")
            ],
            "national_writers": national_writers,
            "fantasy_writers": fantasy_writers_list,
            "teams": [
                {"id": t["id"], "city": t["city"], "name": t["name"], "abbr": t["abbreviation"]}
                for t in teams
            ],
        }
    }


@router.post("/commission_article")
def commission_article_endpoint(body: CommissionIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    if not body.prompt.strip():
        raise HTTPException(400, "prompt cannot be empty")
    busy = running_job()
    if busy:
        raise HTTPException(409, f"job {busy['id']} ({busy['kind']}) is still running")

    os.makedirs(JOBS_DIR, exist_ok=True)
    job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    args = [
        sys.executable, "-u", "-m", "newsroom.custom_commission",
        "--prompt", body.prompt,
        "--season", str(body.season),
        "--week", str(body.week),
        "--desk", body.target_desk.upper(),
        "--type", body.article_type_id,
        "--writer", body.writer_archetype,
    ]
    if body.team_id:
        args += ["--team", str(body.team_id)]
    if body.desk_note:
        args += ["--desk-note", body.desk_note]
    if body.slot:
        args += ["--slot", body.slot]

    cmd = ["sh", "-c", '"$@"; echo $? > "$EXIT"', "sh", *args]
    log = open(_job_path(job_id, "log"), "w")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "EXIT": _job_path(job_id, "exit"), "PYTHONUNBUFFERED": "1"})
    job = {
        "id": job_id,
        "kind": "custom_commission",
        "kind_label": f"Commission {body.target_desk.title()} Article",
        "prompt": body.prompt,
        "article_type_id": body.article_type_id,
        "writer": body.writer_archetype,
        "season": body.season,
        "week": body.week,
        "target_desk": body.target_desk.upper(),
        "args": args[1:],
        "pid": p.pid,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(_job_path(job_id, "json"), "w") as fh:
        json.dump(job, fh, indent=1)
    return _load_job(job_id)


# ------------------------------------------------------------- offseason ----
class OffseasonRequestIn(BaseModel):
    season: int
    manifest_path: str
    dry_run: bool = False
    only: Optional[str] = None


@router.post("/offseason/preview")
def offseason_preview_endpoint(body: OffseasonRequestIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    from newsroom.offseason_service import preview_manifest
    manifest_path = body.manifest_path.strip()
    full_path = manifest_path if os.path.isabs(manifest_path) else os.path.join(ROOT, manifest_path)
    if not os.path.exists(full_path):
        raise HTTPException(404, f"Manifest file '{manifest_path}' does not exist on disk")
    try:
        preview = preview_manifest(full_path, conn_info=CONN_INFO)
        return preview
    except Exception as e:
        raise HTTPException(400, f"Offseason manifest preview failed: {e}")


@router.post("/offseason/run")
def offseason_run_endpoint(body: OffseasonRequestIn, x_debug_token: Optional[str] = Header(None)):
    require_token(x_debug_token)
    busy = running_job()
    if busy:
        raise HTTPException(409, f"job {busy['id']} ({busy['kind']}) is still running")

    manifest_path = body.manifest_path.strip()
    full_path = manifest_path if os.path.isabs(manifest_path) else os.path.join(ROOT, manifest_path)
    if not os.path.exists(full_path):
        raise HTTPException(404, f"Manifest file '{manifest_path}' does not exist on disk")

    os.makedirs(JOBS_DIR, exist_ok=True)
    job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    args = [
        sys.executable, "-u", "-m", "newsroom.offseason_run",
        "--season", str(body.season),
        "--input", manifest_path,
    ]
    if body.dry_run:
        args.append("--dry-run")
    if body.only:
        args += ["--only", body.only]

    cmd = ["sh", "-c", '"$@"; echo $? > "$EXIT"', "sh", *args]
    log = open(_job_path(job_id, "log"), "w")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "EXIT": _job_path(job_id, "exit"), "PYTHONUNBUFFERED": "1"})
    mode_str = " (Dry Run)" if body.dry_run else ""
    only_str = f" [{body.only}]" if body.only else ""
    job = {
        "id": job_id,
        "kind": "offseason_run",
        "kind_label": f"Offseason Run Season {body.season}{only_str}{mode_str}",
        "season": body.season,
        "manifest_path": manifest_path,
        "dry_run": body.dry_run,
        "only": body.only,
        "args": args[1:],
        "pid": p.pid,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(_job_path(job_id, "json"), "w") as fh:
        json.dump(job, fh, indent=1)
    return _load_job(job_id)


