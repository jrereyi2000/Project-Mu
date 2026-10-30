"""Franchise API for the #franchise page (/api/franchise; docs/in_progress/V2_CORE_BUILD_PLAN.md §2.7, §2.7a).

JR runs his team from Mu_Network: he reads the depth chart, roster, cap sheet and medical report, previews and queues
roster moves, makes free-agent offers and extension proposals, evaluates trades, and launches the next game. Nothing
here writes the save or calls save_writer: every move JR makes is queued as a `franchise_pending_moves` row (status
PENDING) that the league office (pipeline/league_office.py) writes at its next run and marks WRITTEN or REFUSED.
Offers and proposals are stored in `contract_offers` (from_user true).

Conventions. Money in whole dollars; null = not recorded (shown as "—", never $0); errors are FastAPI
`{"detail": string}` (400 bad body, 403 not one of the active franchise's user teams, 404 unknown team / player,
409 an illegal or refused action). `season` / `week` are league_state's. The league year (`league_year`, the cap
season) is season + 1 from week 24 on (the offseason, transactions_engine.target_season), else season; contracts and
cap hits are read at the league year. Write endpoints (PUT depth-chart, moves, offers, propose, game/launch) are
refused with 403 for a team that isn't in the active franchise's `user_teams` (db/franchises.py) when that list is
set; an empty list allows every team.

Shared shapes:
  QueuedMove   {move_id, kind, status: PENDING|WRITTEN|REFUSED|CANCELLED, created_at: ISO, player_id: int|null}
  MoveKind     RELEASE | PS_PROMOTE | PS_DEMOTE | IR_PLACE | IR_ACTIVATE
  Contract     {length, years_left, through, total_value, guaranteed, contract_type}
  Utility      {utility, financial, opportunity, contention, scheme, persona}   (free_agency_engine.offer_utility)

§2.7a endpoints (the exact contract of Mu_Network/src/types/franchiseRoster.ts):
  GET  /{abbr}/depth-chart  -> {team, season, week, source: MADDEN|MU_COACH|JR, pending: QueuedMove|null,
                                slots: {<35 slots>: [{player_id, name, position, overall, injured}] best first, 0-6}}
       The chart shown: JR's newest PENDING DEPTH_CHART move (source JR, pending set), else the newest stored chart of
       the season up to this week (MADDEN, then JR, then MU_COACH), else the staff's chart (MU_COACH, not stored).
  PUT  /{abbr}/depth-chart  {slots: {<slot>: [player_id, ...<=6]}} (all 35) -> QueuedMove (DEPTH_CHART). Earlier
       PENDING DEPTH_CHART moves of the team become CANCELLED. Players must be on the team.
  POST /{abbr}/depth-chart/staff {} -> the depth-chart shape, source MU_COACH (depth_chart_engine.staff_depth_chart
       over the active roster, newest player_attributes ratings, the OC/DC schemes and this week's injuries);
       nothing is stored.
  GET  /{abbr}/roster -> {team, season, week, limits: {active: 65, practice_squad: 12}, active, practice_squad,
       injured_reserve}, each [{player_id, name, position, age, overall, injury, weeks_out, contract: Contract|null
       (null on the practice squad), cap_hit, ps_eligible, ir_eligible, pending_move: MoveKind|null}].
       active = an ACTIVE contract and not on IR; practice_squad = on the team without a contract (the save's squad
       players have none); injured_reserve = injuries.is_ir in the team's newest injury report.
  GET  /{abbr}/cap?years=5 -> {team, season (the league year), years: [{season, cap, committed, dead_money,
       rookie_reserve, room = cap - committed - dead_money - rookie_reserve, estimated}]}.
       cap: team_cap_seasons (adjusted cap, else cap), else projected with contracts_engine.next_cap (estimated);
       committed: the cap hits of the ACTIVE contracts in that year; dead_money: team_cap_seasons.cap_penalty where a
       row exists, else 0; rookie_reserve: 0 for the season in progress, next_rookie_reserve for next season, else
       the league-average slotted class (contracts_engine.rookie_contract, picks 1-224 / 32; estimated).
  POST /{abbr}/moves/preview {kind: MoveKind, payload: {player_id, post_june1?}} -> {kind, player_id, name, cap_hit,
       cap_savings (cap_hit - dead_money, may be negative), dead_money, dead_money_next_year (post-June 1 split,
       contracts_engine._dead_split), cap_room_before, cap_room_after, active_count_after,
       practice_squad_count_after, legal, reasons: [str]}.
       RELEASE: dead money by the contract's proration (pre- or post-June 1); illegal when it would leave the team
       over the cap and costs room. PS_DEMOTE: releases the contract (same dead money), needs a squad slot (12), PS
       eligibility (<= 2 accrued seasons, else one of 4 veteran slots) and a healthy player. PS_PROMOTE: a 1-year
       league-minimum deal for his accrued seasons (cap_savings = -that salary), needs an active spot (65) and the cap
       room. IR_PLACE: an injured active player out >= IR_MIN_WEEKS weeks; no cap change. IR_ACTIVATE: an IR player
       who is healthy and an active spot. A player with a move already PENDING is illegal.
  POST /{abbr}/moves  same body -> QueuedMove; 409 {detail: the reasons} when illegal. The payload stored is
       {player_id, post_june1, preview} (+ contract for PS_PROMOTE).
  GET  /{abbr}/medical -> {team, season, week, players: [{player_id, name, position, overall, injury, weeks_out
       (injuries.mu_weeks_out, else weeks_remaining, 0 when healthy), status: OUT|DOUBTFUL|QUESTIONABLE|HEALTHY,
       on_ir, ir_eligible, pending_move}]}. status: OUT on IR or 2+ weeks, DOUBTFUL 1 week, QUESTIONABLE injured with
       0 weeks, else HEALTHY. Injured players first (most weeks out first), then by overall.

Other endpoints (U15's Free Agency / Contract Room / Trade Machine shapes and U17's overview, game center
and launch; these are the contract):
  GET  /{abbr}/overview -> {team: {id, abbr, name}, season, week, league_year, user_controlled: bool,
       record: {wins, losses, ties}, cap: {cap, committed, dead_money, rookie_reserve, room, estimated} (league year),
       roster_counts: {active, practice_squad, injured_reserve, limits: {active, practice_squad}},
       next_game: NextGame|null, pending_moves: [QueuedMove + {payload}]}
       NextGame = {game_id, season, week, game_time, game_type, home: bool, opponent: {id, abbr, name}}
  GET  /free-agents?position&min_ovr&max_age&team&limit=100 -> {season, week, in_season: bool (week < 24),
       players: [{player_id, name, position, age|null, overall|null, dev_trait|null, scheme_fit: 0..1|null (against
       ?team's OC/DC schemes: 1.0 when his archetype suits the side's scheme, 0.5 otherwise; null without team),
       market_worth|null (per year: trade_value.worth W(ovr, group); null without a group or market fit),
       asking: {years, total_value, guaranteed}|null (transactions_engine.asking_price per year x
       free_agency_engine.ideal_years, guaranteed at the engine's target share: 35%, veterans 60%)}]}, best first.
  NegotiationResponse (offers and proposals) = {offer_id, status: ACCEPT|REJECT|COUNTER, utility, bar: float|null
       (free_agency_engine.accept_bar), parts: {financial, opportunity, contention, scheme, persona} (offer_utility's
       raw 0..1 scores), counter: {years, total_value, bonus, guaranteed}|null, note: str|null}.
  POST /{abbr}/offers {player_id, years (1-5), total_value, bonus=0, guaranteed=0} -> NegotiationResponse. Scored
       with offer_utility (projected role from the roster, OC/DC scheme fit, team record and direction) and
       acceptance; stored in contract_offers (kind FREE_AGENT, from_user true, status ACCEPTED|REJECTED|COUNTERED,
       counter JSON). Not a signing: no move is queued. 409 when he isn't a free agent, the first-year cap hit doesn't
       fit the cap room, or the active roster is full.
  GET  /{abbr}/contracts/expiring -> {team, season, week, players: [{player_id, name, position, overall|null,
       age|null, contract: Contract|null, cap_hit|null, eligible, eligible_reason (contracts_engine.extension_eligible),
       options: {tag_value?, transition_tag_value? (contracts_engine.tag_value FRANCHISE_NONEXCL / TRANSITION, for a
       deal that expires at the league year), fifth_year_option?: {tier: 1..4, salary} (a 1st-round ROOKIE deal in
       contract year 3 or 4)}}]}: every active contract that expires at the league year or may be extended; expiring
       first, then by overall.
  POST /{abbr}/contracts/{player_id}/propose {years, total_value, bonus=0, guaranteed=0} -> NegotiationResponse.
       409 when not extension-eligible. Stored in contract_offers (RESIGN for an expiring deal, else EXTENSION,
       from_user true); no move is queued.
  POST /{abbr}/contracts/{player_id}/restructure-preview {amount} -> {player_id, amount, max_amount (90% of this
       league year's base), cap_hit_before, cap_hit_after, cap_savings_now, added_dead_money_future (bonus proration
       moved into later seasons: dead money a later release would carry), by_year: [{season, cap_hit_before,
       cap_hit_after}] (league year to the end of the deal), legal, reasons: [str]} (contracts_engine.restructure;
       legal false with its refusal as the reason). Nothing stored.
  GET  /{abbr}/picks?seasons=3 -> {team, picks: [{season, round, original_team (abbr), overall_pick: int|null}]}: the
       unused draft_picks the team owns in the next N drafts (1-5).
  POST /{abbr}/trades/evaluate {other_team: abbr, send: {players: [id], picks: [{season, round, original_team}]},
       receive: {same}} -> {surplus_us, surplus_them (dollars, gm_negotiation surpluses), verdict:
       ACCEPT|REJECT|COUNTER (gm_negotiation.respond as a fresh negotiation, no hidden reserve; WALK_AWAY is sent as
       REJECT; ACCEPT turns REJECT when a side fails the cap check), counter: {send, receive}|null (from {abbr}'s
       side), cap_legal_us, cap_legal_them, cap_room_after_us|null, cap_room_after_them|null (contracts_engine: cap
       hits at the league year in and out, the sender's remaining bonus proration as dead money; legal when the room
       stays >= 0 or doesn't shrink), reasons: [str] (failed engine checks and cap), dialogue: str|null (the GM's
       template line)}. Nothing stored (trades are made on the Front Office Negotiate card).
  GET  /{abbr}/game-center -> {team, season, week, record, next_game: NextGame|null, opponent: {id, abbr, name,
       record, ratings}|null, ratings: {team_ovr, off_ovr, def_ovr, pass_off_rank, rush_off_rank, pass_rush_rank,
       coverage_rank} (team_seasons)|null, ranks: {team: Ranks|null, opponent: Ranks|null}, matchups: [{unit, team_stat,
       team_value, team_rank, opponent_stat, opponent_value, opponent_rank, edge: team|opponent|even}]}.
       Ranks = {games, <stat>: {value (per game), rank (1 = best of the teams with games)}} for points_for,
       points_against, yards_for, yards_against, pass_yards_for, pass_yards_against, rush_yards_for,
       rush_yards_against, turnovers, takeaways (team_stats over the season's completed regular-season games).
       Matchups: pass offense v pass defense, rush offense v rush defense, scoring offense v scoring defense, each way.
  POST /{abbr}/game/launch {dry_run=false, season?, week?} -> pipeline/game_launch.launch: {pack, command, started,
       refused: reason|null, game}. 200 with `refused` set when it refuses (pending moves, Madden running, no game).
"""
import json
import os
from typing import Dict, List, Optional

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engines import contracts_engine as ce
from engines import depth_chart_engine as dce
from engines import free_agency_engine as fae
from engines import transactions_engine as te
from engines.gm_direction import SLOTS, position_group

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
MOVE_KINDS = ("RELEASE", "PS_PROMOTE", "PS_DEMOTE", "IR_PLACE", "IR_ACTIVATE")
IR_MIN_WEEKS = 4                 # NFL injured reserve: out at least four games
MAX_OFFER_YEARS = 5
CAP_SEED = "franchise_api"       # contracts_engine.next_cap seed for projected caps
FREE_AGENT_LIMIT = 100
SITUATIONAL_WITHIN = 4           # free_agency_engine's role rule: within 4 OVR of the weakest starter
POSITION_SLOT = {"OT": "LT", "G": "LG", "OG": "LG", "DE": "LE", "EDGE": "LE", "OLB": "LOLB", "ILB": "MLB", "LB": "MLB",
                 "S": "FS", "RB": "HB"}
RANK_STATS = (("points_for", True), ("points_against", False), ("yards_for", True), ("yards_against", False),
              ("pass_yards_for", True), ("pass_yards_against", False), ("rush_yards_for", True),
              ("rush_yards_against", False), ("turnovers", False), ("takeaways", True))
MATCHUPS = (("Pass offense v pass defense", "pass_yards_for", "pass_yards_against"),
            ("Rush offense v rush defense", "rush_yards_for", "rush_yards_against"),
            ("Scoring offense v scoring defense", "points_for", "points_against"))

router = APIRouter(prefix="/api/franchise")


# ------------------------------------------------------------------ bodies ----
class DepthChartIn(BaseModel):
    slots: Dict[str, List[int]]


class MovePayload(BaseModel):
    player_id: int
    post_june1: bool = False


class MoveIn(BaseModel):
    kind: str
    payload: MovePayload


class OfferIn(BaseModel):
    player_id: int
    years: int
    total_value: int
    bonus: int = 0
    guaranteed: int = 0


class ProposeIn(BaseModel):
    years: int
    total_value: int
    bonus: int = 0
    guaranteed: int = 0


class RestructureIn(BaseModel):
    amount: int


class PickIn(BaseModel):
    season: int
    round: int
    original_team: str


class TradeSide(BaseModel):
    players: List[int] = []
    picks: List[PickIn] = []


class TradeIn(BaseModel):
    other_team: str
    send: TradeSide = TradeSide()
    receive: TradeSide = TradeSide()


class LaunchIn(BaseModel):
    dry_run: bool = False
    season: Optional[int] = None
    week: Optional[int] = None


# ------------------------------------------------------------- connection ----
def _connect():
    return psycopg.connect(CONN_INFO)


def user_teams() -> List[str]:
    """The active franchise's user teams (db/franchises.py); [] when none are registered."""
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
    except Exception:
        return []
    return [t.upper() for t in ((entry or {}).get("user_teams") or [])]


def require_user_team(team: dict):
    teams = user_teams()
    if teams and team["abbr"].upper() not in teams:
        raise HTTPException(403, f"{team['abbr']} isn't one of your teams ({', '.join(teams)})")


# ---------------------------------------------------------------- readers ----
def _regclass(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def team_row(cur, abbr: str) -> dict:
    clean = (abbr or "").upper().strip()
    cur.execute("SELECT id, abbreviation, name FROM teams WHERE abbreviation IN (%s, %s)",
                (clean, ABBR_ALIASES.get(clean, clean)))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Team '{abbr}' not found")
    return {"id": row[0], "abbr": row[1], "name": row[2]}


def league_state(cur) -> tuple:
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if not row:
        raise HTTPException(409, "league_state has no row")
    return int(row[0]), int(row[1])


CONTRACT_KEYS = ("id", "player_id", "team_id", "season", "length", "years_remaining", "total_value", "bonus", "status",
                 "contract_type", "guaranteed", "signed_season", "signed_week", "last_restructure_season",
                 "drafted_round")


def contract_rows(cur, season: int, player_ids) -> dict:
    """{player_id: ACTIVE player_contracts row with years {calendar year: cap hit}} (transactions_engine's shape)."""
    ids = list(player_ids)
    if not ids:
        return {}
    cur.execute(f"""SELECT {', '.join('c.' + k for k in CONTRACT_KEYS)} FROM player_contracts c
                    WHERE c.season = %s AND c.status = 'ACTIVE' AND c.player_id = ANY(%s)""", (season, ids))
    rows = {}
    for r in cur.fetchall():
        d = dict(zip(CONTRACT_KEYS, r), years={})
        rows[d["player_id"]] = d
    by_id = {d["id"]: d for d in rows.values()}
    if by_id:
        cur.execute("SELECT contract_id, year, cap_hit FROM player_contract_years WHERE contract_id = ANY(%s)",
                    (list(by_id),))
        for cid, year, hit in cur.fetchall():
            if cid in by_id:
                by_id[cid]["years"][year] = hit
    return rows


def injury_rows(cur, team_id: int, season: int, week: int) -> dict:
    """{player_id: {injury, weeks_out, on_ir}} from the team's newest injury report of the season up to `week`."""
    cur.execute("""SELECT player_id, injury_type, weeks_remaining, mu_weeks_out, is_ir FROM injuries
                   WHERE team_id = %s AND season = %s AND player_id IS NOT NULL
                     AND week = (SELECT max(week) FROM injuries WHERE team_id = %s AND season = %s AND week <= %s)""",
                (team_id, season, team_id, season, week))
    out = {}
    for pid, kind, remaining, mu_weeks, is_ir in cur.fetchall():
        weeks = mu_weeks if mu_weeks is not None else (remaining or 0)
        out[pid] = {"injury": kind, "weeks_out": int(weeks or 0), "on_ir": bool(is_ir)}
    return out


def roster_players(cur, team_id: int, season: int, week: int) -> list:
    """Every player on the team this season (not released): {player_id, name, position, age, overall, yoe,
    archetype, dev_trait, contract_row (ACTIVE contract or None), injury: {injury, weeks_out, on_ir} | None}."""
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.position, ps.age, ps.overall_rating,
                          ps.years_of_experience, ps.archetype, ps.dev_trait
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.team_id = %s AND ps.released_week IS NULL
                     AND COALESCE(ps.position, '') <> 'DEF'""", (season, team_id))
    keys = ("player_id", "name", "position", "age", "overall", "yoe", "archetype", "dev_trait")
    players = [dict(zip(keys, r)) for r in cur.fetchall()]
    contracts = contract_rows(cur, season, [p["player_id"] for p in players])
    injuries = injury_rows(cur, team_id, season, week)
    for p in players:
        p["contract_row"] = contracts.get(p["player_id"])
        p["injury"] = injuries.get(p["player_id"])
    return players


MOVE_COLS = "id, kind, status, created_at, payload"


def pending_moves(cur, team_id: int) -> list:
    """The team's PENDING franchise_pending_moves rows: {move_id, kind, status, created_at, payload}."""
    cur.execute(f"SELECT {MOVE_COLS} FROM franchise_pending_moves WHERE team_id = %s AND status = 'PENDING' "
                "ORDER BY id", (team_id,))
    return [_move_row(r) for r in cur.fetchall()]


def _move_row(r) -> dict:
    payload = r[4]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {"move_id": r[0], "kind": r[1], "status": r[2], "created_at": r[3], "payload": payload or {}}


def insert_move(cur, team_id: int, kind: str, payload: dict) -> dict:
    """Queues one move (status PENDING); the league office writes it. Returns the QueuedMove."""
    cur.execute(f"""INSERT INTO franchise_pending_moves (team_id, kind, payload, status)
                    VALUES (%s, %s, %s::jsonb, 'PENDING') RETURNING {MOVE_COLS}""",
                (team_id, kind, json.dumps(payload)))
    return queued_move(_move_row(cur.fetchone()))


def cancel_pending(cur, team_id: int, kind: str):
    cur.execute("""UPDATE franchise_pending_moves SET status = 'CANCELLED', reason = 'replaced by a newer move'
                   WHERE team_id = %s AND kind = %s AND status = 'PENDING'""", (team_id, kind))


def cap_rows(cur, team_id: int, seasons) -> dict:
    """{season: {cap, cap_penalty, next_rookie_reserve}} from team_cap_seasons."""
    if not _regclass(cur, "team_cap_seasons"):
        return {}
    cur.execute("""SELECT season, COALESCE(adjusted_cap, cap), cap_penalty, next_rookie_reserve FROM team_cap_seasons
                   WHERE team_id = %s AND season = ANY(%s)""", (team_id, list(seasons)))
    return {r[0]: {"cap": r[1], "cap_penalty": r[2], "next_rookie_reserve": r[3]} for r in cur.fetchall()}


def stored_depth_chart(cur, team_id: int, season: int, week: int) -> tuple:
    """(source, {slot: [player_id]}) for the newest stored chart of the season up to `week` (MADDEN, then JR, then
    MU_COACH at that week), or (None, {})."""
    cur.execute("""SELECT week, source FROM depth_charts WHERE team_id = %s AND season = %s AND week <= %s
                   GROUP BY week, source""", (team_id, season, week))
    rows = cur.fetchall()
    if not rows:
        return None, {}
    order = {s: i for i, s in enumerate(("MADDEN", "JR", "MU_COACH"))}
    wk, source = sorted(rows, key=lambda r: (-r[0], order.get(r[1], 9)))[0]
    return source, dce.load(cur, team_id, season, wk, source)


def team_scheme(cur, team_id: int, season: int) -> dict:
    """{"offense": OC scheme, "defense": DC scheme} from coordinator_tenures; {} when unknown."""
    if not _regclass(cur, "coordinator_tenures"):
        return {}
    cur.execute("""SELECT ct.role, c.scheme FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.team_id = %s AND ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
                   ORDER BY ct.start_season, ct.id""", (team_id, season, season))
    out = {}
    for role, scheme in cur.fetchall():
        if scheme:
            out["offense" if role == "OC" else "defense"] = scheme
    return out


def player_ratings(cur, player_ids, season: int) -> dict:
    """{player_id: {Rating: int}} from each player's newest player_attributes row up to `season`."""
    ids = list(player_ids)
    if not ids or not _regclass(cur, "player_attributes"):
        return {}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, ratings FROM player_attributes
                   WHERE player_id = ANY(%s) AND season <= %s ORDER BY player_id, season DESC, id DESC""",
                (ids, season))
    out = {}
    for pid, ratings in cur.fetchall():
        out[pid] = json.loads(ratings) if isinstance(ratings, str) else (ratings or {})
    return out


def free_agent_rows(cur, season: int, position=None, min_ovr=None, max_age=None, limit=FREE_AGENT_LIMIT) -> list:
    """Free agents (a player_seasons row this season with no team, or released): best overall first."""
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.position, ps.age, ps.overall_rating,
                          ps.years_of_experience, ps.archetype, ps.dev_trait
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND (ps.team_id IS NULL OR ps.released_week IS NOT NULL)
                     AND COALESCE(ps.position, '') <> 'DEF'
                     AND (%s::text IS NULL OR ps.position = %s)
                     AND (%s::int IS NULL OR ps.overall_rating >= %s)
                     AND (%s::int IS NULL OR ps.age <= %s)
                   ORDER BY ps.overall_rating DESC NULLS LAST, ps.player_id LIMIT %s""",
                (season, position, position, min_ovr, min_ovr, max_age, max_age, limit))
    keys = ("player_id", "name", "position", "age", "overall", "yoe", "archetype", "dev_trait")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def market_fit(cur, season: int):
    """trade_value's market fit (W(ovr, group)), or None when it can't be fitted."""
    try:
        from engines import trade_value as tv
        return tv.fits_from_db(cur, season)["market"]
    except Exception:
        return None


def team_context(cur, team_id: int, season: int) -> dict:
    """{wins, losses, popularity_index, direction} for offer_utility's contention score."""
    cur.execute("SELECT wins, losses, popularity_index FROM team_seasons WHERE team_id = %s AND season = %s",
                (team_id, season))
    r = cur.fetchone()
    out = {"wins": (r[0] if r else 0) or 0, "losses": (r[1] if r else 0) or 0,
           "popularity_index": r[2] if r else None, "direction": None}
    if _regclass(cur, "gm_team_seasons"):
        cur.execute("SELECT direction FROM gm_team_seasons WHERE team_id = %s AND season = %s", (team_id, season))
        d = cur.fetchone()
        out["direction"] = d[0] if d else None
    return out


def free_agent(cur, season: int, player_id: int) -> Optional[dict]:
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.position, ps.age, ps.overall_rating,
                          ps.years_of_experience, ps.archetype, ps.dev_trait,
                          (ps.team_id IS NULL OR ps.released_week IS NOT NULL)
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.player_id = %s""", (season, player_id))
    r = cur.fetchone()
    if not r:
        return None
    keys = ("player_id", "name", "position", "age", "overall", "yoe", "archetype", "dev_trait", "is_free_agent")
    return dict(zip(keys, r))


def insert_offer(cur, season, stage, kind, player_id, team_id, years, total, bonus, guaranteed, utility, status,
                 counter, seed) -> int:
    cur.execute("""INSERT INTO contract_offers (season, stage, wave, kind, player_id, team_id, from_user, years,
                                                total_value, bonus, guaranteed, utility, status, counter, seed)
                   VALUES (%s, %s, NULL, %s, %s, %s, true, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id""",
                (season, stage, kind, player_id, team_id, years, total, bonus, guaranteed, utility, status,
                 json.dumps(counter) if counter else None, seed))
    return cur.fetchone()[0]


def season_games(cur, season: int) -> list:
    """[(game_id, week, home_id, away_id, home_score, away_score, status, game_time, game_type)] of the season."""
    cur.execute("""SELECT id, week, home_team_id, away_team_id, home_score, away_score, status, game_time, game_type
                   FROM games WHERE season = %s AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
                   ORDER BY week, id""", (season,))
    return cur.fetchall()


def team_names(cur) -> dict:
    cur.execute("SELECT id, abbreviation, name FROM teams")
    return {r[0]: {"id": r[0], "abbr": r[1], "name": r[2]} for r in cur.fetchall()}


def rank_rows(cur, season: int) -> list:
    """Per-team season averages over completed regular-season games with box scores:
    [(team_id, games, pts, pts_allowed, yds, yds_allowed, pass, pass_allowed, rush, rush_allowed, giveaways,
    takeaways)]."""
    cur.execute("""SELECT t.id, count(*), avg(ts.score), avg(opp.score), avg(ts.total_offense), avg(opp.total_offense),
                          avg(ts.passing_yards), avg(opp.passing_yards), avg(ts.rushing_yards), avg(opp.rushing_yards),
                          avg(ts.turnovers), avg(opp.turnovers)
                   FROM games g
                   JOIN teams t ON t.id IN (g.home_team_id, g.away_team_id)
                   JOIN teams o ON o.id = CASE WHEN t.id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END
                   JOIN team_stats ts ON ts.game_id = g.id AND ts.team_name = t.name
                   JOIN team_stats opp ON opp.game_id = g.id AND opp.team_name = o.name
                   WHERE g.season = %s AND g.status = 'COMPLETED' AND g.game_type = 'REGULAR_SEASON'
                   GROUP BY t.id""", (season,))
    return cur.fetchall()


def team_ratings(cur, team_id: int, season: int) -> Optional[dict]:
    cur.execute("""SELECT team_ovr, off_ovr, def_ovr, pass_off_rank, rush_off_rank, pass_rush_rank, coverage_rank
                   FROM team_seasons WHERE team_id = %s AND season = %s""", (team_id, season))
    r = cur.fetchone()
    keys = ("team_ovr", "off_ovr", "def_ovr", "pass_off_rank", "rush_off_rank", "pass_rush_rank", "coverage_rank")
    return dict(zip(keys, r)) if r else None


def trade_books(cur):
    """(books, valuer) from gm_negotiation (raises FrontOfficeError when the window is closed)."""
    from engines import gm_negotiation as gn
    return gn._books(cur)


def negotiate_trade(cur, b, valuer, user_id: int, other_id: int, assets: list) -> dict:
    """gm_negotiation.respond on JR's package as a fresh negotiation (no hidden reserve, the GM's full patience, no
    counters made): {engine_assets, response: {action, judge, counter?, reasons}, dialogue: the GM's template line}."""
    from engines import gm_negotiation as gn
    eng = gn._engine_assets(cur, b, user_id, other_id, assets)
    deal = {"a": user_id, "b": other_id, "assets": eng}
    gm = (b.get("gm") or {}).get(other_id) or {}
    r = gn.respond(b, valuer, deal, user_id, other_id, 0, gn.patience_for(gm.get("aggressiveness", 0.5)), 0)
    c = r.get("counter")
    try:
        dialogue = gn.template_message(b, r["action"], user_id, other_id, eng, c["assets"] if c else None,
                                       r["judge"]["mood"], r["reasons"])
    except Exception:
        dialogue = None
    return {"engine_assets": eng, "response": r, "dialogue": dialogue}


# --------------------------------------------------------------- shaping ----
def league_year(season: int, week: int) -> int:
    return te.target_season(season, week)


def stage_for(week: int) -> str:
    """The league office stage name for a league_state week (contracts_engine.OFFSEASON_STAGE_WEEKS)."""
    if week <= ce.REGULAR_SEASON_WEEKS:
        return "REGULAR_WEEK"
    if week < ce.OFFSEASON_STAGE_WEEKS["OFFSEASON_START"]:
        return "PLAYOFF_WEEK"
    past = [(w, s) for s, w in ce.OFFSEASON_STAGE_WEEKS.items() if w <= week]
    return max(past)[1]


def queued_move(m: dict) -> dict:
    created = m["created_at"]
    return {"move_id": m["move_id"], "kind": m["kind"], "status": m["status"],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            "player_id": None if m["kind"] == "DEPTH_CHART" else (m.get("payload") or {}).get("player_id")}


def pending_by_player(moves: list) -> dict:
    """{player_id: MoveKind} of the PENDING roster moves (the newest wins)."""
    out = {}
    for m in moves:
        pid = (m.get("payload") or {}).get("player_id")
        if m["status"] == "PENDING" and m["kind"] in MOVE_KINDS and pid is not None:
            out[pid] = m["kind"]
    return out


def with_contract(p: dict, T: int) -> dict:
    """Adds contract (contracts_engine dict, current_year at T), start, year (index at T or None), cap_hit."""
    row = p.get("contract_row")
    if not row:
        return dict(p, contract=None, start=None, year=None, cap_hit=None)
    c, start = te.contract_from_db(row)
    y = T - start
    c = dict(c, current_year=max(0, min(y, c["length"] - 1)))
    inside = 0 <= y < c["length"]
    return dict(p, contract=c, start=start, year=y if inside else None, cap_hit=ce.cap_hit(c, y) if inside else 0)


def weeks_out(p: dict) -> int:
    return int((p.get("injury") or {}).get("weeks_out") or 0)


def is_injured(p: dict) -> bool:
    inj = p.get("injury")
    return bool(inj and (inj.get("injury") or inj.get("weeks_out") or inj.get("on_ir")))


def on_ir(p: dict) -> bool:
    return bool((p.get("injury") or {}).get("on_ir"))


def roster_status(p: dict) -> str:
    """ACTIVE | IR | PS."""
    if not p.get("contract_row"):
        return "PS"
    return "IR" if on_ir(p) else "ACTIVE"


def accrued(p: dict, season: int, T: int) -> int:
    return max(0, (p.get("yoe") or 0) + T - season)


def split_roster(players: list) -> dict:
    out = {"ACTIVE": [], "PS": [], "IR": []}
    for p in players:
        out[roster_status(p)].append(p)
    return out


def ps_veterans(ps: list, season: int, T: int) -> int:
    return sum(1 for p in ps if accrued(p, season, T) > te.PS_MAX_ACCRUED)


def ps_eligibility(p: dict, season: int, T: int, veterans: int) -> tuple:
    """(eligible, note) for a demotion to the practice squad."""
    a = accrued(p, season, T)
    if a <= te.PS_MAX_ACCRUED:
        return True, f"PS eligibility: {a} seasons accrued"
    if veterans < te.PS_VETERAN_SLOTS:
        return True, f"PS eligibility: veteran slot ({a} seasons accrued, {veterans}/{te.PS_VETERAN_SLOTS} used)"
    return False, f"PS eligibility: {a} seasons accrued and all {te.PS_VETERAN_SLOTS} veteran slots are used"


def ir_eligible(p: dict) -> bool:
    return roster_status(p) == "ACTIVE" and is_injured(p) and weeks_out(p) >= IR_MIN_WEEKS


def shape_contract(p: dict, T: int) -> Optional[dict]:
    c, row = p.get("contract"), p.get("contract_row")
    if not c:
        return None
    through = p["start"] + c["length"] - 1
    return {"length": c["length"], "years_left": max(0, through - T + 1), "through": through,
            "total_value": row.get("total_value"), "guaranteed": row.get("guaranteed"),
            "contract_type": c.get("contract_type") or "STANDARD"}


def shape_roster_player(p: dict, T: int, season: int, veterans: int, pending: dict) -> dict:
    status = roster_status(p)
    injured = is_injured(p)
    return {"player_id": p["player_id"], "name": p["name"], "position": p["position"], "age": p.get("age"),
            "overall": p.get("overall"), "injury": (p["injury"].get("injury") or "Injured") if injured else None,
            "weeks_out": weeks_out(p) if injured else None,
            "contract": None if status == "PS" else shape_contract(p, T),
            "cap_hit": None if status == "PS" else p.get("cap_hit"),
            "ps_eligible": status == "ACTIVE" and ps_eligibility(p, season, T, veterans)[0],
            "ir_eligible": ir_eligible(p), "pending_move": pending.get(p["player_id"])}


_RESERVE_CACHE = {}


def rookie_reserve_estimate(cap: int, season: int) -> int:
    """The league-average rookie class's first-year cap hits (slotted picks 1-224 / 32 teams)."""
    key = (int(cap), season)
    if key not in _RESERVE_CACHE:
        total = sum(ce.cap_hit(ce.rookie_contract(pick, season, cap), 0)
                    for pick in range(1, ce.CBA["DRAFT_PICKS"] + 1))
        _RESERVE_CACHE[key] = int(round(total / 32))
    return _RESERVE_CACHE[key]


def cap_years(players: list, caps: dict, season: int, T: int, years: int) -> list:
    """The cap sheet from the league year on (see the module docstring)."""
    out, prev = [], None
    for y in range(T, T + max(1, years)):
        row = caps.get(y) or {}
        estimated = not row.get("cap")
        if row.get("cap"):
            cap = int(row["cap"])
        elif prev is not None:
            cap = ce.next_cap(prev, y, CAP_SEED)
        else:
            cap = int(ce.CBA["REFERENCE_CAP"])
        committed = 0
        for p in players:
            c = p.get("contract")
            if c and p.get("start") is not None:
                committed += ce.cap_hit(c, y - p["start"])
        dead = int(row.get("cap_penalty") or 0)
        if y == season:
            reserve = 0
        elif y == season + 1 and (caps.get(season) or {}).get("next_rookie_reserve") is not None:
            reserve = int(caps[season]["next_rookie_reserve"])
        else:
            reserve = rookie_reserve_estimate(cap, y)
            estimated = True
        out.append({"season": y, "cap": cap, "committed": committed, "dead_money": dead, "rookie_reserve": reserve,
                    "room": cap - committed - dead - reserve, "estimated": estimated})
        prev = cap
    return out


def preview_move(kind: str, p: dict, roster: dict, room: Optional[int], season: int, T: int, post_june1: bool,
                 pending: dict, cap: Optional[int] = None) -> dict:
    """The §2.7a MovePreview for one move (pure)."""
    status = roster_status(p)
    reasons, problems = [], []
    active_n, ps_n = len(roster["ACTIVE"]), len(roster["PS"])
    cap_hit = p.get("cap_hit") if status != "PS" else None
    dead = dead_next = 0
    savings = 0
    contract = None

    def bad(msg):
        problems.append(msg)
        reasons.append(msg)

    if kind not in MOVE_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(MOVE_KINDS)}")
    if p["player_id"] in pending:
        bad(f"{pending[p['player_id']]} is already pending for him")

    if kind in ("RELEASE", "PS_DEMOTE") and p.get("contract") and p.get("year") is not None:
        dead, dead_next = ce._dead_split(p["contract"], p["year"], post_june1 and kind == "RELEASE")
        savings = (cap_hit or 0) - dead

    if kind == "RELEASE":
        if status == "ACTIVE":
            active_n -= 1
        elif status == "PS":
            ps_n -= 1
        if post_june1:
            reasons.append(f"post-June 1: ${dead_next:,} of dead money moves to {T + 1}")
    elif kind == "PS_DEMOTE":
        if status != "ACTIVE":
            bad("only an active-roster player can be moved to the practice squad")
        else:
            active_n -= 1
            ps_n += 1
            ok, note = ps_eligibility(p, season, T, ps_veterans(roster["PS"], season, T))
            if ok:
                reasons.append(note)
            else:
                bad(note)
            if is_injured(p):
                bad("an injured player can't be moved to the practice squad")
            if ps_n > te.PRACTICE_SQUAD_LIMIT:
                bad(f"the practice squad is full ({te.PRACTICE_SQUAD_LIMIT})")
    elif kind == "PS_PROMOTE":
        if status != "PS":
            bad("only a practice-squad player can be promoted")
        else:
            active_n += 1
            ps_n -= 1
            salary = ce.minimum_salary(accrued(p, season, T), cap)
            contract = te.make_contract(salary, 1, "MINIMUM", signed_season=T, bonus_share=0, guar_share=0)
            savings = -salary
            reasons.append(f"a 1-year minimum deal: ${salary:,}")
            if active_n > te.ROSTER_LIMIT:
                bad(f"the active roster is full ({te.ROSTER_LIMIT})")
    elif kind == "IR_PLACE":
        if status != "ACTIVE":
            bad("only an active-roster player can be placed on IR")
        elif not ir_eligible(p):
            bad(f"IR needs an injury that keeps him out {IR_MIN_WEEKS}+ weeks (out {weeks_out(p)})")
        else:
            active_n -= 1
    elif kind == "IR_ACTIVATE":
        if status != "IR":
            bad("he isn't on injured reserve")
        else:
            active_n += 1
            if weeks_out(p) > 0:
                bad(f"still injured: {weeks_out(p)} weeks out")
            if active_n > te.ROSTER_LIMIT:
                bad(f"the active roster is full ({te.ROSTER_LIMIT})")

    after = None if room is None else room + savings
    if after is not None and after < 0 and savings < 0:
        bad(f"would leave the team ${-after:,} over the cap")
    out = {"kind": kind, "player_id": p["player_id"], "name": p["name"], "cap_hit": cap_hit, "cap_savings": savings,
           "dead_money": dead, "dead_money_next_year": dead_next, "cap_room_before": room, "cap_room_after": after,
           "active_count_after": active_n, "practice_squad_count_after": ps_n, "legal": not problems, "reasons": reasons,
           "_problems": problems}
    if contract:
        out["_contract"] = contract
    return out


def chart_entries(chart: dict, by_id: dict) -> dict:
    """{slot: [DepthChartEntry]} for all 35 slots (unknown player ids dropped)."""
    out = {}
    for slot in dce.POSITION_SLOTS:
        entries = []
        for pid in (chart.get(slot) or [])[:dce.DEPTH]:
            p = by_id.get(pid)
            if p is None:
                continue
            entries.append({"player_id": pid, "name": p["name"], "position": p["position"],
                            "overall": p.get("overall"), "injured": is_injured(p) and (weeks_out(p) > 0 or on_ir(p))})
        out[slot] = entries
    return out


def staff_chart(players: list, scheme: dict, ratings: dict) -> dict:
    roster, injuries = [], {}
    for p in players:
        if roster_status(p) != "ACTIVE" and not on_ir(p):
            continue
        r = ratings.get(p["player_id"]) or {}
        roster.append({"player_id": p["player_id"], "position": p["position"], "archetype": p.get("archetype"),
                       "overall": p.get("overall"), "ratings": r, "age": p.get("age"), "stamina": r.get("Stamina")})
        injuries[p["player_id"]] = {"out": on_ir(p) or weeks_out(p) > 0, "weeks": weeks_out(p)}
    return dce.staff_depth_chart(roster, scheme, injuries)


def projected_depth(player: dict, teammates: list) -> int:
    """1 starter, 2 a role (within 4 OVR of the weakest starter), 3 depth (free_agency_engine's rule)."""
    group = position_group(player.get("position"))
    n = SLOTS.get(group)
    if not n:
        return 2
    ovrs = sorted(((t.get("overall") or 0) for t in teammates
                   if t["player_id"] != player["player_id"] and position_group(t.get("position")) == group),
                  reverse=True)
    if len(ovrs) < n:
        return 1
    line, ovr = ovrs[n - 1], player.get("overall") or 0
    if ovr > line:
        return 1
    return 2 if ovr >= line - SITUATIONAL_WITHIN else 3


def scheme_fit_value(player: dict, scheme: dict) -> float:
    pos = player.get("position") or ""
    slot = pos if pos in dce.POSITION_SLOTS else POSITION_SLOT.get(pos, pos)
    if slot not in dce.POSITION_SLOTS:
        return fae.NEUTRAL
    return 1.0 if dce.scheme_fit(player, slot, scheme) > 0 else fae.NEUTRAL


def fa_player(p: dict, season: int, T: int) -> dict:
    """free_agency_engine's player dict."""
    return {"id": p["player_id"], "group": position_group(p.get("position")), "ovr": p.get("overall"),
            "age": p.get("age"), "accrued_seasons": accrued(p, season, T), "dev": p.get("dev_trait")}


def offer_contract(years, total, bonus, guaranteed, ctype, signed_season) -> dict:
    """A contracts_engine dict for an offer: bonus prorated over min(years, 5), base even, guaranteed base = the
    guarantee beyond the bonus."""
    pro = ce._prorate(bonus, min(years, ce.CBA["PRORATION_MAX_YEARS"]))
    return {"length": years, "current_year": 0, "salary_by_year": ce._prorate(total - bonus, years),
            "bonus_by_year": pro + [0] * (years - len(pro)), "guaranteed": max(0, guaranteed - bonus),
            "contract_type": ctype, "signed_season": signed_season, "last_restructure_season": None,
            "drafted_round": None}


def check_terms(years, total, bonus, guaranteed):
    if not 1 <= years <= MAX_OFFER_YEARS:
        raise HTTPException(400, f"years must be 1-{MAX_OFFER_YEARS}")
    if total <= 0 or bonus < 0 or guaranteed < 0:
        raise HTTPException(400, "total_value must be positive; bonus and guaranteed can't be negative")
    if bonus > total or guaranteed > total:
        raise HTTPException(400, "bonus and guaranteed can't exceed total_value")


def win_loss(games: list, team_id: int) -> dict:
    w = l = t = 0
    for gid, wk, home, away, hs, as_, status, gtime, gtype in games:
        if status != "COMPLETED" or (gtype or "REGULAR_SEASON") != "REGULAR_SEASON" or team_id not in (home, away):
            continue
        mine, theirs = (hs, as_) if home == team_id else (as_, hs)
        mine, theirs = mine or 0, theirs or 0
        w, l, t = (w + 1, l, t) if mine > theirs else (w, l + 1, t) if mine < theirs else (w, l, t + 1)
    return {"wins": w, "losses": l, "ties": t}


def next_game(games: list, team_id: int, week: int, names: dict) -> Optional[dict]:
    for gid, wk, home, away, hs, as_, status, gtime, gtype in games:
        if wk >= week and status not in ("COMPLETED", "BYE") and team_id in (home, away):
            opp = away if home == team_id else home
            return {"game_id": gid, "season": None, "week": wk, "game_time": gtime, "game_type": gtype,
                    "home": home == team_id, "opponent": names.get(opp, {"id": opp, "abbr": None, "name": None})}
    return None


def ranks(rows: list) -> dict:
    """{team_id: Ranks} from rank_rows (rank 1 = best among teams with games)."""
    stats = {}
    for r in rows:
        tid, games = r[0], r[1]
        vals = [float(v) if v is not None else None for v in r[2:]]
        stats[tid] = {"games": int(games), "points_for": vals[0], "points_against": vals[1], "yards_for": vals[2],
                      "yards_against": vals[3], "pass_yards_for": vals[4], "pass_yards_against": vals[5],
                      "rush_yards_for": vals[6], "rush_yards_against": vals[7], "turnovers": vals[8],
                      "takeaways": vals[9]}
    out = {tid: {"games": s["games"]} for tid, s in stats.items()}
    for key, higher in RANK_STATS:
        have = [(tid, s[key]) for tid, s in stats.items() if s[key] is not None]
        have.sort(key=lambda x: -x[1] if higher else x[1])
        rank_of = {}
        for i, (tid, v) in enumerate(have):
            prev = have[i - 1] if i else None
            rank_of[tid] = rank_of[prev[0]] if prev and prev[1] == v else i + 1
        for tid, s in stats.items():
            out[tid][key] = {"value": None if s[key] is None else round(s[key], 1), "rank": rank_of.get(tid)}
    return out


def _edge(team_rank, opp_rank) -> str:
    """team | opponent | even: the better rank (1 = best) by more than 3 places wins the matchup."""
    if team_rank is None or opp_rank is None or abs(team_rank - opp_rank) <= 3:
        return "even"
    return "team" if team_rank < opp_rank else "opponent"


def matchups(mine: Optional[dict], theirs: Optional[dict]) -> list:
    """Each unit both ways: the team's offense v the opponent's defense, then the opponent's offense v the team's
    defense."""
    if not mine or not theirs:
        return []
    out = []
    for unit, off, dfn in MATCHUPS:
        for label, team_stat, opp_stat in ((unit, off, dfn), ("Opponent " + unit[0].lower() + unit[1:], dfn, off)):
            t, o = mine[team_stat], theirs[opp_stat]
            out.append({"unit": label, "team_stat": team_stat, "team_value": t["value"], "team_rank": t["rank"],
                        "opponent_stat": opp_stat, "opponent_value": o["value"], "opponent_rank": o["rank"],
                        "edge": _edge(t["rank"], o["rank"])})
    return out


# -------------------------------------------------------------- loaders ----
def _context(cur, abbr: str) -> dict:
    """Team, season, week, league year, the roster (with contracts at the league year) and pending moves."""
    team = team_row(cur, abbr)
    season, week = league_state(cur)
    T = league_year(season, week)
    players = [with_contract(p, T) for p in roster_players(cur, team["id"], season, week)]
    moves = pending_moves(cur, team["id"])
    return {"team": team, "season": season, "week": week, "T": T, "players": players,
            "roster": split_roster(players), "moves": moves, "pending": pending_by_player(moves)}


def _cap_now(cur, ctx: dict, years: int = 1) -> list:
    caps = cap_rows(cur, ctx["team"]["id"], range(ctx["season"], ctx["T"] + max(1, years)))
    return cap_years(ctx["players"], caps, ctx["season"], ctx["T"], years)


def _player(ctx: dict, player_id: int) -> dict:
    for p in ctx["players"]:
        if p["player_id"] == player_id:
            return p
    raise HTTPException(404, f"player {player_id} isn't on {ctx['team']['abbr']}'s roster")


def _depth_response(ctx: dict, source: str, chart: dict, pending=None) -> dict:
    by_id = {p["player_id"]: p for p in ctx["players"]}
    return {"team": ctx["team"]["abbr"], "season": ctx["season"], "week": ctx["week"], "source": source,
            "pending": pending, "slots": chart_entries(chart, by_id)}


def _staff(cur, ctx: dict) -> dict:
    scheme = team_scheme(cur, ctx["team"]["id"], ctx["season"])
    ratings = player_ratings(cur, [p["player_id"] for p in ctx["players"]], ctx["season"])
    return staff_chart(ctx["players"], scheme, ratings)


def _preview(cur, ctx: dict, body: MoveIn) -> dict:
    p = _player(ctx, body.payload.player_id)
    year0 = _cap_now(cur, ctx)[0]
    return preview_move(body.kind, p, ctx["roster"], year0["room"], ctx["season"], ctx["T"], body.payload.post_june1,
                        ctx["pending"], year0["cap"])


def _score(cur, ctx, p, years, total, bonus, guaranteed, teammates):
    """(fa player, team dict, market, offer, utility, decision, counter, depth) for offer_utility / acceptance."""
    T = ctx["T"]
    market = market_fit(cur, ctx["season"])
    tc = team_context(cur, ctx["team"]["id"], ctx["season"])
    scheme = team_scheme(cur, ctx["team"]["id"], ctx["season"])
    player = fa_player(p, ctx["season"], T)
    depth = projected_depth(p, teammates)
    games = tc["wins"] + tc["losses"]
    team = {"team_id": ctx["team"]["id"], "win_pct": tc["wins"] / games if games else None,
            "popularity_index": tc["popularity_index"], "direction": tc["direction"],
            "projected_depth": {p["player_id"]: depth},
            "scheme_fit": {p["player_id"]: scheme_fit_value(p, scheme)}}
    offer = {"team_id": ctx["team"]["id"], "player_id": p["player_id"], "years": years, "total_value": total,
             "bonus": bonus, "guaranteed": guaranteed, "projected_depth": depth}
    if market is None or not player["group"]:
        market_used = None
    else:
        market_used = market
    score = fae.offer_utility(offer, player, team, market_used)
    live = in_season(ctx["week"])
    decision, counter = fae.acceptance({"offer": offer, "team": team, "market": market_used, "score": score}, player,
                                       live)
    bar = fae.accept_bar(player, live)
    return score, decision, counter, depth, round(bar, 4)


STATUS = {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "COUNTER": "COUNTERED"}


def in_season(week: int) -> bool:
    return week < ce.OFFSEASON_STAGE_WEEKS["OFFSEASON_START"]


def _seed(ctx: dict) -> str:
    return f"user:{ctx['team']['abbr']}:{ctx['season']}:{ctx['week']}"


# ------------------------------------------------------------- endpoints ----
@router.get("/{abbr}/overview")
def overview(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        tid = ctx["team"]["id"]
        games = season_games(cur, ctx["season"])
        names = team_names(cur)
        year0 = _cap_now(cur, ctx)[0]
        nxt = next_game(games, tid, ctx["week"], names)
        if nxt:
            nxt["season"] = ctx["season"]
        teams = user_teams()
        return {"team": ctx["team"], "season": ctx["season"], "week": ctx["week"], "league_year": ctx["T"],
                "user_controlled": (not teams) or ctx["team"]["abbr"].upper() in teams,
                "record": win_loss(games, tid),
                "cap": {k: year0[k] for k in ("cap", "committed", "dead_money", "rookie_reserve", "room", "estimated")},
                "roster_counts": {"active": len(ctx["roster"]["ACTIVE"]), "practice_squad": len(ctx["roster"]["PS"]),
                                  "injured_reserve": len(ctx["roster"]["IR"]),
                                  "limits": {"active": te.ROSTER_LIMIT, "practice_squad": te.PRACTICE_SQUAD_LIMIT}},
                "next_game": nxt,
                "pending_moves": [dict(queued_move(m), payload=m["payload"]) for m in ctx["moves"]]}


@router.get("/{abbr}/depth-chart")
def get_depth_chart(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        mine = [m for m in ctx["moves"] if m["kind"] == "DEPTH_CHART"]
        if mine:
            m = mine[-1]
            return _depth_response(ctx, "JR", (m["payload"] or {}).get("slots") or {}, queued_move(m))
        source, chart = stored_depth_chart(cur, ctx["team"]["id"], ctx["season"], ctx["week"])
        if source:
            return _depth_response(ctx, source, chart)
        return _depth_response(ctx, "MU_COACH", _staff(cur, ctx))


@router.put("/{abbr}/depth-chart")
def put_depth_chart(abbr: str, body: DepthChartIn):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        require_user_team(ctx["team"])
        unknown = sorted(set(body.slots) - set(dce.POSITION_SLOTS))
        if unknown:
            raise HTTPException(400, f"not depth chart slots: {', '.join(unknown)}")
        missing = [s for s in dce.POSITION_SLOTS if s not in body.slots]
        if missing:
            raise HTTPException(400, f"every slot must be sent; missing: {', '.join(missing)}")
        ids = {p["player_id"] for p in ctx["players"]}
        for slot, pids in body.slots.items():
            if len(pids) > dce.DEPTH:
                raise HTTPException(400, f"{slot}: {len(pids)} players, at most {dce.DEPTH}")
            if len(set(pids)) != len(pids):
                raise HTTPException(400, f"{slot}: a player appears twice")
            strangers = [pid for pid in pids if pid not in ids]
            if strangers:
                raise HTTPException(400, f"{slot}: not on {ctx['team']['abbr']}: {strangers}")
        cancel_pending(cur, ctx["team"]["id"], "DEPTH_CHART")
        move = insert_move(cur, ctx["team"]["id"], "DEPTH_CHART",
                           {"slots": {s: list(body.slots[s]) for s in dce.POSITION_SLOTS},
                            "season": ctx["season"], "week": ctx["week"]})
        conn.commit()
        return move


@router.post("/{abbr}/depth-chart/staff")
def staff_depth_chart(abbr: str, body: Optional[dict] = None):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        return _depth_response(ctx, "MU_COACH", _staff(cur, ctx))


@router.get("/{abbr}/roster")
def roster(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        s, T = ctx["season"], ctx["T"]
        vets = ps_veterans(ctx["roster"]["PS"], s, T)
        key = lambda p: (-(p.get("overall") or 0), p["name"] or "")
        shape = lambda lst: [shape_roster_player(p, T, s, vets, ctx["pending"]) for p in sorted(lst, key=key)]
        return {"team": ctx["team"]["abbr"], "season": s, "week": ctx["week"],
                "limits": {"active": te.ROSTER_LIMIT, "practice_squad": te.PRACTICE_SQUAD_LIMIT},
                "active": shape(ctx["roster"]["ACTIVE"]), "practice_squad": shape(ctx["roster"]["PS"]),
                "injured_reserve": shape(ctx["roster"]["IR"])}


@router.get("/{abbr}/cap")
def cap(abbr: str, years: int = 5):
    if not 1 <= years <= 10:
        raise HTTPException(400, "years must be 1-10")
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        return {"team": ctx["team"]["abbr"], "season": ctx["T"], "years": _cap_now(cur, ctx, years)}


@router.post("/{abbr}/moves/preview")
def moves_preview(abbr: str, body: MoveIn):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        out = _preview(cur, ctx, body)
        out.pop("_contract", None)
        out.pop("_problems", None)
        return out


@router.post("/{abbr}/moves")
def queue_move(abbr: str, body: MoveIn):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        require_user_team(ctx["team"])
        pv = _preview(cur, ctx, body)
        problems = pv.pop("_problems")
        if not pv["legal"]:
            raise HTTPException(409, "; ".join(problems))
        contract = pv.pop("_contract", None)
        payload = {"player_id": body.payload.player_id, "post_june1": body.payload.post_june1, "preview": pv}
        if contract:
            payload["contract"] = contract
        move = insert_move(cur, ctx["team"]["id"], body.kind, payload)
        conn.commit()
        return move


@router.get("/{abbr}/medical")
def medical(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        players = []
        for p in ctx["players"]:
            injured, w, ir = is_injured(p), weeks_out(p), on_ir(p)
            status = ("OUT" if ir or w >= 2 else "DOUBTFUL" if w == 1 else "QUESTIONABLE") if injured else "HEALTHY"
            players.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                            "overall": p.get("overall"),
                            "injury": (p["injury"].get("injury") or "Injured") if injured else None,
                            "weeks_out": w if injured else 0, "status": status, "on_ir": ir,
                            "ir_eligible": ir_eligible(p), "pending_move": ctx["pending"].get(p["player_id"])})
        players.sort(key=lambda x: (x["status"] == "HEALTHY", -x["weeks_out"], -(x["overall"] or 0), x["name"] or ""))
        return {"team": ctx["team"]["abbr"], "season": ctx["season"], "week": ctx["week"], "players": players}


def _asking(p: dict, fp: dict, market) -> Optional[dict]:
    """{years, total_value, guaranteed}: his asking price (transactions_engine.asking_price) over his ideal term
    (free_agency_engine.ideal_years), guaranteed at the engine's target share (35%, veterans 60%)."""
    if p.get("age") is None and p.get("overall") is None:
        return None
    tvp = {"group": fp["group"], "ovr": fp["ovr"], "age": fp["age"], "dev": fp["dev"], "yoe": p.get("yoe")}
    per_year = te.asking_price(tvp, market, fp["accrued_seasons"]) if market is not None else \
        ce.minimum_salary(fp["accrued_seasons"])
    years = fae.ideal_years(p.get("age"))
    share = fae.GUAR_TARGET_VET if (p.get("age") or 27) >= fae.VET_AGE else fae.GUAR_TARGET
    total = int(per_year) * years
    return {"years": years, "total_value": total, "guaranteed": int(round(total * share))}


@router.get("/free-agents")
def free_agents(position: Optional[str] = None, min_ovr: Optional[int] = None, max_age: Optional[int] = None,
                team: Optional[str] = None, limit: int = FREE_AGENT_LIMIT):
    limit = max(1, min(int(limit), 500))
    with _connect() as conn, conn.cursor() as cur:
        season, week = league_state(cur)
        T = league_year(season, week)
        scheme = None
        if team:
            scheme = team_scheme(cur, team_row(cur, team)["id"], season)
        market = market_fit(cur, season)
        out = []
        for p in free_agent_rows(cur, season, position.upper() if position else None, min_ovr, max_age, limit):
            fp = fa_player(p, season, T)
            worth = None
            if market is not None and fp["group"]:
                from engines import trade_value as tv
                worth = int(round(tv.worth(fp["ovr"], fp["group"], market)))
            out.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                        "age": p.get("age"), "overall": p.get("overall"), "dev_trait": p.get("dev_trait"),
                        "scheme_fit": None if scheme is None else scheme_fit_value(p, scheme),
                        "market_worth": worth, "asking": _asking(p, fp, market)})
        return {"season": season, "week": week, "in_season": in_season(week), "players": out}


def negotiation(oid: int, decision: str, score: dict, bar, counter, note=None) -> dict:
    """The NegotiationResponse shape shared by offers and proposals."""
    return {"offer_id": oid, "status": decision, "utility": score["utility"], "bar": bar,
            "parts": {k: score[k] for k in ("financial", "opportunity", "contention", "scheme", "persona")},
            "counter": counter, "note": note}


NOTES = {"ACCEPT": "He accepts. The deal is stored as an accepted offer; the league office writes it to the save.",
         "COUNTER": "He wants more: see his counter.",
         "REJECT": "He turns it down."}


@router.post("/{abbr}/offers")
def make_offer(abbr: str, body: OfferIn):
    check_terms(body.years, body.total_value, body.bonus, body.guaranteed)
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        require_user_team(ctx["team"])
        p = free_agent(cur, ctx["season"], body.player_id)
        if not p:
            raise HTTPException(404, f"player {body.player_id} has no season row")
        if not p["is_free_agent"]:
            raise HTTPException(409, f"{p['name']} isn't a free agent")
        year0 = _cap_now(cur, ctx)[0]
        hit = ce.cap_hit(offer_contract(body.years, body.total_value, body.bonus, body.guaranteed, "STANDARD",
                                        ctx["T"]), 0)
        if hit > year0["room"]:
            raise HTTPException(409, f"a ${hit:,} first-year cap hit doesn't fit ${year0['room']:,} of cap room")
        if len(ctx["roster"]["ACTIVE"]) >= te.ROSTER_LIMIT:
            raise HTTPException(409, f"the active roster is full ({te.ROSTER_LIMIT})")
        teammates = ctx["roster"]["ACTIVE"] + ctx["roster"]["IR"]
        score, decision, counter, depth, bar = _score(cur, ctx, p, body.years, body.total_value, body.bonus,
                                                      body.guaranteed, teammates)
        oid = insert_offer(cur, ctx["season"], stage_for(ctx["week"]), "FREE_AGENT", p["player_id"], ctx["team"]["id"],
                           body.years, body.total_value, body.bonus, body.guaranteed, score["utility"],
                           STATUS[decision], counter, _seed(ctx))
        conn.commit()
        return negotiation(oid, decision, score, bar, counter, NOTES[decision])


def salaries_by_position(cur, season: int, T: int) -> dict:
    """{position: [cap hits at the league year]} across every ACTIVE contract (tag and option values)."""
    cur.execute("""SELECT ps.position, y.cap_hit FROM player_contracts c
                   JOIN player_contract_years y ON y.contract_id = c.id
                   JOIN player_seasons ps ON ps.player_id = c.player_id AND ps.season = c.season
                   WHERE c.season = %s AND c.status = 'ACTIVE' AND y.year = %s AND y.cap_hit > 0""", (season, T))
    out = {}
    for pos, hit in cur.fetchall():
        if pos:
            out.setdefault(pos, []).append(int(hit))
    return out


def contract_options(p: dict, T: int, salaries: dict) -> dict:
    """tag_value / transition_tag_value for an expiring deal (contracts_engine.tag_value, at least 120% of this
    year's hit); fifth_year_option {tier, salary} for a 1st-round rookie deal in year 3 or 4 (no Pro Bowl or snap
    data is read yet, so the tier is 4)."""
    c = p.get("contract")
    out = {}
    if not c or not salaries.get(p.get("position")):
        return out
    through = p["start"] + c["length"] - 1
    if through <= T:
        prior = p.get("cap_hit") or 0
        out["tag_value"] = ce.tag_value("FRANCHISE_NONEXCL", p["position"], salaries, prior, None)
        out["transition_tag_value"] = ce.tag_value("TRANSITION", p["position"], salaries, prior, None)
    if c.get("contract_type") == "ROOKIE" and c.get("drafted_round") == 1 and p.get("year") in (2, 3):
        opt = ce.fifth_year_option({"position": p["position"], "drafted_round": 1, "pro_bowls": p.get("pro_bowls"),
                                    "playtime": p.get("playtime")}, salaries)
        if opt.get("tier"):
            out["fifth_year_option"] = {"tier": opt["tier"], "salary": opt["salary"]}
    return out


def _eligibility(p: dict, ctx: dict) -> tuple:
    return ce.extension_eligible(p["contract"], {"rookie_season": ctx["season"] - (p.get("yoe") or 0)},
                                 ctx["season"], ctx["week"])


@router.get("/{abbr}/contracts/expiring")
def contracts_expiring(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        salaries = salaries_by_position(cur, ctx["season"], ctx["T"])
        rows = []
        for p in ctx["players"]:
            c = p.get("contract")
            if not c or roster_status(p) == "PS":
                continue
            ok, reason = _eligibility(p, ctx)
            expiring = p["start"] + c["length"] - 1 <= ctx["T"]
            if not (ok or expiring):
                continue
            rows.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                         "overall": p.get("overall"), "age": p.get("age"), "contract": shape_contract(p, ctx["T"]),
                         "cap_hit": p.get("cap_hit"), "eligible": ok, "eligible_reason": reason,
                         "options": contract_options(p, ctx["T"], salaries), "_expiring": expiring})
        rows.sort(key=lambda r: (not r["_expiring"], -(r["overall"] or 0), r["name"] or ""))
        for r in rows:
            r.pop("_expiring")
        return {"team": ctx["team"]["abbr"], "season": ctx["season"], "week": ctx["week"], "players": rows}


@router.post("/{abbr}/contracts/{player_id}/propose")
def propose(abbr: str, player_id: int, body: ProposeIn):
    check_terms(body.years, body.total_value, body.bonus, body.guaranteed)
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        require_user_team(ctx["team"])
        p = _player(ctx, player_id)
        c = p.get("contract")
        if not c or roster_status(p) == "PS":
            raise HTTPException(409, f"{p['name']} has no contract to extend")
        ok, reason = _eligibility(p, ctx)
        if not ok:
            raise HTTPException(409, reason)
        kind = "RESIGN" if p["start"] + c["length"] - 1 <= ctx["T"] else "EXTENSION"
        teammates = ctx["roster"]["ACTIVE"] + ctx["roster"]["IR"]
        score, decision, counter, depth, bar = _score(cur, ctx, p, body.years, body.total_value, body.bonus,
                                                      body.guaranteed, teammates)
        oid = insert_offer(cur, ctx["season"], stage_for(ctx["week"]), kind, player_id, ctx["team"]["id"], body.years,
                           body.total_value, body.bonus, body.guaranteed, score["utility"], STATUS[decision], counter,
                           _seed(ctx))
        conn.commit()
        return negotiation(oid, decision, score, bar, counter, f"{reason}. {NOTES[decision]}")


@router.post("/{abbr}/contracts/{player_id}/restructure-preview")
def restructure_preview(abbr: str, player_id: int, body: RestructureIn):
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        p = _player(ctx, player_id)
        c = p.get("contract")
        if not c or roster_status(p) == "PS" or p.get("year") is None:
            raise HTTPException(409, f"{p['name']} has no contract year to restructure in {ctx['T']}")
        year = p["year"]
        base = c["salary_by_year"][year] if year < len(c["salary_by_year"]) else 0
        reasons = []
        try:
            new = ce.restructure(c, year, body.amount)
        except ValueError as e:
            new, reasons = c, [str(e)]
        seasons = range(ctx["T"], p["start"] + c["length"])
        by_year = [{"season": s, "cap_hit_before": ce.cap_hit(c, s - p["start"]),
                    "cap_hit_after": ce.cap_hit(new, s - p["start"])} for s in seasons]
        before, after = ce.cap_hit(c, year), ce.cap_hit(new, year)
        if c["length"] - year <= 1 and not reasons:
            reasons.append("final contract year: the converted money all counts this season (no savings)")
        return {"player_id": player_id, "amount": body.amount,
                "max_amount": int(ce.CBA["RESTRUCTURE_MAX_SHARE"] * base), "cap_hit_before": before,
                "cap_hit_after": after, "cap_savings_now": before - after,
                "added_dead_money_future": sum(new["bonus_by_year"][year + 1:]) - sum(c["bonus_by_year"][year + 1:]),
                "by_year": by_year, "legal": new is not c, "reasons": reasons}


def pick_rows(cur, team_id: int, season: int, seasons: int) -> list:
    """The team's unused picks in the next `seasons` drafts: [(draft_season, round, original abbr, overall_pick)]."""
    if not _regclass(cur, "draft_picks"):
        return []
    cur.execute("""SELECT d.draft_season, d.round, o.abbreviation, d.overall_pick FROM draft_picks d
                   JOIN teams o ON o.id = d.original_team_id
                   WHERE d.owner_team_id = %s AND NOT d.used AND d.draft_season > %s AND d.draft_season <= %s
                   ORDER BY d.draft_season, d.round, d.overall_pick NULLS LAST, o.abbreviation""",
                (team_id, season, season + seasons))
    return cur.fetchall()


@router.get("/{abbr}/picks")
def picks(abbr: str, seasons: int = 3):
    seasons = max(1, min(int(seasons), 5))
    with _connect() as conn, conn.cursor() as cur:
        team = team_row(cur, abbr)
        season, _ = league_state(cur)
        rows = pick_rows(cur, team["id"], season, seasons)
        return {"team": team["abbr"], "picks": [{"season": ds, "round": rd, "original_team": orig,
                                                  "overall_pick": overall} for ds, rd, orig, overall in rows]}


def trade_cap(ctx_by_team: dict, rows: dict, eng: list, T: int) -> dict:
    """{team_id: {abbr, cap_room_before, incoming, outgoing, dead_money, cap_room_after, ok}} from contracts_engine:
    cap hits at the league year; the sender keeps the remaining prorated bonus as dead money. ok: the room stays
    >= 0, or the trade doesn't shrink it."""
    out = {}
    for tid, info in ctx_by_team.items():
        inc = outg = dead = 0
        for x in eng:
            if x["type"] != "PLAYER" or x["player_id"] not in rows:
                continue
            c, start = te.contract_from_db(rows[x["player_id"]])
            y = T - start
            if not 0 <= y < c["length"]:
                continue
            hit = ce.cap_hit(c, y)
            if x["to"] == tid:
                inc += hit
            if x["from"] == tid:
                outg += hit
                dead += sum(c["bonus_by_year"][y:])
        before = info["room"]
        after = None if before is None else before - inc + outg - dead
        out[tid] = {"abbr": info["abbr"], "cap_room_before": before, "incoming": inc, "outgoing": outg,
                    "dead_money": dead, "cap_room_after": after,
                    "ok": after is None or after >= 0 or after >= before}
    return out


def trade_assets(body, us: str, them: str) -> list:
    """Front Office JSON assets (engines.front_office.to_engine_assets) from the send / receive sides."""
    out = []
    for side, frm, to in ((body.send, us, them), (body.receive, them, us)):
        for pid in side.players:
            out.append({"type": "PLAYER", "player_id": int(pid), "from": frm, "to": to})
        for pk in side.picks:
            out.append({"type": "PICK", "draft_season": int(pk.season), "round": int(pk.round),
                        "original_team": ABBR_ALIASES.get(pk.original_team.upper(), pk.original_team.upper()),
                        "from": frm, "to": to})
    return out


def trade_sides(eng: list, user_id: int, abbrs: dict) -> dict:
    """Engine assets -> {send: {players, picks}, receive: {players, picks}} from the user's side."""
    out = {"send": {"players": [], "picks": []}, "receive": {"players": [], "picks": []}}
    for x in eng:
        side = out["send" if x["from"] == user_id else "receive"]
        if x["type"] == "PLAYER":
            side["players"].append(x["player_id"])
        else:
            ds, rd, orig = x["pick"]
            side["picks"].append({"season": ds, "round": rd, "original_team": abbrs.get(orig, orig)})
    return out


@router.post("/{abbr}/trades/evaluate")
def trades_evaluate(abbr: str, body: TradeIn):
    from engines.front_office import FrontOfficeError
    with _connect() as conn, conn.cursor() as cur:
        ctx = _context(cur, abbr)
        other = team_row(cur, body.other_team)
        if other["id"] == ctx["team"]["id"]:
            raise HTTPException(400, "pick two different teams")
        assets = trade_assets(body, ctx["team"]["abbr"], other["abbr"])
        if not assets:
            raise HTTPException(400, "a trade needs at least one asset")
        try:
            b, valuer = trade_books(cur)
            res = negotiate_trade(cur, b, valuer, ctx["team"]["id"], other["id"], assets)
        except FrontOfficeError as e:
            raise HTTPException(e.status, e.message)
        eng, r = res["engine_assets"], res["response"]
        j = r["judge"]
        other_ctx = {"team": other, "season": ctx["season"], "week": ctx["week"], "T": ctx["T"],
                     "players": [with_contract(p, ctx["T"]) for p in
                                 roster_players(cur, other["id"], ctx["season"], ctx["week"])]}
        rooms = {ctx["team"]["id"]: {"abbr": ctx["team"]["abbr"], "room": _cap_now(cur, ctx)[0]["room"]},
                 other["id"]: {"abbr": other["abbr"], "room": _cap_now(cur, other_ctx)[0]["room"]}}
        rows = contract_rows(cur, ctx["season"], [x["player_id"] for x in eng if x["type"] == "PLAYER"])
        capinfo = trade_cap(rooms, rows, eng, ctx["T"])
        us, them = capinfo[ctx["team"]["id"]], capinfo[other["id"]]
        reasons = list(r.get("reasons") or [])
        for c in (us, them):
            if not c["ok"]:
                reasons.append(f"cap: {c['abbr']} would be ${-c['cap_room_after']:,} over the cap")
        verdict = {"ACCEPT": "ACCEPT", "COUNTER": "COUNTER"}.get(r["action"], "REJECT")
        if verdict == "ACCEPT" and not (us["ok"] and them["ok"]):
            verdict = "REJECT"
        abbrs = {ctx["team"]["id"]: ctx["team"]["abbr"], other["id"]: other["abbr"]}
        counter = r.get("counter") if verdict == "COUNTER" else None
        return {"surplus_us": j["su"], "surplus_them": j["sg"], "verdict": verdict,
                "counter": trade_sides(counter["assets"], ctx["team"]["id"], abbrs) if counter else None,
                "cap_legal_us": us["ok"], "cap_legal_them": them["ok"],
                "cap_room_after_us": us["cap_room_after"], "cap_room_after_them": them["cap_room_after"],
                "reasons": reasons, "dialogue": res.get("dialogue")}


@router.get("/{abbr}/game-center")
def game_center(abbr: str):
    with _connect() as conn, conn.cursor() as cur:
        team = team_row(cur, abbr)
        season, week = league_state(cur)
        games = season_games(cur, season)
        names = team_names(cur)
        nxt = next_game(games, team["id"], week, names)
        if nxt:
            nxt["season"] = season
        table = ranks(rank_rows(cur, season))
        opp = None
        mine = table.get(team["id"])
        theirs = None
        if nxt and nxt["opponent"].get("id") is not None:
            oid = nxt["opponent"]["id"]
            theirs = table.get(oid)
            opp = dict(nxt["opponent"], record=win_loss(games, oid), ratings=team_ratings(cur, oid, season))
        return {"team": team["abbr"], "season": season, "week": week, "record": win_loss(games, team["id"]),
                "next_game": nxt, "opponent": opp, "ratings": team_ratings(cur, team["id"], season),
                "ranks": {"team": mine, "opponent": theirs}, "matchups": matchups(mine, theirs)}


@router.post("/{abbr}/game/launch")
def game_launch(abbr: str, body: Optional[LaunchIn] = None):
    from pipeline import game_launch as gl
    body = body or LaunchIn()
    with _connect() as conn, conn.cursor() as cur:
        team = team_row(cur, abbr)
        require_user_team(team)
        return gl.launch(team["abbr"], body.season, body.week, dry_run=body.dry_run, cur=cur)
