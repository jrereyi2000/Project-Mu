"""Media voters (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.5, D4-D5): a panel of 20 national-writer
personas casts seeded ballots for the six major awards and the All-Pro teams. No model calls.

  AWARDS, POINTS = (10, 5, 3, 2, 1), VOTER_ARCHETYPES (20 keys of newsroom/config/agent_personas.json)
  load_voters(path=None) -> [voter]            {key, traits, awards_view} from the persona file
  voter_weights(voter, award) -> {metric: w}   the award's neutral blend leaned by the voter's traits and views
  ballot(voter, cands, award, seed) -> [ids]   his top five; seed is the season
  tally(ballots) -> {award: [{id, points, first_place}]}   most points first; ties to more first-place votes
  all_pro_ballot(voter, cands_by_slot, seed) -> {slot: [ids up to the quota]}
  all_pro_tally(ballots, cands_by_slot) -> {"first": {slot: [{id, votes}]}, "second": {...}}
  store_results(cur, season, tallies, cands_by_award, ballots)   awards rows (winners) + award_ballots rows
  store_all_pro(cur, season, teams, cands_by_slot, ballots)      62 awards rows + award_ballots ('ALL_PRO:<slot>')
  run_awards(cur, season) / run_all_pro(cur, season)              candidates -> ballots -> tallies -> storage

Leans (review-only estimates): ANALYTICS_BELIEVER up-weights efficiency; OLD_SCHOOL wins and volume (production);
HISTORIAN adds the record-pace bonus; a view tagged AWARDS adds a lean toward winning teams. Each ballot adds seeded
noise (sha256(['ballot-v1', season, voter, award])) to every candidate's leaned score. The award_ballots table comes
from the owners/scouts/voters migration (2027_02_02, plan U6). Nothing here is wired into the newsroom run yet: the
`media_voters` switchboard feature (activation plan) decides when the Week 19 and Week 22 articles report stored
ballots instead of writing the winners themselves.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random

from engines import award_candidates as ac
from newsroom.all_pro import ALL_SLOT_QUOTAS

AWARDS = ac.AWARDS
POINTS = (10, 5, 3, 2, 1)
BALLOT_VERSION = "ballot-v1"
NOISE = 0.35                     # sd of the per-candidate ballot noise, in metric z units
ALL_PRO_PREFIX = "ALL_PRO:"
AWARD_NAMES = {"MVP": "MVP", "OPOY": "OPOY", "DPOY": "DPOY", "OROY": "OROY", "DROY": "DROY", "COTY": "COTY"}
TEAM_LEVELS = ("First Team All-Pro", "Second Team All-Pro")
VOTER_ARCHETYPES = ("ESPN", "THE_ATHLETIC", "SCHEFTER", "SKIP_BAYLESS", "BARNWELL", "SANDO", "JOYNER", "ROSENTHAL",
                    "CHADIHA", "STEPHEN_A", "SIMMONS", "RAPOPORT", "SHANNON", "JJ_WATT", "COWHERD", "YATES",
                    "WINGO", "KIMES", "ORLOVSKY", "EISEN")
PERSONAS_PATH = Path(__file__).resolve().parent / "config" / "agent_personas.json"

LEAN_ANALYTICS = {"efficiency": 0.15, "production": -0.05}
LEAN_OLD_SCHOOL = {"wins": 0.15, "production": 0.05, "efficiency": -0.10}
LEAN_HISTORIAN = {"record_pace": 0.15}
LEAN_AWARDS_VIEW = {"wins": 0.05}


def load_voters(path=None, keys=VOTER_ARCHETYPES) -> list:
    data = json.loads(Path(path or PERSONAS_PATH).read_text()).get("archetypes", {})
    out = []
    for k in keys:
        a = data.get(k) or {}
        views = a.get("views") or []
        out.append({"key": k, "traits": list(a.get("traits") or []),
                    "awards_view": any("AWARDS" in (v.get("when") or []) for v in views if isinstance(v, dict))})
    return out


def voter_weights(voter, award, base=None) -> dict:
    w = dict(base if base is not None else ac.AWARD_WEIGHTS.get(award, {"production": 1.0}))
    traits = set(voter.get("traits") or [])
    leans = []
    if award != "COTY":
        if "ANALYTICS_BELIEVER" in traits:
            leans.append(LEAN_ANALYTICS)
        if "OLD_SCHOOL" in traits:
            leans.append(LEAN_OLD_SCHOOL)
        if "HISTORIAN" in traits:
            leans.append(LEAN_HISTORIAN)
    if voter.get("awards_view"):
        leans.append(LEAN_AWARDS_VIEW)
    for lean in leans:
        for k, v in lean.items():
            w[k] = max(0.0, w.get(k, 0.0) + v)
    return w


def _rng(season, voter_key, award):
    digest = hashlib.sha256(json.dumps([BALLOT_VERSION, season, voter_key, award]).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _scores(voter, cands, award, seed, base_weights=None, extra=None):
    w = voter_weights(voter, award, base_weights)
    total = sum(w.values()) or 1.0
    rng = _rng(seed, voter["key"], award)
    out = {}
    for c in sorted(cands, key=lambda c: c["id"]):
        s = sum(wt * c["metrics"].get(k, 0.0) for k, wt in w.items()) / total
        s += (extra(c) if extra else 0.0) + rng.gauss(0.0, NOISE)
        out[c["id"]] = s
    return out


def ballot(voter, cands, award, seed) -> list:
    """The voter's top five candidate ids for the award (fewer if fewer candidates)."""
    extra = None
    if award == "MVP":
        extra = lambda c: ac.MVP_POSITION.get(ac.position_group(c.get("position")), 0.0)
    scores = _scores(voter, cands, award, seed, extra=extra)
    return sorted(scores, key=lambda i: (-scores[i], i))[:len(POINTS)]


def tally(ballots) -> dict:
    """ballots: [{voter, award, ranking}] -> {award: [{id, points, first_place}]}, winner first (most points, then
    more first-place votes, then the lower id)."""
    acc = {}
    for b in ballots:
        per = acc.setdefault(b["award"], {})
        for place, cid in enumerate(b["ranking"][:len(POINTS)]):
            row = per.setdefault(cid, {"id": cid, "points": 0, "first_place": 0})
            row["points"] += POINTS[place]
            row["first_place"] += 1 if place == 0 else 0
    return {a: sorted(rows.values(), key=lambda r: (-r["points"], -r["first_place"], r["id"]))
            for a, rows in acc.items()}


# ------------------------------------------------------------------------------------------------ All-Pro ---

def all_pro_ballot(voter, cands_by_slot, seed) -> dict:
    """Every First-Team slot filled to its quota from that slot's candidates, leaned and noised; a player is named
    once on a ballot (at the first slot, in quota order, whose top picks include him)."""
    used, out = set(), {}
    for slot, quota in ALL_SLOT_QUOTAS.items():
        cands = cands_by_slot.get(slot) or []
        scores = _scores(voter, cands, ALL_PRO_PREFIX + slot, seed, base_weights=ac.SLOT_DEFAULT_WEIGHTS
                         if ac.SLOT_RATING_WEIGHT.get(slot, 0.1) < 0.7 else
                         {"production": 1.0 - ac.SLOT_RATING_WEIGHT[slot], "rating": ac.SLOT_RATING_WEIGHT[slot]})
        picks = [i for i in sorted(scores, key=lambda i: (-scores[i], i)) if i not in used][:quota]
        used.update(picks)
        out[slot] = picks
    return out


def all_pro_tally(ballots, cands_by_slot) -> dict:
    """ballots: [{voter, slots: {slot: [ids]}}]. Per slot, the most votes up to the quota make the First Team and the
    next up to the quota the Second Team; a player is placed once, at his best-voted slot; ties go to the higher
    base score. Slots short of voted players fill from the remaining candidates by base score (0 votes)."""
    votes = {}
    for b in ballots:
        for slot, ids in b["slots"].items():
            for i in ids:
                votes[(slot, i)] = votes.get((slot, i), 0) + 1
    base = {(slot, c["id"]): c["base_score"] for slot, cs in cands_by_slot.items() for c in cs}
    entries = sorted(votes, key=lambda e: (-votes[e], -base.get(e, 0.0), e[1], e[0]))
    first = {s: [] for s in ALL_SLOT_QUOTAS}
    second = {s: [] for s in ALL_SLOT_QUOTAS}
    placed = set()

    def place(slot, pid, n):
        if pid in placed or slot not in first:
            return
        for team in (first, second):
            if len(team[slot]) < ALL_SLOT_QUOTAS[slot]:
                team[slot].append({"id": pid, "votes": n})
                placed.add(pid)
                return

    for slot, pid in entries:
        place(slot, pid, votes[(slot, pid)])
    for slot in ALL_SLOT_QUOTAS:
        for c in sorted(cands_by_slot.get(slot) or [], key=lambda c: (-c["base_score"], c["id"])):
            if len(second[slot]) >= ALL_SLOT_QUOTAS[slot]:
                break
            place(slot, c["id"], 0)
    return {"first": first, "second": second}


# ------------------------------------------------------------------------------------------------ storage ---

def _store_ballot_rows(cur, season, rows):
    cur.executemany("""
        INSERT INTO award_ballots (season, award, voter, ranking, ballot_version)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (season, award, voter) DO UPDATE SET ranking = EXCLUDED.ranking,
            ballot_version = EXCLUDED.ballot_version, created_at = now()
    """, rows)


def store_results(cur, season, tallies, cands_by_award, ballots) -> int:
    """Replaces the season's six winners in `awards` (source default) and writes every ballot. Returns winners
    written."""
    cur.execute("DELETE FROM awards WHERE season = %s AND award_type IN ('MVP','OPOY','OROY','DPOY','DROY','COTY')",
                (season,))
    n = 0
    for award in AWARDS:
        rows = tallies.get(award) or []
        if not rows:
            continue
        win = rows[0]["id"]
        cand = next((c for c in cands_by_award.get(award) or [] if c["id"] == win), None)
        if cand is None:
            continue
        if award == "COTY":
            cur.execute("INSERT INTO awards (season, award_type, coach_id, coach_name, team_id) "
                        "VALUES (%s, 'COTY', %s, %s, %s)", (season, win, cand["name"], cand["team_id"]))
        else:
            cur.execute("INSERT INTO awards (season, award_type, player_id, player_name, team_id, position) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (season, award, win, cand["name"], cand["team_id"], cand["position"]))
        n += 1
    _store_ballot_rows(cur, season, [(season, b["award"], b["voter"], json.dumps(b["ranking"]), BALLOT_VERSION)
                                     for b in ballots])
    return n


def store_all_pro(cur, season, teams, cands_by_slot, ballots) -> int:
    """Replaces the season's First and Second Team All-Pro rows (position = the slot) and writes every voter's
    ballot per slot (award 'ALL_PRO:<slot>'). Returns awards rows written."""
    names = {c["id"]: c for cs in cands_by_slot.values() for c in cs}
    cur.execute("DELETE FROM awards WHERE season = %s AND award_type IN ('First Team All-Pro', "
                "'Second Team All-Pro')", (season,))
    rows = []
    for level, team in zip(TEAM_LEVELS, (teams["first"], teams["second"])):
        for slot in ALL_SLOT_QUOTAS:
            for e in team.get(slot) or []:
                c = names.get(e["id"], {})
                rows.append((season, level, e["id"], c.get("team_id"), c.get("name"), slot))
    cur.executemany("INSERT INTO awards (season, award_type, player_id, team_id, player_name, position) "
                    "VALUES (%s, %s, %s, %s, %s, %s)", rows)
    _store_ballot_rows(cur, season, [(season, ALL_PRO_PREFIX + slot, b["voter"], json.dumps(ids), BALLOT_VERSION)
                                     for b in ballots for slot, ids in b["slots"].items()])
    return len(rows)


# --------------------------------------------------------------------------------------------- run helpers ---

def run_awards(cur, season, voters=None) -> dict:
    """Candidates -> ballots -> tallies -> storage for the six awards. Returns {tallies, candidates, ballots}."""
    voters = voters or load_voters()
    cands = {a: ac.candidates(cur, season, a) for a in AWARDS}
    ballots = [{"voter": v["key"], "award": a, "ranking": ballot(v, cands[a], a, season)}
               for a in AWARDS if cands[a] for v in voters]
    tallies = tally(ballots)
    store_results(cur, season, tallies, cands, ballots)
    return {"tallies": tallies, "candidates": cands, "ballots": ballots}


def run_all_pro(cur, season, voters=None) -> dict:
    voters = voters or load_voters()
    cands = ac.all_pro_candidates(cur, season)
    ballots = [{"voter": v["key"], "slots": all_pro_ballot(v, cands, season)} for v in voters]
    teams = all_pro_tally(ballots, cands)
    store_all_pro(cur, season, teams, cands, ballots)
    return {"teams": teams, "candidates": cands, "ballots": ballots}


def results_facts(tallies, cands_by_award) -> dict:
    """The Week 22 article's facts: {award: {winner, team_id, position, points, first_place, top5: [...]}}."""
    out = {}
    for award, rows in tallies.items():
        names = {c["id"]: c for c in cands_by_award.get(award) or []}
        top = [{"name": names.get(r["id"], {}).get("name"), "points": r["points"], "first_place": r["first_place"]}
               for r in rows[:5]]
        if rows:
            w = names.get(rows[0]["id"], {})
            out[award] = {"winner": w.get("name"), "team_id": w.get("team_id"), "position": w.get("position"),
                          "points": rows[0]["points"], "first_place": rows[0]["first_place"], "top5": top}
    return out


def all_pro_facts(teams, cands_by_slot) -> dict:
    """The Week 19 article's facts: {level: {slot: [{name, team_id, votes}]}}."""
    names = {c["id"]: c for cs in cands_by_slot.values() for c in cs}
    return {level: {slot: [{"name": names.get(e["id"], {}).get("name"), "team_id": names.get(e["id"], {}).get(
        "team_id"), "votes": e["votes"]} for e in team.get(slot) or []] for slot in ALL_SLOT_QUOTAS}
            for level, team in zip(TEAM_LEVELS, (teams["first"], teams["second"]))}
