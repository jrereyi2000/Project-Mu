"""The shared agent-loop base (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.1).

Generalizes the trade talks plan's GM loop (§2.1-§2.3) for coaches and players:

  Option(key, action, detail, label)        label is what the model sees; never a hidden score
  seed_for(season, tick, kind, subject) / rng_for(...)   "loop:{season}:{tick}:{kind}:{subject}"
  policy_choice(options, persona, weights, rng) -> key   seeded weighted draw (weights: {key: w} or callable)
  model_choice(llm, persona_block, facts, options, history) -> (key | None, text | None, source)
  grounded(text, facts) -> bool             names, slots and figures in the text must appear in the facts
  decide(cur, *, league_id, season, tick, kind, subject, options, persona, weights, facts, seen, llm, ...) -> row
  marquee_available(store, league_id, season, tick) -> bool   MARQUEE_PER_WEEK model-backed CPU cases a week
  MemoryStore() / SqlStore(cur)             agent_loop_decisions access (decide uses SqlStore(cur) by default)
  make_loop_llm(**kw)                       gm_llm.make_llm('agent_loop', ...)

decide: record, don't replay. A (league_id, season, tick, kind, subject_key) already decided returns the stored
row (replayed=True) without a model call. Otherwise the seeded policy always runs; when the case is seen (JR's
thread, a presser answer, a leak, or a marquee CPU case within the week's budget) the model chooses among the same
options: a key outside them falls back to the policy key (MODEL_REJECTED, its text dropped), no answer at all (no
llm, a failed call) leaves the policy's choice (POLICY), and a MODEL choice's text is kept only when grounded.

Weights are each loop's own slider/trait coefficients, all review-only estimates (plan §2.2-§2.3).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import random
import re

MARQUEE_PER_WEEK = 4          # model calls for CPU cases per league week (JR, 2026-11-03, plan §8 Q1)
TABLE = "agent_loop_decisions"
KINDS = ("CHART", "PROMISE", "PROMISE_REVIEW", "CONTRACT", "INBOX_REPLY", "LEAK")
MARQUEE_KINDS = ("CHART", "PROMISE", "CONTRACT")      # CPU cases that spend the week's model budget
SOURCES = ("POLICY", "MODEL", "MODEL_REJECTED")
VISIBILITIES = ("private", "public")
LLM_LABEL = "agent_loop"
TEXT_MAX = 1200               # characters of model text kept


class AgentLoopError(ValueError):
    """Invalid options, kind or decision input."""


@dataclass(frozen=True)
class Option:
    key: str
    action: str
    detail: dict = field(default_factory=dict)
    label: str = ""

    def as_dict(self):
        return {"key": self.key, "action": self.action, "detail": deepcopy(self.detail), "label": self.label}


def option_dicts(options):
    return [o.as_dict() if isinstance(o, Option) else dict(o) for o in options]


def _check_options(options):
    rows = option_dicts(options)
    if not rows:
        raise AgentLoopError("a decision needs at least one option")
    keys = [r.get("key") for r in rows]
    if any(not isinstance(k, str) or not k for k in keys):
        raise AgentLoopError("every option needs a text key")
    if len(set(keys)) != len(keys):
        raise AgentLoopError(f"duplicate option keys: {keys}")
    return rows


# ------------------------------------------------------------------------------------------------ policy ---

def seed_for(season, tick, kind, subject):
    return f"loop:{season}:{tick}:{kind}:{subject}"


def rng_for(season, tick, kind, subject):
    return random.Random(seed_for(season, tick, kind, subject))


def traits_of(persona):
    return frozenset(str(t) for t in ((persona or {}).get("traits") or []))


def slider(persona, name, default=5.0):
    value = ((persona or {}).get("sliders") or {}).get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return float(default)
    return float(value)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def policy_choice(options, persona, weights, rng):
    """A seeded weighted draw over the options. weights: {key: w >= 0} (missing keys weigh 0) or a callable
    (option dict, persona) -> w. All-zero weights take the first option. The option order is the tie-break."""
    rows = _check_options(options)
    ws = []
    for r in rows:
        w = weights(r, persona) if callable(weights) else (weights or {}).get(r["key"], 0.0)
        w = float(w or 0.0)
        if not math.isfinite(w) or w < 0:
            raise AgentLoopError(f"weight for {r['key']!r} must be finite and >= 0")
        ws.append(w)
    total = sum(ws)
    if total <= 0:
        return rows[0]["key"]
    x = rng.random() * total
    for r, w in zip(rows, ws):
        if x < w:
            return r["key"]
        x -= w
    return rows[-1]["key"]


# --------------------------------------------------------------------------------------------- grounding ---

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CAPS = re.compile(r"\b[A-Z]{2,5}\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
ALLOWED_CAPS = frozenset({"I", "OK", "NFL", "GM", "HC", "OC", "DC", "TV"})
ALLOWED_NAMES = frozenset({"I", "I'm", "I'll", "I've", "I'd", "Coach", "Mr", "Mrs", "Ms", "God"})


def _flatten(value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            _flatten(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flatten(v, out)
    elif value is not None and not isinstance(value, bool):
        out.append(str(value))
    return out


def _num_forms(token):
    """Comparable spellings of a number: '12,500,000' -> {'12500000'}; 12500000 -> also '12.5' (millions)."""
    raw = token.replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return set()
    forms = {f"{v:g}"}
    if v >= 100_000:
        forms.add(f"{v / 1e6:g}")
        forms.add(f"{round(v / 1e6, 1):g}")
        forms.add(f"{round(v / 1e6, 2):g}")
    return forms


def grounded(text, facts):
    """True when every figure, all-caps slot/abbreviation and mid-sentence capitalized name in `text` appears in
    `facts` (any JSON value). A conservative check: a failure only drops the model's words, never the decision."""
    if not isinstance(text, str) or not text.strip():
        return False
    pieces = _flatten(facts, [])
    blob = " ".join(pieces)
    numbers = set()
    for p in pieces:
        for tok in _NUM.findall(p):
            numbers |= _num_forms(tok)
    for tok in _NUM.findall(text):
        forms = {f"{float(tok.replace(',', '')):g}"} if tok.replace(",", "").replace(".", "").isdigit() else set()
        if forms and not (forms & numbers):
            return False
    fact_caps = set(_CAPS.findall(blob))
    for tok in _CAPS.findall(text):
        if tok not in ALLOWED_CAPS and tok not in fact_caps:
            return False
    fact_words = set(_WORD.findall(blob))
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        words = _WORD.findall(sentence)
        for w in words[1:]:
            if w[0].isupper() and not w.isupper() and w not in ALLOWED_NAMES and w not in fact_words:
                return False
    return True


# ------------------------------------------------------------------------------------------------- model ---

def persona_block_for(persona):
    """Plain text for the model: sliders, traits and an optional voice line. No engine numbers."""
    persona = persona or {}
    sliders = persona.get("sliders") or {}
    lines = []
    if persona.get("name"):
        lines.append(f"You are {persona['name']}.")
    if sliders:
        lines.append("Personality (0-10): " + ", ".join(f"{k} {v}" for k, v in sorted(sliders.items())))
    if persona.get("traits"):
        lines.append("Traits: " + ", ".join(sorted(str(t) for t in persona["traits"])))
    if persona.get("voice"):
        lines.append(f"Voice: {persona['voice']}")
    return "\n".join(lines)


def build_prompt(persona_block, facts, options, history=()):
    rows = _check_options(options)
    parts = [persona_block or "", "",
             "Facts (the only names, positions and figures you may use):",
             json.dumps(facts, sort_keys=True, default=str), ""]
    if history:
        parts.append("So far:")
        parts.extend(f"- {h}" for h in history)
        parts.append("")
    parts.append("Choose exactly one option by its key:")
    parts.extend(f"- {r['key']}: {r.get('label') or r['action']}" for r in rows)
    parts += ["", 'Answer with JSON only: {"choice": "<key>", "message": "<2-4 sentences in your voice>"}']
    return "\n".join(parts)


def _parse(answer):
    if not isinstance(answer, str):
        return None
    s = answer.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def model_choice(llm, persona_block, facts, options, history=()):
    """One model call. Returns (key, text, source): source MODEL with a key from the options, MODEL_REJECTED with
    key None when the answer names anything else or can't be read, POLICY with key None when there is no llm or the
    call failed (nothing was chosen). Text is the raw message; decide() checks its grounding."""
    rows = _check_options(options)
    if llm is None:
        return None, None, "POLICY"
    from engines import gm_llm
    answer = gm_llm.call(llm, build_prompt(persona_block, facts, rows, history))
    if answer is None:
        return None, None, "POLICY"
    data = _parse(answer)
    text = data.get("message") if data else None
    text = text.strip()[:TEXT_MAX] if isinstance(text, str) and text.strip() else None
    key = data.get("choice") if data else None
    if key not in {r["key"] for r in rows}:
        return None, text, "MODEL_REJECTED"
    return key, text, "MODEL"


def make_loop_llm(**kw):
    """The live loop model (PERSONALITY_PLAN §8: cloud model for now), or None without a key."""
    from engines import gm_llm
    return gm_llm.make_llm(LLM_LABEL, **kw)


# ------------------------------------------------------------------------------------------------- store ---

ROW_FIELDS = ("league_id", "season", "tick", "kind", "subject_key", "persona_type", "persona_id", "options",
              "policy_key", "chosen_key", "choice_source", "text", "visibility", "facts", "cause_refs")


def _key(league_id, season, tick, kind, subject_key):
    return (int(league_id), int(season), int(tick), kind, subject_key)


class MemoryStore:
    """In-memory agent_loop_decisions (tests and offline previews)."""

    def __init__(self):
        self.rows = {}

    def get(self, league_id, season, tick, kind, subject_key):
        row = self.rows.get(_key(league_id, season, tick, kind, subject_key))
        return deepcopy(row) if row else None

    def put(self, row):
        k = _key(row["league_id"], row["season"], row["tick"], row["kind"], row["subject_key"])
        if k in self.rows:
            return deepcopy(self.rows[k]), False
        self.rows[k] = deepcopy(row)
        return deepcopy(row), True

    def find(self, league_id, season, kind):
        return [deepcopy(r) for k, r in sorted(self.rows.items(), key=lambda kv: kv[0][:3] + (kv[0][4],))
                if k[0] == int(league_id) and k[1] == int(season) and k[3] == kind]

    def count_model(self, league_id, season, tick, kinds=MARQUEE_KINDS):
        return sum(1 for k, r in self.rows.items()
                   if k[:3] == (int(league_id), int(season), int(tick)) and k[3] in kinds
                   and r["choice_source"] != "POLICY")


_SELECT = ("SELECT league_id, season, tick, kind, subject_key, persona_type, persona_id, options, policy_key, "
           "chosen_key, choice_source, text, visibility, facts, cause_refs FROM agent_loop_decisions ")


class SqlStore:
    """agent_loop_decisions through a psycopg cursor; the caller owns the transaction."""

    def __init__(self, cur):
        self.cur = cur

    @staticmethod
    def _row(r):
        out = dict(zip(ROW_FIELDS, r))
        for k in ("options", "facts", "cause_refs"):
            if isinstance(out[k], str):
                out[k] = json.loads(out[k])
        return out

    def get(self, league_id, season, tick, kind, subject_key):
        self.cur.execute(_SELECT + "WHERE league_id = %s AND season = %s AND tick = %s AND kind = %s "
                         "AND subject_key = %s", (league_id, season, tick, kind, subject_key))
        r = self.cur.fetchone()
        return self._row(r) if r else None

    def put(self, row):
        from psycopg.types.json import Jsonb
        vals = [row[f] for f in ROW_FIELDS]
        for i, f in enumerate(ROW_FIELDS):
            if f in ("options", "facts", "cause_refs"):
                vals[i] = Jsonb(vals[i])
        self.cur.execute("INSERT INTO agent_loop_decisions (" + ", ".join(ROW_FIELDS) + ") VALUES ("
                         + ", ".join(["%s"] * len(ROW_FIELDS)) + ") "
                         "ON CONFLICT (league_id, season, tick, kind, subject_key) DO NOTHING RETURNING id", vals)
        if self.cur.fetchone():
            return deepcopy(row), True
        return self.get(row["league_id"], row["season"], row["tick"], row["kind"], row["subject_key"]), False

    def find(self, league_id, season, kind):
        self.cur.execute(_SELECT + "WHERE league_id = %s AND season = %s AND kind = %s ORDER BY tick, subject_key",
                         (league_id, season, kind))
        return [self._row(r) for r in self.cur.fetchall()]

    def count_model(self, league_id, season, tick, kinds=MARQUEE_KINDS):
        self.cur.execute("SELECT count(*) FROM agent_loop_decisions WHERE league_id = %s AND season = %s "
                         "AND tick = %s AND kind = ANY(%s) AND choice_source <> 'POLICY'",
                         (league_id, season, tick, list(kinds)))
        return int(self.cur.fetchone()[0])


def store_for(cur, store=None):
    if store is not None:
        return store
    if hasattr(cur, "get") and hasattr(cur, "put") and hasattr(cur, "count_model"):
        return cur                      # a store passed where a cursor is expected
    return SqlStore(cur)


def marquee_available(store, league_id, season, tick, limit=MARQUEE_PER_WEEK):
    """True while fewer than `limit` model-backed CPU cases (MARQUEE_KINDS) were decided this league week."""
    return store.count_model(league_id, season, tick) < limit


# ------------------------------------------------------------------------------------------------ decide ---

def decide(cur, *, league_id, season, tick, kind, subject, options, persona, weights, facts, seen, llm,
           marquee=False, persona_block=None, history=(), cause_refs=(), visibility="private", store=None):
    """The decision for one case (module docstring). seen: JR's thread, a presser answer or a leak (always use the
    model). marquee: a CPU case that may use the model while the week's MARQUEE_PER_WEEK budget lasts. Returns the
    stored row plus `option` (the chosen option dict) and `replayed`."""
    if kind not in KINDS:
        raise AgentLoopError(f"kind must be one of {KINDS}")
    if visibility not in VISIBILITIES:
        raise AgentLoopError("visibility must be private or public")
    if not isinstance(subject, str) or not subject:
        raise AgentLoopError("subject must be text")
    rows = _check_options(options)
    st = store_for(cur, store)
    prior = st.get(league_id, season, tick, kind, subject)
    if prior is not None:
        return _finish(prior, replayed=True)
    rng = rng_for(season, tick, kind, subject)
    policy_key = policy_choice(rows, persona, weights, rng)
    use_model = bool(seen) or (bool(marquee) and kind in MARQUEE_KINDS
                               and marquee_available(st, league_id, season, tick))
    chosen, source, text = policy_key, "POLICY", None
    if use_model and llm is not None:
        block = persona_block if persona_block is not None else persona_block_for(persona)
        key, raw, source = model_choice(llm, block, facts, rows, history)
        if source == "MODEL":
            chosen = key
        if raw and source == "MODEL" and grounded(raw, facts):
            text = raw
    persona = persona or {}
    row = {"league_id": int(league_id), "season": int(season), "tick": int(tick), "kind": kind,
           "subject_key": subject, "persona_type": persona.get("persona_type") or persona.get("role"),
           "persona_id": persona.get("persona_id"), "options": rows, "policy_key": policy_key,
           "chosen_key": chosen, "choice_source": source, "text": text, "visibility": visibility,
           "facts": deepcopy(facts), "cause_refs": list(cause_refs)}
    stored, _new = st.put(row)
    return _finish(stored, replayed=not _new)


def _finish(row, *, replayed):
    out = deepcopy(row)
    out["option"] = next((o for o in out["options"] if o["key"] == out["chosen_key"]), None)
    out["replayed"] = replayed
    return out
