"""Two-GM CPU trade talks (engines/gm_talks.py). Synthetic books: no database except scripted cursors."""
import json
import random
import re
import time

from engines import gm_negotiation as gn
from engines import gm_talks as gt
from engines import gm_trade_engine as ge
from tests.test_gm_trade_engine import FITS, STAR, deal, pk, pl, star_books

# STAR (WR 82, team 2) for the two picks team 1 owns: a package both GMs accept, so a talk can open on it.
OPENING = [pl(STAR, 2, 1), pk(4, 1, 1, 2), pk(5, 1, 1, 2)]
TALK = {"id": 1, "season": 2036, "week": 4, "trade_window": "IN_SEASON", "user_team_id": 1,
        "gm_team_id": 2, "initiator_team_id": 1, "patience": 4, "patience_left": 4, "carried_weeks": 0,
        "counter_counts": {}}


def setup(**kw):
    b = star_books(**kw)
    return b, ge.Valuer(b, FITS)


def keys(assets):
    return {ge.asset_key(x) for x in assets or []}


def persona(agg=0.5, traits=(), **sl):
    sliders = {"temperament": 5, "sociability": 5, "confidence": 5, "openness": 5, "warmth": 5, "drive": 5}
    sliders.update(sl)
    return {"gm_name": "Bob B", "aggressiveness": agg, "traits": list(traits), "sliders": sliders}


def _choose(b, v, talk, actor, offer, opening=False, llm=None, turn_no=1):
    opts = gt.options_for(b, v, dict(talk, opening=True) if opening else talk, actor, offer)
    if llm is not None:
        sheet = gn.message_sheet(b, 1 if actor == 2 else 2, actor, offer)
        key, text, source = gt.llm_choose(llm, sheet, dict(b["gm"][actor]), opts, [])
    else:
        key = gt.policy_choice(opts, dict(b["gm"][actor]),
                               random.Random(f"talk:{talk['season']}:{talk['id']}:{turn_no}"))
        text, source = gt._template_line(b["gm"][actor], next(o for o in opts if o.key == key)), "POLICY"
    return next(o for o in opts if o.key == key), text, source


def simulate(b, v, opening=OPENING, llm=None, patience=(4, 4), max_turns=8, nid=1):
    """A scripted two-persona talk (the pure turn core: options + policy/model), no database."""
    talk = dict(TALK, id=nid, patience=patience[0], patience_left=patience[1])
    turns = []
    opt, text, source = _choose(b, v, talk, 1, opening, opening=True, llm=llm, turn_no=1)
    turns.append({"actor_team_id": 1, "action": "OFFER", "assets": opt.assets, "message": text})
    offer = opt.assets
    for n in range(2, max_turns + 1):
        actor = 2 if turns[-1]["actor_team_id"] == 1 else 1
        opt, text, source = _choose(b, v, talk, actor, offer, llm=llm, turn_no=n)
        if opt.action == "ACCEPT":
            return "AGREED", offer
        if opt.action == "WALK_AWAY":
            return "WALKED_AWAY", offer
        offer = opt.assets or offer
        turns.append({"actor_team_id": actor, "action": opt.action, "assets": opt.assets, "message": text})
        col = "patience" if actor == 1 else "patience_left"
        talk[col] = max(0, talk[col] - 1)
        if talk[col] <= 0:
            return "WALKED_AWAY", offer
    return "STALLED", offer


def fake_model(prompt):
    """A model that takes ACCEPT when it is legal, else the FIRM/first move: drives a talk to AGREED."""
    found = re.findall(r"- \[([^\]]+)\]", prompt)
    key = "ACCEPT" if "ACCEPT" in found else ("FIRM" if "FIRM" in found else (found[0] if found else "REJECT"))
    return json.dumps({"choice": key, "message": "We can do that. Send it over."})


# ------------------------------------------------------------------ options ----
def test_options_are_all_legal_and_keep_the_core():
    b, v = setup()
    opening = gt.options_for(b, v, dict(TALK, opening=True), 1, OPENING)
    assert opening[0].key == "FIRM" and keys(opening[0].assets) == keys(OPENING)
    assert any(o.posture == "ANCHOR" for o in opening)
    for o in opening:
        assert ge.legal(ge.check_legality({"a": 1, "b": 2, "assets": o.assets}, b, None))
    lowball = [pl(STAR, 2, 1)]                                   # the responder sees counters, no ACCEPT
    resp = gt.options_for(b, v, TALK, 2, lowball)
    assert not any(o.action == "ACCEPT" for o in resp)
    for o in resp:
        if o.action == "COUNTER":
            assert ge.legal(ge.check_legality({"a": 1, "b": 2, "assets": o.assets}, b, None))
            assert ("P", STAR) in keys(o.assets)                 # the core never comes off


def test_accept_appears_only_above_the_actor_floor():
    b, v = setup()
    assert any(o.action == "ACCEPT" for o in gt.options_for(b, v, TALK, 2, OPENING))
    assert not any(o.action == "ACCEPT" for o in gt.options_for(b, v, TALK, 2, [pl(STAR, 2, 1)]))


def test_patience_zero_offers_only_walk_away():
    b, v = setup()
    opts = gt.options_for(b, v, dict(TALK, patience_left=0), 2, [pl(STAR, 2, 1)])
    assert [(o.key, o.action) for o in opts] == [("WALK_AWAY", "WALK_AWAY")]


def test_an_anchor_can_sit_below_the_other_floor():
    b, v = setup()
    opening = gt.options_for(b, v, dict(TALK, opening=True), 1, OPENING)
    anchor = next(o for o in opening if o.posture == "ANCHOR")
    j = gn.judge(b, v, {"a": 1, "b": 2, "assets": anchor.assets}, 1, 2, 0)
    assert not j["ok"] and j["mood"] == "CLOSE"                  # posturing below the floor, still legal
    assert ge.legal(j["checks"])


def test_policy_is_seeded_and_follows_sliders():
    b, v = setup()
    opening = gt.options_for(b, v, dict(TALK, opening=True), 1, OPENING)
    p = persona()
    assert gt.policy_choice(opening, p, random.Random(7)) == gt.policy_choice(opening, p, random.Random(7))
    wild = persona(agg=0.9, confidence=10, warmth=1)
    mild = persona(agg=0.1, confidence=1, warmth=10)
    n_wild = sum(gt.policy_choice(opening, wild, random.Random(i)).startswith("ANCHOR") for i in range(1000))
    n_mild = sum(gt.policy_choice(opening, mild, random.Random(i)).startswith("ANCHOR") for i in range(1000))
    assert n_wild > n_mild and n_mild <= 80


# ------------------------------------------------------------------ model ----
def test_model_choice_outside_the_list_falls_back_to_policy():
    b, v = setup()
    opts = gt.options_for(b, v, TALK, 2, OPENING)
    sheet = gn.message_sheet(b, 1, 2, OPENING)
    key, text, source = gt.llm_choose(lambda p: '{"choice": "NOPE", "message": "We can do that."}',
                                      sheet, persona(), opts, [])
    assert source == "POLICY" and key in {o.key for o in opts}


def test_invalid_message_falls_back_to_the_template():
    b, v = setup()
    opts = gt.options_for(b, v, TALK, 2, OPENING)
    sheet = gn.message_sheet(b, 1, 2, OPENING)
    key, text, source = gt.llm_choose(
        lambda p: '{"choice": "ACCEPT", "message": "Give me 3 firsts and he is yours."}', sheet, persona(), opts, [])
    assert key == "ACCEPT" and source == "LLM" and "3 firsts" not in text
    assert text.startswith("Bob B:")


# ------------------------------------------------------------------ talks ----
def test_two_fixed_personas_converge_to_agreed_and_the_deal_passes_accepts():
    b, v = setup()
    b["gm"][1].update(aggressiveness=0.12, sliders=persona(agg=0.12, confidence=1, warmth=10)["sliders"])
    b["gm"][2].update(aggressiveness=0.12, sliders=persona(agg=0.12)["sliders"])
    status, final = simulate(b, v)
    assert status == "AGREED"
    assert ge.accepts(deal(final), v, b)[0]


def test_the_same_talk_agrees_with_a_fake_model():
    b, v = setup()
    b["gm"][1].update(sliders=persona(confidence=1, warmth=10)["sliders"])
    status, final = simulate(b, v, llm=fake_model)
    assert status == "AGREED" and ge.accepts(deal(final), v, b)[0]


def test_an_anchor_met_with_no_patience_stalls():
    b, v = setup()
    b["gm"][1].update(aggressiveness=0.9, sliders=persona(agg=0.9, confidence=10, warmth=1)["sliders"])
    status, final = simulate(b, v, patience=(4, 0))
    assert status == "WALKED_AWAY"                               # a breakdown: advance_talk stores it STALLED
    assert not ge.accepts(deal(final), v, b)[0]


def test_a_breakdown_stores_a_stalled_proposal(monkeypatch):
    b, v = setup()
    calls = {}
    monkeypatch.setattr(ge, "rationale_for", lambda b, d, llm: {"rationale_a": "", "rationale_b": "",
                                                                "headline": "", "source": "TEMPLATE"})
    monkeypatch.setattr(ge, "store", lambda *a, **k: (calls.update(kwargs=k, args=a), 9)[1])
    cur = ScriptedCur(lambda sql, params: [])
    assert gt._stall(cur, b, v, dict(TALK, flags=["stage:3", "shape:RENTAL"]), OPENING, "broke down") == 9
    assert calls["kwargs"]["status"] == "STALLED"
    assert calls["args"][5] == ["talks:1", "stage:3", "shape:RENTAL"]     # the deal's flags ride along
    assert any("STALLED" in p for p in cur.params)


def test_an_agreement_stores_approved_with_the_talk_flags(monkeypatch):
    b, v = setup()
    seen = {}
    monkeypatch.setattr(ge, "check_legality", lambda d, b, valuer=None: {})
    monkeypatch.setattr(ge, "rationale_for", lambda b, d, llm: {"rationale_a": "", "rationale_b": "",
                                                                "headline": "", "source": "TEMPLATE"})
    monkeypatch.setattr(ge, "release_flags", lambda checks, b: [])
    monkeypatch.setattr(ge, "llm_flags", lambda rat: [])
    monkeypatch.setattr(ge, "store", lambda *a, **k: (seen.update(kwargs=k, flags=a[5]), 9)[1])
    assert gt._agree(ScriptedCur(lambda sql, params: []), b, v, TALK, OPENING, None) == 9
    assert seen["kwargs"]["status"] == "APPROVED"
    assert "talks:1" in seen["flags"] and "auto_approved" in seen["flags"]


def test_an_agreement_carries_the_deals_flags_and_a_resumed_talk_with_none_still_agrees(monkeypatch):
    """A talk opened from a CPU pick stores that pick's flags too (talks:/auto_approved still first); a talk
    resumed from the database (no "flags" key) keeps working with empty flags (V2.11 U8)."""
    b, v = setup()
    seen = {}
    monkeypatch.setattr(ge, "check_legality", lambda d, b, valuer=None: {})
    monkeypatch.setattr(ge, "rationale_for", lambda b, d, llm: {"rationale_a": "", "rationale_b": "",
                                                                "headline": "", "source": "TEMPLATE"})
    monkeypatch.setattr(ge, "release_flags", lambda checks, b: [])
    monkeypatch.setattr(ge, "llm_flags", lambda rat: [])
    monkeypatch.setattr(ge, "store", lambda *a, **k: (seen.update(flags=a[5]), 9)[1])
    talk = dict(TALK, flags=["stage:3", "shape:BLOCKBUSTER"])
    assert gt._agree(ScriptedCur(lambda sql, params: []), b, v, talk, OPENING, None) == 9
    assert seen["flags"][:2] == ["talks:1", "auto_approved"]
    assert "stage:3" in seen["flags"] and "shape:BLOCKBUSTER" in seen["flags"]
    resumed = dict(TALK, flags=[])                              # a resumed talk's stored flags
    assert gt._agree(ScriptedCur(lambda sql, params: []), b, v, resumed, OPENING, None) == 9
    assert seen["flags"] == ["talks:1", "auto_approved"]


OPEN_TALK_ROW = (7, 2036, 24, "OFFSEASON", 3, 1, 2, "CPU", 1, None, 0, None, "OPEN", None, 4, 4, None, "INJURY",
                 "2026-01-01", "2026-01-02", [])


def open_talk_router(sql, params):
    if "INSERT INTO gm_trade_negotiations" in sql:
        return [(7,)]
    if "FROM gm_trade_negotiations WHERE id" in sql:
        return [OPEN_TALK_ROW]
    if "COALESCE(max(turn_no)" in sql:
        return [(0,)]
    return []


def test_open_talk_stores_the_deals_flags_for_a_carried_talk():
    b, v = setup()
    cur = ScriptedCur(open_talk_router)
    d = deal(OPENING)
    d["flags"] = ["stage:3", "shape:BLOCKBUSTER"]
    talk = gt.open_talk(cur, b, v, d, random.Random(1))
    insert = next(p for s, p in zip(cur.sql, cur.params) if "INSERT INTO gm_trade_negotiations" in s)
    assert talk["id"] == 7 and insert[-1] == ["stage:3", "shape:BLOCKBUSTER"]     # P1-16: on the row


def test_a_resumed_talk_reads_its_stored_flags():
    """P1-16: a talk that carried over reads shape:/stage: from its row, so its eventual proposal keeps them."""
    row = OPEN_TALK_ROW[:-1] + (["stage:3", "shape:BLOCKBUSTER"],)
    talk = gt._talk(ScriptedCur(lambda sql, params: [row]), 7)
    assert talk["flags"] == ["stage:3", "shape:BLOCKBUSTER"]


def test_open_talk_stores_the_deals_trigger_for_a_carried_talk():
    b, v = setup()
    cur = ScriptedCur(open_talk_router)
    d = deal(OPENING)
    d["trigger"] = "SELL"
    talk = gt.open_talk(cur, b, v, d, random.Random(1))
    insert = next(p for s, p in zip(cur.sql, cur.params) if "INSERT INTO gm_trade_negotiations" in s)
    assert insert[-2] == "SELL" and insert[-1] == [] and talk["trigger_kind"] == "INJURY"
    # the trigger and the flags are on the row, which is what a resumed talk reads (P1-16)


def test_an_agreed_talk_uses_the_trigger_stored_on_it(monkeypatch):
    b, v = setup()
    seen = {}
    monkeypatch.setattr(ge, "rationale_for", lambda *a, **k: {"rationale_a": "", "rationale_b": "",
                                                              "headline": "", "source": "TEMPLATE"})
    monkeypatch.setattr(ge, "store", lambda *a, **k: (seen.update(k), 9)[1])
    pid = gt._agree(ScriptedCur(lambda s, p: []), b, v, dict(TALK, status="OPEN", trigger_kind="INJURY"),
                    OPENING, None)
    assert pid == 9 and seen["trigger"] == "INJURY"


def _run_limit_turns():
    """Eight turns (the talk's maximum) whose last is a REJECT with no assets; the opening carries the offer."""
    stored = [{"type": "PLAYER", "player_id": STAR, "from": "BBB", "to": "AAA"},
              {"type": "PICK", "draft_season": 2037, "round": 4, "original_team": "AAA",
               "from": "AAA", "to": "BBB"}]
    turns = [{"turn_no": i, "actor": "GM_A" if i % 2 else "GM", "actor_team_id": 1 if i % 2 else 2,
              "action": "OFFER" if i == 1 else "REJECT", "assets": stored if i == 1 else None,
              "mood": "APART", "message": None, "surplus_user": None, "surplus_gm": None, "options": None,
              "choice": None, "choice_source": None, "posture": None, "legality": None,
              "message_source": None} for i in range(1, gt.TALK_MAX_TURNS + 1)]
    return turns, stored


def _run_limit_stall(monkeypatch, store=None, extra=None):
    b, v = setup()
    turns, stored = _run_limit_turns()
    monkeypatch.setattr(gt, "_turns", lambda cur, nid: list(turns))
    monkeypatch.setattr(gt, "carry_allowed", lambda *a, **k: False)
    monkeypatch.setattr(ge, "rationale_for", lambda b, d, llm: {"rationale_a": "", "rationale_b": "",
                                                                "headline": "", "source": "TEMPLATE"})
    monkeypatch.setattr(ge, "store", store or (lambda *a, **k: 9))
    for name, fn in dict({"leak_events": lambda *a, **k: []}, **(extra or {})).items():
        monkeypatch.setattr(gt, name, fn)
    talk = dict(TALK, status="OPEN")
    status = gt.advance_talk(ScriptedCur(lambda sql, params: []), b, v, talk, random.Random(1), None)
    return b, v, stored, talk, status


def test_a_run_limit_stall_stores_the_last_offer_not_the_reject(monkeypatch):
    seen = {}
    b, v, stored, talk, status = _run_limit_stall(
        monkeypatch, store=lambda *a, **k: (seen.update(deal=a[2]), 9)[1])
    assert status == "STALLED" and talk["status"] == "STALLED"
    # the STALLED row is the previous package, not the assetless REJECT turn
    assert keys(seen["deal"]["assets"]) == keys(gt._engine_assets(b, stored))


def test_a_run_limit_stall_leaks_at_the_stalled_stage(monkeypatch):
    seen = []

    def fake_events(talk, turn, books, rng, turns=None):
        seen.append((gt._leak_stage(talk, turn, turns), bool(turn.get("assets"))))
        return []

    b, v, stored, talk, status = _run_limit_stall(monkeypatch, extra={"leak_events": fake_events})
    assert status == "STALLED" and talk["status"] == "STALLED"
    assert seen and seen[-1] == ("STALLED", True)            # highest weight, and the offer's positions


def test_carry_over_only_from_close_never_past_the_deadline_at_most_once():
    b, v = setup()
    t = dict(TALK)
    assert gt.carry_allowed(t, b, "CLOSE", random.Random(1))
    assert not gt.carry_allowed(t, b, "INSULTED", random.Random(1))
    assert not gt.carry_allowed(t, b, "APART", random.Random(1))
    assert not gt.carry_allowed(dict(t, carried_weeks=1), b, "CLOSE", random.Random(1))
    assert not gt.carry_allowed(t, dict(b, week=ge.TRADE_DEADLINE_WEEK), "CLOSE", random.Random(1))
    off = dict(b, window="OFFSEASON")
    assert gt.carry_allowed(dict(t, offseason_stage=1), off, "CLOSE", random.Random(1))
    assert not gt.carry_allowed(dict(t, offseason_stage=len(ge.OFFSEASON_STAGES)), off, "CLOSE",
                                random.Random(1))


def test_a_resumed_talk_with_an_injured_core_stalls():
    b, v = setup()
    assert gt._rejudge(b, v, TALK, [pl(STAR, 2, 1), pk(4, 1, 1, 2)]) is None
    b["injuries"] = {STAR: {"is_ir": False, "weeks_remaining": 4}}
    b["injured"] = {STAR}
    assert gt._rejudge(b, v, TALK, OPENING) == "the deal stopped working"


# ------------------------------------------------------------------ leaks ----
def _leak_turn(assets=None):
    return {"turn_no": 1, "action": "OFFER", "mood": None,
            "assets": assets or [pl(STAR, 2, 1), pk(4, 1, 1, 2)]}


def test_guarded_halves_the_leak_rate():
    b, v = setup()
    b["gm"][1]["sociability"] = 5
    b["gm"][2]["sociability"] = 5
    turn = _leak_turn()
    loud = sum(bool(gt.leak_events(dict(TALK, status="OPEN"), turn, b, random.Random(i))) for i in range(1000))
    for t in (1, 2):
        b["gm"][t]["traits"] = ["GUARDED"]
    quiet = sum(bool(gt.leak_events(dict(TALK, status="OPEN"), turn, b, random.Random(i))) for i in range(1000))
    assert 0 <= quiet < loud
    assert 0.3 < quiet / loud < 0.7


def test_leaks_name_players_only_for_aggressive_leakers():
    b, v = setup()
    b["gm"][1].update(aggressiveness=0.9, traits=[])
    b["gm"][2].update(aggressiveness=0.1, traits=[])
    turn = _leak_turn()
    named = [gt.leak_events(dict(TALK, status="OPEN"), turn, b, random.Random(i)) for i in range(300)]
    named = [e[0] for e in named if e]
    assert named and any(e.named_player_ids for e in named)
    b["gm"][1].update(aggressiveness=0.1)
    passive = [gt.leak_events(dict(TALK, status="OPEN"), turn, b, random.Random(i)) for i in range(300)]
    passive = [e[0] for e in passive if e]
    assert passive and all(not e.named_player_ids for e in passive)


def test_leaks_never_on_an_agreed_turn_and_at_most_one_per_week():
    b, v = setup()
    turn = _leak_turn()
    assert gt.leak_events(dict(TALK, status="AGREED"), turn, b, random.Random(1)) == []
    assert gt.leak_events(dict(TALK, status="OPEN"), dict(turn, action="ACCEPT"), b, random.Random(1)) == []
    assert all(len(gt.leak_events(dict(TALK, status="OPEN"), turn, b, random.Random(i))) <= 1
               for i in range(200))


def test_the_open_stage_is_the_first_turn_a_gm_speaks():
    """A USER talk's turn 1 is JR's own offer: the GM's first reply is what "talks opened" means, not turn 1."""
    talk = dict(TALK, status="OPEN")
    jr_offer = {"turn_no": 1, "actor": "JR", "action": "OFFER"}
    gm_reply = {"turn_no": 2, "actor": "GM", "action": "COUNTER"}
    turns = [jr_offer, gm_reply]
    assert gt._leak_stage(talk, jr_offer, turns) is None
    assert gt._leak_stage(talk, gm_reply, turns) == "OPENED"
    later = turns + [{"turn_no": 3, "actor": "JR", "action": "COUNTER"},
                     {"turn_no": 4, "actor": "GM", "action": "COUNTER"}]
    assert gt._leak_stage(talk, later[-1], later) is None
    cpu = [{"turn_no": 1, "actor": "GM_A", "action": "OFFER"}]
    assert gt._leak_stage(talk, cpu[0], cpu) == "OPENED"
    assert gt._leak_stage(talk, cpu[0]) == "OPENED"          # no transcript given: the old turn-1 rule


def test_record_leaks_is_the_public_writer(monkeypatch):
    b, v = setup()
    leak = gt.Leak(negotiation_id=1, turn_no=2, season=2036, week=4, leaker_team_id=1, stage="OPENED",
                   named_player_ids=[], detail={"teams": ["AAA", "BBB"]}, insider_key=None)
    monkeypatch.setattr(gt, "leak_events", lambda *a, **k: [leak])
    stored = []
    monkeypatch.setattr(gt, "_store_leak", lambda cur, l: stored.append(l) or True)
    n = gt.record_leaks(ScriptedCur(lambda s, p: []), dict(TALK), {"turn_no": 2, "actor": "GM"}, b,
                        random.Random(1), [{"turn_no": 2, "actor": "GM"}])
    assert n == 1 and stored == [leak]


# ------------------------------------------------------------------ transcript ----
class ScriptedCur:
    """A cursor whose every query is answered by a router: `router(sql, params) -> rows`."""

    def __init__(self, router):
        self.router, self.sql, self.params, self.rowcount = router, [], [], 0
        self._rows, self._i = [], 0

    def execute(self, sql, params=()):
        self.sql.append(sql)
        self.params.append(params)
        self._rows, self._i = self.router(sql, params) or [], 0
        return self

    def fetchone(self):
        if self._i < len(self._rows):
            r = self._rows[self._i]
            self._i += 1
            return r
        return None

    def fetchall(self):
        rows = self._rows[self._i:]
        self._i = len(self._rows)
        return rows


TALK_ROW = (41, 2036, 6, "IN_SEASON", None, 1, 2, "CPU", 1, 512, 1, None, "AGREED", None, 4, 4, 512,
            "2026-01-01", "2026-01-02")
TURNS_ROWS = [
    (1, "GM_A", "OFFER", [{"type": "PLAYER", "player_id": 900, "from": "AAA", "to": "BBB"}], 100, 200, None,
     {}, "the opening", "TEMPLATE", 1, [{"key": "FIRM", "action": "OFFER", "assets": [], "su": 1, "sg": 2}],
     "FIRM", "POLICY", "FIRM", "2026-01-01"),
    (2, "GM", "ACCEPT", [{"type": "PLAYER", "player_id": 900, "from": "BBB", "to": "AAA"}], 300, 400, None,
     {"cap": {"ok": True}}, "agreed", "TEMPLATE", 2, [], "ACCEPT", "POLICY", "MEET", "2026-01-02"),
]


def talk_router(sql, params):
    if "FROM gm_trade_negotiations WHERE id" in sql:
        return [TALK_ROW]
    if "FROM teams WHERE id = ANY" in sql:
        return [(1, "AAA"), (2, "BBB")]
    if "FROM gm_profiles WHERE team_id = ANY" in sql:
        return [(1, "Ann A"), (2, "Bob B")]
    if "FROM gm_trade_negotiation_turns WHERE negotiation_id" in sql:
        return list(TURNS_ROWS)
    if "FROM players p" in sql:
        return [(900, "Player 900", "WR", 82)]
    return []


def all_keys(obj):
    out = set()
    if isinstance(obj, dict):
        out |= set(obj)
        for v in obj.values():
            out |= all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= all_keys(v)
    return out


def test_talk_json_public_has_no_surpluses_options_or_legality():
    cur = ScriptedCur(talk_router)
    pub = gt.talk_json(cur, 41, public=True)
    assert pub["weeks"] == [6, 7]
    assert pub["teams"][0]["role"] == "initiator" and pub["teams"][1]["abbr"] == "BBB"
    assert {"su", "sg", "options", "choice_source", "legality"} & all_keys(pub) == set()
    assert pub["turns"][0]["team"] == "AAA" and pub["turns"][1]["action"] == "ACCEPT"
    assert pub["final"]["assets"]
    full = gt.talk_json(ScriptedCur(talk_router), 41, public=False)
    assert {"su", "sg", "options", "choice_source", "legality"} <= all_keys(full)


def test_talk_thread_order_and_shape():
    thread = gt.talk_thread(ScriptedCur(talk_router), 41)
    assert [m["author"] for m in thread] == ["JR", "COUNTERPART"]
    assert thread[0]["engine_ref"] == {"negotiation_id": 41, "turn_no": 1}
    assert set(thread[0]) == {"author", "body", "source", "engine_ref", "created_at"}


# ------------------------------------------------------------------ timeout / queue ----
def test_call_with_timeout_returns_none_on_a_slow_fake():
    assert gt.call_with_timeout(lambda p: (time.sleep(0.2), "late")[1], "p", 0.05) is None
    assert gt.call_with_timeout(lambda p: "fast", "p", 1) == "fast"
    assert gt.call_with_timeout(None, "p", 1) is None


def queue_router(exists=True):
    def router(sql, params):
        if "to_regclass('franchise_pending_moves')" in sql:
            return [(exists,)]
        if "SELECT team_a_id, team_b_id FROM gm_trade_proposals" in sql:
            return [(1, 2)]
        if "SELECT 1 FROM franchise_pending_moves" in sql:
            return []
        if "FROM gm_trade_assets WHERE proposal_id" in sql:
            return [("PLAYER", 900, None, None, None, 1, 2), ("PICK", None, 2037, 4, 1, 1, 2),
                    ("PLAYER", 901, None, None, None, 2, 1)]
        if "INSERT INTO franchise_pending_moves" in sql:
            return [(9,)]
        return []
    return router


def test_queue_trade_move_payload_and_no_op_without_the_table():
    cur = ScriptedCur(queue_router())
    assert gt.queue_trade_move(cur, 512) == 9
    payload = json.loads(cur.params[-1][1])
    assert payload["proposal_id"] == 512 and payload["other_team_id"] == 2
    assert payload["send"] == {"players": [900], "picks": [{"season": 2037, "round": 4, "original_team_id": 1}]}
    assert payload["receive"] == {"players": [901], "picks": []}


def test_queue_trade_move_no_op_when_the_table_is_absent():
    cur = ScriptedCur(queue_router(exists=False))
    assert gt.queue_trade_move(cur, 512) is None
    assert not any("INSERT" in s for s in cur.sql)


# ------------------------------------------------------------------ replay wk window ----
def test_a_replayed_week_uses_its_own_window_not_the_leagues_current_one():
    # The 2036 archive sits at week 21, past the deadline: --week 6 must still replay in season.
    assert gt.replay_target((2036, 21, None), 2036, 6) == (2036, 6, "IN_SEASON")
    assert gt.replay_target((2036, 26, "OFFSEASON"), week=24) == (2036, 24, "OFFSEASON")
    # No --week: the league's own season/week/window, unchanged.
    assert gt.replay_target((2036, 7, "IN_SEASON")) == (2036, 7, "IN_SEASON")
    assert gt.replay_target((2036, 21, None)) == (2036, 21, None)
