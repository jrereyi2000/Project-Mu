"""Writer relationships: each writer's favourites and grudges toward players, coaches and teams
(docs/done/AGENT_WIRE_VOICE_PLAN.md §2.1-§2.2; PROJECT_SPEC §3.3p, §7.7).

Rows live in persona_relationships with persona_type = 'writer', persona_id = the writer's person_key (one person can
run several accounts: Skip Bayless and his tweet account share 'Skip_Bayless'), target_type player | coach | team and
target_id the id as text. trust runs -1..1; evidence keeps the last EVIDENCE_KEEP events that moved it.

  seed_writer_relationships  deterministic starting biases per person (fans, bandwagon, beat reporters, nationals)
  apply_take                 a post or article leaned on a subject: trust += GROW_STEP * lean
  apply_graded               graded picks and season calls: EARN_RIGHT / EARN_BURNED on the backed subject
  decay                      season rollover: untouched rows *= DECAY
  relationship_block         plain sentences for post and article prompts

Every function takes a cursor and never commits. Reads and writes run inside a savepoint, so a missing table (an
older database) returns 0 / "" without aborting the caller's transaction.
"""
import hashlib
import json
import os
import random

GROW_STEP = 0.04
EARN_RIGHT = 0.03
EARN_BURNED = -0.06
DECAY = 0.8
STRONG = 0.5            # |trust| at which a take-driven row gains FAVOURITE / GRUDGE
SHOWN_MIN = 0.2         # non-subject rows weaker than this stay out of the prompt block
EVIDENCE_KEEP = 5
APPLIED_REFS_KEEP = 200  # graded refs remembered per row; a forced rerun never reapplies one
TOP_PLAYERS = 60
DECAY_FLOOR = 0.1       # OWN_TEAM / RIVAL rows never decay below this magnitude

TAGS = ("FAVOURITE", "GRUDGE", "OWN_TEAM", "RIVAL", "FORMER_TEAM", "BELIEVER", "DOUBTER")
TARGET_TYPES = ("player", "coach", "team")

FAN = "DIE_HARD_FAN"
BANDWAGON = "BANDWAGON_FAN"
BEAT = "LOCAL_BEAT_REPORTER"
FORMER_PLAYER = "FORMER_PLAYER"
# No seeded favourites: a panel of several voices, and the two label-first national accounts.
NO_SEED_ARCHETYPES = {"ROUNDTABLE_PANEL", "AGGREGATOR", "STATS_DESK"}
# Season calls that bet against their subject (a right call adds DOUBTER and evidence, no trust change).
AGAINST_CALLS = {"COACH_FIRED", "MISSES_PLAYOFFS"}

# Mirror of social_desk.FORMER_TEAM_SLANT, used only if that module can't be imported.
_FORMER_TEAM_SLANT_FALLBACK = {"OCHOCINCO": "CIN", "SHANNON": "DEN", "JJ_WATT": "HOU", "RG3": "WAS", "CLARK": "PIT",
                               "MCAFEE": "IND"}
PERSONAS_PATH = os.path.join(os.path.dirname(__file__), "config", "agent_personas.json")


# ---------------------------------------------------------------- helpers

def _guarded(cur, fn, default=None):
    """Runs fn inside a savepoint; on any database error rolls back to it and returns default."""
    try:
        cur.execute("SAVEPOINT rel_ctx")
    except Exception:
        try:
            return fn()
        except Exception:
            return default
    try:
        result = fn()
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT rel_ctx")
        return default
    cur.execute("RELEASE SAVEPOINT rel_ctx")
    return result


def _rng(key: str) -> random.Random:
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest(), 16))


def _clamp(x: float) -> float:
    return round(max(-1.0, min(1.0, x)), 2)


def _former_team_slant() -> dict:
    try:
        from newsroom.social_desk import FORMER_TEAM_SLANT
        return dict(FORMER_TEAM_SLANT)
    except Exception:
        return dict(_FORMER_TEAM_SLANT_FALLBACK)


def _archetype_traits(path: str = PERSONAS_PATH) -> dict:
    try:
        with open(path) as f:
            data = json.load(f).get("archetypes", {})
        return {k: list(v.get("traits") or []) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def has_person_key(cur) -> bool:
    def q():
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'agent_memories' "
                    "AND column_name = 'person_key' LIMIT 1;")
        return cur.fetchone() is not None
    return bool(_guarded(cur, q, False))


def _tables_ready(cur) -> bool:
    def q():
        cur.execute("SELECT to_regclass('public.persona_relationships') IS NOT NULL AND EXISTS ("
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'persona_relationships' "
                    "AND column_name = 'evidence');")
        row = cur.fetchone()
        return bool(row and row[0])
    return bool(_guarded(cur, q, False))


# ---------------------------------------------------------------- person key

def person_key(cur, agent_id: str) -> str:
    """The person behind an account (agent_memories.person_key); the agent_id itself when unset or unknown."""
    if not agent_id or not has_person_key(cur):
        return agent_id

    def q():
        cur.execute("SELECT person_key FROM agent_memories WHERE agent_id = %s;", (agent_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else agent_id
    return _guarded(cur, q, agent_id)


def person_agent_ids(cur, agent_id: str) -> list:
    """Every account of the person behind agent_id, first account (lowest agent_memories.id) first;
    [agent_id] when the column is missing or the account is unknown."""
    if not agent_id or not has_person_key(cur):
        return [agent_id]

    def q():
        cur.execute("""SELECT agent_id FROM agent_memories
                       WHERE COALESCE(person_key, agent_id) =
                             (SELECT COALESCE(person_key, agent_id) FROM agent_memories WHERE agent_id = %s)
                       ORDER BY id;""", (agent_id,))
        ids = [r[0] for r in cur.fetchall()]
        return ids or [agent_id]
    ids = _guarded(cur, q, [agent_id]) or [agent_id]
    if agent_id not in ids:
        ids.append(agent_id)
    return ids


# ---------------------------------------------------------------- seeding (pure planning)

def _people(accounts: list) -> list:
    """accounts [{agent_id, archetype, team_id, person_key}] in agent_memories.id order -> one entry per person
    (the first account's archetype and team)."""
    seen, out = set(), []
    for a in accounts:
        pk = a.get("person_key") or a["agent_id"]
        if pk in seen:
            continue
        seen.add(pk)
        out.append({**a, "person_key": pk})
    return out


def _row(target_type, target_id, trust, tags, note=""):
    return {"target_type": target_type, "target_id": str(target_id), "trust": _clamp(trust), "tags": list(tags),
            "note": note}


def _weighted_pick(rng, items, weights, n, exclude=()):
    pool = [(i, w) for i, w in zip(items, weights) if i not in exclude and w > 0]
    out = []
    while pool and len(out) < n:
        total = sum(w for _, w in pool)
        x, acc = rng.random() * total, 0.0
        for k, (item, w) in enumerate(pool):
            acc += w
            if x <= acc:
                out.append(item)
                pool.pop(k)
                break
        else:
            out.append(pool.pop()[0])
    return out


def _rival(rng, team_id, league):
    """Same-division rival: the one that beat this team most recently (stored games), else a seeded pick."""
    div = league["teams"].get(team_id, {}).get("division")
    opponents = sorted(t for t, info in league["teams"].items() if t != team_id and div and info.get("division") == div)
    if not opponents:
        return None
    losses = [g for g in league.get("losses", []) if g["loser"] == team_id and g["winner"] in opponents]
    if losses:
        return max(losses, key=lambda g: (g["season"], g["week"]))["winner"]
    return rng.choice(opponents)


def _team_stars(league, team_id, n):
    return [p["id"] for p in sorted((p for p in league["players"] if p["team_id"] == team_id),
                                    key=lambda p: (-(p["ovr"] or 0), p["id"]))[:n]]


def _plan_fan(rng, team_id, league):
    rows = [_row("team", team_id, 0.8, ["OWN_TEAM"])]
    rival = _rival(rng, team_id, league)
    if rival:
        rows.append(_row("team", rival, -0.6, ["RIVAL"]))
    for pid in _team_stars(league, team_id, rng.choice([1, 2])):
        rows.append(_row("player", pid, 0.5, ["FAVOURITE"]))
    return rows


def _plan_bandwagon(rng, league):
    records = league.get("records") or {}
    if not records:
        return []
    best = max(records, key=lambda t: (records[t][0] - records[t][1], records[t][0], -t))
    return [_row("team", best, 0.6, ["FAVOURITE"])]


def _plan_beat(rng, team_id, league):
    rows = [_row("team", team_id, 0.3, ["OWN_TEAM"])]
    subjects = [("player", p) for p in _team_stars(league, team_id, rng.choice([2, 3]))]
    coach = league["coaches"].get(team_id)
    if coach:
        subjects.append(("coach", coach))
    for ttype, tid in subjects:
        t = round(rng.uniform(-0.3, 0.6), 2)
        tags = ["BELIEVER"] if t >= SHOWN_MIN else (["DOUBTER"] if t <= -SHOWN_MIN else [])
        rows.append(_row(ttype, tid, t, tags))
    return rows


def _plan_national(rng, traits, league, former_team_id=None):
    traits = set(traits or [])
    hot = bool(traits & {"CONTRARIAN", "HOT_TAKER"})
    n_fav = rng.randint(3, 4) if hot else rng.randint(2, 3)
    n_grudge = 2 if hot else rng.choice([1, 1, 2])
    records = league.get("records") or {}
    players = sorted(league["players"], key=lambda p: (-(p["ovr"] or 0), p["id"]))[:TOP_PLAYERS]
    pop_rank = {p["id"]: i for i, p in enumerate(sorted(players, key=lambda p: (-(p.get("pop") or 0), p["id"])))}
    cands, fav_w, grudge_w = [], [], []
    for i, p in enumerate(players):
        star = 1.0 + (TOP_PLAYERS - i) / TOP_PLAYERS          # higher OVR draws more often
        fw, gw = star, star
        if hot:
            fw, gw = star * star, star * star                  # hot takers go after stars both ways
        if "FRONT_RUNNER" in traits and p["team_id"] in records:
            w, l = records[p["team_id"]]
            fw *= 0.5 + (w + 0.5) / (w + l + 1)
        if "ANALYTICS_BELIEVER" in traits and pop_rank.get(p["id"], 0) > i:
            fw *= 1.8                                           # better than he is famous
        cands.append(("player", p["id"]))
        fav_w.append(fw)
        grudge_w.append(gw)
    for team_id, coach_id in sorted(league["coaches"].items()):
        w = 0.6
        fw = w
        if "FRONT_RUNNER" in traits and team_id in records:
            wins, losses = records[team_id]
            fw *= 0.5 + (wins + 0.5) / (wins + losses + 1)
        cands.append(("coach", coach_id))
        fav_w.append(fw)
        grudge_w.append(w)
    favs = _weighted_pick(rng, cands, fav_w, n_fav)
    grudges = _weighted_pick(rng, cands, grudge_w, n_grudge, exclude=set(favs))
    rows = [_row(t, i, rng.uniform(0.4, 0.7), ["FAVOURITE"]) for t, i in favs]
    rows += [_row(t, i, -rng.uniform(0.3, 0.6), ["GRUDGE"]) for t, i in grudges]
    if former_team_id:
        rows.append(_row("team", former_team_id, 0.5, ["FORMER_TEAM"]))
    return rows


def plan_writer_relationships(accounts: list, league: dict, seed: str = "rel", traits_by_archetype: dict = None,
                              former_team_slant: dict = None) -> list:
    """Pure seeding plan. accounts: [{agent_id, archetype, team_id, person_key}] in account order; league:
    {teams: {id: {abbr, division}}, records: {team_id: (wins, losses)} (last season), players: [{id, team_id, ovr,
    pop}], coaches: {team_id: coach_id}, losses: [{season, week, winner, loser}]}. Returns
    [{person_key, target_type, target_id, trust, tags, note}], one row set per person."""
    traits_by_archetype = traits_by_archetype if traits_by_archetype is not None else _archetype_traits()
    slant = former_team_slant if former_team_slant is not None else _former_team_slant()
    by_abbr = {info.get("abbr"): tid for tid, info in league.get("teams", {}).items()}
    out = []
    for person in _people(accounts):
        pk, arch, team_id = person["person_key"], person.get("archetype"), person.get("team_id")
        if arch in NO_SEED_ARCHETYPES:
            continue
        rng = _rng(f"{seed}:{pk}")
        if arch == FAN:
            rows = _plan_fan(rng, team_id, league) if team_id else []
        elif arch == BANDWAGON:
            rows = _plan_bandwagon(rng, league)
        elif arch == BEAT:
            rows = _plan_beat(rng, team_id, league) if team_id else []
        else:
            former = by_abbr.get(slant.get(arch)) or (team_id if arch == FORMER_PLAYER else None)
            rows = _plan_national(rng, traits_by_archetype.get(arch, []), league, former)
        seen = set()
        for r in rows:
            key = (r["target_type"], r["target_id"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"person_key": pk, **r})
    return out


# ---------------------------------------------------------------- seeding (database)

def load_seed_league(cur, season: int) -> dict:
    """League facts the seeding plan needs, from stored data only (missing tables -> empty parts)."""
    league = {"teams": {}, "records": {}, "players": [], "coaches": {}, "losses": []}

    def teams():
        cur.execute("SELECT id, abbreviation, division FROM teams ORDER BY id;")
        league["teams"] = {r[0]: {"abbr": r[1], "division": r[2]} for r in cur.fetchall()}
    _guarded(cur, teams)

    def records():
        cur.execute("""SELECT team_id, wins, losses FROM team_seasons
                       WHERE season = (SELECT MAX(season) FROM team_seasons WHERE season < %s
                                       AND COALESCE(wins, 0) + COALESCE(losses, 0) > 0);""", (season,))
        league["records"] = {r[0]: (r[1] or 0, r[2] or 0) for r in cur.fetchall()}
    _guarded(cur, records)

    def players():
        cur.execute("""SELECT player_id, team_id, overall_rating, COALESCE(popularity_index, 0) FROM player_seasons
                       WHERE season = (SELECT MAX(season) FROM player_seasons WHERE season <= %s)
                         AND team_id IS NOT NULL AND released_week IS NULL AND overall_rating IS NOT NULL
                       ORDER BY overall_rating DESC, player_id;""", (season,))
        league["players"] = [{"id": r[0], "team_id": r[1], "ovr": r[2], "pop": float(r[3])} for r in cur.fetchall()]
    _guarded(cur, players)

    def coaches():
        cur.execute("""SELECT team_id, coach_id FROM coach_tenures
                       WHERE start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                       ORDER BY team_id, start_season DESC, id DESC;""", (season, season))
        for team_id, coach_id in cur.fetchall():
            league["coaches"].setdefault(team_id, coach_id)
    _guarded(cur, coaches)

    def losses():
        cur.execute("""SELECT season, week, home_team_id, away_team_id, home_score, away_score FROM games
                       WHERE status = 'COMPLETED' AND season BETWEEN %s AND %s
                         AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_score <> away_score;""",
                    (season - 2, season))
        for s, w, h, a, hs, as_ in cur.fetchall():
            winner, loser = (h, a) if hs > as_ else (a, h)
            league["losses"].append({"season": s, "week": w, "winner": winner, "loser": loser})
    _guarded(cur, losses)
    return league


def load_accounts(cur) -> list:
    pk = "COALESCE(person_key, agent_id)" if has_person_key(cur) else "agent_id"

    def q():
        cur.execute(f"SELECT agent_id, archetype, assigned_team_id, {pk} FROM agent_memories ORDER BY id;")
        return [{"agent_id": r[0], "archetype": r[1], "team_id": r[2], "person_key": r[3]} for r in cur.fetchall()]
    return _guarded(cur, q, []) or []


def _insert_rows(cur, rows: list) -> int:
    n = 0
    for r in rows:
        cur.execute("""INSERT INTO persona_relationships (persona_type, persona_id, target_type, target_id, trust, note,
                                                          tags, evidence)
                       VALUES ('writer', %s, %s, %s, %s, %s, %s, '[]'::jsonb)
                       ON CONFLICT (persona_type, persona_id, target_type, target_id) DO NOTHING;""",
                    (r["person_key"], r["target_type"], r["target_id"], r["trust"], r.get("note") or None, r["tags"]))
        n += max(cur.rowcount or 0, 0)
    return n


def seed_writer_relationships(cur, season: int, seed: str = "rel") -> int:
    """Seeds every writer's starting biases (AGENT_WIRE_VOICE_PLAN §2.2). Idempotent: existing rows are left alone.
    Returns the number of rows written."""
    if not _tables_ready(cur):
        return 0
    accounts = load_accounts(cur)
    if not accounts:
        return 0
    rows = plan_writer_relationships(accounts, load_seed_league(cur, season), seed)
    return _guarded(cur, lambda: _insert_rows(cur, rows), 0) or 0


# ---------------------------------------------------------------- growth

def _adjust(cur, pk, target_type, target_id, delta, event, add_tags=(), strong_tags=True) -> bool:
    """Moves one row by delta (creating it at 0 when missing), appends the event to evidence (when it carries a
    delta or a tag) and adds FAVOURITE / GRUDGE at |trust| >= STRONG. Returns True when the row changed."""
    cur.execute("""SELECT id, trust, tags, evidence FROM persona_relationships
                   WHERE persona_type = 'writer' AND persona_id = %s AND target_type = %s AND target_id = %s;""",
                (pk, target_type, str(target_id)))
    row = cur.fetchone()
    if row:
        rid, trust, tags, evidence = row[0], float(row[1]), list(row[2] or []), row[3]
    else:
        rid, trust, tags, evidence = None, 0.0, [], []
    if isinstance(evidence, str):
        evidence = json.loads(evidence or "[]")
    evidence = list(evidence or [])
    new = _clamp(trust + delta)
    for t in add_tags:
        if t not in tags:
            tags.append(t)
    if strong_tags and new >= STRONG and "FAVOURITE" not in tags:
        tags.append("FAVOURITE")
    if strong_tags and new <= -STRONG and "GRUDGE" not in tags:
        tags.append("GRUDGE")
    if delta or add_tags:
        # Applied refs ride in their own sticky entry: the last-EVIDENCE_KEEP window would otherwise drop an older
        # ref and a forced rerun would apply the same pick or call twice.
        sticky = next((e for e in evidence if isinstance(e, dict) and e.get("kind") == "APPLIED_REFS"), None)
        rest = [e for e in evidence if not (isinstance(e, dict) and e.get("kind") == "APPLIED_REFS")]
        if sticky is None:
            sticky = {"kind": "APPLIED_REFS", "refs": []}
        # Seed the sticky list from the refs still in the window: rows written before the marker existed then keep
        # what they have instead of losing an old ref to trimming and applying it twice.
        carried = [e.get("ref") for e in rest if isinstance(e, dict) and e.get("ref")]
        ref = (event or {}).get("ref")
        for r in carried + ([ref] if ref else []):
            if r and r not in sticky["refs"]:
                sticky["refs"] = (sticky["refs"] + [r])[-APPLIED_REFS_KEEP:]
        evidence = [sticky] + (rest + [event])[-EVIDENCE_KEEP:]
    ev = json.dumps(evidence)
    if rid is None:
        cur.execute("""INSERT INTO persona_relationships (persona_type, persona_id, target_type, target_id, trust, tags,
                                                          evidence)
                       VALUES ('writer', %s, %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (persona_type, persona_id, target_type, target_id) DO NOTHING;""",
                    (pk, target_type, str(target_id), new, tags, ev))
    else:
        cur.execute("""UPDATE persona_relationships SET trust = %s, tags = %s, evidence = %s::jsonb, updated_at = NOW()
                       WHERE id = %s;""", (new, tags, ev, rid))
    if event and event.get("ref") and _refs_ready(cur):
        _record_applied(cur, pk, target_type, target_id, event["ref"], event.get("season"))
    return True


def apply_take(cur, person_key: str, season: int, week: int, about: list) -> int:
    """A post or article leaned on its subjects: about = [{"type": player|coach|team, "id": int, "lean": -1|0|1,
    optional "text"}]. trust += GROW_STEP * lean. Returns rows touched."""
    if not person_key or not about or not _tables_ready(cur):
        return 0
    items = []
    for it in about:
        try:
            ttype, tid, lean = it.get("type"), int(it.get("id")), int(it.get("lean") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if ttype not in TARGET_TYPES:
            continue
        lean = max(-1, min(1, lean))
        items.append((ttype, tid, lean, str(it.get("text") or "")[:200]))

    def work():
        n = 0
        for ttype, tid, lean, text in items:
            delta = round(GROW_STEP * lean, 2)
            event = {"season": season, "week": week, "kind": "TAKE", "delta": delta, "text": text}
            n += _adjust(cur, person_key, ttype, tid, delta, event)
        return n
    return _guarded(cur, work, 0) or 0


def _refs_ready(cur) -> bool:
    """True when 2027_02_22's per-event applied-refs table exists."""
    def q():
        cur.execute("SELECT to_regclass('public.persona_relationship_event_refs') IS NOT NULL;")
        return bool(cur.fetchone()[0])
    return bool(_guarded(cur, q, False))


def _record_applied(cur, pk, target_type, target_id, ref, season):
    """Records a graded event's ref as applied, once: the durable fact the guard reads. The evidence window cannot
    hold every ref, and inferring from its order got real events wrong (M-0167)."""
    cur.execute("""INSERT INTO persona_relationship_event_refs
                   (persona_type, persona_id, target_type, target_id, ref, season)
                   VALUES ('writer', %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING;""",
                (pk, target_type, str(target_id), ref, season))


def _already_applied(cur, pk, target_type, target_id, ref) -> bool:
    """True when this graded ref was already applied: the per-event table is the record. Without that table (an
    older database) the ref's own evidence entry and the sticky APPLIED_REFS list still count; nothing is inferred
    from the order of the evidence."""
    if _refs_ready(cur):
        cur.execute("""SELECT 1 FROM persona_relationship_event_refs
                       WHERE persona_type = 'writer' AND persona_id = %s AND target_type = %s
                         AND target_id = %s AND ref = %s LIMIT 1;""",
                    (pk, target_type, str(target_id), ref))
        return cur.fetchone() is not None
    cur.execute("""SELECT evidence FROM persona_relationships
                   WHERE persona_type = 'writer' AND persona_id = %s AND target_type = %s AND target_id = %s;""",
                (pk, target_type, str(target_id)))
    row = cur.fetchone()
    if not row:
        return False
    ev = row[0]
    if isinstance(ev, str):
        ev = json.loads(ev or "[]")
    for e in ev or []:
        if not isinstance(e, dict):
            continue
        if e.get("ref") == ref:
            return True
        if e.get("kind") == "APPLIED_REFS" and ref in (e.get("refs") or []):
            return True
    return False


def graded_events(pick_rows: list, call_rows: list) -> list:
    """Pure: graded picks and calls -> [(person_key, target_type, target_id, delta, add_tags, event)].
    pick_rows: [{id, person_key, season, week, winner_backed, actual_winner, label}] (actual_winner None = tie);
    call_rows: [{id, person_key, season, graded_week, call_type, subject_type, subject_id, result, detail}]."""
    out = []
    for p in pick_rows:
        backed, actual = p.get("winner_backed"), p.get("actual_winner")
        if not backed or actual is None:
            continue
        right = backed == actual
        delta = EARN_RIGHT if right else EARN_BURNED
        text = "you picked them and they delivered" if right else "you picked them and they burned you"
        out.append((p["person_key"], "team", backed, delta, (),
                    {"season": p["season"], "week": p["week"], "kind": "PICK_RIGHT" if right else "PICK_BURNED",
                     "delta": delta, "text": text, "ref": f"pick:{p['id']}"}))
    for c in call_rows:
        if c.get("result") not in ("RIGHT", "WRONG") or not c.get("subject_id") or c.get("subject_type") not in TARGET_TYPES:
            continue
        right = c["result"] == "RIGHT"
        ref = f"call:{c['id']}"
        label = c["call_type"].replace("_", " ").lower()
        base = {"season": c["season"], "week": c.get("graded_week"), "ref": ref}
        if c["call_type"] in AGAINST_CALLS:
            if right:
                out.append((c["person_key"], c["subject_type"], c["subject_id"], 0.0, ("DOUBTER",),
                            {**base, "kind": "CALL_AGAINST_RIGHT", "delta": 0.0,
                             "text": f"you called it ({label}) and it happened"}))
            continue
        delta = EARN_RIGHT if right else EARN_BURNED
        out.append((c["person_key"], c["subject_type"], c["subject_id"], delta, (),
                    {**base, "kind": "CALL_RIGHT" if right else "CALL_BURNED", "delta": delta,
                     "text": f"your {label} call came true" if right else f"your {label} call fell through"}))
    return out


def _load_graded(cur, season, week, pk_expr):
    """Picks and graded calls of `season` up to and including `week` (week None = the whole season). `<=`, not `=`:
    a publication week can be skipped, and a season's final weeks are only graded after the Super Bowl."""
    pick_week = "" if week is None else " AND p.week <= %s"
    call_week = "" if week is None else " AND graded_week <= %s"
    pick_params = (season,) if week is None else (season, week)
    call_params = (season,) if week is None else (season, week)
    cur.execute(f"""
        SELECT p.id, {pk_expr}, p.season, p.week,
               CASE WHEN p.pick_type = 'WINNER' THEN p.winner_team_id
                    WHEN p.score_a > p.score_b THEN p.team_a_id WHEN p.score_b > p.score_a THEN p.team_b_id END,
               CASE WHEN g.home_score > g.away_score THEN g.home_team_id
                    WHEN g.away_score > g.home_score THEN g.away_team_id END
        FROM agent_predictions p
        JOIN games g ON g.id = p.game_id
        LEFT JOIN agent_memories m ON m.agent_id = p.agent_id
        WHERE p.season = %s AND g.status = 'COMPLETED'{pick_week};""", pick_params)
    picks = [{"id": r[0], "person_key": r[1], "season": r[2], "week": r[3], "winner_backed": r[4],
              "actual_winner": r[5]} for r in cur.fetchall()]
    calls = []
    cur.execute("SELECT to_regclass('public.agent_season_calls') IS NOT NULL;")
    if cur.fetchone()[0]:
        cur.execute(f"""SELECT id, person_key, season, graded_week, call_type, subject_type, subject_id, result, detail
                        FROM agent_season_calls WHERE season = %s{call_week}
                          AND result IN ('RIGHT', 'WRONG');""", call_params)
        calls = [dict(zip(("id", "person_key", "season", "graded_week", "call_type", "subject_type", "subject_id",
                           "result", "detail"), r)) for r in cur.fetchall()]
    return picks, calls


def apply_graded(cur, season: int, week: int | None) -> int:
    """Run once per weekly run: graded picks and season calls of `season` up to and including `week` (None = the
    whole season, which is what the new season's first run applies to the season that just ended) move the backed
    subject by EARN_RIGHT / EARN_BURNED (a right call against a subject adds DOUBTER and evidence only). `<=` rather
    than `=`, so a skipped publication week is not lost. Safe to rerun: each pick or call is applied once (its ref is
    kept in evidence). Returns rows changed."""
    if not _tables_ready(cur):
        return 0
    pk_expr = "COALESCE(m.person_key, p.agent_id)" if has_person_key(cur) else "p.agent_id"
    loaded = _guarded(cur, lambda: _load_graded(cur, season, week, pk_expr), None)
    if not loaded:
        return 0
    events = graded_events(*loaded)

    def work():
        n = 0
        for pk, ttype, tid, delta, tags, event in events:
            if _already_applied(cur, pk, ttype, tid, event["ref"]):
                continue
            n += _adjust(cur, pk, ttype, tid, delta, event, add_tags=tags)
        return n
    return _guarded(cur, work, 0) or 0


# ---------------------------------------------------------------- decay

def decayed_trust(trust: float, tags) -> float:
    """trust * DECAY; OWN_TEAM / RIVAL rows keep their sign and at least DECAY_FLOOR of it."""
    tags = set(tags or [])
    new = round(trust * DECAY, 2)
    if "OWN_TEAM" in tags and trust > 0:
        new = max(new, DECAY_FLOOR)
    if "RIVAL" in tags and trust < 0:
        new = min(new, -DECAY_FLOOR)
    return new


def _touched_since(evidence, season) -> bool:
    return any(isinstance(e, dict) and e.get("kind") != "DECAY" and (e.get("season") or 0) >= season - 1
               for e in evidence or [])


def decay(cur, season: int) -> int:
    """Season rollover, called once the new `season` starts: rows with no event in season - 1 or later get
    trust *= DECAY (decayed_trust). Idempotent per season: a DECAY marker (not quoted in prompts) records the run.
    Returns rows decayed."""
    if not _tables_ready(cur):
        return 0

    def work():
        cur.execute("""SELECT id, trust, tags, evidence FROM persona_relationships WHERE persona_type = 'writer';""")
        n = 0
        for rid, trust, tags, evidence in cur.fetchall():
            if isinstance(evidence, str):
                evidence = json.loads(evidence or "[]")
            evidence = list(evidence or [])
            if any(isinstance(e, dict) and e.get("kind") == "DECAY" and e.get("season") == season for e in evidence):
                continue
            if _touched_since(evidence, season):
                continue
            new = decayed_trust(float(trust), tags)
            real = [e for e in evidence
                    if isinstance(e, dict) and e.get("kind") not in ("DECAY", "APPLIED_REFS")][-EVIDENCE_KEEP:]
            # The applied-refs entry rides through untouched: dropping it let a forced rerun of an old season apply
            # the same graded picks again (M-0150 B12).
            sticky = next((e for e in reversed(evidence)
                           if isinstance(e, dict) and e.get("kind") == "APPLIED_REFS"), None)
            marker = {"season": season, "week": 0, "kind": "DECAY", "delta": round(new - float(trust), 2), "text": ""}
            cur.execute("UPDATE persona_relationships SET trust = %s, evidence = %s::jsonb, updated_at = NOW() WHERE id = %s;",
                        (new, json.dumps(([sticky] if sticky else []) + real + [marker]), rid))
            n += 1
        return n
    return _guarded(cur, work, 0) or 0


# ---------------------------------------------------------------- prompt block

def _sentence(r: dict) -> str:
    name, t, tags, ttype = r["name"], r["trust"], set(r.get("tags") or []), r["target_type"]
    if "OWN_TEAM" in tags and t > 0:
        return f"The {name} are your team." if t < STRONG else f"The {name} are your team; you live and die with them."
    if "RIVAL" in tags and t < 0:
        return f"The {name} are your rival, and you enjoy every loss they take."
    if "FORMER_TEAM" in tags and t > 0:
        return f"You played for the {name} and still have a soft spot for them."
    if t >= STRONG:
        return {"player": f"You're a {name} believer and you back him every chance you get.",
                "coach": f"You rate {name} as one of the best coaches in the league.",
                "team": f"You're all in on the {name}."}[ttype]
    if t >= SHOWN_MIN:
        return f"You like {name} more than most people do." if ttype != "team" else f"You like what the {name} are doing."
    if t <= -STRONG:
        return f"You can't stand {name}." if ttype != "team" else f"You can't stand the {name}."
    if t <= -SHOWN_MIN or "DOUBTER" in tags:
        return f"You're a {name} doubter and you don't hide it." if ttype != "team" else f"You don't buy the {name}."
    return f"You haven't made up your mind about {name}." if ttype != "team" else f"You haven't made up your mind about the {name}."


def _evidence_line(evidence) -> str:
    real = [e for e in evidence or [] if isinstance(e, dict) and e.get("kind") != "DECAY" and (e.get("text") or e.get("delta"))]
    if not real:
        return ""
    e = real[-1]
    when = f"Week {e.get('week')}" if e.get("week") else f"{e.get('season')}"
    text = e.get("text") or ("it went your way" if (e.get("delta") or 0) > 0 else "it went against you")
    if e.get("kind") == "TAKE":
        return f"({when}: you said \"{text}\")"
    return f"({when}: {text}.)"


def render_relationship_rows(rows: list, subjects: list = None, limit: int = 4) -> str:
    """Pure: rows [{target_type, target_id, trust, tags, evidence, name}] -> plain sentences, the subjects' rows
    first, then the strongest |trust| rows (at least SHOWN_MIN)."""
    want = {(s.get("type"), str(s.get("id"))) for s in subjects or [] if isinstance(s, dict)}
    rows = [r for r in rows if r.get("name")]
    first = [r for r in rows if (r["target_type"], str(r["target_id"])) in want]
    rest = sorted((r for r in rows if r not in first and abs(r["trust"]) >= SHOWN_MIN),
                  key=lambda r: (-abs(r["trust"]), r["target_type"], str(r["target_id"])))
    first.sort(key=lambda r: -abs(r["trust"]))
    lines = []
    for r in (first + rest)[:limit]:
        ev = _evidence_line(r.get("evidence"))
        lines.append(f"- {_sentence(r)}" + (f" {ev}" if ev else ""))
    return "\n".join(lines)


def _names(cur, rows):
    ids = {"player": set(), "coach": set(), "team": set()}
    for r in rows:
        try:
            ids[r["target_type"]].add(int(r["target_id"]))
        except (KeyError, ValueError):
            pass
    names = {}
    if ids["player"]:
        cur.execute("SELECT id, COALESCE(full_name, player_name) FROM players WHERE id = ANY(%s);", (list(ids["player"]),))
        names.update({("player", str(i)): n for i, n in cur.fetchall()})
    if ids["coach"]:
        cur.execute("SELECT id, coach_name FROM coaches WHERE id = ANY(%s);", (list(ids["coach"]),))
        names.update({("coach", str(i)): n for i, n in cur.fetchall()})
    if ids["team"]:
        cur.execute("SELECT id, name FROM teams WHERE id = ANY(%s);", (list(ids["team"]),))
        names.update({("team", str(i)): n for i, n in cur.fetchall()})
    return names


def relationship_block(cur, person_key: str, subjects: list, limit: int = 4) -> str:
    """Plain-sentence biases for a prompt (subjects [{type, id}] of the post or article first). "" when there are
    no rows or no table."""
    if not person_key or cur is None or not _tables_ready(cur):
        return ""

    def q():
        cur.execute("""SELECT target_type, target_id, trust, tags, evidence FROM persona_relationships
                       WHERE persona_type = 'writer' AND persona_id = %s;""", (person_key,))
        rows = []
        for ttype, tid, trust, tags, ev in cur.fetchall():
            if isinstance(ev, str):
                ev = json.loads(ev or "[]")
            rows.append({"target_type": ttype, "target_id": tid, "trust": float(trust), "tags": list(tags or []),
                         "evidence": ev or []})
        if not rows:
            return ""
        names = _names(cur, rows)
        for r in rows:
            r["name"] = names.get((r["target_type"], str(r["target_id"])))
        return render_relationship_rows(rows, subjects, limit)
    return _guarded(cur, q, "") or ""
