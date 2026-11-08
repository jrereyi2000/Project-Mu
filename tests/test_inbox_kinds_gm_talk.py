"""GM_TALK: trades between JR's GM and another GM (FRONT_OFFICE_INBOX_PLAN.md §2.8, §4; U4).

Offline: the inbox store runs on the shared SQLite fake (tests/test_inbox_store.InboxDB); gm_negotiation and
front_office are patched, so the tests assert exactly what the kind asks the engine for and what it exposes back
(no surplus, the engine's turn message as the reply, a judgment only when JR wrote a note).
"""
import pytest

from engines import gm_negotiation
from engines import front_office
from engines.inbox import InboxError, actors, store, voice
from engines.inbox.kinds import gm_talk
from engines.inbox.registry import Trigger
from tests.test_inbox_store import InboxDB


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def proposal(pid=77, origin="ENGINE", a=2, b=1, headline="ATL wants Legette"):
    return {"id": pid, "origin": origin, "status": "PROPOSED", "team_a_id": a, "team_b_id": b,
            "headline": headline, "assets": [{"type": "PLAYER", "player_id": 101, "from": b, "to": a},
                                              {"type": "PICK", "pick": (2027, 2, b), "from": b, "to": a}]}


def thread_row(state=None, source_ref="gm_talk:proposal:77"):
    return {"team_id": 1, "kind": "GM_TALK", "actor_role": "GM", "actor_id": "10", "counterpart_type": "gm",
            "counterpart_id": "20", "counterpart_name": "Terry Fontenot", "subject": "A trade proposal",
            "started_by": "COUNTERPART", "source_ref": source_ref, "season": 2026, "week": 5,
            "state": dict(state or {"proposal_id": 77, "gm_team_id": 2, "phase": "INBOUND"})}


def make_thread(cur, state=None, source_ref="gm_talk:proposal:77"):
    tid = store.create_thread(cur, thread_row(state, source_ref))
    return store.get_thread(cur, tid)


def negotiation(action="COUNTER", message="Terry Fontenot: Here's where we'd get it done.", status="OPEN",
                proposal_id=None):
    counter_assets = [{"type": "PLAYER", "player_id": 200, "from": "ATL", "to": "CAR"}] if action == "COUNTER" else \
        [{"type": "PLAYER", "player_id": 101, "from": "CAR", "to": "ATL"}]
    turns = [{"turn_no": 1, "actor": "JR", "action": "OFFER", "assets": [], "message": None, "surplus_user": 0},
             {"turn_no": 2, "actor": "GM", "action": action, "assets": counter_assets, "message": message,
              "surplus_user": 5_000_000}]             # the fake deliberately carries a surplus the kind must drop
    return {"id": 5, "status": status, "proposal_id": proposal_id, "turns": turns,
            "on_table": {"turn_no": 2, "assets": counter_assets} if action == "COUNTER" else None}


# ---------------------------------------------------------------------------------------------------- scan ---

def test_scan_opens_trade_interest_for_an_engine_proposal(cur, monkeypatch):
    monkeypatch.setattr("engines.gm_trade_engine.read_proposals", lambda c, s, statuses=None: [proposal()])
    out = gm_talk.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR", "leader": "x"})
    assert len(out) == 1
    t = out[0]
    assert t.kind == "GM_TALK" and t.intent == "TRADE_INTEREST"
    assert (t.team_id, t.counterpart_type, t.counterpart_id, t.counterpart_name) == (1, "gm", "20", "Terry Fontenot")
    assert t.source_ref == "gm_talk:proposal:77" and t.link_table == "gm_trade_proposals" and t.link_id == 77
    assert t.state == {"proposal_id": 77, "gm_team_id": 2, "phase": "INBOUND"}
    assert t.facts["team"] == "CAR" and t.facts["other_team"] == "ATL"
    assert "Xavier Legette" in t.facts["assets"] and "2027 R2 (CAR)" in t.facts["assets"]


def test_scan_skips_user_proposals_and_other_teams(cur, monkeypatch):
    rows = [proposal(), proposal(pid=78, origin="USER"), proposal(pid=79, a=1, b=2),
            proposal(pid=80, a=2, b=2)]              # last one doesn't involve CAR at all
    monkeypatch.setattr("engines.gm_trade_engine.read_proposals", lambda c, s, statuses=None: rows)
    out = gm_talk.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"})
    # 77 (ENGINE, CAR/ATL) and 79 (ENGINE, CAR the other side) are the triggers; 78 is USER and 80 isn't CAR
    assert sorted(t.link_id for t in out) == [77, 79]


def test_scan_is_idempotent_once_a_thread_exists(cur, monkeypatch):
    monkeypatch.setattr("engines.gm_trade_engine.read_proposals", lambda c, s, statuses=None: [proposal()])
    make_thread(cur)
    assert gm_talk.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"}) == []


def test_scan_quiet_without_the_tables_or_a_gm(cur, monkeypatch):
    monkeypatch.setattr("engines.gm_trade_engine.read_proposals",
                        lambda c, s, statuses=None: (_ for _ in ()).throw(RuntimeError("no tables")))
    assert gm_talk.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"}) == []
    monkeypatch.setattr("engines.gm_trade_engine.read_proposals", lambda c, s, statuses=None: [proposal(a=1, b=2)])
    cur.execute("UPDATE gm_profiles SET end_season = 2025 WHERE team_id = 2")
    assert gm_talk.scan(cur, 2026, 5, {"team_id": 1, "abbr": "CAR"}) == []


def test_start_opens_a_talk_thread_without_running_the_engine(cur):
    t = gm_talk.start(cur, {"team_id": 1, "abbr": "CAR"}, {"gm_team": "ATL"})
    assert t.intent == "OPEN_TALKS" and t.source_ref == "gm_talk:start:CAR:ATL"
    assert (t.counterpart_id, t.counterpart_name) == ("20", "Terry Fontenot")
    assert t.state == {"gm_team_id": 2, "phase": "TALKS"}
    with pytest.raises(InboxError):
        gm_talk.start(cur, {"team_id": 1, "abbr": "CAR"}, {})


# ------------------------------------------------------------------------------------------------- options ---

def test_options_inbound_and_in_talks(cur):
    t = make_thread(cur)
    assert [o["id"] for o in gm_talk.options(cur, t, None)] == ["OPEN_TALKS", "NOT_INTERESTED"]
    assert gm_talk.options(cur, t, None)[0]["form"] == "trade_assets"
    talks = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS"},
                        source_ref="gm_talk:start:CAR:ATL")
    assert [o["id"] for o in gm_talk.options(cur, talks, None)] == ["MAKE_OFFER", "END_TALKS"]
    talks = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS",
                                    "counter": {"assets": [], "turn_no": 2}},
                        source_ref="gm_talk:start:CAR:ATL:2")
    assert [o["id"] for o in gm_talk.options(cur, talks, None)] == ["MAKE_OFFER", "ACCEPT_COUNTER", "END_TALKS"]


# ----------------------------------------------------------------------------------------------------- act ---

def test_open_talks_calls_open_negotiation_and_uses_the_engine_message(cur, monkeypatch):
    calls = []

    def fake_open(c, user, gm, assets, note=None, llm=None):
        calls.append((user, gm, assets, note, llm))
        return negotiation("COUNTER")

    monkeypatch.setattr(gm_negotiation, "open_negotiation", fake_open)
    t = make_thread(cur)
    payload = {"players": [101], "picks": [{"season": 2027, "round": 2, "original_team": "CAR"}]}
    r = gm_talk.act(cur, t, "OPEN_TALKS", payload, None)

    assert calls == [("CAR", "ATL", [{"type": "PLAYER", "player_id": 101, "from": "CAR", "to": "ATL"},
                                     {"type": "PICK", "draft_season": 2027, "round": 2, "original_team": "CAR",
                                      "from": "CAR", "to": "ATL"}], None, None)]
    assert r.intent == "COUNTER" and r.close is False and r.judge is False       # no note -> no judgment
    assert r.facts["gm_message"] == "Terry Fontenot: Here's where we'd get it done."
    assert r.state["negotiation_id"] == 5 and r.state["phase"] == "TALKS" and r.state["counter"]
    assert r.engine_ref == {"negotiation_id": 5, "status": "OPEN", "action": "COUNTER", "turn_no": 2}
    assert "surplus" not in repr((r.facts, r.engine_ref, r.state))


def test_open_talks_with_a_note_asks_for_a_judgment(cur, monkeypatch):
    monkeypatch.setattr(gm_negotiation, "open_negotiation",
                        lambda c, user, gm, assets, note=None, llm=None: negotiation("REJECT"))
    t = make_thread(cur)
    r = gm_talk.act(cur, t, "OPEN_TALKS", {"players": [101]}, "We're not moving Legette cheap.")
    assert r.intent == "REJECT" and r.judge is True


def test_a_walk_away_closes_but_a_reject_stays_open(cur, monkeypatch):
    monkeypatch.setattr(gm_negotiation, "open_negotiation",
                        lambda c, user, gm, assets, note=None, llm=None: negotiation("WALK_AWAY",
                                                                                     status="WALKED_AWAY"))
    r = gm_talk.act(cur, make_thread(cur), "OPEN_TALKS", {"players": [101]}, None)
    assert r.close is True and r.intent == "WALK_AWAY"
    monkeypatch.setattr(gm_negotiation, "open_negotiation",
                        lambda c, user, gm, assets, note=None, llm=None: negotiation("REJECT"))
    r = gm_talk.act(cur, make_thread(cur, source_ref="gm_talk:proposal:78"), "OPEN_TALKS", {"players": [101]}, None)
    assert r.close is False and r.intent == "REJECT"


def test_make_offer_calls_offer_with_the_negotiation_id(cur, monkeypatch):
    calls = []

    def fake_offer(c, nid, assets, note=None, llm=None):
        calls.append((nid, assets, note))
        return negotiation("ACCEPT", status="AGREED", proposal_id=88)

    monkeypatch.setattr(gm_negotiation, "offer", fake_offer)
    t = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS"},
                    source_ref="gm_talk:start:CAR:ATL")
    r = gm_talk.act(cur, t, "MAKE_OFFER", {"players": [101]}, None)
    assert calls == [(5, [{"type": "PLAYER", "player_id": 101, "from": "CAR", "to": "ATL"}], None)]
    assert r.intent == "AGREED" and r.close is True
    assert r.facts["proposal_id"] == 88 and r.engine_ref["proposal_id"] == 88


def test_accept_counter_calls_accept_counter(cur, monkeypatch):
    calls = []
    monkeypatch.setattr(gm_negotiation, "accept_counter",
                        lambda c, nid, llm=None: calls.append(nid) or negotiation("ACCEPT", status="AGREED",
                                                                                 proposal_id=88))
    t = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS",
                                "counter": {"assets": [], "turn_no": 2}},
                    source_ref="gm_talk:start:CAR:ATL")
    r = gm_talk.act(cur, t, "ACCEPT_COUNTER", {}, None)
    assert calls == [5] and r.close is True and r.intent == "AGREED"


def test_accept_counter_without_a_counter_is_409(cur):
    t = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS"},
                    source_ref="gm_talk:start:CAR:ATL")
    with pytest.raises(InboxError) as e:
        gm_talk.act(cur, t, "ACCEPT_COUNTER", {}, None)
    assert e.value.status == 409


def test_end_talks_closes_via_the_engine(cur, monkeypatch):
    calls = []
    monkeypatch.setattr(gm_negotiation, "close", lambda c, nid, note=None: calls.append((nid, note)))
    t = make_thread(cur, state={"gm_team_id": 2, "negotiation_id": 5, "phase": "TALKS"},
                    source_ref="gm_talk:start:CAR:ATL")
    r = gm_talk.act(cur, t, "END_TALKS", {}, "We're done here.")
    assert calls == [(5, "We're done here.")] and r.close is True and r.intent == "END_TALKS"


def test_not_interested_rejects_the_proposal(cur, monkeypatch):
    calls = []
    monkeypatch.setattr(front_office, "reject", lambda c, pid, reason=None: calls.append((pid, reason)))
    t = make_thread(cur)
    r = gm_talk.act(cur, t, "NOT_INTERESTED", {}, None)
    assert calls and calls[0][0] == 77 and r.close is True and r.intent == "NOT_INTERESTED"


def test_mismatch_text_comes_through(cur, monkeypatch):
    def refuse(c, user, gm, assets, note=None, llm=None):
        raise front_office.FrontOfficeError(400, "you don't control CAR (tick JR controls on the Front Office card)")
    monkeypatch.setattr(gm_negotiation, "open_negotiation", refuse)
    t = make_thread(cur)
    with pytest.raises(InboxError) as e:
        gm_talk.act(cur, t, "OPEN_TALKS", {"players": [101]}, None)
    assert e.value.status == 400 and "you don't control CAR" in str(e.value)


def test_an_offer_needs_an_asset(cur):
    t = make_thread(cur)
    with pytest.raises(InboxError) as e:
        gm_talk.act(cur, t, "OPEN_TALKS", {"players": [], "picks": []}, None)
    assert e.value.status == 400


# ------------------------------------------------------------------------------------------------- draft ---

def test_draft_matches_players_and_picks_from_team_assets(cur, monkeypatch):
    monkeypatch.setattr(gm_negotiation, "team_assets", lambda c, user, gm: {"teams": {
        "CAR": {"players": [{"player_id": 101, "name": "Xavier Legette", "pos": "WR", "tradeable": True},
                            {"player_id": 100, "name": "Bryce Young", "pos": "QB", "tradeable": False}],
                "picks": [{"draft_season": 2027, "round": 2, "original_team": "CAR", "label": "2027 R2 (CAR)",
                           "booked": False},
                          {"draft_season": 2028, "round": 1, "original_team": "CAR", "label": "2028 R1 (CAR)",
                           "booked": False}]}}})
    t = make_thread(cur)
    d = gm_talk.draft(cur, t, "Legette and a 2027 second")
    assert d["option_id"] == "OPEN_TALKS"
    assert d["payload"] == {"players": [101], "picks": [{"season": 2027, "round": 2, "original_team": "CAR"}]}
    assert "Legette" in d["summary"] and "2027 R2" in d["summary"]


def test_draft_with_nothing_recognisable_is_none(cur, monkeypatch):
    monkeypatch.setattr(gm_negotiation, "team_assets", lambda c, user, gm: {"teams": {"CAR": {"players": [],
                                                                                           "picks": []}}})
    assert gm_talk.draft(cur, make_thread(cur), "what do you think?") is None


# ---------------------------------------------------------------------------------------------- template ---

def test_template_prefers_the_engine_message():
    assert gm_talk.template("COUNTER", {"gm_message": "Terry Fontenot: Deal."}) == "Terry Fontenot: Deal."
    assert "Agreed" in gm_talk.template("AGREED", {"gm": "Terry Fontenot"})
    assert "look elsewhere" in gm_talk.template("NOT_INTERESTED", {"gm": "Terry Fontenot"})


# --------------------------------------------------------------------------------------------- one turn ---

def test_exchange_stores_the_engine_message_and_no_judgment_without_a_note(cur, monkeypatch):
    actors.take_control(cur, "CAR", "GM", "10")
    monkeypatch.setattr(gm_negotiation, "open_negotiation",
                        lambda c, user, gm, assets, note=None, llm=None: negotiation("COUNTER"))
    t = make_thread(cur)
    out = voice.exchange(cur, gm_talk, t["id"], "OPEN_TALKS", {"players": [101]}, note=None, llm=None)
    msgs = store.messages(cur, t["id"])
    jr, gm = msgs[0], msgs[1]
    assert jr["author"] == "JR" and gm["author"] == "COUNTERPART"
    assert gm["body"] == "Terry Fontenot: Here's where we'd get it done." and gm["body_source"] == "TEMPLATE"
    assert gm["appraisal"] is None                     # judge=False without a note
    assert out["status"] == "WAITING_JR"
