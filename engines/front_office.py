"""
Front Office: the GM agents' read helpers and JR's trade actions (docs/team_management/03_GM_AGENTS_PLAN.md
§2.7, §2.9, Phase 5; PROJECT_SPEC §3.3f).

Read helpers (the hand-off surface for steps 4 and 5; all read-only, plain tuple cursors):
  get_gm(cur, team_id, season)                     persona + direction, needs, fo_quality, control
  team_directions(cur, season)                     {team_id: direction}
  draft_pick_owner(cur, draft_season, round, original_team_id)
  trade_value(cur, season, team_id=None, player_id=None, pick=None)   (+ player_value / pick_value re-exported)
  executed_trades(cur, season, week=None)          EXECUTED trades with their legs
  trade_rumors(cur, season, week)                  non-executed rumor_ok proposals: teams, asset types/positions
  deadline_summary(cur, season)                    buyers / sellers by direction, executed trades by week
  legality_check(cur, season, week, team_a, team_b, assets)
  front_office_state(cur, season)                  everything the #debug Front Office card shows
Sightings: store_sightings (called by the transactions load), match_sightings (sets only match_status /
proposal_id), consistency_flags (computed, never written). Actions (JR on #debug, inside the caller's
transaction): approve, reject, could_not_make, confirm ("Made it"), record ("Record a trade" / "Made a different
trade"), apply_sighting ("Apply this trade"), set_team_control.
"""
import json
import re

from engines.trade_value import league_value, pick_value, player_value  # noqa: F401  (re-exported for steps 4, 5)

ROUND_WORDS = {"1ST": 1, "FIRST": 1, "2ND": 2, "SECOND": 2, "3RD": 3, "THIRD": 3, "4TH": 4, "FOURTH": 4, "5TH": 5,
               "FIFTH": 5, "6TH": 6, "SIXTH": 6, "7TH": 7, "SEVENTH": 7}


class FrontOfficeError(Exception):
    """A refused action: (status code, message)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def installed(cur):
    cur.execute("SELECT to_regclass('gm_trade_proposals') IS NOT NULL")
    return bool(cur.fetchone()[0])


def current_state(cur):
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    return cur.fetchone()


# ----------------------------------------------------------------- read ----
def get_gm(cur, team_id, season=None):
    """The team's GM: persona, tenure, voice, this season's direction / needs / urgent need / fo_quality, and who
    runs the team. None when the team has no open GM tenure."""
    season = season or current_state(cur)[0]
    cur.execute("""SELECT g.id, g.team_id, t.abbreviation, g.gm_name, g.bio, g.voice, g.bio_source, g.start_season,
                          g.risk_appetite, g.aggressiveness, g.pick_affinity, g.youth_bias, g.position_bias,
                          s.direction, s.direction_score, s.direction_week, s.needs, s.surplus, s.urgent_need,
                          s.fo_quality, s.fo_quality_inputs, s.fo_quality_at,
                          COALESCE(st.user_controlled, false), COALESCE(st.suggestions_on, true)
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                   LEFT JOIN gm_team_seasons s ON s.team_id = g.team_id AND s.season = %s
                   LEFT JOIN gm_team_settings st ON st.team_id = g.team_id
                   WHERE g.team_id = %s AND g.end_season IS NULL""", (season, team_id))
    r = cur.fetchone()
    if not r:
        return None
    cols = ("gm_id", "team_id", "abbreviation", "gm_name", "bio", "voice", "bio_source", "start_season",
            "risk_appetite", "aggressiveness", "pick_affinity", "youth_bias", "position_bias", "direction",
            "direction_score", "direction_week", "needs", "surplus", "urgent_need", "fo_quality", "fo_quality_inputs",
            "fo_quality_at", "user_controlled", "suggestions_on")
    d = dict(zip(cols, r))
    for k in ("risk_appetite", "aggressiveness", "pick_affinity", "youth_bias", "direction_score", "fo_quality"):
        d[k] = float(d[k]) if d[k] is not None else None
    d["season"] = season
    return d


def team_directions(cur, season):
    cur.execute("SELECT team_id, direction FROM gm_team_seasons WHERE season = %s", (season,))
    return {r[0]: r[1] for r in cur.fetchall()}


def draft_pick_owner(cur, draft_season, round, original_team_id):
    cur.execute("""SELECT owner_team_id FROM draft_picks WHERE draft_season = %s AND round = %s
                   AND original_team_id = %s""", (draft_season, round, original_team_id))
    r = cur.fetchone()
    return r[0] if r else None


def trade_value(cur, season, team_id=None, player_id=None, pick=None):
    """One asset's value in dollars of surplus: to team_id (its direction, needs and GM), or the neutral league
    value without one. pick = (draft_season, round, original_team_id)."""
    from engines import gm_trade_engine as ge
    from engines import trade_value as tv
    b = ge.read_books(cur, season, current_state(cur)[1], ge.window_for(current_state(cur)[1]) or "IN_SEASON")
    v = ge.Valuer(b, tv.fits_from_db(cur, season))
    asset = ({"type": "PLAYER", "player_id": player_id} if player_id is not None else
             {"type": "PICK", "pick": tuple(pick)})
    return v.value(asset, team_id) if team_id is not None else v.league(asset)


def _legs(cur, ids):
    """{proposal_id: [{type, from, to, player_id, player_name, position, ovr, pick}]}."""
    if not ids:
        return {}
    cur.execute("""SELECT a.proposal_id, a.asset_type, fa.abbreviation, ta.abbreviation, a.player_id,
                          COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating, a.pick_draft_season,
                          a.pick_round, oa.abbreviation, a.cap_hit_now, a.from_team_id, a.to_team_id,
                          a.pick_original_team_id
                   FROM gm_trade_assets a
                   JOIN gm_trade_proposals g ON g.id = a.proposal_id
                   JOIN teams fa ON fa.id = a.from_team_id JOIN teams ta ON ta.id = a.to_team_id
                   LEFT JOIN players p ON p.id = a.player_id
                   LEFT JOIN player_seasons ps ON ps.player_id = a.player_id AND ps.season = g.season
                   LEFT JOIN teams oa ON oa.id = a.pick_original_team_id
                   WHERE a.proposal_id = ANY(%s) ORDER BY a.id""", (list(ids),))
    out = {}
    for r in cur.fetchall():
        leg = {"type": r[1], "from": r[2], "to": r[3], "from_team_id": r[12], "to_team_id": r[13]}
        if r[1] == "PLAYER":
            leg.update(player_id=r[4], player_name=r[5], position=r[6], ovr=r[7], cap_hit_now=r[11])
        else:
            leg.update(pick={"draft_season": r[8], "round": r[9], "original_team": r[10], "original_team_id": r[14]},
                       label=f"{r[8]} R{r[9]} ({r[10]})")
        out.setdefault(r[0], []).append(leg)
    return out


def executed_trades(cur, season, week=None):
    """EXECUTED trades (in Madden and in Mu), oldest first: [{proposal_id, week, team_a, team_b, executed_at,
    executed_as, origin, headline, rationale_a, rationale_b, legs}]. With week: that week's only."""
    cur.execute("""SELECT p.id, p.week, ta.abbreviation, tb.abbreviation, p.executed_at, p.executed_as, p.origin,
                          p.headline, p.rationale_a, p.rationale_b, p.trade_window
                   FROM gm_trade_proposals p JOIN teams ta ON ta.id = p.team_a_id JOIN teams tb ON tb.id = p.team_b_id
                   WHERE p.season = %s AND p.status = 'EXECUTED' AND (%s::int IS NULL OR p.week = %s)
                   ORDER BY p.executed_at, p.id""", (season, week, week))
    rows = cur.fetchall()
    legs = _legs(cur, [r[0] for r in rows])
    return [{"proposal_id": r[0], "week": r[1], "team_a": r[2], "team_b": r[3], "executed_at": r[4],
             "executed_as": r[5], "origin": r[6], "headline": r[7], "rationale_a": r[8], "rationale_b": r[9],
             "trade_window": r[10], "status": "EXECUTED", "legs": legs.get(r[0], [])} for r in rows]


def trade_rumors(cur, season, week):
    """Rumor material for step 5: proposals made by `week` that were never executed and have rumor_ok. Teams and
    asset types/positions only: no names, no package, no cap figures, never marked as done (status RUMOR)."""
    cur.execute("""SELECT p.id, p.week, ta.abbreviation, tb.abbreviation, p.status, p.trigger_kind
                   FROM gm_trade_proposals p JOIN teams ta ON ta.id = p.team_a_id JOIN teams tb ON tb.id = p.team_b_id
                   WHERE p.season = %s AND p.week <= %s AND p.rumor_ok AND p.status <> 'EXECUTED'
                     AND p.origin = 'ENGINE'
                   ORDER BY p.week, p.id""", (season, week))
    rows = cur.fetchall()
    legs = _legs(cur, [r[0] for r in rows])
    out = []
    for r in rows:
        assets = [{"type": l["type"], "position": l.get("position") if l["type"] == "PLAYER" else None,
                   "from": l["from"], "to": l["to"]} for l in legs.get(r[0], [])]
        out.append({"rumor_id": r[0], "week": r[1], "teams": [r[2], r[3]], "initiator": r[2],
                    "trigger": r[5], "assets": assets, "status": "RUMOR"})
    return out


def deadline_summary(cur, season):
    """Buyers (CONTEND) and sellers (REBUILD) with their top needs, RETOOL teams, and executed trades by week."""
    cur.execute("""SELECT t.abbreviation, s.direction, s.needs, s.direction_score FROM gm_team_seasons s
                   JOIN teams t ON t.id = s.team_id WHERE s.season = %s ORDER BY s.direction_score DESC NULLS LAST""",
                (season,))
    teams = cur.fetchall()
    by = {"CONTEND": [], "RETOOL": [], "REBUILD": []}
    for abbr, d, needs, score in teams:
        if d in by:
            by[d].append({"team": abbr, "needs": [n["group"] for n in (needs or [])[:3]],
                          "score": float(score) if score is not None else None})
    cur.execute("""SELECT week, count(*) FROM gm_trade_proposals WHERE season = %s AND status = 'EXECUTED'
                   GROUP BY week ORDER BY week""", (season,))
    return {"season": season, "buyers": by["CONTEND"], "sellers": by["REBUILD"], "retool": by["RETOOL"],
            "executed_by_week": {r[0]: r[1] for r in cur.fetchall()}}


def to_engine_assets(cur, assets):
    """Front Office JSON assets -> engine assets. Teams by abbreviation or id; a pick as
    {draft_season, round, original_team}."""
    ids = _team_ids(cur)
    out = []
    for a in assets:
        frm, to = _team(ids, a.get("from")), _team(ids, a.get("to"))
        if a.get("type", "PLAYER").upper() == "PLAYER":
            out.append({"type": "PLAYER", "player_id": int(a["player_id"]), "from": frm, "to": to})
        else:
            orig = _team(ids, a.get("original_team") or a.get("original_team_id") or a.get("from"))
            out.append({"type": "PICK", "pick": (int(a["draft_season"]), int(a["round"]), orig), "from": frm, "to": to})
    return out


def _team_ids(cur):
    cur.execute("SELECT id, abbreviation FROM teams WHERE abbreviation IS NOT NULL")
    return {r[1]: r[0] for r in cur.fetchall()}


def _team(ids, v):
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        return int(v)
    if v in ids:
        return ids[v]
    raise FrontOfficeError(400, f"unknown team {v!r}")


def legality_check(cur, season, week, team_a, team_b, assets, ignore_proposal=None):
    """Step 3's legality checks for any package (step 5's helper tool, and the warnings on confirm/record):
    {check: {ok, detail}} plus legal. assets: engine assets."""
    from engines import gm_trade_engine as ge
    from engines import trade_value as tv
    window = ge.window_for(week) or "IN_SEASON"
    b = ge.read_books(cur, season, week, window)
    if ignore_proposal is not None:
        b["open"] = [p for p in b["open"] if p["id"] != ignore_proposal]
        b = ge.finish_books(b)
    deal = {"a": team_a, "b": team_b, "assets": assets}
    missing = [x["player_id"] for x in assets if x["type"] == "PLAYER" and x["player_id"] not in b["players"]]
    if missing:
        return {"legal": False, "checks": {"players": {"ok": False, "detail": f"not on a roster: {missing}"}}}
    checks = ge.check_legality(deal, b, ge.Valuer(b, tv.fits_from_db(cur, season)))
    return {"legal": ge.legal(checks), "checks": checks}


# -------------------------------------------------------------- sightings ----
def pick_from_text(asset_text):
    """'2037 1ST ROUND PICK' -> (2037, 1); None when unreadable."""
    m = re.match(r"(\d{4}) (?:(\w+) )?(?:ROUND|RD)", asset_text or "")
    if not m:
        return None
    word = (m.group(2) or "").upper()
    rd = ROUND_WORDS.get(word) or (int(word) if word.isdigit() else None)
    return (int(m.group(1)), rd) if rd else None


def store_sightings(cur, season, rows, capture_id=None):
    """Trade rows the transactions load skipped -> gm_trade_sightings (when the table exists). rows: [{heading,
    text}]. Returns the number inserted."""
    from pipeline.transactions_parse import parse_row
    cur.execute("SELECT to_regclass('gm_trade_sightings') IS NOT NULL")
    if not cur.fetchone()[0]:
        return 0
    n = 0
    for r in rows:
        parsed = parse_row(r["text"])
        cur.execute("""INSERT INTO gm_trade_sightings (season, txn_date, raw_text, parsed, capture_id)
                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT (season, txn_date, raw_text) DO NOTHING""",
                    (season, r["heading"], r["text"], json.dumps(parsed, default=list), capture_id))
        n += cur.rowcount
    return n


def match_sighting(parsed, candidates, nick_to_abbr):
    """Pure. parsed: transactions_parse's trade dict {team, to_team, legs [{name}], assets}. candidates: EXECUTED
    proposals [{id, teams {abbr, abbr}, player_keys {full_key}}] not matched yet. Returns (status, proposal_id):
    MATCHED (same teams, every player leg in the proposal and as many players), PARTIAL (same teams, some
    players), UNMATCHED."""
    from pipeline.capture_matching import full_key
    teams = {nick_to_abbr.get(parsed.get("team")), nick_to_abbr.get(parsed.get("to_team"))} - {None}
    keys = {full_key(l["name"]) for l in parsed.get("legs") or []}
    best = ("UNMATCHED", None)
    for c in candidates:
        if teams != set(c["teams"]):
            continue
        hit = keys & c["player_keys"]
        if keys and hit == keys and len(keys) == len(c["player_keys"]):
            return "MATCHED", c["id"]
        if (hit or (not keys and not c["player_keys"])) and best[0] == "UNMATCHED":
            best = ("PARTIAL", c["id"])
    return best


def match_sightings(cur, season):
    """Link UNMATCHED/PARTIAL sightings to EXECUTED proposals (sets only match_status and proposal_id). Returns
    {MATCHED: n, PARTIAL: n, UNMATCHED: n}."""
    from pipeline.capture_matching import full_key
    cur.execute("SELECT to_regclass('gm_trade_sightings') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("SELECT name, abbreviation FROM teams WHERE abbreviation IS NOT NULL")
    nick = {r[0].upper(): r[1] for r in cur.fetchall()}
    cur.execute("""SELECT p.id, ta.abbreviation, tb.abbreviation FROM gm_trade_proposals p
                   JOIN teams ta ON ta.id = p.team_a_id JOIN teams tb ON tb.id = p.team_b_id
                   WHERE p.season = %s AND p.status = 'EXECUTED'
                     AND NOT EXISTS (SELECT 1 FROM gm_trade_sightings s WHERE s.proposal_id = p.id
                                     AND s.match_status = 'MATCHED')""", (season,))
    props = cur.fetchall()
    legs = _legs(cur, [r[0] for r in props])
    cands = [{"id": r[0], "teams": {r[1], r[2]},
              "player_keys": {full_key(l["player_name"]) for l in legs.get(r[0], []) if l["type"] == "PLAYER"}}
             for r in props]
    cur.execute("""SELECT id, parsed FROM gm_trade_sightings WHERE season = %s AND match_status <> 'MATCHED'
                   ORDER BY id""", (season,))
    counts = {"MATCHED": 0, "PARTIAL": 0, "UNMATCHED": 0}
    for sid, parsed in cur.fetchall():
        status, pid = match_sighting(parsed or {}, cands, nick)
        counts[status] += 1
        cur.execute("UPDATE gm_trade_sightings SET match_status = %s, proposal_id = %s WHERE id = %s",
                    (status, pid, sid))
        if status == "MATCHED":
            cands = [c for c in cands if c["id"] != pid]
    return counts


def resolve_sighting(cur, season, parsed):
    """A sighting's legs as engine assets for JR to check before Apply this trade: players by team + full name
    (then list-name key), picks by year/round with the sender as the original team unless the row names one.
    Returns (assets, unresolved texts)."""
    from pipeline.capture_matching import full_key, name_key
    cur.execute("SELECT id, upper(name) FROM teams WHERE abbreviation IS NOT NULL")
    nick = {r[1]: r[0] for r in cur.fetchall()}
    assets, unresolved = [], []
    for leg in parsed.get("legs") or []:
        frm, to = nick.get(leg.get("from")), nick.get(leg.get("to"))
        cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), p.player_name FROM player_seasons ps
                       JOIN players p ON p.id = ps.player_id WHERE ps.season = %s AND ps.team_id = %s""", (season, frm))
        rows = cur.fetchall()
        hits = [r[0] for r in rows if full_key(r[1]) == full_key(leg["name"])] or \
               [r[0] for r in rows if name_key(r[2] or r[1]) == name_key(leg["name"])]
        if frm and to and len(hits) == 1:
            assets.append({"type": "PLAYER", "player_id": hits[0], "from": frm, "to": to})
        else:
            unresolved.append(f"({leg.get('pos')}) {leg.get('name')}")
    for a in parsed.get("assets") or []:
        frm, to = nick.get(a.get("from")), nick.get(a.get("to"))
        pk = pick_from_text(a.get("asset"))
        if frm and to and pk:
            assets.append({"type": "PICK", "pick": (pk[0], pk[1], frm), "from": frm, "to": to})
        else:
            unresolved.append(a.get("asset") or "?")
    return assets, unresolved


def consistency_flags(cur, season):
    """Computed, never written: unmatched/partial sightings, EXECUTED trades not yet seen in a later
    transactions scan, and traded players who have moved since (roster or contract on another team)."""
    flags = []
    cur.execute("""SELECT id, txn_date, raw_text, match_status, proposal_id FROM gm_trade_sightings
                   WHERE season = %s AND match_status <> 'MATCHED' ORDER BY id""", (season,))
    for sid, date, text, status, pid in cur.fetchall():
        flags.append({"kind": "unmatched_sighting" if status == "UNMATCHED" else "partial_sighting",
                      "sighting_id": sid, "proposal_id": pid, "detail": f"{date}: {text}"})
    cur.execute("SELECT max(created_at) FROM transactions WHERE season = %s AND capture_id IS NOT NULL", (season,))
    last_scan = cur.fetchone()[0]
    cur.execute("""SELECT p.id, p.executed_at, p.headline FROM gm_trade_proposals p
                   WHERE p.season = %s AND p.status = 'EXECUTED'
                     AND NOT EXISTS (SELECT 1 FROM gm_trade_sightings s WHERE s.proposal_id = p.id
                                     AND s.match_status = 'MATCHED')""", (season,))
    for pid, at, head in cur.fetchall():
        if last_scan and at and last_scan > at:
            flags.append({"kind": "not_yet_seen", "proposal_id": pid,
                          "detail": f"#{pid} {head or ''}: no matching trade row in the transactions scan since"})
    cur.execute("""SELECT p.id, a.player_id, COALESCE(pl.full_name, pl.player_name), ta.abbreviation, ps.team_id,
                          a.to_team_id, (SELECT c.team_id FROM player_contracts c WHERE c.player_id = a.player_id
                                         AND c.season = p.season AND c.status = 'ACTIVE')
                   FROM gm_trade_proposals p JOIN gm_trade_assets a ON a.proposal_id = p.id
                   JOIN teams ta ON ta.id = a.to_team_id JOIN players pl ON pl.id = a.player_id
                   LEFT JOIN player_seasons ps ON ps.player_id = a.player_id AND ps.season = p.season
                   WHERE p.season = %s AND p.status = 'EXECUTED' AND a.asset_type = 'PLAYER'""", (season,))
    for pid, player, name, to_abbr, roster_team, to_team, contract_team in cur.fetchall():
        later = _traded_again(cur, season, player, pid)
        if later:
            continue
        if roster_team != to_team or (contract_team is not None and contract_team != roster_team):
            flags.append({"kind": "moved_since", "proposal_id": pid, "player_id": player,
                          "detail": f"{name}: traded to {to_abbr}; roster team {roster_team}, contract team "
                                    f"{contract_team}"})
    return flags


def _traded_again(cur, season, player_id, proposal_id):
    cur.execute("""SELECT 1 FROM gm_trade_assets a JOIN gm_trade_proposals p ON p.id = a.proposal_id
                   WHERE a.player_id = %s AND p.season = %s AND p.status = 'EXECUTED' AND p.id > %s LIMIT 1""",
                (player_id, season, proposal_id))
    return cur.fetchone() is not None


# -------------------------------------------------------------- actions ----
def _proposal(cur, pid):
    from engines.gm_trade_engine import read_proposals
    cur.execute("SELECT season FROM gm_trade_proposals WHERE id = %s", (pid,))
    r = cur.fetchone()
    if not r:
        raise FrontOfficeError(404, f"no proposal {pid}")
    return read_proposals(cur, r[0], ids=[pid])[0]


def approve(cur, pid):
    """PROPOSED -> APPROVED after a legality re-check (illegal now -> EXPIRED, refused)."""
    p = _proposal(cur, pid)
    if p["status"] != "PROPOSED":
        raise FrontOfficeError(409, f"proposal {pid} is {p['status']}")
    season, week = current_state(cur)
    chk = legality_check(cur, p["season"], week, p["team_a_id"], p["team_b_id"], p["assets"], ignore_proposal=pid)
    if not chk["legal"]:
        why = "no longer legal: " + "; ".join(f"{k} {v['detail']}" for k, v in chk["checks"].items() if not v["ok"])
        cur.execute("UPDATE gm_trade_proposals SET status = 'EXPIRED', status_reason = %s WHERE id = %s", (why, pid))
        raise FrontOfficeError(409, why)
    cur.execute("UPDATE gm_trade_proposals SET status = 'APPROVED', approved_at = now() WHERE id = %s", (pid,))
    return {"id": pid, "status": "APPROVED", "legality": chk}


def reject(cur, pid, reason=None):
    p = _proposal(cur, pid)
    if p["status"] not in ("PROPOSED", "APPROVED"):
        raise FrontOfficeError(409, f"proposal {pid} is {p['status']}")
    cur.execute("UPDATE gm_trade_proposals SET status = 'REJECTED', status_reason = %s WHERE id = %s",
                (reason or "rejected by JR", pid))
    return {"id": pid, "status": "REJECTED"}


def could_not_make(cur, pid, reason=None):
    """Madden refused the approved trade: back to EXPIRED with the reason."""
    p = _proposal(cur, pid)
    if p["status"] not in ("PROPOSED", "APPROVED"):
        raise FrontOfficeError(409, f"proposal {pid} is {p['status']}")
    cur.execute("UPDATE gm_trade_proposals SET status = 'EXPIRED', status_reason = %s WHERE id = %s",
                (f"couldn't make it: {reason or 'Madden refused'}", pid))
    return {"id": pid, "status": "EXPIRED"}


def _execute(cur, proposal, executed_as):
    from pipeline.trade_moves import TradeError, execute_trade, plan_trade_ops, read_state
    state = read_state(cur, proposal["season"], proposal)
    try:
        ops, warnings = plan_trade_ops(proposal, state, executed_as)
    except TradeError as e:
        raise FrontOfficeError(400, str(e))
    done = execute_trade(cur, ops, proposal["season"])
    return {"warnings": warnings, **done}


def confirm(cur, pid):
    """"Made it": JR made the proposal's trade in Madden. Legality is re-checked and reported, never blocking
    (Madden is the truth once the trade is made). An EXPIRED proposal he already made can still be confirmed."""
    p = _proposal(cur, pid)
    if p["status"] not in ("APPROVED", "PROPOSED", "EXPIRED"):
        raise FrontOfficeError(409, f"proposal {pid} is {p['status']}")
    season, week = current_state(cur)
    chk = legality_check(cur, p["season"], week, p["team_a_id"], p["team_b_id"], p["assets"], ignore_proposal=pid)
    res = _execute(cur, dict(p, week=week if p["season"] == season else p["week"]), "PROPOSED")
    return {"id": pid, "status": "EXECUTED", "legality": chk, **res}


def record(cur, team_a, team_b, assets, proposal_id=None, note=None):
    """"Record a trade" (a trade JR made himself, origin USER) or "Made a different trade" on a proposal's card
    (its assets are replaced by JR's and it executes as JR_EDITED). assets: Front Office JSON. Legality is a
    warning, never a block."""
    season, week = current_state(cur)
    ids = _team_ids(cur)
    a, b = _team(ids, team_a), _team(ids, team_b)
    eng = to_engine_assets(cur, assets)
    for x in eng:
        if {x["from"], x["to"]} != {a, b}:
            raise FrontOfficeError(400, "every leg must go between the two teams")
    chk = legality_check(cur, season, week, a, b, eng, ignore_proposal=proposal_id)
    executed_as = "JR_EDITED"
    pid = None
    if proposal_id is not None:
        p = _proposal(cur, proposal_id)
        if p["status"] not in ("APPROVED", "PROPOSED", "EXPIRED"):
            raise FrontOfficeError(409, f"proposal {proposal_id} is {p['status']}")
        cur.execute("DELETE FROM gm_trade_assets WHERE proposal_id = %s", (proposal_id,))
        pid = proposal_id
        cur.execute("UPDATE gm_trade_proposals SET team_a_id = %s, team_b_id = %s, status_reason = %s WHERE id = %s",
                    (a, b, note or "JR made a different trade", pid))
    pid = _store_user(cur, season, week, a, b, eng, chk, pid, note, executed_as)
    p = _proposal(cur, pid)
    res = _execute(cur, dict(p, week=week), executed_as)
    return {"id": pid, "status": "EXECUTED", "legality": chk, **res}


def _store_user(cur, season, week, a, b, assets, chk, pid, note, executed_as, trigger="USER"):
    """A USER proposal row (or new assets on an existing one) for a trade JR made. Returns its id."""
    from engines import gm_trade_engine as ge
    window = ge.window_for(week) or "IN_SEASON"
    if pid is None:
        h = "user:" + ge.deal_hash(a, b, assets) + f":{week}"
        cur.execute("""INSERT INTO gm_trade_proposals (season, week, trade_window, trigger_kind, origin, expires_week,
                           team_a_id, team_b_id, legality, headline, status, status_reason, rumor_ok, deal_hash,
                           flags)
                       VALUES (%s, %s, %s, %s, 'USER', %s, %s, %s, %s, %s, 'PROPOSED', %s, false, %s, %s)
                       ON CONFLICT (season, deal_hash) DO NOTHING RETURNING id""",
                    (season, week, window, trigger, week, a, b, json.dumps(chk["checks"]), None, note,
                     h, [] if chk["legal"] else ["legality_warning"]))
        row = cur.fetchone()
        if not row:
            raise FrontOfficeError(409, "this trade is already recorded")
        pid = row[0]
    else:
        cur.execute("UPDATE gm_trade_proposals SET legality = %s WHERE id = %s", (json.dumps(chk["checks"]), pid))
    for x in assets:
        crow = None
        if x["type"] == "PLAYER":
            cur.execute("""SELECT c.id, y.cap_hit FROM player_contracts c LEFT JOIN player_contract_years y
                           ON y.contract_id = c.id AND y.year = c.season
                           WHERE c.player_id = %s AND c.season = %s AND c.status = 'ACTIVE'""",
                        (x["player_id"], season))
            crow = cur.fetchone()
        pick = x.get("pick") or (None, None, None)
        cur.execute("""INSERT INTO gm_trade_assets (proposal_id, from_team_id, to_team_id, asset_type, player_id,
                           contract_id, pick_draft_season, pick_round, pick_original_team_id, cap_hit_now)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (pid, x["from"], x["to"], x["type"], x.get("player_id"), crow[0] if crow else None,
                     pick[0], pick[1], pick[2], crow[1] if crow else None))
    return pid


def apply_sighting(cur, sighting_id, assets=None):
    """"Apply this trade": an unmatched trade row Madden logged becomes an EXECUTED USER proposal and its moves are
    made (the same ops as a confirmed proposal). assets: JR's checked legs (Front Office JSON); without them the
    resolved legs are used, and any unresolved leg refuses the apply."""
    cur.execute("SELECT season, parsed, match_status, raw_text FROM gm_trade_sightings WHERE id = %s", (sighting_id,))
    r = cur.fetchone()
    if not r:
        raise FrontOfficeError(404, f"no sighting {sighting_id}")
    season, parsed, status, text = r
    if status == "MATCHED":
        raise FrontOfficeError(409, "this sighting already matches a trade")
    if assets:
        eng = to_engine_assets(cur, assets)
    else:
        eng, unresolved = resolve_sighting(cur, season, parsed or {})
        if unresolved or not eng:
            raise FrontOfficeError(400, "couldn't resolve: " + (", ".join(unresolved) or "no legs") +
                                   " (send the assets)")
    teams = sorted({x["from"] for x in eng} | {x["to"] for x in eng})
    if len(teams) != 2:
        raise FrontOfficeError(400, "a trade is between exactly two teams")
    cur_season, week = current_state(cur)
    a = eng[0]["from"]
    b = teams[1] if teams[0] == a else teams[0]
    chk = legality_check(cur, season, week, a, b, eng)
    pid = _store_user(cur, season, week, a, b, eng, chk, None, f"applied from the scan: {text}", "SIGHTING")
    p = _proposal(cur, pid)
    res = _execute(cur, dict(p, week=week), "SIGHTING")
    cur.execute("UPDATE gm_trade_sightings SET match_status = 'MATCHED', proposal_id = %s WHERE id = %s",
                (pid, sighting_id))
    return {"id": pid, "status": "EXECUTED", "legality": chk, **res}


def set_team_control(cur, team_id, user_controlled=None, suggestions_on=None):
    """JR runs this team (its GM only advises) and/or switches its GM's suggestions on or off (§7 Q1)."""
    cur.execute("""INSERT INTO gm_team_settings (team_id, user_controlled, suggestions_on, updated_at)
                   VALUES (%s, COALESCE(%s, false), COALESCE(%s, true), now())
                   ON CONFLICT (team_id) DO UPDATE SET
                       user_controlled = COALESCE(%s, gm_team_settings.user_controlled),
                       suggestions_on = COALESCE(%s, gm_team_settings.suggestions_on), updated_at = now()
                   RETURNING team_id, user_controlled, suggestions_on""",
                (team_id, user_controlled, suggestions_on, user_controlled, suggestions_on))
    r = cur.fetchone()
    return {"team_id": r[0], "user_controlled": r[1], "suggestions_on": r[2]}


# ------------------------------------------------------------ #debug state ----
def front_office_state(cur, season=None):
    """The #debug Front Office card: window, deadline, budget pace, 32 directions with needs and GMs, proposals
    (assets, cap effects, legality, steps, status), flags, unmatched sightings."""
    from engines import gm_trade_engine as ge
    cur_season, week = current_state(cur)
    season = season or cur_season
    if not installed(cur):
        return {"season": season, "installed": False}
    window = ge.window_for(week) if season == cur_season else None
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'EXECUTED' AND trade_window = 'IN_SEASON'),
                          count(*) FILTER (WHERE status IN ('EXECUTED', 'REJECTED') AND trade_window = 'IN_SEASON'),
                          count(*) FILTER (WHERE status IN ('PROPOSED', 'APPROVED')),
                          min(week) FILTER (WHERE origin = 'ENGINE' AND trade_window = 'IN_SEASON'),
                          count(*) FILTER (WHERE status = 'EXECUTED' AND trade_window <> 'IN_SEASON')
                   FROM gm_trade_proposals WHERE season = %s""", (season,))
    ex, dec, open_n, first, ex_off = cur.fetchone()
    first = min(first or week, week) if window == "IN_SEASON" else (first or 1)
    budget = {"executed": ex or 0, "decided": dec or 0, "open": open_n or 0, "deadline_week": ge.TRADE_DEADLINE_WEEK,
              "target": round(ge.scaled_target(ge.IN_SEASON_TARGET, first), 1), "season_target": ge.IN_SEASON_TARGET,
              "p_exec": round(ge.p_exec(ex or 0, dec or 0), 3), "max_open": ge.MAX_OPEN,
              "max_new_per_week": ge.MAX_NEW_PER_WEEK, "offseason_target": ge.offseason_target(season),
              "offseason_executed": ex_off or 0}
    cur.execute("""SELECT t.id, t.abbreviation, t.city, t.name, g.gm_name, g.aggressiveness, s.direction,
                          s.direction_score, s.needs, s.urgent_need, COALESCE(st.user_controlled, false),
                          COALESCE(st.suggestions_on, true), s.fo_quality
                   FROM teams t LEFT JOIN gm_profiles g ON g.team_id = t.id AND g.end_season IS NULL
                   LEFT JOIN gm_team_seasons s ON s.team_id = t.id AND s.season = %s
                   LEFT JOIN gm_team_settings st ON st.team_id = t.id
                   WHERE t.abbreviation IS NOT NULL
                   ORDER BY s.direction_score DESC NULLS LAST, t.abbreviation""", (season,))
    teams = [{"team_id": r[0], "abbr": r[1], "team": f"{r[2]} {r[3]}", "gm_name": r[4],
              "aggressiveness": float(r[5]) if r[5] is not None else None, "direction": r[6],
              "direction_score": float(r[7]) if r[7] is not None else None, "needs": (r[8] or [])[:4],
              "urgent_need": r[9], "user_controlled": r[10], "suggestions_on": r[11],
              "fo_quality": float(r[12]) if r[12] is not None else None} for r in cur.fetchall()]
    control = {t["team_id"]: t["user_controlled"] for t in teams}
    names = {t["team_id"]: t["team"] for t in teams}
    props = ge.read_proposals(cur, season)
    legs = _legs(cur, [p["id"] for p in props])
    cur.execute("""SELECT proposal_id, match_status FROM gm_trade_sightings WHERE season = %s
                   AND proposal_id IS NOT NULL""", (season,))
    seen = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT max(created_at) FROM transactions WHERE season = %s AND capture_id IS NOT NULL", (season,))
    last_scan = cur.fetchone()[0]
    abbr = {t["team_id"]: t["abbr"] for t in teams}
    out_props = []
    for p in sorted(props, key=lambda p: (-p["week"], -p["id"])):
        L = legs.get(p["id"], [])
        chip = None
        if p["status"] == "APPROVED":
            chip = "Awaiting confirm"
        elif p["status"] == "EXECUTED":
            chip = ("Seen in Madden log" if seen.get(p["id"]) == "MATCHED" else
                    "Not yet seen" if last_scan and p["executed_at"] and last_scan > p["executed_at"] else "Executed")
        out_props.append({
            "id": p["id"], "week": p["week"], "status": p["status"], "status_reason": p["status_reason"],
            "chip": chip, "origin": p["origin"], "trigger": p["trigger_kind"], "window": p["trade_window"],
            "team_a": abbr.get(p["team_a_id"]), "team_b": abbr.get(p["team_b_id"]),
            "team_a_id": p["team_a_id"], "team_b_id": p["team_b_id"],
            "headline": p["headline"], "rationale_a": p["rationale_a"], "rationale_b": p["rationale_b"],
            "rationale_source": p["rationale_source"], "value_a": p["value_a"], "value_b": p["value_b"],
            "league_value_a": p["league_value_a"], "league_value_b": p["league_value_b"],
            "cap": {"a": {"before": p["cap_space_before_a"], "after": p["cap_space_after_a"], "dead": p["dead_money_a"],
                          "derived": p["cap_derived_a"]},
                    "b": {"before": p["cap_space_before_b"], "after": p["cap_space_after_b"], "dead": p["dead_money_b"],
                          "derived": p["cap_derived_b"]}},
            "roster_after": {"a": p["roster_after_a"], "b": p["roster_after_b"]},
            "legality": p["legality"], "flags": p["flags"], "rumor_ok": p["rumor_ok"], "legs": L,
            "steps": steps_for(p, L, names, control), "executed_as": p["executed_as"],
            "executed_at": p["executed_at"], "approved_at": p["approved_at"], "created_at": p["created_at"]})
    cur.execute("""SELECT id, txn_date, raw_text, parsed, match_status, proposal_id FROM gm_trade_sightings
                   WHERE season = %s ORDER BY id DESC LIMIT 50""", (season,))
    sightings = []
    for sid, date, text, parsed, status, pid in cur.fetchall():
        item = {"id": sid, "txn_date": date, "raw_text": text, "match_status": status, "proposal_id": pid}
        if status != "MATCHED":
            assets, unresolved = resolve_sighting(cur, season, parsed or {})
            item["resolved"] = [_json_asset(x, abbr) for x in assets]
            item["unresolved"] = unresolved
        sightings.append(item)
    return {"season": season, "installed": True, "week": week, "window": window, "budget": budget, "teams": teams,
            "proposals": out_props, "flags": consistency_flags(cur, season), "sightings": sightings}


def _json_asset(x, abbr):
    if x["type"] == "PLAYER":
        return {"type": "PLAYER", "player_id": x["player_id"], "from": abbr.get(x["from"]), "to": abbr.get(x["to"])}
    ds, rd, orig = x["pick"]
    return {"type": "PICK", "draft_season": ds, "round": rd, "original_team": abbr.get(orig),
            "from": abbr.get(x["from"]), "to": abbr.get(x["to"])}


def steps_for(p, legs, names, control):
    """steps_text for a stored proposal (names: {team_id: 'City Name'}, control: {team_id: user_controlled})."""
    a, b = p["team_a_id"], p["team_b_id"]
    lab = lambda l: l.get("label") or f"{l.get('position')} {l.get('player_name')} {l.get('ovr') or ''}".strip()
    send = [lab(l) for l in legs if l["from_team_id"] == a]
    get = [lab(l) for l in legs if l["from_team_id"] == b]
    s1 = (f"Options > User Teams: you already control the {names.get(a)}." if control.get(a) else
          f"Options > User Teams: switch the {names.get(a)} to user control.")
    return [s1,
            f"Trade with the {names.get(b)}. Send: {', '.join(send) or 'nothing'}. Receive: {', '.join(get) or 'nothing'}.",
            f"Submit the offer, then switch to the {names.get(b)} to accept it" +
            ("." if control.get(b) else " (switch them to user control for this, or turn CPU trades on just for this trade)."),
            "If Madden refuses, click Couldn't make it.",
            "Switch any team you switched back to CPU (and CPU trades back off).",
            "Click Made it."]
