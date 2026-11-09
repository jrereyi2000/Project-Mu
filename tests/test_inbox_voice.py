"""How counterparts speak (FRONT_OFFICE_INBOX_PLAN.md §2.4, D7; U1). Fake models only; offline SQLite fake."""
import json
import time

import pytest

from engines.inbox import InboxError, actors, store, voice
from engines.inbox.registry import ActResult
from tests.test_inbox_store import InboxDB, thread_row

FACTS = {"player": "Bryce Young", "years": 3, "total": "$44.0M", "guaranteed": "$18.0M", "decision": "COUNTER"}


class FakeKind:
    KIND = "CONTRACT"
    COUNTERPART_TYPE = "player"
    ACTOR_ROLES = ("GM",)

    def __init__(self, decision="COUNTER", close=False, judge=True):
        self.decision, self.close, self.judge, self.acted = decision, close, judge, []

    def template(self, intent, facts):
        return f"[{intent}] I need {facts.get('guaranteed', 'more')} guaranteed."

    def options(self, cur, thread, last):
        return [{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms"}]

    def act(self, cur, thread, option_id, payload, note):
        self.acted.append((option_id, payload, note))
        return ActResult(dict(FACTS), self.decision, {"offer_id": 77}, close=self.close, judge=self.judge)


class FakeLLM:
    def __init__(self, answer, delay=0.0):
        self.answer, self.delay, self.prompts = answer, delay, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


GOOD = {"message": "I want to stay, but get me to $18.0M guaranteed and we're done.",
        "appraisal": {"outcome": "positive", "significance": "routine", "mistreated": False,
                      "reason": "a fair, direct offer"}}


@pytest.fixture
def db(monkeypatch):
    d = InboxDB(personas=True)
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    monkeypatch.setattr(voice, "_universe_id", lambda: None)
    d.conn.execute("INSERT INTO persona_sliders VALUES ('gm', '20', 8, 2, 7, 5, 2, 8)")
    d.conn.execute("INSERT INTO persona_traits (persona_type, persona_id, trait_id) VALUES ('gm', '20', 'blunt')")
    d.conn.execute("INSERT INTO persona_sliders VALUES ('writer', 'car_beat', 5, 8, 5, 5, 7, 5)")
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def player_thread(cur):
    return store.get_thread(cur, store.create_thread(cur, thread_row()))


def gm_thread(cur):
    return store.get_thread(cur, store.create_thread(cur, thread_row(kind="GM_TALK", counterpart_type="gm",
                                                                     counterpart_id="20",
                                                                     counterpart_name="Terry Fontenot",
                                                                     source_ref="trade:1")))


# ------------------------------------------------------------------------------------------------- persona ---

def test_neutral_view_for_a_missing_profile(cur):
    v = voice.persona_view(cur, "player", "100")
    assert v["status"] == "missing" and set(v["sliders"].values()) == {5} and v["traits"] == []
    assert voice.persona_view(cur, "prospect", "abc")["status"] == "missing"


def test_gm_and_writer_views(cur):
    gm = voice.persona_view(cur, "gm", "20")
    assert gm["status"] == "legacy" and gm["sliders"]["temperament"] == 8 and gm["traits"] == ["blunt"]
    writer = voice.persona_view(cur, "writer", "car_beat")
    assert writer["status"] == "legacy" and writer["sliders"]["sociability"] == 8
    assert voice.persona_view(cur, "gm", "999")["status"] == "missing"


# ------------------------------------------------------------------------------------------------- writing ---

def test_model_reply_kept_when_it_checks_out(cur):
    llm = FakeLLM(GOOD)
    text, source, ap = voice.write(cur, FakeKind(), gm_thread(cur), "COUNTER", FACTS, [], llm=llm)
    assert source == "LLM" and text == GOOD["message"]
    assert ap == {"outcome": "positive", "significance": "routine", "mistreated": False, "reason": "a fair, direct offer"}
    assert '"appraisal"' in llm.prompts[0] and "blunt" in llm.prompts[0]


@pytest.mark.parametrize("message, kw", [
    ("Make it $25.0M guaranteed and we're done.", {}),                       # invented money
    ("I'd rather have 5 years.", {}),                                         # invented number
    ("Bijan Robinson got more than this.", {"league": {"players": {"BIJAN ROBINSON"}, "teams": set()}}),
    ("Fine, we have a deal.", {"intent": "REJECT"}),                          # contradicts the decision
])
def test_bad_model_text_falls_back_to_template(cur, message, kw):
    intent = kw.pop("intent", "COUNTER")
    text, source, ap = voice.write(cur, FakeKind(), gm_thread(cur), intent, FACTS, [],
                                   llm=FakeLLM(dict(GOOD, message=message)), **kw)
    assert source == "TEMPLATE" and text.startswith(f"[{intent}]") and ap is None


def test_kind_validate_hook(cur):
    kind = FakeKind()
    kind.validate = lambda text, facts: (False, "wrong school")
    assert voice.write(cur, kind, gm_thread(cur), "COUNTER", FACTS, [], llm=FakeLLM(GOOD))[1] == "TEMPLATE"


def test_per_intent_voice_replaces_the_counterpart_for_that_intent(cur):
    class Kind(FakeKind):
        def intent_voice(self, intent):
            return {"name": "the area scout", "voice": "a third-person scouting memo"} \
                if intent == "SUMMARY" else None

    llm = FakeLLM(dict(GOOD, message="He was prepared and answered every question directly."))
    text, source, _ = voice.write(cur, Kind(), player_thread(cur), "SUMMARY", FACTS, [], llm=llm)
    assert source == "LLM" and text.startswith("He was prepared")
    assert "You are the area scout" in llm.prompts[0]
    assert "Your voice: a third-person scouting memo." in llm.prompts[0]

    second = store.get_thread(cur, store.create_thread(cur, thread_row(source_ref="ext:101:2026")))
    answer_llm = FakeLLM(dict(GOOD, message="I was prepared and answered every question directly."))
    voice.write(cur, Kind(), second, "COUNTER", FACTS, [], llm=answer_llm)
    assert "You are the area scout" not in answer_llm.prompts[0]


def test_slow_model_falls_back_within_budget(cur):
    started = time.monotonic()
    text, source, ap = voice.write(cur, FakeKind(), gm_thread(cur), "COUNTER", FACTS, [],
                                   llm=FakeLLM(GOOD, delay=0.6), budget_s=0.05)
    assert source == "TEMPLATE" and ap is None
    assert time.monotonic() - started < 0.5


def test_garbage_and_failing_models_fall_back(cur):
    class Boom:
        def __call__(self, prompt):
            raise RuntimeError("quota")
    t = gm_thread(cur)
    for llm in (FakeLLM("not json"), FakeLLM({"message": ""}), Boom()):
        assert voice.write(cur, FakeKind(), t, "COUNTER", FACTS, [], llm=llm)[1] == "TEMPLATE"


def test_no_model_is_template(cur):
    assert voice.write(cur, FakeKind(), gm_thread(cur), "COUNTER", FACTS, [], llm=None)[1] == "TEMPLATE"


def test_mu_inbox_llm_off(monkeypatch):
    calls = []
    import engines.gm_llm as gm_llm
    monkeypatch.setattr(gm_llm, "make_llm", lambda label, **k: calls.append(label) or "LLM")
    monkeypatch.setenv("MU_INBOX_LLM", "0")
    assert voice.default_llm() is None and calls == []
    monkeypatch.setenv("MU_INBOX_LLM", "1")
    assert voice.default_llm() == "LLM" and calls == ["inbox"]


def test_appraisal_asked_only_when_allowed(cur):
    llm = FakeLLM(GOOD)
    text, source, ap = voice.write(cur, FakeKind(), player_thread(cur), "COUNTER", FACTS, [], llm=llm)
    assert source == "LLM" and ap is None                     # neutral player: no appraisal even if one comes back
    assert '"appraisal"' not in llm.prompts[0] and "plain and professional" in llm.prompts[0]
    llm = FakeLLM(GOOD)
    assert voice.write(cur, FakeKind(), gm_thread(cur), "COUNTER", FACTS, [], llm=llm, judge=False)[2] is None
    assert '"appraisal"' not in llm.prompts[0]


def test_no_relationship_lines_without_relationship_context(cur):
    llm = FakeLLM(GOOD)
    voice.write(cur, FakeKind(), gm_thread(cur), "COUNTER", FACTS, [], llm=llm)
    assert "How you see them" not in llm.prompts[0] and "trust" not in llm.prompts[0].lower()


def test_extra_json_keys_reach_compose(cur):
    r = voice.compose(cur, FakeKind(), gm_thread(cur), "ASK", FACTS, [], llm=FakeLLM(dict(GOOD, done=True)))
    assert r.data == {"done": True}


# ------------------------------------------------------------------------------------------------ one turn ---

def test_exchange_order_and_event(db, cur):
    actors.take_control(cur, "CAR", "GM", "10")
    t = gm_thread(cur)
    kind = FakeKind()
    voice.exchange(cur, kind, t["id"], "MAKE_OFFER", {"years": 3, "total": 40_000_000, "guaranteed": 15_000_000},
                   "That's my best offer for now.", llm=FakeLLM(GOOD))
    msgs = store.messages(cur, t["id"])
    assert [(m["author"], m["body_source"]) for m in msgs] == [("JR", "JR"), ("COUNTERPART", "LLM")]
    assert msgs[0]["engine_ref"] == {"offer_id": 77} and msgs[1]["appraisal"]["outcome"] == "positive"
    assert msgs[1]["options"][0]["id"] == "MAKE_OFFER"
    rows = db.rows("SELECT kind, source_event_id, message_id FROM inbox_relationship_events")
    assert rows == [("meaningful_interaction", f"inbox:msg:{msgs[1]['id']}", msgs[1]["id"])]


def test_exchange_template_records_no_event_and_close(db, cur):
    actors.take_control(cur, "CAR", "GM", "10")
    t = gm_thread(cur)
    after = voice.exchange(cur, FakeKind(decision="ACCEPTED", close=True), t["id"], "ACCEPT_COUNTER", None, None,
                           llm=None)
    assert after["status"] == "CLOSED"
    msgs = store.messages(cur, t["id"])
    assert msgs[0]["body"] == "ACCEPT_COUNTER" and msgs[1]["options"] == []
    assert db.rows("SELECT count(*) FROM inbox_relationship_events") == [(0,)]


def test_null_option_is_talk_with_no_engine_act(db, cur):
    actors.take_control(cur, "CAR", "GM", "10")
    t = gm_thread(cur)
    opening = [{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms"}]
    store.add_message(cur, t["id"], "COUNTERPART", "We are apart on the guaranteed money.", "TEMPLATE",
                      options=opening, facts=dict(FACTS))
    kind, llm = FakeKind(), FakeLLM(GOOD)
    voice.exchange(cur, kind, t["id"], option_id=None, note="What would it take to close this?", llm=llm)
    assert kind.acted == []                                    # talk runs no engine act
    msgs = store.messages(cur, t["id"])
    assert [(m["author"], m["body_source"]) for m in msgs] == [
        ("COUNTERPART", "TEMPLATE"), ("JR", "JR"), ("COUNTERPART", "LLM")]
    assert msgs[1]["body"] == "What would it take to close this?" and msgs[1]["option_id"] is None
    assert msgs[2]["options"] == opening                       # JR keeps the buttons he had
    assert msgs[2]["engine_ref"] is None
    assert "decision" not in msgs[2]["facts"]                  # an old decision is not re-conveyed
    assert "No decision is being made" in llm.prompts[0]
    assert set(store.get_thread(cur, t["id"])["state"]) <= {"_touched"}   # nothing decided or changed


def test_null_option_without_words_is_400(cur):
    actors.take_control(cur, "CAR", "GM", "10")
    t = gm_thread(cur)
    with pytest.raises(InboxError) as e:
        voice.exchange(cur, FakeKind(), t["id"], None, None, "   ")
    assert e.value.status == 400
    assert store.messages(cur, t["id"]) == []


def test_a_typed_text_kind_still_reads_the_note(cur):
    actors.take_control(cur, "CAR", "GM", "10")
    t = gm_thread(cur)

    class TypedKind(FakeKind):
        KIND = "PROSPECT"

    kind = TypedKind()
    voice.exchange(cur, kind, t["id"], option_id=None, note="Why should we draft you?", llm=FakeLLM(GOOD))
    assert kind.acted == [(None, {}, "Why should we draft you?")]


def test_exchange_refuses_read_only_and_long_notes(cur):
    t = gm_thread(cur)
    with pytest.raises(InboxError) as e:                  # JR doesn't control the GM persona
        voice.exchange(cur, FakeKind(), t["id"], None, None, "hi")
    assert e.value.status == 409
    actors.take_control(cur, "CAR", "GM", "10")
    with pytest.raises(InboxError) as e:
        voice.exchange(cur, FakeKind(), t["id"], None, None, "x" * 601)
    assert e.value.status == 400
    with pytest.raises(InboxError) as e:
        voice.exchange(cur, FakeKind(), 999, None, None, "hi")
    assert e.value.status == 404
