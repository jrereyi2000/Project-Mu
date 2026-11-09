"""Live postgame press conference API (`/api/franchise/{abbr}/press*`; PRESS_CONFERENCE_PLAN §2.11, §4.6c;
PROJECT_SPEC §3.3y, §4.6c).

JR's podium: the Game Center reads his last game's presser, opens it, lets him pick a reporter, answer (or say
"no comment"), end it, review and edit his answers, then approve. AI coaches' closed pressers are readable
through ``/history``. Shapes are fixed by ``Mu_Network/src/types/press.ts``: ``Conference.team`` and
``Conference.opponent`` are abbreviations, ``score`` is the subject's side's line ("24-17"), ``result`` is
W/L/T, and a LIVE conference may carry ``hands``.

The router is NOT mounted here (U7 mounts it in ``server/api.py``). Errors are FastAPI ``{"detail": string}``:
400 a bad body or an unraised hand, 403 an AI-controlled team, 404 an unknown team / conference / turn, 409 a
wrong state. Every request opens its own connection (psycopg's context commits on success); the judgments and
the beat wakeup on approve run in a background thread, never inside the request JR waits on.
"""
import os
import threading

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel

from newsroom import press_room

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}

router = APIRouter(prefix="/api/franchise")


class PressStartIn(BaseModel):
    opening: str | None = None


class PressPickIn(BaseModel):
    agent_id: str


class PressAnswerIn(BaseModel):
    turn_id: int
    answer: str | None = None
    no_comment: bool | None = None


class PressEditTurnIn(BaseModel):
    answer: str | None = None
    on_record: bool | None = None


def _connect():
    return psycopg.connect(CONN_INFO)


def team_row(cur, abbr: str) -> dict:
    """The team by abbreviation (with the project's aliases), or 404."""
    clean = (abbr or "").upper().strip()
    cur.execute("/* q:api_team */ SELECT id, abbreviation, name FROM teams WHERE abbreviation IN (%s, %s)",
                (clean, ABBR_ALIASES.get(clean, clean)))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Team '{abbr}' not found")
    return {"id": row[0], "abbr": row[1], "name": row[2]}


def user_teams():
    """The active franchise's user teams (db/franchises.py); [] when none are registered."""
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
    except Exception:  # noqa: BLE001
        return []
    return [str(t).upper() for t in ((entry or {}).get("user_teams") or [])]


def league_state(cur):
    cur.execute("/* q:api_league */ SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if not row:
        raise HTTPException(409, "league_state has no row")
    return int(row[0]), int(row[1])


def _guard(call):
    """Run a press_room lifecycle call, translating its refusals to HTTP errors."""
    try:
        return call()
    except press_room.PressRoomError as exc:
        raise HTTPException(getattr(exc, "status", 409), str(exc)) from exc


def _owned_conference(cur, abbr: str, conference_id: int):
    """The team row and the conference row; 404 when the conference is missing or belongs to another team."""
    team = team_row(cur, abbr)
    row = press_room.get_conference_row(cur, conference_id)
    if row is None or int(row.get("team_id") or 0) != int(team["id"]):
        raise HTTPException(404, f"Press conference {conference_id} not found for {team['abbr']}")
    return team, row


def _start_close_and_wakeup(conference_id: int, team: dict, season: int, week: int):
    """Run the judgments/memories/events, then the team beat wakeup, in a daemon thread. Returns the flags."""
    def work():
        try:
            press_room.close_effects(CONN_INFO, conference_id, None)
        except Exception:  # noqa: BLE001 - the presser is already CLOSED; effects can retry on the next run
            pass
        try:
            from server.api import maybe_start_team_wakeup
            # maybe_start_team_wakeup reads dict rows (it is written for server.api's dict_row pool); a default
            # tuple cursor raises TypeError here inside the swallowed except, so the wakeup never started.
            with psycopg.connect(CONN_INFO, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    maybe_start_team_wakeup(cur, team["id"], season)
                conn.commit()
        except Exception:  # noqa: BLE001 - U7 wires the wakeup; U6 alone simply skips it
            pass

    thread = threading.Thread(target=work, name=f"press-effects-{conference_id}", daemon=True)
    thread.start()
    return True, True


# --------------------------------------------------------------------------------------------- endpoints
@router.get("/{abbr}/press")
def get_press(abbr: str):
    """The team's presser for its last game, plus the opponent's presser for the same game."""
    with _connect() as conn, conn.cursor() as cur:
        team = team_row(cur, abbr)
        season, _week = league_state(cur)
        cur.execute("/* q:api_last_game */ SELECT id, home_team_id, away_team_id FROM games "
                    "WHERE season = %s AND status = 'COMPLETED' AND (home_team_id = %s OR away_team_id = %s) "
                    "ORDER BY week DESC, id DESC LIMIT 1", (season, team["id"], team["id"]))
        game = cur.fetchone()
        if not game:
            return {"conference": None, "opponent_presser": None}
        game_id, home_id, away_id = game
        cur.execute("/* q:api_team_game_conference */ SELECT id FROM press_conferences "
                    "WHERE game_id = %s AND team_id = %s ORDER BY id LIMIT 1", (game_id, team["id"]))
        row = cur.fetchone()
        conference = press_room.build_conference(cur, row[0]) if row else None
        opponent_id = away_id if home_id == team["id"] else home_id
        cur.execute("/* q:api_team_game_conference */ SELECT id, status FROM press_conferences "
                    "WHERE game_id = %s AND team_id = %s ORDER BY id LIMIT 1", (game_id, opponent_id))
        other = cur.fetchone()
        return {"conference": conference,
                "opponent_presser": {"conference_id": other[0], "status": other[1]} if other else None}


@router.post("/{abbr}/press/{conference_id}/start")
def start_press(abbr: str, conference_id: int, body: PressStartIn | None = None):
    with _connect() as conn, conn.cursor() as cur:
        _owned_conference(cur, abbr, conference_id)
        opening = body.opening if body else None
        return _guard(lambda: press_room.start_user(cur, conference_id, opening))


@router.post("/{abbr}/press/{conference_id}/pick")
def pick_press(abbr: str, conference_id: int, body: PressPickIn):
    with _connect() as conn, conn.cursor() as cur:
        _owned_conference(cur, abbr, conference_id)
        question = _guard(lambda: press_room.pick_user(cur, conference_id, body.agent_id))
        return {"question": question}


@router.post("/{abbr}/press/{conference_id}/answer")
def answer_press(abbr: str, conference_id: int, body: PressAnswerIn):
    if (body.answer is None) == (not body.no_comment):
        raise HTTPException(400, "Send exactly one of answer or no_comment: true")
    with _connect() as conn, conn.cursor() as cur:
        _owned_conference(cur, abbr, conference_id)
        return _guard(lambda: press_room.answer_user(cur, conference_id, body.turn_id, body.answer,
                                                     bool(body.no_comment)))


@router.post("/{abbr}/press/{conference_id}/end")
def end_press(abbr: str, conference_id: int):
    with _connect() as conn, conn.cursor() as cur:
        _owned_conference(cur, abbr, conference_id)
        return _guard(lambda: press_room.end_user(cur, conference_id))


@router.put("/{abbr}/press/{conference_id}/turns/{turn_id}")
def edit_press_turn(abbr: str, conference_id: int, turn_id: int, body: PressEditTurnIn):
    with _connect() as conn, conn.cursor() as cur:
        _owned_conference(cur, abbr, conference_id)
        return _guard(lambda: press_room.edit_user(cur, conference_id, turn_id, body.answer, body.on_record))


@router.post("/{abbr}/press/{conference_id}/approve")
def approve_press(abbr: str, conference_id: int):
    with _connect() as conn, conn.cursor() as cur:
        team, row = _owned_conference(cur, abbr, conference_id)
        conference = _guard(lambda: press_room.approve(cur, conference_id))
        effects_started, wakeup_started = _start_close_and_wakeup(
            conference_id, team, int(row.get("season") or 0), int(row.get("week") or 0))
        return {"conference": conference, "effects_started": effects_started,
                "wakeup_started": wakeup_started}


@router.get("/{abbr}/press/history")
def press_history(abbr: str, season: int | None = None, week: int | None = None):
    """Any team's CLOSED pressers for a week (defaults to the league's current season and week)."""
    with _connect() as conn, conn.cursor() as cur:
        team_row(cur, abbr)
        current_season, current_week = league_state(cur)
        s = int(season if season is not None else current_season)
        w = int(week if week is not None else current_week)
        ids = press_room.history_ids(cur, s, w)
        return {"conferences": [press_room.build_conference(cur, cid) for cid in ids]}
