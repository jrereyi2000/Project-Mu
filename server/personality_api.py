"""Personality on the site (/api/personality/*; docs/done/PERSONALITY_DISPLAY_PLAN.md §2.1, PROJECT_SPEC §4.8).

Who everyone in the league is, in words: a person's sliders as sentences with a 0-10 bar, his traits with what
they tend to do, this week's mood tag, how he has changed (evolution notes), his known ties as labels, his
attachment to his team, open promises and a public trade request. No engine numbers: no trust values,
coefficients, chances or draws (PERSONALITY_PLAN D3); the only numbers in a profile body are the slider values,
the profile revision (for the editor's stale check) and the public trade request's season and week.

  GET  /api/personality/{kind}/{ref}           kind: player | coach | coordinator | owner | gm | scout | writer
                                               ref:  players.id | coaches.id | coordinators.id | team abbr | gm_profiles.id
                                                     | team abbr | writer key
  GET  /api/personality/catalogue?role=R       traits a role may hold: {id, name, category, tendency, conflicts}
  POST /api/personality/{kind}/{ref}/override  X-Debug-Token; {sliders?, add_traits?, remove_traits?, reason,
                                               expected_revision?} -> the new GET body (403 / 404 / 409 / 422)

Resolution goes through the personality core: the db-player / db-coach / db-coordinator / db-owner / db-scout
aliases, and adopted writers and GMs by their legacy persona IDs. A writer or GM the core doesn't manage yet
(adoption is JR's call) is read from persona_sliders / persona_traits, marked origin 'legacy' and not editable. A
player without a db-player alias who is still an undrafted prospect is 404 hidden_until_drafted (D1); anyone
else unresolved is 404 no_profile.

Every block reads only what other plans built: the relationships ledger (promises), the relationships projection
(ties, attachment, mood; neutral until a relationships policy is active, so the block is unavailable until the
activation plan supplies one through RELATIONSHIP_POLICY), the core's history (evolution notes) and the trade
request store's PUBLIC projection. A missing table, module or policy makes that block null with a reason in
`unavailable`; the page hides it. Reads never write; the override goes through personality.store.override
(actor JR, the league_state season and week) and commits only on success.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from newsroom import personas as legacy
from personality.evolution import MAX_CHANGE_NOTES
from personality.slider_inference import is_inferred

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# kind -> (core role, alias namespace); writers and GMs are adopted by legacy persona ID (namespace None).
KINDS = {
    "player": ("player", "db-player"),
    "coach": ("coach", "db-coach"),
    "coordinator": ("coach", "db-coordinator"),   # the core has no coordinator role
    "owner": ("owner", "db-owner"),
    "gm": ("gm", None),
    "scout": ("scout", "db-scout"),
    "writer": ("writer", None),
}
NAMESPACE_KIND = {ns: kind for kind, (_, ns) in KINDS.items() if ns}
MAX_TIES = 6
MAX_PER_CATEGORY = 2
MAX_TRAITS = 5
# (lowest trust in the band, label), checked top down; the neutral band (-0.15, 0.15) and unknown trust are omitted.
TRUST_LABELS = ((0.5, "trusts"), (0.15, "on good terms with"))
DISTRUST_LABELS = ((-0.5, "at odds with"), (-0.15, "wary of"))   # trust <= the value
GRUDGE_LABEL = "holds a grudge against"
MENTOR_LABELS = {"mentor": "mentor to", "mentee": "mentored by"}
ATTACHMENT_LABELS = {"none": None, "weak": "settling in", "established": "attached", "strong": "deeply attached"}
BLOCKS = ("mood", "changes", "ties", "attachment", "promises", "trade_request")
TICK_STRIDE = 1000           # engines.player_trade_requests.clock_ordinal: season * 1000 + tick
TICKS_PER_SEASON = 32        # engines.relationship_producers.calendar
RELATIONSHIP_POLICY = None   # the activation plan's switchboard supplies an enabled policy; None = not active

router = APIRouter(prefix="/api/personality")


class Unavailable(Exception):
    """A block whose module, table or policy isn't there yet; the reason goes to the body's `unavailable`."""


# ------------------------------------------------------------------------------------------------ words ---

def slider_rows(sliders: dict, evidence: Optional[dict] = None) -> list[dict]:
    """Each slider in legacy order: 0-3 reads the low phrase, 4-6 nothing, 7-10 the high phrase (SLIDER_WORDS).
    `inferred` is true when the slider's core provenance is an `inferred:` entry (no direct evidence; estimated
    from traits, docs/in_progress/INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.9)."""
    evidence = evidence if isinstance(evidence, dict) else {}
    rows = []
    for name in legacy.SLIDERS:
        value = sliders.get(name)
        if value is None:
            continue
        low, high = legacy.SLIDER_WORDS[name]
        sentence = low if value <= 3 else high if value >= 7 else None
        rows.append({"name": name, "value": int(value), "sentence": sentence, "low": low, "high": high,
                     "inferred": is_inferred(evidence.get("slider:" + name))})
    return rows


def _trait_name(tid: str, entry: dict) -> str:
    return entry.get("name") or entry.get("label") or tid.replace("_", " ").title()


def _tendency(entry: dict) -> Optional[str]:
    return entry.get("prompt_text") or entry.get("prompt") or entry.get("description")


def trait_rows(traits: list, catalogue: dict) -> list[dict]:
    out = []
    for tid in traits:
        entry = catalogue.get(tid) or {}
        out.append({"id": tid, "name": _trait_name(tid, entry), "category": entry.get("category"),
                    "tendency": _tendency(entry)})
    return out


def trust_label(trust) -> Optional[str]:
    if trust is None or isinstance(trust, bool):
        return None
    for floor, label in TRUST_LABELS:
        if trust >= floor:
            return label
    for ceiling, label in DISTRUST_LABELS:
        if trust <= ceiling:
            return label
    return None


def _tie_strength(label: str, trust) -> float:
    if label == GRUDGE_LABEL:
        return 2.0
    base = abs(trust) if isinstance(trust, (int, float)) and not isinstance(trust, bool) else 0.0
    return max(base, 0.5) if label in MENTOR_LABELS.values() else base


def tie_label(edge: dict) -> Optional[str]:
    """One relationship_context edge toward a person -> words, or None (neutral band or unknown)."""
    if edge.get("breaches"):
        return GRUDGE_LABEL
    for tie in edge.get("ties") or []:
        if tie.get("kind") == "mentorship" and tie.get("direction") in MENTOR_LABELS:
            return MENTOR_LABELS[tie["direction"]]
    return trust_label(edge.get("trust"))


def ties_block(context: dict, name_of) -> list[dict]:
    """Known person ties as words, strongest first, at most MAX_TIES. `name_of(PersonRef) -> {kind, ref, name}`."""
    found = []
    for edge in context.get("edges") or []:
        target = edge.get("target") or {}
        if target.get("kind") != "person":
            continue
        label = tie_label(edge)
        if label is None:
            continue
        who = name_of(target)
        if who is None:
            continue
        found.append((-_tie_strength(label, edge.get("trust")), who.get("name") or "", label, who))
    found.sort(key=lambda x: (x[0], x[1]))
    return [{"label": label, "target": who} for _, _, label, who in found[:MAX_TIES]]


def attachment_block(context: dict, team: Optional[dict]) -> Optional[dict]:
    """His attachment tier toward his current team in words; None when there's no team or no label."""
    if not team or team.get("id") is None:
        return None
    key = f"team:{team['id']}"
    for edge in context.get("edges") or []:
        target = edge.get("target") or {}
        if target.get("kind") == "team" and target.get("team_key") == key:
            label = ATTACHMENT_LABELS.get((edge.get("attachment") or {}).get("tier"))
            return {"team": team.get("abbr"), "label": label} if label else None
    return None


def _same_person(a: dict, b: dict) -> bool:
    return bool(a) and bool(b) and a.get("role") == b.get("role") and a.get("persona_id") == b.get("persona_id")


def promises_block(events: list, subject: dict, name_of) -> list[dict]:
    """Open promises he made or was given, from his relationship events (promise_made with no promise_resolved)."""
    resolved = {ev["payload"]["promise_id"] for ev in events if ev.get("kind") == "promise_resolved"}
    seen, out = set(), []
    for ev in events:
        if ev.get("kind") != "promise_made":
            continue
        p = ev.get("payload") or {}
        pid = p.get("promise_id")
        if pid in resolved or pid in seen:
            continue
        seen.add(pid)
        text = ((p.get("terms") or {}).get("description")) or ""
        if _same_person(p.get("beneficiary"), subject):
            who = name_of(p.get("issuer") or {})
            out.append({"direction": "made_to_him", "by": _no_ref(who), "text": text, "status": "open"})
        elif _same_person(p.get("issuer"), subject):
            who = name_of(p.get("beneficiary") or {})
            out.append({"direction": "made_by_him", "to": _no_ref(who), "text": text, "status": "open"})
    return out


def _no_ref(who: Optional[dict]) -> dict:
    return {"kind": (who or {}).get("kind"), "name": (who or {}).get("name") or "someone",
            "ref": (who or {}).get("ref")}


def trade_request_block(projection: Optional[dict]) -> Optional[dict]:
    """The trade-requests plan's PUBLIC projection only: status, since when it is public, and whether that's current."""
    if not projection:
        return None
    ordinal = projection.get("as_of_ordinal")
    since = None
    if isinstance(ordinal, int) and not isinstance(ordinal, bool):
        since = {"season": ordinal // TICK_STRIDE, "tick": ordinal % TICK_STRIDE}
    return {"status": projection.get("status"), "public_since": since, "current": bool(projection.get("current"))}


def mood_block(mood: Optional[dict]) -> Optional[dict]:
    if not mood:
        return None
    return {"primary": mood.get("primary") or "neutral", "secondary": list(mood.get("secondary") or [])}


# --------------------------------------------------------------------------------------------- assembly ---

def build_profile(kind: str, ref: str, src) -> dict:
    """The GET body. `src` is a Sources (the database) or a fake with the same methods. Raises HTTPException 404."""
    if kind not in KINDS:
        raise HTTPException(404, "unknown_kind")
    who = src.resolve(kind, ref)
    status = who.get("status")
    if status == "hidden_until_drafted":
        raise HTTPException(404, "hidden_until_drafted")
    if status != "ok":
        raise HTTPException(404, "no_profile")
    profile = who["profile"]
    catalogue = profile.get("catalogue") or src.catalogue()
    core = who.get("identity") is not None
    body = {"kind": kind, "ref": str(ref), "name": who.get("name"), "origin": profile.get("origin"),
            "editable": bool(core and profile.get("origin") in ("generated", "real")),
            "revision": profile.get("revision") if core else None,
            "team": (who.get("team") or {}).get("abbr"),
            "sliders": slider_rows(profile.get("sliders") or {}, profile.get("evidence")), "traits": trait_rows(profile.get("traits") or [], catalogue)}
    unavailable = {}
    for block in BLOCKS:
        body[block] = None
    if not core:
        for block in BLOCKS:
            unavailable[block] = "not managed by the personality core yet"
        body["unavailable"] = unavailable
        return body
    ident = who["identity"]
    subject = who["subject"]
    name_of = src.name_of

    def fill(block, fn):
        try:
            body[block] = fn()
        except Unavailable as e:
            unavailable[block] = str(e) or "not available"

    fill("changes", lambda: src.change_notes(ident))
    fill("promises", lambda: promises_block(src.relationship_events(subject), subject, name_of))
    if profile.get("origin") == "unknown":
        unavailable.update(mood="unclassified person", ties="unclassified person", attachment="unclassified person")
    else:
        try:
            context = src.relationship_context(subject)
        except Unavailable as e:
            for block in ("ties", "attachment"):
                unavailable[block] = str(e) or "not available"
        else:
            body["ties"] = ties_block(context, name_of)
            body["attachment"] = attachment_block(context, who.get("team"))
        fill("mood", lambda: mood_block(src.mood(subject, profile)))
    if kind == "player":
        fill("trade_request", lambda: trade_request_block(src.trade_request(subject)))
    body["unavailable"] = unavailable
    return body


def catalogue_body(role: str, catalogue: dict) -> dict:
    core_role = KINDS[role][0] if role in KINDS else role
    if core_role not in {r for r, _ in KINDS.values()}:
        raise HTTPException(422, f"unknown role {role!r}")
    traits = []
    for tid, entry in sorted(catalogue.items()):
        if (entry.get("roles") or {}).get(core_role, 0) <= 0:
            continue
        traits.append({"id": tid, "name": _trait_name(tid, entry), "category": entry.get("category"),
                       "tendency": _tendency(entry), "conflicts": list(entry.get("conflicts") or [])})
    return {"role": core_role, "traits": traits, "max_per_category": MAX_PER_CATEGORY, "max_traits": MAX_TRAITS,
            "slider_band": {"min": legacy.SLIDER_BUDGET_MIN, "max": legacy.SLIDER_BUDGET_MAX}}


# ----------------------------------------------------------------------------------------- the database ---

class Sources:
    """Production reads over one psycopg connection (tuple rows, the core's convention). Each block runs in a
    savepoint, so a missing table makes that block unavailable without breaking the others."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self._names = {}
        self._clock = None
        self._universe = None

    # -- helpers
    @contextmanager
    def _sp(self):
        name = "pd_" + uuid.uuid4().hex
        self.cur.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.cur.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self.cur.execute(f"RELEASE SAVEPOINT {name}")

    def _table(self, name):
        self.cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return bool(self.cur.fetchone()[0])

    def _one(self, sql, params=()):
        self.cur.execute(sql, params)
        return self.cur.fetchone()

    def clock(self):
        if self._clock is None:
            row = self._one("SELECT current_season, current_week FROM league_state WHERE id = 1")
            if row is None:
                raise HTTPException(422, "no league_state row")
            self._clock = (int(row[0]), max(1, min(TICKS_PER_SEASON, int(row[1] or 1))))
        return self._clock

    def catalogue(self):
        return legacy.load_traits()

    def league_universe(self):
        if self._universe is None:
            self._universe = ""
            try:
                with self._sp():
                    if self._table("persona_lifecycle_universes"):
                        row = self._one("""SELECT u.universe_id FROM persona_lifecycle_universes u, league_state s
                                           WHERE s.id = 1 AND u.league_id = s.madden_league_id::text""")
                        self._universe = str(row[0]) if row else ""
            except Exception:
                self._universe = ""
        return self._universe or None

    def _team(self, team_id):
        if team_id is None:
            return None
        row = self._one("SELECT abbreviation FROM teams WHERE id = %s", (team_id,))
        return {"id": int(team_id), "abbr": row[0] if row else None}

    def _team_by_abbr(self, abbr):
        row = self._one("SELECT id FROM teams WHERE upper(abbreviation) = upper(%s)", (str(abbr),))
        return None if row is None else int(row[0])

    def _alias_identity(self, role, namespace, external_id):
        from personality.core import Identity
        if not self._table("persona_core_aliases"):
            return None
        self.cur.execute("""SELECT DISTINCT a.universe_id, p.birth_key FROM persona_core_aliases a
                            JOIN persona_core_profiles p ON p.persona_type = a.persona_type AND p.persona_id = a.persona_id
                            WHERE a.persona_type = %s AND a.namespace = %s AND a.external_id = %s""",
                         (role, namespace, str(external_id)))
        rows = self.cur.fetchall()
        league = self.league_universe()
        if league is not None:
            rows = [r for r in rows if r[0] == league] or ([] if len(rows) > 1 else rows)
        if len(rows) != 1:
            return None
        return Identity(rows[0][0], role, rows[0][1])

    def _person(self, kind, ref):
        """(core role, namespace, external id, name, team) for the page's kind/ref; None when there's no such row."""
        role, ns = KINDS[kind]
        if kind == "player":
            row = self._one("""SELECT coalesce(full_name, player_name) FROM players WHERE id = %s""", (int(ref),))
            if row is None:
                return None
            team = None
            if self._table("player_seasons"):
                t = self._one("""SELECT team_id FROM player_seasons WHERE player_id = %s
                                 ORDER BY season DESC LIMIT 1""", (int(ref),))
                team = t[0] if t else None
            return role, ns, str(int(ref)), row[0], team
        if kind == "coach":
            row = self._one("SELECT coach_name FROM coaches WHERE id = %s", (int(ref),))
            if row is None:
                return None
            t = self._one("""SELECT team_id FROM coach_tenures WHERE coach_id = %s
                             ORDER BY (end_season IS NULL) DESC, start_season DESC LIMIT 1""", (int(ref),))
            return role, ns, str(int(ref)), row[0], t[0] if t else None
        if kind == "coordinator":
            row = self._one("SELECT coordinator_name FROM coordinators WHERE id = %s", (int(ref),))
            if row is None:
                return None
            t = self._one("""SELECT team_id FROM coordinator_tenures WHERE coordinator_id = %s
                             ORDER BY (end_season IS NULL) DESC, start_season DESC LIMIT 1""", (int(ref),))
            return role, ns, str(int(ref)), row[0], t[0] if t else None
        if kind in ("owner", "scout"):
            team_id = self._team_by_abbr(ref)
            table, col = ("team_owners", "owner_name") if kind == "owner" else ("scout_directors", "name")
            if team_id is None or not self._table(table):
                return None
            row = self._one(f"""SELECT id, {col} FROM {table} WHERE team_id = %s
                                ORDER BY (end_season IS NULL) DESC, start_season DESC LIMIT 1""", (team_id,))
            return None if row is None else (role, ns, str(row[0]), row[1], team_id)
        if kind == "gm":
            row = self._one("SELECT gm_name, team_id FROM gm_profiles WHERE id = %s", (int(ref),))
            return None if row is None else (role, None, str(int(ref)), row[0], row[1])
        if kind == "writer":
            name = _writer_names().get(str(ref))
            return None if name is None else (role, None, str(ref), name, None)
        return None

    def _prospect(self, ref):
        if not self._table("draft_prospects"):
            return False
        row = self._one("SELECT 1 FROM draft_prospects WHERE player_id = %s AND actual_round IS NULL LIMIT 1",
                        (int(ref),))
        return row is not None

    def resolve(self, kind, ref):
        from personality import store
        from personality.core import Identity
        try:
            with self._sp():
                person = self._person(kind, ref)
        except (ValueError, TypeError):
            return {"status": "no_profile"}
        if person is None:
            return {"status": "no_profile"}
        role, ns, external, name, team_id = person
        with self._sp():
            team = self._team(team_id)
        identity = None
        with self._sp():
            if ns is not None:
                identity = self._alias_identity(role, ns, external)
            elif self._table("persona_core_profiles"):
                managed = legacy.core_managed(self.cur, role, external)
                identity = Identity(managed[0], role, managed[1]) if managed else None
            profile = store.read(self.cur, identity) if identity is not None else None
        if profile is None:
            if kind == "player":
                with self._sp():
                    hidden = self._prospect(ref)
                return {"status": "hidden_until_drafted" if hidden else "no_profile"}
            if kind in ("gm", "writer"):
                with self._sp():
                    legacy_profile = self._legacy_profile(role, external)
                if legacy_profile is not None:
                    return {"status": "ok", "identity": None, "profile": legacy_profile, "name": name, "team": team}
            return {"status": "no_profile"}
        subject = {"kind": "person", "universe_id": identity.universe_id, "role": role,
                   "persona_id": identity.persona_id}
        return {"status": "ok", "identity": identity, "subject": subject, "profile": profile, "name": name,
                "team": team, "kind": kind}

    def _legacy_profile(self, role, persona_id):
        if not self._table("persona_sliders"):
            return None
        row = self._one(f"SELECT {', '.join(legacy.SLIDERS)} FROM persona_sliders "
                        "WHERE persona_type = %s AND persona_id = %s", (role, persona_id))
        if row is None:
            return None
        self.cur.execute("SELECT trait_id FROM persona_traits WHERE persona_type = %s AND persona_id = %s AND active "
                         "ORDER BY id", (role, persona_id))
        return {"origin": "legacy", "sliders": dict(zip(legacy.SLIDERS, row)),
                "traits": [r[0] for r in self.cur.fetchall()], "revision": None}

    # -- blocks
    def change_notes(self, identity):
        from personality import store
        from personality.evolution import change_notes
        try:
            with self._sp():
                history = store.history(self.cur, identity)
        except Exception:
            raise Unavailable("change history needs the core extensions migration")
        return change_notes(history, as_of=self.clock(), limit=MAX_CHANGE_NOTES)

    def relationship_events(self, subject):
        from personality import relationship_store as rs
        try:
            with self._sp():
                if not self._table(rs.EVENTS_TABLE):
                    raise Unavailable("relationship ledger not migrated")
                season, tick = self.clock()
                return rs.read_events(self.cur, subject=subject, as_of={"season": season, "tick": tick})
        except Unavailable:
            raise
        except Exception:
            raise Unavailable("relationship ledger unreadable")

    def _calendar(self, events):
        seasons = {ev["occurred_at"]["season"] for ev in events} | {self.clock()[0]}
        return {s: TICKS_PER_SEASON for s in range(min(seasons), max(seasons) + 1)}

    def relationship_context(self, subject):
        from personality import relationships as rel
        if RELATIONSHIP_POLICY is None:
            raise Unavailable("relationships not active yet (no approved policy)")
        events = self.relationship_events(subject)
        season, tick = self.clock()
        state = rel.project_relationships(events, subject=subject, as_of={"season": season, "tick": tick},
                                          known_through=None, policy=RELATIONSHIP_POLICY,
                                          calendar=self._calendar(events))
        context = rel.relationship_context(state)
        if context.get("status") != "enabled":
            raise Unavailable("relationships not active yet")
        return context

    def mood(self, subject, profile):
        from personality import core, reactions as rx, relationships as rel
        if RELATIONSHIP_POLICY is None:
            raise Unavailable("relationships not active yet (no approved policy)")
        events = self.relationship_events(subject)
        season, tick = self.clock()
        as_of, cal = {"season": season, "tick": tick}, self._calendar(events)
        state = rel.project_relationships(events, subject=subject, as_of=as_of, known_through=None,
                                          policy=RELATIONSHIP_POLICY, calendar=cal)
        result = rx.project_reactions(events, state, feature_view=core.feature_view(profile, "reactions"),
                                      as_of=as_of, known_through=None, policy=RELATIONSHIP_POLICY, calendar=cal)
        context = rx.reaction_context(result)
        if context.get("status") != "enabled":
            raise Unavailable("relationships not active yet")
        return {"primary": context["mood"], "secondary": context["secondary"]}

    def trade_request(self, subject):
        from engines import player_trade_request_store as trs
        from engines import player_trade_requests as tr
        try:
            with self._sp():
                if not self._table(trs.REQUESTS_TABLE):
                    raise Unavailable("trade requests not migrated")
                episode = trs.read_active(self.cur, subject["universe_id"], subject["persona_id"])
                if episode is None:
                    self.cur.execute(f"""SELECT state FROM {trs.REQUESTS_TABLE} WHERE universe_id = %s
                                         AND persona_id = %s AND state->>'visibility' = 'PUBLIC'
                                         ORDER BY updated_at DESC NULLS LAST LIMIT 1""",
                                     (subject["universe_id"], subject["persona_id"]))
                    row = self.cur.fetchone()
                    episode = None if row is None else tr.episode_from_dict(
                        json.loads(row[0]) if isinstance(row[0], (str, bytes)) else row[0])
        except Unavailable:
            raise
        except Exception:
            raise Unavailable("trade requests unreadable")
        return tr.request_projection(episode, audience="PUBLIC")

    def name_of(self, person_ref):
        """{kind, ref, name} for a PersonRef, through its db-* alias or adopted legacy ID; None when unknown."""
        key = (person_ref.get("role"), person_ref.get("persona_id"))
        if key in self._names:
            return self._names[key]
        found = None
        try:
            with self._sp():
                found = self._name_of(*key)
        except Exception:
            found = None
        self._names[key] = found
        return found

    def _name_of(self, role, persona_id):
        if not role or not persona_id:
            return None
        if role in ("gm", "writer"):
            if role == "writer":
                name = _writer_names().get(persona_id)
                return None if name is None else {"kind": "writer", "ref": persona_id, "name": name}
            row = self._one("SELECT gm_name FROM gm_profiles WHERE id::text = %s", (persona_id,))
            return None if row is None else {"kind": "gm", "ref": persona_id, "name": row[0]}
        if not self._table("persona_core_aliases"):
            return None
        self.cur.execute("""SELECT namespace, external_id FROM persona_core_aliases
                            WHERE persona_type = %s AND persona_id = %s AND namespace = ANY(%s)""",
                         (role, persona_id, list(NAMESPACE_KIND)))
        for ns, external in self.cur.fetchall():
            kind = NAMESPACE_KIND[ns]
            sql = {"player": "SELECT coalesce(full_name, player_name) FROM players WHERE id::text = %s",
                   "coach": "SELECT coach_name FROM coaches WHERE id::text = %s",
                   "coordinator": "SELECT coordinator_name FROM coordinators WHERE id::text = %s",
                   "owner": "SELECT owner_name, (SELECT abbreviation FROM teams t WHERE t.id = o.team_id) "
                            "FROM team_owners o WHERE id::text = %s",
                   "scout": "SELECT name, (SELECT abbreviation FROM teams t WHERE t.id = d.team_id) "
                            "FROM scout_directors d WHERE id::text = %s"}[kind]
            row = self._one(sql, (external,))
            if row is not None:
                ref = row[1] if kind in ("owner", "scout") else external
                return {"kind": kind, "ref": str(ref), "name": row[0]}
        return None

    # -- the write
    def commit(self):
        self.conn.commit()

    def override(self, identity, request):
        from personality import store
        return store.override(self.cur, identity, request)


def _writer_names() -> dict:
    try:
        with open(legacy.PERSONAS_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    items = data.get("archetypes", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [dict(v, id=k) if isinstance(v, dict) else {"id": k} for k, v in items.items()]
    out = {}
    for p in items or []:
        if isinstance(p, dict):
            key = p.get("id") or p.get("persona_id") or p.get("key")
            if key:
                out[str(key)] = p.get("name") or p.get("display_name") or str(key).replace("_", " ").title()
    return out


def get_sources():
    """FastAPI dependency: one connection per request, rolled back after reads (the override commits itself)."""
    import psycopg
    conn = psycopg.connect(CONN_INFO)
    try:
        yield Sources(conn)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


# ------------------------------------------------------------------------------------------- endpoints ---

class OverrideIn(BaseModel):
    sliders: Optional[dict] = None
    add_traits: Optional[list] = None
    remove_traits: Optional[list] = None
    reason: str = ""
    expected_revision: Optional[int] = None


def require_token(token: Optional[str]):
    from pipeline.notify import env
    expected = env("DEBUG_TOKEN")
    if not expected:
        raise HTTPException(403, "DEBUG_TOKEN isn't set in .env; edits are off")
    if token != expected:
        raise HTTPException(403, "wrong or missing debug token")


@router.get("/catalogue")
def get_catalogue(role: str = "player"):
    return catalogue_body(role, legacy.load_traits())


@router.get("/{kind}/{ref}")
def get_profile(kind: str, ref: str, src=Depends(get_sources)):
    return build_profile(kind, ref, src)


@router.post("/{kind}/{ref}/override")
def post_override(kind: str, ref: str, body: OverrideIn, x_debug_token: Optional[str] = Header(None),
                  src=Depends(get_sources)):
    from personality.core import ConflictError, NotFoundError, PersonalityError
    require_token(x_debug_token)
    if kind not in KINDS:
        raise HTTPException(404, "unknown_kind")
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(422, "a reason is required")
    who = src.resolve(kind, ref)
    if who.get("status") == "hidden_until_drafted":
        raise HTTPException(404, "hidden_until_drafted")
    if who.get("status") != "ok":
        raise HTTPException(404, "no_profile")
    if who.get("identity") is None:
        raise HTTPException(422, "not managed by the personality core yet (adopt it first)")
    profile = who["profile"]
    season, tick = src.clock()
    request = {"override_id": "site-" + uuid.uuid4().hex, "actor": "JR", "reason": reason, "season": season,
               "tick": tick,
               "expected_revision": profile["revision"] if body.expected_revision is None else body.expected_revision}
    if body.sliders:
        request["sliders"] = dict(body.sliders)
    if body.add_traits:
        request["add_traits"] = list(body.add_traits)
    if body.remove_traits:
        request["remove_traits"] = list(body.remove_traits)
    try:
        src.override(who["identity"], request)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except PersonalityError as e:
        raise HTTPException(422, str(e))
    src.commit()
    return build_profile(kind, ref, src)
